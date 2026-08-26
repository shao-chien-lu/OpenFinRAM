#!/usr/bin/env python3
r"""Periphery wire RC from the routed ctrl_decode DEF (P1).

Prices the routed wordline/clock/sense-enable nets of the synthesized
periphery with the ORFS ASAP7 platform RC vendored in tech/setRC.tcl -
the same numbers the OpenROAD flow uses for estimate_parasitics - so
the Liberty-side periphery load stops being an ideal-wire assumption.

Geometry comes straight from the DEF ROUTED/NEW segments (Manhattan
length per routing layer) plus via counts. No commercial extraction;
OpenRCX-pattern flows remain future work.

Usage:
    python3 scripts/extract_periphery_rc.py \
        --def tmp/openroad_<ts>/ctrl_decode.def \
        --nets '^wl[tb]\[' --output results/periphery_rc.json
"""

import argparse
import json
import re
import sys
from pathlib import Path

# From tech/setRC.tcl (ORFS ASAP7 platform): fF/um and ohm/um.
LAYER_RC = {
    "M1": {"cap_ff_per_um": 0.1000,   "res_ohm_per_um": 70.4175},
    "M2": {"cap_ff_per_um": 0.174942, "res_ohm_per_um": 29.7127},
    "M3": {"cap_ff_per_um": 0.155554, "res_ohm_per_um": 31.2870},
    "M4": {"cap_ff_per_um": 0.178475, "res_ohm_per_um": 18.0365},
    "M5": {"cap_ff_per_um": 0.164264, "res_ohm_per_um": 18.9935},
}
VIA_RES_OHM = {
    "VIA12": 17.2, "VIA23": 17.2, "VIA34": 11.8,
    "VIA45": 11.8, "VIA56": 8.2, "VIA67": 8.2, "VIA78": 6.3, "VIA89": 6.3,
}

POINT = re.compile(r"\(\s*(\S+)\s+(\S+)\s*\)")


def def_to_um(def_text: str) -> float:
    m = re.search(r"DATABASE MICRONS (\d+)", def_text)
    return float(m.group(1)) if m else 1000.0


def parse_net(net_body: str, dbu_um: float) -> dict:
    """Sum Manhattan wirelength per layer and via counts for one net body."""
    lengths: dict[str, float] = {}
    vias: dict[str, int] = {}
    for stmt in re.finditer(
            r"(?:ROUTED|NEW)\s+(M\d+)([^;\n]*)", net_body):
        layer, rest = stmt.group(1), stmt.group(2)
        pts = [(int(a) if a != "*" else None, int(b) if b != "*" else None)
               for a, b in POINT.findall(rest)]
        # Resolve '*' against the previous point within the statement.
        resolved: list[tuple[int, int]] = []
        for x, y in pts:
            px, py = resolved[-1] if resolved else (None, None)
            resolved.append((x if x is not None else px, y if y is not None else py))
        for (x0, y0), (x1, y1) in zip(resolved, resolved[1:]):
            lengths[layer] = lengths.get(layer, 0.0) + \
                (abs(x1 - x0) + abs(y1 - y0)) / dbu_um
        for v in re.findall(r"VIA\d+", rest):
            vias[v] = vias.get(v, 0) + 1
    cap_ff = sum(LAYER_RC[l]["cap_ff_per_um"] * um
                 for l, um in lengths.items() if l in LAYER_RC)
    res_ohm = sum(LAYER_RC[l]["res_ohm_per_um"] * um
                  for l, um in lengths.items() if l in LAYER_RC)
    res_ohm += sum(VIA_RES_OHM.get(v, 0) * n for v, n in vias.items())
    return {
        "wirelength_by_layer_um": {l: round(u, 4) for l, u in sorted(lengths.items())},
        "vias": vias,
        "cap_ff": round(cap_ff, 5),
        "res_ohm": round(res_ohm, 2),
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--def-file", type=Path, required=True)
    ap.add_argument("--nets", default=r"^wl[tb]\[\d+\]",
                    help="regex over net names (default: wordline outputs)")
    ap.add_argument("--output", type=Path, default=None)
    args = ap.parse_args(argv)

    txt = args.def_file.read_text()
    sec_m = re.search(r"NETS\s+(\d+)\s*;(.*?)END NETS", txt, re.S)
    if not sec_m:
        print("ERROR: no NETS section", file=sys.stderr)
        return 1
    dbu_um = def_to_um(txt)

    pat = re.compile(args.nets)
    out: dict[str, dict] = {}
    for body in re.split(r"\n(?=\s*-\s)", sec_m.group(2)):
        m = re.match(r"\s*-\s+(\S+)", body)
        if not m or not pat.search(m.group(1)):
            continue
        name = m.group(1)
        name = name.split("/")[-1] if "/" in name else name
        out[name] = parse_net(body, dbu_um)

    if not out:
        print(f"ERROR: no nets matched {args.nets!r}", file=sys.stderr)
        return 1

    total_cap = sum(v["cap_ff"] for v in out.values())
    summary = {
        "source_def": str(args.def_file),
        "rc_source": "tech/setRC.tcl (ORFS ASAP7 platform)",
        "net_filter": args.nets,
        "nets": out,
        "total_cap_ff": round(total_cap, 4),
        "remainder_not_extracted": [
            "coupling caps (open platform data is total-cap only)",
            "in-macro WL distribution (see extract_parasitics.py)",
            "LISD/LIG local-interconnect stubs at pin landings",
        ],
    }
    print(f"{len(out)} nets, total {total_cap:.4f} fF:")
    for n, v in out.items():
        print(f"  {n:>24}: {v['cap_ff']:8.4f} fF  {v['res_ohm']:9.1f} ohm  "
              f"{v['wirelength_by_layer_um']}")
    if args.output:
        args.output.write_text(json.dumps(summary, indent=2) + "\n")
        print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
