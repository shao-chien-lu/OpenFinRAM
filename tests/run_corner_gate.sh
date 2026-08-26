#!/bin/bash
# P4 corner gate: three real characterization libs (SS/TT/FF), produced by
# the full open chain at each corner, must be physically ordered.
#
#   SS 0.63 V > TT 0.70 V > FF 0.77 V   for clk->Q delay
# and each emitted .lib must carry its corner in nom_voltage /
# operating_conditions so multicorner STA binds the right file.
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
[ -z "$BIN" ] && { echo "FAIL: OpenFinRAM binary not found"; exit 1; }
BIN="$(cd "$(dirname "$BIN")" && pwd)/$(basename "$BIN")"

OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT

declare -A VDD=( [SS]=0.63 [TT]=0.70 [FF]=0.77 )
for CORNER in TT SS FF; do
    echo "[corner $CORNER @ ${VDD[$CORNER]} V] sweep d64..."
    python3 scripts/characterize_read.py --mode sweep --depths 64 \
        --simulator xyce --sim-exe "$XYCE" --real-device \
        --models "$REPO/tech/models/hspice/7nm_${CORNER}.pm" \
        --vdd "${VDD[$CORNER]}" \
        --workdir "$OUT/sweep_$CORNER" >"$OUT/sweep_$CORNER.log" 2>&1 \
        || { echo "FAIL: $CORNER sweep failed"; tail -8 "$OUT/sweep_$CORNER.log"; exit 1; }
done

echo "emitting corner Liberty files..."
declare -A LIBPATH
for CORNER in TT SS FF; do
    JSON=$(ls "$OUT"/sweep_$CORNER/sweep_d64*.json | head -1)
    RUN="$OUT/run_$CORNER"
    mkdir -p "$RUN/results"
    ln -sfn "$REPO/tech" "$RUN/tech"
    ( cd "$RUN" && "$BIN" --single-port --num-wls 2 --num-data-bits 4 \
          --num-banks 1 --skip-characterization --liberty-from "$JSON" >gen.log 2>&1 )
    LIBPATH[$CORNER]="$(find "$RUN" -name sram_x4x4x1.lib | head -1)"
    [ -n "${LIBPATH[$CORNER]}" ] || { echo "FAIL: no .lib for $CORNER"; exit 1; }
done

python3 - "${LIBPATH[SS]}" "${LIBPATH[TT]}" "${LIBPATH[FF]}" <<'EOF' || exit 1
import re, sys

def mid_cell_rise(path):
    lib = open(path).read()
    m = re.search(r"\bcell_rise\s*\([^)]*\)\s*\{(.*?)\}", lib, re.S)
    vals = [float(x) for x in re.findall(r'[-+0-9.eE]+',
             re.search(r'values\s*\(([^)]*)\)', m.group(1), re.S).group(1))]
    return vals[len(vals)//2]

def nominal_volt(path):
    return float(re.search(r'nom_voltage : ([0-9.]+)', open(path).read()).group(1))

def oc_name(path):
    return re.search(r'default_operating_conditions : (\w+)', open(path).read()).group(1)

ss, tt, ff = (mid_cell_rise(p) for p in sys.argv[1:4])
print(f"  clk->Q @mid-grid: SS={ss*1e3:.1f} ps  TT={tt*1e3:.1f} ps  FF={ff*1e3:.1f} ps")
assert ss > tt > ff, f"corner ordering violated: SS({ss}) TT({tt}) FF({ff})"

for path, want_v, name in zip(sys.argv[1:4], (0.63, 0.70, 0.77), ("SS","TT","FF")):
    v = nominal_volt(path)
    assert abs(v - want_v) < 1e-6, f"{name}: nom_voltage {v} != {want_v}"
    print(f"  {name}: nom_voltage={v} oc={oc_name(path)} OK")

print("  PASS: SS > TT > FF and each lib carries its own corner")
EOF

echo "corner_gate: PASS"
