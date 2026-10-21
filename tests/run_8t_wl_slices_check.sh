#!/usr/bin/env bash
# The wordline driver slice ladder the 2RW macro's strips are built from:
# the GDS must match its recorded digest and the SPICE the committed netlist.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
generator="$repo_root/scripts/generate_asap7_8t_wl_slices.py"
committed_spice="$repo_root/tech/spice/sram_8t_wl_slices.sp"

if [[ -x "$repo_root/.venv/bin/python" ]]; then
    python_bin="$repo_root/.venv/bin/python"
else
    python_bin="${PYTHON:-python3}"
fi

if ! "$python_bin" -c 'import gdstk, chipforge_asap7' >/dev/null 2>&1; then
    echo "SKIP: gdstk or chipforge_asap7 is unavailable" >&2
    exit 77
fi

scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT

generated="$scratch/sram_8t_wl_slices.gds"
"$python_bin" "$generator" --output "$generated" --spice-output "$scratch/slices.sp"
"$python_bin" "$generator" --verify --output "$generated" --spice-output "$scratch/slices.sp"
"$python_bin" "$repo_root/scripts/tech_gds.py" --check "$generated"
if ! cmp -s "$scratch/slices.sp" "$committed_spice"; then
    echo "FAIL: committed 8T wordline slice ladder SPICE is stale; regenerate it with:" >&2
    echo "  $python_bin scripts/generate_asap7_8t_wl_slices.py" >&2
    exit 1
fi

echo "PASS: 8T wordline driver slice ladder"
