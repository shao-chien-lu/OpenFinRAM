#!/usr/bin/env python3
"""Extract bitline/wordline wire parasitics from the SRAM macro GDS.

P1 of the characterization plan: replace the guessed
CBL_PER_CELL_F = 0.06 fF in characterize_read.py with geometry-derived
wire capacitance, using the per-layer RC values from the OpenROAD-flow-
scripts ASAP7 platform (vendored as tech/setRC.tcl).

Method
------
In the 6T_122 bitcell (verified against tech/gds/srambank_32b_boundary_2):

  * BL/BLB run VERTICALLY through the cell on LISD (layer 17) straps -
    total vertical LISD extent spans both rails, so one net sees half;
  * the wordlines run HORIZONTALLY as full-width M2 (layer 20) rails;

    C_wire = length_um * cap_ff_per_um(layer)

LISD has no entry in the ORFS platform RC data (std cells never route
long wires on it), so its capacitance is priced with a RANGE bracketed by
the M1 and M2 numbers and reported explicitly as a bracket, not a point.
Diffusion/junction caps are NOT extracted (no open device-level ASAP7
extractor); they stay an explicit remainder rather than a silent guess.

Usage:
    python3 scripts/extract_parasitics.py \
        --gds tech/gds/srambank_32b_boundary_2.gds \
        --output results/parasitics.json
"""

import argparse
import json
import sys
from pathlib import Path

try:
    import gdstk
except ImportError:
    print("ERROR: gdstk Python module required", file=sys.stderr)
    raise SystemExit(1)

# Per-layer capacitance [fF/um], from tech/setRC.tcl (ORFS ASAP7 platform,
# fF/um upstream values; our copy stores pF for OpenSTA and scales by 1e-3).
LAYER_CAP_FF_PER_UM = {
    "M1": 0.1000,   # setRC M1 cap = 1e-13 pF/um -> 0.1 fF/um (M1 is short;
                    # local interconnect dominates WL)
    "M2": 0.174942,
    "M3": 0.155554,
}

BITCELL = "sram_cell_6t_122"


def layer_shapes(cell, layer, datatype):
    polys = []
    for p in cell.polygons:
        if p.layer == layer and p.datatype == datatype:
            polys.append(p)
    return polys


def poly_bbox_len(poly, axis):
    pts = poly.points
    xs = [pt[0] for pt in pts]
    ys = [pt[1] for pt in pts]
    if axis == 0:
        return max(xs) - min(xs), min(xs), min(ys)
    return max(ys) - min(ys), min(xs), min(ys)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gds", type=Path,
                    default=Path("tech/gds/srambank_32b_boundary_2.gds"))
    ap.add_argument("--bitcell-name", default=BITCELL)
    ap.add_argument("--m2-layer", type=int, default=20)
    ap.add_argument("--lisd-layer", type=int, default=17)
    ap.add_argument("--datatype", type=int, default=0)
    ap.add_argument("--output", type=Path, default=None)
    args = ap.parse_args(argv)

    lib = gdstk.read_gds(str(args.gds))
    cells = {c.name: c for c in lib.cells}
    if args.bitcell_name not in cells:
        print(f"ERROR: cell {args.bitcell_name} not in {args.gds}; "
              f"have {[c.name for c in lib.cells][:10]}...", file=sys.stderr)
        return 1
    cell = cells[args.bitcell_name]

    m2 = layer_shapes(cell, args.m2_layer, args.datatype)
    lisd = layer_shapes(cell, args.lisd_layer, args.datatype)
    if not m2 or not lisd:
        print("ERROR: no M2/LISD shapes found in bitcell", file=sys.stderr)
        return 1

    # Bitlines: VERTICAL LISD straps; both rails' extents are summed, one net
    # sees half. Wordlines: HORIZONTAL full-width M2 rails.
    bl_both_rails_len = sum(poly_bbox_len(p, 1)[0] for p in lisd)
    bl_one_net_len = 0.5 * bl_both_rails_len
    wl_rail_len = sum(poly_bbox_len(p, 0)[0] for p in m2)

    c_m1, c_m2 = LAYER_CAP_FF_PER_UM["M1"], LAYER_CAP_FF_PER_UM["M2"]
    bl_lo = bl_one_net_len * min(c_m1, c_m2)
    bl_hi = bl_one_net_len * max(c_m1, c_m2)
    wl_per_cell_ff = wl_rail_len * c_m2

    result = {
        "source_gds": str(args.gds),
        "bitcell": args.bitcell_name,
        "rc_source": "tech/setRC.tcl (ORFS ASAP7 platform)",
        "bl_wire_ff_per_cell_low": round(bl_lo, 5),
        "bl_wire_ff_per_cell_high": round(bl_hi, 5),
        "wl_m2_wire_ff_per_cell": round(wl_per_cell_ff, 5),
        "geometry": {
            "lisd_vertical_extent_both_bl_rails_um": round(bl_both_rails_len, 5),
            "m2_total_length_um": round(wl_rail_len, 5),
            "m2_shapes_in_cell": len(m2),
        },
        "assumptions": [
            "BL priced on LISD with cap bracketed by M1..M2 per-um values "
            "(no platform RC exists for local interconnect)",
            f"wordline priced on M2 rails ({c_m2} fF/um)",
        ],
        "remainder_not_extracted": [
            "diffusion/junction caps (no open ASAP7 device extractor)",
            "gate/LIG device-level wordline load",
            "via and inter-layer fringe terms",
        ],
    }

    print(f"bitcell {args.bitcell_name}: "
          f"{len(lisd)} LISD shapes (BL/BLB), {len(m2)} M2 shapes")
    print(f"BL wire cap per cell : {bl_lo:.5f} .. {bl_hi:.5f} fF "
          f"(guess was 0.06 fF -> ratio {bl_lo / 0.06:.2f}x..{bl_hi / 0.06:.2f}x)")
    print(f"WL M2 wire per cell  : {wl_per_cell_ff:.5f} fF")
    if args.output:
        args.output.write_text(json.dumps(result, indent=2) + "\n")
        print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
