#!/usr/bin/env python3
"""Generate the ASAP7 single-port 6T column tile: the released 6T array, one IO at one end.

One data bit's column, as the released bank lays a row out, with the
parametric IO in place of the released core::

    edge filler | cap | N bitcells ... | dummy | tap | IO block

The bitcell, its dummy, tap, column cap and edge filler are the published
academic 6T cells (``tech/gds/srambank_32b_boundary_2.gds``).  The IO is
chipforge_asap7's `SidewaysIoColumnSpec`: one column of 270 nm bitline leaves
(the released leaf's side-by-side arrangement, odd rows' leaves swapped), one
beside each array row, then the sense amplifier, write driver and output
latch.  Its bitlines enter on the block's left edge at the bitcell's own
heights; neither the dummy nor the tap carries a
bitline, so M2 straps cross them from the dummy's stubs into the block, as the
released core's overhang did.

Pin names follow the 8T tiles' port A (``WLA[i]``, ``DA``, ``yselA[i]``,
``sae_A``, ...), so the macro assembler maps them the same way.  The IO
block's netlist is written beside the GDS (``tech/spice/sram_6t_iocolumn.sp``)
for ``SpiceGenerator``.
"""

from __future__ import annotations

import argparse
import math
import re
import sys
import tempfile
from pathlib import Path

import gdspy
import gdstk
from chipforge_asap7.devices import (
    SidewaysIoColumnSpec,
    build_sideways_io_column,
    io_column_pins,
    sideways_block_netlist,
)

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "tech/gds/srambank_32b_boundary_2.gds"
BOUNDARY, M1, M2, M3, M4, M5 = 100, 19, 20, 30, 40, 50
#: The SRAM array's marker: the design rules for SRAM apply under it.
SRAMDRC = 99
V3, V4 = 35, 45
GCUT = 10
PIN_TEXTTYPE = 251
MUX_ROWS = 4
#: Column mux ratios a tile is built for: the edge filler covers four rows.
MUXES = (4, 8, 16)
BITCELL, DUMMY, TAP = "sram_cell_6t_122", "dummy_sram_6t122", "tapcell_sram_6t122"
CAPS = ("dummy_topbot_v1", "dummy_topbot_v2")  # even rows, odd rows (drawn mirrored)
EDGE_FILLER = "FILLER_cgedge"
#: The block's pins, by the IO column's name (the 8T tiles' port A).
BLOCK_PIN = {
    "PRECHN": "blprechnA", "SAE": "sae_A", "D": "DA", "Q": "QA",
    "WRENA": "wrenaA", "WRENAN": "wrenanA", "OE": "oe_outA", "OEB": "oeb_outA",
}
IO_NAME = "iocol_sram_6t"


def io_name(mux: int = MUX_ROWS) -> str:
    """The IO column's cell and subcircuit: ``iocol_sram_6t`` at 4:1, ``iocol_sram_6t_x8`` ..."""
    return IO_NAME if mux == MUX_ROWS else f"{IO_NAME}_x{mux}"


def boundary_box(cell: gdstk.Cell) -> tuple[float, float, float, float]:
    boxes = [p.bounding_box() for p in cell.polygons if p.layer == BOUNDARY and p.datatype == 0]
    if len(boxes) != 1:
        raise RuntimeError(f"{cell.name}: expected one direct BOUNDARY polygon")
    (x0, y0), (x1, y1) = boxes[0]
    return tuple(round(float(v), 7) for v in (x0, y0, x1, y1))


def rect(cell: gdstk.Cell, box: tuple[float, float, float, float], layer: int) -> None:
    x0, y0, x1, y1 = box
    cell.add(gdstk.rectangle((x0, y0), (x1, y1), layer=layer))


def label(cell: gdstk.Cell, text: str, point: tuple[float, float], layer: int) -> None:
    cell.add(gdstk.Label(text, point, layer=layer, texttype=PIN_TEXTTYPE))


def load_source() -> dict[str, gdstk.Cell]:
    return {cell.name: cell for cell in gdstk.read_gds(str(SOURCE)).cells}


def add_with_dependencies(library: gdstk.Library, cell: gdstk.Cell) -> None:
    have = {c.name for c in library.cells}
    for dep in (cell, *cell.dependencies(True)):
        if dep.name not in have:
            library.add(dep)
            have.add(dep.name)


def io_spec(bitcell: gdstk.Cell, selects: int = MUX_ROWS) -> SidewaysIoColumnSpec:
    """The block for this bitcell: its row pitch and its bitlines' heights (bar centres)."""
    x0, y0, x1, y1 = boundary_box(bitcell)
    heights = {}
    for net in ("BL", "BLN"):
        found = next(l for l in bitcell.labels if l.text == net and l.layer == M2)
        bars = [p.bounding_box() for p in bitcell.polygons if p.layer == M2
                and p.bounding_box()[0][1] <= found.origin[1] <= p.bounding_box()[1][1]
                and p.bounding_box()[1][0] - p.bounding_box()[0][0] > 0.1]  # fmt: skip
        if len(bars) != 1:
            raise RuntimeError(f"{bitcell.name}: no single M2 bar under its {net} label")
        (_, b0), (_, b1) = bars[0]
        heights[net] = round(1000 * (float(b0) + float(b1)) / 2 - 1000 * y0, 1)
    if round(1000 * (y1 - y0)) != SidewaysIoColumnSpec.row_pitch:
        raise RuntimeError(f"{bitcell.name}: a {round(1000 * (y1 - y0))} nm row; the block's leaves are 270 nm")
    # The compact logic: the one-row write driver under the sense amplifier,
    # the 270 nm output latch beside them at 4:1 (1998 nm against 2862),
    # above them from 8:1.  From 8:1 each leaf also makes its own YSEL from
    # YSELN, which halves the select tracks that set the leaves' width; the
    # block then has no YSEL pins (8:1 1674 nm wide).  From 16:1 each leaf
    # decodes YSELN from two predecoded groups the block takes once (YPA[0..3],
    # YPB[...], on the tile's yselA[0..]): 8 select tracks, 1782 nm.
    return SidewaysIoColumnSpec(selects=selects, bitline_entry=(heights["BL"], heights["BLN"]),
                                compact=True, local_ysel=selects >= 8, predecode=selects >= 16)  # fmt: skip


def iocol_pin_name(pin: str) -> str:
    """The tile's name for a block pin: ``BL[2]`` -> ``BL_A[2]``, ``YSEL[1]`` -> ``yselA[1]``.

    The predecoded groups ride the select bus: ``YPA[k]`` -> ``yselA[k]``,
    ``YPB[k]`` -> ``yselA[4 + k]`` (the controller's YSEL_PREDECODE).
    """
    group = re.fullmatch(r"YP([AB])\[(\d+)\]", pin)
    if group:
        return f"yselA[{int(group[2]) + (4 if group[1] == 'B' else 0)}]"
    match = re.fullmatch(r"(BL|BLN|YSEL|YSELN)\[(\d+)\]", pin)
    if match:
        bus = match.group(1)
        return f"{bus}_A[{match.group(2)}]" if bus.startswith("BL") else f"{bus.lower()}A[{match.group(2)}]"
    if pin in ("VDD", "VSS"):
        return pin
    return BLOCK_PIN[pin]


def build_io_block(spec: SidewaysIoColumnSpec) -> tuple[gdstk.Cell, list[gdstk.Cell], str]:
    """Draw the block with chipforge (nm), read it back in um; and its netlist with the tile's pin names."""
    nm = gdspy.GdsLibrary(unit=1e-9, precision=1e-10)
    gdspy.current_library = nm
    name = build_sideways_io_column(spec, lib=nm, draw_pin_labels=False).name
    with tempfile.TemporaryDirectory() as scratch:
        path = Path(scratch) / "io_block.gds"
        nm.write_gds(str(path))
        um = gdstk.read_gds(str(path), unit=1e-6)
    cells = {cell.name: cell for cell in um.cells}
    block = cells[name]
    pins = io_column_pins(spec)
    name = io_name(spec.selects)
    block_name = name.replace("iocol_sram_6t", "iocol_block_6t")
    netlist = sideways_block_netlist(spec, name=block_name).replace(".END\n", "")
    wrapper_pins = [iocol_pin_name(pin) for pin in pins]
    netlist += (
        f".SUBCKT {name} {' '.join(wrapper_pins)}\n"
        f"X_block {' '.join(wrapper_pins)} {block_name}\n"
        f".ENDS {name}\n.END\n"
    )
    return block, list(block.dependencies(True)), netlist


def build_io(library: gdstk.Library, block: gdstk.Cell, spec: SidewaysIoColumnSpec) -> gdstk.Cell:
    """The tile's IO column: the block, labelled with the tile's names."""
    cell = library.new_cell(io_name(spec.selects))
    cell.add(gdstk.Reference(block))
    layers = {"M1": 19, "M2": M2, "M3": M3, "M4": M4}
    for pin, (metal, (x, y)) in spec.pin_positions.items():
        label(cell, iocol_pin_name(pin), (x / 1000, y / 1000), layers[metal])
    rect(cell, (0.0, 0.0, spec.width / 1000, spec.height / 1000), BOUNDARY)
    return cell


def build_row(library: gdstk.Library, cells: dict[str, gdstk.Cell], wordlines: int) -> gdstk.Cell:
    """One bitline's row: `wordlines` bitcells, alternate ones mirrored, then the dummy and the tap."""
    if (found := existing(library, f"sramcol_x{wordlines}_6t")) is not None:
        return found
    bitcell, dummy, tap = cells[BITCELL], cells[DUMMY], cells[TAP]
    x0, y0, x1, y1 = boundary_box(bitcell)
    width = x1 - x0
    row = library.new_cell(f"sramcol_x{wordlines}_6t")
    for k in range(wordlines):
        if k % 2:
            row.add(gdstk.Reference(bitcell, origin=((k + 1) * width + x0, -y0), rotation=math.pi, x_reflection=True))
        else:
            row.add(gdstk.Reference(bitcell, origin=(k * width - x0, -y0)))
        # On the wordline's M3 track (its centre; the bitcell's own label is off it).
        (tx0, _), (tx1, _) = next(p for p in bitcell.polygons if p.layer == M3).bounding_box()
        track = (float(tx0) + float(tx1)) / 2 - x0
        lx = (k + 1) * width - track if k % 2 else k * width + track
        label(row, f"WL[{k}]", (lx, (y1 - y0) / 2), M3)
    for slot, master in ((wordlines, dummy), (wordlines + 1, tap)):
        mx0, my0, _, _ = boundary_box(master)
        row.add(gdstk.Reference(master, origin=(slot * width - mx0, -my0)))
    rect(row, (0.0, 0.0, (wordlines + 2) * width, y1 - y0), BOUNDARY)
    return row


def build_array(library: gdstk.Library, cells: dict[str, gdstk.Cell], wordlines: int,
                mux: int = MUX_ROWS) -> gdstk.Cell:
    """``cap | rows``: `mux` rows, alternate ones mirrored in y, the cap column at their left (x < 0)."""
    if (found := existing(library, f"array_x{wordlines}x{mux}_6t")) is not None:
        return found
    row = build_row(library, cells, wordlines)
    _, _, row_width, pitch = boundary_box(row)
    array = library.new_cell(f"array_x{wordlines}x{mux}_6t")
    for r in range(mux):
        if r % 2:
            array.add(gdstk.Reference(row, origin=(0.0, (r + 1) * pitch), x_reflection=True))
        else:
            array.add(gdstk.Reference(row, origin=(0.0, r * pitch)))
        cap = cells[CAPS[r % 2]]
        cx0, cy0, cx1, _ = boundary_box(cap)
        # Mirrored in x, its right edge on the array's left one.
        array.add(gdstk.Reference(cap, origin=(cx0, r * pitch - cy0), rotation=math.pi, x_reflection=True))
    for label_ in row.labels:
        label(array, label_.text, tuple(label_.origin), label_.layer)
    rect(array, (0.0, 0.0, row_width, mux * pitch), BOUNDARY)
    return array


#: The released dummy row, as `dummy_vertical_array_X64` draws it below an
#: array: a 283.5 nm row whose cells sit at these origins (its own frame, x
#: from 27 nm left of the first bitcell, y from the row's bottom) for a
#: bitcell slot ``k`` (`DUMMY_ROW` maps the slot's kind to cell, x, rotation).
END_ROW_HEIGHT = 0.2835
#: Past the dummy row to the tile's edge.  The row ends on a fin *space*
#: (fins at 0 mod 27 nm from row 0; 283.5 is 10.5 pitches) and half a
#: nanometre off the DEF's grid; an odd number of half fin pitches more ends
#: the tile on a fin and on the grid, as the 8T tile's edge does, so the
#: driver strips sit the way they sit there (half a fin pitch inside their
#: own edge).  Three, not one: the row's outer rail is VSS and a strip's
#: rail facing it VDD, and at one half pitch each the two stood 27 nm apart
#: centre to centre (LIG, V0 and M1 9-11 nm apart).  The row's wordline
#: stubs are carried across the margin on M3.
END_ROW_MARGIN = 0.0405


def existing(library: gdstk.Library, name: str) -> gdstk.Cell | None:
    return next((c for c in library.cells if c.name == name), None)


def ends_tag(bottom: bool, top: bool) -> str:
    return {(True, True): "", (True, False): "_endb", (False, True): "_endt", (False, False): "_noend"}[(bottom, top)]


def build_end_row(library: gdstk.Library, cells: dict[str, gdstk.Cell], wordlines: int) -> gdstk.Cell:
    """The dummy row below row 0 (``y`` in ``[-END_ROW_HEIGHT, 0]``), in the tile's x from the edge filler.

    Over each bitcell a ``dummy_vertical_6t122`` (even slots flipped in y,
    odd ones turned a half), whose wordline stub continues the column's; a
    ``dummy_corner`` over the dummy, ``tapcell_dummy_6t122`` over the tap and
    ``dummy_corner_v2`` over the cap, as the released row places them; and
    two blank fillers over the edge filler.  Nothing over the IO block.
    """
    if (found := existing(library, f"end_row_x{wordlines}_6t")) is not None:
        return found
    row = library.new_cell(f"end_row_x{wordlines}_6t")
    filler_width = boundary_box(cells[EDGE_FILLER])[2] - boundary_box(cells[EDGE_FILLER])[0]
    cap_width = boundary_box(cells[CAPS[0]])[2] - boundary_box(cells[CAPS[0]])[0]
    array_x = filler_width + cap_width
    dx, dy = array_x - 0.027, -END_ROW_HEIGHT  # the released row's frame in the tile
    y_cells = 0.297  # where the released row puts its flipped cells' origin
    for k in range(wordlines):
        if k % 2:
            row.add(gdstk.Reference(cells["dummy_vertical_6t122"], origin=(dx + 0.27 + 0.108 * (k - 1), dy + y_cells), rotation=math.pi))
        else:
            row.add(gdstk.Reference(cells["dummy_vertical_6t122"], origin=(dx + 0.108 * k, dy + y_cells), x_reflection=True))
    row.add(gdstk.Reference(cells["dummy_corner"], origin=(dx + 0.108 * wordlines, dy + y_cells), x_reflection=True))
    row.add(gdstk.Reference(cells["tapcell_dummy_6t122"], origin=(dx + 0.108 * (wordlines + 1) + 0.027, dy + y_cells), x_reflection=True))
    row.add(gdstk.Reference(cells["dummy_corner_v2"], origin=(dx + 0.027, dy), rotation=math.pi, x_reflection=True))
    # The released row's own shapes between the corner and the tap's dummy
    # (well, implants, a fin column, a gate, rails), drawn there for its 32
    # bitcells: moved to this row's dummy and tap.
    released = cells["dummy_vertical_array_X64"]
    shift = 0.108 * (wordlines - 32)
    for poly in released.polygons:
        (_, _), (px1, _) = poly.bounding_box()
        if px1 <= 3.76 and poly.layer != BOUNDARY:  # the left half's; the M1 rail across the core is not the row's
            moved = poly.copy()
            moved.translate(dx + shift, dy)
            row.add(moved)
    blank = cells["FILLER_BLANK_6t122"]
    for col in range(2):
        row.add(gdstk.Reference(blank, origin=(0.054 * col, dy)))
    # The blank fillers are the array's edge, like the dummies beside them, but
    # carry no marker of their own (their fins are on the array's grid).
    rect(row, (0.0, dy, 0.108, 0.0), SRAMDRC)
    # The row's gate cut at its outer edge and a strip's at its own stand
    # 10 nm apart across the margin (GCUT.S.3 wants 35): one bar across the
    # margin, over the strip's extent (cap column to dummy column), joins them.
    # Its ends fall midway between gate tracks, 17 nm past each gate.
    rect(row, (array_x - 0.108, dy - END_ROW_MARGIN, array_x + (wordlines + 1) * 0.108, dy), GCUT)
    # Each column's wordline on across the margin to the tile's edge, where the strip's meets it.
    for k in range(wordlines):
        x = array_x + 0.054 + 0.108 * k
        rect(row, (x - 0.009, dy - END_ROW_MARGIN, x + 0.009, dy + 0.010), M3)
    rect(row, (0.0, dy, array_x + (wordlines + 2) * 0.108, 0.0), BOUNDARY)
    return row


#: The tile's M5 supply stripes: 0.12 um (a table width; V4 under it a bar
#: as wide), one beside each of the IO block's full-height M3 supply straps,
#: away from its pair's other strap, 72 nm or more from the next (M5 over
#: 25 nm wide).  An M4 pad per strap and tile joins strap and stripe.
STRIPE_WIDTH = 0.120
STRIPE_FROM_STRAP = {"left": (-0.138, -0.018), "right": (0.030, 0.150)}  # stripe x0, x1 from the strap's centre
#: M5's tracks in the tile's frame: the macro puts a tile's frame on a multiple
#: of 48 nm in x (compile_asap7_2rw's TRACK_LATTICE), and the DRM's tracks are
#: centred 12 nm past one (M5.AUX.2); a stripe is centred on one (M5.W.4).
M5_TRACK_PITCH, M5_TRACK_OFFSET = 0.048, 0.012
#: A pad is clear of other M4 40 nm along its track, or two tracks (96 nm) off
#: it: M4's tip-to-tip rule grows every M4 48 nm up and down (M4.S.3/S.4).
M4_TIP, M4_TRACKS = 0.040, 0.096


def io_supply_straps(io: gdstk.Cell) -> list[tuple[str, float]]:
    """(net, x centre) of each of the block's M3 supply straps that runs its full height."""
    from chipforge_asap7.verification.connectivity import MetalGraph

    graph = MetalGraph(io)
    nets = {graph.label_root(l): l.text.upper() for l in io.labels if l.text.upper() in ("VDD", "VSS")}
    _, y0, _, y1 = boundary_box(io)
    straps = []
    for i, poly in enumerate(graph.polygons):
        (a, b), (c, d) = poly.bounding_box()
        if poly.layer == M3 and graph.root(i) in nets and d - b > 0.95 * (y1 - y0):
            straps.append((nets[graph.root(i)], round((a + c) / 2, 4)))
    return sorted(set(straps), key=lambda s: s[1])


def add_supply_stripes(tile: gdstk.Cell, io: gdstk.Cell, io_x: float, y_low: float, y_high: float,
                       m4_grid: tuple[float, float] | None = None) -> None:
    """M5 stripes the tile's height beside the IO block's supply straps, joined to them.

    A middle tile took its supply only through its neighbours' two
    minimum-width M3 straps a net (~135 ohm a 4:1 tile); abutted, the
    stripes run the stack's length at about a tenth of that.  With
    `m4_grid` (`build_tile`) the joining M4 pads lie on the DRM's M4 tracks
    and end on its vertex grid.
    """
    straps = io_supply_straps(io)
    if len(straps) % 2:
        raise RuntimeError(f"{io.name}: unpaired supply straps {straps}")
    _, _, _, io_top = boundary_box(io)
    # The block's M4, and the abutted neighbours' a block above and below.
    blocked = [((a, b + dy), (c, d + dy)) for p in io.get_polygons(depth=None) if p.layer == M4
               for (a, b), (c, d) in [p.bounding_box()] for dy in (-io_top, 0.0, io_top)]
    for k, (net, x) in enumerate(straps):
        side = "left" if k % 2 == 0 else "right"
        s0, s1 = (round(x + d, 3) for d in STRIPE_FROM_STRAP[side])
        # Onto the nearest M5 track, away from the strap on a tie.
        centre = io_x + (s0 + s1) / 2 - M5_TRACK_OFFSET
        below = math.floor(round(centre / M5_TRACK_PITCH, 6)) * M5_TRACK_PITCH
        offsets = sorted((below - centre, below + M5_TRACK_PITCH - centre),
                         key=lambda d: (round(abs(d), 6), d > 0 if side == "left" else d < 0))  # fmt: skip
        s0, s1 = round(s0 + offsets[0], 4), round(s1 + offsets[0], 4)
        pad0, pad1 = (round(min(s0, x - 0.009) - 0.011, 3), round(max(s1, x + 0.009) + 0.011, 3))
        y, step = 0.040, 0.001
        if m4_grid is not None:
            # Out to the vertex grid; up to the first track 40 nm in, a track at a time.
            gx, gy = m4_grid
            pad0 = (math.floor(round((1000 * (io_x + pad0) - gx) / 24, 6)) * 24 + gx) / 1000 - io_x
            pad1 = (math.ceil(round((1000 * (io_x + pad1) - gx) / 24, 6)) * 24 + gx) / 1000 - io_x
            pad0, pad1 = round(pad0, 4), round(pad1, 4)
            y, step = round((gy + math.ceil((40 - gy) / 48) * 48) / 1000, 4), 0.048
        while y < io_top - 0.040:
            box = (pad0, y - 0.012, pad1, y + 0.012)
            if all(b[0][0] >= box[2] + M4_TIP or b[1][0] <= box[0] - M4_TIP
                   or b[0][1] >= box[3] + M4_TRACKS or b[1][1] <= box[1] - M4_TRACKS for b in blocked):
                break
            y = round(y + step, 4)
        else:
            raise RuntimeError(f"{io.name}: no M4 row free to join the {net} strap at {x}")
        blocked.append(((box[0], box[1]), (box[2], box[3])))
        X = io_x
        rect(tile, (X + pad0, y - 0.012, X + pad1, y + 0.012), M4)
        rect(tile, (X + x - 0.009, y - 0.012, X + x + 0.009, y + 0.012), V3)
        rect(tile, (X + s0, y - 0.012, X + s1, y + 0.012), V4)
        rect(tile, (X + s0, y_low, X + s1, y_high), M5)


def build_tile(library: gdstk.Library, cells: dict[str, gdstk.Cell], wordlines: int,
               io: gdstk.Cell, spec: SidewaysIoColumnSpec,
               bottom: bool = False, top: bool = False,
               m4_grid: tuple[float, float] | None = None) -> gdstk.Cell:
    """``edge filler | cap | array | IO``, the array's row 0 at y = 0; a dummy row below and/or above.

    In a stack of abutted tiles only the stack's two ends carry a dummy row;
    the tile's boundary is then `END_ROW_MARGIN` past it.  `m4_grid`: a point
    of the DRM's M4 grid, nm in the tile, where the tile is placed; the
    block's `spec` has to have been drawn for it.
    """
    mux = spec.selects
    array = build_array(library, cells, wordlines, mux)
    _, _, array_width, height = boundary_box(array)
    cap_width = boundary_box(cells[CAPS[0]])[2] - boundary_box(cells[CAPS[0]])[0]
    filler = cells[EDGE_FILLER]
    fx0, fy0, fx1, fy1 = boundary_box(filler)
    # The released edge filler covers four rows; a taller tile takes it again.
    copies = round(height / (fy1 - fy0))
    if abs(copies * (fy1 - fy0) - height) > 1e-6:
        raise RuntimeError(f"{EDGE_FILLER} is {fy1 - fy0} um, the array {height}")
    array_x = (fx1 - fx0) + cap_width
    io_x = array_x + array_width
    if m4_grid is not None:
        # The block's x is a float sum of cell widths: reduce it as `io_block_grids`
        # does, to 0.1 nm and then into the period, or a hair under a multiple of
        # 24 nm reads as 24 rather than 0 (it did at 32 and 256 wordlines).
        wanted = (round((m4_grid[0] - 1000 * io_x) % 24, 1) % 24, round(m4_grid[1] % 48, 1) % 48)
        if spec.m4_grid is None or any(abs(a - b) > 1e-6 for a, b in zip(spec.m4_grid, wanted)):
            raise RuntimeError(f"the block is drawn for M4 grid {spec.m4_grid}; the tile places it on {wanted}")
    tile = library.new_cell(f"colgrp_x{wordlines}x{mux}_6t{ends_tag(bottom, top)}")
    for k in range(copies):
        tile.add(gdstk.Reference(filler, origin=(-fx0, k * (fy1 - fy0) - fy0)))
    # The edge filler column is the array's edge too, with no marker of its own.
    rect(tile, (0.0, 0.0, fx1 - fx0, height), SRAMDRC)
    tile.add(gdstk.Reference(array, origin=(array_x, 0.0)))
    tile.add(gdstk.Reference(io, origin=(io_x, 0.0)))

    # The dummy and the tap carry no bitline: strap each from the dummy's
    # stub, which the last bitcell's bar overlaps, across the tap into the
    # block's landing.
    bitcell = cells[BITCELL]
    pitch = boundary_box(bitcell)[3] - boundary_box(bitcell)[1]
    dummy_x = array_x + wordlines * (boundary_box(bitcell)[2] - boundary_box(bitcell)[0])
    for r in range(mux):
        for net, y in spec.bitline_ys(r).items():
            y = y / 1000
            rect(tile, (dummy_x + 0.054, y - 0.009, io_x + 0.054, y + 0.009), M2)
    for label_ in array.labels:
        match = re.fullmatch(r"WL\[(\d+)\]", label_.text)
        if match:
            x, y = map(float, label_.origin)
            label(tile, f"WLA[{match.group(1)}]", (array_x + x, y), M3)
    for label_ in io.labels:
        if label_.text.startswith(("BL_", "BLN_")):
            continue
        x, y = map(float, label_.origin)
        label(tile, label_.text, (io_x + x, y), label_.layer)
    end_row = build_end_row(library, cells, wordlines)
    if bottom:
        tile.add(gdstk.Reference(end_row))
    if top:
        # The row above the last (odd) row is the row below row 0 mirrored
        # about the array's top edge, as that row is row 0 mirrored there.
        tile.add(gdstk.Reference(end_row, origin=(0.0, height), x_reflection=True))
        # The row is the one below row 0 mirrored, but the last row's cap is the
        # v2 cap, whose VSS bar at the array's edge stops 9 nm short of the
        # mirrored corner's (where the v1 cap's meets it): close the gap.
        rect(tile, (array_x - 0.108, height - 0.0045, array_x - 0.067, height + 0.0045), M1)
    reach = END_ROW_HEIGHT + END_ROW_MARGIN
    add_supply_stripes(tile, io, io_x, -reach if bottom else 0.0, height + (reach if top else 0.0), m4_grid)
    rect(tile, (0.0, -reach if bottom else 0.0, io_x + boundary_box(io)[2], height + (reach if top else 0.0)),
         BOUNDARY)
    return tile


#: The released cells a tile places.
SOURCE_CELLS = (BITCELL, DUMMY, TAP, *CAPS, EDGE_FILLER, "dummy_vertical_6t122", "dummy_corner",
                "dummy_corner_v2", "tapcell_dummy_6t122", "FILLER_BLANK_6t122")


def build_library(wordline_counts: list[int], muxes: tuple[int, ...] = MUXES) -> tuple[gdstk.Library, str]:
    """A tile for every count and mux ratio, and the IO columns' SPICE."""
    cells = load_source()
    library = gdstk.Library("openfinram_asap7_6t_iocolumn", unit=1e-6, precision=2.5e-10)
    for name in SOURCE_CELLS:
        add_with_dependencies(library, cells[name])
    netlists = []
    for mux in muxes:
        spec = io_spec(cells[BITCELL], mux)
        block, deps, netlist = build_io_block(spec)
        netlists.append(netlist.replace(".END\n", ""))
        for dep in (block, *deps):
            if dep.name not in {c.name for c in library.cells}:
                library.add(dep)
        io = build_io(library, block, spec)
        for count in wordline_counts:
            for bottom, top in ((True, True), (True, False), (False, True), (False, False)):
                build_tile(library, cells, count, io, spec, bottom=bottom, top=top)
    return library, "".join(netlists) + ".END\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--word-lines", default="4,32,64",
                        help="comma-separated array wordline counts (default: 4,32,64)")
    parser.add_argument("--output", type=Path, default=REPO / "tech/gds/sram_6t_iocolumn.gds")
    parser.add_argument("--spice-output", type=Path, default=REPO / "tech/spice/sram_6t_iocolumn.sp")
    args = parser.parse_args(argv)
    counts = sorted({int(v) for v in args.word_lines.split(",")})
    if any(c < 2 or c % 2 for c in counts):
        print("ERROR: wordline counts must be even and at least 2", file=sys.stderr)
        return 1
    library, netlist = build_library(counts)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    library.write_gds(str(args.output))
    args.spice_output.parent.mkdir(parents=True, exist_ok=True)
    args.spice_output.write_text(netlist)
    print(f"wrote {args.output} and {args.spice_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
