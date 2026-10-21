# Whole-macro read/write simulation

The decks under `tests/spice/` simulate the bitcell and the IO column with
ideal wordline, select and sense-enable sources, and `characterize_read.py`
says as much: "wordline, clock and select drivers are ideal sources ... write
path not exercised yet". Nothing simulated the macro: the synthesized
controller making those signals, in its own time, for the array it is wired
to. `scripts/simulate_macro.py` does:

```bash
.venv/bin/python scripts/simulate_macro.py results/sram_x4x2x1_<stamp>            # back to back
.venv/bin/python scripts/simulate_macro.py results/sram_x4x2x1_<stamp> --spaced   # idle after each write
```

The DUT is the `.sp` the compiler writes beside the GDS, all 2410 devices of
it, driven only at its pins, in Xyce with the ASAP7 BSIM-CMG cards (level 72
rewritten to 107, `W`/`nf`/`m` stripped, as the other Xyce gates do). Five to
six minutes for ten cycles at 0.7 V TT. It is the schematic: no wire
parasitics, and not the layout, which `docs/macro_verification.md` shows does
not yet match it.

## How the testbench works

* **No DC operating point.** 32 bistable cells, the flops and the output
  latches have no unique one and Xyce finds none. The transient starts from
  `.IC` on every storage node with everything else at zero (`NOOP`), and reset
  is held for a quarter period.
* **Every cell is preloaded** with one of four words chosen by a hash of its
  address (zeros, ones, 0101.., 1010..), so reads can be checked before any
  write, and a stuck line or a swapped half does not reproduce the pattern.
  Cells are found by walking the netlist and placed by what they are wired to
  (`wl_a_lo[i]` or `wl_a_hi[i]`, `BL_A[c]`, `D_A[i]`), not by instance name.
  A column is one unsplit array with an IO at each end of the bitlines; "half"
  below means only the address bit above the column select, the top bit of
  the wordline index. The `lo`/`hi` is the stack of data bits the cell is in,
  each stack having its own wordlines from its own driver strips.
* **Inputs change on falling edges.** `sram_control.v` captures a command and
  its address on a rising edge with `ce_n` low and runs the operation in the
  clock-high phase that follows; Q is sampled at 45 % of the period.
* **A read only counts if Q moved after sense enable.** The output latch holds
  the last word a port read, and Q shows it again the moment the output
  enables, about 140 ps before sense enable fires. A read that expects what its
  port read last cannot fail, and the latch powers up holding something. The
  first version of this testbench passed for exactly that reason. Every read
  in the program differs from the one before it on its port, and a run is
  accepted only if each port's every bit was proven both ways.
* **The final state of all 32 cells** is compared with a software model, so a
  disturb anywhere in the array shows, not just in the words that were read.
* **Two hazards are reported by name**, because a corrupted cell does not say
  why: write enable moving in a cycle that is not a write, and an unselected
  wordline lifting above 0.3 VDD.

The program: read zeros on both ports (settles the latches); read ones, one
half each; write zeros over ones on both ports at once; each port reads what
the other wrote; write 1010.. and 0101.. at once; each port reads its own word,
then the other's; both ports read the same word; column 0 and the last column
in each half.

## What it found

> **Floorplan, 2026-09-21.** The measurements below were taken on the
> mid-bitline floorplan, where each port's bitline was two cells long in this
> macro. A column is now one unsplit array of four, with port A's IO at one end
> of the bitlines and port B's at the other. "On the unsplit column" at the
> bottom says what the same runs give now: timing is unchanged, and the
> write-enable glitch still fires but no longer costs data in this macro.

### With an idle cycle after each write: everything works

All sixteen reads correct and proven after sense enable, both ports, both bits,
both polarities; simultaneous two-port writes; cross-port and cross-half
read-back; same-address read on both ports; all four mux columns; all 32 cells
right at the end; no hazards.

| TT, 0.7 V, schematic | after an idle cycle | back to back |
| --- | --- | --- |
| clk to wordline | 160 ps | 118 ps |
| clk to sense enable | 231 ps | 190 ps |
| clk to Q (last transition) | 273-285 ps | 231-239 ps |

The 40 ps difference is one clock-to-Q of the state register: after an idle
cycle `read_req` has to become true before precharge can release, where back to
back it already is. Liberty's estimated clock-to-Q is 218 ps.

### A read straight after a write corrupts the cell it reads

```systemverilog
if (write_req_A) begin
    wrena_A[slice_sel_r_A]  = clk;       // write_req_A = (state_A == WRITE) && !ce_n_A
    wrenan_A[slice_sel_r_A] = ~clk;
end
```

On the rising edge after a write cycle the clock is already high and
`state_A` is still WRITE for one clock-to-Q. If the next command keeps `ce_n`
low, write enable pulses for that long. In the read cycle after writing `11`
to address 2 (times from the clock edge):

```
 ps    wrena  wrenan  prechN   sa     BL     WL     Q     QB
 50    0.00   0.70    0.00    0.70   0.70   0.00   0.70  0.00
 60    0.69   0.19    0.47    0.35   ..     0.00   0.70  0.00    <- write enable glitches
 80    0.00   0.69    0.70    0.04   0.04   0.00   0.70  0.00    <- D pin (0) is on the bitline; precharge has let go
120    0.00   0.70    0.70    0.05   0.06   0.48   0.72  0.02    <- wordline opens onto a grounded bitline
160    0.00   0.70    0.70    0.10   0.08   0.70   0.04  0.69    <- the cell is overwritten
```

The write driver's latch is transparent to the D pins whenever write enable is
low, so what is written is whatever D holds during the read: data is lost
unless D happens to equal it. It needs a write and then a read back to back on
one port, which is ordinary use. Which reads are hit was simulated, not
reasoned (my first two guesses were wrong):

| write, then read on the same port | hazard fires | result |
| --- | --- | --- |
| the same address | yes | the word is overwritten with D |
| the same column, another row | yes | the word that is read is overwritten with D |
| another column, same row or not | yes | reads correctly, all 32 cells intact |
| either, with an idle cycle between | no | correct |

The column select of the written column is still on when the glitch comes, so
its bitline goes to ground with the sense line, and stays there only if that
column stays selected: the sense line is the larger node and holds it down
until the wordline opens. When the select moves to another column the new
bitline is dragged part of the way and the read still resolves. That is this
four-row array at TT with no wire capacitance; it says nothing reassuring
about a taller column or a slow corner.

Both ports do it. In the default program: write enable at 0.70 V in cycles 4
and 6 on A and B, four wrong reads, and address 4 bit 1 and address 13 bit 0
left at 0. RTL and gate-level simulation without timing cannot see this, and
STA has no path to flag: it is a glitch on a combinational output gated by the
clock it is registered on.

A candidate fix: gate write enable with the delayed clock that already
times the wordline (`wl_any_fire_A`, five buffers, about 100 ps after the
edge) instead of `clk`. The state has settled well before it rises, it falls
while the state is still stable, and write enable then arrives with the
wordline rather than 100 ps ahead of it. Not applied here: it is the
controller's RTL, and it wants a rebuild and this simulation to confirm.

### The clock period Liberty claims is four times too short

The spaced program, TT, 0.7 V, schematic, Q observed at 45 % of the period:

| period | result |
| --- | --- |
| 2.0, 1.0, 0.8, 0.7 ns | pass |
| 0.6 ns | six reads wrong, all 32 cells intact: Q arrives at 283 ps, the clock falls at 300 |
| 0.5 ns | every read wrong, cells intact; the previous wordline is still at 0.36 V a quarter period before the next edge |
| 0.4 ns | every read wrong, cells intact; the previous wordline is fully up (0.73 V) into the next cycle |

The operation lives in the clock-high phase, so the high phase has to hold
clock-to-Q (285 ps after an idle cycle), and the low phase has to hold the
wordline's fall, which trails the clock by the same delay chain that times its
rise, plus precharge recovery. `sram_x4x2x1.lib` says `min_period : 0.157`.
That number is `kMinPeriod` in `liberty_estimator.cpp`, a constant; this is the
first measurement against it, and it is the schematic without wires, so the
real figure is longer still.

### Noted, not failing

* At the end of a write, precharge comes back with the falling clock while the
  wordline stays up another 100 ps (it follows the delayed clock). The cell's
  low node is bumped to 0.12 V and recovers: the same stress as a read.
* Back to back, precharge releases with the clock edge while the address
  registers are still changing; the wordline fires at 118 ps, after they have
  settled (about 50 ps plus decode). No unselected wordline lifted in any run,
  but that margin is tens of picoseconds at TT with no wires.
* The sense lines float whenever no column is selected. From power-up to the
  first operation they sat at 0.38 V.

## Limits

* The schematic, not the layout, and no parasitics: bitline and wordline RC
  will move every number in the table.
* TT, 0.7 V, 25 C only (`--corner SS|FF`, `--vdd` exist; not yet run).
* One bank. The address map covers the single-bank macros the 2RW flow builds
  today.
* `tests/test_macro_simulation.py` runs the testbench's own logic always, and
  the two Xyce runs with `OPENFINRAM_SLOW_TESTS=1`. The second half of that
  test holds the write-enable failure as found, and should become a plain pass
  when the controller is fixed.

## On the unsplit column (2026-09-21)

Same testbench, the macro rebuilt as `iocol A | cap | one array | iocol B`
(`wl_A[4]`, `ysel_A`, `blprechn_A`, and the same for B):

* **Spaced program: PASS**, as before. All sixteen reads correct and proven
  after sense enable, both ports, both address halves, all four columns,
  two-port writes, all 32 cells right, no hazards. clk to wordline 118 / 154 ps,
  to sense enable 193 / 229 ps, to Q 234-288 ps: the same numbers, which is
  what a schematic without wires should say about a bitline four cells long
  instead of two.
* **Back to back: FAIL on the hazard alone.** Write enable still reaches
  0.71 V in cycles 4 and 6 on both ports. Every read is right and all 32 cells
  are intact, where the mid-bitline macro lost four reads and two cells.
* The cases that used to lose data were run on their own (`tmp/unsplit/
  glitch_*.py`): a read straight after a write to the same address, to the
  same column in the same address half, and to the same column in the other
  half, which is the same bitline now. D held 00 against a stored 11 each time.
  The hazard fires and the read is correct in all three.

Why, from the nodes (port A, column 1, time from the clock edge of the read):

```
 ps    wrena   BL     BLN    sa     san    WL
 50    0.00   0.70   0.70   0.70   0.70   0.00
 65    0.65   0.20   0.68   0.17   0.68   0.00    <- the glitch puts D (0) on the bitline
 75    0.00   0.15   0.70   0.15   0.70   0.00
115    0.00   0.15   0.70   0.15   0.70   0.00    <- it floats there until the wordline opens
130    0.00   0.23   0.45   0.21   0.53   0.69
160    0.00   0.31   0.04   0.31   0.05   0.70
185    0.00   0.34   0.00   0.34   0.00   0.70    <- 0.34 V against 0: the right answer, barely
```

The cell holds a 1, so it pulls the grounded bitline up while its other side
discharges the complement, and sense enable finds 0.34 V against 0 V instead
of 0.70 against 0.5. It resolves, 6 ps later than a clean read. That is
margin on four rows at TT with no wire capacitance, not a fix: the pulse is
still there, a cell is still exposed to a bitline at 0.15 V with its wordline
open, and nothing here says what a taller column, a slow corner or mismatch
does with it. The candidate fix above stands.

## With the parametric IO (2026-09-22)

Same testbench, the IO columns replaced by chipforge_asap7's block: spaced
program PASS (16/16 reads, all 32 cells, no hazards), clk to Q 219-259 ps
against 234-288 with the wrappers, 903 fJ against 986. Back to back: the
write-enable hazard fires as before, no read wrong, no cell lost.

## With the abutted stacks and driver strips (2026-09-22)

Same testbench on the mid-band floorplan: the controller no longer drives
the wordlines, it sends `sel_hi`/`sel_lo` to a pair of driver strips per
stack, and each stack's wordlines (`wl_a_lo[i]`, `wl_a_hi[i]`, ...) are
watched separately; a read's wordline time is the later of the two stacks'.
Spaced program PASS: 16/16 reads, all 32 cells, no hazards on either stack's
unselected wordlines; clk to wordline 113 / 148 ps (was 118 / 154 with the
controller's buffer stage and the wire-less net), to sense enable 189 / 224,
to Q 218-258 ps, 954 fJ (903 with the controller driving the wordlines
directly: the slices add their NAND and inverter switching, and both stacks'
strips decode every access).
Back to back: FAIL on the write-enable hazard alone, as before (0.71 V in
cycles 4 and 6 on both ports); every read right, all 32 cells intact, no
wordline hazard in either stack.

## With the IO block abutting the array (2026-09-22)

Spaced program PASS, 16/16 reads, all 32 cells, no hazards; clk to
wordline 113 / 148, to sense enable 189 / 224, to Q 218-258 ps, 959 fJ:
the same macro electrically, which is what removing 144 nm of strap from
each bitline should say.

## With the floorplan tightened (2026-09-22)

Same testbench on the 4.65 x 17.93 um macro: spaced program PASS, 16/16
reads, all 32 cells, no hazards; clk to wordline 113 / 148 ps, to Q 218-258
ps, 953 fJ. Shorter top-level wires change nothing a schematic can see.

## Write enable fixed: back to back passes (2026-09-22)

`sram_control.v` now drives `wrena`/`wrenan` from the delayed clock
(`wl_any_fire && write_req`) instead of `clk`, so on the edge after a write
the state has settled before the delayed edge arrives and write enable
cannot pulse. Both programs PASS on `sram_x4x2x1`, no hazards, all 32 cells:
spaced clk to wordline 103 / 172 ps, to Q 212-285 ps, 1031 fJ; back to back
103 ps and 212-217 ps, 1012 fJ. Write enable now opens with the wordline,
about 100 ps into the cycle instead of at the edge; the writes still land.
(The wordline times move with each controller place-and-route; this run's
after-idle case is slower than the last one's 148 ps.) The slow test
asserts both passes.

## x8x8 (2026-09-23)

256 cells, both programs PASS at TT 0.7 V, 2 ns: every read right and proven,
all 256 cells intact, no hazards. clk to wordline 114 / 182 ps, to Q 228-305
ps spaced, 228-233 back to back; 1934 / 1993 fJ. Xyce takes about 80 min
per program at this size. The testbench now finds each column group's data
bit by following its `DA` pin to the top-level `D_A` (it had assumed one bit
per datapath half, true only for 2-bit macros).

## Dummy rows only at each stack's ends (2026-09-23)

x4x2: both programs pass (the slow test). x8x8 on the 33.22 um floorplan:
spaced program PASS, all 256 cells, no hazards, clk to wordline 114 / 182 ps,
to Q 228-305 ps, 1934 fJ: the same numbers as before, which is what a
schematic says about a floorplan change.

## Several banks (2026-09-24)

`simulate_macro.py` takes the bank from the address bits above the half bit
(`Geometry.banks`, `bank_shift`), places cells by bank, adds cycles that write
and read the upper bank (and both banks on the same row at once), and checks
write and sense enable per bank. `sram_x4x2x2`: both programs PASS, all 64
cells, no hazards, clk to Q 206-279 ps. Two of the four upper-bank read bits
are proven (Q moved after sense enable); the others returned the word the
port had just read.

With `--share-port-b` (`physical.json` `shared_port_b`), port B's sense and
write enables are watched per pair of banks (`Geometry.io`). `sram_x4x2x2`
shared: both programs PASS, all 64 cells, no hazards, clk to Q 206-289 ps,
1592 / 1651 fJ. A port-B read of bank 1 (address 20) moves both bits after
sense enable, so the block's second group reads through the shared amplifier.
