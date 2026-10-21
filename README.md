# ASAP7 SRAM Compiler

This is an SRAM compiler designed for the ASAP7 PDK, leveraging the GDSII Tool Kit (GDSTK) to create SRAM layouts.

# Usage

```
git clone --recursive https://github.com/shao-chien-lu/OpenFinRAM.git
cd OpenFinRAM
mkdir build
cd build
cp -r ../tech .
cmake ..
make

./OpenFinRAM --num-wls 2 --num-data-bits 4 --num-banks 1 --single-port
```

## Open-Source Flow (single-port ASAP7)

```
./OpenFinRAM --num-wls 2 --num-data-bits 4 --num-banks 1 --single-port --openroad
```

Runs Yosys synthesis -> OpenROAD P&R/CTS/STA -> KLayout DEF->GDS streaming,
then assembles the macro. Requires `yosys`, `openroad` (or `--openroad-path`),
and KLayout's Python bindings — see External Dependencies.

Notes:
- The commercial flow remains the default when `--openroad` is omitted.
- The build hard-fails on missing timing paths, setup/hold, or slew/capacitance/fanout violations.
- The routed controller's SPICE comes from `scripts/mapped_verilog_to_spice.py`, which
  reads the mapped Verilog with Yosys and orders each cell's pins from the CDL, so no
  licensed `v2lvs` is needed. Unknown cells or a failed conversion stop the build; there
  is no placeholder netlist.
- STA uses routed global parasitics at TT only — a conservative implementation target, not a characterized frequency claim. Use `scripts/characterize_read.py` for the array datapath.

## Dual-port 8T flow (2RW, ASAP7)

For measured schematic read timing and Liberty generation from an existing
8T result, see [8T Liberty characterization](docs/liberty_8t.md). The flow
sweeps clock slew and output load separately for both ports and identifies
the timing and power fields that remain estimated or uncharacterized.

Dual-port is the default mode (omit `--single-port`): two independent
read/write ports on an 8T bitcell. Run it from the repository root, since the
binary finds `scripts/`, `tech/` and `.venv/` there and writes `results/`
beside them:

```
./build/OpenFinRAM --num-wls 8 --num-data-bits 16 --num-banks 1 --openroad
```

1. Yosys and OpenROAD synthesize, place, route and time the controller and
   decoders (`tmp/openroad_<timestamp>/`). Without `--openroad` the commercial
   flow builds the controller instead (`tmp/innovus_<timestamp>/`).
2. `scripts/compile_asap7_2rw.py` assembles and routes the macro with OpenROAD
   (`tmp/macro_2rw_<timestamp>/`) and re-extracts every pin connection from
   the streamed GDS before publishing it.
3. The macro lands in `results/sram_x<2*wls>x<bits>x<banks>_<timestamp>/`:
   `.gds`, `.lef`, estimated `.lib`, `.sp` and `.physical.json`.

Sizes: `--num-wls` is the rows one row-select address field covers; each
array has twice that, so the example is `sram_x16x16x1`. Wordlines and bits
must be even and at least 2, banks a power of two, and the column mux four
rows.

Options:
- `--share-port-b`: banks in pairs, mirrored about one two-sided port-B IO
  block (needs an even bank count).
- `--strips-in-controller`: put the wordline driver strips in the controller's
  band, abutting the IO blocks (see [Controller band](#controller-band---strips-in-controller)).
  Supported for one column of tiles (one bank, or one shared pair) so far.
- `--row-predecode-bits 2|3|4`: address bits per predecode group in the
  controller's wordline decode (default 3). 4-bit groups only pay off at 256 or
  more wordlines a bank; on an x64x8x1 they decode slower (327 vs 276 ps, OpenSTA)
  and take more area.

Floorplan: each data bit is a column tile, `port-A IO | cap | array | port-B
IO`, with port A's IO at one end of the bitlines and port B's at the other.
The tiles form two abutted stacks with the controller band between them;
wordlines run through each stack by abutment from a pair of driver strips at
its edge. Each IO block carries full-height VDD/VSS M3 straps, tied to the
array's supply bars at every seam, so a stack's supplies join end to end and
only its end tiles' are routed.

Power grid: vdd and vss are full-height stripes on the top routing layer (M7
by default), alternating, one vdd every 1.28 um with vss halfway between.
Every stripe is a shape of its pin in the LEF, so the chip's horizontal M8
straps can drop vias onto them anywhere along the macro; the router joins each
block's supply pins to them. They are minimum width, as ASAP7's V6 must be
exactly as wide as the M7 it lands on. The assembler's `--power-pitch` sets
the spacing, and `--power-pitch 0` gives the old single pin per supply.

The assembler can be run on its own against an existing controller GDS, which
skips synthesis; it then writes only the `.gds` and `.physical.json`:

```
.venv/bin/python scripts/compile_asap7_2rw.py --wordlines 8 --bits 16 --banks 1 \
  --controller tmp/openroad_<timestamp>/ctrl_decode.gds \
  --work tmp/macro_2rw_manual --output results/manual/sram_x16x16x1.gds
```

It starts at a 0.3 um pin margin and doubles it up to `--max-margin` if
routing fails. Python needs `gdstk`, KLayout's bindings and `chipforge_asap7`
(the driver strips); `.venv/bin/python` is used when present.

Verify a result with transistor-level LVS and gdscheck DRC (the default):

```
.venv/bin/python scripts/verify_macro.py results/sram_x16x16x1_<timestamp>
```

Known limits: the public DRC is not clean (remaining findings are mostly the
dummy end rows' gate pitch and router-level M1/V1/M4 spacing); the band
controller fails max-fanout on larger macros such as x32x8x2 and x16x32x2;
middle tiles take their supply only through the IO straps, so IR drop on tall
stacks is unmeasured.

## Single-port 6T flow (generated, `--bitcell 6t`)

The two-port machinery with port A alone, on the released ASAP7 6T cell:

```
./build/OpenFinRAM --num-wls 8 --num-data-bits 16 --num-banks 1 --bitcell 6t --openroad
```

- Column tile: `edge filler | cap | 2*NUM_WL 6T bitcells | dummy | tap | IO`,
  all released `srambank_32b` cells except the IO block, which is chipforge_asap7's
  `StaggeredIoColumnSpec`. The 6T row (270 nm) is shorter than any IO leaf the
  dense row style can draw (297 nm), so the even rows' leaves stand in one column
  and the odd rows' in a second, the bitlines lifted to M4 at the block's edge.
  Written by `scripts/generate_asap7_6t_iocolumn.py` (`tech/gds/sram_6t_iocolumn.gds`,
  `tech/spice/sram_6t_iocolumn.sp`).
- Floorplan as the two-port macro: two stacks of abutted tiles with the controller
  band between them, one driver strip on each side, released dummy rows at each
  stack's ends. Controller: `tech/verilog_dp/sram_control_1p.v`.
- Pins: `clk rst_n ce_n we_n oe_n A[] D[] Q[]` (port A of the two-port
  machinery inside; the GDS/LEF, the deck's top subckt and the `.lib` agree).
  D[] and Q[] are on the right edge, at the IO end of the bitlines, each bit's
  pair level with its tile; the address and controls are on the left, level
  with the controller's inputs. (The two-port macro has port A's pins on the
  left and port B's on the right, at their IO blocks' ends.)
- `--single-port` with `--openroad` builds this macro; without it (the commercial
  flow) it is still the legacy srambank flow.
- Larger banks: `--segment-bits N` divides each stack's wordlines into segments of
  N data bits, a strip pair between segments; `--num-rows-per-mux 8|16` for deeper
  column muxes; `--num-banks` side by side.  Each strip joins its slices'
  predecode inputs on M4 rails, and the controller's array-wide outputs (sel_lo,
  sae, blprechn, ...) get drivers sized for their counted gate load.
- The `.lib` timing and energy come from `scripts/timing_model_6t.py`, fit to
  whole-macro simulations (`tech/timing/sram_6t_timing.json`).
- Verified: strict LVS match (`scripts/verify_macro.py`) on x4x2x1, x16x16x1,
  x64x8x1 (segmented), x256x2x1 and multi-bank x4x2x2/x4x8x2; the whole-macro Xyce
  program (`scripts/simulate_macro.py`) passes on x4x2x1, x16x16x1 and x256x2x1
  (clk->Q ~250 ps).  x16x16x1 is 6.64 x 25.28 um against 6.00 x 52.71 um for the
  8T x16x16x1.
- `--strips-in-controller` works with one bank, segmented or not (see
  [Controller band](#controller-band---strips-in-controller)); `--share-port-b`
  does not apply.
- Limits so far: the timing model is fit to small macros and to the old BUFx2 controller outputs.
  The public DRC count is the released 6T cells' own findings, the end rows'
  gate pitch, standard cells, and a few router M1 markers.

## Controller band (`--strips-in-controller`)

By default the wordline driver strips stand beside the tile stacks and the
controller is a separate block. With `--strips-in-controller`, OpenROAD places
and routes the controller around the strips, and its band fills the space
between the two stacks, so the strips abut the IO blocks directly. It applies to
both bitcells:

```
# 8T two-port: one bank, or one pair sharing port B
./build/OpenFinRAM --num-wls 2 --num-data-bits 2 --num-banks 2 --share-port-b \
  --strips-in-controller --openroad

# 6T single-port: one bank, optionally segmented
./build/OpenFinRAM --num-wls 32 --num-data-bits 8 --num-banks 1 --bitcell 6t \
  --num-rows-per-mux 4 --segment-bits 2 --strips-in-controller --openroad
```

The flow has three steps:

1. `compile_asap7_2rw.py --plan-band` writes the strip placement (`plan.txt`),
   the strips' abstract (`strips.lef`) and the floorplan script (`band.tcl`).
2. OpenROAD builds the controller on that band die, keeping the strips fixed,
   and reports the die it used (`die.txt`).
3. The assembler (`--band`) abuts the two tile stacks on the band's edges.

The tiles and strips sit half a nanometre off the whole-nm grid in their
masters, with or without the band (see the DRM grid note below). For the 6T
tile, the shift puts its M4 pins off the LEF's 1 nm grid. Each pin therefore
also gets a V4 and a short M5 landing on an M5 track, so the router can reach
it.

**DRM grid.** The macro's frame is the ASAP7 DRM's absolute grid, so a chip
must place the macro's origin on a multiple of 1.728 um (the least common
multiple of the gate pitch and the M1-M7 track pitches):
- **Rules:**
  - fins centred 13.5 nm past a multiple of 27 (FIN.S.1);
  - gates 27 nm past a multiple of 54 (GATE.S.1);
  - M4/M5 tracks 12 nm past a multiple of 48, and M6/M7 tracks 16 nm past a
    multiple of 64 (AUX.2).
- **How the assembler meets them:**
  - The controller is routed on those tracks.
  - Each block's origin snaps onto the grids. The bitcell's fins sit half a
    fin pitch off the standard cells', so the tiles and strips also take the
    half-nanometre shift.
  - The margins and channels grow by up to 0.43 um to make room.
  - The supply stripes and side pins sit on the tracks.
  - The IO blocks route on M4, so their lines go on the tracks too. Where a
    tile lands is known only once the floorplan is laid out, and a stack's
    tiles land on different tracks: a middle 8T tile is 2376 nm, half a
    track pitch past a multiple of 48. Each tile is therefore rebuilt for
    its place, its blocks drawn for that grid (chipforge's `m4_grid`), and
    the masters are named for it (`dp_column_g0x19p5`).
- **Array edge:** the array's edge cells all carry the SRAMDRC marker, the 6T
  tile's blank and edge fillers included.
  - The 8T strip pairs climb to port B's M5 wordlines on via stacks, one
    row of them per pair, on M4 too. After the floorplan each pair's row
    moves onto the track it lands nearest, the M3 stubs under it grown to
    reach (`retrack_strip_vias`). The stubs reach 17-62 nm from the pair's
    edge, so 4 of the 96 half-nanometre placements have no track in reach;
    outside the band the assembler moves the pair to the next fin-grid
    place, and in the band it warns and leaves the row as drawn. The band's
    controller is routed before the row moves, so the band plan obstructs
    the row's whole reach on M3 and M4.

Results (`scripts/verify_macro.py`, `scripts/simulate_macro.py`):

- 8T x4x2x2 with `--share-port-b`: 20 % smaller than without the band, LVS match.
  x8x2x2 also matches LVS.
- 6T x64x8x1 with `--segment-bits 2`: 9.99 x 20.20 um (202 um²) against
  10.03 x 22.52 um (226 um²) without the band, 11 % smaller. LVS matches; the
  macro is too large to simulate at transistor level.
- 6T x16x8x1: 4.81 x 16.91 um, LVS match. The Xyce program passes, with clk->WL
  128 ps, clk->SAE 223 ps and clk->Q about 260 ps.
- DRC findings fall in the same categories as without the band: 52 against 63
  on x64x8x1.

### 8T cell libraries

The flow builds on generated native ASAP7 cells (schematics in `tech/spice/`).
The GDS libraries are build output: the `tech_gds` target (part of the default
build when `.venv/bin/python`, or `python3`, can import `gdstk` and
`chipforge_asap7`) runs `scripts/tech_gds.py`, which writes them into
`tech/gds/` in about 15 s, and `OpenFinRAM` writes any that are missing before
a 2RW or 6T run. Git tracks only their geometry digests
(`tech/gds/digests.json`); after an intended generator change, run
`scripts/tech_gds.py --update`.

- `tech/gds/sram_cell_8t.gds`: the 8T bitcell and its mirrored-slot variants,
  built from the published ASAP7 6T core with ASAP7-native port-B geometry and
  the public OpenRAM dual-port topology. The matching dummy, row-cap,
  column-cap and corner family is in `tech/gds/sram_cell_8t_edges.gds`. See
  [docs/asap7_8t_bitcell.md](docs/asap7_8t_bitcell.md) for the pin map,
  provenance, rebuild commands and verification scope.
- `tech/gds/sram_wordline_arrays.gds`: parameterized `sramcol_xN` and
  `array_xNxM` hierarchy for 8T and 6T. The tracked ladder has 2/32/64/128
  wordlines; `scripts/generate_asap7_wordline_arrays.py` accepts any positive
  count, and the macro compiler generates the tapped arrays it needs on demand.
- `tech/gds/sram_8t_iocolumn.gds`: the port-A, port-B and two-sided port-B IO
  blocks (`iocol_sram_8t_a/b/b2`) and the `colgrp_x{N}x4_sram_8t` column
  groups that join them to an array, written with their SPICE by
  `scripts/generate_asap7_8t_iocolumn.py`.
- `tech/gds/sram_8t_wl_slices.gds` and `tech/gds/sram_6t_iocolumn.gds`: the
  wordline driver slices and the 6T IO block (both chipforge_asap7 devices).

## Tests

Device DRC uses a pinned [gdscheck](https://github.com/aesc-silicon/gdscheck)
dependency and a local ASAP7 width/spacing deck. Install with
`bash scripts/install_gdscheck.sh`, then run
`bash tests/run_8t_device_drc_check.sh`. See [gdscheck DRC](docs/gdscheck.md)
for coverage, calibration, reference cross-checks, and macro usage. The full
public ASAP7 runset and LVS continue to use KLayout.

Run via CTest (requires `iverilog` and `yosys` in `PATH`):

```
cmake -S . -B build && ctest --test-dir build --output-on-failure
```

- `tests/run_decode_check.sh`: exhaustive RTL decode-correctness sweep of
  `ctrl_decode` (Icarus Verilog) over read/write, deselect and no-op cases.
- `tests/run_equiv_check.sh`: post-synthesis formal equivalence (Yosys
  `equiv_*`) between the `ctrl_decode` RTL and the mapped ASAP7 netlist,
  including the production structural signoff assertions. This catches
  synthesis-introduced decode bugs that RTL simulation cannot see.
- `asap7_tech_gds_check`: the built `tech/gds` libraries against their
  recorded geometry digests.
- `tests/run_8t_bitcell_check.sh`: ASAP7 8T bitcell/edge GDS regeneration
  against its digests, focused rules, extracted connectivity, and abutment checks.
- `tests/run_wordline_array_check.sh`: deterministic parameterized 8T/6T
  wordline-row and array hierarchy, including a non-standard x18/mux-2 case.
- `tests/run_8t_iocolumn_check.sh`: deterministic dual-port IO-column routing,
  capped array abutment, internal read-port ties, and a non-standard x18 case.
- `asap7_2rw_physical_check` (`tests/tools/test_2rw_physical.py`): the 2RW
  assembler's tiles, abstracts and abutments without running the router.
- `asap7_2rw_macro_check`: builds and routes a small 2RW macro end to end and
  checks its GDS, LEF, Liberty and SPICE agree (slow; needs OpenROAD).
- `python3 tests/check_macro_pins.py macro.gds macro.lef`: checks that a
  macro's GDS pin labels match its LEF pins and sit on metal of their own
  layer, including routed M4/M5 control pins and the M7 power stripes. The
  macro check runs it; it is a geometry check, not connectivity LVS.

## Commercial Flow (Cadence / Synopsys, default)

```
./OpenFinRAM --num-wls 2 --num-data-bits 4 --num-banks 1 --single-port
# uses dc_shell + innovus and optional calibre/siliconsmart as before;
# LEF export itself is native and no longer needs Cadence Abstract
```

## External Dependencies

**Build (all flows):** CMake >= 3.22, C++17 compiler, Python 3 (for the
helper scripts in `scripts/`).

**Commercial flow (default):** Synopsys `dc_shell`, Cadence `Innovus`;
optional Calibre (LVS/DRC) and SiliconSmart (.lib characterization).
LEF export is native GDSTK and no longer needs Cadence Abstract or `tcsh`.

**Open-source flow (`--openroad`):**
| Tool | Tested with | Purpose |
|---|---|---|
| [Yosys](https://github.com/YosysHQ/yosys) | 0.69 (SiliconCompiler's pin) | RTL synthesis of `ctrl_decode` |
| [OpenROAD](https://github.com/The-OpenROAD-Project/OpenROAD) | `755f5778c4` (SiliconCompiler's pin) | P&R, CTS, STA; pin `--openroad-path` |
| ASAP7 platform | OpenROAD `platform/asap7` | LEF/lib/GDS/RC; pin `--platform-path` |
| KLayout Python bindings (`pip install klayout`) | 0.29+ | DEF -> merged GDS streaming (`scripts/def_to_gds.py`) |
| `chipforge_asap7` (`pip install -r requirements-asap7.txt`) | pinned | ASAP7 device generators for the 8T/6T cell libraries, IO blocks and wordline strips |

**Tests (`ctest`):**
| Tool | Tested with | Used by |
|---|---|---|
| Icarus Verilog (`iverilog`/`vvp`) | 13.x | `decode_check` (RTL decode sweep) |
| Yosys | 0.69 | `equiv_check` (post-synthesis equivalence) |
| googletest | submodule | `unit_tests` (TCL generator goldens) |

## TODO / Future Work

- **Array decoupling capacitors**: industrial SRAM macros embed MOSCAP or MOM
  decap in dummy rows/columns and periphery strips to suppress di/dt droop.
  ASAP7 provides no cap device (no MIM layer exists at 7nm; the GF180/Sky130
  style metal-insulator-metal option disappeared after 28nm), so this requires
  characterizing a gate-only NMOS MOSCAP dummy cell or an interdigitated
  M3/M4/M5 MOM cell (DRC + LVS + extraction) before it can be tiled by the
  compiler. The current dummy-fill cells provide the structure but no
  intentional capacitance.

## References

- **ASAP7 PDK**: https://github.com/The-OpenROAD-Project/asap7
- **GDSTK**: https://github.com/heitzmann/gdstk
- **BSG FakeRAM**: https://github.com/bespoke-silicon-group/bsg_fakeram
- **PLOG**: https://github.com/SergiusTheBest/plog

## License

This project is licensed under the BSD 3-Clause License - see the [LICENSE](LICENSE) file for details.
