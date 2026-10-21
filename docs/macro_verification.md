# Macro verification: transistor LVS and device DRC with open tools

`scripts/compile_asap7_2rw.py` publishes a macro once the routed metal graph
re-extracts correctly, and records what it did not do:

```json
"physical_connectivity": "PASS", "signoff_lvs": "not_run", "device_drc": "not_run"
```

The metal-graph check treats `dp_column` and `dp_controller` as abstracts. It
proves that every routed terminal reaches its net. It cannot see a short
inside a column, because it never looks inside one. `scripts/verify_macro.py`
does the part that was not run:

```bash
.venv/bin/python scripts/verify_macro.py results/sram_x4x2x1_<stamp> \
    --baseline tests/golden/asap7_2rw_macro_verification.json
```

About 40 s for the 4x2 macro: strict LVS, a second diagnostic LVS if that
fails, DRC over the whole macro, `tmp/verify_<name>/verification.json`.
`tests/test_macro_verification.py` holds the result against the baseline.

## How the LVS works

KLayout with chipforge_asap7's ASAP7 deck (`run_hierarchical_lvs`), against the
`.sp` the compiler writes beside the GDS. Four things make a macro different
from a generated cell, and each had to be dealt with before the comparison
meant anything:

| Problem | What is done |
| --- | --- |
| The reference has `nfin=N` devices; extraction finds one device per fin | The reference is expanded to unit fins. Totals agree exactly: 6910 (3264/3198 RVT, 384/64 SRAM-Vt) |
| Released CDL has one net inside a series stack; on the fins it is one per fin | Source/drain is taken as the whole ACTIVE outside the gate, as the ASAP7 Calibre deck does (`merge_fin_diffusion`) |
| A bitcell's transistors are completed by its neighbours, so the layout's `sram_cell_8t` holds 3 devices and the schematic's 14 | `sram_cell_*` is flattened on both sides |
| Released cells whose GDS and CDL order a series stack differently | Proven equivalent as series/parallel networks, then compared by pin name, and listed |

The last one is a property of the released ASAP7 library, not of this
compiler. `AOI211xp5` stacks its pull-up C, B, (A1‖A2) in the GDS and
(A1‖A2), B, C in the CDL. `AO21x1` has A1 and A2 the other way up its
pull-down. Calibre's gate recognition lets both pass. KLayout's compare is
strictly topological: it fails the first, and *matches* the second by pairing
A1 with A2, which then surfaces in `ctrl_decode` as four nets that look
identical and will not pair. The series/parallel check reduces each pull
network to a tree with unordered series, names internal nets from the pins
inward, and requires equal trees and equal fins per input. Only library cells
(`*_ASAP7_75t_*`) can be excused, only by that proof, and the result names
them.

With that, everything below the top matches at transistor level: all 24
standard cells, `ctrl_decode` (258 instances, 275 nets), `ioprech_sram_8t_a`
and `_b` (514 devices each: precharge, sense amplifier, write driver, column
mux).

> **Floorplan, 2026-09-21.** The numbers in the rest of this note were taken on
> the mid-bitline floorplan (two half arrays with both IO wrappers between
> them). A column is now one unsplit array with port A's IO at one end of the
> bitlines and port B's at the other; see "After the move to one IO per end"
> at the bottom for what changed. The two findings and their mechanism are
> the same.

## What it found

The top level did not match, for two reasons. Both were shorts to VSS, and
both real layout defects rather than artefacts of the deck. The second has
since been fixed; the first is what still stands between the macro and a match.

### 1. Every complement bitline is tied to the substrate at the array taps

`tapcell_sram_8t` starts its PSELECT at its cell edge. The bitcell beside it
overhangs that edge, as the ASU 6T cell does: NSELECT by 27 nm, ACTIVE by 8 nm.
So an 8 x 54 nm sliver of the bitcell's diffusion lies under both implants,
and that sliver is the complement-bitline contact. In
`sramcol_x2_tap2_sram_8t`:

```
ACTIVE  (154,68)-(224,122)      bitcell diffusion, to 8 nm past the edge at x=216
NSELECT (150,54)-(243,135)      bitcell implant, to 27 nm past it
PSELECT (216,54)-(324,135)      tap cell implant, from the edge
SDT     (204,74)-(228,122)      the contact bar, over n+ and the sliver alike
```

ACTIVE under PSELECT outside NWELL is a substrate tap, so the contact ties
`BLTN`/`BLBN` of both ports to the p-well and the taps tie the p-well to VSS:
32 IO-column pins on `vss` in the layout and on their own nets in the
reference. DRC sees the same thing independently, as 20
`NSELECT.PSELECT.AUX.1` ("NSELECT and PSELECT may not overlap"). The released
`array_x32x4_sram_6t122` has the same overhangs and no overlap at all, so this
is the 8T tap cell's placement, not the style. The fix is in the tap cell's
implant extents (or a spacer beside it), so that PSELECT begins where the
neighbour's NSELECT ends.

### 2. Port-A wordlines shorted at the placement seam (fixed 2026-09-21)

**Fixed** in `scripts/generate_asap7_8t_bitcell.py` (`add_wla_routes`,
`add_storage_straps`): the bottom WLA gate contact started on the cell's west
seam, LIG 0..37 nm, and columns are placed mirrored about that seam, so two
contacts met edge to edge and were one LIG strap. It is now on gate A at
16..37 nm (V0 18..36), the lower storage strap jogs right around its M1
landing as the upper one already jogged left, and the top contact's LIG stops
at 92 nm instead of 96: 32 nm between neighbours on both seams. Arrays built
with 2, 4 and 32 wordlines per half now extract with every wordline on its own
net, the macro's diagnostic LVS run **matches**, no DRC rule went up and seven
went down (2722 to 1922 markers on the 32-wordline group; four entries came off
the device-DRC ratchet). What follows is the finding as made.


The `dummy_vertical_8t*` cells ground their own wordline gates: LIG strap, V0,
M1, V1, an M2 bar labelled `vss!`, V2, M3. For the track at x = 369-387 that M3
is drawn full length through the end rows, (369,-594)-(387,0), where the other
tracks get short stubs such as (405,-575)-(423,-541). That track is the live
`WLA[1]`, continuous with the array's M3 on its way to the decoder. The same
LIG strap, (395,-166)-(469,-150), also reaches the corner cell's VSS track at
x = 477-495. It first appears in `capped_colgrp_x4x4_sram_8t`, where one net
carries `WLTA[1]` and `vss!`; `colgrp_x4x4_sram_8t` is clean. In the macro
`wlt_A[1]` and `wlb_A[1]` are on `vss` with 64 access gates; the other six
wordlines are right.

The 4x2 macro has two wordlines per half, which hides most of this. The end
cells come in mirrored pairs that share that LIG strap, so what is really tied
together is each *pair of adjacent port-A tracks*, and the last track before a
tap or a corner goes to the corner's VSS instead. Extracting
`compile_asap7_2rw.build_leaf(n, ...)` on its own, top half, port A:

```
n = 4    0   1+2   3=vss
n = 32   0   1+2  3+4  ...  13+14  15=vss   16  17+18  ...  29+30  31=vss
```

Port B, on M5, is separate all the way. So in any array taller than the test
macro, fifteen of every sixteen port-A wordlines are shorted to a neighbour or
to ground. Found while abutting a driver slice to a four-wordline array
(`docs/wordline_driver_study.md`), not by the macro LVS, which only ever saw
two wordlines.

### These are all there is, in the 4x2 macro

Removing the eight V2 that land those tie-offs on the wordline track, in a
scratch copy, and not counting doubly implanted ACTIVE as a tap, the whole
macro matches: 6910 devices, every net. That second switch
(`double_implant_is_tap=False`) is how `verify_macro.py` separates the two
findings: the first run reports 34 instance pins on VSS, the second the 2
wordline pins that are left. It is for seeing past the implant error, not for
passing with it.

## DRC

The public ASAP7 KLayout runset over the whole macro: 687 markers in 42 rules,
15 s, identical on builds from 13 and 20 September. It is not a sign-off deck,
and not all of the count is the generators': M7 to M9 are drawn only by the
top-level routing and its vias, so the `M8.*`, `M9.*` and `V7`/`V8` rules
(about 170 markers) are OpenROAD's wires and the macro's pins, and the
released hard cells trip the runset as well. The baseline holds each rule at
its current count, so the number can only go down.

## Limits

* Bodies are tied by declaration (`VDD`/`VSS`), because tapless library cells
  expose their wells as pins otherwise. Whether taps exist and are close enough
  is left to DRC (`ACTIVE.LUP.1`).
* Run on the 4x2 and 4x4 two-port macros, which show the same two findings and
  nothing else (4x4: 66 pins on VSS, 2 without the implant error, 47 s); only
  4x2 is held by a test. A 2-bank build did not get this far: the compiler's own
  `PERIPHERY_STA` gate stopped it on slew and capacitance.
* KLayout 0.30.2's hierarchical extractor stops on an internal assertion (`dbHierNetworkProcessor.cc ... id_new != 0`) on the
  single-port results from August; `run_lvs(flat=True)` avoids the assertion
  but cannot compare a cell by pin name, so those do not verify yet.
* This is LVS against the compiler's own netlist. It proves the layout is the
  circuit the compiler says it built, not that the circuit works: a full-macro
  read/write simulation is still the next gap.

## After the move to one IO per end (2026-09-21)

The column became `iocol A | edge cap | one array of 2*NUM_WL wordlines | iocol
B`; `wlt`/`wlb`, `yselt`/`yselb` and `blprechtn`/`blprechbn` collapsed to `wl`,
`ysel` and `blprechn` in the RTL, the generated netlist and the routing pin
map. Re-run on `sram_x4x2x1`:

* **Diagnostic LVS matches the whole macro**, including the eighteen idle-face
  controls the router ties to VSS/VDD, so the layout is the new netlist.
* **Strict LVS** still fails at the top for the one open reason, the tap
  implant overlap, now as 8 wrapper pins instead of 32 (`BLBN_A[0..3]` on
  `ioprech_sram_8t_a`, `BLTN_B[0..3]` on `_b`, each twice): one array has half
  the taps two half arrays had, and only one face of each wrapper meets it.
  `NSELECT.PSELECT.AUX.1` went from 20 to 10.
* **DRC: 651 -> 450 markers.** No marker falls in `iocol_sram_8t_a`, `_b` or
  the `colgrp`. The edge-row cell's counts all halved (one capped end instead
  of two) and the sixteen `V5.M6.AUX.2` of the M6 crossover are gone with it.
  Five rules went up (`M3.S.6` 12->16, `SDT.W.3` 42->48, `V1.M2.EN.2` 8->10 in
  the array row cells, which are four bitcells long now instead of two; `M8`/`M9`
  ones in the top-level routing, which differs run to run).
* The macro is 12.58 x 21.60 um against 13.00 x 22.14 um, with 742 checked net
  partitions against 816.
* `tests/golden/asap7_2rw_macro_verification.json` was rewritten for it.

## With the parametric IO (2026-09-22)

`iocol_sram_8t_a/b` now hold chipforge_asap7's column block instead of the
reused 6T wrappers. `verify_macro.py` flattens the block's cells (`blmux_*`,
`sarow_*`, `wrdrv_*`, `outlatch_*`, the supports) into the per-port block,
which the compiler's deck carries as one flat subcircuit. Re-run on
`sram_x4x2x1`: diagnostic LVS matches the whole macro; strict LVS fails only
on the tap implant overlap, now as `IOCOL_SRAM_8T_A.BLN_A[i]` and
`IOCOL_SRAM_8T_B.BLN_B[i]`; DRC 450 -> 458 markers, none in the block or its
cells (the deltas are the controller's place-and-route and the top-level
routing, which move run to run). The macro is 8.31 x 25.89 um: the IO is
half as wide, and the controller, sized to the narrower column, came out
taller. The baseline JSON was rewritten.

One finding on the way: the block's cells put their via row one fin from the
rail, which left M1 pads 13 nm from the M1 rail. The public runset has no
rule for a short edge facing a long one, but every published cell keeps 18,
and the device-DRC gate said so. The via row now sits a fin further in.

## With the abutted stacks and driver strips (2026-09-22)

The macro is now two abutted stacks of column tiles with the controller band
between them and a pair of `DriverSliceSpec` strips on each side (see
`docs/asap7_8t_bitcell.md`, "Floorplan: controller band between two abutted
stacks"). `verify_macro.py` flattens the slice cells (`wl_slice_*`,
`nand2_fin_*`, `inv_fin_*`, the via cells) so the strip
`wl_strip_c<cells>_x<slices>` and the pairs `wl_strips_{lo,hi}_...` compare
against the deck's subcircuits of the same names. Re-run on `sram_x4x2x1`:

* diagnostic LVS matches the whole macro, so every wordline is one net from
  its slice through the tile abutments (port A on M3 directly, port B through
  the VIA34/VIA45 stack and M5 strap), and no wordline touches a neighbour or
  a rail at any seam;
* strict LVS fails only on the tap implant overlap, the same eight
  `IOCOL_SRAM_8T_{A,B}.BLN_*` pins;
* DRC 458 -> 436 markers. The tile-to-strip seam adds nothing: the
  half-fin-pitch margin keeps `FIN.S.1` away, and the gate tracks are bridged
  across it (without that, the array's 7 nm gate stubs and the slice's gates
  left a 1.5 nm gap that read as a broken 54 nm pitch, `GATE.S.1`). What is
  left in the strip bands is the router meeting the strips' M1 rails and M4
  pads, of the kind the baseline already carries. The baseline JSON was
  rewritten; `AND4x1` left the series-order list with the controller's
  wordline gating.

One finding on the way: the compiler's deck writes the slices' devices with
`nf` fingers, and chipforge_asap7's unit-fin reference expansion multiplied
`nfin` by `m` but not by `nf`, so the strip's output inverters compared as
half their fins. The expansion now counts fingers.

## With the IO block abutting the array (2026-09-22)

The block now sits half a fin pitch up the column, on the array's fin grid,
with no gap (`asap7_8t_bitcell.md`, "The block sits on the array's fin
grid"). On the tile alone the public deck gives 220 markers before and
after, rule for rule, none at either seam. Re-run on `sram_x4x2x1`
(8.04 x 26.89 um: 0.28 um narrower, and taller because the controller is
floorplanned to the column's width): diagnostic LVS matches the whole macro,
so every bitline reaches its leaf through the cap's 18 nm and the tap's
27 nm bar overhang alone; strict LVS fails on the same eight tap-implant
pins; DRC 436 -> 455, the deltas (`M1.S.2`, `V1.S.4`, `M5.S.5`, `M8.*`) all
inside the re-placed controller and the top-level routing, whose marker
coordinates put them there. The baseline JSON was rewritten.

## With the floorplan tightened (2026-09-22)

Margins, channels and bank gap 0.3 um instead of 2 (0.2 is the floor), the
controller at 50 % utilization instead of 40, the dummy rows' stray stubs
tied inside the tile and supply pins offered on every layer
(`asap7_8t_bitcell.md`, "The dummy rows' stubs are tied in the tile").
`sram_x4x2x1` is 4.65 x 17.93 um, 83 um2 against 216. Diagnostic LVS matches
the whole macro; strict LVS fails on the same eight tap-implant pins; DRC
455 -> 435, the tile itself unchanged at 220. Baseline rewritten. The
narrow margins were not routable before the stubs were tied: the router
reached those lone bars only at some track phases, which is why the old
floorplan kept 2 um everywhere.

## The tap implant fixed: strict LVS matches (2026-09-22)

Finding 1 is fixed. `tapcell_sram_8t` is now two bitcell slots wide: its
tie implants stop 27 nm short of each seam, so they meet the neighbours'
overhanging selects edge to edge instead of doubly implanting the
neighbour's complement-bitline diffusion, and what is left (162 nm) clears
the 108 nm minimum implant width that a one-slot tap could not. The fins,
gates and rails run through both slots; the dummy rows put a corner cell
over each slot, the second mirrored. On `sram_x4x2x1` (4.70 x 17.93 um, a
bitcell slot wider per tap): **strict LVS matches the whole macro**, no
supply shorts; `NSELECT.PSELECT.AUX.1` 10 -> 0; DRC 435 -> 408. The tile
alone goes 220 -> 215, with one new `LIG.S.4-5` where the last dummy-row
corner meets the port-B IO block, which overhangs the column by half a fin
pitch (an unmirrored corner there clears it but breaks the gate pitch, +48
`GATE.S.1`). The test now asserts the strict match.

## Larger macros: x8x8 and x16x16 (2026-09-23)

`sram_x8x8x1` (5.74 x 40.35 um, c16 slices, two per strip) and
`sram_x16x16x1` (6.00 x 69.34 um, c32, four per strip) both build, route
and pass **strict LVS**; DRC 660 and 1034 markers, led by M4/M8 routing
spacing. Getting there found two things the 2-bit macro could not:

* A strip's slices do not share their predecode inputs: each slice's
  `B0..B3` is its own metal, and the strip had labelled them once, so every
  slice after the first had floating inputs (LVS: `wl_strip_c16_x2`
  nomatch). Every slice's `B<j>` is now a pin the router ties to `sel_lo<j>`,
  and `verify_macro.py` flattens the strips so that top-level wiring is in the
  comparison. (chipforge's two-butted-slice test checks DRC only.)
* x8x8 does not route at a 0.3 um margin (maze failures beside the
  controller's pins at the macro edge). The assembler now doubles the margin
  on a routing failure, up to `--max-margin` (1.2 um), and records the one
  it used (`margin_um`); x8x8 took 0.6.

## Dummy rows only at each stack's ends (2026-09-23)

With the interior dummy rows removed (`asap7_8t_bitcell.md`, "Dummy rows only
at a stack's ends"), strict LVS matches all three sizes: `sram_x4x2x1` 4.70 x
17.93 um (unchanged: one tile per stack keeps both rows), `sram_x8x8x1` 5.74 x
33.22 (was 40.35), `sram_x16x16x1` 6.00 x 52.71 (was 69.34). DRC 403 / 697 /
1053; the changes are all in the top-level M8/M9 routing, which now carries
each tile's nets over the blocks rather than beside its dummy rows, with no
gate, fin or local-interconnect rule moving. Baseline rewritten from x4x2.

## Routed on M1-M7, pins on M6/M7 (2026-09-23)

The macro used to route and pin on M1-M9, and about a quarter of the DRC
markers were on M8/M9: the public deck sizes those layers by wire length
(60 nm wide past 400 nm, 80 past 1.2 um, 120 past 1.8 um) and spaces them
by width, and wants V8 enclosed by 20 nm, while `tech/lef/asap7_tech.lef`
gives the router a flat 40 nm width and spacing and a V8 with no enclosure.
Nothing the macro needs is up there, so `compile_asap7_2rw.py` now routes on
M1 to `--top-layer` (7 by default) and places the pins on M6 (side edges)
and M7 (top and bottom); the LEF's obstructions stop at M7, leaving M8/M9 to
the chip. M6/M7 have no length-dependent widths, and the LEF's VIA67/VIA78
already carry the deck's 11 nm enclosure. Strict LVS still matches;
DRC x4x2 403 -> 318, x8x8 697 -> 481, x16x16 1053 -> 808, none on M6-M9
(`M4.S.5` grows with the extra lower-layer wiring, 168 -> 194 on x16x16).
Baseline rewritten.

## Several banks (2026-09-24)

Banks sit side by side, each with its own strip pairs, one controller
spanning them. `sram_x4x2x2` (10.46 x 16.88 um) and `sram_x8x8x2` build and
route. Transistor LVS of a whole two-bank macro does not finish: extraction
takes seconds, but KLayout's comparer gets lost pairing the banks' copies of
each bit's column, which share the data nets (x4x2x2 timed out after an hour;
naming every net in the layout brought that one to 16 s, but x8x8x2 still
ran past 50 minutes, and capping the comparer's search makes it give up on
circuits that match). Splitting at the tile does not work either: tiles abut
array row to array row, so a tile's edge bitcells are completed by its
neighbour's diffusion and the tile alone is not a circuit.

`verify_macro.py` therefore checks a multi-bank macro bank by bank
(`bank_views`): each view drops the other banks' column tiles from the layout
and their column groups (`X<bank>_<bit>`) from the reference, and keeps the
controller, every strip and all the routing. Each is a single-bank problem:
x8x8x2 matches strict in 47 s for both banks, x4x2x2 in 22 s. A short between
tiles of different banks is outside every view; the compiler's connectivity
gate, which proves every net one conductor isolated from every other, covers
it. DRC runs on the whole macro (x4x2x2 409, x8x8x2 693 markers).

With `--share-port-b` a pair of banks is one tile (`dp_colpair*`), so the
views are per pair (`X<pair>_<bit>`), and `sram_x4x2x2`, one pair, is checked
whole: strict match in one run. DRC 562 markers against 409 unshared; the
difference is KLayout listing a cell once per orientation it is used in
(`dummy_vertical_array_X4_tap4_8t:r0` and `:m90`, the same markers), plus
routing noise over the tiles (six `M1.W.1` via landings, one `V0.M1.AUX.3`,
one `V4.S.1-2-3`).

The compiled macro now also carries a label on every named net at a point the
connectivity gate verified, so nets are named in a viewer and LVS has names to
start from. The two-bank controller's place-and-route left one 1 nm M1 spacing
at 50 % utilization; the flow now retries at 44 % and 38 %.

## Waivers (2026-10-01)

`verify_macro.py` reads `tech/drc/asap7/waivers.yml` (gdscheck's waiver
shape: cell globs, rule ids, a reason). A KLayout marker is waived when the
runset files it under a cell matching a waiver, i.e. it came from that cell's
own geometry, and its rule is listed; it is reported apart and not counted, so
baselines pin only the rest. `--no-waivers` counts everything. Two waivers:
ASAP7 standard cells as delivered (DFFASRHQNx1, TIEHIx1 and TIELOx1 trip
V1.S.4, V0.S.1, LIG.SDT.S.8 and LIG.LISD.S.7) and the runset's V5.M6.AUX.2,
which checks horizontal M6 with V5's vertical-edge selection.

x16x16x1 is then 0 markers (27 waived). x4x2x1 keeps four at the macro's top
level: two 17.5 nm M1.W.1 slivers and two M1.S.6 corners where router M1
meets a tile's half-nanometre-shifted M1; the baseline was rewritten from a
fresh x4x2x1 build.

## gdscheck baseline (2026-10-21)

Macro DRC now defaults to gdscheck's ASAP7 main suite (0.2.0), and the
baseline records which engine, suite and version produced it, so the KLayout
baseline above no longer applied. The suite reads M1.S.2 as the DRM writes
it: 25 nm tip-to-side on M1, M2 and M3. It exempts shapes under the
bitcells' SRAMDRC marker. A fresh x4x2x1 matches LVS strictly and has
457 markers in 4 rules:
* 430 are in the column tiles (221 M1.S.2, 209 M2.S.2), outside the arrays.
  These are the 297 nm 8T IO cells, still drawn to the runset's 18 nm rail
  spacing.
* 24 are on the router's vias at the top level: M1.S.2, V1.M1.EN.1, M2.S.2
  and V0.M1.AUX.3.
* 3 are in the wordline driver strips: M1.S.2 and V0.M1.AUX.3.

The baseline was rewritten from that build, made with chipforge_asap7 at
53d27e8.
