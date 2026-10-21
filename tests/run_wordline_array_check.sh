#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
generator="$repo_root/scripts/generate_asap7_wordline_arrays.py"

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

# The default artifact mirrors the academic 32/64/128 ladder and includes the
# compiler's two-wordline smoke geometry.
generated="$scratch/sram_wordline_arrays.gds"
"$python_bin" "$generator" --output "$generated"
"$python_bin" "$generator" --verify "$generated"
"$python_bin" "$repo_root/scripts/tech_gds.py" --check "$generated"

# Exercise a genuinely non-academic wordline count and a non-default mux
# height for both the 8T and back-ported 6T builders.
nonstandard="$scratch/sram_wordline_arrays_x18_m2.gds"
"$python_bin" "$generator" --word-lines 18 --mux-rows 2 --output "$nonstandard"
"$python_bin" "$generator" --word-lines 18 --mux-rows 2 --verify "$nonstandard"

# Well/substrate taps interleaved into the 8T rows.  The tap hands every
# bitline through, so the abutment probes must still find them continuous.
tapped="$scratch/sram_wordline_arrays_tap8.gds"
"$python_bin" "$generator" --word-lines 32 --tap-pitch 8 --output "$tapped"
"$python_bin" "$generator" --word-lines 32 --tap-pitch 8 --verify "$tapped"

# Upgrade a subset of the taps to power straps.  The strap carries the same
# ties and bitline pass-throughs, so every abutment probe must still pass.
strapped="$scratch/sram_wordline_arrays_tap8_strap2.gds"
"$python_bin" "$generator" --word-lines 32 --tap-pitch 8 --strap-pitch 2 \
    --output "$strapped"
"$python_bin" "$generator" --word-lines 32 --tap-pitch 8 --strap-pitch 2 \
    --verify "$strapped"

echo "PASS: parameterized 8T/6T wordline rows, arrays, taps, straps, and 8T IO pitch"
