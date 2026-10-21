#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="${PYTHON:-python3}"
if [[ -x "$repo_root/.venv/bin/python" ]]; then
    python_bin="$repo_root/.venv/bin/python"
fi
for tool in yosys iverilog vvp; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "SKIP: $tool unavailable" >&2
        exit 77
    fi
done
if ! "$python_bin" -c 'import gdstk' >/dev/null 2>&1; then
    echo "SKIP: gdstk unavailable" >&2
    exit 77
fi
"$python_bin" "$repo_root/tests/tools/test_8t_decoder.py"
