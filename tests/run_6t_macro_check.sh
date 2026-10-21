#!/usr/bin/env bash
# End-to-end gate of the generated single-port 6T macro (--bitcell 6t):
# compile x4x2x1, check its interface, then strict transistor-level LVS.
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
for tool in yosys openroad python3; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "SKIP: $tool unavailable"
        exit 77
    fi
done
python_bin="${PYTHON:-python3}"
if [[ -x .venv/bin/python ]]; then python_bin=.venv/bin/python; fi
if ! "$python_bin" -c 'import gdstk, chipforge_asap7' >/dev/null 2>&1 ||
   ! python3 -c 'import sys; sys.path.insert(0,"scripts"); from def_to_gds import _load_klayout_db; _load_klayout_db()' >/dev/null 2>&1; then
    echo "SKIP: gdstk, chipforge_asap7 or KLayout bindings unavailable"
    exit 77
fi
mkdir -p "$repo_root/tmp"
scratch="$(mktemp -d "$repo_root/tmp/6t_integration_XXXXXX")"
binary="${OPENFINRAM_BIN:-$repo_root/build/OpenFinRAM}"
if ! "$binary" --openroad --bitcell 6t --num-wls 2 --num-data-bits 2 --num-banks 1 \
        --skip-characterization >"$scratch/compiler.log" 2>&1; then
    tail -n 60 "$scratch/compiler.log"
    echo "FAIL: compiler log: $scratch/compiler.log"
    exit 1
fi
folder="$("$python_bin" - "$scratch/compiler.log" <<'PY'
from pathlib import Path
import json
import re
import sys
sys.path.insert(0, "scripts")
from compile_asap7_2rw import check_route_drc, verify
log = Path(sys.argv[1]).read_text()
match = re.search(r"results/(sram_x4x2x1_\d{8}_\d{6})/", log)
assert match, "no result directory in compiler log"
folder = Path("results") / match[1]
name = "sram_x4x2x1"
report = json.loads((folder / f"{name}.physical.json").read_text())
assert report["bitcell"] == "6t" and report["physical_connectivity"] == "PASS"
work = Path(report["reports"])
verify(folder / f"{name}.gds", json.loads((work / "connectivity.json").read_text()))
check_route_drc(work / "macro_drc.rpt")
external = set(json.loads((work / "connectivity.json").read_text())["external"])
assert not any(re.search(r"_B\b|_B\[", pin) for pin in external), "a port-B pin on a single-port macro"
lef = (folder / f"{name}.lef").read_text()
assert set(re.findall(r"^\s+PIN (\S+)", lef, re.M)) == external, "LEF pins differ from the physical ports"
liberty = (folder / f"{name}.lib").read_text()
# One port, named without it: ce_n, we_n, oe_n, A[], D[], Q[].
assert external >= {"ce_n", "we_n", "oe_n", "A[0]", "D[0]", "Q[0]"}, sorted(external)
assert not any(re.search(r"_A\b|_A\[", pin) for pin in external), "a port-A name on the single-port macro"
assert "pin (we_n)" in liberty and "bus (Q)" in liberty and "contention_condition" not in liberty
assert not re.search(r"\b(ce_n|we_n|oe_n|A|D|Q)_A\b", liberty), "a port-A name in the Liberty"
deck = (folder / f"{name}.sp").read_text()
assert ".INCLUDE" not in deck.upper(), "deck depends on tmp files"
assert "sram_cell_6t_122" in deck and "iocol_sram_6t" in deck
print(folder)
PY
)"
echo "Artifacts: $folder"
"$python_bin" scripts/verify_macro.py "$folder" --no-drc --out "$scratch/verify"
