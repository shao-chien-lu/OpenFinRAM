#!/usr/bin/env python3
"""Check all handcrafted libraries against an engine-specific DRC ratchet."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from drc import REPO, find_gdscheck, provenance, run_device_drc

LIBRARIES = ("sram_cell_8t", "sram_cell_8t_tap", "sram_cell_8t_edges", "sram_8t_iocolumn")
CALIBRATION_CELLS = ("sram_cell_6t_122", "tapcell_sram_6t122", "dummy_sram_6t122",
                     "senseamp_sram_6t122", "iocolgrp_sram_6t122_v2")


def compare(current: dict, baseline: dict) -> tuple[list[str], list[str]]:
    regressions, fixed = [], []
    for key, now in sorted(current.items()):
        was = baseline.get(key)
        if was is None:
            regressions.append(f"NEW {key}: {now}")
            continue
        if now["limit_nm"] != was["limit_nm"]:
            regressions.append(f"LIMIT {key}: {was['limit_nm']} -> {now['limit_nm']}")
        if now["count"] > was["count"]:
            regressions.append(f"MORE {key}: {was['count']} -> {now['count']}")
        if now["worst_nm"] < was["worst_nm"]:
            regressions.append(f"TIGHTER {key}: {was['worst_nm']} -> {now['worst_nm']} nm")
        if now["count"] < was["count"] or now["worst_nm"] > was["worst_nm"]:
            fixed.append(key)
    fixed += sorted(set(baseline) - set(current))
    return regressions, fixed


def reference(gds: Path, out: Path, cells: tuple[str, ...] = ()) -> list[dict]:
    binary = os.environ.get("KLAYOUT") or shutil.which("klayout")
    if not binary:
        raise FileNotFoundError("klayout unavailable for the reference check")
    cmd = [binary, "-b", "-r", str(REPO / "tech/drc/asap7_device.drc"), "-rd", f"gds={gds}"]
    if cells:
        cmd += ["-rd", "cells=" + ",".join(cells)]
    proc = subprocess.run(cmd, check=True, text=True, capture_output=True, timeout=600)
    out.mkdir(parents=True, exist_ok=True)
    (out / "reference.json").write_text(proc.stdout)
    return json.loads(proc.stdout)["findings"]


def scan(gds: Path, out: Path, binary: Path, cells: tuple[str, ...] = (), jobs: int = 2) -> list[dict]:
    import gdstk

    available = sorted(gdstk.read_gds(str(gds)).cells, key=lambda c: c.name)
    names = [c.name for c in available]
    if not names or set(cells) - set(names):
        raise ValueError(f"Missing cells in {gds}: {set(cells) - set(names)}")
    names = list(cells) if cells else names

    def check(entry):
        index, name = entry
        return run_device_drc(gds, name, out / str(index), binary=binary)["findings"]

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        return [finding for findings in pool.map(check, enumerate(names)) for finding in findings]


def keyed(library: str, findings: list[dict]) -> dict:
    return {f"{library}|{f['cell']}|{f['rule']}":
            {key: f[key] for key in ("count", "worst_nm", "limit_nm")} for f in findings}


def cross_check(current: dict, reference_findings: dict) -> list[str]:
    """Counts differ between engines; coverage and worst distances must agree.

    gdscheck's report rounds to 0.1 nm, so allow half that quantization step.
    This is agreement on cell/rule minima, not proof of marker-by-marker parity.
    """
    problems = []
    for key in sorted(current.keys() | reference_findings.keys()):
        now, ref = current.get(key), reference_findings.get(key)
        if now is None or ref is None:
            problems.append(f"Coverage differs: {key}")
        elif now["limit_nm"] != ref["limit_nm"] or abs(now["worst_nm"] - ref["worst_nm"]) > 0.050001:
            problems.append(f"Distance/limit differs: {key}: {now} vs {ref}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=("gdscheck", "klayout"), default="gdscheck")
    parser.add_argument("--out", type=Path, default=REPO / "tmp/device_drc")
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--cross-check", action="store_true", help="Also record the KLayout reference and differences")
    parser.add_argument("--jobs", type=int, default=2)
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    args.out.mkdir(parents=True, exist_ok=True)
    try:
        if args.engine == "gdscheck":
            binary = find_gdscheck()
            import gdstk  # noqa: F401

            identity = provenance(binary)
        else:
            binary = None
            if not (os.environ.get("KLAYOUT") or shutil.which("klayout")):
                raise FileNotFoundError("klayout unavailable")
            identity = {"engine": "klayout"}
    except (FileNotFoundError, ImportError) as exc:
        print(f"SKIP: {exc}", file=sys.stderr)
        return 77
    baseline_path = args.baseline or REPO / "tests/golden" / (
        "asap7_8t_drc_gdscheck.json" if binary else "asap7_8t_drc_baseline.json")

    def check(gds, out, cells=()):
        if binary:
            return scan(gds, out, binary, cells, args.jobs)
        return reference(gds, out, cells)

    calibration_gds = REPO / "tech/gds/srambank_32b_boundary_2.gds"
    calibration = check(calibration_gds, args.out / "calibration", CALIBRATION_CELLS)
    if calibration:
        raise ValueError(f"Vendor calibration is not clean: {calibration}")
    if args.cross_check and reference(calibration_gds, args.out / "reference/calibration", CALIBRATION_CELLS):
        raise ValueError("KLayout vendor calibration is not clean")
    current, reference_findings = {}, {}
    for library in LIBRARIES:
        gds = REPO / "tech/gds" / f"{library}.gds"
        findings = check(gds, args.out / library)
        current.update(keyed(library, findings))
        if args.cross_check:
            reference_findings.update(keyed(library, reference(gds, args.out / "reference" / library)))
        print(f"{library}: {len(findings)} cell/rule findings", flush=True)
    result = {**identity, "findings": dict(sorted(current.items()))}
    (args.out / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    if args.cross_check:
        differences = {key: {"gdscheck": current.get(key), "klayout": reference_findings.get(key)}
                       for key in sorted(current.keys() | reference_findings.keys())
                       if current.get(key) != reference_findings.get(key)}
        (args.out / "cross_check.json").write_text(json.dumps(differences, indent=2) + "\n")
        print(f"Cross-check: {len(differences)} differences; see {args.out / 'cross_check.json'}")
        problems = cross_check(current, reference_findings)
        if problems:
            print("FAIL: engines disagree:\n" + "\n".join(problems), file=sys.stderr)
            return 1
    if args.write_baseline:
        # Pin the deck/version and record the executable hash for auditing.
        baseline_path.write_text(json.dumps(result if binary else current, indent=2, sort_keys=True) + "\n")
        print(f"Wrote {baseline_path}")
        return 0
    baseline = json.loads(baseline_path.read_text())
    if binary:
        for key in ("engine", "profile", "deck_sha256", "version"):
            if baseline.get(key) != identity[key]:
                raise ValueError(f"Baseline {key} differs; cross-check before updating it")
        baseline = baseline["findings"]
    regressions, fixed = compare(current, baseline)
    if regressions:
        print("FAIL: device DRC regressed:\n" + "\n".join(regressions), file=sys.stderr)
        return 1
    print(f"PASS: {args.engine} device DRC at baseline ({len(current)} known findings)")
    if fixed:
        print(f"{len(fixed)} improvements; tighten the baseline with --write-baseline")
    return 0


if __name__ == "__main__":
    sys.exit(main())
