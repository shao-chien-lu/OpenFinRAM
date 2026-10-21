#!/usr/bin/env bash
# True-dual-port ctrl_decode functional regression (Icarus Verilog).
set -u
cd "$(dirname "$0")/.."

SRC="tests/prim_models.v tests/tb_ctrl_decode_dp.sv tech/verilog_dp/sram_control.v tech/verilog_dp/delay_cell.v tech/verilog_dp/row_decoder.v"
# NUM_WL BANKS MUX SHARED_B [ROW_PREDECODE, default 3]: SHARED_B 1 shares port
# B's IO in bank pairs; 32 and 256 wordlines give the slice decode 4 and 7
# address bits, where 4-bit groups differ.
CONFIGS=("2 1 2 0" "8 2 4 0" "16 4 4 0" "8 2 4 1" "16 4 4 1" "32 2 4 1 4" "256 1 2 0 4")

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
fail=0
for cfg in "${CONFIGS[@]}"; do
    set -- $cfg
    if ! iverilog -g2012 -s tb_ctrl_decode_dp \
            -Ptb_ctrl_decode_dp.NUM_WL="$1" \
            -Ptb_ctrl_decode_dp.NUM_BANK="$2" \
            -Ptb_ctrl_decode_dp.COLUMN_MUX="$3" \
            -Ptb_ctrl_decode_dp.SHARED_B="$4" \
            -Ptb_ctrl_decode_dp.ROW_PREDECODE="${5:-3}" \
            -o "$work/tb_ctrl_decode_dp" $SRC >"$work/compile.log" 2>&1; then
        printf "NUM_WL=%-3s BANKS=%-2s MUX=%-2s SHARED_B=%s PRE=%s: COMPILE ERROR\n" "$1" "$2" "$3" "$4" "${5:-3}"
        cat "$work/compile.log"
        fail=1
        continue
    fi
    result="$(vvp "$work/tb_ctrl_decode_dp" 2>&1)"
    if grep -q '^PASS' <<<"$result"; then
        printf "NUM_WL=%-3s BANKS=%-2s MUX=%-2s SHARED_B=%s PRE=%s: %s\n" "$1" "$2" "$3" "$4" "${5:-3}" "$(grep '^PASS' <<<"$result")"
    else
        printf "NUM_WL=%-3s BANKS=%-2s MUX=%-2s SHARED_B=%s PRE=%s: FAIL\n" "$1" "$2" "$3" "$4" "${5:-3}"
        grep -E '^FAIL|^FATAL' <<<"$result" | head
        fail=1
    fi
done

[ "$fail" -eq 0 ] && echo "ALL 2RW DECODE CHECKS PASSED" || echo "2RW DECODE CHECKS FAILED"
exit "$fail"
