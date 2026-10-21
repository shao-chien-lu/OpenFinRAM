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
- The commercial flow remains the default when `--openroad` is omitted; dual-port falls back to it.
- The build hard-fails on missing timing paths, setup/hold, or slew/capacitance/fanout violations.
- The routed controller's SPICE comes from `scripts/mapped_verilog_to_spice.py`, which
  reads the mapped Verilog with Yosys and orders each cell's pins from the CDL, so no
  licensed `v2lvs` is needed. Unknown cells or a failed conversion stop the build; there
  is no placeholder netlist.
- STA uses routed global parasitics at TT only — a conservative implementation target, not a characterized frequency claim. Use `scripts/characterize_read.py` for the array datapath.

## 8T cell libraries

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
- `tests/run_wordline_array_check.sh`: deterministic parameterized 8T/6T
  wordline-row and array hierarchy, including a non-standard x18/mux-2 case.
- `tests/run_8t_iocolumn_check.sh`: deterministic dual-port IO-column routing,
  capped array abutment, internal read-port ties, and a non-standard x18 case.
- `python3 tests/check_macro_pins.py macro.gds macro.lef`: checks that a
  macro's GDS pin labels match its LEF pins and sit on metal of their own
  layer, including routed M4/M5 control and address pins. It is a geometry
  check, not connectivity LVS.

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
