#!/usr/bin/env python3
"""Transistor-level LVS and device DRC of an assembled macro, with open tools.

    .venv/bin/python scripts/verify_macro.py results/sram_x4x2x1_<stamp>

The compiler's own gate (`compile_asap7_2rw.py verify()`) re-extracts the metal
graph and checks that every routed terminal reaches its net.  It treats the
columns and the controller as abstracts, so it cannot see inside them, and it
says so: ``"signoff_lvs": "not_run", "device_drc": "not_run"``.  This runs both.

LVS is KLayout with chipforge_asap7's ASAP7 deck, against the ``.sp`` the
compiler wrote beside the GDS:

* devices are compared fin by fin, source/drain taken the way released CDL
  assumes (the whole ACTIVE outside the gate);
* bitcells are flattened on both sides, since a bitcell's transistors are
  completed by its neighbours and the layout cell is not the schematic's;
* released standard cells whose GDS and CDL order a series stack differently
  (``AO21x1``, ``AOI211xp5``, ...) are proven equivalent as series/parallel
  networks and then compared by pin name.  They are listed; nothing else is
  excused.

When the strict run fails it is repeated with ``double_implant_is_tap=False``.
ACTIVE under both implants acts as a well tap, which ties whatever diffusion it
belongs to to the substrate; one such error grounds a whole class of nets and
hides everything else.  The second run shows what is left without it.

DRC is the public ASAP7 KLayout runset over the whole macro.  It is not a
sign-off deck, and the released hard cells and OpenROAD's routing both trip it,
so the result is a count per rule to hold a line with, not a pass.

``--baseline`` compares against known findings, the way
`tests/run_8t_device_drc_check.sh` holds cell DRC: exit 0 if nothing is new,
1 if something is, 3 if something known has gone (tighten the baseline).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
# Flattened into their parents for the comparison: the bitcell family, whose
# devices the generator splits across cells, and the parametric IO block's
# cells, which the compiler's deck carries as one flat subcircuit per port.
BITCELLS = ("sram_cell_*", "blmux_*", "sarow_*", "wrdrv_*", "outlatch_*", "filler_fin_*", "tap_fin_*",
            "wl_slice_*", "nand2_fin_*", "inv_fin_*", "wl_via*",
            # A strip's slices take their predecode inputs from the top-level
            # routing, one pin per slice, so the strip is compared in place.
            "wl_strip_*", "wl_strips_*",
            # The released 6T family (--bitcell 6t): the dummy, caps, taps and
            # end rows are completed by their neighbours as bitcells are.
            "dummy_*", "tapcell_*", "end_row_*")  # fmt: skip


def _lvs_findings(result) -> dict:
    """The parts of a hierarchical LVS result worth keeping, JSON-safe."""
    shorts = sorted(
        f"{short['supply']} <- {short['circuit']}.{short['pin']} x{short['count']}"
        for circuit in result.summary["circuits"]
        for short in circuit.get("supply_shorts", [])
    )
    unmatched = {
        (circuit["layout"] or circuit["reference"]): {
            what: {k: v for k, v in tally.items() if k != "match"}
            for what, tally in circuit["counts"].items()
            if any(k != "match" for k in tally)
        }
        for circuit in result.summary["circuits"]
        if circuit["status"] in ("mismatch", "nomatch")
    }
    return {
        "matched": result.matched,
        "series_order_cells": sorted(result.series_order_cells),
        "failing": list(result.failing),
        "unmatched": unmatched,
        "supply_shorts": shorts,
        "report": str(result.lvs.report),
    }


def bank_views(gds: Path, netlist: Path, cell: str, out: Path) -> list[tuple[Path, Path]]:
    """One copy of the macro per bank, the other banks' column tiles taken out of both sides.

    KLayout's comparer does not finish on a whole multi-bank macro: every
    bit's column appears once per bank on the same data nets, and it gets
    lost pairing the copies.  Each view keeps one bank's tiles and all the
    rest (controller, routing), so it is a single-bank problem.  A bank's
    wordline strips (``Xwl_<half>[_m<k><d|u>]_<bank>``, the strip masters
    standing in its column) go with its tiles: left behind, a segmented 6T
    macro's other-bank strips drive wordlines nothing loads on either side,
    and the comparer leaves them unpaired.  What no view sees, a short
    between tiles of different banks, is what the compiler's connectivity
    gate already rules out (every net one conductor, isolated from every
    other).
    """
    import gdstk

    text = netlist.read_text()
    source = gdstk.read_gds(str(gds))
    top = next(c for c in source.cells if c.name == cell)
    # A pair of banks sharing port B's IO is one tile (dp_colpair), X<pair>_<bit>.
    # A bank is a column of tiles; its tiles' origins differ by the variant
    # (a segmented 6T stack mixes end tiles whose outlines start apart), so
    # group them: banks stand a tile's width apart, variants nanometres.
    origins = sorted({round(float(r.origin[0]), 3) for r in top.references if r.cell.name.startswith("dp_col")})
    groups: list[list[float]] = []
    for x in origins:
        if groups and x - groups[-1][-1] < 1.0:
            groups[-1].append(x)
        else:
            groups.append([x])
    views = []
    for bank, xs_bank in enumerate(groups):
        view = out / f"bank{bank}"
        view.mkdir(parents=True, exist_ok=True)
        library = gdstk.read_gds(str(gds))
        view_top = next(c for c in library.cells if c.name == cell)
        def other_bank(ref, bank=bank, xs_bank=xs_bank):
            x = round(float(ref.origin[0]), 3)
            if ref.cell.name.startswith("dp_col"):
                return x not in xs_bank
            # A strip pair is drawn in its bank's tile's x, anywhere across
            # it (the 8T pair under the array, 1.7 um in): its bank is the
            # last one starting at or before it.
            owner = max((k for k, xs in enumerate(groups) if min(xs) <= x + 1e-6), default=0)
            return ref.cell.name.startswith("dp_wl_strips") and owner != bank

        view_top.remove(*[r for r in view_top.references if other_bank(r)])
        library.write_gds(str(view / gds.name))
        # The column groups are X<bank>_<bit> in the stacked column group,
        # and a 6T stack's dummy end rows XE<bank>_B/_T (inside its end tiles).
        kept, dropping = [], False
        for line in text.splitlines():
            if line.startswith("+"):
                if not dropping:
                    kept.append(line)
                continue
            dropping = bool(re.match(rf"X(?!{bank}_)\d+_\d+ |XE(?!{bank}_)\d+_[BT] "
                                     rf"|Xwl_(?:lo|hi)(?:_m\d+[du])?_(?!{bank} )\d+ ", line))  # fmt: skip
            if not dropping:
                kept.append(line)
        (view / netlist.name).write_text("\n".join(kept) + "\n")
        views.append((view / gds.name, view / netlist.name))
    return views


def _merge_findings(parts: list[dict]) -> dict:
    return {
        "matched": all(p["matched"] for p in parts),
        "series_order_cells": sorted({c for p in parts for c in p["series_order_cells"]}),
        "failing": sorted({c for p in parts for c in p["failing"]}),
        "unmatched": {f"bank{b}:{k}": v for b, p in enumerate(parts) for k, v in p["unmatched"].items()},
        "supply_shorts": sorted({s for p in parts for s in p["supply_shorts"]}),
        "report": [p["report"] for p in parts],
        "per_bank": True,
    }


#: Markers reported but not counted: tech/drc/asap7/waivers.yml.
DEFAULT_WAIVERS = Path(__file__).resolve().parents[1] / "tech/drc/asap7/waivers.yml"


def load_waivers(path: Path | None) -> list[dict]:
    if path is None:
        return []
    import yaml

    waivers = (yaml.safe_load(path.read_text()) or {}).get("waivers", [])
    for waiver in waivers:
        if not waiver.get("cells") or not waiver.get("reason"):
            raise ValueError(f"{path}: every waiver needs cells and a reason")
    return waivers


def waive(report: Path, waivers: list[dict]) -> tuple[dict, dict, list[dict]]:
    """Split a KLayout report's markers into ``(counted, waived)`` per rule, and per waiver.

    The runset files a marker under the cell whose own geometry produced it
    (``cell:orientation``); a waiver covers it when that cell matches one of
    its globs and its rule is listed.  The runset sometimes repeats a rule
    id in its category ("LIG.LISD.S.7LIG.LISD.S.7"); either form matches.
    """
    import fnmatch
    import xml.etree.ElementTree as ET

    counted, waived = {}, {}
    used = [0] * len(waivers)
    for item in ET.parse(report).getroot().findall("./items/item"):
        rule = (item.findtext("category") or "").strip("'\"").split(" ")[0]
        owner = (item.findtext("cell") or "").split(":")[0]
        hit = next((i for i, w in enumerate(waivers)
                    if any(fnmatch.fnmatchcase(owner, glob) for glob in w["cells"])
                    and (not w.get("rules") or any(rule in (r, r + r) for r in w["rules"]))), None)  # fmt: skip
        target = counted if hit is None else waived
        target[rule] = target.get(rule, 0) + 1
        if hit is not None:
            used[hit] += 1
    applied = [{"cells": w["cells"], "rules": w.get("rules"), "reason": w["reason"], "markers": n}
               for w, n in zip(waivers, used)]  # fmt: skip
    return dict(sorted(counted.items())), dict(sorted(waived.items())), applied


def verify(result_dir: Path, out: Path, *, cell: str | None = None, drc: bool = True,
           drc_engine: str = "gdscheck", waivers: Path | None = DEFAULT_WAIVERS,
           lvs_timeout: float = 3600) -> dict:
    from chipforge_asap7.verification import drc_counts, run_drc, run_hierarchical_lvs

    result_dir = result_dir.resolve()
    described = sorted(result_dir.glob("*.physical.json"))
    banks = json.loads(described[0].read_text()).get("banks", 1) if described else 1
    if cell is None:
        cell = json.loads(described[0].read_text())["cell"] if described else result_dir.name.rsplit("_", 2)[0]
    gds, netlist = result_dir / f"{cell}.gds", result_dir / f"{cell}.sp"
    for path in (gds, netlist):
        if not path.is_file():
            raise FileNotFoundError(f"{path} is missing; is {result_dir} a compiler result?")

    started = time.time()
    shared = described and json.loads(described[0].read_text()).get("shared_port_b", False)
    units = banks // 2 if shared else banks
    # Per-bank views keep KLayout's comparer from losing itself in identical
    # banks: a whole two-bank 6T macro at 8:1 (x8x8x2) did not finish in
    # four hours; its views match in minutes once each takes its bank's
    # wordline strips with its tiles.
    views = bank_views(gds, netlist, cell, out / "banks") if units > 1 else [(gds, netlist)]
    strict, relaxed = [], []
    for index, (view_gds, view_netlist) in enumerate(views):
        tag = f"bank{index}/" if units > 1 else ""
        result = run_hierarchical_lvs(view_gds, view_netlist, out / f"{tag}lvs", cell_name=cell, flatten_circuits=BITCELLS,
                                      timeout=lvs_timeout)  # fmt: skip
        strict.append(_lvs_findings(result))
        print(("" if units == 1 else f"== bank {index}\n") + result.describe())
        if not result.matched:
            result = run_hierarchical_lvs(view_gds, view_netlist, out / f"{tag}lvs_diagnostic", cell_name=cell,
                                          flatten_circuits=BITCELLS, double_implant_is_tap=False,
                                          timeout=lvs_timeout)  # fmt: skip
            relaxed.append(_lvs_findings(result))
            print("\n-- again, with ACTIVE under both implants not acting as a tap:")
            print(result.describe())
    verdict = {"cell": cell, "gds": str(gds), "lvs": strict[0] if units == 1 else _merge_findings(strict)}
    if relaxed:
        verdict["lvs_without_double_implant_taps"] = relaxed[0] if units == 1 else _merge_findings(relaxed)
    if drc:
        if drc_engine == "gdscheck":
            from drc import find_gdscheck, run_full_drc

            binary = find_gdscheck()
            version = subprocess.run([str(binary), "--version"], capture_output=True, text=True).stdout.strip()
            verdict["drc"] = {"engine": "gdscheck", "version": version,
                              **run_full_drc(gds, cell, out / "drc", binary=binary, timeout=7200)}
            print(f"\nDRC (gdscheck ASAP7 main suite): {verdict['drc']['markers']} markers in "
                  f"{len(verdict['drc']['rules'])} rules")
        elif drc_engine == "gdscheck-device":
            from drc import find_gdscheck, provenance, run_device_drc

            binary = find_gdscheck()
            verdict["drc"] = {**provenance(binary),
                              **run_device_drc(gds, cell, out / "drc", binary=binary, timeout=7200)}
            print(f"\nDRC (gdscheck calibrated device subset): {verdict['drc']['markers']} markers")
        elif drc_engine == "klayout":
            violations = run_drc(gds, out / "drc", cell_name=cell, timeout=7200)
            reports = sorted((out / "drc").glob("*.lyrdb"))
            if len(reports) != 1:
                raise RuntimeError(f"expected one KLayout report in {out / 'drc'}, found {len(reports)}")
            counts, waived, applied = waive(reports[0], load_waivers(waivers))
            if sum(counts.values()) + sum(waived.values()) != len(violations):
                raise RuntimeError("waiver split lost markers")
            verdict["drc"] = {"markers": sum(counts.values()), "rules": counts,
                              "waived": {"markers": sum(waived.values()), "rules": waived, "waivers": applied}}
            print(f"\nDRC (public KLayout runset): {sum(counts.values())} markers in {len(counts)} rules, "
                  f"{sum(waived.values())} waived; "
                  f"implant overlap (NSELECT.PSELECT.AUX.1): {counts.get('NSELECT.PSELECT.AUX.1', 0)}")
            for waiver in applied:
                if waiver["markers"]:
                    print(f"  waived {waiver['markers']}: {waiver['reason']}")
        else:
            raise ValueError(f"Unknown DRC engine: {drc_engine}")
    verdict["seconds"] = round(time.time() - started, 1)
    out.mkdir(parents=True, exist_ok=True)
    (out / "verification.json").write_text(json.dumps(verdict, indent=1) + "\n")
    return verdict


def known_findings(verdict: dict) -> dict:
    """What a baseline pins: LVS findings exactly, DRC as a ceiling per rule."""
    relaxed = verdict.get("lvs_without_double_implant_taps", {})
    findings = {
        "lvs_matched": verdict["lvs"]["matched"],
        "series_order_cells": verdict["lvs"]["series_order_cells"],
        "supply_shorts": verdict["lvs"]["supply_shorts"],
        "supply_shorts_without_double_implant_taps": relaxed.get("supply_shorts", []),
        "drc_rules": verdict.get("drc", {}).get("rules", {}),
    }
    if verdict.get("drc", {}).get("engine") == "gdscheck":
        findings["drc_identity"] = {key: verdict["drc"][key]
                                    for key in ("engine", "suite", "profile", "version", "deck_sha256")
                                    if key in verdict["drc"]}
    return findings


def compare(found: dict, baseline: dict) -> tuple[list[str], list[str]]:
    """``(new, gone)`` against `baseline`."""
    new, gone = [], []
    if found.get("drc_identity") != baseline.get("drc_identity"):
        return ["DRC engine/deck differs from baseline; use a separate baseline for this profile"], []
    if baseline["lvs_matched"] and not found["lvs_matched"]:
        new.append("LVS no longer matches")
    if found["lvs_matched"] and not baseline["lvs_matched"]:
        gone.append("LVS now matches")
    for key in ("series_order_cells", "supply_shorts", "supply_shorts_without_double_implant_taps"):
        new += [f"{key}: {item}" for item in found[key] if item not in baseline.get(key, [])]
        gone += [f"{key}: {item}" for item in baseline.get(key, []) if item not in found[key]]
    for rule, count in found["drc_rules"].items():
        if count > baseline["drc_rules"].get(rule, 0):
            new.append(f"DRC {rule}: {baseline['drc_rules'].get(rule, 0)} -> {count}")
    gone += [f"DRC {rule}: {count} -> {found['drc_rules'].get(rule, 0)}"
             for rule, count in baseline["drc_rules"].items() if found["drc_rules"].get(rule, 0) < count]  # fmt: skip
    return new, gone


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("result_dir", type=Path, help="A compiler result folder (holds <cell>.gds and <cell>.sp).")
    parser.add_argument("--cell", default=None, help="Top cell; read from <cell>.physical.json when omitted.")
    parser.add_argument("--out", type=Path, default=None, help="Work and report folder (default tmp/verify_<name>).")
    parser.add_argument("--no-drc", action="store_true")
    parser.add_argument("--drc-engine", choices=("gdscheck", "klayout", "gdscheck-device"), default="gdscheck",
                        help="gdscheck's ASAP7 main suite (default), the public KLayout runset, "
                             "or gdscheck's calibrated device subset.")
    parser.add_argument("--waivers", type=Path, default=DEFAULT_WAIVERS,
                        help="DRC waivers (KLayout engine); reported but not counted.")
    parser.add_argument("--no-waivers", action="store_true", help="Count every DRC marker.")
    parser.add_argument("--lvs-timeout", type=float, default=3600,
                        help="Seconds each LVS pass may take (large macros take hours).")
    parser.add_argument("--baseline", type=Path, default=None, help="Known findings to hold the line against.")
    parser.add_argument("--write-baseline", type=Path, default=None, help="Record this run's findings as known.")
    args = parser.parse_args(argv)

    out = args.out or REPO_ROOT / "tmp" / f"verify_{args.result_dir.resolve().name}"
    if args.no_drc and (args.baseline or args.write_baseline):
        parser.error("A DRC baseline requires DRC; remove --no-drc")
    verdict = verify(args.result_dir, out, cell=args.cell, drc=not args.no_drc, drc_engine=args.drc_engine,
                     waivers=None if args.no_waivers else args.waivers, lvs_timeout=args.lvs_timeout)
    found = known_findings(verdict)
    print(f"\nwrote {out / 'verification.json'}")
    if args.write_baseline:
        args.write_baseline.write_text(json.dumps(found, indent=1) + "\n")
        print(f"wrote {args.write_baseline}")
    if args.baseline:
        new, gone = compare(found, json.loads(args.baseline.read_text()))
        for line in new:
            print("NEW  ", line)
        for line in gone:
            print("GONE ", line)
        if new:
            return 1
        if gone:
            print("fewer findings than the baseline records: tighten it with --write-baseline")
            return 3
        print("no findings beyond the baseline")
        return 0
    return 0 if verdict["lvs"]["matched"] else 1


if __name__ == "__main__":
    sys.exit(main())
