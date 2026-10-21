"""gdscheck adapter: its full ASAP7 suite, and the calibrated device subset.

`run_full_drc` (``--process asap7 --suite main``) is verify_macro.py's DRC.
`run_device_drc` is the earlier calibrated subset (``tech/drc/asap7``).  The
public KLayout runset has different coverage and rule names; never compare
its baseline to these.
"""

from __future__ import annotations

import argparse
import hashlib
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

REPO = Path(__file__).resolve().parents[1]
PDK = REPO / "tech/drc/asap7/pdk.yml"
DECK = PDK.with_name("device.json")
PROFILE = "asap7-device-v2"


def find_gdscheck() -> Path:
    override = os.environ.get("GDSCHECK")
    candidates = [override] if override else [
        str(REPO / "build/tools/gdscheck/bin/gdscheck"), shutil.which("gdscheck")
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            binary = Path(candidate).resolve()
            require_directional_gcut(binary)
            return binary
    raise FileNotFoundError("gdscheck unavailable; run bash scripts/install_gdscheck.sh or set GDSCHECK")


@lru_cache(maxsize=None)
def require_directional_gcut(binary: Path) -> None:
    """Older 0.1.2 binaries silently ignore facing: y; version alone is insufficient."""
    result = subprocess.run([str(binary), "show-deck", "--process", "asap7", "--deck", "gcut"],
                            capture_output=True, text=True, timeout=10)
    if result.returncode or not all(
        any(rule in line and "facing=y" in line for line in result.stdout.splitlines())
        for rule in ("GCUT.W.1", "GCUT.S.3")
    ):
        raise RuntimeError("gdscheck lacks native ASAP7 directional gate-cut rules; "
                           "run bash scripts/install_gdscheck.sh or set GDSCHECK to a current build")


@lru_cache(maxsize=None)
def require_full_asap7(binary: Path) -> None:
    """The embedded ASAP7 process with the main suite and the DRM's ACTIVE.W.2."""
    suites = subprocess.run([str(binary), "list-suites", "--process", "asap7"],
                            capture_output=True, text=True, timeout=10)
    active = subprocess.run([str(binary), "show-deck", "--process", "asap7", "--deck", "active"],
                            capture_output=True, text=True, timeout=10)
    if suites.returncode or active.returncode or not re.search(r"^\s*main\b", suites.stdout, re.M) \
            or "ACTIVE.W.2" not in active.stdout:
        raise RuntimeError("gdscheck lacks the full ASAP7 process (suite main, ACTIVE.W.2); "
                           "GDSCHECK_SRC=<checkout> bash scripts/install_gdscheck.sh, or set GDSCHECK")


def run_full_drc(gds: Path, cell: str, out: Path, *, binary: Path | None = None,
                 timeout: int = 3600) -> dict:
    """gdscheck's whole ASAP7 suite on `cell`: markers per rule (flat, on the top cell)."""
    binary = binary or find_gdscheck()
    require_full_asap7(binary)
    out.mkdir(parents=True, exist_ok=True)
    report = out / "drc.lyrdb"
    report.unlink(missing_ok=True)
    cmd = [str(binary), "run", "--input", str(gds.resolve()), "--process", "asap7",
           "--suite", "main", "--topcell", cell, "--report", str(report.resolve())]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    (out / "drc.log").write_text(proc.stdout + proc.stderr)
    if proc.returncode not in (0, 2) or not report.is_file():
        raise RuntimeError(f"gdscheck failed ({proc.returncode}); see {out / 'drc.log'}")
    rules: dict[str, int] = {}
    for item in ET.parse(report).getroot().findall("./items/item"):
        rule = item.findtext("category", "").strip("'\"")
        rules[rule] = rules.get(rule, 0) + int(item.findtext("multiplicity", "1"))
    return {"cell": cell, "report": str(report), "suite": "main",
            "rules": dict(sorted(rules.items())), "markers": sum(rules.values())}


def provenance(binary: Path) -> dict:
    version = subprocess.run([str(binary), "--version"], check=True, capture_output=True,
                             text=True, timeout=10).stdout.strip()
    return {
        "engine": "gdscheck", "version": version, "profile": PROFILE,
        "deck_sha256": hashlib.sha256(PDK.read_bytes() + DECK.read_bytes()).hexdigest(),
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
    }


def parse_report(report: Path, *, cell: str) -> list[dict]:
    """Normalize markers without treating skipped/waived checks as a clean run.

gdscheck writes measured distances to four decimal places in micrometres
(0.1 nm). Rule limits come from our deck, not its rounded human description.
"""
    limits = {r["id"]: round(r["value"] * 1000, 3) for r in json.loads(DECK.read_text())["rules"]}
    root = ET.parse(report).getroot()
    if root.tag != "report-database" or root.find("items") is None:
        raise ValueError(f"Malformed DRC report: {report}")
    findings = {}
    for item in root.findall("./items/item"):
        if item.findtext("tags", "").strip():
            raise ValueError(f"Skipped or waived rule in {report}: {item.findtext('category')}")
        rule = item.findtext("category", "").strip("'\"")
        if rule not in limits or item.findtext("cell") != cell:
            raise ValueError(f"Unexpected rule/cell in {report}: {rule}")
        message = " ".join(v.text or "" for v in item.findall("./values/value"))
        measured = re.search(r"\b(?:width|space|notch) ([0-9]+(?:\.[0-9]+)?) µm", message)
        if not measured:
            raise ValueError(f"Missing distance for {rule} in {report}: {message}")
        distance = round(float(measured[1]) * 1000, 3)
        count = int(item.findtext("multiplicity", "1"))
        if count < 1:
            raise ValueError(f"Invalid marker multiplicity in {report}")
        found = findings.setdefault(rule, {"cell": cell, "rule": rule, "count": 0,
                                          "worst_nm": distance, "limit_nm": limits[rule]})
        found["count"] += count
        found["worst_nm"] = min(found["worst_nm"], distance)
    return [findings[r] for r in sorted(findings)]


def run_device_drc(gds: Path, cell: str, out: Path, *, binary: Path | None = None,
                   timeout: int = 300) -> dict:
    binary = binary or find_gdscheck()
    out.mkdir(parents=True, exist_ok=True)
    report = out / "drc.lyrdb"
    # A failed invocation must never reuse a previous report.
    report.unlink(missing_ok=True)
    cmd = [str(binary), "run", "--input", str(gds.resolve()), "--process", str(PDK),
           "--deck", "device", "--topcell", cell, "--threads", "1",
           "--tile", "1", "--memory", "512M", "--report", str(report.resolve())]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    (out / "drc.log").write_text(proc.stdout + proc.stderr)
    # 2 means completed with violations; 1 is an error and 3 an incomplete run.
    if proc.returncode not in (0, 2):
        raise RuntimeError(f"gdscheck failed ({proc.returncode}); see {out / 'drc.log'}")
    findings = parse_report(report, cell=cell)
    if bool(findings) != (proc.returncode == 2):
        raise ValueError(f"gdscheck exit status disagrees with {report}")
    return {"cell": cell, "report": str(report), "findings": findings,
            "rules": {f["rule"]: f["count"] for f in findings},
            "markers": sum(f["count"] for f in findings)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gds", type=Path)
    parser.add_argument("--cell", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    binary = find_gdscheck()
    result = {**provenance(binary), **run_device_drc(args.gds, args.cell, args.out, binary=binary)}
    (args.out / "drc.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 2 if result["markers"] else 0


if __name__ == "__main__":
    sys.exit(main())
