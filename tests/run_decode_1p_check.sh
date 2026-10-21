#!/usr/bin/env bash
# Single-port ctrl_decode functional regression (Icarus Verilog), --bitcell 6t.
set -u
cd "$(dirname "$0")/.."

SRC="tests/prim_models.v tests/tb_ctrl_decode_1p.sv tech/verilog_dp/sram_control_1p.v tech/verilog_dp/delay_cell.v tech/verilog_dp/row_decoder.v"
# NUM_WL BANKS MUX [ROW_PREDECODE, default 3]: 32 and 256 wordlines per half
# give the slice decode 4 and 7 address bits, where 4-bit groups differ.
CONFIGS=("2 1 4" "8 1 4" "8 2 4" "16 4 4" "32 1 2" "32 2 4 4" "256 1 2 4" "256 1 2 2")

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
fail=0
for cfg in "${CONFIGS[@]}"; do
    set -- $cfg
    if ! iverilog -g2012 -s tb_ctrl_decode_1p \
            -Ptb_ctrl_decode_1p.NUM_WL="$1" \
            -Ptb_ctrl_decode_1p.NUM_BANK="$2" \
            -Ptb_ctrl_decode_1p.COLUMN_MUX="$3" \
            -Ptb_ctrl_decode_1p.ROW_PREDECODE="${4:-3}" \
            -o "$work/tb_ctrl_decode_1p" $SRC >"$work/compile.log" 2>&1; then
        printf "NUM_WL=%-3s BANKS=%-2s MUX=%-2s PRE=%s: COMPILE ERROR\n" "$1" "$2" "$3" "${4:-3}"
        cat "$work/compile.log"
        fail=1
        continue
    fi
    result="$(vvp "$work/tb_ctrl_decode_1p" 2>&1)"
    if grep -q '^PASS' <<<"$result"; then
        printf "NUM_WL=%-3s BANKS=%-2s MUX=%-2s PRE=%s: %s\n" "$1" "$2" "$3" "${4:-3}" "$(grep '^PASS' <<<"$result")"
    else
        printf "NUM_WL=%-3s BANKS=%-2s MUX=%-2s PRE=%s: FAIL\n" "$1" "$2" "$3" "${4:-3}"
        grep -E '^FAIL|^FATAL' <<<"$result" | head
        fail=1
    fi
done

[ "$fail" -eq 0 ] && echo "ALL 1RW DECODE CHECKS PASSED" || echo "1RW DECODE CHECKS FAILED"
exit "$fail"
