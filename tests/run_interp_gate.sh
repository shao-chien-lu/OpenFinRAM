#!/bin/bash
# P5 interpolation gate: characterize a depth LADDER, interpolate to a HELD-
# OUT depth, then measure that depth for real and compare. The interpolation
# is only trusted as far as this held-out residual.
#
#   ladder: 64 and 256 words -> interpolated tables at 128 words
#   truth :  measured d64 sweep (same chain)
#   gate  :  every cell_rise/cell_fall/transition entry within 15%
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

MODELS="$REPO/tech/models/hspice/7nm_TT.pm"
OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT

for D in 64 256 128; do
    echo "[sweep d$D]"
    python3 scripts/characterize_read.py --mode sweep --depths $D \
        --simulator xyce --sim-exe "$XYCE" --real-device --models "$MODELS" \
        --workdir "$OUT/sweep_$D" >"$OUT/sweep_$D.log" 2>&1 \
        || { echo "FAIL: d$D sweep failed"; tail -8 "$OUT/sweep_$D.log"; exit 1; }
done

python3 scripts/gen_interpolated_char.py \
    --rung "$OUT/sweep_64/sweep_d64.json=64" \
    --rung "$OUT/sweep_256/sweep_d256.json=256" \
    --target-depth 128 --output "$OUT/char_d128_interp.json" \
    || { echo "FAIL: interpolation failed"; exit 1; }

python3 - "$OUT/char_d128_interp.json" "$OUT/sweep_128/sweep_d128.json" <<'EOF' || exit 1
import json, sys

interp = json.load(open(sys.argv[1]))["timing"]["delay"]
truth = json.load(open(sys.argv[2]))["timing"]["delay"]

TOL = 0.15
worst = 0.0
for key in ("cell_rise", "cell_fall"):
    for ri, (row_i, row_t) in enumerate(zip(interp[key], truth[key])):
        for ci, (v_i, v_t) in enumerate(zip(row_i, row_t)):
            err = abs(v_i - v_t) / v_t
            worst = max(worst, err)
            assert err <= TOL, (
                f"{key}[{ri}][{ci}]: interp {v_i:.4f} vs measured {v_t:.4f} "
                f"({err*100:.1f}% > {TOL*100:.0f}%)")
for key in ("rise_transition", "fall_transition"):
    for ci, (v_i, v_t) in enumerate(zip(interp[key], truth[key])):
        if v_i is None or v_t is None:
            continue
        err = abs(v_i - v_t) / v_t
        worst = max(worst, err)
        assert err <= TOL, f"{key}[{ci}] off by {err*100:.1f}%"

print(f"  PASS: held-out d128 residual max {worst*100:.2f}% <= {TOL*100:.0f}%")
EOF

echo "interp_gate: PASS"
