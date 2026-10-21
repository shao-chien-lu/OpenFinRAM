# Wordline driver study: the compiler's cells against a sized slice

Should `DriverSliceSpec` (chipforge_asap7's pitch-matched post-decode NAND and
wordline inverter, sized by `size_decoder`) replace what the compiler uses
today, `AND2x2` + `BUFx4` inside the synthesized controller? The macros built
so far put four cells on a wordline, where the wordline rises in 8 ps whatever
drives it, so they cannot say. `scripts/characterize_wl_driver.py` asks in
Xyce, before any layout is touched:

```bash
.venv/bin/python scripts/characterize_wl_driver.py [--wire xact|setrc] [--corner TT|SS|FF]
```

Fifteen seconds for the whole sweep. The wordline is an RC ladder driven from
one end, as the controller at the top of today's floorplan drives it: per cell
0.594 um of 18 nm M3 and the two port-A access gates of a real, preloaded
`sram_cell_8t` with its bitlines held at VDD. Measured: 0.181 fF a cell, half
of it wire.

## Results (TT, 0.7 V, far end of the wordline, 50 % to 50 %, ps)

M3 resistance is the largest unknown, and the repo holds two values 5.4 apart:
168 ohm/um from chipforge_asap7's stack (its M1 sheet resistance, calibrated on
the released xACT extraction, carried to M3) and 31 ohm/um in `tech/setRC.tcl`,
which the compiler's STA uses and which is bulk copper at that cross-section.

| cells | C | wire alone | `BUFx4` | `BUFx24` | slice | slice vs today | energy today / slice |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **168 ohm/um** | | | | | | | |
| 16 | 2.9 fF | 2 | 27 | 28 | 15 | -12 | 3.0 / 2.3 fJ |
| 32 | 5.8 | 8 | 34 | 34 | 22 | -12 | 4.5 / 4.4 |
| 64 | 11.6 | 28 | 58 | 55 | 42 | -16 | 7.3 / 8.6 |
| 128 | 23.1 | 111 | 147 | 139 | 124 | -23 | 13.0 / 17.5 |
| 256 | 46.4 | 440 | 488 | 470 | 458 | -31 | 24.3 / 34.2 |
| **31 ohm/um** | | | | | | | |
| 16 | 2.9 | 0 | 26 | 27 | 14 | -12 | 3.0 / 2.3 |
| 32 | 5.8 | 2 | 30 | 28 | 18 | -12 | 4.5 / 4.4 |
| 64 | 11.5 | 6 | 38 | 33 | 20 | -18 | 7.3 / 8.7 |
| 128 | 23.1 | 21 | 61 | 49 | 34 | -27 | 13.0 / 17.5 |
| 256 | 46.3 | 82 | 135 | 112 | 99 | -36 | 24.4 / 34.1 |

"Wire alone" is an ideal source driving the ladder: no driver can beat it.

## What it says

1. **The slice is 12 to 36 ps faster at every length, under either wire.** It
   is two stages where `AND2x2` + `BUFx4` is four, and that is nearly the whole
   difference: the library path spends 25 ps reaching its own output before the
   wordline starts. At SS and 128 cells it is 122 against 149 ps.
2. **Upsizing the library buffer does not recover it.** `BUFx24` is within 3 ps
   of `BUFx4` up to 64 cells and never gets closer to the slice than 12 ps; the
   `AND2x2` in front cannot drive it, and the near-end delay stays at 24-27 ps
   whatever follows.
3. **The slice costs energy at length**: equal up to 32 cells, 35 % more at 128
   (17.5 against 13.0 fJ a pulse), because its driver is 70 fins where `BUFx4`
   is 12 and the wordline does not get there any sooner for it. It also puts
   1.5 to 6.6 fF on the select line per four wordlines, where four `AND2x2`
   put 1.1 fF; `size_decoder` sizes the stage before it for that.
4. **Past 64 cells the wire decides, not the driver.** At 168 ohm/um a 128-cell
   wordline takes 111 ps with a perfect driver and a 256-cell one 440 ps, against
   a clock-to-wordline of 118 ps in the whole-macro simulation. No driver fixes
   that; a strap on a thicker layer, a shorter wordline (a lower mux ratio, more
   banks) or a repeater does. Which resistance is right is worth more than the
   choice of driver, and the released xACT netlists of cells that route on M2
   (same 36 nm thickness as M3) would settle it.
5. **`size_decoder` is right to within a few picoseconds, and conservative.**
   It predicts 17 to 18 ps for NAND plus driver into a lumped load; Xyce gives
   13.6 ps at 16 cells and 16.1 to 16.7 from 32 to 128. Its constants were
   calibrated on single gates and this is the first check of a sized chain.
6. **`DriverSliceSpec.from_sizing` stops at 128 cells.** A 46 fF wordline asks
   for a 72-fin NAND stack, 36 a finger, and `NandSpec` is one 18-fin row. The
   256-cell rows above use the largest NAND that can be drawn (20.8 ps into a
   lumped load instead of 17). A two-row NAND, as the inverter has, removes the
   limit.

## A mock-up against the array

```bash
.venv/bin/python tmp/wl_driver_study/gds/make_mockup.py 4 <slices.gds> slice_array_mockup_x4.gds
.venv/bin/python tmp/wl_driver_study/gds/check_mockup.py slice_array_mockup_x4.gds <workdir>
```

The smallest slice abutted to a capped column group built the way the compiler
builds it (`compile_asap7_2rw.build_leaf`), with **four wordlines per half**, so
that one slice sits exactly under the four bitcell columns of each half. (A
first attempt used the 4x2 macro's group, which has two wordlines and then a
tap per half: the slice is four wide, so half of it had nothing to drive and it
sat one column right of the array block. Wordlines per half have to be a
multiple of four.) Nothing is placed by hand: the script reads each port's
track positions off the array's own `WLTA[i]`/`WLTB[i]` labels, puts a slice
under every four, and refuses if any output is not on its wordline. Port A
slices sit against the array's bottom edge, the bottom half's mirrored; port B
slices hang below, flipped so the two share a VSS rail, and reach the array
through a VIA34/VIA45 stack and an M5 strap over both slices; fillers close
every row end. Seven seconds to extract and check. It is a way to see what
lines up, not a design.

What lines up:

* Port A wordlines are M3 at 54 nm into each 108 nm cell (270, 378, 486, 594 in
  the top half), which is where the slice puts its outputs. The gate tracks
  share one 54 nm grid, and the slice's top gate cut (22 nm either side of the
  edge) covers the array's 19 nm poly overhang.
* Port B wordlines are 24 nm M5, 6 nm off the M3 tracks. The two via pads
  overlap on M4, so the jog costs nothing.
* Extracted: all sixteen slice outputs, both ports and both halves, reach
  exactly their own wordline. No slice rail touches an array net: power has to
  be connected on purpose.

What does not:

* Until 2026-09-21 only ten did. Port A's wordlines 1 and 2 were one net and
  wordline 3 was on `vss!`, in the array on its own as well: the bottom WLA gate
  contact started on the placement seam and met its mirrored neighbour's
  (`docs/macro_verification.md`, finding 2, where it had shown up as one
  wordline on VSS). Building this mock-up is what exposed the general case; it
  is fixed in the bitcell generator.
* DRC adds ten markers, all on the seam, none inside the slices: `FIN.S.1`
  across each half, because the array centres a fin on its cell boundary and the
  slice centres a fin *space* on its own, half a pitch (13.5 nm) apart, so no
  direct abutment has both grids on pitch; and `LIG.S.4-5` with
  `SRAM.LIG.GATE.OV.2` where each *filler* column meets the array's bottom edge
  (under the column cap and under the tap), not where a slice does. A transition
  row, or a slice drawn on the array's fin grid, is needed at the seam.

> **Floorplan, 2026-09-21.** The mock-up above was built on the mid-bitline
> column (two half arrays, `WLTA`/`WLTB` labels). A column is now one unsplit
> array with an IO at each end of the bitlines, its wordlines labelled
> `WLA[i]`/`WLB[i]` over `2 * --wordlines`; `make_mockup.py` reads the old
> labels and needs that one change before it is rerun. Nothing about the
> slice or the comparison depends on it.

## What it does not say

* **It was not the floorplan of the day.** When this was written,
  `compile_asap7_2rw.py` placed one `dp_column` macro per bit below the
  controller with 2 um channels, and OpenROAD routed each wordline between
  them on upper metal. Since 2026-09-22 it is: the tiles abut in two stacks
  with the controller between them, and a strip pair of these slices drives
  each stack's wordlines from its edge (`docs/asap7_8t_bitcell.md`,
  "Floorplan: controller band between two abutted stacks";
  `scripts/generate_asap7_8t_wl_slices.py` writes the ladder the compiler
  picks from). The mock-up's seam lessons carried over: the strips sit half
  a fin pitch in from the seam, and the gate tracks are bridged across that
  margin.
* The schematic of both drivers, with no parasitics of their own. The slice's
  extracted resistance is available in seconds (`run_open_pex(capacitance=False)`)
  and is the obvious next refinement.
* One port, one wordline switching, ideal 20 ps input edge, cells in read
  condition.
