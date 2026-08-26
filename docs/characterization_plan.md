# Timing characterization plan (open-source, no SiliconSmart)

## Goal

Replace the estimated Liberty that `--skip-characterization` writes today —
flat 2x2 tables, `kClockToQ = 0.218 ns` regardless of geometry, no power, one
PVT point — with **measured** timing and power that vary with the macro's
shape and corner, produced entirely with tools already installed: Xyce 7.10
(BSIM-CMG), magic, ngspice, KLayout, netgen.

Success is not "a .lib exists". Success is: an STA run on a chip that
instantiates these macros gets numbers it can act on, and the numbers survive
comparison with the two references already in this repo (the vendor
`srambank` Liberty and the MOST-report Table III columns).

## Why the current model is not enough

`src/liberty_estimator.cpp` emits one constant for every clk->Q arc:

```
constexpr double kClockToQ = 0.218;   // ns
constexpr double kMinPeriod = 0.157;  // ns
...
cell_leakage_power : 0;
```

`scripts/characterize_read.py` already disproves the constant: real ASAP7 TT
clk->Q measured through the actual sense amp runs **132 ps -> 386 ps over
16 -> 256 cells per bitline (2.93x)**, and the WL->BL access component alone
runs 12 ps -> 140 ps (11.9x). A flat 218 ps is optimistic for deep columns and
pessimistic for shallow ones — the error has *both* signs, so it cannot be
absorbed by a global margin.

Consumers make this concrete. ChipForge registers the same estimated Liberty
for all three corners (`{c: macro.lib for c in ("typical", "fast", "slow")}`)
because there is only one; real multicorner STA on a design containing these
macros is impossible until this plan lands.

## What already exists (start here, do not rebuild)

| Asset | What it does | State |
| --- | --- | --- |
| `scripts/characterize_read.py --mode access` | WL -> BL to sense margin, real 6T cell | works, TT only, lumped Cbl |
| `... --mode clkq` | full read through the real `sense_amp_sram` + `io_nand` + `TBUF_INV`, replica-timed SAE | works, 1-D (depth), TT only |
| `... --mode setuphold` | setup/hold on the real ASAP7 `DFFHQNx1` by bisection | works, but measures the **capture-failure boundary**, not the Liberty delay-pushout definition |
| `... --mode table3` | reproduces the report's stimulus point (40 ps slew, 46.08 fF load) | works |
| `scripts/gen_table3_report.py` | assembles vendor lib vs report vs estimated vs measured | works — this is the validation harness |
| Xyce recipe | HSPICE `level 72` -> Xyce `level 110`, emit `NFIN` not `W` | proven |
| `tech/models/hspice/7nm_{TT,SS,FF}.pm` | all three corners | present, only TT used |

Gaps, in order of how much error each contributes:

1. **No extracted parasitics.** `CBL_PER_CELL_F = 0.06e-15` is a guess, and the
   wordline is ideal. Bitline RC dominates the read path.
2. **Tables are 1-D.** Liberty needs delay vs (input slew x output load); the
   harness measures one stimulus point.
3. **No power at all.** `cell_leakage_power : 0` and no `internal_power()`.
4. **One corner.** SS/FF cards sit unused.
5. **Setup/hold uses the wrong definition** and an isolated flop, not the
   macro's own input register with its real clock arrival.
6. **Idealized replica SAE.** The characterizer self-times SAE from its own
   access measurement rather than through `ctrl_decode`'s `sdel` chain — which
   is the thing that actually fires SAE in silicon.

## Phases

### P0 — Make the emitter data-driven (prerequisite, ~half a day)

Nothing measured can ship until there is a place to put it.

- Define `characterization.json`: per cell, per corner — a list of arcs, each
  with `template`, `index_1`, `index_2`, `values`, plus scalars
  (`min_period`, pin capacitances, leakage).
- `liberty_estimator.cpp` gains `--liberty-from <json>`: same emitter, same
  Liberty structure, values read from the file instead of the constants. When
  the file is absent the current estimated constants stay (so
  `--skip-characterization` keeps working unchanged), and the `comment` field
  flips to say which mode produced the file.
- Keep the units honest: Liberty header stays ns/pF and the writer converts
  from the ps/fF the SPICE decks measure in. (The vendor `srambank` lib gets
  this wrong — declares ns/pF, stores ps/fF — and it has cost this project a
  debugging cycle already. Do not repeat it; assert on the range.)

**Gate:** regenerate today's estimated numbers *through* the JSON path and get
a byte-identical .lib.

### P1 — Extract real parasitics (~2 days, biggest single error source)

- `magic` `ext2spice` on the generated GDS for: one column group (BL/BLN with
  N cells), one wordline segment across the array width, and the SAE/replica
  path. Emit per-cell R and C, not a lumped number.
- OpenRCX (`define_process_corner` / `extract_parasitics` — OpenROAD is already
  in the flow) for the routed `ctrl_decode` periphery, where a DEF exists.
- Replace `CBL_PER_CELL_F` and the ideal WL in the decks with the extracted
  values; re-run `--mode access` and record the delta.

**Gate:** report the extracted Cbl/cell against the 0.06 fF guess. If it moves
more than ~2x, every number downstream of it changes and P2's grid should be
re-planned around the new access time.

### P2 — The arcs, as 2-D tables (~1 week)

Sweep grid: 4 input slews x 4 output loads (extend later if the fit is poor).
Anchor the grid on ASAP7 std-cell convention and *include the report's
stimulus point* (40 ps, 46.08 fF) exactly, so Table III stays comparable
without interpolation.

- **clk -> Q** (`cell_rise`, `cell_fall`, `rise_transition`, `fall_transition`):
  extend `--mode clkq` to sweep (slew, load). This is the arc that matters most.
- **Setup/hold** for `A`, `D`, `ce_n`, `we_n` against `clk`: switch from the
  capture-failure boundary to the Liberty definition — the input-to-clock
  separation at which clk->Q degrades by 10% — and drive the macro's own input
  register, not a bare flop. 2-D (related-pin slew x constrained-pin slew).
- **`min_period`** from the replica loop; **write recovery/removal** if the
  write path needs it.
- **Pin capacitance** per pin by transient charge integration, replacing the
  flat `default_input_pin_cap : 0.005`.

**Gate:** monotonicity (delay rises with load and with slew) and the Table III
comparison below.

### P3 — Power (~3 days)

- **Dynamic:** energy per read and per write = integral of I(VDD) over the
  access, minus output-load charging, at each grid point ->
  `internal_power()` groups on `Q` (related_pin `clk`) and on the write path.
- **Leakage:** steady-state I(VDD) for deselected / all-zeros / all-ones ->
  `leakage_power()` groups. Anything is better than the current `0`, which
  silently tells a chip-level power estimate that a 256 Kbit array is free.

### P4 — PVT corners (~2 days, mostly runtime)

Run P2+P3 at three corners matching the ASAP7 filesets the consumers expect:

| Fileset | Card | V | T |
| --- | --- | --- | --- |
| `slow` | `7nm_SS.pm` | 0.63 | 125 C |
| `typical` | `7nm_TT.pm` | 0.70 | 25 C |
| `fast` | `7nm_FF.pm` | 0.77 | -40 C |

Emit `<cell>.{slow,typical,fast}.lib`. **This is the phase that unblocks real
multicorner STA downstream** — the consumer stops aliasing one lib onto three
corners.

### P5 — Integration and caching (~3 days)

- `--characterize` on the compiler: run the measurement, or reuse a cached
  `characterization.json` keyed by (geometry, corner, tool versions).
- Geometry interpolation: full SPICE for a ladder of shapes (e.g. BL = 32, 64,
  128, 256, 512 cells), fit delay vs BL length and column count, interpolate
  for shapes in between — and **report the fit residual in the Liberty
  `comment`** so a consumer can see whether a given macro was measured or
  interpolated.
- Downstream: ChipForge's macro cache gains per-corner libs and stops
  registering one Liberty three times.

### P6 — Validation gates (do not skip; each is pass/fail)

1. **Table III reproduction** — cell rise/fall and transitions at (40 ps,
   46.08 fF) for the 256/512/1024-word instances, against the report's
   SiliconSmart-characterized "ours" column already transcribed in
   `gen_table3_report.py`. Target: within 15%.
2. **Vendor sanity** — same stimulus against `asap7_sram_0p0`'s `srambank`
   Liberty (the report's "PDK" column). Same order, monotone in depth.
3. **Self-consistency** — `min_period >= clk->Q + setup` at every corner;
   SS slower than TT slower than FF for every arc.
4. **OpenSTA smoke** — read each corner's .lib, report a path through the
   macro, zero STA warnings.
5. **Regression** — golden JSON per geometry under `tests/`, wired to `ctest`
   next to `decode_check` and `equiv_check`.

## Cost

Per geometry per corner, roughly: 32 transients for clk->Q, ~10 bisection
sims per constraint arc (5 pins x setup+hold x 4 slew pairs), ~10 for power.
Order 500 Xyce runs per geometry per corner, each seconds on a column-level
deck — tens of minutes wall clock with parallelism, x3 corners. That is why
P5's caching and interpolation are part of the plan and not an afterthought.

## Traps that will bite

- **SAE must track bitline develop.** A fixed SAE that is early at a deep
  column makes reads *fail*, not merely slow. Characterize through the real
  `sdel` path, or pin the tap and say which tap the Liberty describes.
- **`sdel` is programmable** — a Liberty describes one tie-off. Consumers tie
  it to tap 0; characterize that tap, and emit per-tap libs only if a consumer
  asks.
- **BSIM-CMG syntax:** `level 72` -> `110`, `NFIN` not `W`, strip `W` from the
  6T subckts.
- **Statistical margin is out of scope.** 6T read/write stability is a
  distribution; Vmin and yield need Monte Carlo. This plan produces nominal
  corner timing, which is what STA consumes — say so in the Liberty comment
  rather than implying more.

## Order of work

P0 -> P1 -> P2 -> P6.1/P6.2 (validate the read arc before building on it) ->
P4 -> P3 -> P5 -> P6.3-5.

The early Table III gate is deliberate: if the measured clk->Q cannot land
near a SiliconSmart-characterized reference at one stimulus point, nothing is
gained by spending a week filling in tables around it.
