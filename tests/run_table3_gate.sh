#!/bin/bash
# P6 validation gate: MOST-report Table III reproduction through the full
# open-source chain:
#
#   Xyce transient (real ASAP7 BSIM-CMG, real read path)
#     -> scripts/gen_characterization_json.py  (measured .mt0 -> JSON)
#     -> OpenFinRAM --liberty-from             (JSON -> .lib)
#     -> validator                             (.lib values vs report)
#
# Bitline cap comes from scripts/extract_parasitics.py (geometry-derived
# wire cap, midpoint of its bracket); the un-extracted junction/via remainder
# is why measured clk->Q may sit slightly below the characterized reference -
# still gated at 15%.

# Gate: measured clk->Q and output transitions must land within 15% of the
# SiliconSmart-characterized "ours" column of the report's Table III
# (256-word x 64-bit class, stimulus slew 0.04 ns / load 0.04608 pF).
# The vendor srambank lib is printed for reference only, NOT gated: it is a
# mux-4 part (64-row effective bitline) vs this flow's full-depth sweep, so
# its numbers are not expected to agree within tolerance.
#
# Skips (exit 77) when Xyce is unavailable.
set -u
cd "$(dirname "$0")/.."
REPO="$(pwd)"

XYCE="/home/jeff/iv4/local/xyce-14.4/bin/Xyce"
[ -x "$XYCE" ] || XYCE="$(command -v Xyce || true)"
if [ -z "${XYCE:-}" ] || [ ! -x "$XYCE" ]; then
    echo "SKIP: Xyce not available"
    exit 77
fi

BIN=""
for c in "${OPENFINRAM_BIN:-}" build/OpenFinRAM build/*/OpenFinRAM; do
    [ -x "$c" ] && BIN="$c" && break
done
if [ -z "$BIN" ]; then
    echo "FAIL: OpenFinRAM binary not found (build the project first, or set OPENFINRAM_BIN)"
    exit 1
fi
BIN="$(cd "$(dirname "$BIN")" && pwd)/$(basename "$BIN")"

MODELS="$REPO/tech/models/hspice/7nm_TT.pm"
OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT

echo "[1/4] Xyce table3 measurement (256-word depth, TT)..."
python3 scripts/extract_parasitics.py --output "$OUT/parasitics.json" \
    || { echo "FAIL: parasitic extraction failed"; exit 1; }
python3 scripts/characterize_read.py --mode table3 --depths 256 \
    --simulator xyce --sim-exe "$XYCE" --real-device --models "$MODELS" \
    --parasitics-json "$OUT/parasitics.json" \
    --workdir "$OUT/char" >"$OUT/table3.log" 2>&1 \
    || { echo "FAIL: table3 measurement failed"; tail -20 "$OUT/table3.log"; exit 1; }
grep -A5 "^ *256-word" "$OUT/table3.log" | head -6

MT0="$OUT/char/table3_d256.sp.mt0"
[ -s "$MT0" ] || { echo "FAIL: no .mt0 produced"; exit 1; }

echo "[2/4] Measured values -> characterization JSON..."
python3 scripts/gen_characterization_json.py --xyce-mt0 "$MT0" \
    --depth-words 256 --output "$OUT/char_d256.json" || exit 1

echo "[3/4] Emitting Liberty through the JSON path..."
RUN="$OUT/run"
mkdir -p "$RUN/results"
ln -sfn "$REPO/tech" "$RUN/tech"
( cd "$RUN" && "$BIN" --single-port --num-wls 2 --num-data-bits 64 \
      --num-banks 1 --skip-characterization \
      --liberty-from "$OUT/char_d256.json" >gen.log 2>&1 )
LIB="$(ls "$RUN"/results/*/sram_x4x64x1.lib 2>/dev/null | head -1)"
[ -n "$LIB" ] || { echo "FAIL: Liberty not emitted"; tail -10 "$RUN/gen.log"; exit 1; }
grep -q "MEASURED XYCE TRANSIENT" "$LIB" \
    || { echo "FAIL: emitted .lib does not carry the measured banner"; exit 1; }

echo "[4/4] Validating .lib against Table III references..."
python3 - "$LIB" <<'EOF' || exit 1
import re, sys

lib = open(sys.argv[1]).read()

def table_values(name):
    """values (...) rows following the named table."""
    m = re.search(rf"\b{name}\s*\([^)]*\)\s*\{{(.*?)\}}", lib, re.S)
    if not m:
        return None
    v = re.search(r'values\s*\(([^)]*)\)', m.group(1), re.S)
    if not v:
        return None
    return [float(x) for x in re.findall(r'[-+0-9.eE]+', v.group(1))]

measured = {
    "cell_rise":       table_values("cell_rise"),
    "cell_fall":       table_values("cell_fall"),
    "rise_transition": table_values("rise_transition"),
    "fall_transition": table_values("fall_transition"),
}
assert all(v is not None for v in measured.values()), f"missing tables: {measured}"

# MOST-report Table III, "Ours" column, 256x64 class [ns].
REPORT_OURS = {"cell_rise": 0.216, "rise_transition": 0.064,
               "cell_fall": 0.216, "fall_transition": 0.048}
# Vendor Cadence-characterized srambank_256x4x64 delays [ns] (mux-4 config;
# transition conventions differ, so only delays are compared).
VENDOR_DELAY = {"cell_rise": 0.198, "cell_fall": 0.191}

TOL = 0.15
fail = False
for k, ours in measured.items():
    val = ours[len(ours)//2]
    ref = REPORT_OURS[k]
    err = (val - ref) / ref
    status = "PASS" if abs(err) <= TOL else "FAIL"
    if status == "FAIL":
        fail = True
    print(f"  {k:>16}: lib={val:.4f} ns vs report-ours={ref:.3f} ns "
          f"({err*100:+.1f}%) {status}")

print("  vendor reference (informational, mux-4 config - not gated):")
for k, ref in VENDOR_DELAY.items():
    val = measured[k][len(measured[k])//2]
    err = (val - ref) / ref
    print(f"    {k:>16}: lib={val:.4f} ns vs vendor={ref:.3f} ns ({err*100:+.1f}%)")

sys.exit(1 if fail else 0)
EOF

echo "table3_gate: PASS - measured clk->Q within 15% of the report's characterized column"
