# OpenFinRAM open-source SRAM tools

Analysis/characterization tools for the open-source (Yosys + OpenROAD + Xyce)
flow, complementing the C++ generator. All are self-contained Python; no
Cadence dependency.

## `gds2lef.py` — GDS → LEF abstract (no Cadence)

The C++ flow builds a real macro GDS but generates the LEF via Cadence
`strmin` + Abstract Generator. This reads the GDS back with `gdstk` and writes
the LEF (SIZE, PINs from the texttype-251 labels, OBS) directly, so the
open-source path yields a P&R-consumable abstract.

```
uv run python scripts/gds2lef.py results/<run>/sram_x1024x64x1.gds -o out.lef
```

Pins land on their label's metal layer (ASAP7 layer 30=M3, 50=M5, ...);
`--obs M1,M2` sets blanket obstruction. Validated in OpenROAD `read_lef`.

## `characterize_read.py` — real read-path SPICE characterization

Real transient measurement of the read critical path, sweeping column depth to
show the timing is NOT constant (what a FakeRAM `.lib` cannot capture). Modes:

- `--mode access` — WL → BL sense-margin develop.
- `--mode clkq` — full read through the REAL sense amp + io_nand latch +
  tristate buffer to Q (2-cycle: read-1 pre-sets Q high, then read-0 times the
  fall; SAE is replica-timed per depth from the access measurement).
- `--mode setuphold` — A/D/WE input-register setup/hold via bisection on the
  real ASAP7 `DFFHQNx1` flop (capture-failure boundary).

### Simulator note (important)

ASAP7 devices are BSIM-CMG (FinFET). **ngspice (v45) has no BSIM-CMG** — it
runs a planar stand-in (harness exercised, numbers NOT ASAP7). For real numbers
use **Xyce** (`--simulator xyce --real-device --models tech/models/hspice/7nm_TT.pm`).
The harness auto-adapts the card: HSPICE `level 72` → Xyce BSIM-CMG `level 107`,
and emits `NFIN` (BSIM-CMG rejects `W`). `SS`/`FF` `.pm` give the other corners.
107 rather than 110 because the card declares `version = 107` and Xyce selects
the BSIM-CMG equations by level — see the note in
`characterize_read.py::prep_models`.

```
uv run python scripts/characterize_read.py --mode clkq --simulator xyce \
  --real-device --models tech/models/hspice/7nm_TT.pm
```

## `lvs_instance_check.py` — GDS instance-level LVS audit

Stage-1 layout verification for the routed controller (`ctrl_decode.gds`):
confirms the placed-cell multiset exactly matches the reference gate netlist
(`netlist_for_lvs.v`) and that every distinct placed cell's geometry is
bit-faithful to its ASAP7 library master. Physical-only cells inserted after
synthesis (fillers, tapcells, routing vias) are allowlisted.

This catches dropped/extra/swapped instances and corrupted cell layouts.
It does not trace routing connectivity between instances — that remains
open (full extraction needs Calibre or a complete Magic ASAP7 extraction
tech; neither is available). Combined with `tests/run_equiv_check.sh`
(netlist ≡ RTL) and OpenROAD emitting DEF and GDS from one database, the
residual exposure is limited to routing defects.

```
python3 scripts/lvs_instance_check.py \
    --macro-gds tmp/openroad_<ts>/ctrl_decode.gds --top ctrl_decode \
    --netlist tmp/openroad_<ts>/netlist_for_lvs.v \
    --library-gds tech/gds/asap7sc7p5t_28_R_220121a.gds
```

## `gen_table3_report.py` — Table III timing-comparison report

Assembles `reports/table3_timing_comparison.md` from every characterization
source available without commercial tools: the Cadence-characterized
`srambank_*x4x64` Liberty files shipped in the ASAP7 PDK add-on, the MOST
report's transcribed columns, OpenFinRAM's estimated `.lib`, and Xyce
transient measurements (`characterize_read.py --mode table3`). See the
generated report for methodology notes and known caveats.

```
python3 scripts/gen_table3_report.py \
    --estimate-lib results/<run>/sram_x256x64x1.lib \
    --xyce-mt0 /tmp/ofr_t3/table3_d{256,512,1024}.sp.mt0 \
    -o reports/table3_timing_comparison.md
```

## `generate_asap7_8t_iocolumn.py` writes the IO columns' SPICE too

The IO columns are chipforge_asap7's `IoColumnSpec` block, built to the
bitcell's bitline heights. Regenerating writes `tech/gds/sram_8t_iocolumn.gds`
and `tech/spice/sram_8t_iocolumn.sp` together; `SpiceGenerator` reads the
latter at run time, and `tests/run_8t_iocolumn_check.sh` compares both.

## `generate_asap7_8t_wl_slices.py` — the wordline driver slice ladder

```
.venv/bin/python scripts/generate_asap7_8t_wl_slices.py --verify
```

The 2RW macro drives its wordlines at the array with chipforge_asap7's
`DriverSliceSpec` (four wordlines a slice, `WL<i> = SEL . B<i>`), one strip
per port on each side of the controller band. This writes the ladder the
compiler picks from, `wl_slice_c{4,8,16,32,64}` sized with `size_decoder` to
that many cells along the wordline, with a filler and tap each, as
`tech/gds/sram_8t_wl_slices.gds` and `tech/spice/sram_8t_wl_slices.sp`;
`SpiceGenerator` reads the SPICE and `compile_asap7_2rw.py` the GDS. A macro
whose stack puts more than 64 cells on a wordline is refused.

## `verify_macro.py` — transistor LVS and device DRC of an assembled macro

```bash
.venv/bin/python scripts/verify_macro.py results/sram_x4x2x1_<stamp> \
    --baseline tests/golden/asap7_2rw_macro_verification.json
```

KLayout LVS (chipforge_asap7's ASAP7 deck) of the macro GDS against the `.sp`
written beside it, then the public ASAP7 DRC runset over the whole macro; about
40 s for 4x2. This is the `signoff_lvs` and `device_drc` that
`compile_asap7_2rw.py` records as `not_run`. Everything below the top level
matches; the top does not, because of two shorts to VSS that the metal-graph
check cannot see. Method, findings and limits are in
`docs/macro_verification.md`; `tests/test_macro_verification.py` holds the
result against the baseline.

## `simulate_macro.py` — whole-macro read/write simulation

```bash
.venv/bin/python scripts/simulate_macro.py results/sram_x4x2x1_<stamp> [--spaced] [--period 1e-9] [--corner SS]
```

Every transistor of the compiler's `.sp`, driven only at its pins, in Xyce:
cells preloaded through `.IC`, a two-port program checked against a software
memory model, the final state of every cell compared, timing from the clock
edge, and two hazards reported by name. Five to six minutes. With an idle cycle
after each write everything works; back to back, a read straight after a write
to the same column is overwritten with the D pins, because `wrena = clk`
glitches for one clock-to-Q. Works down to a 0.7 ns clock, against a Liberty
`min_period` of 0.157. See `docs/macro_simulation.md`.

## `characterize_wl_driver.py` — what should drive a wordline

```bash
.venv/bin/python scripts/characterize_wl_driver.py [--cells 16,32,64,128,256] [--wire xact|setrc] [--corner SS]
```

Xyce, fifteen seconds: an RC-ladder wordline of real preloaded 8T cells, driven
by an ideal source, by the compiler's `AND2x2` + `BUFx4`/`x8`/`x24`, and by
chipforge_asap7's `DriverSliceSpec` sized by `size_decoder`. The slice is 12 to
36 ps faster at every length; past 64 cells the wire sets the delay, and the two
M3 resistances in the repo differ by 5.4. See `docs/wordline_driver_study.md`.

## `characterize_sense_margin.py` — what a shared IO costs the read

```
.venv/bin/python scripts/characterize_sense_margin.py [--cells 16,64] [--deltas 5,10,20,30,50]
```

Xyce, about ten seconds: port B's IO block as the compiler builds it, one-sided
against chipforge_asap7's two-sided variant (one amplifier, driver and latch for
two facing banks), reading both values from a column of real `sram_cell_8t`
with the other cells storing the opposite. Prints the bitline and
sense-node splits at sense enable, the amplifier's resolve time, and whether
every read came out right. Nominal devices at TT: it shows function and the
relative cost, not the margin an amplifier offset leaves. On 2026-09-24 the
two-sided block read right everywhere, down to 5 ps from wordline to sense
enable; its split at sense time was 17-34 % smaller (about 3-7 ps more sense
delay for the same split), against the controller's roughly 76 ps.
`tests/test_sense_margin.py` checks the deck, and with `OPENFINRAM_SLOW_TESTS=1`
the read.

## Periphery status and remaining signoff

The open-source `ctrl_decode` flow now preserves and checks its 108 physical
delay inverters, uses a dedicated `BUFx4` stage on every WLT/WLB with predicted
array capacitance, performs propagated-clock CTS, and fails on post-route
setup/hold or electrical violations. Its current 2.5 ns SDC is a conservative
TT implementation target using routed global parasitics; it is not yet an
extracted-SPEF, multi-corner frequency characterization.

`characterize_read` still measures the separate datapath/array and deliberately
uses an ideal replica-timed SAE. Correlating that replica delay to the selected
physical `sdel` taps across PVT, producing a characterized macro `.lib`, and
transistor-level LVS remain signoff work.
