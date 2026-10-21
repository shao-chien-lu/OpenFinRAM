#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
generator="$repo_root/scripts/generate_asap7_8t_iocolumn.py"
array_generator="$repo_root/scripts/generate_asap7_wordline_arrays.py"

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

generated="$scratch/sram_8t_iocolumn.gds"
generated_spice="$scratch/sram_8t_iocolumn.sp"
committed_spice="$repo_root/tech/spice/sram_8t_iocolumn.sp"
"$python_bin" "$generator" --output "$generated" --spice-output "$generated_spice"
"$python_bin" "$generator" --verify "$generated" --verify-spice "$committed_spice"
"$python_bin" "$repo_root/scripts/tech_gds.py" --check "$generated"
if ! cmp -s "$generated_spice" "$committed_spice"; then
    echo "FAIL: committed 8T IO-column SPICE is stale; regenerate it with:" >&2
    echo "  $python_bin scripts/generate_asap7_8t_iocolumn.py" >&2
    exit 1
fi

# Carry a non-academic wordline count through both parameterized stages so the
# IO column cannot accidentally depend on one of the tracked ladder sizes.
nonstandard_arrays="$scratch/sram_wordline_arrays_x18.gds"
nonstandard_column="$scratch/sram_8t_iocolumn_x18.gds"
"$python_bin" "$array_generator" \
    --word-lines 18 --output "$nonstandard_arrays"
"$python_bin" "$generator" \
    --word-lines 18 --arrays-gds "$nonstandard_arrays" \
    --output "$nonstandard_column" --spice-output "$scratch/x18.sp"
"$python_bin" "$generator" \
    --word-lines 18 --verify "$nonstandard_column" --verify-spice "$scratch/x18.sp"

echo "PASS: routed dual-port 8T IO columns and non-standard x18 hierarchy"
