#!/usr/bin/env bash
# End-to-end physical compiler gate. Reports/artifacts remain in tmp/results.
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
if ! "$python_bin" -c 'import gdstk' >/dev/null 2>&1 ||
   ! python3 -c 'import sys; sys.path.insert(0,"scripts"); from def_to_gds import _load_klayout_db; _load_klayout_db()' >/dev/null 2>&1; then
    echo "SKIP: gdstk or KLayout bindings unavailable"
    exit 77
fi
mkdir -p "$repo_root/tmp"
scratch="$(mktemp -d "$repo_root/tmp/2rw_integration_XXXXXX")"
binary="${OPENFINRAM_BIN:-$repo_root/build/OpenFinRAM}"
if ! "$binary" --openroad --num-wls 2 --num-data-bits 2 --num-banks 1 \
        --skip-characterization >"$scratch/compiler.log" 2>&1; then
    tail -n 60 "$scratch/compiler.log"
    echo "FAIL: compiler log: $scratch/compiler.log"
    exit 1
fi
"$python_bin" - "$scratch/compiler.log" <<'PY'
from pathlib import Path
import re
import sys
sys.path.insert(0, "tests/tools")
from check_2rw_macro import check
log = Path(sys.argv[1]).read_text()
match = re.search(r"results/(sram_x4x2x1_\d{8}_\d{6})/", log)
assert match, "no result directory in compiler log"
folder = Path("results") / match[1]
assert ".INCLUDE" not in (folder / "sram_x4x2x1.sp").read_text().upper(), "deck depends on tmp files"
check(folder)
print(f"Artifacts: {folder}")
PY
folder="$(grep -o 'results/sram_x4x2x1_[0-9]\{8\}_[0-9]\{6\}' "$scratch/compiler.log" | head -n 1)"
python3 tests/check_macro_pins.py "$folder/sram_x4x2x1.gds" "$folder/sram_x4x2x1.lef"
