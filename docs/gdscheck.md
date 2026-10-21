# gdscheck DRC

OpenFinRAM uses [gdscheck](https://github.com/aesc-silicon/gdscheck) for macro
DRC (its ASAP7 main suite, the default in `scripts/verify_macro.py`) and for the
calibrated device DRC regression. It runs as an external executable. The
dependency is pinned to revision `592c8a79e236575039496c3b9897a6b374603d20`
of [jeffhsu3/gdscheck](https://github.com/jeffhsu3/gdscheck), branch `asap7`
(reports version `0.2.0`), in `scripts/install_gdscheck.sh`.

## Install and run

A Rust toolchain supporting edition 2024 is required to build the dependency;
the integration was tested with Rust 1.93.0. The library scanner also needs
Python `gdstk`, already used by the layout generators.

```bash
bash scripts/install_gdscheck.sh
# Or, after configuring CMake:
cmake --build build --target install_gdscheck

bash tests/run_8t_device_drc_check.sh
```

The installer writes to `build/tools/gdscheck`, without changing a global
installation. The adapter checks `GDSCHECK` first, then that local binary,
then `PATH`. `GDSCHECK` is an executable path, not a shell command. Set
`PYTHON` to select an interpreter; the gate otherwise uses `.venv/bin/python`
when present. A missing binary or Python `gdstk` skips the CTest gate with
exit 77; an execution error or incomplete check fails it.

The adapter requires the native ASAP7 directional-rule support in this revision;
an executable whose native `gcut` deck lacks `facing: y` is rejected. To use the sibling checkout without installing:

```bash
GDSCHECK=../gdscheck/target/release/gdscheck bash tests/run_8t_device_drc_check.sh
```

Check a specific layout and top cell:

```bash
.venv/bin/python scripts/drc.py tech/gds/sram_cell_8t.gds \
  --cell sram_cell_8t --out tmp/bitcell_drc
```

This writes `drc.lyrdb` (KLayout Marker Browser), `drc.log`, and `drc.json`.
Exit 0 means clean, 2 means violations. Skipped rules, waived markers,
malformed reports, missing reports, and tool errors cannot report clean.
Each subprocess uses one thread, 1 µm tiles and a 512 MB memory budget;
the library gate runs two subprocesses at a time (`--jobs` overrides).

## Coverage

`tech/drc/asap7/pdk.yml` maps ASAP7 layers; `device.json` is a YAML-compatible
JSON rule deck, in micrometres. The thresholds and geometry semantics match
`tech/drc/asap7_device.drc`: FIN/GATE/GCUT/ACTIVE/LIG/LISD/M1–M5 widths and
spacing, and V0–V5 widths. Each spacing rule includes `min_space` and
`min_notch`, because KLayout's `Region.space_check` also reads notches within
one merged region. Both checks use the same rule ID.

Profile `asap7-device-v2` corrects GCUT to the native ASAP7 `GCUT.W.1` and
`GCUT.S.3` semantics: **17 nm vertical width and 35 nm vertically projected
spacing**, using `params: {facing: y}`. The KLayout reference uses projection
and horizontal-edge filtering. The old 37 nm all-direction spacing check
incorrectly flagged staggered cuts on adjacent gates. The obsolete bitcell/array baseline entries
were removed. Assembled column groups still have four true 10 nm vertical
gaps (eight for paired columns); these were retained at the corrected limit.
Every non-GCUT baseline count and distance was preserved.
This subset still does not check gate-cut enclosure or channel clearance;
the native `--process asap7 --deck gcut` checks those too.

This is a calibrated subset, **not the full ASAP7 runset or a signoff claim**.
It does not add enclosure, implant, density, antenna or upper-metal rules.
Macro verification instead runs gdscheck's full ASAP7 main suite by default;
`--drc-engine klayout` selects the public KLayout runset and
`--drc-engine gdscheck-device` this subset. KLayout remains the LVS engine.

```bash
.venv/bin/python scripts/verify_macro.py results/<macro> \
  --drc-engine gdscheck-device --out tmp/macro_device_drc
```

The main suite exempts every shape that overlaps the `SRAMDRC` marker, which
the bitcells carry, so it does not check inside the arrays. The device
subset and the bitcell tests (`tests/tools/test_8t_edges.py` removes the
marker from its copy) cover them.

Macro baselines from the two profiles are deliberately incompatible.

## Regression and reference checks

The gate checks every cell, including recursively instantiated geometry, in
four generated libraries, and requires five published ASAP7 SRAM calibration
cells to be clean. Reports stay under `tmp/device_drc`. The gdscheck baseline
is `tests/golden/asap7_8t_drc_gdscheck.json`; the original KLayout baseline is
preserved separately. The gate rejects new cell/rule findings, larger counts,
smaller worst distances, and changed rule limits. Version and deck changes
require an explicit baseline update. Executable hashes are recorded for
auditing, not compared across platforms/builds.

```bash
# Same thresholds, both engines; compare coverage and minimum distances.
bash tests/run_8t_device_drc_check.sh --cross-check

# Original KLayout gate and its original baseline:
DRC_ENGINE=klayout bash tests/run_8t_device_drc_check.sh

# Deliberate update after reviewing changes:
WRITE_BASELINE=1 bash tests/run_8t_device_drc_check.sh --cross-check
```

The original v1 migration cross-check found the same **121 cell/rule combinations** and
identical worst distances, with all five calibration cells clean in both
engines. There were **33 count differences**, on M1/LIG spacing. These are
engine-specific marker counts, not layout fixes; the old baseline must not
be reused. Cross-checking fails on different cell/rule coverage or distances
beyond the report's 0.1 nm quantization (half-step tolerance). Count differences
are retained in `cross_check.json`. This checks aggregate findings, not exact
marker geometry equivalence.

Adapter tests use generated layouts for exact rule boundaries, sub-threshold
width/spacing, notches, and rotated/reflected arrays, plus failure-path tests.
The GCUT regression checks 35 nm passes and 34.75 nm fails, 17 nm vertical
width passes and 16.75 nm fails, legal 10 nm staggered cuts pass, and rotation
changes the measured axis. It cross-checks every case with KLayout.

```bash
.venv/bin/python -m unittest discover -s tests -p test_drc.py
ctest --test-dir build -R 'gdscheck_adapter_check|asap7_8t_device_drc_check' --output-on-failure
```

The dependency is AGPL-3.0-or-later; its source and license remain upstream.
The installer pins the source revision; transitive Rust crate resolution is
not locked by the install command.

## Where it runs

- CTest `asap7_8t_device_drc_check` calls `tests/run_8t_device_drc_check.sh`,
  then `scripts/check_device_drc.py`, then this adapter for every cell in the
  four generated libraries. It rejects regressions against the saved baseline;
  passing does not mean zero violations.
- `scripts/drc.py <gds> --cell <name> --out <directory>` checks one layout.
- `scripts/verify_macro.py` runs the ASAP7 main suite by default;
  `--drc-engine gdscheck-device` selects this subset and `--drc-engine klayout`
  the public KLayout runset.
- Macro compilation checks connectivity and OpenROAD routing DRC. It does not
  run this device check automatically; the physical report records that fact.
