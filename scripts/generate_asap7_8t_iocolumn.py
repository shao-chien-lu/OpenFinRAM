#!/usr/bin/env python3
"""Generate the ASAP7 dual-port 8T column hierarchy: one IO per end of the bitlines.

Each port has its IO column at its own end of one unsplit array::

    iocol A | edge cap | array, N wordlines ... tap | iocol B

Port A's bitlines leave the array on M2 and port B's on M4, so neither port
crosses the other's IO.  (The earlier floorplan put both IO cores mid-bitline
between two half arrays: port B crossed the A core on M4 and port A climbed to
M6 to cross the B core.)

Each port's IO is the parametric column block from ``chipforge_asap7``
(`IoColumnSpec`): a four-leaf bitline mux group, sense amplifier, write
driver and output latch drawn on the 8T row, with a tap.  It is built here at
the bitcell's own bitline heights, mirrored for port A so its entries face
the array, and abutted to it: the block sits half a fin pitch up the row so
its fins fall on the bitcell's grid (the bitcell centres a fin on its row
boundary, the block a fin space), and the array's bitline bars overhang into
its landings.  Its netlist is written beside the GDS
(``tech/spice/sram_8t_iocolumn.sp``) for ``SpiceGenerator``.

The resulting ``colgrp_x{N}x4_sram_8t`` cells match the logical
``colgrp_sram_8t`` hierarchy emitted by ``SpiceGenerator``.  N is the number
of wordlines of the array and may be any value present in the parameterized
wordline-array input GDS.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import math
import re
import sys
import tempfile
from pathlib import Path

import gdspy
import gdstk
from chipforge_asap7.devices import (
    BitlineMuxSpec,
    IoColumnSpec,
    block_netlist,
    build_io_column,
    io_column_pins,
)
from chipforge_asap7.layout.layers import LAYERS
from chipforge_asap7.layout.straps import (
    add_supply_straps,
    io_supply_rails,
    tie_seam_supplies,
)
from chipforge_asap7.layout.transform import (
    bbox,
    clone_label,
    direct_label,
    edge_boundary,
    indexed_labels,
    rect,
)
from chipforge_asap7.verification.sram import (
    assert_close,
    bitline_entries,
    colgrp_name,
    fin_grid_offset,
    io_block_specs,
    iocol_pins,
    load_cells,
    port_io_name,
    verify_iocolumn_gds,
)
from generate_asap7_8t_bitcell import topbot_name

BOUNDARY = LAYERS["BOUNDARY"]["layer"]
FIN = LAYERS["FIN"]["layer"]
GATE = LAYERS["GATE"]["layer"]
LIG = LAYERS["LIG"]["layer"]
V0 = LAYERS["V0"]["layer"]
M1 = LAYERS["M1"]["layer"]
V1 = LAYERS["V1"]["layer"]
M2 = LAYERS["M2"]["layer"]
V2 = LAYERS["V2"]["layer"]
M3 = LAYERS["M3"]["layer"]
V3 = LAYERS["V3"]["layer"]
M4 = LAYERS["M4"]["layer"]
V4 = LAYERS["V4"]["layer"]
M5 = LAYERS["M5"]["layer"]
V5 = LAYERS["V5"]["layer"]
M6 = LAYERS["M6"]["layer"]
V6 = LAYERS["V6"]["layer"]
M7 = LAYERS["M7"]["layer"]
PIN_TEXTTYPE = 251
MUX_ROWS = 4

#: Where a rail's LIG stops short of the block's array-facing edge: the end
#: tile's dummy row puts a WLB gate contact 17 nm past that edge, level with
#: the block's top rail, and two short LIG edges want 31 nm (LIG.S.4-5).
RAIL_LIG_INSET = 0.014
#: ASAP7's fin pitch, and where chipforge's cells (and ASAP7's standard cells)
#: centre their first fin above a row boundary.
FIN_PITCH, LEAF_FIN_PHASE = 27.0, 13.5
FIXED_GDS_TIMESTAMP = dt.datetime(2020, 1, 1, 0, 0, 0)

boundary_box = edge_boundary
supply_rails = io_supply_rails
verify_gds = verify_iocolumn_gds

#: The block's pin for each of the IO column's, per port.
BLOCK_PIN = {
    "PRECHN": "blprechn_{p}", "SAE": "sae_{p}", "D": "D{P}", "Q": "Q{P}",
    "WRENA": "wrena_{p}", "WRENAN": "wrenan_{p}", "OE": "oe_out_{p}", "OEB": "oeb_out_{p}",
}


def iocol_pin_name(block_pin: str, port: str) -> str:
    """The IO column's name for a block pin: ``BL[2]`` -> ``BL_A[2]``, ``PRECHN`` -> ``blprechn_A``.

    A two-sided block's second group (``_R``) serves the second bank of the
    pair: its bitlines and selects continue the first group's indices
    (``BL_R[2]`` -> ``BL_B[6]``), its precharge is ``blprechn_BR``.
    """
    match = re.fullmatch(r"(BL|BLN|YSEL|YSELN)(_R)?\[(\d+)\]", block_pin)
    if match:
        bus = match.group(1)
        bus = bus if bus.startswith("BL") else bus.lower()
        return f"{bus}_{port}[{int(match.group(3)) + (MUX_ROWS if match.group(2) else 0)}]"
    if block_pin in ("VDD", "VSS"):
        return block_pin
    if block_pin == "PRECHN_R":
        return f"blprechn_{port}R"
    return BLOCK_PIN[block_pin].format(p=port, P=port)


def colgrp_control_name(name: str) -> str:
    match = re.fullmatch(
        r"(wrena|wrenan|oeb_out|oe_out|blprechn)_([AB]R?)", name
    )
    if match:
        return f"{match.group(1)}{match.group(2)}"
    match = re.fullmatch(r"(yseln|ysel)_([AB])\[(\d+)\]", name)
    if match:
        return f"{match.group(1)}{match.group(2)}[{match.group(3)}]"
    return name



def build_io_blocks(specs: dict[str, IoColumnSpec]) -> tuple[dict[str, gdstk.Cell], str]:
    """Draw both ports' blocks with chipforge (nm), read them back in um; and their netlists.

    Both go into one library so the cells they share (amplifier, driver,
    latch, supports) exist once.
    """
    nm = gdspy.GdsLibrary(unit=1e-9, precision=1e-10)
    gdspy.current_library = nm
    names = {port: build_io_column(spec, lib=nm, draw_pin_labels=False).name for port, spec in specs.items()}
    with tempfile.TemporaryDirectory() as scratch:
        path = Path(scratch) / "io_blocks.gds"
        nm.write_gds(str(path))
        um = gdstk.read_gds(str(path), unit=1e-6)
    cells = {cell.name: cell for cell in um.cells}
    trim_rail_lig([cells[name] for name in names.values()])
    netlists = []
    for port, spec in specs.items():
        block = f"iocol_block_{port.lower()}"
        netlists.append(block_netlist(spec, name=block).replace(".END\n", ""))
        pins = io_column_pins(spec)
        netlists.append(
            f".SUBCKT {port_io_name(port)} {' '.join(iocol_pins(port[0], spec.two_sided))}\n"
            f"X_block {' '.join(iocol_pin_name(pin, port[0]) for pin in pins)} {block}\n"
            f".ENDS {port_io_name(port)}\n"
        )
    return {port: cells[name] for port, name in names.items()}, "\n".join(netlists) + ".END\n"


def trim_rail_lig(blocks: list[gdstk.Cell]) -> int:
    """Start the rows' rail LIG RAIL_LIG_INSET in from the blocks' array-facing edge.

    chipforge runs an LI line under every supply rail, full width, so a V0
    on the rail lands on local interconnect.  At the edge that meets the
    array (x = 0 of each bitline-mux row; the two-sided block places its
    mux twice, the second mirrored, so both its edges) nothing lands that
    close, and the top rail's line, past the block's boundary, would sit
    17 nm from the end row's WLB contact.  Returns the lines trimmed.
    """
    rows = {c.name: c for block in blocks for c in block.dependencies(True) if c.name.endswith("__row")}
    trimmed = 0
    for row in rows.values():
        inner = row.get_polygons(depth=None)
        for poly in [q for q in row.polygons if q.layer == LIG]:
            x0, y0, x1, y1 = bbox(poly)
            if y1 - y0 > 0.017 or x1 - x0 < 0.3 or abs(x0) > 1e-6:
                continue  # not a rail line reaching the array-facing edge
            lo = x0 + RAIL_LIG_INSET
            for q in inner:
                if q.layer in (V0, GATE):
                    a, b, c, d = bbox(q)
                    if b < y1 and d > y0 and a < lo and c > x0:
                        raise RuntimeError(f"{row.name}: a {'V0' if q.layer == V0 else 'GATE'} uses the rail LIG's end")
            row.remove(poly)
            rect(row, (lo, y0, x1, y1), LIG)
            trimmed += 1
    if not trimmed:
        raise RuntimeError("no rail LIG reaches the IO blocks' array-facing edge")
    return trimmed


#: A supply strap: an M3 line the IO column's height, landing on every M1




def build_port_io(
    library: gdstk.Library, port: str, block: gdstk.Cell, spec: IoColumnSpec
) -> gdstk.Cell:
    """One port's IO column: its block, abutting the array.

    Port A stands left of the array, so its block is mirrored in x to put the
    bitline entries on its right face; port B stands right of the array with
    the block as drawn.  The block sits `grid_offset` up the column so its
    fins are on the array's; its landings then meet each row's bitline bar,
    which overhangs the array's edge into them.  The cell's boundary is the
    column's, which the block overhangs by that offset at the top.

    ``B2``, the two-sided port-B block, stands between two arrays, its
    second group's bitlines entering from its right face.
    """
    width, height = spec.width / 1000, spec.height / 1000
    offset = spec.mux.grid_offset / 1000
    mirrored = port == "A"

    cell = library.new_cell(port_io_name(port))
    if mirrored:
        # A reflection in y then a half turn is a mirror in x.
        cell.add(gdstk.Reference(block, origin=(width, offset), rotation=math.pi, x_reflection=True))
    else:
        cell.add(gdstk.Reference(block, origin=(0.0, offset)))

    def placed(x: float, y: float) -> tuple[float, float]:
        """A block-coordinate point (nm) in the cell (um)."""
        return ((width - x / 1000) if mirrored else x / 1000, offset + y / 1000)

    for pin, (metal, (x, y)) in spec.pin_positions.items():
        base = pin.split(".")[0]
        gds_layer = {"M1": M1, "M2": M2, "M3": M3, "M4": M4}[metal]
        px, py = placed(x, y)
        name = iocol_pin_name(base, port[0])
        if re.fullmatch(r"BLN?(_R)?\[\d+\]", base):
            face = width - 0.006 if mirrored or "_R" in base else 0.006
            cell.add(gdstk.Label(name, (face, py), layer=gds_layer, texttype=PIN_TEXTTYPE))
        else:
            cell.add(gdstk.Label(name, (px, py), layer=gds_layer, texttype=PIN_TEXTTYPE))

    rect(cell, (0.0, 0.0, width, height), BOUNDARY)
    # Port A's outer edge is its west one (its block is mirrored), port B's
    # the east; the two-sided block faces arrays both ways, so its straps
    # go in its logic column.
    add_supply_straps(cell, {"A": "low", "B": "high", "B2": "middle"}[port])
    return cell


def build_cap_array(library: gdstk.Library, edges: dict[str, gdstk.Cell],
                    mirror_x: bool = False) -> gdstk.Cell:
    cap = edges[topbot_name()]
    filler = edges["FILLER_cgedge_8t"]
    x0, y0, x1, y1 = boundary_box(cap)
    width, height = x1 - x0, y1 - y0
    fx0, fy0, fx1, fy1 = boundary_box(filler)
    filler_width = fx1 - fx0
    if abs(fy1 - fy0 - MUX_ROWS * height) > 1e-7:
        raise RuntimeError("8T column-group filler height does not match caps")
    cell = library.new_cell("col_cap_x4_sram_8t" + ("_lr" if mirror_x else ""))
    cell.add(gdstk.Reference(filler, origin=((width if mirror_x else 0) - fx0, -fy0)))
    for row in range(MUX_ROWS):
        master = edges[topbot_name(mirror_x, bool(row % 2))]
        origin = ((0 if mirror_x else filler_width) - x0, row * height - y0)
        cell.add(gdstk.Reference(master, origin=origin))
    rect(cell, (0.0, 0.0, width + filler_width, MUX_ROWS * height), BOUNDARY)
    return cell



def build_colgrp(
    library: gdstk.Library,
    array: gdstk.Cell,
    io_a: gdstk.Cell,
    io_b: gdstk.Cell | None,
    cap_array: gdstk.Cell,
    wordlines: int,
) -> gdstk.Cell:
    """``iocol A | edge cap | array | iocol B``, one array of `wordlines` wordlines.

    The cap terminates the array's first bitcell as it did at the outer end of
    a half array; it hands every bitline through, and the filler beside it has
    no metal, so port A's M2 bitlines are strapped across the filler.  The far
    end needs no cap: a tapped array ends in a tap there, which is what an IO
    face has always met.

    Without `io_b` it is the half of a pair (`build_colgrp_pair`): the
    array's port-B bitlines end at its right edge, labelled ``BL_B``/``BLN_B``
    where the shared block's landings meet them.
    """
    arx0, ary0, arx1, ary1 = boundary_box(array)
    ax0, ay0, ax1, ay1 = boundary_box(io_a)
    bx0, by0, bx1, by1 = boundary_box(io_b) if io_b is not None else (0.0, ary0, 0.0, ary1)
    cpx0, cpy0, cpx1, cpy1 = boundary_box(cap_array)
    array_width, array_height = arx1 - arx0, ary1 - ary0
    cap_width = cpx1 - cpx0
    for box, what in (((ax0, ay0, ax1, ay1), "port-A IO"), ((bx0, by0, bx1, by1), "port-B IO"),
                      ((cpx0, cpy0, cpx1, cpy1), "edge cap")):
        if abs((box[3] - box[1]) - array_height) > 1e-6:
            raise RuntimeError(f"8T array and {what} heights do not match")

    cap_x = ax1 - ax0
    array_x = cap_x + cap_width
    io_b_x = array_x + array_width
    total_width = io_b_x + (bx1 - bx0)
    cell = library.new_cell(colgrp_name(wordlines, "" if io_b is not None else "half"))
    cell.add(gdstk.Reference(io_a, origin=(-ax0, -ay0)))
    cell.add(gdstk.Reference(cap_array, origin=(cap_x - cpx0, -cpy0)))
    cell.add(gdstk.Reference(array, origin=(array_x - arx0, -ary0)))
    if io_b is not None:
        cell.add(gdstk.Reference(io_b, origin=(io_b_x - bx0, -by0)))

    for source, layer in (("WLA", M3), ("WLB", M5)):
        for index, label in indexed_labels(array, source).items():
            x, y = map(float, label.origin)
            cell.add(clone_label(
                label, f"{source}[{index}]", (array_x + x - arx0, y - ary0), layer,
            ))

    # Each port's bitlines have to arrive at the height and on the layer its IO
    # column takes them.  Port A's cross the metal-free filler to the cap.
    filler = next(ref.cell for ref in cap_array.references
                  if ref.cell_name == "FILLER_cgedge_8t")
    if any(poly.layer in (M2, M4) for poly in filler.polygons):
        raise RuntimeError("edge filler is no longer free of bitline metal")
    fx0, _, fx1, _ = boundary_box(filler)
    cap_metal_x = cap_x + (fx1 - fx0)
    bitline_map = {
        "BLA": ("BL_A", io_a, M2), "BLAN": ("BLN_A", io_a, M2),
        "BLB": ("BL_B", io_b, M4), "BLBN": ("BLN_B", io_b, M4),
    }
    for source, (io_prefix, io_cell, layer) in bitline_map.items():
        array_labels = indexed_labels(array, source)
        if io_cell is None:
            for index, array_label in array_labels.items():
                y = float(array_label.origin[1]) - ary0
                cell.add(gdstk.Label(f"{io_prefix}[{index}]", (io_b_x - 0.006, y), layer=layer,
                                     texttype=PIN_TEXTTYPE))
            continue
        io_labels = indexed_labels(io_cell, io_prefix)
        if set(array_labels) != set(io_labels):
            raise RuntimeError(f"{cell.name}: incomplete {source} interface")
        # The IO column's pin sits on the centre of the bitcell's bar; the
        # array's label is wherever the bitcell put its own, within the bar.
        half = 0.009 if layer == M2 else 0.012
        for index, array_label in array_labels.items():
            y = float(array_label.origin[1]) - ary0
            io_label = io_labels[index]
            io_y = float(io_label.origin[1]) - (ay0 if io_cell is io_a else by0)
            if io_label.layer != layer or abs(io_y - y) > half + 1e-6:
                raise RuntimeError(
                    f"{cell.name}: {io_label.text} does not align to {source}[{index}]"
                )
            if io_cell is io_a:
                rect(cell, (cap_x - 0.018, io_y - half, cap_metal_x + 0.018, io_y + half), layer)

    for io_cell, io_x, (ox, oy) in ((io_a, 0.0, (ax0, ay0)), (io_b, io_b_x, (bx0, by0))):
        if io_cell is None:
            continue
        for label in io_cell.labels:
            if label.text.startswith(("BL_", "BLN_")):
                continue
            if label.text in {"VDD", "VSS"} and label.layer == M1 and io_cell is io_a and io_b is not None:
                continue  # one rail label per supply is enough; port B's are kept
            point = (io_x + float(label.origin[0]) - ox, float(label.origin[1]) - oy)
            cell.add(clone_label(label, colgrp_control_name(label.text), point))

    # Port A meets the cap, port B the array's last column: a tap in a
    # tapped array, a bitcell (no room for every tie) in a bare one.
    tie_seam_supplies(cell, cap_x, "left")
    if io_b is not None:
        tie_seam_supplies(cell, io_b_x, "right", every=False)
    rect(cell, (0.0, 0.0, total_width, array_height), BOUNDARY)
    return cell


#: The second bank's name for a column pin in a pair tile: wordlines and
#: selects continue the first bank's indices, one-per-bank controls take an R.
def second_bank_name(name: str, wordlines: int) -> str:
    match = re.fullmatch(r"(WLA|WLB)\[(\d+)\]", name)
    if match:
        return f"{match.group(1)}[{int(match.group(2)) + wordlines}]"
    match = re.fullmatch(r"(yseln|ysel)A\[(\d+)\]", name)
    if match:
        return f"{match.group(1)}A[{int(match.group(2)) + MUX_ROWS}]"
    if re.fullmatch(r"(wrena|wrenan|oeb_out|oe_out|blprechn)A|sae_A", name):
        return name + "R"
    return name  # DA, QA (the bit's, whichever bank), supplies


def build_colgrp_pair(
    library: gdstk.Library, half: gdstk.Cell, io_b2: gdstk.Cell, wordlines: int, name: str | None = None
) -> gdstk.Cell:
    """``half | iocol B2 | half mirrored``: two banks' columns sharing port B's block.

    The second half is the first mirrored in x, so both arrays' port-B ends
    face the block, the first's on its left group, the second's on its right.
    `half` is `build_colgrp`'s half or anything built on it (with dummy rows),
    its boundary the column's; its top-level labels are the pair's first
    bank's, and the second bank's are renamed by `second_bank_name`.
    """
    hx0, hy0, hx1, hy1 = boundary_box(half)
    bx0, by0, bx1, by1 = boundary_box(io_b2)
    half_width, block_width = hx1 - hx0, bx1 - bx0
    if abs(hx0) > 1e-9:
        raise RuntimeError(f"{half.name}: a half starts at x = 0")
    total_width = 2 * half_width + block_width
    cell = library.new_cell(name or colgrp_name(wordlines, "pair"))
    cell.add(gdstk.Reference(half))
    cell.add(gdstk.Reference(io_b2, origin=(half_width - bx0, -by0)))
    # A reflection in y then a half turn is a mirror in x: x -> total - x.
    cell.add(gdstk.Reference(half, origin=(total_width, 0.0), rotation=math.pi, x_reflection=True))

    # Both arrays' port-B bitlines meet the block's landings.
    for prefix in ("BL_B", "BLN_B"):
        ends = indexed_labels(half, prefix)
        block = indexed_labels(io_b2, prefix)
        if set(block) != set(range(2 * MUX_ROWS)) or set(ends) != set(range(MUX_ROWS)):
            raise RuntimeError(f"{cell.name}: incomplete {prefix} interface")
        for index, end in ends.items():
            for leaf in (index, index + MUX_ROWS):
                if abs(float(block[leaf].origin[1]) - by0 - float(end.origin[1])) > 0.012 + 1e-6:
                    raise RuntimeError(f"{cell.name}: {block[leaf].text} does not align to {end.text}")

    for label in half.labels:
        if label.text.startswith(("BL_", "BLN_")):
            continue
        x, y = map(float, label.origin)
        cell.add(clone_label(label, label.text, (x, y)))
        cell.add(clone_label(label, second_bank_name(label.text, wordlines), (total_width - x, y)))
    for label in io_b2.labels:
        if label.text.startswith(("BL_", "BLN_")):
            continue
        point = (half_width + float(label.origin[0]) - bx0, float(label.origin[1]) - by0)
        cell.add(clone_label(label, colgrp_control_name(label.text), point))
    tie_seam_supplies(cell, half_width, "right", every=False)
    tie_seam_supplies(cell, half_width + block_width, "left", every=False)
    rect(cell, (0.0, hy0, total_width, hy1), BOUNDARY)
    return cell



def build_library(
    arrays_gds: Path,
    edges_gds: Path,
    wordline_counts: list[int],
) -> tuple[gdstk.Library, str]:
    """The column hierarchy for every count, and the IO columns' SPICE."""
    arrays_lib, arrays = load_cells(arrays_gds)
    edges_lib, edges = load_cells(edges_gds)
    units = {(library.unit, library.precision) for library in (arrays_lib, edges_lib)}
    if len(units) != 1:
        raise RuntimeError("8T source GDS units/precision do not match")
    edge_names = {topbot_name(False, my) for my in (False, True)}
    edge_names.update({"FILLER_BLANK_8t", "FILLER_cgedge_8t"})
    bitcell = arrays.get("sram_cell_8t")
    if bitcell is None or not edge_names <= edges.keys():
        raise RuntimeError("8T source GDS is missing a required hard cell")

    unit, precision = units.pop()
    library = gdstk.Library(
        "openfinram_asap7_8t_iocolumn", unit=unit, precision=precision
    )
    library.add(bitcell, arrays["sram_cell_8t_b"], arrays["sram_cell_8t_b_end"],
                *(edges[name] for name in sorted(edge_names)))
    selected_arrays: dict[int, gdstk.Cell] = {}
    for count in wordline_counts:
        row_name = f"sramcol_x{count}_sram_8t"
        array_name = f"array_x{count}x4_sram_8t"
        if row_name not in arrays or array_name not in arrays:
            raise RuntimeError(
                f"{arrays_gds}: missing {array_name}; regenerate the wordline "
                f"artifact with --word-lines including {count}"
            )
        library.add(arrays[row_name], arrays[array_name])
        selected_arrays[count] = arrays[array_name]

    specs = io_block_specs(bitcell)
    blocks, netlist = build_io_blocks(specs)
    for block in blocks.values():
        for dep in (block, *block.dependencies(True)):
            if dep not in library.cells:
                library.add(dep)
    io_a = build_port_io(library, "A", blocks["A"], specs["A"])
    io_b = build_port_io(library, "B", blocks["B"], specs["B"])
    io_b2 = build_port_io(library, "B2", blocks["B2"], specs["B2"])
    cap_array = build_cap_array(library, edges)
    for count in wordline_counts:
        build_colgrp(library, selected_arrays[count], io_a, io_b, cap_array, count)
        half = build_colgrp(library, selected_arrays[count], io_a, None, cap_array, count)
        build_colgrp_pair(library, half, io_b2, count)
    return library, netlist






def parse_wordlines(value: str) -> list[int]:
    try:
        counts = sorted({int(item) for item in value.split(",")})
    except ValueError as error:
        raise argparse.ArgumentTypeError("wordline counts must be integers") from error
    if not counts or any(count < 1 for count in counts):
        raise argparse.ArgumentTypeError("wordline counts must be positive")
    return counts


def parse_args(argv: list[str]) -> argparse.Namespace:
    repo = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--word-lines", type=parse_wordlines,
        default=parse_wordlines("2,32,64,128"),
        help="comma-separated array wordline counts (default: 2,32,64,128)",
    )
    parser.add_argument(
        "--arrays-gds", type=Path,
        default=repo / "tech/gds/sram_wordline_arrays.gds",
    )
    parser.add_argument(
        "--edges-gds", type=Path,
        default=repo / "tech/gds/sram_cell_8t_edges.gds",
    )
    parser.add_argument(
        "--output", type=Path,
        default=repo / "tech/gds/sram_8t_iocolumn.gds",
    )
    parser.add_argument(
        "--spice-output", type=Path, default=None,
        help="the IO columns' SPICE (default: tech/spice/sram_8t_iocolumn.sp beside the default output)",
    )
    parser.add_argument(
        "--verify", type=Path,
        help="verify an existing GDS instead of generating one",
    )
    parser.add_argument(
        "--verify-spice", type=Path, default=None,
        help="with --verify: the SPICE file that has to go with it",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    repo = Path(__file__).resolve().parents[1]
    try:
        if args.verify:
            digests = verify_gds(args.verify, args.word_lines, args.verify_spice)
            print(f"PASS {args.verify}: {len(digests)} routed IO-column cells")
        else:
            library, netlist = build_library(args.arrays_gds, args.edges_gds, args.word_lines)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            library.write_gds(str(args.output), timestamp=FIXED_GDS_TIMESTAMP)
            spice = args.spice_output or (
                repo / "tech/spice/sram_8t_iocolumn.sp"
                if args.output == repo / "tech/gds/sram_8t_iocolumn.gds"
                else args.output.with_suffix(".sp")
            )
            spice.parent.mkdir(parents=True, exist_ok=True)
            spice.write_text(netlist)
            digests = verify_gds(args.output, args.word_lines, spice)
            print(f"wrote {args.output} and {spice}: {len(digests)} routed IO-column cells")
        for name, digest in digests.items():
            print(f"  {name}: sha256={digest}")
        return 0
    except (OSError, RuntimeError, ValueError, StopIteration) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
