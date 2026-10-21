# ASAP7 true-dual-port 8T bitcell

OpenFinRAM includes a generated ASAP7 bitcell at `tech/gds/sram_cell_8t.gds`
and its array-edge family at `tech/gds/sram_cell_8t_edges.gds`. Their source is
`scripts/generate_asap7_8t_bitcell.py`; do not hand-edit the GDS.

These libraries are build output, not tracked files: the `tech_gds` target
(`scripts/tech_gds.py`) writes them into `tech/gds`, and git keeps only their
geometry digests in `tech/gds/digests.json`.

Parameterized wordline rows and mux-height arrays are generated in
`tech/gds/sram_wordline_arrays.gds` by
`scripts/generate_asap7_wordline_arrays.py`. The hierarchy follows the
academic library's `sramcol_xN` -> `array_xNx4` composition, but accepts any
positive wordline count and independently parameterizes the mux-row height.
The same implementation emits 6T variants for non-standard sizes.

The routed dual-port IO-column hierarchy is generated in
`tech/gds/sram_8t_iocolumn.gds` by
`scripts/generate_asap7_8t_iocolumn.py`. It contains one IO column per port
(`iocol_sram_8t_a`, `iocol_sram_8t_b`), the edge cap, and parameterized
`colgrp_x{N}x4_sram_8t` cells: port A's IO, the cap, one unsplit array of N
wordlines, and port B's IO at the far end of the bitlines.

## Reference and topology

The logical topology and port convention follow the public OpenRAM
`openram_dp_cell` in VLSIDA's Apache-2.0 Sky130 SRAM cell library, pinned to
commit `fc63b12883b4bf458ee8c756ba64c37063e1ffb9`:

- [OpenRAM dual-port cell directory](https://github.com/VLSIDA/sky130_fd_bd_sram/tree/fc63b12883b4bf458ee8c756ba64c37063e1ffb9/cells/openram_dp_cell)
- [Reference SPICE topology](https://github.com/VLSIDA/sky130_fd_bd_sram/blob/fc63b12883b4bf458ee8c756ba64c37063e1ffb9/cells/openram_dp_cell/sky130_fd_bd_sram__openram_dp_cell.base.spice)

No Sky130 geometry is copied. The generator starts from the published ASAP7
`sram_cell_6t_122` in `tech/gds/srambank_32b_boundary_2.gds` and adds two
2-fin access NMOS devices on the exact ASAP7 27 nm fin and 54 nm gate grids.
The OpenRAM reference's parallel pull-down fingers collapse into the two-fin
ASAP7 pull-down devices already present in the published core.

| OpenRAM | OpenFinRAM | Function |
| --- | --- | --- |
| `WL0` | `WLA` | Port-A wordline |
| `BL0` | `BLA` | Port-A true bitline |
| `BR0` | `BLAN` | Port-A complement bitline |
| `WL1` | `WLB` | Port-B wordline |
| `BL1` | `BLB` | Port-B true bitline |
| `BR1` | `BLBN` | Port-B complement bitline |

The matching schematic is `tech/spice/sram_cell_8t.sp`, which is also the
topology emitted by `SpiceTemplates::get_cell_8t()`.

## Edge-cell family

The edge library follows the roles of OpenRAM's public `openram_dp_cell_dummy`,
`openram_dp_cell_cap_row`, and `openram_dp_cell_cap_col` cells:

| Cell | Electrical contents | Boundary role |
| --- | --- | --- |
| `dummy_cell_8t` | Forced 8T core, both wordlines off | Inactive terminal bitcell |
| `sram_cell_8t_col_cap` | No devices; BLA/BLAN/BLB/BLBN and supply rails | Left/right column cap |
| `sram_cell_8t_row_cap` | No devices; WLA/WLB and ground routes | Top/bottom row cap |
| `sram_cell_8t_corner`, `_lr`, `_v2`, `_v2_lr` | No devices; grounded terminating routes | Four explicit array corners |
| `dummy_vertical_8t`, `_lr`, `_v2`, `_v2_lr` | No devices; WLA/WLB and ground routes | Oriented end-row tiles |
| `dummy_vertical_array_X<N>_8t` and its orientation variants | N vertical dummy tiles | Parameterized array end row |
| `dummy_topbot_8t_v1`, `_v2`, and their `_lr` variants | No devices; bitline and supply rails | Alternating mux-row side caps |
| `FILLER_BLANK_8t` | FIN/poly only | 0.054 µm × 0.594 µm blank tile |
| `FILLER_cgedge_8t` | Eight blank tiles | 0.108 µm × 2.376 µm outer column-group filler |

The core dummy and cap masters have a 0.108 µm × 0.594 µm boundary and the
bitcell's FIN/GATE/GCUT/WELL/select frame in the corresponding orientation.
`build_edge_library` derives the four corners from one canonical cell and
materializes each variant's polygons and pins. `_lr` reverses X and `_v2`
reverses Y about the **placement boundary**, retaining its original coordinates;
geometry overhangs do not determine the mirror center. Side caps use `v1` for
the canonical row and `v2` for the Y-reversed row. Placement selects these
masters with translations, so asymmetric select/well bands do not rely on
mirrored parent references. (The `_lr` masters terminated the right half
array of the earlier mid-bitline floorplan; the unsplit column has one capped
end, port A's, and places only the canonical ones.)

`--edge-rows N[,N...]` generates the equivalent of `dummy_vertical_array_X64`
for arbitrary positive address row counts (default: 64). As in the academic
hierarchy, these address rows run across X at the 0.108 µm pitch. The macro
compiler calls `build_dummy_vertical_array` at the requested wordline count,
inserting a grounded corner at each tap slot and preserving bitcell mirror
parity across taps. It generates all four end orientations directly. Both the
standalone IO-column generator and compiler place explicit side caps and
`FILLER_cgedge_8t`; the compiler closes their top/bottom ends with corners and
blank tiles. The outer fillers add 0.216 µm to each column group's width.

The cap-cell SPICE subcircuits are intentionally empty, as in OpenRAM;
their GDS conductors provide physical continuity without adding transistors.
The matching netlists are in `tech/spice/sram_cell_8t_edges.sp`.

## Array well/substrate tap

`tech/gds/sram_cell_8t_tap.gds` holds `tapcell_sram_8t`, generated by the same
`scripts/generate_asap7_8t_bitcell.py`. It follows the published ASAP7
`tapcell_sram_6t122`, which the academic bank interleaves into its array leaf
tile (`sramcol_x2` is two `sram_cell_6t_122` plus one tap).

The tap holds no devices. Its tie polarity is inverted with respect to the
bitcell: n+ over the n-well ties it to VDD, and p+ over each of the four
substrate bands the bitcell implants n+ ties them to VSS. The tie diffusions
sit between the two gate columns on the bitcell's own fin grid, so no channel
can form, and the FIN and GATE grids run through the tap column unchanged. It
carries no SRAM-Vt implant.

Unlike the published 6T tap, which terminates its column, `tapcell_sram_8t`
hands every bitline and supply rail straight through at the bitcell's own
coordinates, so it can be inserted anywhere in a row rather than only at the
end. Each tie climbs LISD -> V0 -> M1 -> V1 to a supply rail of its own net;
the tap itself does not reach M3 or above, because the 8T bitcell already uses
M3/M4/M5 for its wordlines and port-B bitlines. The strap variant below does
climb, using the one place those layers are free.

`scripts/generate_asap7_wordline_arrays.py --tap-pitch N` interleaves a tap
after every N 8T bitcells, producing `sramcol_x<W>_tapN_sram_8t` and
`array_x<W>x<M>_tapN_sram_8t` beside the untapped cells. The pitch must divide
the wordline count. The standalone generator defaults to 0 (no taps), preserving
the tracked untapped library. The macro compiler instead generates tapped
arrays on demand with pitch `gcd(wordlines, 16)` and rebuilds their IO columns
at the resulting width. Only the 8T rows take these taps: the published 6T tap
does not pass bitlines through.

## Power straps

`tech/gds/sram_cell_8t_tap.gds` also holds `strapcell_sram_8t`: the tap plus a
supply climb out of the M2 rails. The tap column is the only x position in the
array where M3 and M5 are unused, so the climb has to happen there. Without it
nothing carries supply between mux rows -- the M2 rails run east/west, and rows
abut north/south with no vertical path.

Each supply takes a staggered M2 -> M3 -> M4 -> M5 staircase, following the same
direction rules the IO column encodes (M4 horizontal-only, M5 vertical-only),
and lands on a full-height M5 spine. Rows abut at the cell boundary exactly as
the WLA/WLB trunks do, so the spines join into a continuous vertical supply rail
down the tap column. M3 is pinned to the cell centre because neighbouring
bitcells overhang M3 by 9 nm into both slot edges. Row mirroring is useful here:
the VDD rail is self-symmetric about the cell centre, but the two VSS rails swap,
so alternate rows tie the spine to alternate VSS rails and both end up connected.

The cell stops at M5 on purpose. Horizontal M6 tying the tap columns together
crosses bitcells, which have no M6 of their own, so that mesh belongs to the
array assembler rather than to a 0.108 um cell; the spines are exposed as
`vdd!`/`vss!` pins for it to land on.

`--strap-pitch N` upgrades every Nth tap to a strap, producing
`sramcol_x<W>_tap<P>_strap<N>_sram_8t` and the matching arrays. It requires
`--tap-pitch` and defaults to 0, so the tracked artifacts are unchanged.

The strap has not been through a real DRC deck: `asap7_drc.drc` is a stub, so
the new metal is verified only against `verify_strap_topology` and the keep-outs
derived from the bitcell.

## Port-specific IO/precharge wrappers

These wrappers' layout generator was removed when the macro moved to the
routed IO columns; their schematics remain as `SpiceGenerator` templates
for the device-level SPICE gates below.

`ioprech_sram_8t_a` and `ioprech_sram_8t_b` reuse the published ASAP7
`iocolgrp_sram_6t122_v2` differential IO core. Each wrapper retains separate
write-enable, sense-enable, sense-precharge, bitline-precharge, column-select,
and output-enable pins. The two wrappers therefore do not share dynamic
control or sense nodes.

The source IO core uses the compact 6T four-row pitch. Each wrapper adds a
two-sided channel-routed adapter to the 0.594 um 8T row pitch:

| Wrapper | Array bitline layer | Core landing | Function |
| --- | --- | --- | --- |
| `ioprech_sram_8t_a` | M2 | M2 through M3 pitch-shift risers | Port-A read/write IO |
| `ioprech_sram_8t_b` | M4 | M4/V3/M3/V2 to M2 | Port-B read/write IO |

The macro interface is true dual-port (2RW). Port B has its own `we_n_B`,
`D_B`, `wrena_B`, and `wrenan_B` path through the controller, generated SPICE,
physical IO column, and Liberty interface; no B write input is tied to a
supply. The wrapper contracts keep `SAE` and `SAPRECHN` distinct, while the
current composite maps both to its existing per-port `sae_A`/`sae_B` phase
signal until the controller exposes a separate sense-precharge phase. The
controller generates each phase through its protected `SAE_BUF` chain of
physical `BUFx2_ASAP7_75t_R` cells; the obsolete replica-bitline-derived SPICE
buffer chain is not part of this implementation.

The controller uses a shared hierarchical row predecoder per port and explicit
wordline enable/driver stages per bank, in two arrays of `NUM_WL` that drive
the lower and upper `NUM_WL` wordlines of the one array (`wl_A[2*NUM_WL]`,
`wl_B[2*NUM_WL]`); the address bit above the column select picks between them.
Each port has one precharge enable (`blprechn`) and one select bus
(`ysel`/`yseln`) per bank. See the
[parameterized decoder design](asap7_8t_decoder.md) for its RTL, load/drive
parameters, and standalone layout generator at the 8T array pin coordinates.

Both ports may operate in the same cycle, including accesses to different
addresses and same-address read/read. A simultaneous write/write or read/write
to the same address is an **illegal operating condition**: no priority or
arbitration is provided, and the stored/read value is undefined. The generated
Liberty marks the exact control-and-address combination with the cell-level
`contention_condition` attribute and repeats the restriction in its comment.
Integrators must prevent that condition before the macro boundary.

The same-address read/read half of that contract is backed by measurement, not
assumption: see "Measured stability" below. Concurrent dual-port read holds
101 mV of static noise margin at the worst corner, about 79% of the
single-port figure — with the caveat, stated there, that these are nominal
corner values and carry no mismatch or yield claim.

Physically each port has its IO column at its own end of the bitlines:

```
iocol_sram_8t_a | edge cap | array, 2*NUM_WL wordlines ... tap | iocol_sram_8t_b
```

Port A's bitlines leave the array on M2 and port B's on M4, so neither crosses
the other's core. (Until 2026-09-21 both wrappers sat side by side between two
half arrays: port B crossed the A wrapper on M4 and port A rose to M6 to cross
the B wrapper. That bought bitlines half as long at the price of both
crossovers and a top/bottom split through the RTL and the netlist.) Each
`iocol` abuts the array. The edge cap hands every bitline through and the
filler beside it has no metal, so port A's M2 bitlines are strapped across
the filler; port B's end meets its IO on the array's last tap, as an IO face
always has.

Each `iocol` is the parametric column block from `chipforge_asap7`
(`IoColumnSpec`): a four-leaf bitline mux group, a sense amplifier, a write
driver and an output latch drawn on the 8T row, with a tap, 1620 x 2376 nm.
`generate_asap7_8t_iocolumn.py` builds it at the bitcell's own bitline
heights (the centres of the `BLA`/`BLAN` M2 bars and the `BLB`/`BLBN` M4
bars), mirrors it in x for port A so the entries face the array, and writes
the block's netlist beside the GDS (`tech/spice/sram_8t_iocolumn.sp`, one flat
subcircuit per port and an `iocol_sram_8t_{a,b}` wrapper in the pin order
`SpiceGenerator` instantiates). The amplifier's `SAE` and `SAPRECHN` are one
net (`one_sense_phase`), as the earlier composite drove them. The reused 6T
wrappers (the `ioprech_sram_8t_*` templates) are no longer in the macro;
their device-level SPICE gates remain, their layout generator does not.

Against the wrappers, on `sram_x4x2x1`: the port IO is 1.76 um wide instead
of 3.89, the macro reads 15 to 29 ps sooner at Q, the spaced program's energy
is 8 % lower, and every read and cell is right. The write-enable glitch is
unchanged.

### Dummy rows only at a stack's ends (2026-09-23)

A tile used to carry a dummy row above and below its four array rows, a
leftover from when every bit stood alone in a routing channel; abutted, two
dummy rows sat between every pair of bits, a third of each stack's height.
Now only a stack's first tile carries the bottom one and its last the top one
(`build_leaf(..., bottom=, top=)`; the assembler abstracts the variants it
needs as `dp_column`, `dp_column_endb`, `_endt` and `_noend`), and the tiles
between abut array row to array row, cap to cap and IO block to IO block,
rail on rail (both block edges are VSS). The array rows keep alternating,
since a tile holds an even count, and the blocks' half-fin-pitch overhang
makes one block end exactly where the next begins.

Abutting the IO blocks exposed two ways a bit's metal reached the next bit's:
the write driver's `D` column ran to the block's top edge, onto the next
block's `SA` stub; and the mux group's `SA`/`SAN` tracks ran the group's full
height into the next bit's. chipforge now stops the write driver's control
columns an M3 tip-to-tip space (31 nm) short of the top edge, and draws the
sense lines in the leaf so a group opens them 16 nm short of its outer ends;
the select and precharge tracks still run on, one net per bank, so a stack's
selects become one continuous track. The public deck on three abutted tiles:
307 markers against 571 for the same tiles apart, no new rule at the seams.
`tests/tools/test_2rw_physical.py` checks that no per-bit net or unnamed
component crosses a seam. x8x8 goes from 40.35 to 33.22 um tall.

A block's abstract carries two sizes: the LEF SIZE is its bounding box,
overhangs included, which the router must see; its GDS boundary is the cell's
own placement boundary, so abutted tiles meet on one clean outline instead of
overlapping bounding boxes.

### The dummy rows' stubs are tied in the tile (2026-09-22)

The corner and tap-slot cells of a tile's two dummy rows keep bitline and
supply stubs at the real cells' heights, labelled VSS or VDD but touching
nothing: twenty 162 nm bars a tile on M2 and M4. Each was a terminal of its
own, and reaching one between the M5 wordlines went right or wrong with the
track phase, which is what had held the margins at 2 um. `build_leaf` now
ties them (`tie_end_row_stubs`): stubs of one net at one height are bridged
along the row on M2; a VSS stub on M2 gets a strap up to the row's VSS bar;
an M4 stub, which M4 (one direction only) cannot strap, gets a V3 onto the
cell's own VSS via stack or an M3 jog to it; and the VDD bar, whose nearest
VDD is the neighbouring real row's across the dummy row's VSS bars, gets a
V2, an M3 jog on a free track and a V2 down. VSS components per tile 46 to
26, VDD 16 to 12, tile DRC unchanged at 220. With that and supply pins on
every layer, margins from 0.2 um up all route.

### The block sits on the array's fin grid (2026-09-22)

The bitcell centres a fin on its row boundary (fins at 0, 27, 54 ... nm up the
row); chipforge's cells, like ASAP7's standard cells, centre a fin *space*
there (13.5, 40.5 ...). Until 2026-09-22 the block stood 0.144 um off the
array with a strap per bitline across the gap, which kept the two fin grids
from touching but did not put them on one grating. The released ASAP7 bank
has the same two grids (its `sram_cell_6t_122` on one, its sense amplifier,
tap and decoder cells on the other) and resolves them by placing the IO group
half a fin pitch up its row so the fins run straight through the seam. The
block now does the same: `BitlineMuxSpec(grid_offset=13.5)` keeps
`bitline_entry` measured from the array's row while the leaf is drawn 13.5 nm
lower on its own edge (and 13.5 higher for the group's flipped leaves, whose
rows it sits *below*), `build_port_io` places the block 13.5 nm up the
column, and the gap and its straps are gone: the array's bars overhang its
edge by 18 nm (the cap's) and 27 nm (the tap's) into the block's landings.
`fin_grid_offset(bitcell)` reads the offset off the bitcell rather than
assuming it. The tile is 3.996 um wide instead of 4.284; the public DRC deck
gives the same 220 markers on it, rule for rule, none at either seam; every
fin in the tile is on one 27 nm grid.

## Physical interface

- Cell name: `sram_cell_8t`
- SRAM boundary: 0.108 µm × 0.594 µm
- Full drawing bounding box: 0.163 µm × 0.632 µm, including legal overhangs
- Port A: `BLA`/`BLAN` on M2 and `WLA` on M3
- Port B: `BLB`/`BLBN` on M4 and `WLB` on M5
- Supplies: the published core's `vdd!`/`vss!` interface
- Device count: 2 PMOS and 6 NMOS

## Device sizing and strength ratios

Every channel sits on one gate pitch, so `L` is common to all eight devices and
a fin-count ratio is a width ratio is a drive-strength ratio.

| Devices | Role | `nfin` | Model |
| --- | --- | --- | --- |
| M0, M1 | pull-down (driver) | 2 | `nmos_sram` |
| M2, M3 | pull-up (load) | 1 | `pmos_sram` |
| M4–M7 | access, **both ports** | 2 | `nmos_sram` |

| Ratio | Value | Governs |
| --- | --- | --- |
| Cell ratio, pull-down / access | **1.00** | read stability, one port selected |
| Cell ratio, **both ports selected** | **0.50** | read stability under concurrent access |
| Pull-up ratio, pull-up / access | 0.50 | write-ability (lower is easier to write) |

These derive from `PULLDOWN_NFIN` / `ACCESS_NFIN` / `PULLUP_NFIN` in
`scripts/generate_asap7_8t_bitcell.py`, which `verify_topology()` asserts
against the extracted GDS — the constants and the layout cannot drift apart,
and `--verify` prints the ratios on every run.

**The sizing is inherited, not chosen.** `build_cell()` only *adds* two access
devices to the published `sram_cell_6t_122` core, so these widths were picked
for a single-port 6T. A cell ratio of 1.00 is already aggressive against the
1.5–2.0 usually recommended for a 6T read, and because both ports land on the
same storage nodes, asserting `WLA` and `WLB` together puts two access devices
against one pull-down and halves it to 0.50.

That matters here specifically because the macro contract below declares
same-address read/read **legal**, and a row half-selected on both ports sees
the same bias. Whether 0.50 is survivable is a measurement, not an argument —
so it was measured.

## Measured stability

`tests/run_8t_stability_check.sh` (ctest `asap7_8t_stability_check`) measures
the bitcell alone, with no periphery, so a failure points at the cell. Static
noise margin comes from a DC butterfly reduced to its largest inscribed square;
the reported SNM is the smaller of the two lobe margins (both stored states).
The four biases are hold, read A only, read B only, and simultaneous read A+B.
All four bitlines are clamped to VDD, measuring static precharged-bitline
loading rather than a finite-capacitance bitline transient. The cell is
derived from the compiler's SPICE templates, exposing Q/QB without changing
any transistor connections or sizes.

Each bias/corner runs at 5 mV and 2.5 mV sweep steps; their margins must agree
within 1 mV. The finer sweep supplies the reported SNM. The extractor evaluates
the exact diagonal-separation extrema of the piecewise-linear curves, so only
the DC sweep interpolation remains approximate. It rejects incomplete,
non-finite, missing-point, or materially nonmonotonic sweeps. Separate checks
require nominal Q/QB VTC symmetry and A/B read-margin agreement within 0.1 mV.

The existing write test records the bitline voltage at which a slow ramp flips
the latch. A higher trip voltage is easier to write; VDD minus that voltage is
the **required bitline drop**, not an SNM or a larger-is-better write margin.
This transient trip measurement is not a DC write-SNM extraction.

| Corner | VDD / T | Hold SNM | Read SNM, A or B | **Read SNM, both ports** | Required write BL drop |
| --- | --- | --- | --- | --- | --- |
| SS | 0.63 V / 125 °C | 260.3 mV | 128.8 mV | **101.1 mV** | 0.426 V (68% of VDD) |
| TT | 0.70 V / 25 °C | 307.6 mV | 148.7 mV | **116.4 mV** | 0.465 V (66% of VDD) |
| FF | 0.77 V / −40 °C | 346.4 mV | 161.6 mV | **119.9 mV** | 0.494 V (64% of VDD) |

Port A and port B write trips and single-port read SNM agree to the printed
precision at every corner. The maximum change between the 5 mV and 2.5 mV
sweeps is 0.131 mV (TT hold), below the 1 mV convergence guard.

**Concurrent dual-port read reduces nominal read SNM by 21–26% here.**
The ratio is 0.79 / 0.78 / 0.74 at SS / TT / FF for this sizing and these
sampled corners, not a universal topology guarantee. Halving the effective
cell ratio does not halve SNM: the latter depends on the nonlinear transfer
curves, not just a device-strength ratio. The disturbed low level also shows
sublinear change: adding the
second access device lifts it from 17.0% to 24.7% of VDD at TT, a factor of
1.45 rather than 2.

These nominal simulations show positive simultaneous-read SNM at the three
tested corners, with about 101 mV at the weakest sampled corner. That is useful
evidence for the read/read contract, not macro stability signoff. The
`contention_condition` restriction on same-address access with either port
writing remains unchanged.

**What this does not establish.** These are nominal corner values: the centre
of a distribution whose width is unmeasured. SRAM stability is a mismatch
property, and `docs/characterization_plan.md` puts Monte Carlo out of scope, so
there is no Vmin or yield claim here and none should be read into the table.
SS/low-VDD/hot, TT/nominal/room, and FF/high-VDD/cold are three combined PVT
points, not an exhaustive independent process/voltage/temperature sweep.
Layout-extracted parasitics, finite-bitline dynamics, supply noise, and
statistical device mismatch are not covered by these bitcell-only DC tests.
The 88 mV floor the gate enforces is a regression guard, not a spec. A cell
ratio of 1.00 leaves less headroom against mismatch than a conventional 1.5–2.0
would, and that remains the open risk — quantifying it needs the statistical
work that is deliberately not in this plan.

### Running the stability tests

```sh
cmake --build build --target dump_8t_ioprech_spice -j4
ctest --test-dir build -R 'asap7_8t_(snm_analysis|stability)_check' --output-on-failure
```

`asap7_8t_snm_analysis_check` runs the standard-library Python geometry/parser
unit tests without Xyce, including known symmetric/asymmetric lobes, the
smaller-lobe rule, degenerate curves, and malformed simulation output.
The simulation gate skips (exit 77) if Xyce is unavailable or its MPI runtime
cannot initialize; a skip is not an electrical pass. Set `XYCE` to select a
different executable.

To retain decks, model cards, simulator logs, raw sweeps, per-sweep JSON/SVG
butterflies, and a combined `summary.csv`:

```sh
IOPRECH_SPICE_DUMPER="$PWD/build/tests/dump_8t_ioprech_spice" \
SNM_RESULTS_DIR="$PWD/results/8t_stability" \
bash tests/run_8t_stability_check.sh
```

Each invocation creates a unique `snm-*` subdirectory and prints its path;
artifacts survive both success and failure. CSV bias names are `hold`,
`read1` (A only), `readb` (B only), and `read2` (A+B). Without
`SNM_RESULTS_DIR`, temporary outputs are removed on exit. Regression windows
and the 88 mV simultaneous-read floor live in `tests/run_8t_stability_check.sh`.
Investigate a margin change before updating them; they are not specifications.

Two measurement notes worth keeping, because both were silent failures:

- Xyce rejects `.temp` as an unrecognised dot line, so a deck using it runs at
  the default 27 °C whatever its corner label says. Temperature is not a small
  effect — dual-port read SNM moves 129 → 91 mV from −40 to 125 °C on the TT
  card alone. Use `.OPTIONS DEVICE TEMP=`.
- The ASAP7 card declares `version = 107`, a parameter Xyce ignores, so the
  `level` is the only thing selecting the BSIM-CMG equations. Level 107 matches
  the extraction; at level 110 `nmos_sram` I<sub>on</sub> shifts 7.2%.

Port B uses M4/M5 so its routes cross the dense published M2/M3 core without
electrical contact. The cell retains the official core's 108 nm east/west
pitch and grows only north/south for the two added access devices. ACTIVE,
FIN, WELL, select, and horizontal bitline/supply shapes deliberately jut past
the boundary just as they do in the published 6T cell; alternating-X instances
therefore overlap at every seam instead of forming isolated pairs separated by
empty columns. WLA on M3 and WLB on M5 both span the complete cell height, so
they continue through every north/south row abutment without shorting adjacent
wordline addresses.

Both 20 nm GATE columns also extend through the complete north/south boundary.
They retain the published 6T cell's complementary 19/7 nm and 7/19 nm boundary
overhangs; the GCUT bands divide those raw columns into the intended effective
gate nets, so the extension does not add transistor channels.

The four storage-side LISD and SDT terminals retain the published core's
canonical 24 nm width. Their 18 nm V0 landings and local M1 landing pads are
centered at x=54 nm between the two GATE columns, giving 3 nm of LISD enclosure
on both sides and no LISD-to-GATE overlap. The upper core V0 is shifted 1 nm
south within its LISD solely to preserve diagonal V0 spacing to WLA; it remains
x-centered. The upper storage M1 strap jogs left between its two centered
landings to pass the top WLA contact without moving that contact toward
an east/west placement seam, and the lower strap jogs right past the bottom
one for the same reason. Verification rejects
same-layer LISD overlap, off-center storage V0 landings, or any LISD-to-GATE
intersection.

Both WLA gate contacts sit on their gate, V0 included, with the LIG reaching
only its 1 nm past GATE: x=16..37 nm on gate A at the bottom, x=71..92 nm on
gate B at the top, each 16 nm from its placement seam. Columns are placed
mirrored about both seams, so that is 32 nm between neighbouring contacts,
which clears the 31 nm the deck asks of two short LIG edges. The bottom contact
used to start *on* the west seam (LIG 0..37 nm, its V0 on the overhang at
6..24 nm, to keep its M1 landing clear of the straight Q strap). Two mirrored
copies then met edge to edge and their LIG was one strap: adjacent WLA
wordlines were shorted in pairs in every row, live and dummy, and the last
before a tap or a corner to the corner cell's `vss!` track
(`docs/macro_verification.md`). Verification now rejects a WLA landing within
16 nm of a seam. Moving it also removed the V0, V1 and M1 spacing violations
the seam-hugging stack made with its own mirror image.

The lower and upper WLA LIG landings are displaced 0.5 nm south and 1 nm north,
respectively, to maintain the public deck's 15 nm LIG-to-LISD corner spacing.
Their V0 contacts follow the LIG displacement so the landing-stack enclosure is
unchanged. The lower BLBN V0 alone is shifted 4.5 nm north within its M1 landing
so it is fully contained by the outside LISD terminal.

## Device DRC

Until now nothing checked the drawn geometry. OpenROAD's `detailed_route
-output_drc` covers only what the router drew, so the handcrafted cells --
bitcell, tap, strap, edge frame, IO wrappers -- and their abutment went
unverified, and the macro's own `.physical.json` records `device_drc:
"not_run"`.

`tech/drc/asap7_device.drc` is a conservative subset run by
`tests/run_8t_device_drc_check.sh`. ASAP7's real DRM is not redistributable,
so each threshold is the tightest geometry the published ASAP7 libraries
actually draw, measured by `scripts/derive_asap7_drc_rules.rb`. The deck is
then calibrated: it must run clean on the vendor's own SRAM cells, and the
check script re-verifies that calibration on every run. A violation therefore
means "tighter than anything the published library does" -- a strong signal
without inventing a rule. It is not a signoff deck and does not replace one.

Because the existing cells already carry violations, the gate is a ratchet
against `tests/golden/asap7_8t_drc_baseline.json`: it fails on a new violation,
a higher count, or a tighter worst-case distance. Regenerate deliberately with
`WRITE_BASELINE=1`.

The tap, strap and IO/precharge wrappers pass the calibrated subset.
Other findings remain; the significant ones are:

| Rule | Worst | Limit | Where |
| --- | --- | --- | --- |
| `M2.S` | 8 nm | 18 nm | array abutment |
| `M1.S` | 12 nm | 18 nm | array abutment |

The former `GCUT.S` finding (10 nm against a blanket 37 nm spacing) was a
checker error: the public rule measures 35 nm vertical spacing between
projecting edges. Staggered cuts on adjacent gates do not violate it. The
v2 simplified deck and its KLayout reference use that directional rule;
see `docs/gdscheck.md` for the corrected gate and its tests.

The former 6 nm M4 tip gaps are removed by alternating `sram_cell_8t` with
`sram_cell_8t_b` in mirrored slots. Their WLB landings use different tracks:
A at y=-132/396 nm, B at y=12/300 nm. Each landing encloses V3 and V4 by
11 nm horizontally. Both WLB bridges stay at x=18 nm, preserving the x=90 nm
macro routing corridor that the earlier alternating-cell attempt blocked.

BLBN moves one M4 track, from y=-84 to -36 nm. Its upper via stack sits 18 nm
inside A and outside B, so the shared stack coincides across the mirror seam.
An untapped terminal B uses `sram_cell_8t_b_end`, with that stack inside the
cell at x=90 nm, to avoid a 5 nm M2 gap to the IO. All three masters have the
same devices, pin contract and 108 x 594 nm boundary. The IO entry heights,
taps, straps and dummy rows follow the new bitline position; the corner left
of the array takes B's landing tracks while retaining its process orientation.

The native ASAP7 main suite reports zero M4 and V3-to-M4 enclosure markers in
the 32x4 array and column group (formerly 120 `M4.S.2`, 30 `M4.S.3`, and 256
`V3.M4.EN.2`). The full public KLayout deck also reports zero M4 markers there.
A capped, tapped 4x4 compiler tile is clean for native M4 rules; the public
deck still reports 24 pre-existing `M4.S.5` short parallel-run markers in its
end rows (down from 32). This is not a claim of full device DRC closure.

`tests/run_8t_bitcell_check.sh` now exercises native gdscheck M4 rules on a
mirrored array and capped tiles with taps and straps. Restoring the original
unstaggered landing geometry is a positive control that must fail. The 16x16
macro also routes at its original 0.3 um margin with zero router violations
and passes metal connectivity and transistor-level LVS.

The calibrated baselines tighten the eliminated M3/M4 findings and record
the alternate masters' inherited M1 finding. Pre-existing discrepancies
between current M2 counts and the saved baseline remain failures; this change
does not raise those limits.

## Rebuild and verification

From the repository root:

```sh
.venv/bin/python scripts/tech_gds.py            # every library, in dependency order
.venv/bin/python scripts/generate_asap7_8t_bitcell.py \
  --verify tech/gds/sram_cell_8t.gds
.venv/bin/python scripts/generate_asap7_8t_bitcell.py \
  --verify-edges tech/gds/sram_cell_8t_edges.gds
.venv/bin/python scripts/generate_asap7_wordline_arrays.py \
  --verify tech/gds/sram_wordline_arrays.gds
.venv/bin/python scripts/generate_asap7_8t_iocolumn.py \
  --verify tech/gds/sram_8t_iocolumn.gds
.venv/bin/python scripts/tech_gds.py --update   # only when a change is intended
ctest --test-dir build -R asap7_tech_gds_check --output-on-failure
ctest --test-dir build -R asap7_8t_bitcell_check --output-on-failure
ctest --test-dir build -R asap7_8t_ioprech_spice_check --output-on-failure
ctest --test-dir build -R asap7_wordline_array_check --output-on-failure
ctest --test-dir build -R asap7_8t_iocolumn_check --output-on-failure
```

The verifier reconstructs the effective gates after GCUT, extracts the eight
device channels and conductor graph, and checks the complete dual-port graph:
two cross-coupled CMOS inverters, two WLA access devices, two WLB access
devices, paired true/complement storage nodes, independent pins, and supplies.
It also checks the exact fin/gate grids and the focused lower-metal rules used
by the added geometry. Array verification probes the actual flattened metal at
every east/west and north/south seam, requires WELL/FIN/ACTIVE/select overlap
at every mirrored cell boundary, checks complete bitline and wordline
continuity, and requires one isolated wordline component per address. The test
regenerates the GDS and requires its geometry to match the recorded digests
(`tech/gds/digests.json`). For the edge library it additionally
checks the forced dummy state, zero transistor channels in every cap cell,
independent pass-through pins, identical process-frame fingerprints, and
compatible boundary/grid geometry for mirrored abutment. Edge-frame regressions
exercise asymmetric well/select bands, counts 1/3/18/64/129, tap slot parity,
GDS round trips, and rejection of a corrupted corner mirror. They also check
that the compiler uses all four corner/end-row orientations without mirrored
parent references.

When Xyce is installed, `asap7_8t_ioprech_spice_check` runs the emitted ASAP7
transistor netlists at the TT corner. In addition to standalone precharge,
read, and write checks, its shared-cell bench connects both composite IO ports
to two real `sram_cell_8t` instances. It performs concurrent writes and reads
to different addresses, then same-address read/read for both stored data
polarities. It intentionally never drives the forbidden same-address case in
which either port writes; that condition is rejected by the macro contract
rather than assigned a simulated result.

This verification is not a substitute for foundry signoff. Calibre is not
available in the open-source test environment, and the public ASAP7 Calibre
decks distributed with the PDK are encrypted. Run the official ASAP7 DRC/LVS
deck before treating this cell as tapeout-qualified.

## Physical macro compiler

The open-source 2RW path now uses `scripts/compile_asap7_2rw.py` for macro
placement and routing, separate from the academic 6T assembler. It generates
one tapped array of `2 * --wordlines` wordlines per column, the cap family at
its port-A end, and an IO column at each end. Banks share the external data
buses and receive separate controller selects. Both write-enable polarities
and both write-data buses are routed without tie-offs.

### Floorplan: controller band between two abutted stacks

Since 2026-09-22 the data bits are two stacks of column tiles with the
controller band between them, the way the 6T macro keeps its control spine
mid-array: bits `0 .. bits/2 - 1` below the band, the rest above. The tiles
of a stack abut at the tile's boundary pitch (six bitcell rows: four mux rows
and the dummy row on each side), so a stack's wordlines (port A on M3, port B
on M5) run through every tile by abutment and are never routed. Each stack's
wordlines are driven at its edge by a pair of driver strips built from
chipforge_asap7's `DriverSliceSpec` (`WL<i> = SEL . B<i>`, four wordlines a
slice on the array's 108 nm pitch):

* port A's strip sits against the array, its outputs on the port-A tracks;
* port B's strip is flipped under it on a shared VSS rail, each output
  climbing to the tile's M5 wordline (6 nm off the M3 track) on a
  VIA34/VIA45 stack and an M5 strap;
* both strips sit half a fin pitch (13.5 nm) in from the pair's edges, so
  their fins and the array's are on one 27 nm grid (the array centres a fin on
  its boundary, the slices a fin space), port A's outputs bridged to the edge
  on M3;
* a filler stands wherever a slice has no neighbour: under the array's tap
  columns and at the row's ends.

The slice is picked from the ladder `scripts/generate_asap7_8t_wl_slices.py`
writes (`tech/gds/sram_8t_wl_slices.gds`, `tech/spice/sram_8t_wl_slices.sp`:
`wl_slice_c{4,8,16,32,64}`, sized with `size_decoder` at 0.181 fF a cell) by
the cells along the wordline in one stack, `4 * bits/2`; past 64 cells the
post-decode NAND no longer fits its band and the compiler refuses. The
controller (`tech/verilog_dp/sram_control.v`) no longer has a wordline
driver stage: per port it emits `sel_lo[3:0]`, the static one-hot of the
wordline index's low two bits shared by every slice, and `sel_hi[bank][k]`,
the one-hot of the high bits gated by the wordline phase and the bank. The
SPICE deck carries the ladder, one `wl_strip_c<cells>_x<slices>` and the two
pairs `wl_strips_{lo,hi}_...`; the datapath halves are `Xdata_lo`/`Xdata_hi`
on `wl_{a,b}_{lo,hi}[...]`.

Between the strips and the controller lies `--channel-width`, outside the
blocks `--margin`, and between banks `--bank-gap`, all 0.3 um by default
(0.2 is the floor). A tile's IO columns are two rows tall against its six-row
pitch, so the abutted stacks still leave a channel per tile on each IO side
for the controls and data, and everything else is routed over the blocks on
the upper layers; the macro pins land on M6/M7 at the edge (`--top-layer`,
7 by default), leaving M8 and M9 to the chip. In the abstracts
the wordline nets are obstructions, not pins (`abstract(..., abutted=...)`):
the router never sees them, and the connectivity gate proves each one is a
single conductor through the tiles and its strip, isolated from every other
net.

The controller's floorplan is the column's width by whatever height puts
its cells at 50 % utilization (40 % for the single-port controller, which
still carries a buffer stage per wordline; 60 % left the two-port one no room
for its hold-repair buffers).

The assembler extracts metal/via connectivity from the actual hard-cell GDS.
Each disconnected supply component gets its own terminal, so the router must
connect all array, IO, tap, and controller supply islands. A supply component
is a pin on every layer it has, so the router lands on a rail or a bar; a
signal component's pin is its highest metal. Controller signal access
follows the designated pin layer, including its connected landing wire but
not higher internal routing. Other metal is an obstruction. After OpenROAD
routing and KLayout streamout, a second conductor
graph verifies every terminal, all external pins, and isolation of internal
unnamed interconnect. Nonzero routing DRC reports, opens, shorts, and missing
reports prevent final GDS publication. Controller routing DRC and electrical
constraints are also checked; reset fanout introduced by hold repair is split
into bounded branches.

Example (16 words × 4 bits, 64 storage cells, true 2RW):

```sh
cmake --build build -j2
build/OpenFinRAM --openroad --num-wls 2 --num-data-bits 4 --num-banks 1 --skip-characterization
```

Outputs are in `results/sram_x4x4x1_<timestamp>/`: GDS, LEF, structural SPICE,
estimated Liberty, and `.physical.json` with verification status and report
paths. Capacity is `2 * num_wls * 4 * num_banks` words; `num_data_bits` is
the width of each port. The mux height is currently fixed at four. Wordline
count and data width must be even and at least two; banks must be a power of
two. Arrays are generated on demand rather than limited to tracked GDS sizes.

### Banks sharing port B's IO (`--share-port-b`)

With two or more banks, `--share-port-b` puts the banks in pairs and gives
each pair's column one port-B IO block between its two arrays:

    port A IO | cap | array (bank 2p) | port B IO, two-sided | array (2p+1) | cap | port A IO

The second bank's half is the first's mirrored in x, so both arrays' port-B
ends face the block. The block is chipforge_asap7's
`IoColumnSpec(two_sided=True)`: the mux group on each face, one sense
amplifier, write driver and output latch between them
(`iocol_sram_8t_b2`, written with the other IO columns by
`scripts/generate_asap7_8t_iocolumn.py`, with `colgrp_half_*`/`colgrp_pair_*`).
Only one bank of a pair is accessed on a port in a cycle, so the controller
(`SHARED_B`, set by the flag) drives port B's sense, write and output enables
once per pair (`sae_B[pair]`, ...), and its precharges and column selects per
bank, the unselected bank's group staying precharged and deselected.
`scripts/characterize_sense_margin.py` measured what the far group and the
wire across the block cost a read: 17-34 % of the sense split, a few ps.

In the pair tile the second bank's wordlines and selects continue the first's
indices (`WLA[rows + i]`, `yselB[4 + i]`) and its one-per-bank controls end
in `R` (`sae_AR`, `blprechnBR`), which is what `leaf_net` maps to the
macro's per-bank nets. Its wordline strips are the first bank's mirrored the
same way. The deck's `stacked_colgrp` instantiates `colgrp_pair_sram_8t`
per pair and bit (`X<pair>_<bit>`), each two `colgrp_half_sram_8t` and the
block. `sram_x4x2x2`: 7.94 x 16.31 um against 10.46 x 16.88 um unshared
(-27 % area); strict LVS matches, DRC finds nothing the unshared macro does
not have (KLayout reports a cell once per orientation, so the mirrored half
repeats the cells' known markers under `:m90`).

### Strips in the controller's band (`--strips-in-controller`)

A strip pair is only as wide as its array, so the band it stands in was
mostly empty (two thirds of it on a shared `sram_x4x2x2`), and a 0.3 um
channel on each side of the controller held its fan-out to the strips. With
`--strips-in-controller` the controller's die is the whole band between the
two stacks and the strips sit inside it:

* `compile_asap7_2rw.py --plan-band DIR` builds the tiles and strips first and
  writes the band's width, the core area the strips and their halos take, the
  strips' abstracts (`strips.lef`) and `band.tcl`;
* the controller's place-and-route (`OpenRoadManager`) sizes the die from its
  cells at the utilization cap plus that keep-out, places the strips as fixed
  physical instances, and cuts the rows around them (`cut_rows`, and
  `tapcell` with the same halos: 0.216 um across, one row along). Its pins
  stand on the side edges only. After timing closure on the ports' SDC loads
  it connects every strip's `SEL`/`B<j>` pin to its `sel_hi`/`sel_lo` port
  net, routes them (`global_route -critical_nets_percentage 0`: the strips
  have no timing model, and timing-driven routing asks STA for their slack
  and crashes), and removes the instances before writing out, so its GDS
  keeps only the wires it landed on the strip pins;
* the assembler (`--band DIR`, reading back `die.txt`) abuts the stacks to
  the band's edges and puts the strips where the plan did. The select nets
  are joined by the controller's own wires, so like the wordlines they are
  probed by the connectivity gate and not routed again.

The band's edges are the stacks' IO block edges, not the tiles'. An end tile
carries a dummy row above (below) its array, but its IO blocks are four rows
tall, so over the IO columns the tile was a dummy row of empty space. The
IO blocks already keep standard-cell conventions at their edges: the gate
grid 27 nm (mod 54) from the tile's edge, the gate cut centred on the edge,
the first fin 13.5 nm in, an 18 nm VSS rail on the edge and the p-well
against it. So the band starts at the lower stack's IO top edge (0.5805 um
into its last tile) and ends at the upper stack's IO bottom edge (0.6075 um
into its first), with its first row N and its last FS: the gates run
straight through a shared gate cut, the fins stay on pitch and the rails
merge. Over each half's cap and array the dummy row reaches into the band;
a keep-out block (M1-M5 obstructed) stands on it, removed with the strips,
and the end tiles' outline steps back to the IO blocks over the IO columns.

The IO blocks and strips sit half a fin pitch off the bitcell's row grid,
so on a tile's own grid they are at half nanometres; abutting them to a
controller on the DEF's 1 nm grid left their rails, local interconnect and
fins half a nanometre apart (`V0.LIG.EN.4` at every gate pitch along both
seams, `FIN.S.1`). The tiles and strips therefore carry half a nanometre
inside their masters (`abstract(..., shift=)`), which puts every chipforge
shape, and the controller's first row, on the grid, and the bitcell's own
metal on the half. What a shifted master's LEF says is snapped to the grid
the safe way, only where a shape has a half-nanometre edge: obstructions
out, so the router never sees less metal than there is, and pins in, so it
never lands beyond it (growing every obstruction a nanometre instead crowded
the strips' own supply pins; leaving them to the LEF reader's rounding let
the router short supplies in the tiles). As supply pins it offers only shapes
on the grid in M1-M3 (M4 up may not bend, and a landing half a nanometre off
a shape leaves a sliver); a component with none, a dummy row's tied stubs,
keeps its 18 nm landings whole for the reader to round.
(Shifting the controller instead put its M4 pins half a nanometre off every
landing: 138 `M4.AUX.3` bends; its pins on M2 did not route.)

`sram_x4x2x2` shared: 7.99 x 13.02 um, against 7.94 x 16.31 um without the
band (-20 %); the band is 6.48 um where controller, strips and channels took
8.55, and the extra room lets the controller legalize at 50 %. Strict LVS
matches; DRC 547 against 562, nothing at the seams. `sram_x8x8x2` shared:
8.86 x 27.82 um, against 28.68 with the inset band and 31.54 without (-12 %),
controller at 50 %, strict LVS, DRC 902 against 876 without the band (router
classes on M1/M4/M5, the controller's own placement), the spaced program
passing on all 512 cells. The band abuts one tile
column so far: with several banks or pairs the stride would also have to keep
the gate grid, a multiple of 54 nm.

`scripts/mapped_verilog_to_spice.py` parses the routed controller through
Yosys and orders every instance using the actual CDL formal pins, including
expanded one-bit buses and supply connections. Unknown cells/pins fail instead
of producing an empty controller stub. The delivered SPICE deck includes the
standard-cell definitions; transistor model cards belong in the testbench.
Passive array caps/taps do not add dummy storage transistors to the netlist.

```sh
ctest --test-dir build -R asap7_2rw_physical_check --output-on-failure
ctest --test-dir build -R asap7_2rw_macro_check --output-on-failure
.venv/bin/python tests/tools/check_2rw_macro.py results/sram_x4x4x1_<timestamp>
```

The fast test exercises connectivity failures, cap/tap geometry at 2/4/18
wordlines, port/bank mapping, and CDL pin ordering. The integration test runs
the compiler and compares GDS storage-cell count, all LEF/SPICE ports, Liberty
2RW interfaces, controller instances, and SPICE hierarchy arities.

This is a conservative routable floorplan, not a density-optimized or
tapeout-qualified memory compiler. OpenROAD routing DRC is not the encrypted
ASAP7 device-level signoff deck, and the metal graph is not transistor LVS.
Full-macro extracted simulation, PVT/Monte Carlo characterization, power-grid
IR/EM analysis, and timing with extracted macro interconnect remain separate
qualification work. Liberty remains explicitly estimated. The same-address
collision restrictions above still apply; no hardware arbiter is inserted.
