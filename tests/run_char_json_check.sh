#!/bin/bash
# P0 characterization-JSON gate for the Liberty emitter.
#
# 1. A JSON carrying exactly the estimated early-PPA constants
#    (tests/golden/characterization_estimated_passthrough.json) must
#    regenerate the estimated .lib BYTE-FOR-BYTE vs the plain --liberty-from-
#    less run.
# 2. A measured-values JSON (tests/golden/characterization_xyce_fixture.json)
#    must actually override timing, scalars and the library comment, and its
#    per-sdel variants must be emitted as standalone <stem>_<variant>.lib
#    files with variant-specific tables.
set -u
cd "$(dirname "$0")/.."
REPO="$(pwd)"

# Binary resolution: OPENFINRAM_BIN (set by ctest) wins; otherwise the newest
# of ./build/OpenFinRAM or any build*/OpenFinRAM relative to the repo root.
BIN=""
for c in "${OPENFINRAM_BIN:-}" build/OpenFinRAM build/*/OpenFinRAM; do
    [ -x "$c" ] && BIN="$c" && break
done
if [ -z "$BIN" ]; then
    echo "FAIL: OpenFinRAM binary not found (build the project first, or set OPENFINRAM_BIN)"
    exit 1
fi
BIN="$(cd "$(dirname "$BIN")" && pwd)/$(basename "$BIN")"

OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT

gen() { # $1 = run tag; remaining args passed to the generator
    local tag="$1"; shift
    local extra="${*:-}"
    local dir="$OUT/run$tag"
    mkdir -p "$dir/results"
    ln -sfn "$REPO/tech" "$dir/tech"
    ( cd "$dir" && "$BIN" --single-port --num-wls 2 --num-data-bits 4 \
          --num-banks 1 --skip-characterization $extra >gen.log 2>&1 )
    local lib
    # Main macro .lib only (exclude per-sdel variant files).
    lib="$(ls -t "$dir"/results/*/*.lib 2>/dev/null | grep -v "_sdel" | head -1)"
    if [ -z "$lib" ]; then
        echo "FAIL: no Liberty produced (run $tag); log tail:"
        tail -5 "$dir/gen.log"
        exit 1
    fi
    echo "$lib"
}

PASSTHROUGH="$REPO/tests/golden/characterization_estimated_passthrough.json"
MEASURED="$REPO/tests/golden/characterization_xyce_fixture.json"

A="$(gen 1)"
B="$(gen 2 --liberty-from "$PASSTHROUGH")"

if ! cmp -s "$A" "$B"; then
    echo "FAIL: passthrough characterization JSON is not byte-identical to the estimated model"
    diff "$A" "$B" | head -20
    exit 1
fi
echo "PASS: passthrough JSON regenerates the estimated .lib byte-for-byte"

if cmp -s "$A" "$(dirname "$B")/nonexistent" 2>/dev/null; then true; fi
C="$(gen 3 --liberty-from "$MEASURED")"
if cmp -s "$A" "$C"; then
    echo "FAIL: measured JSON produced output identical to estimated model"
    exit 1
fi
if ! grep -q 'values ("0.217, 0.272", "0.245, 0.3")' "$C"; then
    echo "FAIL: measured cell_rise table not found in $C"
    exit 1
fi
if ! grep -q 'XYCE TRANSIENT CHARACTERIZED' "$C"; then
    echo "FAIL: library comment not overridden by measured JSON"
    exit 1
fi
if ! grep -q 'min_period : 1.4;' "$C"; then
    echo "FAIL: clock_min_period override missing"
    exit 1
fi
if ! grep -q 'cell_leakage_power : 12.5;' "$C"; then
    echo "FAIL: leakage override missing"
    exit 1
fi
echo "PASS: measured values override timing/scalars/comment"

for v in sdel0 sdel1; do
    VL="$(ls "$OUT/run3"/results/*/*_"$v".lib 2>/dev/null | head -1)"
    if [ -z "$VL" ]; then
        echo "FAIL: variant $v .lib not emitted"
        exit 1
    fi
    if [ "$v" = sdel0 ] && ! grep -q 'values ("0.15, 0.16", "0.19, 0.21")' "$VL"; then
        echo "FAIL: variant $v tables wrong in $VL"
        exit 1
    fi
done
echo "PASS: per-sdel variants emitted as standalone .lib files"

echo "char_json_equiv_check: all gates passed"
