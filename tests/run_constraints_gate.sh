#!/bin/bash
# P2 constraints gate: the Liberty-constraint measurements must run end to
# end through Xyce and land in physically sane ranges.
#
#   [1] sweep (d64): cell_rise non-decreasing along the load axis
#   [2] setup/hold via the Liberty 10%-pushout criterion: |values| < 50 ps,
#       consistent with the vendor SEQ lib's D-pin grid (8..11 / -3..+10 ps)
#   [3] min_period: 10..500 ps (measured 27.4 ps ~= t_cq + setup)
#   [4] pin caps: 0.02..3 fF per pin (measured ~0.31 fF D / ~0.22 fF clk with
#       the non-probed input held low; vendor 0.47..0.56)
#   [5] power, measured on the clk->Q read column (sense amp + latch + output
#       stage, 256 deep): leakage 1 pW..100 uW (measured ~1.5 nW standby; the
#       earlier deck read tens of uW from floating wordlines / a metastable
#       output latch), read energy 0.001..50 pJ (measured ~8.5 fJ per access
#       over one full 4 ns cycle including the bitline restore)
#
# Every python check must fail the gate: the blocks below all carry '|| exit 1'.
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

run_mode() { # $1=mode $2=extra-args... ; echoes stdout log path via last arg convention
    local mode="$1"; shift
    python3 scripts/characterize_read.py --mode "$mode" \
        --simulator xyce --sim-exe "$XYCE" --real-device --models "$MODELS" \
        --workdir "$OUT/$mode" "$@"
}

echo "[1/4] sweep d64 -> load monotonicity..."
run_mode sweep --depths 64 >"$OUT/sweep.log" 2>&1 \
    || { echo "FAIL: sweep failed"; tail -10 "$OUT/sweep.log"; exit 1; }
python3 - "$OUT/sweep/sweep_d64.json" <<'EOF' || exit 1
import json, sys
t = json.load(open(sys.argv[1]))["timing"]["delay"]
for row_i, row in enumerate(t["cell_rise"]):
    loads = t["index_2"]
    for a, b, la, lb in zip(row, row[1:], loads, loads[1:]):
        assert b >= a - 0.005, (
            f"cell_rise not monotonic in load at slew {t['index_1'][row_i]}ns: "
            f"{a:.4f}@{la}pF -> {b:.4f}@{lb}pF")
print("  PASS: cell_rise non-decreasing along load at every slew")
EOF

echo "[2/4] setup/hold (Liberty 10%-pushout)..."
run_mode setuphold --setuphold-criterion pushout >"$OUT/sh.log" 2>&1 \
    || { echo "FAIL: setuphold failed"; tail -10 "$OUT/sh.log"; exit 1; }
SU=$(grep -oP 'setup = \K[0-9.]+' "$OUT/sh.log")
HO=$(grep -oP 'hold = \K[-0-9.]+' "$OUT/sh.log")
python3 -c "
su, ho = float('$SU'), float('$HO')
assert 0 <= su <= 50, f'setup {su} ps out of range'
assert -30 <= ho <= 50, f'hold {ho} ps out of range'
print(f'  PASS: setup={su} ps hold={ho} ps within sane ranges')" || exit 1

echo "[3/4] min_period..."
run_mode minperiod >"$OUT/mp.log" 2>&1 \
    || { echo "FAIL: minperiod failed"; tail -10 "$OUT/mp.log"; exit 1; }
MP=$(python3 -c "import json;print(json.load(open('$OUT/minperiod/minperiod.json'))['clock_min_period'])")
python3 -c "
mp = float('$MP') * 1000
assert 10 <= mp <= 500, f'min_period {mp} ps out of range'
print(f'  PASS: min_period = {mp:.1f} ps')" || exit 1

echo "[4/4] pin caps..."
run_mode pincap >"$OUT/pc.log" 2>&1 \
    || { echo "FAIL: pincap failed"; tail -10 "$OUT/pc.log"; exit 1; }
python3 -c "
import json
caps = json.load(open('$OUT/pincap/pincaps.json'))['pin_capacitance']
for k, v in caps.items():
    assert v is not None and 0.02 <= v <= 3.0, f'{k}: {v} fF out of range'
print(f'  PASS: caps {caps} fF within sane ranges')" || exit 1

echo "[5/5] power (leakage + read energy)..."
python3 scripts/extract_parasitics.py --output "$OUT/parasitics.json" >/dev/null \
    || { echo "FAIL: parasitic extraction failed"; exit 1; }
run_mode power --parasitics-json "$OUT/parasitics.json" >"$OUT/pw.log" 2>&1 \
    || { echo "FAIL: power mode failed"; tail -10 "$OUT/pw.log"; exit 1; }
python3 -c "
import json
p = json.load(open('$OUT/power/power.json'))['power']
assert 1e-6 <= p['leakage_uw'] <= 100, f'leakage {p[\"leakage_uw\"]} uW out of range'
assert 0.001 <= p['read_access_pj'] <= 50, f'read energy {p[\"read_access_pj\"]} pJ out of range'
print(f\"  PASS: leakage={p['leakage_uw']} uW, read={p['read_access_pj']} pJ/access\")" || exit 1

echo "constraints_gate: PASS"
