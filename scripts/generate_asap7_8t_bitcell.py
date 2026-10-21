#!/usr/bin/env python3
"""Generate and verify OpenFinRAM's ASAP7 8T bitcell and edge-cell family.

The logical topology follows OpenRAM's public dual-port cell:

  https://github.com/VLSIDA/sky130_fd_bd_sram/tree/
      fc63b12883b4bf458ee8c756ba64c37063e1ffb9/cells/openram_dp_cell

Only the topology and port convention are used.  The geometry is native ASAP7:
the published ``sram_cell_6t_122`` core is copied from OpenFinRAM's tracked
ASAP7 SRAM GDS and two 2-fin access NMOS devices are added on the same 27 nm
fin / 54 nm gate grid.  Port A retains the core's M2 bitlines and M3 wordline;
port B is routed on M4/M5 to avoid disturbing the compact 6T-core routing.

Pin mapping from the OpenRAM reference is:

  WL0  BL0  BR0  WL1  BL1  BR1
  WLA  BLA  BLAN  WLB  BLB  BLBN

The second generated GDS contains the forced-state dummy, explicit oriented
edge masters, parameterized dummy rows, and blank fillers at the 8T pitch.

Shared geometric transformations, in-memory connectivity extraction,
and verification routines are delegated to ``chipforge_asap7``.

"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import gdstk
from chipforge_asap7.layout.layers import LAYERS
from chipforge_asap7.layout.transform import (
    bbox,
    clip_gcut_to_cell,
    clone_label,
    clone_polygons,
    copy_labels,
    dummy_array_name,
    edge_boundary,
    is_box,
    oriented_cell,
    oriented_name,
    rect,
    snap_sdt_heights,
    topbot_name,
)
from chipforge_asap7.verification.connectivity import (
    DisjointSetUnion,
    extract_connectivity,
    filtered_polygon_fingerprint,
    layer_polygons,
    overlap,
    polygon_fingerprint,
    process_fingerprint,
    verify_rules,
    verify_tap_rules,
)
from chipforge_asap7.verification.sram import (
    ACCESS_NFIN,
    CELL_RATIO,
    CONCURRENT_CELL_RATIO,
    CORE_EDGE_CELL_NAMES,
    EDGE_CELL_NAMES,
    ORIENTATIONS,
    PULLDOWN_NFIN,
    PULLUP_NFIN,
    PULLUP_RATIO,
    RECOMMENDED_MIN_CELL_RATIO,
    group_tie_bands,
    is_bitline_m4_rail,
    is_full_m2_rail,
    sizing_report,
    supply_rails,
    tie_bands,
    verify_cap_topology,
    verify_dummy_topology,
    verify_edge_gds,
    verify_gds,
    verify_strap_topology,
    verify_tap_gds,
    verify_tap_topology,
    verify_topology,
    wlb_landing_tracks,
)

# Compatibility aliases
_Dsu = DisjointSetUnion
_overlap = overlap
_layer_polygons = layer_polygons


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


CELL_NAME = "sram_cell_8t"
SOURCE_CELL = "sram_cell_6t_122"
TAP_CELL_NAME = "tapcell_sram_8t"
STRAP_CELL_NAME = "strapcell_sram_8t"
FIXED_GDS_TIMESTAMP = dt.datetime(2020, 1, 1, 0, 0, 0)

# ASAP7 GDS drawing layers from chipforge_asap7 LAYERS.
WELL = LAYERS["NWELL"]["layer"]
FIN = LAYERS["FIN"]["layer"]
GATE = LAYERS["GATE"]["layer"]
GCUT = LAYERS["GATE_CUT"]["layer"]
ACTIVE = LAYERS["ACTIVE"]["layer"]
NSELECT = LAYERS["NSELECT"]["layer"]
PSELECT = LAYERS["PSELECT"]["layer"]
LIG = LAYERS["LIG"]["layer"]
LISD = LAYERS["LISD"]["layer"]
V0 = LAYERS["V0"]["layer"]
M1 = LAYERS["M1"]["layer"]
M2 = LAYERS["M2"]["layer"]
V1 = LAYERS["V1"]["layer"]
V2 = LAYERS["V2"]["layer"]
M3 = LAYERS["M3"]["layer"]
V3 = LAYERS["V3"]["layer"]
M4 = LAYERS["M4"]["layer"]
V4 = LAYERS["V4"]["layer"]
M5 = LAYERS["M5"]["layer"]
SDT = LAYERS["SDT"]["layer"]
SRAMDRC = LAYERS["SRAMDRC"]["layer"]
BOUNDARY = LAYERS["BOUNDARY"]["layer"]
SRAMVT = LAYERS["SRAMVT"]["layer"]

PIN_TEXTTYPE = 251
MARKER_LAYERS = {SRAMDRC, BOUNDARY, SRAMVT}

# Coordinates are micrometers.  Port B grows the cell north/south while the
# east/west pitch remains the published 108 nm 6T pitch.  As in the official
# bitcell, FIN, select, ACTIVE, and horizontal rails deliberately overhang the
# BOUNDARY so mirrored neighbors merge at the placement seam.
MARKER = (0.000, -0.162, 0.108, 0.432)

# Well/substrate tie geometry.  The tap is two bitcell slots wide.  Its tie
# implants are the complement of its neighbours' at every band, and a
# neighbouring bitcell's ACTIVE and select overhang the seam by 8 and 27 nm,
# so the tap's own implants start 27 nm in: on a one-slot tap that left a
# 54 nm implant, under the 108 nm minimum (PSELECT.W.1), and a full-width one
# doubly implanted the neighbour's bitline diffusion into a substrate tie.
# Two slots leave 162 nm.  The tie diffusions sit between the second slot's
# two gate columns on the bitcell's own fin grid, so no gate can cross them
# and the tap stays device-free while keeping the array's FIN and poly pitch.
TAP_SLOTS = 2
TAP_MARKER = (0.000, -0.162, TAP_SLOTS * 0.108, 0.432)
TAP_IMPLANT_INSET = 0.027
TAP_TIE_OFFSET = (TAP_SLOTS - 1) * 0.108 / 2
TAP_ACTIVE_X = (0.046 + TAP_TIE_OFFSET, 0.062 + TAP_TIE_OFFSET)
TAP_CONTACT_X = (0.042 + TAP_TIE_OFFSET, 0.066 + TAP_TIE_OFFSET)
TAP_VIA_X = (0.045 + TAP_TIE_OFFSET, 0.063 + TAP_TIE_OFFSET)
TAP_VIA_HEIGHT = 0.018
TAP_BAND_INSET = 0.0135

# Power-strap geometry.  The bitcell fills M1-M5 (M3 is WLA, M4 carries the
# port-B bitlines, M5 is WLB), so the only place a supply can climb out of the
# M2 rails is the tap column, where M3 and M5 are unused.  M3 still has to
# clear the 9 nm neighbour overhang at both slot edges, which pins the climb to
# the cell centre; M5 is free across the whole slot, so the two spines sit on
# the gate-column centres.
STRAP_CLIMB_X = 0.054 + TAP_TIE_OFFSET
STRAP_SPINE_X = {"vss!": 0.027 + TAP_TIE_OFFSET, "vdd!": 0.081 + TAP_TIE_OFFSET}
STRAP_STEP_Y = 0.050
STRAP_M5_HALF = 0.012
GATE_A = (0.017, -0.181, 0.037, 0.439)
GATE_B = (0.071, -0.169, 0.091, 0.451)
WLA_M3 = (0.045, -0.162, 0.063, 0.432)
WLB_M5 = (0.048, -0.162, 0.072, 0.432)

#: Alternating mirrored slots put the WLB landings on different M4 tracks.
#: Both variants use x=18 nm for the M3 bridge: the x=90 nm corridor remains
#: available to the macro router. The former variant's x=90 bridge stopped
#: x16x16 routing (see commit 51d400c).
#: BLBN's M4 bar moves from y=-84 to -36 nm, with its upper via stack 18 nm
#: into the unmirrored cell. Variant B uses x=-18 so the shared stacks coincide
#: at the mirrored seam, leaving its x=18 WLB bridge a legal 18 nm M3 gap.
#: This bridge can now reach y=12 without crossing the BLBN stack. BLB and
#: the cell boundary, devices and external wordline tracks do not move.
WLB_LANDINGS = {False: (-0.132, 0.396), True: (0.012, 0.300)}
VARIANT_B_NAME = CELL_NAME + "_b"
VARIANT_B_END_NAME = CELL_NAME + "_b_end"


def clone_base(source: gdstk.Cell, target: gdstk.Cell) -> None:
    """Copy the official core, replacing only shapes that must be extended."""
    gate_a = (0.017, -0.019, 0.037, 0.277)
    gate_b = (0.071, -0.007, 0.091, 0.289)
    # Relocate the original WLA gate contacts so that an M1 strap can reach
    # each storage node without crossing the wordline.  These are routing
    # shapes only; all six original device channels remain untouched.
    relocated = {
        (LIG, (0.022, -0.008, 0.054, 0.008)): "lig_a",
        (V0, (0.0305, -0.009, 0.0485, 0.009)): "v0_a",
        (M1, (0.0305, -0.019, 0.0485, 0.019)): "m1_a",
        (M2, (0.0215, -0.009, 0.072, 0.009)): "m2_a",
        (V1, (0.0305, -0.009, 0.0485, 0.009)): "v1_a",
        (V2, (0.045, -0.009, 0.063, 0.009)): "v2_a",
        (M3, (0.045, -0.040, 0.063, 0.310)): "m3_wla",
        (LIG, (0.054, 0.262, 0.086, 0.278)): "lig_b",
        (V0, (0.0595, 0.261, 0.0775, 0.279)): "v0_b",
        (M1, (0.0595, 0.251, 0.0775, 0.289)): "m1_b",
        (M2, (0.036, 0.261, 0.0865, 0.279)): "m2_b",
        (V1, (0.0595, 0.261, 0.0775, 0.279)): "v1_b",
        (V2, (0.045, 0.261, 0.063, 0.279)): "v2_b",
    }
    full_width_m2 = {
        (-0.027, 0.126, 0.135, 0.144),
        (-0.026, 0.0745, 0.136, 0.0925),
        (-0.027, 0.225, 0.135, 0.243),
        (-0.026, 0.1775, 0.136, 0.1955),
        (-0.027, 0.027, 0.135, 0.045),
    }

    replaced = {"gate_a": False, "gate_b": False}
    relocated_found = set()
    for poly in source.polygons:
        if poly.datatype != 0:
            target.add(
                gdstk.Polygon(poly.points.copy(), layer=poly.layer,
                              datatype=poly.datatype)
            )
            continue
        if poly.layer in MARKER_LAYERS:
            continue
        if poly.layer == FIN:
            _x0, y0, _x1, y1 = bbox(poly)
            rect(target, (-0.027, y0, 0.135, y1), FIN)
            continue
        if poly.layer == GATE and is_box(poly, gate_a):
            # Retain the official gate's 19/7 nm boundary overhang while
            # extending it through the taller cell.
            rect(target, GATE_A, GATE)
            replaced["gate_a"] = True
            continue
        if poly.layer == GATE and is_box(poly, gate_b):
            # Retain the complementary 7/19 nm official overhang.
            rect(target, GATE_B, GATE)
            replaced["gate_b"] = True
            continue
        key = (poly.layer, bbox(poly))
        if key in relocated:
            relocated_found.add(relocated[key])
            continue
        if poly.layer == M2 and bbox(poly) in full_width_m2:
            x0, y0, x1, y1 = bbox(poly)
            rect(target, (x0, y0, x1, y1), M2)
            continue
        target.add(
            gdstk.Polygon(poly.points.copy(), layer=poly.layer,
                          datatype=poly.datatype)
        )

    missing = [name for name, found in replaced.items() if not found]
    missing.extend(sorted(set(relocated.values()) - relocated_found))
    if missing:
        raise RuntimeError(f"source 6T geometry changed; missing shapes: {', '.join(missing)}")

    rename = {"WL": "WLA", "BL": "BLA", "BLN": "BLAN"}
    for label in source.labels:
        text = rename.get(label.text, label.text)
        # Place the wordline label on the exact center of its M3 trunk so
        # mirrored array instances retain the 108 nm address pitch.
        x = 0.054 if text == "WLA" else float(label.origin[0])
        target.add(
            gdstk.Label(
                text,
                (x, float(label.origin[1])),
                anchor=label.anchor,
                rotation=label.rotation,
                magnification=label.magnification,
                x_reflection=label.x_reflection,
                layer=label.layer,
                texttype=label.texttype,
            )
        )


def add_via_stack_to_m4(
    cell: gdstk.Cell,
    cx: float,
    cy: float,
    *,
    v0_cy: float | None = None,
) -> None:
    """Connect an LISD terminal to an M4 horizontal track at (cx, cy)."""
    landing_y = cy if v0_cy is None else v0_cy
    rect(
        cell,
        (cx - 0.009, landing_y - 0.009, cx + 0.009, landing_y + 0.009),
        V0,
    )
    rect(cell, (cx - 0.009, cy - 0.014, cx + 0.009, cy + 0.014), M1)
    rect(cell, (cx - 0.009, cy - 0.009, cx + 0.009, cy + 0.009), V1)
    # 6 nm of M2 past V1/V2 each side: the public deck's V1.M2.EN.2 opening
    # drops an end cap of exactly 5 nm.
    rect(cell, (cx - 0.015, cy - 0.009, cx + 0.015, cy + 0.009), M2)
    rect(cell, (cx - 0.009, cy - 0.009, cx + 0.009, cy + 0.009), V2)
    rect(cell, (cx - 0.009, cy - 0.017, cx + 0.009, cy + 0.017), M3)
    rect(cell, (cx - 0.009, cy - 0.012, cx + 0.009, cy + 0.012), V3)


def add_gate_to_m5(
    cell: gdstk.Cell, gate_via_x: float, bridge_x: float, y: float,
    landing_y: float | None = None,
) -> None:
    """Connect an LIG wordline contact to the vertical WLB route on M5.

    The contact climbs to M2, runs to the M3 bridge, and the bridge carries
    it to the M4 landing on `landing_y` (the contact's own track unless
    given), where a V4 reaches the WLB trunk.
    """
    landing_y = y if landing_y is None else landing_y
    rect(cell, (gate_via_x - 0.009, y - 0.009,
                gate_via_x + 0.009, y + 0.009), V0)
    rect(cell, (gate_via_x - 0.009, y - 0.019,
                gate_via_x + 0.009, y + 0.019), M1)
    rect(cell, (gate_via_x - 0.009, y - 0.009,
                gate_via_x + 0.009, y + 0.009), V1)
    rect(cell, (min(gate_via_x, bridge_x) - 0.014, y - 0.009,
                max(gate_via_x, bridge_x) + 0.014, y + 0.009), M2)
    rect(cell, (bridge_x - 0.009, y - 0.009,
                bridge_x + 0.009, y + 0.009), V2)
    rect(cell, (bridge_x - 0.009, min(y, landing_y) - 0.017,
                bridge_x + 0.009, max(y, landing_y) + 0.017), M3)
    rect(cell, (bridge_x - 0.009, landing_y - 0.012,
                bridge_x + 0.009, landing_y + 0.012), V3)
    # The M4 landing runs from V3 to the V4 under the trunk, 11 nm past
    # each (V3.M4.EN.2 / V4.M4.EN.1), with M4.W.5's 44 nm minimum
    # horizontal length. The DRM has no 2,000 nm^2 M4 area rule.
    v4 = (0.048, 0.072)
    x0 = min(bridge_x - 0.009, v4[0]) - 0.011
    x1 = max(bridge_x + 0.009, v4[1]) + 0.011
    rect(cell, (x0, landing_y - 0.012, x1, landing_y + 0.012), M4)
    rect(cell, (v4[0], landing_y - 0.012, v4[1], landing_y + 0.012), V4)


def add_wla_routes(cell: gdstk.Cell) -> None:
    """Add the two WLA gate contacts and their common M3 route."""
    # Bottom WLA contact: on gate A, 16 nm clear of the west placement seam,
    # the mirror image of the top contact on gate B.
    rect(cell, (0.016, -0.0115, 0.037, 0.0045), LIG)
    rect(cell, (0.018, -0.0125, 0.036, 0.0055), V0)
    rect(cell, (0.018, -0.017, 0.036, 0.011), M1)
    rect(cell, (0.018, -0.012, 0.036, 0.006), V1)
    rect(cell, (0.016, -0.012, 0.072, 0.006), M2)
    rect(cell, (0.045, -0.012, 0.063, 0.006), V2)

    # Keep the top WLA landing away from the east/west placement seam.
    rect(cell, (0.071, 0.2655, 0.092, 0.2815), LIG)
    rect(cell, (0.072, 0.2645, 0.090, 0.2825), V0)
    rect(cell, (0.072, 0.2585, 0.090, 0.2865), M1)
    rect(cell, (0.072, 0.2635, 0.090, 0.2815), V1)
    rect(cell, (0.040, 0.2635, 0.092, 0.2815), M2)
    rect(cell, (0.045, 0.2635, 0.063, 0.2815), V2)
    # Both wordline ports are full-height trunks.
    rect(cell, WLA_M3, M3)


def add_storage_straps(cell: gdstk.Cell) -> None:
    """Strap the added access drains to the published core's Q and QB."""
    for cy in (-0.0675, 0.052, 0.2285, 0.3375):
        rect(cell, (0.045, cy - 0.009, 0.063, cy + 0.009), V0)
    # Lower M1 bypass
    cell.add(
        gdstk.Polygon(
            (
                (0.045, -0.0815), (0.063, -0.0815),
                (0.063, -0.0715), (0.072, -0.0715),
                (0.072, 0.056), (0.063, 0.056),
                (0.063, 0.066), (0.045, 0.066),
                (0.045, 0.038), (0.054, 0.038),
                (0.054, -0.0535), (0.045, -0.0535),
            ),
            layer=M1,
            datatype=0,
        )
    )
    # Upper M1 bypass
    cell.add(
        gdstk.Polygon(
            (
                (0.045, 0.2155), (0.063, 0.2155),
                (0.063, 0.2435), (0.054, 0.2435),
                (0.054, 0.3235), (0.063, 0.3235),
                (0.063, 0.3515), (0.045, 0.3515),
                (0.045, 0.3415), (0.036, 0.3415),
                (0.036, 0.2255), (0.045, 0.2255),
            ),
            layer=M1,
            datatype=0,
        )
    )


def add_wlb_routes(cell: gdstk.Cell, variant: bool = False) -> None:
    """Add the two WLB gate contacts and their common M5 route."""
    rect(cell, (0.017, -0.140, 0.054, -0.124), LIG)
    rect(cell, (0.054, 0.388, 0.086, 0.404), LIG)
    lower, upper = WLB_LANDINGS[variant]
    add_gate_to_m5(
        cell,
        gate_via_x=0.0395,
        bridge_x=0.018,
        y=-0.132,
        landing_y=lower,
    )
    add_gate_to_m5(
        cell,
        gate_via_x=0.0685,
        bridge_x=0.018,
        y=0.396,
        landing_y=upper,
    )
    rect(cell, WLB_M5, M5)


def add_storage_straps_and_wla(cell: gdstk.Cell) -> None:
    add_wla_routes(cell)
    add_storage_straps(cell)


def add_port_b(cell: gdstk.Cell, variant: bool = False, terminal: bool = False) -> None:
    """Add the two access devices and their independent B-side routing."""
    # Two-fin access devices.
    rect(cell, (-0.008, -0.0945, 0.062, -0.0405), ACTIVE)
    rect(cell, (0.046, 0.3105, 0.116, 0.3645), ACTIVE)
    rect(cell, (-0.027, -0.108, 0.135, -0.027), NSELECT)
    rect(cell, (-0.027, 0.297, 0.135, 0.378), NSELECT)

    # Separate WLB's gate segments from WLA and the cross-coupled core.
    rect(cell, (0.000, -0.0355, 0.054, -0.0185), GCUT)
    rect(cell, (0.054, 0.2885, 0.108, 0.3055), GCUT)

    # Storage and outside source/drain terminals plus diffusion/contact marker shapes.
    rect(cell, (0.042, -0.0945, 0.066, -0.0405), LISD)
    rect(cell, (0.042, 0.3105, 0.066, 0.3645), LISD)
    rect(cell, (-0.012, -0.0885, 0.012, -0.0405), LISD)
    rect(cell, (0.096, 0.3105, 0.120, 0.3585), LISD)
    for box in (
        (0.042, -0.0945, 0.066, -0.0405),
        (-0.012, -0.0885, 0.012, -0.0405),
        (0.042, 0.3105, 0.066, 0.3645),
        (0.096, 0.3105, 0.120, 0.3585),
    ):
        rect(cell, box, SDT)

    # M4 bitlines stay on the 48 nm track grid.
    bx = 0.090 if terminal else (-0.018 if variant else 0.018)
    rect(cell, (-0.009, -0.0885, 0.009, -0.0705), V0)
    rect(cell, (-0.009, -0.0935, 0.009, -0.043), M1)
    rect(cell, (-0.009, -0.066, 0.009, -0.048), V1)
    rect(cell, (min(0, bx) - 0.014, -0.066,
                max(0, bx) + 0.014, -0.048), M2)
    rect(cell, (bx - 0.009, -0.066, bx + 0.009, -0.048), V2)
    rect(cell, (bx - 0.009, -0.077, bx + 0.009, -0.019), M3)
    rect(cell, (bx - 0.009, -0.048, bx + 0.009, -0.024), V3)
    rect(cell, (-0.038, -0.048, 0.135, -0.024), M4)
    add_via_stack_to_m4(cell, 0.108, 0.348)
    rect(cell, (-0.027, 0.336, 0.135, 0.360), M4)

    # WLB gate stacks occupy the neighboring M4 tracks.
    add_wlb_routes(cell, variant)

    cell.add(gdstk.Label("BLBN", (-0.018, -0.036), layer=M4,
                           texttype=PIN_TEXTTYPE))
    cell.add(gdstk.Label("BLB", (0.130, 0.348), layer=M4,
                           texttype=PIN_TEXTTYPE))
    cell.add(gdstk.Label("WLB", (0.060, -0.150), layer=M5,
                           texttype=PIN_TEXTTYPE))
    # The added NMOS bodies use the same p-substrate connection as the core.
    cell.add(gdstk.Label("vss!", (0.048, -0.0675), layer=3,
                           texttype=PIN_TEXTTYPE))
    cell.add(gdstk.Label("vss!", (0.048, 0.3375), layer=3,
                           texttype=PIN_TEXTTYPE))


def build_cell(source_gds: Path) -> gdstk.Library:
    source_lib = gdstk.read_gds(str(source_gds))
    source = next((c for c in source_lib.cells if c.name == SOURCE_CELL), None)
    if source is None:
        raise RuntimeError(f"{SOURCE_CELL!r} not found in {source_gds}")

    lib = gdstk.Library("openfinram_asap7_8t", unit=source_lib.unit,
                        precision=source_lib.precision)
    # The cell, and variant B for the mirrored slots of a column (WLB_LANDINGS).
    for name, variant, terminal in ((CELL_NAME, False, False),
                                    (VARIANT_B_NAME, True, False),
                                    (VARIANT_B_END_NAME, True, True)):
        cell = lib.new_cell(name)
        clone_base(source, cell)
        clip_gcut_to_cell(cell)
        add_storage_straps_and_wla(cell)
        add_port_b(cell, variant, terminal)
        snap_sdt_heights(cell)

        for layer in sorted(MARKER_LAYERS):
            rect(cell, MARKER, layer)

        # Maintain exact 27 nm fin pitch throughout the expanded SRAM marker.
        existing_centers = {round((bbox(p)[1] + bbox(p)[3]) / 2, 6)
                            for p in cell.polygons if p.layer == FIN and p.datatype == 0}
        for index in range(-6, 17):
            center = round(index * 0.027, 6)
            if center not in existing_centers:
                rect(cell, (-0.027, center - 0.0035, 0.135, center + 0.0035), FIN)

    return lib


def add_process_frame(source: gdstk.Cell, target: gdstk.Cell) -> None:
    """Copy device-free process continuity used by all three cap cells."""
    frame_layers = {
        WELL,
        FIN,
        GATE,
        GCUT,
        NSELECT,
        PSELECT,
        SRAMDRC,
        BOUNDARY,
        SRAMVT,
    }
    clone_polygons(source, target, lambda poly: poly.layer in frame_layers)


def add_full_rails(source: gdstk.Cell, target: gdstk.Cell) -> None:
    clone_polygons(
        source,
        target,
        lambda poly: is_full_m2_rail(poly) or is_bitline_m4_rail(poly),
    )


def add_ground_rails(source: gdstk.Cell, target: gdstk.Cell) -> None:
    ground_y = {
        (0.027, 0.045),
        (0.225, 0.243),
    }
    clone_polygons(
        source,
        target,
        lambda poly: is_full_m2_rail(poly)
        and (bbox(poly)[1], bbox(poly)[3]) in ground_y,
    )


def build_edge_library(
    bitcell: gdstk.Cell, unit: float = 1e-6, precision: float = 2.5e-10,
    row_counts: tuple[int, ...] = (64,),
) -> gdstk.Library:
    """Build edge masters and dummy arrays at the bitcell's placement pitch."""
    if not row_counts or any(type(count) is not int or count < 1 for count in row_counts):
        raise ValueError("edge row counts must be positive integers")
    lib = gdstk.Library(
        "openfinram_asap7_8t_edges", unit=unit, precision=precision
    )

    # Forced-state dummy matching SpiceTemplates::get_dummy_cell_8t():
    # BLA/BLB storage side is VSS; BLAN/BLBN storage side is VDD; both
    # wordlines are held inactive at VSS.
    dummy = lib.new_cell("dummy_cell_8t")
    clone_polygons(bitcell, dummy, lambda _poly: True)
    copy_labels(
        bitcell,
        dummy,
        {label.text for label in bitcell.labels},
        {"WLA": "vss!", "WLB": "vss!"},
    )
    dummy.add(
        gdstk.Label("vdd!", (0.051, 0.052), layer=LISD,
                     texttype=PIN_TEXTTYPE),
        gdstk.Label("vss!", (0.045, 0.2295), layer=LISD,
                     texttype=PIN_TEXTTYPE),
    )

    # Column cap: horizontal bitline and supply continuity, but no devices or
    # wordline contacts.  This mirrors OpenRAM's electrically empty cap_col.
    col_cap = lib.new_cell("sram_cell_8t_col_cap")
    add_process_frame(bitcell, col_cap)
    add_full_rails(bitcell, col_cap)
    copy_labels(
        bitcell,
        col_cap,
        {"BLA", "BLAN", "BLB", "BLBN", "vdd!", "vss!"},
    )

    # Row cap: vertical wordline and ground continuity.  Gates are retained as
    # mask/load continuity, but ACTIVE is deliberately absent, so this cell is
    # electrically empty just like OpenRAM's cap_row.
    row_cap = lib.new_cell("sram_cell_8t_row_cap")
    row_cap_b = gdstk.Cell("sram_cell_8t_row_cap_b")
    for cap, variant in ((row_cap, False), (row_cap_b, True)):
        add_process_frame(bitcell, cap)
        add_ground_rails(bitcell, cap)
        add_wla_routes(cap)
        add_wlb_routes(cap, variant)
        cap.add(
            gdstk.Label("WLA", (0.054, -0.028), layer=M3,
                         texttype=PIN_TEXTTYPE),
            gdstk.Label("WLB", (0.060, -0.150), layer=M5,
                         texttype=PIN_TEXTTYPE),
        )
        copy_labels(bitcell, cap, {"vss!"})

    # Corner cap carries both sets of terminating routes, all grounded, but no
    # ACTIVE. Bake each orientation into a separate master: placement must
    # select the matching process-band orientation, not mirror a reference.
    corner = lib.new_cell("sram_cell_8t_corner")
    corner_b = gdstk.Cell("sram_cell_8t_corner_b")
    for cap, variant in ((corner, False), (corner_b, True)):
        add_process_frame(bitcell, cap)
        add_full_rails(bitcell, cap)
        add_wla_routes(cap)
        add_wlb_routes(cap, variant)
        copy_labels(
            bitcell,
            cap,
            {"BLA", "BLAN", "BLB", "BLBN", "vdd!", "vss!"},
            {"BLA": "vss!", "BLAN": "vss!", "BLB": "vss!", "BLBN": "vss!"},
        )
        cap.add(
            gdstk.Label("vss!", (0.054, -0.028), layer=M3,
                         texttype=PIN_TEXTTYPE),
            gdstk.Label("vss!", (0.060, -0.150), layer=M5,
                         texttype=PIN_TEXTTYPE),
        )
    for mx, my in ORIENTATIONS:
        if mx or my:
            lib.add(oriented_cell(corner_b if mx else corner, oriented_name(corner.name, mx, my), mx, my))
        lib.add(oriented_cell(row_cap_b if mx else row_cap, oriented_name("dummy_vertical_8t", mx, my), mx, my))
        lib.add(oriented_cell(col_cap, topbot_name(mx, my), mx, my))

    # Like FILLER_BLANK_6t122, the blank is a half-width FIN/poly tile with
    # no well, select, ACTIVE, or conductors. Use the extended 8T grids.
    x0, y0, x1, y1 = edge_boundary(bitcell)
    half = (x1 - x0) / 2
    blank = lib.new_cell("FILLER_BLANK_8t")
    for poly in bitcell.polygons:
        if poly.layer == FIN:
            _, fy0, _, fy1 = bbox(poly)
            rect(blank, (0, fy0, half, fy1), FIN)
        elif poly.layer == GATE and bbox(poly)[2] <= x0 + half:
            blank.add(poly.copy().translate(-x0, 0))
    rect(blank, (0, y0, half, y1), BOUNDARY)
    filler = lib.new_cell("FILLER_cgedge_8t")
    for row in range(4):
        for col in range(2):
            filler.add(gdstk.Reference(
                blank, origin=(col * half, (row + 1) * (y1 - y0) + y0
                                if row % 2 else row * (y1 - y0) - y0),
                x_reflection=bool(row % 2),
            ))
    rect(filler, (0, 0, 2 * half, 4 * (y1 - y0)), BOUNDARY)
    cells = {cell.name: cell for cell in lib.cells}
    for count in sorted(set(row_counts)):
        for mx, my in ORIENTATIONS:
            build_dummy_vertical_array(lib, cells, count, mirror_x=mx, mirror_y=my)
    return lib


def build_dummy_vertical_array(
    library: gdstk.Library, cells: dict[str, gdstk.Cell], rows: int,
    tap_pitch: int = 0, *, mirror_x: bool = False, mirror_y: bool = False,
) -> gdstk.Cell:
    """Tile one end row, like dummy_vertical_array_X64, for any address count.

    Addresses run along X in the academic hierarchy. Taps occupy extra slots
    and take grounded corners; they do not advance the bitcell mirror parity.
    All references translate explicit masters, including the opposite ends.
    """
    if type(rows) is not int or rows < 1:
        raise ValueError("edge row count must be a positive integer")
    if type(tap_pitch) is not int or tap_pitch < 0 or (tap_pitch and rows % tap_pitch):
        raise ValueError("edge tap pitch must be zero or a positive divisor of rows")
    x0, y0, x1, y1 = edge_boundary(cells["dummy_vertical_8t"])
    width, height = x1 - x0, y1 - y0
    slots = rows + (TAP_SLOTS * (rows // tap_pitch) if tap_pitch else 0)
    cell = library.new_cell(dummy_array_name(rows, tap_pitch, mirror_x, mirror_y))
    slot = 0

    def place(base: str, flipped: bool) -> None:
        name = oriented_name(base, flipped != mirror_x, mirror_y)
        physical_slot = slots - 1 - slot if mirror_x else slot
        cell.add(gdstk.Reference(cells[name], origin=(physical_slot * width - x0, -y0)))

    for index in range(rows):
        place("dummy_vertical_8t", bool(index % 2))
        slot += 1
        if tap_pitch and (index + 1) % tap_pitch == 0:
            for k in range(TAP_SLOTS):
                place("sram_cell_8t_corner", bool(k % 2))
                slot += 1
    rect(cell, (0, 0, slots * width, height), BOUNDARY)
    return cell


def add_supply_climb(
    cell: gdstk.Cell, rail: tuple[float, float], spine_x: float,
    y_direction: int, step_y: float = STRAP_STEP_Y,
) -> None:
    """Stagger an M2->M3->M4->M5 climb, as the IO column's staircase does."""
    x2 = STRAP_CLIMB_X
    y2 = (rail[0] + rail[1]) / 2
    y3 = y2 + y_direction * step_y

    rect(cell, (x2 - 0.009, y2 - 0.009, x2 + 0.009, y2 + 0.009), V2)
    rect(cell, (x2 - 0.009, min(y2, y3) - 0.014,
                x2 + 0.009, max(y2, y3) + 0.014), M3)

    rect(cell, (x2 - 0.014, y3 - 0.012, x2 + 0.014, y3 + 0.012), M3)
    rect(cell, (x2 - 0.009, y3 - 0.012, x2 + 0.009, y3 + 0.012), V3)
    rect(cell, (min(x2 - 0.020, spine_x - 0.023), y3 - 0.012,
                max(x2 + 0.020, spine_x + 0.023), y3 + 0.012), M4)

    rect(cell, (spine_x - 0.012, y3 - 0.012,
                spine_x + 0.012, y3 + 0.012), V4)


def build_tap_library(
    bitcell: gdstk.Cell, unit: float = 1e-6, precision: float = 2.5e-10
) -> gdstk.Library:
    """Build the array well/substrate tap, following tapcell_sram_6t122."""
    lib = gdstk.Library(
        "openfinram_asap7_8t_tap", unit=unit, precision=precision
    )
    for name in (TAP_CELL_NAME, STRAP_CELL_NAME):
        build_tap_cell(lib, name, bitcell, strap=name == STRAP_CELL_NAME)
    return lib


def build_tap_cell(
    lib: gdstk.Library, name: str, bitcell: gdstk.Cell, strap: bool
) -> gdstk.Cell:
    """Build the array tap; `strap` adds the M2-to-M5 supply climb."""
    cell = lib.new_cell(name)
    x0, y0, bx1, y1 = bbox(
        next(p for p in bitcell.polygons if p.layer == BOUNDARY)
    )
    slot = bx1 - x0
    x1 = x0 + TAP_SLOTS * slot

    # Fin and poly grids run through the tap column unchanged, one bitcell
    # frame per slot; the dummy gates are cut at the row-abutment line
    # exactly as tapcell_sram_6t122 does.
    for k in range(TAP_SLOTS):
        clone_polygons(bitcell, cell, lambda poly: poly.layer == GATE, dx=k * slot)
    for poly in bitcell.polygons:  # fins and rails run the whole width, overhang and all
        if poly.layer == FIN or is_full_m2_rail(poly) or is_bitline_m4_rail(poly):
            px0, py0, px1, py1 = bbox(poly)
            rect(cell, (px0, py0, px1 + (TAP_SLOTS - 1) * slot, py1), poly.layer)
    rect(cell, (x0, y0 - 0.0085, x1, y0 + 0.0085), GCUT)
    for layer in (SRAMDRC, BOUNDARY):
        rect(cell, (x0, y0, x1, y1), layer)

    # The well runs through; the tie implants stop short of both seams so
    # they meet the neighbours' overhanging selects edge to edge.
    bands = tie_bands(bitcell)
    rails = supply_rails(bitcell)
    well_y0, well_y1, _ = next(b for b in bands if b[2] == "vdd!")
    rect(cell, (x0, well_y0, x1, well_y1), WELL)
    ix0, ix1 = x0 + TAP_IMPLANT_INSET, x1 - TAP_IMPLANT_INSET
    rect(cell, (ix0, well_y0, ix1, well_y1), NSELECT)
    for band_y0, band_y1, net in bands:
        if net == "vss!":
            rect(cell, (ix0, band_y0, ix1, band_y1), PSELECT)

    ax0, ax1 = TAP_ACTIVE_X
    cx0, cx1 = TAP_CONTACT_X
    vx0, vx1 = TAP_VIA_X
    for band_y0, band_y1, _net in bands:
        ty0, ty1 = band_y0 + TAP_BAND_INSET, band_y1 - TAP_BAND_INSET
        rect(cell, (ax0, ty0, ax1, ty1), ACTIVE)
        for layer in (LISD, SDT):
            rect(cell, (cx0, ty0, cx1, ty1), layer)
        centre = (ty0 + ty1) / 2
        rect(cell, (vx0, centre - TAP_VIA_HEIGHT / 2,
                    vx1, centre + TAP_VIA_HEIGHT / 2), V0)

    # One M1 strap per run of same-net bands, each landing on a supply rail of
    # its own net.
    for net, group in group_tie_bands(bands):
        bottom = group[0][0] + TAP_BAND_INSET
        top = group[-1][1] - TAP_BAND_INSET
        rect(cell, (vx0, bottom, vx1, top), M1)
        landed = [r for r in rails[net] if bottom <= r[0] and r[1] <= top]
        _assert(bool(landed), f"tap {net} strap reaches no {net} rail")
        for rail_y0, rail_y1 in landed:
            rect(cell, (vx0, rail_y0, vx1, rail_y1), V1)

    copy_labels(bitcell, cell, {"vdd!", "vss!"})

    if strap:
        # One full-height M5 spine per supply.
        for net, spine_x in STRAP_SPINE_X.items():
            rail = min(rails[net], key=lambda r: r[0])
            add_supply_climb(cell, rail, spine_x,
                             1, step_y=0.040 if net == "vss!" else STRAP_STEP_Y)
            rect(cell, (spine_x - STRAP_M5_HALF, y0,
                        spine_x + STRAP_M5_HALF, y1), M5)
            cell.add(gdstk.Label(net, (spine_x, (rail[0] + rail[1]) / 2),
                                 layer=M5, texttype=PIN_TEXTTYPE))
    return cell


def parse_row_counts(value: str) -> tuple[int, ...]:
    try:
        counts = tuple(sorted({int(item) for item in value.split(",")}))
    except ValueError as error:
        raise argparse.ArgumentTypeError("edge row counts must be integers") from error
    if not counts or any(count < 1 for count in counts):
        raise argparse.ArgumentTypeError("edge row counts must be positive")
    return counts


def parse_args(argv: list[str]) -> argparse.Namespace:
    repo = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-gds",
        type=Path,
        default=repo / "tech/gds/srambank_32b_boundary_2.gds",
        help="tracked ASAP7 GDS containing sram_cell_6t_122",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=repo / "tech/gds/sram_cell_8t.gds",
        help="generated bitcell-only GDS",
    )
    parser.add_argument(
        "--edge-output",
        type=Path,
        help="generated dummy/row-cap/column-cap/corner GDS; defaults beside --output",
    )
    parser.add_argument(
        "--edge-rows", type=parse_row_counts, default=(64,),
        help="comma-separated dummy-array address row counts (default: 64)",
    )
    parser.add_argument(
        "--tap-output",
        type=Path,
        help="generated array well/substrate tap GDS; defaults beside --output",
    )
    verify_group = parser.add_mutually_exclusive_group()
    verify_group.add_argument(
        "--verify",
        type=Path,
        help="verify an existing GDS instead of generating one",
    )
    verify_group.add_argument(
        "--verify-edges",
        type=Path,
        help="verify an existing 8T edge-cell GDS instead of generating",
    )
    verify_group.add_argument(
        "--verify-tap",
        type=Path,
        help="verify an existing 8T tap GDS instead of generating",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        if args.verify:
            digest = verify_gds(args.verify)
            print(f"PASS {args.verify}: {CELL_NAME}, topology=8T, sha256={digest}")
            for line in sizing_report():
                print(f"  {line}")
            return 0

        if args.verify_edges:
            digests = verify_edge_gds(args.verify_edges, args.edge_rows)
            print(f"PASS {args.verify_edges}: {len(digests)} edge cells")
            for name, digest in digests.items():
                print(f"  {name}: sha256={digest}")
            return 0

        if args.verify_tap:
            bitcell_path = args.output
            digests = verify_tap_gds(args.verify_tap, bitcell_path)
            print(f"PASS {args.verify_tap}: {len(digests)} tap cells")
            for name, digest in digests.items():
                print(f"  {name}: sha256={digest}")
            return 0

        lib = build_cell(args.source_gds)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        lib.write_gds(str(args.output), timestamp=FIXED_GDS_TIMESTAMP)
        digest = verify_gds(args.output)
        print(f"wrote {args.output}: {CELL_NAME}, topology=8T, sha256={digest}")

        edge_output = args.edge_output or args.output.with_name("sram_cell_8t_edges.gds")
        edge_output.parent.mkdir(parents=True, exist_ok=True)
        edge_lib = build_edge_library(
            lib.cells[0], unit=lib.unit, precision=lib.precision, row_counts=args.edge_rows
        )
        edge_lib.write_gds(str(edge_output), timestamp=FIXED_GDS_TIMESTAMP)
        edge_digests = verify_edge_gds(edge_output, args.edge_rows)
        print(f"wrote {edge_output}: {len(edge_digests)} edge cells")
        for name, edge_digest in edge_digests.items():
            print(f"  {name}: sha256={edge_digest}")

        tap_output = args.tap_output or args.output.with_name(
            "sram_cell_8t_tap.gds"
        )
        tap_output.parent.mkdir(parents=True, exist_ok=True)
        tap_lib = build_tap_library(
            lib.cells[0], unit=lib.unit, precision=lib.precision
        )
        tap_lib.write_gds(str(tap_output), timestamp=FIXED_GDS_TIMESTAMP)
        tap_digests = verify_tap_gds(tap_output, args.output)
        print(f"wrote {tap_output}: {len(tap_digests)} tap cells")
        for name, digest in tap_digests.items():
            print(f"  {name}: sha256={digest}")
        return 0
    except (OSError, RuntimeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
