#!/usr/bin/env bash
# Cell-level stability regression for the ASAP7 true-dual-port 8T bitcell.
#
# The macro contract in docs/asap7_8t_bitcell.md declares concurrent
# same-address read/read legal, and every cell in a row half-selected on both
# ports sees the same bias.  Both rest on the cell's strength ratios, which are
# inherited from the published single-port 6T -- cell ratio 1.0 with one port
# selected.  Nothing measured that until this gate.
#
# Everything runs on the bitcell alone, no periphery, so a failure points at
# the cell rather than at a sense amp or a write driver.
#
# Scope: this is nominal corner stability, not a Vmin or a yield number.
# SRAM margin is a distribution and the mean is only half the story;
# docs/characterization_plan.md puts the Monte Carlo half out of scope.  What
# this measures is the nominal penalty of concurrent versus single-port
# reads. Neither the absolute margin nor that ratio establishes mismatch yield.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$repo_root/tests/xyce_gate_common.sh"
require_xyce

dumper="${IOPRECH_SPICE_DUMPER:-}"
if [[ -z "$dumper" || ! -x "$dumper" ]]; then
    echo "FAIL: IOPRECH_SPICE_DUMPER does not name the built netlist dumper" >&2
    exit 1
fi

python_bin="${PYTHON:-python3}"
if [[ -x "$repo_root/.venv/bin/python" ]]; then
    python_bin="$repo_root/.venv/bin/python"
fi

if [[ -n "${SNM_RESULTS_DIR:-}" ]]; then
    mkdir -p "$SNM_RESULTS_DIR"
    scratch="$(mktemp -d "${SNM_RESULTS_DIR%/}/snm-XXXXXXXX")"
    scratch="$(cd "$scratch" && pwd)"
    echo "Stability artifacts: $scratch (retained on success or failure)"
else
    scratch="$(mktemp -d)"
    trap 'rm -rf "$scratch"' EXIT
fi
printf 'corner,vdd_v,temp_c,bias,snm_mv,lobe_a_mv,lobe_b_mv,coarse_snm_mv,convergence_delta_mv\n' \
    > "$scratch/summary.csv"

# The bitcell netlist comes from the generator's own templates, so the cell
# measured here cannot drift from the cell that ships.  W= is stripped because
# BSIM-CMG derives width from nfin.
"$dumper" | sed -E 's/[[:space:]]+[Ww]=[^[:space:]]+//g' > "$scratch/subckts.sp"

# SNM and write margin both need to force or observe a storage node, but Q and
# QB are internal to sram_cell_8t.  Rather than paste a copy of the cell into
# the fixture -- which is exactly the drift the dumper exists to prevent --
# derive a probe variant by promoting Q and QB to ports.  The body is
# untouched, so this is provably the generator's cell plus two terminals.
#
# The templates close with a bare `.ENDS`, so the range ends on the first one
# after the opening `.SUBCKT`; matching `.ENDS sram_cell_8t` would never fire
# and would swallow every subcircuit to the end of the file.
sed -n '/^\.SUBCKT[[:space:]]\+sram_cell_8t[[:space:]]/,/^\.ENDS/p' \
    "$scratch/subckts.sp" \
  | sed -E 's/^\.SUBCKT[[:space:]]+sram_cell_8t[[:space:]]+(.*)$/.SUBCKT sram_cell_8t_probe \1 Q QB/;
            s/^\.ENDS[[:space:]]*$/.ENDS sram_cell_8t_probe/' \
  > "$scratch/probe.sp"

if ! grep -q '^\.SUBCKT sram_cell_8t_probe .* Q QB$' "$scratch/probe.sp"; then
    echo "FAIL: could not derive sram_cell_8t_probe from the dumped templates" >&2
    sed -n '1,5p' "$scratch/probe.sp" >&2
    exit 1
fi
# Eight channels in, eight channels out: the promotion must not have dropped a
# device or matched the wrong subcircuit.
probe_devices="$(grep -c '^M[0-7][[:space:]]' "$scratch/probe.sp" || true)"
if [[ "$probe_devices" != "8" ]]; then
    echo "FAIL: sram_cell_8t_probe has $probe_devices devices, expected 8" >&2
    exit 1
fi

# The inscribed-square fit is the one piece of geometry here that is easy to
# get subtly wrong, so it validates itself against a case with an exact known
# answer before any measured number is trusted.
if ! "$python_bin" "$repo_root/tests/tools/snm_from_sweep.py" --self-test; then
    echo "FAIL: snm_from_sweep self-test failed; measured SNM is not trustworthy" >&2
    exit 1
fi

# Corner definitions from docs/characterization_plan.md.
declare -A CORNER_VDD=(  [TT]=0.70 [SS]=0.63 [FF]=0.77 )
declare -A CORNER_TEMP=( [TT]=25   [SS]=125  [FF]=-40  )

# Measured windows, roughly +/-12% around the current values.  Wide enough not
# to be brittle against model noise, tight enough that reweighting a device
# moves out of them.
declare -A WM_TRIP_LO=( [TT]=0.205 [SS]=0.175 [FF]=0.240 )
declare -A WM_TRIP_HI=( [TT]=0.265 [SS]=0.235 [FF]=0.310 )
declare -A HOLD_LO=(    [TT]=270   [SS]=230   [FF]=305   )
declare -A HOLD_HI=(    [TT]=345   [SS]=290   [FF]=385   )
declare -A READ1_LO=(   [TT]=131   [SS]=113   [FF]=142   )
declare -A READ1_HI=(   [TT]=168   [SS]=145   [FF]=181   )
declare -A READ2_LO=(   [TT]=102   [SS]=89    [FF]=105   )
declare -A READ2_HI=(   [TT]=131   [SS]=114   [FF]=135   )

# The floor the concurrent-access contract has to clear at its worst corner.
# This is a regression guard, not a spec: a real limit needs the mismatch
# distribution, which is out of scope here.  Set below the measured worst case
# (SS, 101.1 mV) with headroom.
READ2_WORST_FLOOR_MV=88

# Run one fixture at the active corner and echo the path of the output Xyce
# wrote.
#
#   run_deck <fixture-suffix> <tag> <extension> [extra-sed]
#
# The fixture's title line is replaced and its `.param VDDVAL` is rewritten to
# the corner's supply; the fixtures keep their own defaults so each stays
# runnable standalone.  <extension> is mt0 for transient measures or prn for a
# DC sweep -- Xyce writes DC .measure results to .ms0 and printed sweeps to
# .prn, so a gate that assumed .mt0 would silently see nothing for the sweeps.
run_deck() {
    local name="$1"
    local tag="$2"
    local extension="$3"
    local edit="${4:-}"
    local fixture="$repo_root/tests/spice/asap7_8t_${name}.sp"
    local deck="$scratch/${tag}.cir"
    local log="$scratch/${tag}.log"
    local vdd_edit="s/^\.param VDDVAL=.*/.param VDDVAL=${corner_vdd}/"
    {
        printf '* OpenFinRAM ASAP7 8T %s regression.\n' "$tag"
        printf '.include "%s"\n' "$corner_model"
        printf '.include "%s"\n' "$scratch/probe.sp"
        # Xyce rejects `.temp` outright ("Unrecognized dot line will be
        # ignored"), so a deck using it runs silently at the default 27 C and
        # every corner number is really just a VDD and model-card sweep.
        # .OPTIONS DEVICE TEMP is the directive Xyce honours, and temperature
        # matters here: dual-port read SNM moves 129 -> 91 mV from -40 to
        # 125 C on the TT card alone.
        printf '.OPTIONS DEVICE TEMP=%s\n' "$corner_temp"
        sed -n '2,$p' "$fixture" | sed -E "${vdd_edit}${edit:+; $edit}"
    } > "$deck"

    if ! (cd "$scratch" && "$XYCE" "$deck" > "$log" 2>&1); then
        cat "$log" >&2
        return 1
    fi
    if [[ ! -s "$deck.$extension" ]]; then
        cat "$log" >&2
        echo "FAIL: Xyce did not write $deck.$extension for $tag" >&2
        return 1
    fi
    printf '%s\n' "$deck.$extension"
}

# Sweep the butterfly at one wordline bias and reduce it to SNM.  Emits a
# "NAME = value" file so check_measure can bound it like any raw measure.
run_snm() {
    local tag="$1"
    local wla="$2"
    local wlb="$3"
    local prn prefix resolution step result coarse fine delta
    prefix="$(printf '%s' "${tag##*_}" | tr '[:lower:]' '[:upper:]')_"
    for resolution in coarse fine; do
        step=0.005
        [[ "$resolution" == fine ]] && step=0.0025
        prn="$(run_deck snm "${tag}_${resolution}" prn \
            "s/^\.param WLAV=.*/.param WLAV=${wla}/; s/^\.param WLBV=.*/.param WLBV=${wlb}/; s/^\.param SWEEPSTEP=.*/.param SWEEPSTEP=${step}/")" || return 1
        result="$scratch/${tag}_${resolution}.snm"
        "$python_bin" "$repo_root/tests/tools/snm_from_sweep.py" \
            --prefix "$prefix" --vdd "$corner_vdd" --step "$step" \
            --json "${prn}.json" --svg "${prn}.svg" "$prn" > "$result" || return 1
    done
    coarse="$(value_of "$scratch/${tag}_coarse.snm" "${prefix}SNM_MV")"
    fine="$(value_of "$result" "${prefix}SNM_MV")"
    delta="$(awk -v a="$coarse" -v b="$fine" 'BEGIN { d=a-b; print d<0 ? -d : d }')"
    if ! awk -v d="$delta" 'BEGIN { exit !(d <= 1.0) }'; then
        echo "FAIL: $tag SNM sweep convergence: 5 mV=$coarse, 2.5 mV=$fine mV (limit 1 mV)" >&2
        return 1
    fi
    printf '%s,%s,%s,%s,%s,%s,%s,%s,%s\n' \
        "$corner" "$corner_vdd" "$corner_temp" "${tag##*_}" "$fine" \
        "$(value_of "$result" "${prefix}SNM_LOBE_A_MV")" \
        "$(value_of "$result" "${prefix}SNM_LOBE_B_MV")" "$coarse" "$delta" \
        >> "$scratch/summary.csv"
    printf '%s\n' "$result"
}

value_of() { awk -v key="$2" '$1 == key { print $3 }' "$1"; }

declare -A READ2_AT
declare -A READ1_AT

for corner in SS TT FF; do
    corner_vdd="${CORNER_VDD[$corner]}"
    corner_temp="${CORNER_TEMP[$corner]}"
    corner_model="$scratch/${corner}.pm"

    # ASAP7 ships the card as HSPICE level 72.  Xyce's BSIM-CMG is selected by
    # level, and the card declares `version = 107` on every model -- a
    # parameter Xyce reports as unrecognised and ignores, so the level is the
    # only thing choosing the equations.  107 is the level matching the
    # extraction; at level 110 nmos_sram Ion shifts 7.2% (57.07 -> 52.97 uA),
    # which is more than enough to move a margin.
    sed -E 's/level[[:space:]]*=[[:space:]]*72/level = 107/g' \
        "$repo_root/tech/models/hspice/7nm_${corner}.pm" > "$corner_model"

    echo
    echo "=== ${corner}: VDD ${corner_vdd} V, ${corner_temp} C ==="

    # --- write margin -----------------------------------------------------
    wm="$(run_deck write_margin "${corner}_wm" mt0)"

    # The cell must actually end up flipped.  Without this, a deck whose latch
    # never wrote would report no trip and check_measure would fail with a
    # confusing "not measured" rather than the real reason.
    check_measure "$wm" QA_FINAL  -0.02 0.15
    check_measure "$wm" QAN_FINAL "$(awk -v v="$corner_vdd" 'BEGIN{print v-0.12}')" \
                                  "$(awk -v v="$corner_vdd" 'BEGIN{print v+0.02}')"
    check_measure "$wm" QB_FINAL  -0.02 0.15
    check_measure "$wm" QBN_FINAL "$(awk -v v="$corner_vdd" 'BEGIN{print v-0.12}')" \
                                  "$(awk -v v="$corner_vdd" 'BEGIN{print v+0.02}')"

    # Trip point as a bitline voltage: a HIGHER trip is easier to write.
    # VDD minus trip is the required bitline drop, not a noise margin.
    check_measure "$wm" WM_A_TRIP "${WM_TRIP_LO[$corner]}" "${WM_TRIP_HI[$corner]}"
    check_measure "$wm" WM_B_TRIP "${WM_TRIP_LO[$corner]}" "${WM_TRIP_HI[$corner]}"

    # The two ports must agree.  This is the 2RW claim stated as a
    # measurement: identical cells, one shared ramp, so any split would be the
    # cell's asymmetry and not the stimulus.
    wm_a="$(value_of "$wm" WM_A_TRIP)"
    wm_b="$(value_of "$wm" WM_B_TRIP)"
    if ! awk -v a="$wm_a" -v b="$wm_b" \
        'BEGIN { d = a - b; if (d < 0) d = -d; exit !(d <= 1e-3) }'; then
        echo "FAIL: ${corner} port write margins differ: A=$wm_a B=$wm_b" >&2
        exit 1
    fi
    awk -v v="$corner_vdd" -v t="$wm_a" \
        'BEGIN { printf "  %-18s %.4f V (%.0f%% of VDD)\n", "write BL drop", v - t, 100 * (v - t) / v }'

    # --- static noise margin ----------------------------------------------
    hold="$(run_snm "${corner}_hold" 0.0 0.0)"
    check_measure "$hold" HOLD_SNM_MV "${HOLD_LO[$corner]}" "${HOLD_HI[$corner]}" mV

    read1="$(run_snm "${corner}_read1" "$corner_vdd" 0.0)"
    check_measure "$read1" READ1_SNM_MV "${READ1_LO[$corner]}" "${READ1_HI[$corner]}" mV

    readb="$(run_snm "${corner}_readb" 0.0 "$corner_vdd")"
    check_measure "$readb" READB_SNM_MV "${READ1_LO[$corner]}" "${READ1_HI[$corner]}" mV
    if ! awk -v a="$(value_of "$read1" READ1_SNM_MV)" \
        -v b="$(value_of "$readb" READB_SNM_MV)" \
        'BEGIN { d=a-b; if (d<0) d=-d; exit !(d <= 0.1) }'; then
        echo "FAIL: ${corner} port A/B read SNM differs by more than 0.1 mV" >&2
        exit 1
    fi

    # Both ports selected: the configuration the macro contract declares legal
    # for same-address read/read, and the one every cell in a row
    # half-selected on both ports sees.
    read2="$(run_snm "${corner}_read2" "$corner_vdd" "$corner_vdd")"
    check_measure "$read2" READ2_SNM_MV "${READ2_LO[$corner]}" "${READ2_HI[$corner]}" mV

    hold_mv="$(value_of "$hold" HOLD_SNM_MV)"
    read1_mv="$(value_of "$read1" READ1_SNM_MV)"
    read2_mv="$(value_of "$read2" READ2_SNM_MV)"
    READ1_AT[$corner]="$read1_mv"
    READ2_AT[$corner]="$read2_mv"

    # Expected ordering for this sizing and precharged-bitline bias. This is
    # a nominal regression expectation, not a guarantee for arbitrary cells.
    if ! awk -v h="$hold_mv" -v r1="$read1_mv" -v r2="$read2_mv" \
        'BEGIN { exit !(h > r1 && r1 > r2) }'; then
        echo "FAIL: ${corner} SNM must fall as ports are selected, got" \
             "hold=$hold_mv read1=$read1_mv read2=$read2_mv mV" >&2
        exit 1
    fi

    awk -v r1="$read1_mv" -v r2="$read2_mv" \
        'BEGIN { printf "  %-18s %.2f  (dual-port read keeps %.0f%% of single-port SNM)\n", \
                 "RSNM_2P/RSNM_1P", r2 / r1, 100 * r2 / r1 }'
done

echo
echo "=== across corners ==="

# Stability tracks the supply and falls with temperature, and both effects run
# the same way here, so SS is the weak corner and FF the strong one at every
# bias.  Asserted because a violation means a corner is not actually being
# applied -- which is exactly the failure `.temp` produced silently.
for cfg in READ1 READ2; do
    declare -n table="${cfg}_AT"
    if ! awk -v ss="${table[SS]}" -v tt="${table[TT]}" -v ff="${table[FF]}" \
        'BEGIN { exit !(ss < tt && tt < ff) }'; then
        echo "FAIL: ${cfg} SNM should order SS < TT < FF, got" \
             "SS=${table[SS]} TT=${table[TT]} FF=${table[FF]} mV" >&2
        exit 1
    fi
    printf '  %-18s SS=%s  TT=%s  FF=%s mV\n' "${cfg}_SNM" \
        "${table[SS]}" "${table[TT]}" "${table[FF]}"
done

# The number the concurrent-access contract actually has to survive.
worst="${READ2_AT[SS]}"
for corner in TT FF; do
    if awk -v a="${READ2_AT[$corner]}" -v b="$worst" 'BEGIN { exit !(a < b) }'; then
        worst="${READ2_AT[$corner]}"
    fi
done
if ! awk -v w="$worst" -v floor="$READ2_WORST_FLOOR_MV" \
    'BEGIN { exit !(w >= floor) }'; then
    echo "FAIL: worst-corner dual-port read SNM ${worst} mV is below the" \
         "${READ2_WORST_FLOOR_MV} mV nominal regression floor" >&2
    exit 1
fi
printf '  %-18s %s mV (floor %s mV)\n' "worst-corner RSNM_2P" "$worst" \
    "$READ2_WORST_FLOOR_MV"

echo
echo "PASS: ASAP7 8T bitcell stability across SS/TT/FF"
