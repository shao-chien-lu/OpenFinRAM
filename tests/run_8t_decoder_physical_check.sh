#!/usr/bin/env bash
# Actual routes exercise one-bit ports, tap phase changes, and an opposite half.
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
python_bin="${PYTHON:-python3}"
if [[ -x .venv/bin/python ]]; then python_bin=.venv/bin/python; fi
for tool in yosys openroad python3; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "SKIP: $tool unavailable"
        exit 77
    fi
done
if ! "$python_bin" -c 'import gdstk' >/dev/null 2>&1 ||
   ! python3 -c 'import sys; sys.path.insert(0,"scripts"); from def_to_gds import _load_klayout_db; _load_klayout_db()' >/dev/null 2>&1; then
    echo "SKIP: gdstk or KLayout bindings unavailable"
    exit 77
fi
mkdir -p tmp
scratch="$(mktemp -d "$repo_root/tmp/decoder_physical_XXXXXX")"
for count in 1 2 32; do
    taps=0
    if [[ "$count" == 32 ]]; then taps=16; fi
    "$python_bin" scripts/generate_asap7_8t_decoder.py \
        --word-lines "$count" --tap-pitch "$taps" --route --work "$scratch/x$count"
done
"$python_bin" scripts/generate_asap7_8t_decoder.py \
    --word-lines 18 --tap-pitch 6 --mirror-x --route --work "$scratch/x18_mirror"
echo "PASS: routed decoder parameter checks; artifacts: $scratch"
