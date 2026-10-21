#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$repo_root/tests/xyce_gate_common.sh"
require_xyce

dumper="${IOPRECH_SPICE_DUMPER:-}"
if [[ -z "$dumper" || ! -x "$dumper" ]]; then
    echo "FAIL: IOPRECH_SPICE_DUMPER does not name the built netlist dumper" >&2
    exit 1
fi

model="$repo_root/tech/models/hspice/7nm_TT.pm"
if [[ ! -f "$model" ]]; then
    echo "FAIL: missing ASAP7 TT model: $model" >&2
    exit 1
fi

scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT

# Xyce's BSIM-CMG implementation uses level 107 and derives fin width from
# nfin.  Translate the HSPICE model selector and remove the redundant W
# instance parameter without changing the canonical emitted netlist.
sed -E 's/level[[:space:]]*=[[:space:]]*72/level = 107/g' \
    "$model" > "$scratch/asap7_tt_xyce.pm"
"$dumper" | sed -E 's/[[:space:]]+[Ww]=[^[:space:]]+//g' \
    > "$scratch/ioprech_subcircuits.sp"

run_deck() {
    local name="$1"
    local fixture="$repo_root/tests/spice/asap7_8t_ioprech_${name}.sp"
    local deck="$scratch/ioprech_${name}.cir"
    local log="$scratch/ioprech_${name}.log"

    {
        printf '* OpenFinRAM ASAP7 8T IO/precharge %s regression.\n' "$name"
        printf '.include %s\n' "$scratch/asap7_tt_xyce.pm"
        printf '.include %s\n' "$scratch/ioprech_subcircuits.sp"
        sed -n '2,$p' "$fixture"
    } > "$deck"

    if ! (cd "$scratch" && "$XYCE" "$deck" > "$log" 2>&1); then
        cat "$log" >&2
        return 1
    fi
    if [[ ! -s "$deck.mt0" ]]; then
        cat "$log" >&2
        echo "FAIL: Xyce did not produce measurements for $name" >&2
        return 1
    fi
    printf '%s\n' "$deck.mt0"
}

check_measure() {
    local measures="$1"
    local name="$2"
    local minimum="$3"
    local maximum="$4"
    local value

    value="$(awk -v key="$name" '$1 == key { print $3 }' "$measures")"
    if [[ -z "$value" ]]; then
        echo "FAIL: $name was not measured in $measures" >&2
        exit 1
    fi
    if ! awk -v value="$value" -v lo="$minimum" -v hi="$maximum" \
        'BEGIN { exit !(value >= lo && value <= hi) }'; then
        echo "FAIL: $name=$value, expected [$minimum, $maximum]" >&2
        exit 1
    fi
    printf '  %-18s %s V\n' "$name" "$value"
}

precharge_measures="$(run_deck precharge)"
echo "Precharge:"
for name in ATN0_PRE AT0_PRE ABN0_PRE AB0_PRE \
            BTN0_PRE BT0_PRE BBN0_PRE BB0_PRE; do
    check_measure "$precharge_measures" "$name" 0.65 0.75
done

read_measures="$(run_deck read)"
echo "Differential read:"
check_measure "$read_measures" A_NEG_BEFORE_SA -0.01 0.10
check_measure "$read_measures" A_POS_BEFORE_SA 0.60 0.75
check_measure "$read_measures" B_NEG_BEFORE_SA 0.60 0.75
check_measure "$read_measures" B_POS_BEFORE_SA -0.01 0.10
check_measure "$read_measures" QA_READ 0.60 0.75
check_measure "$read_measures" QB_READ -0.01 0.10

write_measures="$(run_deck write)"
echo "Differential write:"
check_measure "$write_measures" A_NEG_WRITE -0.01 0.10
check_measure "$write_measures" A_POS_WRITE 0.60 0.75
check_measure "$write_measures" B_NEG_BEFORE_WRITE 0.60 0.75
check_measure "$write_measures" B_POS_BEFORE_WRITE 0.60 0.75
check_measure "$write_measures" B_NEG_WRITE 0.60 0.75
check_measure "$write_measures" B_POS_WRITE -0.01 0.10
check_measure "$write_measures" A_NEG_AFTER_B -0.01 0.10
check_measure "$write_measures" A_POS_AFTER_B 0.60 0.75
for name in A_UNSEL_NEG A_UNSEL_POS B_UNSEL_NEG B_UNSEL_POS; do
    check_measure "$write_measures" "$name" 0.60 0.75
done

isolation_measures="$(run_deck isolation)"
echo "Shared-cell port isolation:"
check_measure "$isolation_measures" A_DIFF_NEG -0.01 0.15
check_measure "$isolation_measures" A_DIFF_POS 0.55 0.75
check_measure "$isolation_measures" B_DIFF_NEG 0.55 0.75
check_measure "$isolation_measures" B_DIFF_POS -0.01 0.15
check_measure "$isolation_measures" QA_DIFF 0.55 0.75
check_measure "$isolation_measures" QB_DIFF -0.01 0.15
check_measure "$isolation_measures" A_SAME_NEG -0.01 0.15
check_measure "$isolation_measures" A_SAME_POS 0.55 0.75
check_measure "$isolation_measures" B_SAME_NEG -0.01 0.15
# On the same-address read both ports see identical stimulus, yet port B's
# high-side bitline settles ~54 mV below port A's (0.650 V against 0.703 V);
# in the same-address-zero case below, both ports droop together instead.  The
# mechanism behind that A/B split is not established, so bound the measured
# value rather than a theory about it: any growth in the droop eats the
# differential port B's sense amp has to resolve, and same-address read/read is
# the case the Liberty contention_condition declares legal, so it has to keep
# working.  The upper bound sits just above the precharge rail so that reaching
# port A's level would be an improvement rather than a failure.
check_measure "$isolation_measures" B_SAME_POS 0.63 0.71
check_measure "$isolation_measures" QA_SAME 0.55 0.75
check_measure "$isolation_measures" QB_SAME 0.55 0.75
check_measure "$isolation_measures" A_SAME_ZERO_NEG 0.55 0.75
check_measure "$isolation_measures" A_SAME_ZERO_POS -0.01 0.15
check_measure "$isolation_measures" B_SAME_ZERO_NEG 0.55 0.75
check_measure "$isolation_measures" B_SAME_ZERO_POS -0.01 0.15
check_measure "$isolation_measures" QA_SAME_ZERO -0.01 0.15
check_measure "$isolation_measures" QB_SAME_ZERO -0.01 0.15

echo "PASS: ASAP7 8T port-A/port-B IO and shared-cell isolation transients"
