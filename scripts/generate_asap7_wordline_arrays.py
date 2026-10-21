#!/usr/bin/env python3
"""Generate parameterized ASAP7 SRAM wordline rows and mux-height arrays.

The published academic 6T library composes its fixed bitcell into
``sramcol_x32/x64/x128`` rows and then stacks four copies into ``array_xNx4``.
This generator preserves that hierarchy for the OpenFinRAM 8T cell and also
provides the same construction for arbitrary 6T wordline counts.

The wordline count and the column-mux height are deliberately independent:
``--word-lines`` controls the number of addressable cells across a row, while
``--mux-rows`` controls the number of bitline pairs presented to each IO mux.
The checked-in 8T IO wrappers have a four-row interface, so their pitch is
cross-checked whenever ``--mux-rows=4``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import gdstk

from generate_asap7_8t_bitcell import TAP_SLOTS  # bitcell slots a tap takes


BOUNDARY = 100
TAP_CELL_NAME = "tapcell_sram_8t"
STRAP_CELL_NAME = "strapcell_sram_8t"
GATE = 7
PIN_TEXTTYPE = 251
FIXED_GDS_TIMESTAMP = dt.datetime(2020, 1, 1, 0, 0, 0)

# Layers whose bitcell shapes intentionally extend beyond the placement
# boundary.  This is the same mirrored-abutment convention used by the
# published ASAP7 6T cell; checking it prevents a centered core with empty
# east/west seams from passing the hierarchy verifier.
PROCESS_SEAM_LAYERS = {
    "WELL": 1,
    "FIN": 2,
    "ACTIVE": 11,
    "NSELECT": 12,
    "PSELECT": 13,
}


@dataclass(frozen=True)
class CellContract:
    key: str
    bitcell_name: str
    suffix: str
    wordlines: tuple[str, ...]
    bitlines: tuple[str, ...]
    pin_layers: dict[str, int]
    #: The master of the slots mirrored in x, if not the bitcell: the 8T's
    #: variant B, whose WLB landings are on other M4 tracks so mirrored
    #: neighbours never meet landing to landing.
    mirrored_bitcell_name: str | None = None
    #: Untapped mirrored end: keep its bitline via stack inside the array.
    terminal_mirrored_bitcell_name: str | None = None


CONTRACTS = (
    CellContract(
        key="8t",
        bitcell_name="sram_cell_8t",
        suffix="sram_8t",
        wordlines=("WLA", "WLB"),
        bitlines=("BLA", "BLAN", "BLB", "BLBN"),
        pin_layers={"WLA": 30, "WLB": 50, "BLA": 20, "BLAN": 20,
                    "BLB": 40, "BLBN": 40},
        mirrored_bitcell_name="sram_cell_8t_b",
        terminal_mirrored_bitcell_name="sram_cell_8t_b_end",
    ),
    CellContract(
        key="6t",
        bitcell_name="sram_cell_6t_122",
        suffix="sram_6t122",
        wordlines=("WL",),
        bitlines=("BL", "BLN"),
        pin_layers={"WL": 30, "BL": 20, "BLN": 20},
    ),
)


def bbox(poly: gdstk.Polygon) -> tuple[float, float, float, float]:
    (x0, y0), (x1, y1) = poly.bounding_box()
    return tuple(round(float(value), 7) for value in (x0, y0, x1, y1))


def boundary_box(cell: gdstk.Cell) -> tuple[float, float, float, float]:
    boxes = [bbox(poly) for poly in cell.polygons
             if poly.layer == BOUNDARY and poly.datatype == 0]
    if len(boxes) != 1:
        raise RuntimeError(f"{cell.name}: expected one direct BOUNDARY polygon")
    return boxes[0]


def direct_label(cell: gdstk.Cell, name: str) -> gdstk.Label:
    labels = [label for label in cell.labels if label.text == name]
    if len(labels) != 1:
        raise RuntimeError(
            f"{cell.name}: expected one direct {name!r} label, found {len(labels)}"
        )
    return labels[0]


def supply_label(cell: gdstk.Cell, prefix: str) -> gdstk.Label:
    labels = [label for label in cell.labels
              if label.text.lower().startswith(prefix.lower())]
    if not labels:
        raise RuntimeError(f"{cell.name}: missing {prefix} supply label")
    return labels[0]


def clone_label(label: gdstk.Label, text: str,
                origin: tuple[float, float]) -> gdstk.Label:
    return gdstk.Label(
        text,
        origin,
        anchor=label.anchor,
        rotation=label.rotation,
        magnification=label.magnification,
        x_reflection=label.x_reflection,
        layer=label.layer,
        texttype=PIN_TEXTTYPE,
    )


def transformed_point(
    point: tuple[float, float],
    origin: tuple[float, float],
    mirror_x: bool = False,
    mirror_y: bool = False,
) -> tuple[float, float]:
    x, y = point
    ox, oy = origin
    return (
        round(ox - x if mirror_x else ox + x, 7),
        round(oy - y if mirror_y else oy + y, 7),
    )


def slot_layout(wordlines: int, tap_pitch: int) -> tuple[int, list[int]]:
    """Physical slot count, and the slot each wordline's bitcell occupies.

    A tap takes `TAP_SLOTS` bitcell slots after every `tap_pitch` cells.
    """
    if not tap_pitch:
        return wordlines, list(range(wordlines))
    slot_of = [index + TAP_SLOTS * (index // tap_pitch) for index in range(wordlines)]
    return wordlines + TAP_SLOTS * (wordlines // tap_pitch), slot_of


def tap_slots(wordlines: int, tap_pitch: int) -> list[int]:
    """The first slot of every tap in a row."""
    if not tap_pitch:
        return []
    return [index + 1 + TAP_SLOTS * (index // tap_pitch)
            for index in range(wordlines) if (index + 1) % tap_pitch == 0]


def tap_tag(tap_pitch: int, strap_pitch: int = 0) -> str:
    if not tap_pitch:
        return ""
    return f"_tap{tap_pitch}" + (f"_strap{strap_pitch}" if strap_pitch else "")


def strap_slots(wordlines: int, tap_pitch: int, strap_pitch: int) -> set[int]:
    """Physical slots (a tap's first) whose tap is upgraded to a supply strap."""
    if not tap_pitch or not strap_pitch:
        return set()
    return {slot for index, slot in enumerate(tap_slots(wordlines, tap_pitch))
            if index % strap_pitch == 0}


def row_name(contract: CellContract, wordlines: int, tap_pitch: int = 0,
             strap_pitch: int = 0) -> str:
    return (f"sramcol_x{wordlines}"
            f"{tap_tag(tap_pitch, strap_pitch)}_{contract.suffix}")


def array_name(contract: CellContract, wordlines: int, mux_rows: int,
               tap_pitch: int = 0, strap_pitch: int = 0) -> str:
    return (f"array_x{wordlines}x{mux_rows}"
            f"{tap_tag(tap_pitch, strap_pitch)}_{contract.suffix}")


def build_row(
    library: gdstk.Library,
    bitcell: gdstk.Cell,
    contract: CellContract,
    wordlines: int,
    tap_cell: gdstk.Cell | None = None,
    tap_pitch: int = 0,
    strap_cell: gdstk.Cell | None = None,
    strap_pitch: int = 0,
    mirrored_bitcell: gdstk.Cell | None = None,
    terminal_mirrored_bitcell: gdstk.Cell | None = None,
) -> gdstk.Cell:
    x0, y0, x1, y1 = boundary_box(bitcell)
    for master in (mirrored_bitcell, terminal_mirrored_bitcell):
        if master is not None and boundary_box(master) != (x0, y0, x1, y1):
            raise RuntimeError(f"{master.name} does not share the bitcell's boundary")
    width = x1 - x0
    height = y1 - y0
    if tap_pitch:
        if tap_cell is None:
            raise RuntimeError("a tap pitch needs a tap cell")
        tx0, ty0, tx1, ty1 = boundary_box(tap_cell)
        if abs((tx1 - tx0) - TAP_SLOTS * width) > 1e-6 or abs((ty1 - ty0) - height) > 1e-6:
            raise RuntimeError(f"tap cell is not {TAP_SLOTS} bitcell slots on the bitcell's row")
        if strap_pitch:
            if strap_cell is None:
                raise RuntimeError("a strap pitch needs a strap cell")
            if boundary_box(strap_cell) != boundary_box(tap_cell):
                raise RuntimeError("strap cell does not share the tap boundary")
    straps = strap_slots(wordlines, tap_pitch, strap_pitch)
    row = library.new_cell(
        row_name(contract, wordlines, tap_pitch, strap_pitch)
    )

    origins: list[tuple[float, float]] = []
    mirrors: list[bool] = []
    slot = 0
    for index in range(wordlines):
        mirror_x = index % 2 == 1
        origin = (
            (slot + 1) * width + x0 if mirror_x else slot * width - x0,
            -y0,
        )
        master = mirrored_bitcell if mirror_x and mirrored_bitcell is not None else bitcell
        if mirror_x and index == wordlines - 1 and not tap_pitch and terminal_mirrored_bitcell is not None:
            master = terminal_mirrored_bitcell
        row.add(gdstk.Reference(
            master,
            origin=origin,
            rotation=math.pi if mirror_x else 0,
            x_reflection=mirror_x,
        ))
        origins.append(origin)
        mirrors.append(mirror_x)
        slot += 1

        for pin in contract.wordlines:
            label = direct_label(bitcell, pin)
            point = transformed_point(
                tuple(label.origin), origin, mirror_x=mirror_x
            )
            row.add(clone_label(label, f"{pin}[{index}]", point))

        # Bound the distance from any bitcell to a well/substrate tie.  The tap
        # hands every bitline straight through, so it can sit mid-row.
        if tap_pitch and (index + 1) % tap_pitch == 0:
            master = strap_cell if slot in straps else tap_cell
            row.add(gdstk.Reference(
                master, origin=(slot * width - tx0, -ty0)
            ))
            slot += TAP_SLOTS

    # As in the academic sramcol hierarchy, expose each shared bitline at the
    # terminal bitcell.  Mirroring the row later moves these pins to the
    # opposite IO-facing edge without changing the logical net name.
    terminal = wordlines - 1
    for pin in contract.bitlines:
        label = direct_label(bitcell, pin)
        point = transformed_point(
            tuple(label.origin), origins[terminal], mirror_x=mirrors[terminal]
        )
        row.add(clone_label(label, pin, point))

    for source_name, output_name in (("vdd", "VDD"), ("vss", "VSS")):
        label = supply_label(bitcell, source_name)
        point = transformed_point(tuple(label.origin), origins[terminal],
                                  mirror_x=mirrors[terminal])
        row.add(clone_label(label, output_name, point))

    row.add(gdstk.rectangle((0, 0), (slot * width, height),
                            layer=BOUNDARY, datatype=0))
    return row


def build_array(
    library: gdstk.Library,
    row: gdstk.Cell,
    contract: CellContract,
    wordlines: int,
    mux_rows: int,
    tap_pitch: int = 0,
    strap_pitch: int = 0,
) -> gdstk.Cell:
    x0, y0, x1, y1 = boundary_box(row)
    width = x1 - x0
    height = y1 - y0
    array = library.new_cell(
        array_name(contract, wordlines, mux_rows, tap_pitch, strap_pitch)
    )

    row_origins: list[tuple[float, float]] = []
    row_mirrors: list[bool] = []
    for index in range(mux_rows):
        mirror_y = index % 2 == 1
        origin = (
            -x0,
            (index + 1) * height + y0 if mirror_y else index * height - y0,
        )
        array.add(gdstk.Reference(row, origin=origin, x_reflection=mirror_y))
        row_origins.append(origin)
        row_mirrors.append(mirror_y)

    # Wordlines run through every mux row.  Export one label per address and
    # port from the first row; the abutted conductors continue through the
    # remaining row references.
    for pin in contract.wordlines:
        for index in range(wordlines):
            label = direct_label(row, f"{pin}[{index}]")
            point = transformed_point(tuple(label.origin), row_origins[0])
            array.add(clone_label(label, f"{pin}[{index}]", point))

    # Each stacked row has an independent bitline pair selected by its YSEL.
    for row_index in range(mux_rows):
        for pin in contract.bitlines:
            label = direct_label(row, pin)
            point = transformed_point(
                tuple(label.origin),
                row_origins[row_index],
                mirror_y=row_mirrors[row_index],
            )
            array.add(clone_label(label, f"{pin}[{row_index}]", point))

    for source_name in ("VDD", "VSS"):
        label = direct_label(row, source_name)
        for row_index in range(mux_rows):
            point = transformed_point(
                tuple(label.origin),
                row_origins[row_index],
                mirror_y=row_mirrors[row_index],
            )
            array.add(clone_label(label, source_name, point))

    array.add(gdstk.rectangle((0, 0), (width, mux_rows * height),
                              layer=BOUNDARY, datatype=0))
    return array


def load_cell(path: Path, name: str) -> tuple[gdstk.Library, gdstk.Cell]:
    library = gdstk.read_gds(str(path))
    cells = {cell.name: cell for cell in library.cells}
    if name not in cells:
        raise RuntimeError(f"{path}: missing {name}")
    return library, cells[name]


def build_library(
    source_8t: Path,
    source_6t: Path,
    wordline_counts: list[int],
    mux_rows: int,
    tap_gds: Path | None = None,
    tap_pitch: int = 0,
    strap_pitch: int = 0,
) -> gdstk.Library:
    source_lib_8t, bitcell_8t = load_cell(source_8t, "sram_cell_8t")
    source_lib_6t, bitcell_6t = load_cell(source_6t, "sram_cell_6t_122")
    if (source_lib_8t.unit, source_lib_8t.precision) != (
        source_lib_6t.unit, source_lib_6t.precision
    ):
        raise RuntimeError("6T and 8T GDS units/precision do not match")
    x8_0, _y8_0, x8_1, _y8_1 = boundary_box(bitcell_8t)
    x6_0, _y6_0, x6_1, _y6_1 = boundary_box(bitcell_6t)
    if abs((x8_1 - x8_0) - (x6_1 - x6_0)) > 1e-6:
        raise RuntimeError("8T east/west pitch must match the published 6T cell")

    library = gdstk.Library(
        "openfinram_asap7_wordline_arrays",
        unit=source_lib_8t.unit,
        precision=source_lib_8t.precision,
    )
    library.add(bitcell_8t, bitcell_6t)
    bitcells = {"8t": bitcell_8t, "6t": bitcell_6t}
    # A missing alternate master would silently restore the M4 tip violations.
    mirrored, terminal = {}, {}
    for contract in CONTRACTS:
        cell = next((c for c in source_lib_8t.cells if c.name == contract.mirrored_bitcell_name), None)
        if contract.mirrored_bitcell_name is not None:
            if cell is None:
                raise RuntimeError(f"Missing {contract.mirrored_bitcell_name}; regenerate the bitcell library")
            library.add(cell)
            mirrored[contract.key] = cell
        if contract.terminal_mirrored_bitcell_name is not None:
            _, cell = load_cell(source_8t, contract.terminal_mirrored_bitcell_name)
            library.add(cell)
            terminal[contract.key] = cell

    # Only the 8T tap hands every bitline through, so only the 8T rows can
    # take an interleaved tap.  The published 6T tap terminates its column.
    tap_cell = strap_cell = None
    if tap_pitch:
        if tap_gds is None:
            raise RuntimeError("--tap-pitch needs --tap-gds")
        _tap_lib, tap_cell = load_cell(tap_gds, TAP_CELL_NAME)
        library.add(tap_cell)
        if strap_pitch:
            _strap_lib, strap_cell = load_cell(tap_gds, STRAP_CELL_NAME)
            library.add(strap_cell)

    for contract in CONTRACTS:
        pitch = tap_pitch if contract.key == "8t" else 0
        straps = strap_pitch if contract.key == "8t" else 0
        for count in wordline_counts:
            row = build_row(library, bitcells[contract.key], contract, count,
                            tap_cell=tap_cell, tap_pitch=pitch,
                            strap_cell=strap_cell, strap_pitch=straps,
                            mirrored_bitcell=mirrored.get(contract.key),
                            terminal_mirrored_bitcell=terminal.get(contract.key))
            build_array(library, row, contract, count, mux_rows, pitch, straps)
    return library


def indexed_labels(cell: gdstk.Cell, pin: str) -> dict[int, gdstk.Label]:
    pattern = re.compile(rf"{re.escape(pin)}\[([0-9]+)\]")
    result: dict[int, gdstk.Label] = {}
    for label in cell.labels:
        match = pattern.fullmatch(label.text)
        if match:
            index = int(match.group(1))
            if index in result:
                raise RuntimeError(f"{cell.name}: duplicate {label.text}")
            result[index] = label
    return result


def assert_close(actual: float, expected: float, message: str) -> None:
    if abs(actual - expected) > 1e-6:
        raise RuntimeError(f"{message}: {actual} != {expected}")


def verify_8t_abutment(
    row: gdstk.Cell,
    array: gdstk.Cell,
    wordlines: int,
    mux_rows: int,
    pitch_x: float,
    pitch_y: float,
    slots: int,
    slot_of: list[int],
) -> None:
    """Audit electrical continuity and isolation across every array seam.

    Boundary dimensions alone cannot catch an off-center core, a wordline
    that stops before a north/south edge, or neighboring wordlines that
    accidentally touch after mirroring.  Probe the actual flattened metal at
    every cell/row seam and require one distinct vertical component per
    address on both ports.
    """
    x_probes = sorted(
        {(index + 0.5) * pitch_x for index in range(slots)}
        | {index * pitch_x for index in range(1, slots)}
    )
    for pin in ("BLA", "BLAN", "BLB", "BLBN"):
        label = direct_label(row, pin)
        polygons = row.get_polygons(layer=label.layer, datatype=0)
        points = [(x, float(label.origin[1])) for x in x_probes]
        if not all(gdstk.inside(points, polygons)):
            raise RuntimeError(f"{row.name}: {pin} is discontinuous east/west")

    y_probes = sorted(
        {(index + 0.5) * pitch_y for index in range(mux_rows)}
        | {index * pitch_y for index in range(1, mux_rows)}
    )
    for pin in ("WLA", "WLB"):
        labels = indexed_labels(array, pin)
        ordered = [labels[index] for index in range(wordlines)]
        for index, label in enumerate(ordered):
            x = float(label.origin[0])
            slot = slot_of[index]
            left, right = slot * pitch_x, (slot + 1) * pitch_x
            if not left < x < right:
                raise RuntimeError(
                    f"{array.name}: {pin}[{index}] lies outside its cell"
                )

        polygons = array.get_polygons(layer=contract_pin_layer(pin), datatype=0)
        for label in ordered:
            points = [(float(label.origin[0]), y) for y in y_probes]
            if not all(gdstk.inside(points, polygons)):
                raise RuntimeError(
                    f"{array.name}: {label.text} is discontinuous north/south"
                )

        merged = gdstk.boolean(polygons, [], "or", precision=1e-6)
        roots = []
        for label in ordered:
            hits = [index for index, polygon in enumerate(merged)
                    if polygon.contain(label.origin)]
            if len(hits) != 1:
                raise RuntimeError(
                    f"{array.name}: {label.text} belongs to {len(hits)} components"
                )
            roots.append(hits[0])
        if len(set(roots)) != wordlines:
            raise RuntimeError(f"{array.name}: neighboring {pin} nets are shorted")

    # The two raw 20 nm GATE columns per address use the published cell's
    # north/south overhang and must cross every mirrored row boundary.  GCUT
    # still partitions these columns into the intended electrical gate nets.
    gate_boxes = [bbox(poly) for poly in array.get_polygons(layer=GATE, datatype=0)]
    expected_gate_centers = {
        round(0.027 + index * 0.054, 7)
        for index in range(2 * slots)
    }
    for index in range(1, mux_rows):
        seam = index * pitch_y
        crossing_centers = {
            round((x0 + x1) / 2, 7)
            for x0, y0, x1, y1 in gate_boxes
            if y0 <= seam - 0.007 and y1 >= seam + 0.007
        }
        if crossing_centers != expected_gate_centers:
            raise RuntimeError(
                f"{array.name}: GATE does not overlap north/south at seam {index}"
            )

    # Every mirrored bitcell seam must be occupied by the process layers that
    # form a continuous device-array fabric.  Requiring at least 7 nm on each
    # side catches the former 216 nm centered-core implementation while
    # preserving the official cell's 16 nm minimum ACTIVE overlap.
    seam_half_span = 0.007
    for layer_name, layer in PROCESS_SEAM_LAYERS.items():
        boxes = [bbox(poly) for poly in row.get_polygons(layer=layer, datatype=0)]
        for index in range(1, slots):
            seam = index * pitch_x
            if not any(
                x0 <= seam - seam_half_span and x1 >= seam + seam_half_span
                for x0, _y0, x1, _y1 in boxes
            ):
                raise RuntimeError(
                    f"{row.name}: {layer_name} does not overlap east/west "
                    f"at seam {index}"
                )


def contract_pin_layer(pin: str) -> int:
    """Return the 8T contract layer without duplicating numeric constants."""
    return CONTRACTS[0].pin_layers[pin]


def verify_contract(
    cells: dict[str, gdstk.Cell],
    contract: CellContract,
    wordlines: int,
    mux_rows: int,
    tap_pitch: int = 0,
    strap_pitch: int = 0,
) -> None:
    bitcell = cells[contract.bitcell_name]
    bx0, by0, bx1, by1 = boundary_box(bitcell)
    pitch_x, pitch_y = bx1 - bx0, by1 - by0
    slots, slot_of = slot_layout(wordlines, tap_pitch)
    row = cells[row_name(contract, wordlines, tap_pitch, strap_pitch)]
    array = cells[array_name(contract, wordlines, mux_rows, tap_pitch,
                             strap_pitch)]

    rx0, ry0, rx1, ry1 = boundary_box(row)
    ax0, ay0, ax1, ay1 = boundary_box(array)
    for actual, expected, description in (
        (rx0, 0, "row x0"), (ry0, 0, "row y0"),
        (rx1, slots * pitch_x, "row width"),
        (ry1, pitch_y, "row height"),
        (ax0, 0, "array x0"), (ay0, 0, "array y0"),
        (ax1, slots * pitch_x, "array width"),
        (ay1, mux_rows * pitch_y, "array height"),
    ):
        assert_close(actual, expected, f"{array.name}: {description}")

    first_tap_slots = tap_slots(wordlines, tap_pitch)
    if len(row.references) != wordlines + len(first_tap_slots):
        raise RuntimeError(f"{row.name}: expected {wordlines + len(first_tap_slots)} cell references")
    allowed = {contract.bitcell_name} | ({contract.mirrored_bitcell_name, contract.terminal_mirrored_bitcell_name} - {None})
    # Mirrored slots take the variant, the others the bitcell.
    for index, reference in enumerate(row.references):
        if reference.cell_name in allowed:
            want = (contract.mirrored_bitcell_name if reference.x_reflection
                    and contract.mirrored_bitcell_name else contract.bitcell_name)
            if reference.x_reflection and not tap_pitch and index == wordlines - 1 and contract.terminal_mirrored_bitcell_name:
                want = contract.terminal_mirrored_bitcell_name
            if reference.cell_name != want:
                raise RuntimeError(f"{row.name}: {reference.cell_name} where {want} belongs")
    if tap_pitch:
        allowed |= {TAP_CELL_NAME, STRAP_CELL_NAME}
    if any(reference.cell_name not in allowed
           for reference in row.references):
        raise RuntimeError(f"{row.name}: contains an unexpected reference")

    # Every tap_pitch bitcells must be followed by a tie, and the taps must
    # land on the slots the bitcell origins skip.
    taps = sorted(float(reference.origin[0]) - rx0
                  for reference in row.references
                  if reference.cell_name in (TAP_CELL_NAME, STRAP_CELL_NAME))
    expected_taps = first_tap_slots
    free = sorted(set(range(slots)) - {slot_of[index] for index in range(wordlines)})
    if free != sorted(t + k for t in first_tap_slots for k in range(TAP_SLOTS)):
        raise RuntimeError(f"{row.name}: the bitcells leave slots {free}, not {TAP_SLOTS} per tap")
    if len(taps) != len(expected_taps):
        raise RuntimeError(
            f"{row.name}: expected {len(expected_taps)} taps, found {len(taps)}"
        )
    for actual, slot in zip(taps, expected_taps):
        assert_close(actual, slot * pitch_x, f"{row.name}: tap slot {slot}")

    # Straps must land on the tap slots the pitch selects, and nowhere else.
    want = strap_slots(wordlines, tap_pitch, strap_pitch)
    got = {round((float(r.origin[0]) - rx0) / pitch_x)
           for r in row.references if r.cell_name == STRAP_CELL_NAME}
    if got != want:
        raise RuntimeError(
            f"{row.name}: straps on slots {sorted(got)}, expected {sorted(want)}"
        )
    if len(array.references) != mux_rows:
        raise RuntimeError(f"{array.name}: expected {mux_rows} row references")
    if any(reference.cell_name != row.name for reference in array.references):
        raise RuntimeError(f"{array.name}: contains a non-row reference")

    for pin in contract.wordlines:
        labels = indexed_labels(row, pin)
        if set(labels) != set(range(wordlines)):
            raise RuntimeError(f"{row.name}: incomplete {pin} wordline bus")
        array_labels = indexed_labels(array, pin)
        if set(array_labels) != set(range(wordlines)):
            raise RuntimeError(f"{array.name}: incomplete {pin} wordline bus")
        if any(label.layer != contract.pin_layers[pin]
               for label in (*labels.values(), *array_labels.values())):
            raise RuntimeError(f"{array.name}: {pin} is on the wrong layer")

    for pin in contract.bitlines:
        direct_label(row, pin)
        labels = indexed_labels(array, pin)
        if set(labels) != set(range(mux_rows)):
            raise RuntimeError(f"{array.name}: incomplete {pin} bitline bus")
        if any(label.layer != contract.pin_layers[pin]
               for label in labels.values()):
            raise RuntimeError(f"{array.name}: {pin} is on the wrong layer")

    if contract.key == "8t":
        verify_8t_abutment(
            row, array, wordlines, mux_rows, pitch_x, pitch_y, slots, slot_of
        )


def verify_io_pitch(cells: dict[str, gdstk.Cell], io_gds: Path,
                    wordlines: int, mux_rows: int,
                    tap_pitch: int = 0, strap_pitch: int = 0) -> None:
    if mux_rows != 4:
        return
    io_lib = gdstk.read_gds(str(io_gds))
    io_cells = {cell.name: cell for cell in io_lib.cells}
    array = cells[array_name(CONTRACTS[0], wordlines, mux_rows, tap_pitch,
                             strap_pitch)]
    # Each port's IO column pins its bitlines at the array face, on the centre
    # of the bitcell's bar; the array's label is wherever the bitcell put its
    # own, within that bar.
    mappings = {
        "iocol_sram_8t_a": {"BLA": "BL_A", "BLAN": "BLN_A"},
        "iocol_sram_8t_b": {"BLB": "BL_B", "BLBN": "BLN_B"},
    }
    for io_name, pins in mappings.items():
        if io_name not in io_cells:
            raise RuntimeError(f"{io_gds}: missing {io_name}")
        io = io_cells[io_name]
        assert_close(boundary_box(io)[3], boundary_box(array)[3],
                     f"{io_name}: array/IO height")
        for array_pin, io_pin in pins.items():
            array_labels = indexed_labels(array, array_pin)
            io_labels = indexed_labels(io, io_pin)
            if set(array_labels) != set(io_labels):
                raise RuntimeError(f"{io_name}: incomplete {io_pin} interface")
            for index in array_labels:
                layer = io_labels[index].layer
                half = 0.009 if layer == 20 else 0.012
                offset = abs(float(array_labels[index].origin[1]) - float(io_labels[index].origin[1]))
                if offset > half + 1e-6:
                    raise RuntimeError(
                        f"{io_name}: {io_pin}[{index}] is {offset * 1000:.1f} nm off the array's bar"
                    )
                if array_labels[index].layer != layer:
                    raise RuntimeError(f"{io_name}: {io_pin}[{index}] layer mismatch")


def cell_digest(cell: gdstk.Cell) -> str:
    records: list[str] = []
    for polygon in cell.polygons:
        records.append(f"P:{polygon.layer}:{polygon.datatype}:{bbox(polygon)}")
    for label in cell.labels:
        records.append(
            f"L:{label.text}:{label.layer}:{label.texttype}:"
            f"{tuple(round(float(v), 7) for v in label.origin)}"
        )
    for reference in cell.references:
        records.append(
            f"R:{reference.cell_name}:"
            f"{tuple(round(float(v), 7) for v in reference.origin)}:"
            f"{round(float(reference.rotation or 0), 7)}:"
            f"{int(reference.x_reflection)}"
        )
    return hashlib.sha256("\n".join(sorted(records)).encode()).hexdigest()


def verify_gds(path: Path, wordline_counts: list[int], mux_rows: int,
               io_gds: Path | None, tap_pitch: int = 0,
               strap_pitch: int = 0) -> dict[str, str]:
    """Check the library; ``io_gds`` None skips the IO pitch (no IO columns yet)."""
    library = gdstk.read_gds(str(path))
    cells = {cell.name: cell for cell in library.cells}
    expected = {contract.bitcell_name for contract in CONTRACTS}
    expected |= {name for contract in CONTRACTS for name in
                 (contract.mirrored_bitcell_name, contract.terminal_mirrored_bitcell_name)} - {None}
    if tap_pitch:
        expected.add(TAP_CELL_NAME)
        if strap_pitch:
            expected.add(STRAP_CELL_NAME)
    for contract in CONTRACTS:
        pitch = tap_pitch if contract.key == "8t" else 0
        straps = strap_pitch if contract.key == "8t" else 0
        for count in wordline_counts:
            expected.add(row_name(contract, count, pitch, straps))
            expected.add(array_name(contract, count, mux_rows, pitch, straps))
    if set(cells) != expected:
        missing = sorted(expected - set(cells))
        extra = sorted(set(cells) - expected)
        raise RuntimeError(f"{path}: cell set mismatch; missing={missing}, extra={extra}")

    for contract in CONTRACTS:
        pitch = tap_pitch if contract.key == "8t" else 0
        straps = strap_pitch if contract.key == "8t" else 0
        for count in wordline_counts:
            verify_contract(cells, contract, count, mux_rows, pitch, straps)
            if contract.key == "8t" and io_gds is not None:
                verify_io_pitch(cells, io_gds, count, mux_rows, pitch, straps)
    return {name: cell_digest(cells[name]) for name in sorted(cells)
            if name.startswith(("sramcol_", "array_"))}


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
        "--word-lines",
        type=parse_wordlines,
        default=parse_wordlines("2,32,64,128"),
        help="comma-separated wordline counts (default: 2,32,64,128)",
    )
    parser.add_argument(
        "--mux-rows", type=int, default=4,
        help="number of vertically stacked rows per IO mux (default: 4)",
    )
    parser.add_argument(
        "--8t-gds", dest="gds_8t", type=Path,
        default=repo / "tech/gds/sram_cell_8t.gds",
    )
    parser.add_argument(
        "--6t-gds", dest="gds_6t", type=Path,
        default=repo / "tech/gds/srambank_32b_boundary_2.gds",
    )
    parser.add_argument(
        "--io-gds", type=Path,
        default=repo / "tech/gds/sram_8t_iocolumn.gds",
        help="8T IO wrappers used for four-row pitch verification",
    )
    parser.add_argument(
        "--output", type=Path,
        default=repo / "tech/gds/sram_wordline_arrays.gds",
    )
    parser.add_argument(
        "--tap-gds", type=Path,
        default=repo / "tech/gds/sram_cell_8t_tap.gds",
        help="8T array well/substrate tap cell",
    )
    parser.add_argument(
        "--tap-pitch", type=int, default=0,
        help="insert a well/substrate tap after every N 8T bitcells "
             "(default: 0, no taps)",
    )
    parser.add_argument(
        "--strap-pitch", type=int, default=0,
        help="upgrade every Nth tap to a power strap (default: 0, no straps)",
    )
    parser.add_argument(
        "--verify", type=Path,
        help="verify an existing GDS instead of generating one",
    )
    args = parser.parse_args(argv)
    if args.mux_rows < 1:
        parser.error("--mux-rows must be positive")
    if args.tap_pitch < 0:
        parser.error("--tap-pitch must not be negative")
    if args.tap_pitch and any(count % args.tap_pitch
                              for count in args.word_lines):
        parser.error("--tap-pitch must divide every wordline count")
    if args.strap_pitch < 0:
        parser.error("--strap-pitch must not be negative")
    if args.strap_pitch and not args.tap_pitch:
        parser.error("--strap-pitch needs --tap-pitch")
    return args


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        if args.verify:
            digests = verify_gds(
                args.verify, args.word_lines, args.mux_rows, args.io_gds,
                args.tap_pitch, args.strap_pitch,
            )
            print(f"PASS {args.verify}: {len(digests)} parameterized cells")
        else:
            library = build_library(
                args.gds_8t, args.gds_6t, args.word_lines, args.mux_rows,
                args.tap_gds, args.tap_pitch, args.strap_pitch,
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            library.write_gds(str(args.output), timestamp=FIXED_GDS_TIMESTAMP)
            # The IO columns are built from these arrays: on a first build
            # there are none to check the pitch against yet (scripts/tech_gds.py
            # verifies again once they exist).
            io_gds = args.io_gds if args.io_gds.is_file() else None
            if io_gds is None:
                print(f"note: {args.io_gds} not found; IO pitch not checked")
            digests = verify_gds(
                args.output, args.word_lines, args.mux_rows, io_gds,
                args.tap_pitch, args.strap_pitch,
            )
            print(f"wrote {args.output}: {len(digests)} parameterized cells")
        for name, digest in digests.items():
            print(f"  {name}: sha256={digest}")
        return 0
    except (OSError, RuntimeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
