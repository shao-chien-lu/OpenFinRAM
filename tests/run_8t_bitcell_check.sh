#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
generator="$repo_root/scripts/generate_asap7_8t_bitcell.py"

if [[ -x "$repo_root/.venv/bin/python" ]]; then
    python_bin="$repo_root/.venv/bin/python"
else
    python_bin="${PYTHON:-python3}"
fi

if ! "$python_bin" -c 'import gdstk' >/dev/null 2>&1; then
    echo "SKIP: gdstk Python module is unavailable" >&2
    exit 77
fi

scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT
generated="$scratch/sram_cell_8t.gds"
generated_edges="$scratch/sram_cell_8t_edges.gds"
generated_tap="$scratch/sram_cell_8t_tap.gds"

"$python_bin" "$generator" --output "$generated"
"$python_bin" "$generator" --verify "$generated"
"$python_bin" "$generator" --verify-edges "$generated_edges"
"$python_bin" "$generator" --verify-tap "$generated_tap" --output "$generated"
"$python_bin" "$repo_root/scripts/tech_gds.py" --check "$generated" "$generated_edges" "$generated_tap"

"$python_bin" "$repo_root/tests/tools/test_8t_edges.py"

echo "PASS: ASAP7 8T bitcell/edge/tap topology, tiling, rules, and artifacts"
