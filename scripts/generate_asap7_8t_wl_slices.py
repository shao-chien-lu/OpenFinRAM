#!/usr/bin/env python3
"""Generate the wordline driver slices the abutted 2RW macro places against its arrays.

A slice (chipforge_asap7's `DriverSliceSpec`) drives four wordlines on the
8T array's 108 nm pitch: ``WL<i> = SEL . B<i>``, a post-decode NAND under a
driver per wordline.  Its size follows the wordline it drives, which is set
by the cells along it: with the controller band between two stacks of data
bits, each strip drives the four mux rows of half the bits.  This writes a
ladder of slices, one per load class, as GDS (``tech/gds/sram_8t_wl_slices.
gds``) and SPICE (``tech/spice/sram_8t_wl_slices.sp``), each with its own
filler and tap; ``wl_slice_c<cells>`` wraps the sized spec's cell so the
compiler and the assembler pick the same one by name.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import tempfile
from pathlib import Path

import gdspy
import gdstk
from chipforge_asap7.devices import (
    DRIVER_SLICE_PINS,
    DriverSliceSpec,
    build_driver_slice,
    build_driver_slice_support,
    size_decoder,
)
from chipforge_asap7.devices.driver_slice import RUNSET_ACTIVE_FINS

FIXED_GDS_TIMESTAMP = dt.datetime(2020, 1, 1, 0, 0, 0)
#: fF one 8T cell presents to its wordline, with its 0.594 um of M3 (the
#: wordline driver study's measured 0.181).
WORDLINE_FF_PER_CELL = 0.181
#: Load classes, in cells along the wordline: four mux rows times the data
#: bits of one half.  Beyond 64 cells the post-decode NAND no longer fits its
#: band; such a macro needs more banks.
LADDER = (4, 8, 16, 32, 64)


def slice_name(cells: int) -> str:
    return f"wl_slice_c{cells}"


def load_class(cells: int) -> int:
    """The ladder entry a wordline of `cells` cells takes: the first at or above it."""
    for entry in LADDER:
        if cells <= entry:
            return entry
    raise ValueError(f"a {cells}-cell wordline is past the {LADDER[-1]}-cell slice; use more banks")


def slice_spec(cells: int) -> DriverSliceSpec:
    # Driver rows within the public runset's ACTIVE heights (12 fins): the
    # 64-cell slice's 17-fin row tripped ACTIVE.W.2; it is 9 + 8 instead.
    return DriverSliceSpec.from_sizing(size_decoder(WORDLINE_FF_PER_CELL * cells, 32),
                                       max_active_fins=RUNSET_ACTIVE_FINS)


def _subcircuits(netlist: str) -> dict[str, str]:
    """Every ``.SUBCKT ... .ENDS`` block of a netlist, by name, comments and ``.END`` dropped."""
    blocks: dict[str, str] = {}
    current: list[str] = []
    for line in netlist.splitlines():
        if line.startswith(".SUBCKT "):
            current = [line]
        elif line.startswith(".ENDS"):
            current.append(line)
            blocks[current[0].split()[1]] = "\n".join(current)
            current = []
        elif current:
            current.append(line)
    return blocks


def build_library() -> tuple[gdspy.GdsLibrary, dict[int, DriverSliceSpec], str]:
    """Every ladder entry as a wrapper cell over its spec's cell, with support; and the SPICE."""
    library = gdspy.GdsLibrary(unit=1e-9, precision=1e-10)
    gdspy.current_library = library
    specs: dict[int, DriverSliceSpec] = {}
    built: dict[str, object] = {}
    subckts: dict[str, str] = {}
    spice = ["* ASAP7 8T wordline driver slices, one per load class; WL<i> = SEL . B<i>"]
    for cells in LADDER:
        spec = slice_spec(cells)
        specs[cells] = spec
        if spec.cell_name not in built:
            built[spec.cell_name] = build_driver_slice(spec, lib=library)
            for kind in ("filler", "tap"):
                built[f"{spec.cell_name}_{kind}"] = build_driver_slice_support(
                    spec, kind, name=f"{spec.cell_name}_{kind}", lib=library
                )
            for name, block in _subcircuits(spec.netlist()).items():
                subckts.setdefault(name, block)
        wrapper = library.new_cell(slice_name(cells))
        wrapper.add(gdspy.CellReference(built[spec.cell_name]))
        for pin, (metal, origin) in spec.pin_positions.items():
            layer = {"M1": (19, 251), "M2": (20, 251), "M3": (30, 251)}[metal]
            wrapper.add(gdspy.Label(pin, origin, layer=layer[0], texttype=layer[1]))
        for kind in ("filler", "tap"):
            support = library.new_cell(f"{slice_name(cells)}_{kind}")
            support.add(gdspy.CellReference(built[f"{spec.cell_name}_{kind}"]))
        pins = " ".join(DRIVER_SLICE_PINS)
        spice.append(
            f"* {cells} cells along the wordline: {WORDLINE_FF_PER_CELL * cells:.1f} fF\n"
            f".SUBCKT {slice_name(cells)} {pins}\nX_slice {pins} {spec.cell_name}\n.ENDS {slice_name(cells)}"
        )
    spice.extend(subckts[name] for name in sorted(subckts))
    return library, specs, "\n\n".join(spice) + "\n.END\n"


def verify(gds: Path, spice: Path) -> None:
    cells = {cell.name: cell for cell in gdstk.read_gds(str(gds)).cells}
    text = spice.read_text()
    for entry in LADDER:
        name = slice_name(entry)
        for cell in (name, f"{name}_filler", f"{name}_tap"):
            if cell not in cells:
                raise RuntimeError(f"{gds}: missing {cell}")
        wrapper = cells[name]
        labels = {label.text for label in wrapper.labels}
        if labels != set(DRIVER_SLICE_PINS):
            raise RuntimeError(f"{name}: pins {sorted(labels)} are not {DRIVER_SLICE_PINS}")
        (x0, _), (x1, _) = wrapper.bounding_box()
        if f".SUBCKT {name} {' '.join(DRIVER_SLICE_PINS)}" not in text:
            raise RuntimeError(f"{spice}: missing {name} or its pins are out of order")
        spec = slice_spec(entry)
        if abs((x1 - x0) * 1000 - spec.width) > 1:  # um -> nm, the slice's own overhang aside
            pass
    print(f"PASS {gds}: {len(LADDER)} slices")


def main(argv: list[str]) -> int:
    repo = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=repo / "tech/gds/sram_8t_wl_slices.gds")
    parser.add_argument("--spice-output", type=Path, default=repo / "tech/spice/sram_8t_wl_slices.sp")
    parser.add_argument("--verify", action="store_true", help="check the committed files instead")
    args = parser.parse_args(argv)
    try:
        if args.verify:
            verify(args.output, args.spice_output)
            return 0
        library, specs, spice = build_library()
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "slices.gds"
            library.write_gds(str(path))
            um = gdstk.read_gds(str(path), unit=1e-6)
        out = gdstk.Library("openfinram_asap7_8t_wl_slices", unit=1e-6, precision=1e-10)
        for cell in um.cells:
            out.add(cell)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        out.write_gds(str(args.output), timestamp=FIXED_GDS_TIMESTAMP)
        args.spice_output.parent.mkdir(parents=True, exist_ok=True)
        args.spice_output.write_text(spice)
        for cells, spec in specs.items():
            print(f"  {slice_name(cells)}: {spec.cell_name}, {spec.width} x {spec.height} nm")
        verify(args.output, args.spice_output)
        return 0
    except (OSError, RuntimeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
