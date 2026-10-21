# 8T schematic read-timing Liberty

`scripts/characterize_8t.py` characterizes an existing **8T** macro's read
path through its actual synthesized controller, decoder, array, sense
amplifiers and output circuitry. It sweeps clock slew and output load in
Xyce, then calls the C++ Liberty emitter without repeating synthesis or P&R.
It rejects 6T results, including directories with the same macro dimensions.

This is partial characterization for early PPA. It does not establish
signoff timing or physical correctness. Use gdscheck as the DRC authority;
LVS and DRC remain separate checks on the macro being characterized.

## Run

Build `OpenFinRAM` first, then select a result directory explicitly:

```sh
cmake --build build --target OpenFinRAM -j4
.venv/bin/python scripts/characterize_8t.py \
  results/sram_x4x2x1_<8t_timestamp> \
  --out tmp/char_8t_tt \
  --slews 0.01,0.04 --loads 0.0005,0.002 --jobs 2
```

The output directory must be new. The example runs a 2x2 grid: 10/40 ps
**20–80% clock slew**, with 0.5/2 fF on every output. CLI and JSON units are
ns and pF; the SPICE deck converts them to seconds and farads. Input data
and controls retain the functional testbench's fixed edge rate. The clock's
full-swing ramp is slew/0.6, keeping its 50% crossing at the intended edge.
Output delays use 50% thresholds and transitions use 20–80% thresholds.

Defaults are TT, 0.7 V, 25 °C, a 2 ns period and a 3x3 grid. Use
`--corner SS --vdd 0.63 --temperature 125` for a separate slow-corner run;
no timing is scaled from TT. `--timeout` limits each Xyce process; `--jobs`
controls concurrent simulations. Whole-macro transient analysis can take
minutes per point even for the smallest macro.

Each point runs the existing read/write program: both ports, both address
halves, first/last mux columns, same-address reads and cross-port writes
and reads. It checks read values, every bitcell's final state, write-enable
and wordline hazards, and that every output bit moves in both directions
after sense enable. Failed checks or missing measurements prevent export.
This program samples address, data and concurrency cases; it is not an
exhaustive worst-case search.

Outputs include:

- `<cell>.timing.json`: separate A/B delay and transition tables, operating
  conditions, source SHA-256 hashes, Xyce version and per-point results.
- `<cell>.lib`: Liberty with the measured read tables and explicit partial
  characterization comments.
- `slew_<i>_load_<j>/`: deck, converted netlist/model, waveforms, Xyce log and
  functional/timing verdict. These remain available if a point fails.
- `liberty.log`: exporter diagnostics.

At each grid point, a bus uses the worst observed bit/pattern separately
for rising/falling delay and rising/falling transition. Port B never reuses
port A's measurements. Maximum output capacitance and clock transition
limits are bounded by the grid. Consumers should stay within the measured
grid; extrapolation is not validated.

## Regenerate Liberty without simulation or P&R

```sh
build/OpenFinRAM \
  --liberty-only results/sram_x4x2x1_<8t_timestamp>/sram_x4x2x1.lef \
  --liberty-from tmp/char_8t_tt/sram_x4x2x1.timing.json \
  --liberty-output tmp/char_8t_tt/regenerated.lib \
  --num-wls 2 --num-data-bits 2 --num-banks 1 --num-rows-per-mux 4
```

Supply the original geometry. The emitter checks the LEF macro name and
pin interface, plus the JSON cell name when present. Invalid explicitly
requested JSON is an error, including in a full compiler run. Omitting
`--liberty-from` deliberately produces the historical estimated model.

The version-2 extension is `timing.port_delays`, containing exactly `A`
and `B`. Each port has positive, strictly increasing `index_1` (clock slew)
and `index_2` (load) axes, plus `cell_rise`, `cell_fall`,
`rise_transition`, and `fall_transition` matrices. Rows index slew and
columns index load. Legacy version-1 common delay tables and one-dimensional
transition vectors remain supported; their estimated passthrough fixture
still reproduces the original Liberty byte for byte.

## Remaining characterization

Setup/hold, reset recovery/removal and input capacitances still use the
emitter's estimated defaults. Leakage and internal power are uncharacterized.
The emitted `min_period` is conservatively set to the period at which all
grid points passed; it is **not** a measured minimum period or frequency limit.
The schematic contains no extracted interconnect. These limitations are
also recorded in the generated Liberty comment.

Subsequent work needs macro-level setup/hold pushout searches, minimum
period and pulse-width searches, pin capacitances, operation-dependent
power, broader pattern/concurrency coverage, additional PVT runs, and
extracted-netlist correlation. Read characterization should be rerun for
every changed macro/netlist and corner.

## Initial validation (2026-10-19)

The existing 8T `sram_x4x2x1_20261010_061929` result (16 words x 2 bits)
passed all four points at TT, 0.7 V, 25 °C and a 2 ns period. All 32 stored
bits passed the final-state check at each point. The maximum observed
clock-to-Q delay over rising/falling transitions and sampled patterns was:

| Clock slew (ps) | Load per Q (fF) | Port A (ps) | Port B (ps) |
| ---: | ---: | ---: | ---: |
| 10 | 0.5 | 251.1 | 250.1 |
| 10 | 2.0 | 255.8 | 254.7 |
| 40 | 0.5 | 260.3 | 259.6 |
| 40 | 2.0 | 265.0 | 264.2 |

OpenSTA loaded the generated Liberty without warnings and reproduced all
16 port/polarity/grid delay and transition pairs from the characterization
JSON. A separate regression checks interpolation between grid points.
These results validate this schematic read-model path; the uncharacterized
items above remain outstanding.
