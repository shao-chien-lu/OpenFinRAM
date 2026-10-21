#!/usr/bin/env python3
"""Assemble and route the 2RW macro from real ASAP7 hard-cell geometry.

Every disconnected supply component is a separate routing terminal.  Physical
pin connectivity is extracted again after DEF stream-out before publishing GDS.
The controller is the already placed/routed ctrl_decode from this compiler run.

A column is one unsplit array with port A's IO at one end of the bitlines and
port B's at the other.  ``--wordlines`` is still NUM_WL, the rows one row-select
address field covers; the array has twice that, the address bit above the
column select being the top bit of its wordline index.

The data bits are two stacks of abutted column tiles with the controller band
between them.  Each stack's wordlines run through its tiles by abutment and
are driven at its edge by a pair of driver strips (chipforge_asap7's
DriverSliceSpec, four wordlines a slice): port A's strip against the array,
port B's flipped under it sharing a VSS rail and reaching the M5 wordlines
through a via stack and strap.  Those wordline nets are never routed: the
connectivity gate proves them through the abutments instead.

With ``--share-port-b`` the banks go in pairs and a tile is a pair's column
for one bit: ``port A IO | array | port B IO | array | port A IO``, the second
bank's half the first's mirrored, so one two-sided port-B block serves both
arrays.  The second bank's strips are the first's mirrored the same way.
"""

from __future__ import annotations

import argparse
import dataclasses
from collections import Counter, defaultdict
import functools
import itertools
import json
import math
from pathlib import Path
import re
import subprocess

import gdstk
from chipforge_asap7.verification.connectivity import MetalGraph, METALS
import generate_asap7_wordline_arrays as arrays
import generate_asap7_8t_iocolumn as columns
import generate_asap7_8t_bitcell as edge_cells
import generate_asap7_8t_wl_slices as wl_slices

REPO = Path(__file__).resolve().parents[1]
LAYER_NAMES = dict(zip(METALS, (f"M{i}" for i in range(1, 10))))
#: Each metal's minimum width, um.
MIN_WIDTH_UM = dict(zip(METALS, (0.018, 0.018, 0.018, 0.024, 0.024, 0.032, 0.032, 0.040, 0.040)))
#: A driver slice: four wordlines at the array's pitch, its outputs 54 nm in.
SLICE_WIDTH, WORDLINE_PITCH = 0.432, 0.108
#: M3 past a V3, along the column (chipforge's V3_M3_CAP), um.
V3_M3_CAP_UM = 0.017
#: The array centres a fin on its edge, the slices a fin space on theirs: this
#: far apart both fin grids are on pitch.
FIN_HALF_PITCH = 0.0135
WORDLINE = re.compile(r"WL_?([AB])\[(\d+)\]")
#: A mid strip pair's outputs: into the segment below (D) and above (U).
SEGMENT_WORDLINE = re.compile(r"WL_([DU])\[(\d+)\]")


def supply(text):
    name = text.lower().rstrip("!")
    return name if name in ("vdd", "vss") else None


def probe_point(polygon, offset):
    """Choose an interior point that survives GDS grid conversion."""
    inset = gdstk.offset([polygon], -0.0005, precision=1e-7)
    if not inset:
        raise RuntimeError("conductor too narrow for a physical connectivity probe")
    return tuple(float(v) - o for v, o in zip(inset[0].points[0], offset))


def bbox_origin(cell):
    """Where `abstract` puts the cell: its bounding box's corner, on the nm grid."""
    low, _ = cell.bounding_box()
    return tuple(math.floor(float(v) * 1000) / 1000 for v in low)


#: How far a tile's supply pins stay inside its abutting edges (`abstract`).
SUPPLY_PIN_INSET = 0.05


def abstract(cell, name, signal_labels, abutted=None, shift=(0.0, 0.0), abutted_supply=None,
             snap_half_nm=False, supply_span=None):
    """Create an exact metal abstract and a pin for every named component.

    Nets `abutted` names are joined by abutment, never routed: they are
    terminals for the connectivity gate but obstructions to the router.
    `abutted_supply` does the same for the supply components whose
    polygons it accepts.
    `shift` moves the cell inside its master (and everything the LEF says
    of it), for a cell that has to land between the DEF's nanometres.
    `snap_half_nm` snaps the LEF's obstructions out the same way for a cell
    that sits on the grid but draws half-nanometre metal (the 6T bitlines'
    M4 across its IO block): OpenROAD would round them inward and route
    23.5 nm from a 24 nm rule.  Its pins stay as drawn: snapped in, a
    supply pin would be half a nanometre short of its metal and the router
    would land beside it that much too close (x64x64x1's M1 on vdd).
    `supply_span` ``(y_lo, y_hi)`` keeps the supply pins `SUPPLY_PIN_INSET`
    inside those edges (the rest of their metal is an obstruction), so that
    no two abutted instances' pins of one net overlap: the router takes
    overlapping pins as joined, its connectivity check does not
    (x8x4x1 at 16:1, a stack of two end tiles under the strips: DRT-0206 on vss).
    """
    graph = MetalGraph(cell)
    roots = {}
    for label in signal_labels:
        root = graph.label_root(label)
        if root in roots and roots[root] != label.text:
            raise RuntimeError(f"{name}: short {roots[root]} / {label.text}")
        roots[root] = label.text
    for label in cell.get_labels():
        net = supply(label.text)
        if net is None or label.layer not in METALS:
            continue
        root = graph.label_root(label)
        if root in roots and roots[root] != net:
            raise RuntimeError(f"{name}: short {roots[root]} / {net}")
        roots[root] = net
    if not {"vdd", "vss"}.issubset(roots.values()):
        raise RuntimeError(f"{name}: missing supply conductors")

    # Retain overhangs inside the abstract boundary; never hide device geometry
    # from the router by using only the smaller SRAM abutment rectangle.
    low, high = cell.bounding_box()
    ox, oy = (math.floor(float(v) * 1000) / 1000 for v in low)
    width, height = (
        math.ceil((float(v) - o) * 1000) / 1000 for v, o in zip(high, (ox, oy))
    )
    ox, oy = ox - shift[0], oy - shift[1]
    master = gdstk.Cell(name)
    master.add(gdstk.Reference(cell, origin=(-ox, -oy)))
    groups = defaultdict(list)
    for i, polygon in enumerate(graph.polygons):
        if polygon.layer in METALS:
            groups[graph.root(i)].append(polygon)
    terminals = {}
    obstacles = [
        p for root, polys in groups.items() if root not in roots for p in polys
    ]
    lef = [
        f"MACRO {name}",
        "  CLASS BLOCK ;",
        "  ORIGIN 0 0 ;",
        f"  SIZE {width:.6f} BY {height:.6f} ;",
        "  SYMMETRY X Y ;",
    ]

    # A shifted cell's metal has edges half a nanometre off what the LEF can
    # say.  Snap each shape to the nanometre the safe way: an obstruction
    # out (the router never sees less metal than there is), a pin in (it
    # never lands beyond the metal).  Each moves only the half it has: a
    # quarter-nanometre offset then rounding leaves an edge on the grid
    # where it is and takes a half-nanometre edge the chosen way.
    snap = 0.00025 if any(shift) or snap_half_nm else 0.0
    snap_pins = bool(any(shift))

    def geometry(polygons, outward=True, snapped=True):
        out = []
        by_layer = defaultdict(list)
        for poly in polygons:
            by_layer[poly.layer].append(poly)
        for layer, polys in sorted(by_layer.items()):
            out.append(f"    LAYER {LAYER_NAMES[layer]} ;")
            merged = gdstk.boolean(polys, [], "or", precision=1e-6)
            if snap and snapped:
                shapes = []
                for poly in merged:
                    moved = gdstk.offset(poly, snap if outward else -snap, join="miter", precision=1e-7)
                    if not outward and any(min(b - a for a, b in zip(*q.bounding_box()))
                                           < MIN_WIDTH_UM[layer] - 1e-6 for q in moved):  # fmt: skip
                        # A minimum-width pin on half nanometres would come out
                        # a nanometre narrower than its layer allows, and the
                        # router finds no access on it (6T band, M4 at 23 nm).
                        moved = gdstk.offset(poly, snap, join="miter", precision=1e-7)
                    shapes += moved
                merged = shapes
            for poly in merged:
                coords = [(x - ox, y - oy) for x, y in poly.points]
                if snap and snapped:
                    coords = [(round(x * 1000) / 1000, round(y * 1000) / 1000) for x, y in coords]
                points = " ".join(f"{x:.6f} {y:.6f}" for x, y in coords)
                out.append(f"    POLYGON {points} ;")
        return out

    for root, net in sorted(roots.items(), key=lambda item: (item[1], item[0])):
        pin = f"P{len(terminals)}"
        polys = groups[root]
        if (abutted is not None and abutted(net)) or (
            net in ("vdd", "vss") and abutted_supply is not None and abutted_supply(polys)
        ):
            obstacles.extend(polys)
            polygon = max(polys, key=lambda p: p.area())
            terminals[pin] = {
                "net": net,
                "layer": polygon.layer,
                "point": probe_point(polygon, (ox, oy)),
                "abutted": True,
            }
            continue
        if name == "dp_controller" and net not in ("vdd", "vss"):
            labels = [label for label in signal_labels if label.text == net]
            access_layers = {label.layer for label in labels}
            # Include the connected landing wire, not just its pin marker:
            # marking its immediately adjacent same-net extension as OBS
            # creates artificial spacing violations during via access.
            access = [p for p in polys if p.layer in access_layers]
            if not access:
                raise RuntimeError(
                    f"{name}: no explicit physical pin landing for {net}"
                )
            obstacles.extend(p for p in polys if p.layer not in access_layers)
        elif net in ("vdd", "vss"):
            # A supply component is reachable on every layer it has: the
            # router lands on a rail or a bar, not on the top stub of a via
            # stack squeezed between the wordlines.  A shifted cell offers
            # only what lands on the DEF's nanometre grid: M4 up may not
            # bend, and a landing half a nanometre off a shape leaves a
            # sliver on any layer.
            access, exact = polys, True
            if any(shift):
                on_grid = [p for p in polys if p.layer in METALS[:3] and all(
                    abs(v * 1000 - round(v * 1000)) < 1e-3
                    for x, y in p.points for v in (x - ox, y - oy))]  # fmt: skip
                access = on_grid or [p for p in polys if p.layer in METALS[:3]] or polys
                # Nothing on the grid (a dummy row's tied stubs): narrowing
                # its 18 nm landings to 17 would leave no access; let the
                # reader round them whole instead.
                exact = bool(on_grid)
                # Vertical straps (M3, M5) are whole across: only their ends
                # move, inward.  The 6T tile's half-nanometre rails leave only
                # stubs in its IO block's M1 on the grid, and a route there
                # never converged (6T band, x64x8x1); an 8T dummy row's tied
                # stubs meet only on their M5 strap, and offered M1-M3 alone
                # the router joined their pieces to each other and to nothing
                # else (x4x2x1: vss in two).
                straps = [p for p in polys if p.layer in (METALS[2], METALS[4]) and all(
                    abs((x - ox) * 1000 - round((x - ox) * 1000)) < 1e-3 for x, _ in p.points)]  # fmt: skip
                if straps and (snap_half_nm or any(p.layer == METALS[4] for p in straps)):
                    access, exact = straps, True
            elif snap_half_nm:
                # The 6T end row's M1 comb is half-nanometre metal with
                # 4 nm steps; a router wire landed on it leaves a notch
                # (x64x64x1: M1 spacing on vdd, the route never converged).
                # Its M2 rail is whole: land there, keep the comb as metal
                # to clear.
                above = [p for p in polys if p.layer != METALS[0] and all(
                    abs(v * 1000 - round(v * 1000)) < 1e-3
                    for x, y in p.points for v in (x - ox, y - oy))]  # fmt: skip
                access = above or polys
            obstacles.extend(p for p in polys if not any(p is a for a in access))
            if supply_span is not None:
                y_lo, y_hi = supply_span
                lo, hi = y_lo + SUPPLY_PIN_INSET, y_hi - SUPPLY_PIN_INSET
                x0, x1 = (float(v) for v in (low[0], high[0]))
                keep = gdstk.rectangle((x0 - 1, lo), (x1 + 1, hi))
                inside, outside = [], []
                for poly in access:
                    inside += [gdstk.Polygon(q.points, layer=poly.layer)
                               for q in gdstk.boolean(poly, keep, "and", precision=1e-6)]
                    outside += [gdstk.Polygon(q.points, layer=poly.layer)
                                for q in gdstk.boolean(poly, keep, "not", precision=1e-6)]
                # A component wholly at an edge (an end row's bar) keeps its pin whole.
                if inside:
                    access = inside
                    obstacles.extend(outside)
        else:
            access_layer = max(p.layer for p in polys)
            access = [p for p in polys if p.layer == access_layer]
            obstacles.extend(p for p in polys if p.layer != access_layer)
        # Signal use is intentional: even the supply trees must be physically
        # routed here, not collapsed into unconnected global/special nets.
        direction = (
            "OUTPUT"
            if (
                net.startswith(("QA", "QB"))
                or (
                    name == "dp_controller"
                    and net.startswith(
                        ("sel_", "wrena", "ysel", "sae", "oe_out", "oeb_out", "blprech")
                    )
                )
            )
            else "INPUT"
        )
        if net in ("vdd", "vss"):
            direction = "INOUT"
        lef += [
            f"  PIN {pin}",
            f"    DIRECTION {direction} ;",
            "    USE SIGNAL ;",
            "    PORT",
        ]
        # The 6T tile's signal pins stay as drawn even shifted, as unshifted:
        # the router centres its via on a half-nanometre pin and lands flush,
        # where on a snapped one it would leave a half-nanometre step
        # (M4.AUX.3), or find no access on 23 nm (DRT-0073).
        supply_net = net in ("vdd", "vss")
        lef += geometry(access, outward=False, snapped=snap_pins and (
            exact if supply_net else not snap_half_nm))
        lef += ["    END", f"  END {pin}"]
        polygon = max(access, key=lambda p: p.area())
        point = probe_point(polygon, (ox, oy))
        terminals[pin] = {"net": net, "layer": polygon.layer, "point": point}
    lef += ["  OBS"]
    lef += geometry(obstacles)
    lef += ["  END", f"END {name}"]
    # Freeze the partition of unnamed internal interconnect too. A route that
    # shorts into a storage node or a controller internal net must fail even
    # if all exported pins still appear mutually isolated.
    for root, polys in groups.items():
        if root in roots:
            continue
        polygon = max(polys, key=lambda p: p.area())
        point = probe_point(polygon, (ox, oy))
        terminals[f"internal_{root}"] = {
            "net": f"internal_{root}",
            "layer": polygon.layer,
            "point": point,
            "private": True,
        }
    # The GDS boundary is the cell's own placement boundary where it has one,
    # so abutted blocks show one clean outline; the LEF SIZE above stays the
    # bounding box, overhangs included, which is what the router must see.
    own = [p for p in cell.polygons if p.layer == 100 and p.datatype == 0]
    if own:
        outline = max(own, key=lambda p: p.area())
        master.add(gdstk.Polygon([(x - ox, y - oy) for x, y in outline.points], layer=100))
    else:
        master.add(gdstk.rectangle(shift, (width + shift[0], height + shift[1]), layer=100))
    return master, terminals, "\n".join(lef), (width, height)


def half_of(bit, bits):
    """Which stack a data bit is in: `lo` below the controller band, `hi` above."""
    return "lo" if bit < bits // 2 else "hi"


def wordline_bus(port, half, segment=None):
    """A stack's wordline bus, or a segment's: ``wl_A_lo``, ``wl_A_lo_s1``."""
    return f"wl_{port}_{half}" + ("" if segment is None else f"_s{segment}")


def leaf_net(name, bank, bit, wordlines, bits, shared_b=False, segment=None, mux=4):
    """Macro net of a column pin; `wordlines` is NUM_WL, half the array's.

    A pair tile (`shared_b`, `bank` its first) names the second bank's
    wordlines and selects by continuing the first's indices, and its
    one-per-bank controls with an ``R``; port B's enables are the pair's.
    """
    if name in ("vdd", "vss"):
        return name
    if name in ("DA", "DB", "QA", "QB"):
        return f"{name[0]}_{name[1]}[{bit}]"
    wl = WORDLINE.fullmatch(name)
    if wl:
        return f"{wordline_bus(wl[1], half_of(bit, bits), segment)}[{bank * 2 * wordlines + int(wl[2])}]"
    # A bank's select bus: a line a column, or from 16:1 (6T) the two
    # predecoded groups the controller's YSEL_W carries.
    stride = 4 + mux // 4 if mux >= 16 else mux
    mux = re.fullmatch(r"(yseln|ysel)([AB])\[(\d+)\]", name)
    if mux:
        return f"{mux[1]}_{mux[2]}[{bank * stride + int(mux[3])}]"
    ctrl = re.fullmatch(r"(wrena|wrenan|oeb_out|oe_out|blprechn|sae_)([AB])(R?)", name)
    if ctrl:
        signal = ctrl[1].rstrip("_")
        if shared_b and ctrl[2] == "B" and signal != "blprechn":
            return f"{signal}_B[{bank // 2}]"
        return f"{signal}_{ctrl[2]}[{bank + (1 if ctrl[3] else 0)}]"
    raise RuntimeError(f"unmapped column pin {name}")


def strip_net(name, bank, half, wordlines, segment=None, below=None, above=None):
    """Macro net of a strip pair pin: the controller's predecode lines in, wordlines out.

    A band pair drives `segment` of its stack (None: the stack is one
    segment); a mid pair drives port A's segments `below` and `above` it.
    """
    if name in ("vdd", "vss"):
        return name
    sel = re.fullmatch(r"SEL_([ABDU])\[(\d+)\]", name)
    if sel:
        port = sel[1] if sel[1] in "AB" else "A"
        return f"sel_hi_{port}[{bank * (2 * wordlines // 4) + int(sel[2])}]"
    low = re.fullmatch(r"B_([ABDU])\[(\d+)\]", name)
    if low:
        port = low[1] if low[1] in "AB" else "A"
        return f"sel_lo_{port}[{low[2]}]"
    wl = WORDLINE.fullmatch(name)
    if wl:
        return f"{wordline_bus(wl[1], half, segment)}[{bank * 2 * wordlines + int(wl[2])}]"
    mid = SEGMENT_WORDLINE.fullmatch(name)
    if mid:
        target = below if mid[1] == "D" else above
        return f"{wordline_bus('A', half, target)}[{bank * 2 * wordlines + int(mid[2])}]"
    raise RuntimeError(f"unmapped strip pin {name}")


def outer_boundary(cell):
    """The cell's boundary at any depth: a ladder wrapper's is its spec cell's."""
    boxes = [
        poly.bounding_box()
        for poly in cell.get_polygons(depth=None)
        if poly.layer == 100 and poly.datatype == 0
    ]
    if not boxes:
        raise RuntimeError(f"{cell.name}: no BOUNDARY polygon")
    (x0, y0), (x1, y1) = max(
        boxes, key=lambda b: (b[1][0] - b[0][0]) * (b[1][1] - b[0][1])
    )
    return tuple(round(float(v), 7) for v in (x0, y0, x1, y1))


def wordline_tracks(cell):
    """x of every wordline of a column tile, by port and index, from its labels."""
    tracks = {}
    for label in cell.labels:
        wl = WORDLINE.fullmatch(label.text)
        if wl:
            tracks[(wl[1], int(wl[2]))] = round(float(label.origin[0]), 4)
    return tracks


def build_wordline_strips(lib, leaf, wordlines, bits, ports=("A", "B"), gate_reach=0.0, segment_bits=None,
                          mux=4, rail_on_grid=None):
    """The strip pair for each side of the controller band, in the tile's x.

    A strip is one slice per four of the tile's port-A wordlines, the slice's
    outputs on them, with a filler wherever a slice has no neighbour (the
    array's tap columns and the row's ends).  The pair puts port A's strip
    against the array and port B's flipped under it on a shared VSS rail;
    port B's outputs climb to the tile's M5 wordlines on a VIA34/VIA45 stack
    and an M5 strap.  Both strips sit half a fin pitch in from the pair's
    edges so their fins and the array's are on one grid, port A's outputs
    bridged to the edge on M3.  Returns ``{"lo": cell, "hi": cell}`` and the
    load class the slices were sized to.

    With one port (``ports=("A",)``, the single-port 6T tile) the "pair" is
    port A's strip alone.  `gate_reach` carries the gate bridges that far
    past the pair's edge, into a tile whose own gates stop short of its
    edge (the 6T dummy row ends half a fin pitch inside the tile).

    With `segment_bits` the stacks' wordlines are cut into segments of that
    many data bits, and the slices are sized for a segment.  A third cell,
    ``mid``, goes between two segments: one strip flipped facing down into
    the segment below, one facing up into the segment above, on a shared
    VSS rail (the two-port pair's arrangement), its outputs labelled
    ``WL_D``/``WL_U``.
    """
    cells_along = mux * (segment_bits or bits // 2)
    load = wl_slices.load_class(cells_along)
    ladder = {
        c.name: c
        for c in gdstk.read_gds(str(REPO / "tech/gds/sram_8t_wl_slices.gds")).cells
    }
    slice_cell, filler = (
        ladder[wl_slices.slice_name(load)],
        ladder[wl_slices.slice_name(load) + "_filler"],
    )
    have = {c.name for c in lib.cells}
    for cell in (slice_cell, filler):
        for dep in (cell, *cell.dependencies(True)):
            if dep.name not in have:
                lib.add(dep)
                have.add(dep.name)
    rows = 2 * wordlines
    if rows % 4:
        raise RuntimeError(
            "the array's wordlines must be a multiple of four, one driver slice each"
        )
    tracks = wordline_tracks(leaf)
    if len(tracks) != len(ports) * rows:
        raise RuntimeError(
            f"expected {len(ports) * rows} wordline labels on the tile, found {len(tracks)}"
        )
    height = outer_boundary(slice_cell)[3]
    pins = {label.text: (float(label.origin[0]), float(label.origin[1]), label.layer)
            for label in slice_cell.labels}  # fmt: skip

    strip = lib.new_cell(f"wl_strip_c{load}_x{rows // 4}")
    starts = []
    for k in range(rows // 4):
        x0 = tracks[("A", 4 * k)] - WORDLINE_PITCH / 2
        for j in range(4):
            want, got = (
                tracks[("A", 4 * k + j)],
                x0 + WORDLINE_PITCH / 2 + WORDLINE_PITCH * j,
            )
            if abs(want - got) > 1e-6:
                raise RuntimeError(
                    f"slice {k}: WL{4 * k + j} is at {want}, the slice's output at {got}"
                )
        strip.add(gdstk.Reference(slice_cell, (x0, 0)))
        starts.append(x0)
        sx, sy, layer = pins["SEL"]
        strip.add(gdstk.Label(f"SEL[{k}]", (x0 + sx, sy), layer=layer, texttype=251))
        for j in range(4):
            x, y, layer = pins[f"WL{j}"]
            strip.add(
                gdstk.Label(f"WL[{4 * k + j}]", (x0 + x, y), layer=layer, texttype=251)
            )
    # Every slice's B<j> is its own metal: the slices' predecode inputs do
    # not join across a slice boundary, so each is a pin and the router ties
    # them to the controller's sel_lo<j>.
    for x0_slice in starts:
        for j in range(4):
            x, y, layer = pins[f"B{j}"]
            strip.add(
                gdstk.Label(f"B[{j}]", (x0_slice + x, y), layer=layer, texttype=251)
            )
    if ports == ("A",) and len(starts) > 1:  # one slice has nothing to join
        _predecode_rails(strip, starts, pins, RAIL_ON_GRID if rail_on_grid is None else rail_on_grid)
    spans = [(x0, x0 + SLICE_WIDTH) for x0 in starts]
    gaps = [
        (spans[0][0] - WORDLINE_PITCH, spans[0][0]),
        (spans[-1][1], spans[-1][1] + WORDLINE_PITCH),
    ]
    gaps += [
        (left[1], right[0])
        for left, right in zip(spans, spans[1:])
        if right[0] - left[1] > 1e-6
    ]
    for g0, g1 in gaps:
        count = round((g1 - g0) / WORDLINE_PITCH)
        if abs((g1 - g0) - count * WORDLINE_PITCH) > 1e-6:
            raise RuntimeError(
                f"a {round((g1 - g0) * 1000)} nm gap between slices is not whole fillers"
            )
        for c in range(count):
            strip.add(gdstk.Reference(filler, (g0 + c * WORDLINE_PITCH, 0)))
    x_left, x_right = spans[0][0] - WORDLINE_PITCH, spans[-1][1] + WORDLINE_PITCH

    # OpenROAD's ASAP7 default via stacks, drawn as cells so the pair is one place to look.
    via34 = lib.new_cell("wl_via34")
    via34.add(gdstk.rectangle((-0.020, -0.012), (0.020, 0.012), layer=40),
              gdstk.rectangle((-0.009, -0.017), (0.009, 0.017), layer=30),
              gdstk.rectangle((-0.009, -0.012), (0.009, 0.012), layer=35))  # fmt: skip
    via45 = lib.new_cell("wl_via45")
    via45.add(gdstk.rectangle((-0.012, -0.023), (0.012, 0.023), layer=50),
              gdstk.rectangle((-0.023, -0.012), (0.023, 0.012), layer=40),
              gdstk.rectangle((-0.012, -0.012), (0.012, 0.012), layer=45))  # fmt: skip

    # The strip's gate tracks (slices and fillers share the array's 54 nm
    # grid).  The array's gate stubs reach 7 or 19 nm past its boundary and
    # the strip's 5 nm past its own, so the fin-grid margin would leave a
    # short gap on a track; bridge each track across the margin, under the
    # slice's gate cut, so the abutment reads as one poly line.
    gate_tracks = sorted(
        {
            round(
                (float(poly.bounding_box()[0][0]) + float(poly.bounding_box()[1][0]))
                / 2,
                4,
            )
            for poly in strip.get_polygons(depth=None)
            if poly.layer == 7
        }
    )
    pairs = {}
    if ports == ("A",):
        pair_height = height + 2 * FIN_HALF_PITCH
        seam = FIN_HALF_PITCH  # port A's strip from here up, facing the array
    else:
        pair_height = 2 * height + 2 * FIN_HALF_PITCH
        seam = height + FIN_HALF_PITCH  # the shared VSS rail
    for half, facing_up in (("lo", False), ("hi", True)):
        pair = lib.new_cell(f"wl_strips_{half}_c{load}_x{rows // 4}")

        def at(y):  # the `hi` pair is drawn; the `lo` pair is its mirror image
            return y if facing_up else pair_height - y

        def rect(x0, y0, x1, y1, layer):
            ys = sorted((at(y0), at(y1)))
            pair.add(gdstk.rectangle((x0, ys[0]), (x1, ys[1]), layer=layer))

        def label(text, x, y, layer):
            pair.add(gdstk.Label(text, (x, at(y)), layer=layer, texttype=251))

        for port, flipped in (("A", False), ("B", True))[: len(ports)]:
            pair.add(
                gdstk.Reference(
                    strip, (0, at(seam)), x_reflection=flipped != (not facing_up)
                )
            )
            sign = -1 if flipped else 1
            for k in range(rows // 4):
                sx, sy, layer = pins["SEL"]
                label(f"SEL_{port}[{k}]", starts[k] + sx, seam + sign * sy, layer)
            for x0_slice in starts:  # every slice's, as in the strip
                for j in range(4):
                    x, y, layer = pins[f"B{j}"]
                    label(f"B_{port}[{j}]", x0_slice + x, seam + sign * y, layer)
        for i in range(rows):
            x = tracks[("A", i)]
            rect(x - 0.009, seam + height, x + 0.009, pair_height, 30)
            label(f"WL_A[{i}]", x, pair_height - 0.005, 30)
            if "B" not in ports:
                continue
            x5 = tracks[("B", i)]
            y_via = seam - height + 0.025
            for cell, vx in ((via34, x), (via45, x5)):
                pair.add(gdstk.Reference(cell, (vx, at(y_via))))
            rect(x5 - 0.012, y_via - 0.023, x5 + 0.012, pair_height, 50)
            label(f"WL_B[{i}]", x5, pair_height - 0.005, 50)
        for x in gate_tracks:
            rect(x - 0.010, seam + height, x + 0.010, pair_height + gate_reach, 7)
        pair.add(gdstk.rectangle((x_left, 0), (x_right, pair_height), layer=100))
        pairs[half] = pair
    if segment_bits and segment_bits < bits // 2:
        if ports != ("A",):
            raise RuntimeError("wordline segments are for the single-port macro so far")
        pairs["mid"] = _mid_strip_pair(lib, strip, starts, pins, tracks, gate_tracks, rows, height,
                                       x_left, x_right, gate_reach, load)  # fmt: skip
    return pairs, load


#: Port B's via stacks in a strip pair (`build_wordline_strips`): the cells,
#: and the M3 spacings that bound how far a stack's M3 stub may grow to reach
#: an M4 track: tip to tip (M3.S.4), corner to corner (M3.S.6).
STRIP_VIAS = ("wl_via34", "wl_via45")
M3_TIP_SPACE, M3_CORNER_SPACE = 0.031, 0.020


def strip_via_window(pair):
    """Where port B's via row may go in `pair`: ``(y, lo, hi, stacks)``, um.

    `y` is the row's centre as drawn; ``[lo, hi]`` the centres every stack's
    V3 can take, its M3 stub grown to cover it (`V3_M3_CAP` past it) and kept
    clear of the strip's other M3 (`M3_TIP_SPACE`, `M3_CORNER_SPACE`) and
    inside the pair; `stacks` each stack's ``(x of its V3, x of its V4)``.
    None for a pair without them (the single-port strip).
    """
    refs = [r for r in pair.references if r.cell.name in STRIP_VIAS]
    if not refs:
        inner = _mirrored_pair(pair)
        if inner is None or (window := strip_via_window(inner.cell)) is None:
            return None
        # `mirrored`'s pair: x -> its origin - x, y as drawn.
        y, lo, hi, stacks = window
        x = float(inner.origin[0])
        return y, lo, hi, [(x - a, x - b) for a, b in stacks]
    y = round(float(refs[0].origin[1]), 6)
    xs3 = sorted(float(r.origin[0]) for r in refs if r.cell.name == "wl_via34")
    xs4 = sorted(float(r.origin[0]) for r in refs if r.cell.name == "wl_via45")
    m3 = [tuple(float(v) for corner in q.bounding_box() for v in corner)
          for q in pair.get_polygons(layer=30, datatype=0)]  # fmt: skip
    _, bottom, _, top = columns.boundary_box(pair)
    lo, hi = bottom + V3_M3_CAP_UM, top - V3_M3_CAP_UM
    for x in xs3:
        stub = [b for b in m3 if b[0] <= x <= b[2] and b[1] <= y <= b[3]]
        s0, s1 = min(b[0] for b in stub), max(b[2] for b in stub)
        t0, t1 = min(b[1] for b in stub), max(b[3] for b in stub)
        for a0, b0, a1, b1 in m3:
            gap = max(a0 - s1, s0 - a1)
            if gap >= M3_CORNER_SPACE or (b1 > t0 and b0 < t1):
                continue  # far enough across, or beside the stub already
            reach = M3_TIP_SPACE if gap < 0 else math.sqrt(M3_CORNER_SPACE**2 - gap**2)
            if b0 >= t1:
                hi = min(hi, b0 - reach - V3_M3_CAP_UM)
            elif b1 <= t0:
                lo = max(lo, b1 + reach + V3_M3_CAP_UM)
    return y, math.ceil(lo * 2000) / 2000, math.floor(hi * 2000) / 2000, list(zip(xs3, xs4))


def _mirrored_pair(pair):
    """The one reference of a `mirrored` pair (to the pair as drawn), else None."""
    refs = pair.references
    if len(refs) == 1 and refs[0].x_reflection and abs(refs[0].rotation - math.pi) < 1e-9:
        return refs[0]
    return None


def strip_via_row(window, track):
    """The M4 track (`track` nm past a multiple of 48) in `window` (`strip_via_window`)
    nearest its drawn row, um; None if there is none."""
    y, lo, hi, _ = window
    k_lo, k_hi = math.ceil((lo * 1000 - track) / 48 - 1e-9), math.floor((hi * 1000 - track) / 48 + 1e-9)
    if k_lo > k_hi:
        return None
    return min(((track + 48 * k) / 1000 for k in range(k_lo, k_hi + 1)), key=lambda v: abs(v - y))


def retrack_strip_vias(pair, track, name):
    """A copy of `pair` named `name` with port B's via row on the M4 track nearest its
    drawn row, the tracks `track` nm past a multiple of 48 in its frame; None when no
    track is in reach (`strip_via_window`) or the pair has no via row."""
    window = strip_via_window(pair)
    if window is None:
        return None
    if (inner := _mirrored_pair(pair)) is not None:
        moved = retrack_strip_vias(inner.cell, track, inner.cell.name + name.removeprefix(pair.name))
        if moved is None:
            return None
        out = gdstk.Cell(name)
        out.add(gdstk.Reference(moved, inner.origin, inner.rotation, x_reflection=inner.x_reflection))
        out.add(*[q.copy() for q in pair.polygons], *[label.copy() for label in pair.labels])
        return out
    y, _, _, stacks = window
    new = strip_via_row(window, track)
    if new is None:
        return None
    out = pair.copy(name)
    for ref in out.references:
        if ref.cell.name in STRIP_VIAS:
            ref.origin = (ref.origin[0], new)
    for x3, x4 in stacks:
        # The M5 strap from the stack to the pair's far edge; the M3 stub to the V3.
        strap = next(q for q in out.polygons if q.layer == 50
                     and abs((q.bounding_box()[0][0] + q.bounding_box()[1][0]) / 2 - x4) < 1e-6
                     and q.bounding_box()[0][1] - 1e-6 <= y <= q.bounding_box()[1][1] + 1e-6)  # fmt: skip
        (a, b), (c, d) = strap.bounding_box()
        out.remove(strap)
        far_up = d - y > y - b
        out.add(gdstk.rectangle((a, new - 0.023 if far_up else b), (c, d if far_up else new + 0.023), layer=50))
        out.add(gdstk.rectangle((x3 - 0.009, new - V3_M3_CAP_UM), (x3 + 0.009, new + V3_M3_CAP_UM), layer=30))
    return out


def block_strip_via_window(lef, pair, shift):
    """`lef` (`pair`'s abstract) with its via row's reach obstructed on M3 and M4.

    The band's controller is routed around the strips before the macro knows
    which track their via rows land on (`retrack_strip_vias`).
    """
    window = strip_via_window(pair)
    if window is None:
        return lef
    _, lo, hi, stacks = window
    low, _ = pair.bounding_box()
    ox, oy = (math.floor(float(v) * 1000) / 1000 - d for v, d in zip(low, shift))
    rects = ["    LAYER M4 ;"]
    x0 = min(x for stack in stacks for x in stack) - 0.024
    x1 = max(x for stack in stacks for x in stack) + 0.024
    rects.append(f"    RECT {x0 - ox:.4f} {lo - 0.012 - oy:.4f} {x1 - ox:.4f} {hi + 0.012 - oy:.4f} ;")
    rects.append("    LAYER M3 ;")
    for x3, _ in stacks:
        rects.append(f"    RECT {x3 - 0.009 - ox:.4f} {lo - V3_M3_CAP_UM - oy:.4f} "
                     f"{x3 + 0.009 - ox:.4f} {hi + V3_M3_CAP_UM - oy:.4f} ;")  # fmt: skip
    return lef.replace("  OBS\n", "  OBS\n" + "\n".join(rects) + "\n", 1)


#: Each predecode input's join to its M4 rail, in the slice: (x of the
#: V2/V3 stack, the rail's offset from the pin's M2 row).  B0 and B1 stack
#: straight up (B1 left of the M3 line crossing its row); B2 and B3, which
#: share those rows, step 48 nm down on an M3 stub between the slice's
#: 207 and 288 nm M3 lines (20 nm corner to corner from the first, 25 from
#: the second's long edge).  The same for every slice of the ladder: the
#: c64 slice is taller, but its rows sit at the labels as the others' do.
PREDECODE_JOIN = {"B0": (0.108, 0.0), "B1": (0.036, 0.0), "B2": (0.254, -0.048), "B3": (0.254, -0.048)}
#: The strips sit half a fin pitch (13.5 nm) in from their pair's edge, so
#: the slices' own metal lands on half nanometres; the rails are the
#: router's M4, which must not (23.5 nm from its wires, half-nm jogs where
#: it joins them).  Half a nanometre up puts them on whole nanometres in
#: either orientation: the strip's height is whole.  In the controller's
#: band the strips' masters carry half a nanometre of their placement
#: (`abstract`'s shift), which does the same: the rails are drawn without it.
RAIL_ON_GRID = 0.0005


def _predecode_rails(strip, starts, pins, on_grid=None):
    """Join each predecode input B<j> across the strip's slices on an M4 rail.

    Each slice's B<j> is its own short M2 run; B1/B3 (and B0/B2) alternate
    along one row 36 nm apart, so the router had to drop a via onto each of
    the strip's 2 x 16 interleaved runs from M3, and with several segments'
    strips stacked it ran out of tracks (DRT-0255 on sel_lo_A[1], x64x8x1
    and x64x64x1 with --segment-bits).  A rail per input makes them one
    pin, reached anywhere along the strip.
    """
    for j in range(4):
        sx, dy = PREDECODE_JOIN[f"B{j}"]
        _, y, _ = pins[f"B{j}"]
        rail_y = y + dy + (RAIL_ON_GRID if on_grid is None else on_grid)
        xs = [x0 + sx for x0 in starts]
        for x in xs:
            strip.add(gdstk.rectangle((x - 0.009, y - 0.009), (x + 0.009, y + 0.009), layer=25))  # V2
            strip.add(gdstk.rectangle((x - 0.009, rail_y - 0.012), (x + 0.009, rail_y + 0.012), layer=35))  # V3
            low, high = min(y - 0.014, rail_y - 0.017), max(y + 0.014, rail_y + 0.017)
            strip.add(gdstk.rectangle((x - 0.009, low), (x + 0.009, high), layer=30))  # M3
        strip.add(gdstk.rectangle((xs[0] - 0.020, rail_y - 0.012), (xs[-1] + 0.020, rail_y + 0.012), layer=40))


def _mid_strip_pair(lib, strip, starts, pins, tracks, gate_tracks, rows, height, x_left, x_right,
                    gate_reach, load):  # fmt: skip
    """Two port-A strips back to back between two wordline segments: ``D`` drives the one below, ``U`` above.

    The two-port pair's geometry: each strip half a fin pitch in from its
    outer edge, the two sharing the VSS rail at the seam; each one's outputs
    bridged across its margin on M3, every gate track bridged there too (and
    `gate_reach` on, into the tile's half-pitch margin).
    """
    pair = lib.new_cell(f"wl_strips_mid_c{load}_x{rows // 4}")
    seam = height + FIN_HALF_PITCH
    pair_height = 2 * seam
    pair.add(gdstk.Reference(strip, (0, seam)))  # U, facing up
    pair.add(gdstk.Reference(strip, (0, seam), x_reflection=True))  # D, facing down
    for side, sign in (("U", 1), ("D", -1)):
        for k in range(rows // 4):
            sx, sy, layer = pins["SEL"]
            pair.add(gdstk.Label(f"SEL_{side}[{k}]", (starts[k] + sx, seam + sign * sy), layer=layer, texttype=251))
        for x0_slice in starts:
            for j in range(4):
                x, y, layer = pins[f"B{j}"]
                pair.add(gdstk.Label(f"B_{side}[{j}]", (x0_slice + x, seam + sign * y), layer=layer, texttype=251))
    for i in range(rows):
        x = tracks[("A", i)]
        pair.add(gdstk.rectangle((x - 0.009, seam + height), (x + 0.009, pair_height), layer=30))
        pair.add(gdstk.rectangle((x - 0.009, 0.0), (x + 0.009, seam - height), layer=30))
        pair.add(gdstk.Label(f"WL_U[{i}]", (x, pair_height - 0.005), layer=30, texttype=251))
        pair.add(gdstk.Label(f"WL_D[{i}]", (x, 0.005), layer=30, texttype=251))
    for x in gate_tracks:
        pair.add(gdstk.rectangle((x - 0.010, seam + height), (x + 0.010, pair_height + gate_reach), layer=7))
        pair.add(gdstk.rectangle((x - 0.010, -gate_reach), (x + 0.010, seam - height), layer=7))
    pair.add(gdstk.rectangle((x_left, 0), (x_right, pair_height), layer=100))
    return pair


def ends_tag(bottom, top):
    return {
        (True, True): "",
        (True, False): "_endb",
        (False, True): "_endt",
        (False, False): "_noend",
    }[(bottom, top)]


def mirrored(lib, cell, name, width):
    """`cell` mirrored in x about ``width / 2`` (x -> width - x), labels and boundary included."""
    out = lib.new_cell(name)
    out.add(gdstk.Reference(cell, origin=(width, 0.0), rotation=math.pi, x_reflection=True))
    for label in cell.labels:
        x, y = map(float, label.origin)
        out.add(columns.clone_label(label, label.text, (width - x, y)))
    for poly in cell.polygons:
        if poly.layer == 100:
            (x0, y0), (x1, y1) = poly.bounding_box()
            out.add(gdstk.rectangle((width - x1, y0), (width - x0, y1), layer=100))
    return out


#: A chipforge IO block's cell (`IoColumnSpec`, `SidewaysIoColumnSpec`), and one
#: drawn for its place on the DRM's M4 grid (``m4_grid``: ``_g<x>x<y>``).
IO_BLOCK = re.compile(r"iocol(270)?_x\d+_.*")
PLACED_BLOCK = re.compile(r".*_g[\dp]+x[\dp]+")


def io_block_grids(tile, grid):
    """``block name -> (x, y)``: the DRM's M4 grid point `grid` (nm in `tile`) in each IO block's frame.

    The grid repeats every 24 nm in x and 48 in y, so any point of it will
    do; references may mirror but not turn (M4 runs in x).
    """
    found = {}

    def walk(cell, point):
        for ref in cell.references:
            if ref.rotation % math.pi > 1e-9 or ref.repetition.size > 1:
                if any(IO_BLOCK.fullmatch(c.name) for c in (ref.cell, *ref.cell.dependencies(True))):
                    raise RuntimeError(f"{ref.cell.name}: an IO block under a turned or repeated reference")
                continue
            x, y = point[0] - 1000 * ref.origin[0], point[1] - 1000 * ref.origin[1]
            if abs(ref.rotation) > 1e-9:  # a half turn
                x, y = -x, -y
            if ref.x_reflection:
                y = -y
            local = (round(x % 24, 1) % 24, round(y % 48, 1) % 48)
            if IO_BLOCK.fullmatch(ref.cell.name):
                if found.setdefault(ref.cell.name, local) != local:
                    raise RuntimeError(f"{ref.cell.name} is placed on two M4 phases in one tile")
            else:
                walk(ref.cell, (x, y))

    walk(tile, grid)
    return found


def tag_hierarchy(cell, tag):
    """Append `tag` to the name of `cell` and of every cell under it holding a block drawn
    for its place: those differ from the same tile's elsewhere in the macro."""
    holds = {}

    def holding(c):
        if id(c) not in holds:
            holds[id(c)] = any(PLACED_BLOCK.fullmatch(r.cell.name) or holding(r.cell) for r in c.references)
        return holds[id(c)]

    for c in [c for c in (cell, *cell.dependencies(True)) if holding(c)]:
        c.name += tag


def build_leaf(wordlines, tap_pitch, bottom=True, top=True, shared_b=False, strap_pitch=0, m4_grids=None):
    """``iocol A | cap | array of `wordlines` | iocol B``, with a dummy row below and/or above.

    In a stack of abutted tiles only the stack's two ends need a dummy row
    (its outer end and the end facing its driver strip); the tiles between
    abut array row to array row, their IO blocks rail to rail.

    With `shared_b` it is a pair's tile: the half without port B's IO, then
    the two-sided port-B block, then the half mirrored (`build_colgrp_pair`).

    With `strap_pitch` every that many taps is a strap cell, whose M5 supply
    spines run down the tap column through every row and abutted tile.

    `m4_grids` maps an IO block's name to the DRM's M4 grid in it
    (`IoColumnSpec.m4_grid`): where the tile lands (`io_block_grids`).
    """
    gds = REPO / "tech/gds"
    lib = arrays.build_library(
        gds / "sram_cell_8t.gds",
        gds / "srambank_32b_boundary_2.gds",
        [wordlines],
        4,
        gds / "sram_cell_8t_tap.gds",
        tap_pitch,
        strap_pitch,
    )
    cells = {c.name: c for c in lib.cells}
    edges = {
        c.name: c for c in gdstk.read_gds(str(gds / "sram_cell_8t_edges.gds")).cells
    }
    # Each port's IO is the parametric column block, built to this bitcell.
    specs = columns.io_block_specs(cells["sram_cell_8t"])
    if m4_grids:
        specs = {port: dataclasses.replace(spec, m4_grid=m4_grids.get(spec.cell_name))
                 for port, spec in specs.items()}  # fmt: skip
    blocks, _ = columns.build_io_blocks(specs)
    for block in blocks.values():
        for dep in (block, *block.dependencies(True)):
            if dep not in lib.cells:
                lib.add(dep)
    io_a = columns.build_port_io(lib, "A", blocks["A"], specs["A"])
    io_b = columns.build_port_io(lib, "B", blocks["B"], specs["B"])
    caps = columns.build_cap_array(lib, edges)
    array = cells[arrays.array_name(arrays.CONTRACTS[0], wordlines, 4, tap_pitch, strap_pitch)]
    leaf = columns.build_colgrp(lib, array, io_a, None if shared_b else io_b, caps, wordlines)
    capped = lib.new_cell("capped_" + leaf.name + ends_tag(bottom, top))
    capped.add(gdstk.Reference(leaf))
    for label in leaf.labels:
        capped.add(columns.clone_label(label, label.text, tuple(label.origin)))
    width = columns.boundary_box(leaf)[2]
    bx0, by0, bx1, by1 = columns.boundary_box(cells["sram_cell_8t"])
    slot_width, pitch = bx1 - bx0, by1 - by0
    io_a_width = columns.boundary_box(io_a)[2]
    cap_width = columns.boundary_box(caps)[2]
    blank = edges["FILLER_BLANK_8t"]
    fx0, fy0, fx1, fy1 = columns.boundary_box(blank)
    half_width = fx1 - fx0
    ends = lib.new_cell("dp_array_end_rows" + ends_tag(bottom, top))
    # The array has one capped end, port A's; a dummy row runs above and below
    # it from there, with a corner over the cap and blanks over the filler.
    # Port B's end meets its IO on the array's last tap, as an IO face always
    # has, and the dummy rows simply stop there.
    array_x = io_a_width + cap_width
    corner_x = array_x - slot_width
    filler_x = io_a_width
    # Over a strap the dummy row's corners must not keep their M5 stripe:
    # the strap's supply spines run up to the tap's edge, and the second
    # corner's stripe lands on the VDD spine.  Over a tap nothing below it
    # uses that stripe.
    row_edges = edges
    if strap_pitch:
        row_edges = dict(edges)
        for name, master in edges.items():
            if name.startswith("sram_cell_8t_corner"):
                bare = gdstk.Cell(name + "_strap")
                bare.add(*[q.copy() for q in master.polygons if q.layer not in (50, 45)])
                bare.add(*[lab.copy() for lab in master.labels if lab.layer != 50])
                row_edges[name] = bare
    for bottom in [b for b, wanted in ((False, top), (True, bottom)) if wanted]:
        y = -pitch if bottom else 4 * pitch
        row = edge_cells.build_dummy_vertical_array(
            lib, row_edges, wordlines, tap_pitch, mirror_x=False, mirror_y=bottom
        )
        ends.add(gdstk.Reference(row, origin=(array_x, y)))
        # The corner immediately left of slot A needs B's landing tracks.
        # Restore the x orientation of the B master so its process frame still
        # matches the column cap below; only its wordline routing differs.
        corner = edge_cells.oriented_cell(
            edges[edge_cells.oriented_name("sram_cell_8t_corner", True, bottom)],
            edge_cells.oriented_name("sram_cell_8t_corner", False, bottom) + "_b",
            mirror_x=True,
        )
        # The bitcell's two gates end 12 nm apart at a dummy row's outer
        # edge, the array's columns in pairs: short, long, long, short.  The
        # corner's long gate stood beside the row's short first gate, whose
        # other neighbour is long too, and in that 12 nm GATE.S.1 measures
        # the 88 nm across it.  The corner's gates end short together.
        gates = [q for q in corner.polygons if q.layer == edge_cells.GATE]
        outer = (max if bottom else min)(q.bounding_box()[0 if bottom else 1][1] for q in gates)
        for q in gates:
            (gx0, gy0), (gx1, gy1) = q.bounding_box()
            corner.remove(q)
            corner.add(gdstk.rectangle((gx0, max(gy0, outer) if bottom else gy0),
                                       (gx1, gy1 if bottom else min(gy1, outer)), layer=edge_cells.GATE))
        ends.add(gdstk.Reference(corner, origin=(corner_x - bx0, y - by0)))
        for col in range(2):
            ends.add(
                gdstk.Reference(
                    blank,
                    origin=(
                        filler_x + col * half_width - fx0,
                        y + fy1 if bottom else y - fy0,
                    ),
                    x_reflection=bottom,
                )
            )
    if ends.references:
        capped.add(gdstk.Reference(ends))
        tie_end_row_stubs(capped)
        # A bitline bar runs 27 nm past its cell to overlap the next cell's;
        # in a dummy row the next cell (a dummy, past the corner or before
        # the far tap) has only a WLB landing beside that stub, a run under
        # 44 nm (M4.S.5).  The bars lengthen to overlap it by 44.
        fix_short_parallel_runs(capped, blocks_too=True)
    columns.rect(
        capped, (0, -pitch if bottom else 0, width, (5 if top else 4) * pitch), 100
    )
    if shared_b:
        io_b2 = columns.build_port_io(lib, "B2", blocks["B2"], specs["B2"])
        return columns.build_colgrp_pair(
            lib, capped, io_b2, wordlines, name=capped.name.replace("capped_", "capped_pair_", 1)
        )
    return capped


def _io_6t(mux, m4_grids=None):
    """A library of the released 6T cells and the single-column IO block (`build_leaf`'s
    `m4_grids`); the block's tile wrapper, its spec, the cells."""
    import generate_asap7_6t_iocolumn as columns6

    cells = columns6.load_source()
    lib = gdstk.Library(unit=1e-6, precision=2.5e-10)
    for name in sorted(columns6.SOURCE_CELLS):
        columns6.add_with_dependencies(lib, cells[name])
    spec = columns6.io_spec(cells[columns6.BITCELL], mux)
    if m4_grids:
        spec = dataclasses.replace(spec, m4_grid=m4_grids.get(spec.cell_name))
    block, deps, _ = columns6.build_io_block(spec)
    for dep in (block, *deps):
        if dep.name not in {c.name for c in lib.cells}:
            lib.add(dep)
    return lib, columns6.build_io(lib, block, spec), spec, cells


@functools.cache
def _library_6t(wordlines, mux=4):
    """The single-port 6T tiles' library: the released 6T cells, the single-column IO block, every end variant."""
    import generate_asap7_6t_iocolumn as columns6

    lib, io, spec, cells = _io_6t(mux)
    tiles = {(bottom, top): columns6.build_tile(lib, cells, wordlines, io, spec, bottom=bottom, top=top)
             for bottom, top in itertools.product((True, False), repeat=2)}  # fmt: skip
    return lib, tiles


def build_leaf_6t(wordlines, bottom=True, top=True, mux=4, m4_grid=None, m4_grids=None):
    """``edge filler | cap | array of `wordlines` | dummy | tap | IO``: the single-port 6T tile.

    The released 6T cells with chipforge's single-column IO block
    (`generate_asap7_6t_iocolumn`); a dummy row below and/or above, as the
    8T tile has.  Its pins are the 8T tile's port A's.  With `m4_grid` (nm
    in the tile) its M4 is drawn for a place on the DRM's grid, the block's
    as `m4_grids` says (`build_leaf`).
    """
    if m4_grid is None:
        return _library_6t(wordlines, mux)[1][(bottom, top)]
    import generate_asap7_6t_iocolumn as columns6

    lib, io, spec, cells = _io_6t(mux, m4_grids)
    return columns6.build_tile(lib, cells, wordlines, io, spec, bottom=bottom, top=top, m4_grid=m4_grid)


def tie_end_row_stubs(cell):
    """Join each dummy row's stray supply stubs to the row's own supply metal.

    The corner and tap-slot cells of the dummy rows keep bitline and supply
    stubs at the real cells' heights, labelled VSS or VDD but touching
    nothing: twenty 162 nm bars a tile, on M2 and M4, that the router would
    otherwise have to reach one by one between the M5 wordlines, which it
    manages only at some track phases.  Stubs of one net at one height are
    first bridged along the row on M2.  Then a VSS stub on M2 takes a strap
    up the column to the row's VSS bar; an M4 stub, which cannot be strapped
    on M4 (one direction only), takes a V3 onto the cell's own VSS via
    stack, or an M3 jog to it; and the VDD bar, whose nearest VDD is the
    neighbouring real row's M2 bar across the dummy row's VSS bars, takes a
    V2, an M3 jog on a free track and a V2 down.  The tile then presents
    rails.
    """
    HALF, HALF3, CAP3 = 0.009, 0.009, 0.014
    graph, net_of_root, members, m3, m2 = _supply_graph(cell)

    def is_stub(polys):
        """A lone bar on one layer: one track tall, however many cells long."""
        layers = {p.layer for p in polys}
        if len(layers) != 1 or next(iter(layers)) not in (20, 40):
            return False
        y0 = min(p.bounding_box()[0][1] for p in polys)
        y1 = max(p.bounding_box()[1][1] for p in polys)
        return y1 - y0 < 0.03

    # Bridge: same net, M2, same height, nothing on M2 in between.
    stubs = {root: polys for root, polys in members.items() if is_stub(polys)}
    groups = defaultdict(list)
    for root, polys in stubs.items():
        x0 = min(p.bounding_box()[0][0] for p in polys)
        x1 = max(p.bounding_box()[1][0] for p in polys)
        (_, y0), (_, y1) = polys[0].bounding_box()
        if polys[0].layer == 20:
            groups[(net_of_root[root], round((y0 + y1) / 2, 3))].append(
                (x0, x1, y0, y1)
            )
    for (net, _), bars in groups.items():
        bars.sort()
        for (_, ax1, y0, y1), (bx0, _, _, _) in zip(bars, bars[1:]):
            if any(
                bb[0][0] < bx0
                and bb[1][0] > ax1
                and bb[0][1] < y1 + 0.018
                and bb[1][1] > y0 - 0.018
                for bb, root in m2
                if net_of_root.get(root) != net
            ):
                continue
            cell.add(gdstk.rectangle((ax1, y0), (bx0, y1), layer=20))

    # Tie: everything single-layer and short is a stub, before or after bridging.
    graph, net_of_root, members, m3, m2 = _supply_graph(cell)

    def m3_is_free(x, y0, y1, own):
        # M3 keeps 18 nm from a long edge and 25 nm from a short one (M3.S.1, .2).
        for ((ax, ay), (bx, by)), root in m3:
            if root in own:
                continue
            clear = 0.018 if by - ay > 0.036 else 0.025
            if (
                ax < x + HALF3 + clear
                and bx > x - HALF3 - clear
                and ay < y1 + clear
                and by > y0 - clear
            ):
                return False
        return True

    def m2_is_free(x0, y0, x1, y1, own):
        return not any(
            bb[0][0] < x1 + 0.018
            and bb[1][0] > x0 - 0.018
            and bb[0][1] < y1 + 0.018
            and bb[1][1] > y0 - 0.018
            for bb, root in m2
            if root not in own
        )

    def nearest(box, net, layer, reach, exclude, min_overlap):
        """Closest same-net polygon on `layer` sharing `min_overlap` of x: (gap, root, bbox of its run)."""
        (sx0, sy0), (sx1, sy1) = box
        best = None
        for root, polys in members.items():
            if net_of_root[root] != net or root == exclude:
                continue
            for polygon in polys:
                if polygon.layer != layer:
                    continue
                (ox0, oy0), (ox1, oy1) = polygon.bounding_box()
                gap = max(oy0 - sy1, sy0 - oy1)
                if min(sx1, ox1) - max(sx0, ox0) >= min_overlap - 1e-6 and gap < reach:
                    if best is None or gap < best[0]:
                        best = (gap, root, ((ox0, oy0), (ox1, oy1)))
        if best is None:
            return None
        # A row's bar is a chain of overlapping cell-wide pieces: take the
        # whole run at that height, so the jog can go anywhere along it.
        gap, root, ((ox0, oy0), (ox1, oy1)) = best
        for polygon in members[root]:
            (px0, py0), (px1, py1) = polygon.bounding_box()
            if (
                polygon.layer == layer
                and abs(py0 - oy0) < 0.003
                and abs(py1 - oy1) < 0.003
            ):
                ox0, ox1 = min(ox0, px0), max(ox1, px1)
        return gap, root, ((ox0, oy0), (ox1, oy1))

    def m3_jog(x, y_a, y_b, own):
        lo, hi = sorted((y_a, y_b))
        if not m3_is_free(x, lo - CAP3, hi + CAP3, own):
            return False
        cell.add(
            gdstk.rectangle((x - HALF3, lo - CAP3), (x + HALF3, hi + CAP3), layer=30)
        )
        m3.append((((x - HALF3, lo - CAP3), (x + HALF3, hi + CAP3)), own[0]))
        return True

    for root, polys in members.items():
        layers = {p.layer for p in polys}
        if len(layers) != 1 or next(iter(layers)) not in (20, 40):
            continue
        net, layer = net_of_root[root], next(iter(layers))
        sx0 = min(p.bounding_box()[0][0] for p in polys)
        sx1 = max(p.bounding_box()[1][0] for p in polys)
        sy0 = min(p.bounding_box()[0][1] for p in polys)
        sy1 = max(p.bounding_box()[1][1] for p in polys)
        if sx1 - sx0 > 0.2 and sy1 - sy0 > 0.03:
            continue  # not a stub nor a bridged run: a bar the router can reach
        y_stub = (sy0 + sy1) / 2
        box = ((sx0, sy0), (sx1, sy1))
        if layer == 20:
            found = nearest(box, net, 20, 0.7, root, 0.036)
            if found is None:
                raise RuntimeError(
                    f"no {net} M2 near the stub at ({sx0:.3f}, {sy0:.3f})"
                )
            gap, target, ((tx0, ty0), (tx1, ty1)) = found
            x0, x1 = max(sx0, tx0), min(sx1, tx1)
            if gap < 0.2:  # the row's own bar, a strap away on M2
                lo, hi = min(sy0, ty0), max(sy1, ty1)
                for x in (
                    x0 + 0.018 + k * 0.036 for k in range(int((x1 - x0) / 0.036))
                ):
                    if m2_is_free(x - HALF, lo, x + HALF, hi, (root, target)):
                        cell.add(
                            gdstk.rectangle((x - HALF, lo), (x + HALF, hi), layer=20)
                        )
                        break
                else:
                    raise RuntimeError(
                        f"no room for an M2 strap from the {net} stub at ({sx0:.3f}, {sy0:.3f})"
                    )
                continue
            # A V2 at each end and an M3 jog on a track (the wordlines sit at
            # 18 mod 36 nm) inside both bars and clear of every other M3.
            y_bar = (ty0 + ty1) / 2
            first = math.ceil((x0 + 2 * HALF - 0.018) / 0.036) * 0.036 + 0.018
            # Every track along the two bars' overlap; none free is an error,
            # never a pair of vias with no jog between them.
            k = 0
            while True:
                x = first + k * 0.036
                if x + 2 * HALF > x1:
                    raise RuntimeError(
                        f"no free M3 track over the {net} bar at ({x0:.3f}..{x1:.3f}, {y_stub:.3f})"
                    )
                if m3_jog(x, y_stub, y_bar, (root, target)):
                    break
                k += 1
            for y in (y_stub, y_bar):
                cell.add(
                    gdstk.rectangle(
                        (x - HALF, y - HALF), (x + HALF, y + HALF), layer=25
                    )
                )
        else:
            found = nearest(box, net, 30, 0.2, root, 2 * HALF3)
            if found is None:
                raise RuntimeError(
                    f"no {net} M3 near the M4 stub at ({sx0:.3f}, {sy0:.3f})"
                )
            _, target, ((tx0, ty0), (tx1, ty1)) = found
            x = (tx0 + tx1) / 2  # the stack's own M3 column
            if not sx0 + 0.020 <= x <= sx1 - 0.020:  # M4 past V3 by 11 nm a side
                raise RuntimeError(
                    f"the {net} M3 at x={x:.3f} is not under the M4 stub ({sx0:.3f}..{sx1:.3f})"
                )
            if (
                not ty0 <= y_stub <= ty1
            ):  # the stack's M3 stops short of the stub: jog to it
                y_m3 = ty0 if ty0 > y_stub else ty1
                if not m3_jog(x, y_stub, y_m3, (root, target)):
                    raise RuntimeError(
                        f"the M3 jog to the {net} M4 stub at ({x:.3f}, {y_stub:.3f}) is blocked"
                    )
            cell.add(
                gdstk.rectangle(
                    (x - HALF3, y_stub - 0.012), (x + HALF3, y_stub + 0.012), layer=35
                )
            )


def _supply_graph(cell):
    """The cell's metal graph, its supply-labelled roots, their polygons, and all M3/M2 boxes by root."""
    graph = MetalGraph(cell)
    net_of_root = {}
    for label in cell.get_labels(depth=None):
        net = supply(label.text)
        if net is not None and label.layer in METALS:
            net_of_root[graph.label_root(label)] = net
    members = defaultdict(list)
    m3, m2 = [], []
    for i, polygon in enumerate(graph.polygons):
        root = graph.root(i)
        if polygon.layer == 30:
            m3.append((polygon.bounding_box(), root))
        if polygon.layer == 20:
            m2.append((polygon.bounding_box(), root))
        if polygon.layer in METALS and root in net_of_root:
            members[root].append(polygon)
    return graph, net_of_root, members, m3, m2


def run(args):
    if args.wordlines < 2 or args.wordlines % 2 or args.bits < 2 or args.bits % 2:
        raise RuntimeError("wordlines and bits must be positive even values >= 2")
    if args.banks < 1 or args.banks & (args.banks - 1):
        raise RuntimeError("banks must be a power of two")
    shared_b = args.share_port_b
    if shared_b and args.banks < 2:
        raise RuntimeError("sharing port B's IO needs banks in pairs")
    # The single-port 6T macro is the 8T floorplan with port A alone: one IO
    # block per column tile, one strip per side of the controller band.
    single = args.bitcell == "6t"
    ports = ("A",) if single else ("A", "B")
    if args.mux not in ((4, 8, 16) if single else (4,)):
        raise RuntimeError(f"a {args.mux}:1 column mux is not built for the {args.bitcell} macro")
    if single and (shared_b or args.strap_pitch):
        raise RuntimeError("the 6T macro takes no --share-port-b or straps yet")
    # What the floorplan repeats across: a bank, or a pair of banks.
    units, banks_per_unit = (args.banks // 2, 2) if shared_b else (args.banks, 1)
    if args.plan_band is None:
        work = args.work.resolve()
        work.mkdir(parents=True, exist_ok=True)
    array_wordlines = 2 * args.wordlines
    # The released 6T row ends in its own tap; only the 8T array takes them inside.
    tap_pitch = 0 if single else math.gcd(array_wordlines, 16)

    def is_wordline(net):
        return WORDLINE.fullmatch(net) is not None or SEGMENT_WORDLINE.fullmatch(net) is not None

    # Only a stack's two ends carry a dummy row: its first tile the bottom
    # one, its last the top one, the tiles between none, so they abut array
    # row to array row and IO block to IO block.
    half_bits = args.bits // 2
    # Divided wordlines: each stack cut into segments of `segment` bits, a
    # mid strip pair between two, every segment its own wordlines and its
    # own end rows; numbered from the bottom of the stack.
    segment = args.segment_bits or half_bits
    if half_bits % segment:
        raise RuntimeError(f"--segment-bits {segment} does not divide a stack of {half_bits} bits")
    segments = half_bits // segment
    if segments > 1 and not single:
        raise RuntimeError("wordline segments are for the single-port macro (--bitcell 6t) so far")

    def ends_of(index):
        return (index % segment == 0, index % segment == segment - 1)

    def segment_of(index):
        return None if segments == 1 else index // segment

    banding = args.band is not None or args.plan_band is not None
    variants = {}
    # The tiles' left boundary edge in the macro: the bank's margin, or just
    # past it where the tiles' gates meet the gate grid.
    margin_x = None
    grids = {}
    shifts = {}

    def make_variant(bottom, top, m4_grid=None, tag=""):
        """A tile and its master: with `m4_grid` (nm in the tile), its M4 drawn for a place
        on the DRM's grid in the macro, and every cell holding its blocks renamed with `tag`."""
        nonlocal margin_x
        m4_grids = None if m4_grid is None else io_block_grids(variants[(bottom, top)][0], m4_grid)
        if single:
            cell = build_leaf_6t(array_wordlines, bottom=bottom, top=top, mux=args.mux,
                                 m4_grid=m4_grid, m4_grids=m4_grids)  # fmt: skip
        else:
            cell = build_leaf(array_wordlines, tap_pitch, bottom=bottom, top=top, shared_b=shared_b,
                              strap_pitch=args.strap_pitch, m4_grids=m4_grids)
        if banding:
            # Over the IO columns an end tile stops at its IO blocks: the
            # controller's band takes the rest.
            notch_band_edges(cell, end_row_spans(cell, shared_b), single)
        labels = [label for label in cell.labels if not supply(label.text)]
        # The tile's fins are on the array's grid, half a fin pitch off the
        # standard cells': it sits half a nanometre up in its master.
        phase = grid_phase(cell)
        shift = grid_shift(phase)
        bx0 = columns.boundary_box(cell)[0]
        # The tile's origin lies its boundary's left edge less its corner's x.
        # Its frame is on M5's track lattice too: the 6T tile draws its
        # supply stripes on the tracks there (generate_asap7_6t_iocolumn).
        x_rules = offset_rules(grid_rules(cell, phase, shift, tracks=(5,))[0], bbox_origin(cell)[0] - bx0)
        if args.band is not None:
            # The band's controller has its frame's origin at the tiles' left edge.
            x_rules.append((TRACK_LATTICE, 0))
        left = snap(args.margin, x_rules)
        if margin_x is not None and abs(left - margin_x) > 1e-6:
            raise RuntimeError(f"the tiles' gates are on different grids ({left} um, {margin_x} um)")
        margin_x = left
        if single and any(shift):
            add_m5_landings(cell, [label for label in labels if not is_wordline(label.text)],
                            margin_x - bx0 + shift[0])  # fmt: skip
        master = ("dp_colpair" if shared_b else "dp_column") + ends_tag(bottom, top) + tag
        if tag:
            tag_hierarchy(cell, tag)
        grids[master] = grid_rules(cell, phase, shift, tracks=(5,))
        # A tile between a stack's ends meets tiles above and below: its IO
        # blocks' supply straps (and the top rail past the block, the next
        # tile's bottom one) join theirs end to end, so only the end tiles'
        # are routed.
        _, y_lo, _, y_hi = columns.boundary_box(cell)
        # A supply component reaching from under the band is offered where the
        # tile is its own, short of the band's edge: one wholly under it keeps
        # its pin and joins the controller's there (the seam ties of 369624b
        # merged a VSS rail under the band with two stubs below it, and the
        # router chose its access on the rail: DRT-1231 on x8x2x2).
        span_lo, span_hi = y_lo, y_hi
        if banding:
            band_lo, band_hi = band_edges(cell, single)
            span_lo = y_lo if band_lo is None else band_lo
            span_hi = y_hi if band_hi is None else band_hi

        def through(polys, y_lo=y_lo, y_hi=y_hi):
            return (min(p.bounding_box()[0][1] for p in polys) <= y_lo + 0.001
                    or max(p.bounding_box()[1][1] for p in polys) >= y_hi - 0.001)  # fmt: skip

        shifts[master] = shift
        return (cell, *abstract(
            cell, master, labels, abutted=is_wordline,
            shift=shift,
            abutted_supply=None if bottom or top else through,
            snap_half_nm=single,
            supply_span=(span_lo, span_hi),
        ))  # fmt: skip

    for index in range(half_bits):
        if ends_of(index) not in variants:
            variants[ends_of(index)] = make_variant(*ends_of(index))
    leaf = next(iter(variants.values()))[0]
    size = next(iter(variants.values()))[4]
    strip_lib = gdstk.Library(unit=1e-6, precision=1e-10)
    # The strips are drawn on the first bank's half: a pair tile's first reference.
    strip_pairs, slice_load = build_wordline_strips(
        strip_lib, leaf.references[0].cell if shared_b else leaf, args.wordlines, args.bits,
        ports=ports,
        segment_bits=segment if segments > 1 else None, mux=args.mux,
        rail_on_grid=0.0,
    )
    if shared_b:
        tile_width = columns.boundary_box(leaf)[2]
        for half in list(strip_pairs):
            strip_pairs[half + "_r"] = mirrored(
                strip_lib, strip_pairs[half], strip_pairs[half].name + "_r", tile_width
            )
    # With the strips in the controller's band, the controller routes its
    # select outputs onto the strips' SEL/B pins itself: at this level those
    # nets are joined already, like the wordlines, and only probed.
    in_band = args.band is not None

    def is_strip_select(net):
        return re.fullmatch(r"(SEL|B)_[AB]\[\d+\]", net) is not None

    pair_masters, pair_shifts = {}, {}

    def strip_master(half, pair, name):
        return abstract(
            pair, name, [label for label in pair.labels if not supply(label.text)],
            abutted=lambda net: is_wordline(net) or (in_band and is_strip_select(net)),
            shift=pair_shifts[half],
        )

    for half, pair in strip_pairs.items():
        # On the array's fin grid like the tiles, half a nanometre up in its
        # master: in the controller's frame (the band's edge, on the
        # chipforge half-nanometre phase) the slices are on its grid.
        phase = grid_phase(pair)
        pair_shifts[half] = grid_shift(phase)
        grids[f"dp_wl_strips_{half}"] = grid_rules(pair, phase, pair_shifts[half])
        pair_masters[half] = strip_master(half, pair, f"dp_wl_strips_{half}")

    def via_track(half, y_pair):
        """The M4 tracks' offset, nm, in the frame of a `half` pair placed at `y_pair`."""
        return (12 - round(2000 * (y_pair + pair_shifts[half][1])) / 2) % 48

    def strip_on_track(side, y_pair):
        """Whether every `side` pair placed at `y_pair` has a track its via row reaches."""
        windows = [strip_via_window(strip_pairs[d]) for d in strip_pairs if d.split("_")[0] == side]
        return all(w is None or strip_via_row(w, via_track(side, y_pair)) is not None for w in windows)

    bank_gap = args.bank_gap
    # Preserve the validated pin-access phase in each bank, and its gates on
    # the gate grid: 1.728 um is the least common multiple of the gate pitch
    # and the M1-M7 routing pitches; an arbitrary bank stride can strand the
    # small cap/tap ground terminals between tracks.
    bank_stride = math.ceil(round((size[0] + bank_gap) / 1.728, 6)) * 1.728
    pair_height = columns.boundary_box(strip_pairs["hi"])[3]
    tile_width = columns.boundary_box(leaf)[2]
    band_width = (units - 1) * bank_stride + tile_width

    # The band's edges are the IO blocks' edges, which lie inside the end
    # tiles: a dummy row above (below) the array, and only the block's
    # half-fin-pitch offset of it over the IO columns.  There the rows,
    # fins, gate grid and VSS rail of the block and a standard-cell row are
    # the same, so the controller's first row abuts the block.
    seam_lo, seam_hi = band_seams(single)
    dummy_spans = end_row_spans(leaf, shared_b)

    def strip_placements(die_height):
        """Every strip pair in a band `die_height` tall, from the band's lower left: its
        instance, drawn master, bank, side, and origin; the lower pair on the lower
        stack's dummy row, the upper under the upper stack's, both in the tiles' x."""
        placed = []
        for unit, (half, second) in itertools.product(
            range(units), itertools.product(("lo", "hi"), range(banks_per_unit))
        ):
            drawn = half + ("_r" if second else "")
            pox, poy = bbox_origin(strip_pairs[drawn])
            y = seam_lo if half == "lo" else die_height - seam_hi - pair_height
            bank = unit * banks_per_unit + second
            placed.append((f"WL_{half.upper()}_{bank}", drawn, bank, half,
                           (unit * bank_stride + pox, y + poy)))  # fmt: skip
        return placed

    if in_band or args.plan_band is not None:
        if units > 1:
            raise RuntimeError("the controller band abuts one tile column (a bank or a pair) so far: "
                               "a second one would put its gates off the band's 54 nm grid")  # fmt: skip
    def tile_height(index):
        _, y0, _, y1 = columns.boundary_box(variants[ends_of(index)][0])
        return y1 - y0

    mid_height = columns.boundary_box(strip_pairs["mid"])[3] if segments > 1 else 0.0
    # A tile's offset in its stack: the tiles below it and the mid pairs between their segments.
    offsets = [sum(tile_height(k) for k in range(index)) + (index // segment) * mid_height
               for index in range(half_bits)]  # fmt: skip
    stack = offsets[-1] + tile_height(half_bits - 1)

    if args.plan_band is not None:
        keepouts = [(x0, x1, half) for x0, x1 in dummy_spans for half in ("lo", "hi")]
        # Which track a pair's via row lands on is known only once the band is.
        pair_masters = {half: (m[0], m[1], block_strip_via_window(m[2], strip_pairs[half], pair_shifts[half]), m[3])
                        for half, m in pair_masters.items()}  # fmt: skip
        plan_band(args, pair_masters, strip_placements, band_width, pair_height,
                  keepouts, (seam_lo, seam_hi))  # fmt: skip
        return
    ctrl_lib = gdstk.read_gds(str(args.controller))
    ctrl = next(c for c in ctrl_lib.cells if c.name == "ctrl_decode")
    add_pin_conductors(ctrl)
    if in_band:
        # The controller's router took the band's strips' SEL/B rails as its
        # own metal, and may have reached one pin of a net through another's
        # rail: without them that branch is a piece of its own, which the
        # assembled macro then joins to the net (6T, x64x8x1: a short of
        # sel_lo_A[1] with the controller's internal_5908).  Its abstract
        # sees the rails as they lie in the band.
        for _, drawn, _, _, (x, y) in strip_placements(band_die(args.band)[1]):
            pair = strip_pairs[drawn]
            pox, poy = bbox_origin(pair)
            graph = MetalGraph(pair)
            for label in pair.labels:
                if not is_strip_select(label.text):
                    continue
                root = graph.label_root(label)
                polys = [p for i, p in enumerate(graph.polygons) if graph.root(i) == root]
                top_layer = max(p.layer for p in polys)
                for poly in polys:
                    if poly.layer == top_layer:
                        ctrl.add(gdstk.Polygon(poly.points + (x - pox, y - poy), layer=poly.layer))
    # Its router did not know the run-length rule either.
    ctrl_prl = fix_short_parallel_runs(ctrl)
    ctrl_labels = [
        label
        for label in ctrl.labels
        if label.texttype == 251 and not supply(label.text)
    ]
    controller, ctrl_pins, ctrl_lef, ctrl_size = abstract(
        ctrl, "dp_controller", ctrl_labels,
        # The band's strips take its selects inside it; with segments the
        # mid pairs take the same nets outside it, from its pins.
        abutted=(lambda net: net.startswith("sel_")) if in_band and segments == 1 else None,
    )
    ctrl_phase = grid_phase(ctrl)
    if any(grid_shift(ctrl_phase)):
        raise RuntimeError("the controller's gates or fins are off the standard cells' grids")
    # It was routed on the DRM's M4/M5 tracks in its own frame.
    grids["dp_controller"] = grid_rules(ctrl, ctrl_phase, tracks=(4, 5))

    top_name = f"sram_x{args.wordlines * 2}x{args.bits}x{args.banks}"
    # Three kinds of empty space, each its own option: the margin between the
    # blocks and the macro edge, where the macro's pins land (M8/M9) and turn;
    # the channel on each side of the controller, where its outputs fan out;
    # and the gap between banks.  Every one is routed on the top layers over
    # the blocks as well, so none needs to hold whole buses.
    margin, channel = args.margin, args.channel_width
    margin_y = args.margin_y if args.margin_y is not None else margin
    for name, value in (
        ("margin", margin),
        ("margin_y", margin_y),
        ("channel", channel),
        ("bank gap", bank_gap),
    ):
        if value < 0.2:
            raise RuntimeError(f"the {name} must be at least 0.2 um, got {value}")
    ctrl_x = snap(margin, grids["dp_controller"][0])
    width = max(margin_x + (units - 1) * bank_stride + size[0], ctrl_x + ctrl_size[0]) + margin

    # Bottom to top: the lower stack of tiles, abutted at the tile's boundary
    # pitch; its strip pair facing down into it; a channel; the controller; a
    # channel; the upper stack's pair facing up; the upper stack.  A tile's
    # IO columns are shorter than its array, so the stacks keep a channel per
    # tile on each IO side for the controls and data.
    # Each stack's tiles and strips are abutted; the margin under the lower
    # stack and each channel beside the controller grow to put the lower
    # stack, the controller and the upper stack on the grids.
    first = variants[ends_of(0)][1].name
    _, by0, _, _ = columns.boundary_box(variants[ends_of(0)][0])
    toy = by0 - bbox_origin(variants[ends_of(0)][0])[1]  # the tile's boundary in its master
    y_rules = offset_rules(grids[first][1], -toy)
    if in_band:
        # The controller's frame starts at the band's edge (`y_band`).
        y_rules.append((TRACK_LATTICE, round(-(stack + HALF_NM - seam_lo) * 1000) % TRACK_LATTICE))
    y_lo_tiles = snap(margin_y, y_rules)
    # A few places leave port B's via row no M4 track in reach: the next does.
    while not in_band and not strip_on_track("lo", y_lo_tiles + stack):
        y_lo_tiles = snap(y_lo_tiles + 0.001, y_rules)
    if in_band:
        # The controller's die is the whole band between the stacks, the
        # strips in the holes its placement left against each stack.
        die_width, die_height = band_die(args.band)
        if abs(die_width - band_width) > 1e-3:
            raise RuntimeError(f"the controller's band is {die_width} um wide, the tiles {band_width}")
        # The band starts at the lower stack's IO edge and ends at the
        # upper's; the tiles and strips sit half a nanometre up in their
        # masters, so the positions here are their DEF origins' and the
        # boundaries are half a nanometre above them.
        y_band = round(y_lo_tiles + stack + HALF_NM - seam_lo, 4)
        if abs(y_band * 1000 - round(y_band * 1000)) > 1e-6:
            raise RuntimeError(f"the band's edge {y_band} is off the DEF grid")
        y_lo_strips = y_band + seam_lo - HALF_NM
        y_hi_strips = y_band + die_height - seam_hi - pair_height - HALF_NM
        y_hi_tiles = y_band + die_height - seam_hi - HALF_NM
        cox, coy = bbox_origin(ctrl)
        ctrl_origin = (margin_x + cox, y_band + coy)
    else:
        y_lo_strips = y_lo_tiles + stack
        y_ctrl = snap(y_lo_strips + pair_height + channel, grids["dp_controller"][1])
        hi_oy = bbox_origin(strip_pairs["hi"])[1]
        y_hi_strips = snap(y_ctrl + ctrl_size[1] + channel + hi_oy, grids["dp_wl_strips_hi"][1]) - hi_oy
        while not strip_on_track("hi", y_hi_strips):
            y_hi_strips = snap(y_hi_strips + hi_oy + 0.001, grids["dp_wl_strips_hi"][1]) - hi_oy
        y_hi_tiles = y_hi_strips + pair_height
        ctrl_origin = (ctrl_x, y_ctrl)
    height = y_hi_tiles + stack + margin_y
    if in_band:
        # The band is the tiles' width; only the controller's side pins stand
        # out of it, into the margin.
        width = max(margin_x + band_width, ctrl_origin[0] + ctrl_size[0]) + margin
    # (instance, master, origin, pin net -> macro net)
    instances = [("CTRL", "dp_controller", ctrl_origin, lambda net: net)]
    nets = defaultdict(list)
    probes = defaultdict(list)
    for pin, info in ctrl_pins.items():
        if info.get("private") or info.get("abutted"):
            continue
        nets[info["net"]].append(("CTRL", pin))
    for unit in range(units):
        x_bank = margin_x + unit * bank_stride
        bank = unit * banks_per_unit  # a pair tile's first bank
        for bit in range(args.bits):
            inst = f"COL_{bank}_{bit}"
            index = bit % half_bits
            tile, hard, pins = variants[ends_of(index)][:3]
            bx0, by0, _, _ = columns.boundary_box(tile)
            tile_ox, tile_oy = bbox_origin(tile)
            y_tile = (y_lo_tiles if bit < half_bits else y_hi_tiles) + offsets[index]
            origin = (x_bank - (bx0 - tile_ox), y_tile - (by0 - tile_oy))
            resolve = functools.partial(
                leaf_net, bank=bank, bit=bit, wordlines=args.wordlines, bits=args.bits, mux=args.mux,
                shared_b=shared_b, segment=segment_of(index),
            )
            instances.append((inst, hard.name, origin, resolve))
            for pin, info in pins.items():
                if info.get("private") or info.get("abutted"):
                    continue
                nets[resolve(info["net"])].append((inst, pin))
        for (half, y_pair), second in itertools.product(
            (("lo", y_lo_strips), ("hi", y_hi_strips)), range(banks_per_unit)
        ):
            drawn = half + ("_r" if second else "")  # the second bank's are mirrored
            cell, cell_pins, _, _ = pair_masters[drawn]
            inst = f"WL_{half.upper()}_{bank + second}"
            pox, poy = bbox_origin(strip_pairs[drawn])
            origin = (x_bank + pox, y_pair + poy)  # the pair is drawn in the tile's x
            # A band pair drives the stack's segment next to the band.
            resolve = functools.partial(
                strip_net, bank=bank + second, half=half, wordlines=args.wordlines,
                segment=None if segments == 1 else (segments - 1 if half == "lo" else 0),
            )
            instances.append((inst, cell.name, origin, resolve))
            for pin, info in cell_pins.items():
                if info.get("private") or info.get("abutted"):
                    continue
                nets[resolve(info["net"])].append((inst, pin))
        # A mid pair under each segment but a stack's first drives it and the one below.
        for (half, y_stack), g in itertools.product(
            (("lo", y_lo_tiles), ("hi", y_hi_tiles)), range(1, segments)
        ):
            cell, cell_pins, _, _ = pair_masters["mid"]
            inst = f"WL_{half.upper()}_M{g}_{bank}"
            pox, poy = bbox_origin(strip_pairs["mid"])
            origin = (x_bank + pox, y_stack + offsets[g * segment] - mid_height + poy)
            resolve = functools.partial(strip_net, bank=bank, half=half, wordlines=args.wordlines,
                                        below=g - 1, above=g)  # fmt: skip
            instances.append((inst, cell.name, origin, resolve))
            for pin, info in cell_pins.items():
                if info.get("private") or info.get("abutted"):
                    continue
                nets[resolve(info["net"])].append((inst, pin))

    # The IO blocks route on M4, which the DRM holds to absolute tracks
    # (M4.AUX.1-2), and where a tile lands is known only now: a stack's
    # tiles land on different ones (a middle 8T tile is 2376 nm, half a
    # track pitch past a multiple).  Each tile takes blocks drawn for its
    # place, a variant per place.
    phased = {}
    for k, (inst, master, origin, resolve) in enumerate(instances):
        ends = next((e for e, v in variants.items() if v[1].name == master), None)
        if ends is None:
            continue
        tile = variants[ends][0]
        # A point of the tile lands at its master's origin, plus its shift,
        # less its bounding box's corner.
        land = [round(2000 * (o + d - b)) / 2 for o, d, b in zip(origin, shifts[master], bbox_origin(tile))]
        grid = ((-land[0]) % 24, (12 - land[1]) % 48)
        if (ends, grid) not in phased:
            tag = "_g" + "x".join(f"{v:g}".replace(".", "p") for v in grid)
            phased[(ends, grid)] = make_variant(*ends, m4_grid=grid, tag=tag)
            if bbox_origin(phased[(ends, grid)][0]) != bbox_origin(tile):
                raise RuntimeError(f"{master}{tag}: its M4 moved the tile's bounding box")
        instances[k] = (inst, phased[(ends, grid)][1].name, origin, resolve)
    # Likewise port B's via row in each strip pair: onto the M4 track it lands
    # nearest, its stubs grown to reach (in the band the controller was
    # routed around all of that reach: `block_strip_via_window`).
    retracked = {}
    for k, (inst, master, origin, resolve) in enumerate(instances):
        drawn = master.removeprefix("dp_wl_strips_")
        if drawn not in strip_pairs:
            continue
        pair = strip_pairs[drawn]
        track = via_track(drawn, origin[1] - bbox_origin(pair)[1])
        if (drawn, track) not in retracked:
            tag = "_g" + f"{track:g}".replace(".", "p")
            moved = retrack_strip_vias(pair, track, pair.name + tag)
            if moved is None and strip_via_window(pair) is not None:
                print(f"WARNING: {inst}: no M4 track in reach of its via row; left where drawn", flush=True)
            retracked[(drawn, track)] = moved and strip_master(drawn, moved, master + tag)
            if moved is not None:
                grids[master + tag] = grids[master]
        if retracked[(drawn, track)]:
            instances[k] = (inst, retracked[(drawn, track)][0].name, origin, resolve)
    tiles = {v[1].name: v for v in (*variants.values(), *phased.values())
             if any(master == v[1].name for _, master, _, _ in instances)}  # fmt: skip
    masters = {name: (v[1], v[2]) for name, v in tiles.items()}
    masters["dp_controller"] = (controller, ctrl_pins)
    strip_masters = [m for m in (*pair_masters.values(), *filter(None, retracked.values()))
                     if any(master == m[0].name for _, master, _, _ in instances)]  # fmt: skip
    masters.update({m[0].name: (m[0], m[1]) for m in strip_masters})
    (work / "blocks.lef").write_text(
        'VERSION 5.8 ;\nBUSBITCHARS "[]" ;\nDIVIDERCHAR "/" ;\n'
        + "\n".join(v[3] for v in tiles.values())
        + "\n"
        + ctrl_lef
        + "\n"
        + "\n".join(m[2] for m in strip_masters)
        + "\nEND LIBRARY\n"
    )
    library = gdstk.Library(unit=1e-6, precision=1e-10)
    for c in (
        *(v[1] for v in tiles.values()),
        controller,
        *(m[0] for m in strip_masters),
    ):
        for dep in [c, *c.dependencies(True)]:
            if dep.name not in {p.name for p in library.cells}:
                library.add(dep)
    library.write_gds(str(work / "blocks.gds"), timestamp=columns.FIXED_GDS_TIMESTAMP)
    check_grid(instances, grids)

    external = {"clk", "rst_n", "vdd", "vss"}
    external.update(f"{p}_n_{port}" for p in ("ce", "we", "oe") for port in ports)
    address_bits = (2 * args.wordlines * args.mux * args.banks - 1).bit_length()
    external.update(f"A_{port}[{bit}]" for port in ports for bit in range(address_bits))
    external.update(
        f"{p}_{port}[{bit}]" for p in "DQ" for port in ports for bit in range(args.bits)
    )
    for net in nets:
        if net not in external and not any(inst == "CTRL" for inst, pin in nets[net]):
            raise RuntimeError(f"controller has no driver for {net}")
    if external - nets.keys():
        raise RuntimeError(f"missing external nets {sorted(external - nets.keys())}")

    def dbu(value):
        return round(value * 1000)

    d = [
        "VERSION 5.8 ;",
        'DIVIDERCHAR "/" ;',
        'BUSBITCHARS "[]" ;',
        f"DESIGN {top_name} ;",
        "UNITS DISTANCE MICRONS 1000 ;",
        f"DIEAREA ( 0 0 ) ( {dbu(width)} {dbu(height)} ) ;",
        f"COMPONENTS {len(instances)} ;",
    ]
    for inst, master, (x, y), resolve in instances:
        d.append(f"- {inst} {master} + FIXED ( {dbu(x)} {dbu(y)} ) N ;")
        for pin, info in masters[master][1].items():
            if info.get("private"):
                net = f"private:{inst}:{pin}"
            else:
                net = resolve(info["net"])
            probes[net].append(
                {
                    "instance": inst,
                    "pin": pin,
                    "layer": info["layer"],
                    "point": [x + info["point"][0], y + info["point"][1]],
                }
            )
    stripes = (power_stripes(width, height, args.top_layer, args.power_pitch)
               if args.power_pitch > 0 else [])  # fmt: skip
    # The supplies' pins are their stripes, fixed before routing: the
    # router takes one pin shape a terminal, so each stripe is a terminal
    # of its own (vdd, vdd_stripe1, ...), renamed to its net once routed.
    # A signal net to the router, which joins the blocks to them.
    stripe_pins = {}
    for net, x, y_top in stripes:
        name = net if net not in stripe_pins.values() else f"{net}_stripe{len(stripe_pins)}"
        stripe_pins[name] = net
    half = STRIPE_LAYERS[args.top_layer][1] // 2
    d += ["END COMPONENTS", f"PINS {len(external) + len(stripe_pins) - len(set(stripe_pins.values()))} ;"]
    for net in sorted(external):
        direction = (
            "INOUT"
            if net in ("vdd", "vss")
            else ("OUTPUT" if net.startswith("Q_") else "INPUT")
        )
        if net in stripe_pins.values():
            continue
        d.append(f"- {net} + NET {net} + DIRECTION {direction} + USE SIGNAL ;")
        nets[net].append(("PIN", net))
    for name, (net, x, y_top) in zip(stripe_pins, stripes):
        d.append(f"- {name} + NET {net} + DIRECTION INOUT + USE SIGNAL"
                 f" + LAYER M{args.top_layer} ( {-half} 0 ) ( {half} {y_top} ) + FIXED ( {x} 0 ) N ;")
        nets[net].append(("PIN", name))
    d += ["END PINS", f"NETS {len(nets)} ;"]
    for net, ends in sorted(nets.items()):
        d.append(
            f"- {net} " + " ".join(f"( {inst} {pin} )" for inst, pin in ends) + " ;"
        )
    d += ["END NETS", "END DESIGN"]
    (work / "placed.def").write_text("\n".join(d) + "\n")
    manifest = {
        "cell": top_name,
        "size": [width, height],
        "tap_pitch": tap_pitch,
        "nets": probes,
        "external": sorted(external),
    }
    (work / "connectivity.json").write_text(json.dumps(manifest, indent=2) + "\n")
    # The macro router meets the cells only through block abstracts, so it can
    # keep the runset's 20 nm M1 corner spacing (M1.S.6), which the tech LEF
    # leaves at 18 because a cell's own pins sit 18 nm from its rails.
    tech = (REPO / "tech/lef/asap7_tech.lef").read_text()
    corner = 'CORNERSPACING CONVEXCORNER CORNERONLY 0.01 WIDTH 0.018 SPACING 0.018 ;'
    if tech.count(corner) != 1:
        raise RuntimeError("tech LEF's M1 corner spacing rule changed")
    (work / "tech.lef").write_text(tech.replace(corner, corner.replace("SPACING 0.018", "SPACING 0.020")))
    tcl = [
        "read_lef tech.lef",
        "read_lef blocks.lef",
        "read_def placed.def",
        "set_thread_count 2",
    ]
    for i, pitch in enumerate(
        (0.036, 0.036, 0.036, 0.048, 0.048, 0.064, 0.064, 0.080, 0.080), 1
    ):
        offset = DRM_TRACKS.get(i, (0, 0))[1] / 1000
        tcl.append(
            f"make_tracks M{i} -x_offset {offset} -y_offset {offset} -x_pitch {pitch} -y_pitch {pitch}"
        )
    tcl += side_pin_placements(external, probes, width, height, args.top_layer - 1, ports=ports,
                               dq_side="right" if single else None)
    tcl += [
        # The macro routes and pins on M1 up to --top-layer: odd, so the pins
        # take its horizontal neighbour below for the side edges and itself
        # for the top and bottom.  M7 by default: M8/M9 are the chip's, and
        # the public deck sizes them by length, which the tech LEF does not.
        # The signal pins are placed above; this places the supplies, unless
        # their stripes already are.
        *([] if stripes else
          [f"place_pins -hor_layers M{args.top_layer - 1} -ver_layers M{args.top_layer}"]),
        f"set_routing_layers -signal M1-M{args.top_layer}",
        "global_route",
        f"detailed_route -droute_end_iter {args.route_iterations} -output_drc macro_drc.rpt",
        "write_def routed.def",
        "write_db routed.odb",
        "exit",
    ]
    (work / "route.tcl").write_text("\n".join(tcl) + "\n")
    with (work / "route.log").open("w") as log:
        try:
            result = subprocess.run(
                [args.openroad, "-exit", "route.tcl"],
                cwd=work,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=args.route_timeout,
            )
        except subprocess.TimeoutExpired as error:
            # A route that does not converge in time is a failed one: widen.
            raise RoutingFailed(f"macro routing timed out after {args.route_timeout} s; "
                                f"see {work}/route.log") from error
    if result.returncode:
        raise RoutingFailed(f"macro routing failed; see {work}/route.log")
    # Distribution KLayout bindings may target system Python, while gdstk is
    # installed in a virtual environment with a different Python ABI.
    subprocess.run(
        [
            "python3",
            str(REPO / "scripts/def_to_gds.py"),
            "--def-file",
            str(work / "routed.def"),
            "--tech-lef",
            str(REPO / "tech/lef/asap7_tech.lef"),
            "--cell-lef",
            str(work / "blocks.lef"),
            "--macro-gds",
            str(work / "blocks.gds"),
            "--output-gds",
            str(work / "routed.gds"),
            "--top-cell",
            top_name,
        ],
        check=True,
    )
    lib = gdstk.read_gds(str(work / "routed.gds"))
    top = next(c for c in lib.cells if c.name == top_name)
    # KLayout writes DEF BPin rectangles on purpose 251. They are real
    # conductor landings as well as LEF pin markers, not label-only geometry.
    add_pin_conductors(top)
    for label in top.labels:
        label.text = stripe_pins.get(label.text, label.text)
    # The router does not know ASAP7's run-length rule on M4-M7; the gate
    # below proves the lengthened wires changed no net.
    top_prl = fix_short_parallel_runs(top)
    if single and top_prl[1]:
        # What the router's own wires cannot mend, a block's wire can: the
        # 6T IO block's bitlines cross its first leaf column on M4 and end
        # beside the second column's select tracks, where the router lands.
        # Lengthening is the wire's own metal; the gate below re-proves it.
        more = fix_short_parallel_runs(top, blocks_too=True)
        top_prl = (top_prl[0] + more[0], more[1])
    fill_m1_notches(top)
    lib.write_gds(str(work / "routed.gds"), timestamp=columns.FIXED_GDS_TIMESTAMP)
    # No final artifact is published until every physical terminal is joined
    # to its net and every distinct named net remains isolated.
    # The router's own violations first: one it could not mend (a short in
    # a crowded channel, x4x2x1 at 16:1) is a routing failure, which a wider
    # margin may route, not a connectivity defect of the blocks.
    check_route_drc(work / "macro_drc.rpt")
    verify(work / "routed.gds", manifest)
    # The LEF takes a pin shape for each pin label: one on every stripe.
    for net, x, y_top in stripes:
        point = (x / 1000, y_top / 2000)
        if not any(label.text == net and abs(label.origin[0] - point[0]) < 1e-3 for label in top.labels):
            top.add(gdstk.Label(net, point, layer=METALS[args.top_layer - 1], texttype=251))
    top.add(gdstk.rectangle((0, 0), (width, height), layer=100))
    # Name every net at a point the connectivity gate has just proven is on
    # it.  Transistor LVS uses matching names as starting points (it still
    # checks every connection), which it needs once two banks make the
    # columns interchangeable by topology alone; and a viewer shows the nets.
    named = {label.text for label in top.labels}
    for net, net_probes in probes.items():
        if net.startswith("private:") or net in named or net in ("vdd", "vss"):
            continue
        probe = net_probes[0]
        top.add(gdstk.Label(net, tuple(probe["point"]), layer=probe["layer"]))
    if single:
        # Port A inside, no port at the boundary: ce_n, A[], D[], Q[] (the
        # deck's top subckt and the Liberty say the same).
        for label in top.labels:
            label.text = single_port_name(label.text)
        # The manifest describes the published GDS: its names too.
        manifest["nets"] = {single_port_name(net): probes for net, probes in manifest["nets"].items()}
        manifest["external"] = sorted(single_port_name(net) for net in manifest["external"])
        (work / "connectivity.json").write_text(json.dumps(manifest, indent=2) + "\n")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    lib.write_gds(str(args.output), timestamp=columns.FIXED_GDS_TIMESTAMP)
    report = {
        "cell": top_name,
        "size_um": [width, height],
        "banks": args.banks,
        "shared_port_b": shared_b,
        "bits": args.bits,
        "margin_um": args.margin,
        "bitcell": args.bitcell,
        "floorplan": "controller band between two stacks of abutted column tiles "
        + ("(cap | 6T array | dummy | tap | IO)" if single
           else "(port A IO | array | shared port B IO | mirrored array | port A IO)" if shared_b
           else "(port A IO | array | port B IO)")
        + (", a wordline driver strip on each side" if single else ", a wordline driver strip pair on each side"),
        "wordlines_per_half": args.wordlines,
        "array_wordlines": array_wordlines,
        "tap_pitch": tap_pitch,
        "column_tiles": units * args.bits,
        "wordline_slice": wl_slices.slice_name(slice_load),
        "column_mux": args.mux,
        "cells_along_wordline": args.mux * segment,
        "wordline_segments_per_stack": segments,
        "wordline_strip_pairs": 2 * args.banks * segments,
        "checked_net_partitions": len(probes),
        "routing_drc_violations": 0,
        "short_parallel_runs_fixed": {"controller": ctrl_prl[0], "macro": top_prl[0]},
        "short_parallel_runs_left": ctrl_prl[1] + top_prl[1],
        "physical_connectivity": "PASS",
        "signoff_lvs": "not_run",
        "device_drc": "not_run",
        "characterization": "not_measured",
        "reports": str(work),
    }
    args.output.with_suffix(".physical.json").write_text(
        json.dumps(report, indent=2) + "\n"
    )
    print(
        f"PASS: {top_name}: {units * args.bits} column tiles, {2 * args.banks * segments} strip pairs, "
        f"{len(probes)} connected nets"
    )


#: The IO blocks and strips (chipforge, fins a half pitch off the bitcell's
#: row grid) sit half a nanometre off a tile's own grid.  With the band
#: abutting them, the tiles and strips carry half a nanometre inside their
#: masters, which puts every chipforge shape, and so the controller's first
#: row, on the DEF's grid; the bitcell's own metal takes the half instead.
HALF_NM = 0.0005

#: The ASAP7 DRM's absolute grids, which are the macro's frame (nm): every fin
#: centred 13.5 past a multiple of 27 (FIN.S.1), every gate 27 past a multiple
#: of 54 (GATE.S.1), and M4-M7's tracks (AUX.2) 12 past a multiple of 48 (M4,
#: M5) and 16 past one of 64 (M6, M7), a minimum-width wire's edges then on its
#: layer's grid (AUX.1).  A chip places the macro on a multiple of them all.
FIN_GRID, GATE_GRID = (27, 13.5), (54, 27)
DRM_TRACKS = {4: (48, 12), 5: (48, 12), 6: (64, 16), 7: (64, 16)}
FIN_LAYER, GATE_LAYER = 2, 7


def grid_phase(cell):
    """Where `cell`'s gates and fins fall in its own frame, nm: the commonest
    gate centre x mod 54 and fin centre y mod 27, to the half nanometre, None
    for a cell without them."""

    def commonest(layer, axis, pitch):
        counts = Counter()
        for poly in cell.get_polygons(layer=layer, datatype=0):
            low, high = poly.bounding_box()
            counts[round(1000 * (low[axis] + high[axis])) / 2 % pitch] += 1
        return counts.most_common(1)[0][0] if counts else None

    return commonest(GATE_LAYER, 0, GATE_GRID[0]), commonest(FIN_LAYER, 1, FIN_GRID[0])


def grid_shift(phase):
    """How far (um, per axis) a cell of `phase` moves in its master (`abstract`'s
    `shift`) for a whole-nanometre origin to put its gates and fins on the grids:
    half a nanometre where it has them on the other half."""
    return tuple(HALF_NM if p is not None and (want - p) % 1 else 0.0
                 for p, want in zip(phase, (GATE_GRID[1], FIN_GRID[1])))  # fmt: skip


def grid_rules(cell, phase, shift=(0.0, 0.0), tracks=()):
    """What the origin of a master `abstract` made of `cell` (moved `shift` in it)
    must meet for the cell's gates and fins, and the M`tracks` it was routed on
    in its own frame (the DRM's), to land on the macro's grids:
    ``(x_rules, y_rules)``, each ``[(pitch, residue)]`` in nm."""
    # The cell's origin in its master's frame.
    fx, fy = (round(1000 * (s - o), 1) for s, o in zip(shift, bbox_origin(cell)))
    gate, fin = phase
    x_rules = [] if gate is None else [(GATE_GRID[0], (GATE_GRID[1] - gate - fx) % GATE_GRID[0])]
    y_rules = [] if fin is None else [(FIN_GRID[0], (FIN_GRID[1] - fin - fy) % FIN_GRID[0])]
    for layer in tracks:
        pitch, _ = DRM_TRACKS[layer]
        # M5 and M7 run vertically: their tracks are x positions.
        (x_rules if layer % 2 else y_rules).append((pitch, -(fx if layer % 2 else fy) % pitch))
    for pitch, residue in x_rules + y_rules:
        if residue != int(residue):
            raise RuntimeError(f"{cell.name}: its grids need a half-nanometre shift in its master")
    return x_rules, y_rules


def snap(nominal, rules):
    """The least whole-nanometre position, um, at or past `nominal` (um) that
    meets every ``(pitch, residue)`` of `rules` (nm)."""
    start = math.ceil(round(nominal * 1000, 3))
    period = math.lcm(*(int(pitch) for pitch, _ in rules)) if rules else 1
    for v in range(start, start + period):
        if all((v - residue) % pitch == 0 for pitch, residue in rules):
            return v / 1000
    raise RuntimeError(f"no position meets {rules}")


def offset_rules(rules, offset):
    """`rules` on an origin `offset` um past a position, as rules on the position."""
    nm = round(offset * 1000, 3)
    return [(pitch, (residue - nm) % pitch) for pitch, residue in rules]


#: The lattice a block routed on the DRM's M4/M5 tracks in its own frame (the
#: controller) needs its frame's origin on, nm: the gate and fin pitches' and
#: the tracks' least common multiple, in x and in y.
TRACK_LATTICE = math.lcm(GATE_GRID[0], DRM_TRACKS[5][0])


def check_grid(instances, rules):
    """Raise unless every instance's origin meets its master's `grid_rules`."""
    off = []
    for inst, master, (x, y), _ in instances:
        x_rules, y_rules = rules[master]
        for value, axis_rules in ((x, x_rules), (y, y_rules)):
            nm = round(value * 1000, 3)
            if any((nm - residue) % pitch for pitch, residue in axis_rules):
                off.append(f"{inst} ({master}) at ({x:.4f}, {y:.4f})")
                break
    if off:
        raise RuntimeError("blocks off the DRM's fin, gate or track grids: " + ", ".join(off))

#: Keep-out around a strip in the controller's band: std-cell wells and
#: implants clear of the slices', and room for the strips' supply pins.
BAND_HALO_X, BAND_HALO_Y = 0.216, 0.27


@functools.cache
def _bitcell_row():
    """The bitcell's row pitch and the IO blocks' fin-grid offset in it, um."""
    bitcell = next(c for c in gdstk.read_gds(str(REPO / "tech/gds/sram_cell_8t.gds")).cells
                   if c.name == "sram_cell_8t")  # fmt: skip
    _, y0, _, y1 = columns.boundary_box(bitcell)
    return y1 - y0, columns.fin_grid_offset(bitcell) / 1000


def _end_reach(single):
    """How far an end tile's dummy row reaches past its array, and its IO blocks' rail offset, um.

    8T: a bitcell row, the blocks on the fin grid's offset in it.  6T: the
    end row and its margin; the blocks' leaves, and so their rails, stand
    half a fin pitch above the block's frame (`SidewaysIoColumnSpec`).
    """
    if single:
        import generate_asap7_6t_iocolumn as columns6

        return columns6.END_ROW_HEIGHT + columns6.END_ROW_MARGIN, FIN_HALF_PITCH
    return _bitcell_row()


def band_seams(single=False):
    """How far into an end tile the band reaches over its IO columns: ``(lower, upper)``.

    The lower stack's last tile ends a dummy row above its IO blocks, whose
    top edge sits the fin-grid offset above the array's last row; the upper
    stack's first tile starts a dummy row below its blocks' bottom edge,
    which sits the offset above the array's first row.
    """
    reach, offset = _end_reach(single)
    return reach - offset, reach + offset


def end_row_spans(tile, shared_b):
    """x ranges of a tile's dummy rows (over its cap and array), in the tile."""
    half = tile.references[0].cell if shared_b else tile
    spans = set()
    for ref in half.references:
        if ref.cell.name.startswith(("dp_array_end_rows", "end_row_")):
            (x0, _), (x1, _) = ref.bounding_box()
            spans.add((round(float(x0), 4), round(float(x1), 4)))
    width = columns.boundary_box(tile)[2]
    if shared_b:
        spans |= {(round(width - x1, 4), round(width - x0, 4)) for x0, x1 in spans}
    return sorted(spans)


def band_edges(tile, single=False):
    """Where the band takes over a tile's IO columns: ``(bottom, top)``, None at an end without a dummy row."""
    reach, offset = _end_reach(single)
    _, y0, _, y1 = columns.boundary_box(tile)
    if single:
        # The 6T tile's top end row is its bottom one mirrored.
        rows = [r for r in tile.references if r.cell.name.startswith("end_row_")]
        above, below = any(r.x_reflection for r in rows), any(not r.x_reflection for r in rows)
    else:
        above, below = y1 > 4 * reach + 1e-6, y0 < -1e-6
    top = y1 - reach + offset if above else None
    bottom = y0 + reach + offset if below else None
    return bottom, top


#: An M5 landing on an M4 pin (`add_m5_landings`): V4's side, M5's pitch,
#: the landing's length (V4.M5.EN.2 and M5.W.5), V4's reach into the M4
#: past its ends (V4.M4.EN.1), and the space it keeps from other M5 and V4.
V4_LAYER = 45
V4_SIDE, M5_PITCH, M5_LANDING = 0.024, 0.048, 0.072
V4_M4_END, M5_SPACE, V4_SPACE = 0.011, 0.040, 0.033


def add_m5_landings(cell, labels, x_macro):
    """Raise each M4-topped signal pin of a band tile onto a V4 and an M5 landing.

    Half a nanometre up in its master, the 6T tile's whole-nanometre M4
    lies between the macro's nanometres, and a router via on it leaves a
    half-nanometre step (M4.AUX.3; a half-nanometre via is off the
    manufacturing grid).  An M5 landing on the macro's M5 tracks is whole
    across: the router's via on it leaves a rectangle.  `x_macro` is where
    the cell's x = 0 lands in the macro; the landings are on its M5 tracks.
    """
    graph = MetalGraph(cell)
    m5 = [p.bounding_box() for p in cell.get_polygons(layer=METALS[4], datatype=0)]
    v4 = [p.bounding_box() for p in cell.get_polygons(layer=V4_LAYER, datatype=0)]
    done = set()
    for label in labels:
        root = graph.label_root(label)
        if root in done:
            continue
        done.add(root)
        polys = [p for i, p in enumerate(graph.polygons) if graph.root(i) == root and p.layer in METALS]
        if max(p.layer for p in polys) != METALS[3]:
            continue
        (x0, y0), (x1, y1) = max((p for p in polys if p.layer == METALS[3]), key=lambda p: p.area()).bounding_box()
        y = (y0 + y1) / 2
        lo, hi = y - M5_LANDING / 2, y + M5_LANDING / 2
        track = DRM_TRACKS[5][1] / 1000
        k = math.ceil((x0 + x_macro + V4_M4_END + V4_SIDE / 2 - track) / M5_PITCH)
        while (c := k * M5_PITCH + track - x_macro) + V4_SIDE / 2 + V4_M4_END <= x1 + 1e-9:
            a, b = c - V4_SIDE / 2, c + V4_SIDE / 2
            clear = all(b + M5_SPACE <= p0[0] or p1[0] + M5_SPACE <= a or hi + M5_SPACE <= p0[1]
                        or p1[1] + M5_SPACE <= lo for p0, p1 in m5) and all(
                b + V4_SPACE <= p0[0] or p1[0] + V4_SPACE <= a or y1 + V4_SPACE <= p0[1]
                or p1[1] + V4_SPACE <= y0 for p0, p1 in v4)  # fmt: skip
            if clear:
                cell.add(gdstk.rectangle((a, y0), (b, y1), layer=V4_LAYER),
                         gdstk.rectangle((a, lo), (b, hi), layer=METALS[4]))  # fmt: skip
                m5.append(((a, lo), (b, hi)))
                v4.append(((a, y0), (b, y1)))
                break
            k += 1
        else:
            raise RuntimeError(f"{cell.name}: no M5 track over {label.text}'s M4 for a landing")


#: GCUT (layer, datatype); how far a standard cell's cut reaches past its row's
#: edge, and the space between cuts (GCUT.S.3).
GCUT = (10, 0)
GCUT_REACH, GCUT_SPACE = 0.022, 0.035


def notch_band_edges(tile, spans, single=False):
    """Cut the tile's outline back to its IO blocks over the IO columns, at each end with a dummy row.

    The 6T tile's supply stripes run to its outline there: they stop at the
    blocks' edge, the controller's band taking the rest.
    """
    x0, y0, x1, y1 = columns.boundary_box(tile)
    bottom, top = band_edges(tile, single)
    cut = [gdstk.rectangle((x0, top), (x1, y1))] if top is not None else []
    if bottom is not None:
        cut.append(gdstk.rectangle((x0, y0), (x1, bottom)))
    if not cut:
        return
    keep = [gdstk.rectangle((a, y0), (b, y1)) for a, b in spans]
    taken = gdstk.boolean(cut, keep, "not")
    outline = gdstk.boolean(gdstk.boolean(gdstk.rectangle((x0, y0), (x1, y1)), taken, "not"), [], "or")
    if len(outline) != 1:
        raise RuntimeError(f"{tile.name}: the notched outline is not one polygon")
    tile.remove(*[p for p in tile.polygons if p.layer == 100 and p.datatype == 0])
    if single:
        for poly in [p for p in tile.polygons if p.layer != 100]:
            if gdstk.boolean(poly, taken, "and"):
                tile.remove(poly)
                for piece in gdstk.boolean(poly, taken, "not", layer=poly.layer, datatype=poly.datatype):
                    tile.add(piece)
        # The controller's top (bottom) row cuts its gates GCUT_REACH either
        # side of the rail it shares with an IO block; the block's own first
        # cut stands back from that rail, too little to clear one: bridge them
        # (6T, x64x8x1: GCUT.S.3 at 10 nm).
        for poly in tile.get_polygons(layer=GCUT[0], datatype=GCUT[1]):
            (a, lo), (b, hi) = poly.bounding_box()
            for edge, end, past in ((bottom, lo, -0.01), (top, hi, 0.01)):
                if edge is None or not 0 < (end - edge) * -past / 0.01 < GCUT_REACH + GCUT_SPACE:
                    continue
                # Only over the IO columns, where the band lies past the edge.
                if gdstk.boolean(gdstk.rectangle((a, edge), (b, edge + past)), taken, "and"):
                    tile.add(gdstk.rectangle((a, min(edge, end)), (b, max(edge, end)),
                                             layer=GCUT[0], datatype=GCUT[1]))  # fmt: skip
    outline[0].layer = 100
    tile.add(outline[0])


def plan_band(args, pair_masters, strip_placements, band_width, pair_height, keepouts, seams):
    """What the controller's place-and-route needs to take the strips into its band.

    ``strips.lef`` holds the strips' abstracts; ``plan.txt`` the band's
    width, the core area the strips and their halos take, the halos and the
    inset; ``band.tcl`` places the strips in a die of ``$band_height`` as
    fixed physical instances, connects their SEL/B pins to the controller's
    ``sel_hi``/``sel_lo`` ports so it routes them, and removes the instances
    before the controller is written out.

    The band's bottom and top edges are the stacks' IO block edges (`seams`
    into the end tiles), so its rows start and end on the blocks' VSS rails;
    over the caps and arrays the end tiles' dummy rows reach into it, and a
    keep-out block (all of M1-M5 obstructed) stands on each (`keepouts`,
    ``(x0, x1, side)``), removed with the strips.
    """
    out = args.plan_band
    out.mkdir(parents=True, exist_ok=True)
    seam_lo, seam_hi = seams
    keepout_lef, masters = [], {}
    for x0, x1, side in keepouts:
        size = (round(x1 - x0, 4), seam_lo if side == "lo" else seam_hi)
        if size not in masters:
            name = f"dp_band_keepout_{len(masters)}"
            masters[size] = name
            obs = "\n".join(f"    LAYER M{m} ;\n    RECT 0 0 {size[0]:.4f} {size[1]:.4f} ;" for m in range(1, 6))
            keepout_lef.append(f"MACRO {name}\n  CLASS BLOCK ;\n  ORIGIN 0 0 ;\n"
                               f"  SIZE {size[0]:.4f} BY {size[1]:.4f} ;\n  OBS\n{obs}\n  END\nEND {name}")  # fmt: skip
    (out / "strips.lef").write_text(
        'VERSION 5.8 ;\nBUSBITCHARS "[]" ;\nDIVIDERCHAR "/" ;\n'
        + "\n".join([*(m[2] for m in pair_masters.values()), *keepout_lef]) + "\nEND LIBRARY\n"
    )
    placed = strip_placements(0.0)
    reserved = sum((pair_masters[drawn][3][0] + 2 * BAND_HALO_X) * (pair_height + BAND_HALO_Y)
                   for _, drawn, _, _, _ in placed)  # fmt: skip
    reserved += sum((x1 - x0 + 2 * BAND_HALO_X) * (seam_lo if side == "lo" else seam_hi)
                    for x0, x1, side in keepouts)  # fmt: skip
    (out / "plan.txt").write_text(
        f"width {band_width:.4f}\nreserved {reserved:.4f}\ninset 0\n"
        f"halo_x {BAND_HALO_X}\nhalo_y {BAND_HALO_Y}\n"
        f"seam_lo {seam_lo:.4f}\nseam_hi {seam_hi:.4f}\n"
    )
    tcl = [
        "# Generated by compile_asap7_2rw.py --plan-band: the wordline strips in the controller's band.",
        f"read_lef {{{out / 'strips.lef'}}}",
        "set band_strips {}",
        "proc band_place_strips {die_height} {",
        "  global band_strips",
        "  set block [ord::get_db_block]",
        "  set dbu [$block getDbUnitsPerMicron]",
    ]
    for inst, drawn, bank, half, (x, y) in placed:
        # strip_placements(0) puts an upper pair `pair_height` below a zero-height
        # band's top; the strips' LEF carries half a nanometre of the placement.
        y = round(y - HALF_NM, 4)
        y_expr = f"{y:.4f}" if half == "lo" else f"$die_height + ({y:.4f})"
        tcl += [
            f"  set inst [odb::dbInst_create $block [[ord::get_db] findMaster dp_wl_strips_{drawn}] {inst}]",
            f"  $inst setLocation [expr {{round({x:.4f} * $dbu)}}] [expr {{round(({y_expr}) * $dbu)}}]",
            "  $inst setPlacementStatus FIRM",
            "  lappend band_strips $inst",
        ]
    for k, (x0, x1, side) in enumerate(keepouts):
        size = (round(x1 - x0, 4), seam_lo if side == "lo" else seam_hi)
        y_expr = "0" if side == "lo" else f"$die_height - {seam_hi:.4f}"
        tcl += [
            f"  set inst [odb::dbInst_create $block [[ord::get_db] findMaster {masters[size]}] KEEPOUT_{k}]",
            f"  $inst setLocation [expr {{round({x0:.4f} * $dbu)}}] [expr {{round(({y_expr}) * $dbu)}}]",
            "  $inst setPlacementStatus FIRM",
            "  lappend band_strips $inst",
        ]
    tcl += ["}", "proc band_connect_strips {} {", "  set block [ord::get_db_block]"]
    for inst, drawn, bank, half, _ in placed:
        for pin, info in pair_masters[drawn][1].items():
            if info.get("private") or info.get("abutted"):
                continue
            net = strip_net(info["net"], bank=bank, half=half, wordlines=args.wordlines)
            if net.startswith("sel_"):
                # A net the controller keeps whole for its port driver
                # (the 6T one's sel_hi/sel_lo) is opened for the connection.
                tcl += [f"  set net [[$block findBTerm {{{net}}}] getNet]",
                        "  set kept [$net isDoNotTouch]",
                        "  $net setDoNotTouch 0",
                        f"  [[$block findInst {inst}] findITerm {pin}] connect $net",
                        "  $net setDoNotTouch $kept"]  # fmt: skip
    tcl += ["}", "proc band_remove_strips {} {", "  global band_strips",
            # Removing a strip disconnects its pins from nets kept whole (above).
            "  foreach inst $band_strips {",
            "    foreach iterm [$inst getITerms] {",
            "      set net [$iterm getNet]",
            "      if {$net ne \"NULL\"} { $net setDoNotTouch 0 }",
            "    }",
            "    odb::dbInst_destroy $inst",
            "  }", "  set band_strips {}", "}"]  # fmt: skip
    (out / "band.tcl").write_text("\n".join(tcl) + "\n")
    print(f"PLAN: band {band_width:.3f} um wide, {len(placed)} strip pairs, {reserved:.2f} um^2 kept out")


def band_die(band):
    """The controller's die, as its place-and-route wrote it beside the plan: ``(width, height)``."""
    values = dict(line.split() for line in (band / "die.txt").read_text().splitlines() if line.strip())
    return float(values["width"]), float(values["height"])


#: ASAP7's run-length rule on the one-direction layers M4-M7 (M4.S.5 ...):
#: two wires on adjacent tracks either do not overlap or overlap by 44 nm.
#: The router does not know it; `fix_short_parallel_runs` mends its wires.
PRL_LAYERS = {40: True, 50: False, 60: True, 70: False}  # layer: horizontal
MIN_PRL, ADJACENT_GAP, TIP_SPACE = 44, 25, 40  # nm
#: M4.S.3/M6.S.3: the deck grows each horizontal wire 48 nm across its track
#: and wants 40 nm between facing ends of the grown wires, where an end lies
#: on real metal: a wire whose track is within 48 nm of another's.  A wire
#: that overlaps the other along the track, by TIP_OVERLAP (MIN_PRL on an
#: adjacent track), has no facing end left.
TIP_REACH, TIP_OVERLAP = 48, 10  # nm
TIP_LAYERS = {40, 60}


def fix_short_parallel_runs(top, blocks_too=False):
    """Lengthen the macro's own wires where two on adjacent tracks overlap by under 44 nm,
    or where two wires' ends within two tracks face each other closer than 40 nm.

    Of each such pair, a wire the router drew (never a block's metal, unless
    `blocks_too`: a hard cell's own bars, for a cell assembled from them) is
    extended along its track just far enough to overlap its neighbour by
    44 nm, if its track is clear (tip-to-tip spacing) and the extension
    leaves every other neighbour it now runs beside either clear of it or
    at 44 nm.  The extension is the wire's own metal, so no net changes.
    Returns ``(fixed, left)`` pair counts.
    """
    fixed_total = left_total = 0
    for layer, horizontal in PRL_LAYERS.items():
        def nm(poly):
            (a, b), (c, d) = poly.bounding_box()
            return [round(float(v) * 1000) for v in (a, b, c, d)]

        routed = top.get_polygons(layer=layer, datatype=0, depth=0)
        routed += [q for r in top.references if r.cell.name.startswith("VIA")
                   for q in r.get_polygons(layer=layer, datatype=0)]  # fmt: skip
        blocks = [q for r in top.references if not r.cell.name.startswith("VIA")
                  for q in r.get_polygons(layer=layer, datatype=0, depth=None)]  # fmt: skip
        if blocks_too:
            shapes = [(nm(q), True) for q in gdstk.boolean(routed + blocks, [], "or", precision=1e-4)]
        else:
            shapes = [(nm(q), True) for q in gdstk.boolean(routed, [], "or", precision=1e-4)]
            shapes += [(nm(q), False) for q in gdstk.boolean(blocks, [], "or", precision=1e-4)]
        # along: (start, end) on the track; across: (low, high) across it
        def along(b):
            return (b[0], b[2]) if horizontal else (b[1], b[3])

        def across(b):
            return (b[1], b[3]) if horizontal else (b[0], b[2])

        def overlap(a, b):
            return min(along(a)[1], along(b)[1]) - max(along(a)[0], along(b)[0])

        def gap(a, b):
            return max(across(a)[0], across(b)[0]) - min(across(a)[1], across(b)[1])

        def short(a, b):
            return 0 < gap(a, b) < ADJACENT_GAP and 0 < overlap(a, b) < MIN_PRL

        def grown(b):  # the deck's view: 48 nm wider either side of the track
            reach = TIP_REACH
            return [b[0], b[1] - reach, b[2], b[3] + reach] if horizontal else [b[0] - reach, b[1], b[2] + reach, b[3]]

        def tip(a, b):
            if not (layer in TIP_LAYERS and 0 < gap(a, b) < TIP_REACH and -TIP_SPACE < overlap(a, b) < 0):
                return False
            # The ends face across the gap between them, where both grown
            # wires reach, unless other wires' grown metal fills that gap.
            ga, gb = grown(a), grown(b)
            lo_end, hi_start = (along(a)[1], along(b)[0]) if along(a)[1] <= along(b)[0] else (along(b)[1], along(a)[0])
            lo, hi = max(across(ga)[0], across(gb)[0]), min(across(ga)[1], across(gb)[1])
            window = extended([0, lo, 0, hi] if horizontal else [lo, 0, hi, 0], lo_end, hi_start)
            fill = [gdstk.rectangle((g[0], g[1]), (g[2], g[3]))
                    for g in (grown(z) for z, _ in shapes if z is not a and z is not b)
                    if g[0] < window[2] and g[2] > window[0] and g[1] < window[3] and g[3] > window[1]]  # fmt: skip
            hole = gdstk.boolean(gdstk.rectangle((window[0], window[1]), (window[2], window[3])), fill, "not")
            return bool(hole)

        def extended(b, lo, hi):
            return [lo, b[1], hi, b[3]] if horizontal else [b[0], lo, b[2], hi]

        added = []
        fixed = 0
        for i in range(len(shapes)):
            for j in range(len(shapes)):
                if j == i or not shapes[i][1]:
                    continue
                x, y = shapes[i][0], shapes[j][0]
                if not short(x, y) and not tip(x, y):
                    continue
                width = across(x)[1] - across(x)[0]
                if width != (24 if layer in (40, 50) else 32):
                    continue  # one track wide only
                (x0, x1), (y0, y1) = along(x), along(y)
                options = []
                if short(x, y):
                    if y1 - max(x0, y0) >= MIN_PRL:  # extend x's far end towards y's
                        options.append((x0, max(x1, max(x0, y0) + MIN_PRL)))
                    if min(x1, y1) - y0 >= MIN_PRL:  # or its near end
                        options.append((min(x0, min(x1, y1) - MIN_PRL), x1))
                else:  # past y's facing end, so no two ends face each other
                    need = MIN_PRL if gap(x, y) < ADJACENT_GAP else TIP_OVERLAP
                    options.append((x0, y0 + need) if x1 <= y0 else (y1 - need, x1))
                for lo, hi in sorted(options, key=lambda o: (o[1] - o[0])):
                    new = extended(x, lo, hi)
                    ok = True
                    for k, (z, _) in enumerate(shapes):
                        if k == i:
                            continue
                        g = gap(new, z)
                        if g <= 0:  # the same track (or touching): keep the tip spacing
                            if overlap(new, z) > -TIP_SPACE and overlap(x, z) <= -TIP_SPACE:
                                ok = False
                                break
                            if overlap(new, z) > 0 and overlap(x, z) <= 0:
                                ok = False
                                break
                        elif g < ADJACENT_GAP:
                            before, after = overlap(x, z), overlap(new, z)
                            if after != before and not (after >= MIN_PRL or after <= -TIP_SPACE):
                                ok = False
                                break
                        if g > 0 and tip(new, z) and not tip(x, z):
                            ok = False
                            break
                    if ok:
                        shapes[i] = (new, True)
                        a, b = along(x), (lo, hi)
                        for seg in ((b[0], a[0]), (a[1], b[1])):
                            if seg[1] > seg[0]:
                                added.append(extended(x, *seg))
                        fixed += 1
                        break
        for b in added:
            top.add(gdstk.rectangle((b[0] / 1000, b[1] / 1000), (b[2] / 1000, b[3] / 1000), layer=layer))
        left = sum(1 for i in range(len(shapes)) for j in range(i + 1, len(shapes))
                   if (shapes[i][1] or shapes[j][1])
                   and (short(shapes[i][0], shapes[j][0]) or tip(shapes[i][0], shapes[j][0])))  # fmt: skip
        fixed_total += fixed
        left_total += left
    return fixed_total, left_total


def add_pin_conductors(cell):
    """DEF pin rectangles are conductors as well as pin-purpose markers."""
    for polygon in list(cell.polygons):
        if polygon.layer in METALS and polygon.datatype == 251:
            drawing = polygon.copy()
            drawing.datatype = 0
            cell.add(drawing)


#: M1.S.2/M1.S.6: 25 nm between short M1 edges, 20 nm corner to corner,
#: within one net's merged metal as well as between nets.
M1_NOTCH, M1_CLEAR = 0.025, 0.020


def fill_m1_notches(top):
    """Fill the notches where the router's M1 lands on a block's M1 off its track.

    A tile's M1 sits at several phases of the 36 nm M1 grid, so a router wire
    landing on one overlaps it a few nanometres off centre, and the merged
    metal keeps a notch narrower than M1.S.6's 20 nm.  Each router shape with
    everything on M1 it touches is closed over gaps under 25 nm; a filler
    piece is drawn where it stays 20 nm from all other M1.  Returns the count.
    """
    layer = 19
    routed = top.get_polygons(layer=layer, datatype=0, depth=0)
    routed += [q for r in top.references if r.cell.name.startswith("VIA")
               for q in r.get_polygons(layer=layer, datatype=0)]  # fmt: skip
    blocks = [q for r in top.references if not r.cell.name.startswith("VIA")
              for q in r.get_polygons(layer=layer, datatype=0, depth=None)]  # fmt: skip
    every = routed + blocks
    boxes = [q.bounding_box() for q in every]

    def near(box, reach):
        (a, b), (c, d) = box
        return [i for i, ((x0, y0), (x1, y1)) in enumerate(boxes)
                if x0 < c + reach and x1 > a - reach and y0 < d + reach and y1 > b - reach]  # fmt: skip

    filled = 0
    for shape in routed:
        # Only metal that really meets the shape: the same net.  (Boxes that
        # merely overlap can belong to another net, which closing would join.)
        reach = gdstk.offset(shape, 1e-4, join="miter", precision=1e-5)
        touching = [every[i] for i in near(shape.bounding_box(), 1e-6)
                    if gdstk.boolean(reach, every[i], "and", precision=1e-5)]  # fmt: skip
        patches = [(patch, True) for patch in jog_patches(shape, [t for t in touching if t is not shape])]
        merged = gdstk.boolean(touching, [], "or", precision=1e-4)
        half = M1_NOTCH / 2
        closed = gdstk.boolean(gdstk.offset(gdstk.offset(merged, half, join="miter", precision=1e-4), -half,
                                            join="miter", precision=1e-4), [], "or", precision=1e-4)  # fmt: skip
        patches += [(patch, False) for patch in gdstk.boolean(closed, merged, "not", precision=1e-4)]
        for patch, jog in patches:
            (a, b), (c, d) = patch.bounding_box()
            if patch.area() < 1e-8 or (min(c - a, d - b) >= M1_NOTCH and not jog):
                continue
            others = [every[i] for i in near(patch.bounding_box(), M1_CLEAR)
                      if not any(every[i] is t for t in touching)]  # fmt: skip
            grown = gdstk.offset(patch, M1_CLEAR - 1e-4, join="round", precision=1e-4)
            if others and gdstk.boolean(grown, others, "and", precision=1e-4):
                continue
            patch.layer = layer
            top.add(patch)
            every.append(patch)
            boxes.append(patch.bounding_box())
            filled += 1
    return filled


def jog_patches(shape, touching):
    """Square up where a router wire meets a block's wire a fraction of a nanometre to one side.

    The two overlap only briefly along their run, so the merged metal is a
    jog whose corners stand 17.5 nm apart across it (M1.W.1): the patch
    makes the junction their combined width over the overlap and one wire
    width either side.
    """
    (rx0, ry0), (rx1, ry1) = shape.bounding_box()
    out = []
    for other in touching:
        (bx0, by0), (bx1, by1) = other.bounding_box()
        for along, lo, hi in ((1, (ry0, by0), (ry1, by1)), (0, (rx0, bx0), (rx1, bx1))):
            # `along` = 1: wires running in y, offset in x; 0: running in x, offset in y.
            side = ((rx0, rx1), (bx0, bx1)) if along else ((ry0, ry1), (by0, by1))
            (s0, s1), (o0, o1) = side
            if (s0, s1) == (o0, o1) or abs(s0 - o0) > 0.002 or abs(s1 - o1) > 0.002:
                continue
            overlap = (max(lo), min(hi))
            if not 0 < overlap[1] - overlap[0] < 0.018:
                continue
            start = max(min(lo), overlap[0] - 0.018)
            end = min(max(hi), overlap[1] + 0.018)
            box = ((min(s0, o0), start), (max(s1, o1), end)) if along else ((start, min(s0, o0)), (end, max(s1, o1)))
            out.append(gdstk.rectangle(*box))
    return out


#: Side-edge pins: track pitch and wire width per horizontal layer (nm), and
#: how far a pin reaches into the macro.
SIDE_PIN_LAYERS = {4: (48, 24), 6: (64, 32)}
SIDE_PIN_DEPTH = 69
#: The side pins' pitch: the controller's own input pitch (openroad_tcl_generator's
#: place_pins -min_distance), and a whole number of M4 and M6 tracks, so each
#: pin stands level with its controller input.  At two M6 tracks (128 nm)
#: against the controller's 96 the offsets grew a pin at a time until the
#: address nets' M5 jogs onto the controller's edge column overlapped
#: (x16x8x1 with narrower tiles: M5 shorts on A[0..2] at a 0.3 um margin).
SIDE_PIN_PITCH = 192

#: Supply stripes: track pitch and wire width per vertical top layer (nm).
#: Minimum width, as the router's V6 must be exactly as wide as the M7 it
#: lands on (V6.M7.AUX.2); its ends on the layer's 32 (24) nm line-end grid.
STRIPE_LAYERS = {5: (48, 24), 7: (64, 32), 9: (80, 40)}


def power_stripes(width, height, layer, pitch):
    """The macro's supply grid on its top layer: ``[(net, x_centre, y_top)]`` in nm.

    Full-height vertical stripes, vdd and vss alternating, an even count
    spread over the width about one stripe per ``pitch / 2`` um, each on a
    routing track.  The chip's own grid lands on them from the layer above;
    the router joins every block's supply pins to them.
    """
    track, wire = STRIPE_LAYERS[layer]
    offset = DRM_TRACKS.get(layer, (track, 0))[1]
    count = 2 * max(1, round(width / pitch))
    top = int(1000 * height) // wire * wire
    stripes = []
    for k in range(count):
        x = round((1000 * width * (k + 0.5) / count - offset) / track) * track + offset
        stripes.append(("vdd" if k % 2 == 0 else "vss", x, top))
    if len({x for _, x, _ in stripes}) < count:
        raise RuntimeError(f"{count} supply stripes do not fit in {width} um")
    return stripes


def single_port_name(net):
    """The single-port macro's boundary name for port A's: ``ce_n_A`` -> ``ce_n``, ``A_A[3]`` -> ``A[3]``."""
    net = re.sub(r"^(ce|we|oe)_n_A$", r"\1_n", net)
    return re.sub(r"^([ADQ])_A\[", r"\1[", net)


def side_pin_placements(external, probes, width, height, layer, ports=("A", "B"), dq_side=None):
    """``place_pin`` commands for the signal pins: port A's on the left edge, B's on the right.

    With port A alone (the single-port macro) clk and rst_n join port A's
    controls on the left.  ``dq_side`` puts every D and Q on that edge
    instead, the one at the IO end of the bitlines (the 6T tile's IO is at
    its right end).

    A bit's D and Q go level with the tile terminals they drive and read; a
    port's address bus then its enables (port B's followed by clk and rst_n)
    go level with the controller's inputs, in index order.  Groups keep their
    order bottom to top and stand at least two tracks apart.
    """
    pitch, wire = SIDE_PIN_LAYERS[layer]
    offset = DRM_TRACKS[layer][1]

    def index(net):
        match = re.search(r"\[(\d+)\]$", net)
        return int(match.group(1)) if match else -1

    def target(net):
        ys = [probe["point"][1] for probe in probes[net] if probe["instance"] != "PIN"]
        if not ys:
            raise RuntimeError(f"{net}: no terminal to place its pin by")
        return 1000 * sum(ys) / len(ys)

    edges = {"left": [], "right": []}
    for port, side in (("A", "left"), ("B", "right"))[: len(ports)]:
        for bit in sorted({index(n) for n in external if re.fullmatch(rf"[DQ]_{port}\[\d+\]", n)}):
            pair = sorted((n for n in (f"D_{port}[{bit}]", f"Q_{port}[{bit}]") if n in external), key=target)
            edges[dq_side or side].append(pair)
        controls = sorted((n for n in external if n.startswith(f"A_{port}[")), key=index)
        controls += [n for n in (f"ce_n_{port}", f"we_n_{port}", f"oe_n_{port}") if n in external]
        if port == ports[-1]:
            controls += [n for n in ("clk", "rst_n") if n in external]
        edges[side].append(controls)
    commands = []
    for side, groups in edges.items():
        groups.sort(key=lambda group: sum(map(target, group)) / len(group))
        sep = SIDE_PIN_PITCH
        y_next = SIDE_PIN_PITCH + offset
        for group in groups:
            centre = sum(map(target, group)) / len(group)
            start = max(round((centre - (len(group) - 1) * sep / 2 - offset) / pitch) * pitch + offset, y_next)
            for k, net in enumerate(group):
                y = start + k * sep
                x = SIDE_PIN_DEPTH / 2 if side == "left" else round(1000 * width) - SIDE_PIN_DEPTH / 2
                commands.append(
                    f"place_pin -pin_name {{{net}}} -layer M{layer} -location {{{x / 1000:.4f} {y / 1000:.3f}}}"
                    f" -pin_size {{{SIDE_PIN_DEPTH / 1000:.3f} {wire / 1000:.3f}}}"
                )
            y_next = start + len(group) * sep
        if y_next > round(1000 * height) - pitch:
            raise RuntimeError(f"the {side} edge's pins do not fit")
    return commands


def check_route_drc(report):
    if not report.is_file():
        raise RuntimeError(f"missing routing DRC report: {report}")
    if report.read_text().strip():
        raise RoutingFailed(f"macro routing DRC violations remain: {report}")


def verify(path, manifest):
    lib = gdstk.read_gds(str(path))
    top = next(c for c in lib.cells if c.name == manifest["cell"])
    graph = MetalGraph(top)
    roots = {}
    for net, probes in manifest["nets"].items():
        components = set()
        for probe in probes:
            found = graph.at(probe["layer"], probe["point"])
            if len(found) != 1:
                raise RuntimeError(f"{net}: missing/ambiguous terminal {probe}")
            components.update(found)
        if len(components) != 1:
            raise RuntimeError(
                f"{net}: {len(components)} disconnected physical components"
            )
        root = components.pop()
        if root in roots:
            raise RuntimeError(f"short: {net} / {roots[root]}")
        roots[root] = net
    labels = {label.text: label for label in top.labels}
    for net in manifest["external"]:
        if net not in labels or roots.get(graph.label_root(labels[net])) != net:
            raise RuntimeError(f"external pin {net} missing or disconnected")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wordlines", type=int, required=True)
    parser.add_argument("--bits", type=int, required=True)
    parser.add_argument("--banks", type=int, default=1)
    parser.add_argument("--controller", type=Path)
    parser.add_argument("--plan-band", type=Path, default=None,
                        help="write the controller band's plan (strips, keep-outs) here and stop")  # fmt: skip
    parser.add_argument("--band", type=Path, default=None,
                        help="a plan whose controller took the strips into its band (die.txt written)")  # fmt: skip
    parser.add_argument("--work", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--openroad", default="openroad")
    parser.add_argument("--route-iterations", type=int, default=64)
    # The band's shifted abstracts leave the router a few stubborn tiles:
    # x8x8x2 takes about half an hour.
    parser.add_argument("--route-timeout", type=int, default=3600)
    parser.add_argument("--channel-width", type=float, default=0.3,
                        help="um between the controller and each strip pair")  # fmt: skip
    parser.add_argument("--margin", type=float, default=0.3,
                        help="um from the blocks to the macro edge, where the pins land")  # fmt: skip
    parser.add_argument("--bank-gap", type=float, default=0.3, help="um between banks")
    parser.add_argument("--strap-pitch", type=int, default=0,
                        help="every this many array taps is a supply strap (M5 spines down the tap column)")
    parser.add_argument("--bitcell", choices=("8t", "6t"), default="8t",
                        help="8t: the two-port macro; 6t: single port, the released 6T array with port A's IO")
    parser.add_argument("--mux", type=int, default=4,
                        help="column mux ratio: 4, or 8 or 16 for the single-port macro")
    parser.add_argument("--segment-bits", type=int, default=0,
                        help="divided wordlines: cut each stack into segments of this many data bits, "
                        "a mid strip pair between two (single-port macro)")
    parser.add_argument("--share-port-b", action="store_true",
                        help="banks in pairs, mirrored about one two-sided port-B IO block "
                        "(the controller built with SHARED_B)")  # fmt: skip
    parser.add_argument(
        "--margin-y",
        type=float,
        default=None,
        help="top/bottom margin, if not --margin",
    )
    parser.add_argument(
        "--top-layer",
        type=int,
        default=7,
        choices=(5, 7, 9),
        help="highest metal the macro routes and pins on",
    )
    parser.add_argument("--power-pitch", type=float, default=1.28,
                        help="um between vdd stripes on the top layer (vss halfway); 0: one supply pin each")  # fmt: skip
    parser.add_argument(
        "--max-margin",
        type=float,
        default=1.2,
        help="widen the margin (doubling) up to this if routing fails",
    )
    args = parser.parse_args()
    if args.plan_band is None and None in (args.controller, args.work, args.output):
        parser.error("--controller, --work and --output are required unless --plan-band")
    if args.plan_band is not None:
        run(args)
        return
    # The margin is where the macro pins land and the controller's outputs
    # turn; how much the router needs depends on where it put the pins,
    # which is not known before routing.  Start tight, widen on failure.
    while True:
        try:
            run(args)
            return
        except RoutingFailed as error:
            wider = round(args.margin * 2, 3)
            if wider > args.max_margin:
                raise RuntimeError(
                    f"{error}; margin {args.margin} um is the widest tried"
                ) from error
            print(
                f"routing failed at a {args.margin} um margin; retrying at {wider} um"
            )
            args.margin = wider


class RoutingFailed(RuntimeError):
    """OpenROAD could not route the assembled macro."""


if __name__ == "__main__":
    main()
