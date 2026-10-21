#!/usr/bin/env python3
"""Check generated GDS/LEF pins: python3 tests/check_macro_pins.py macro.gds macro.lef."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from def_to_gds import _load_klayout_db

kdb = _load_klayout_db()

gds, lef = map(Path, sys.argv[1:])
layout = kdb.Layout()
layout.read(str(gds))
top = layout.cell(gds.stem)
assert top is not None, f"missing top cell {gds.stem}"
lef_text = lef.read_text()
assert re.search(rf'^MACRO\s+{re.escape(top.name)}\s*$', lef_text, re.M)
lef_pins = set(re.findall(r'^\s*PIN\s+(\S+)', lef_text, re.M))
gds_pins = set()
missing_geometry = []
for index in layout.layer_indexes():
    info = layout.get_info(index)
    if info.datatype != 251 or info.layer not in (19, 20, 30, 40, 50, 60, 70, 80, 90):
        continue
    metal = kdb.Region(top.begin_shapes_rec(index))
    drawing = layout.find_layer(info.layer, 0)
    if drawing is not None:
        metal += kdb.Region(top.begin_shapes_rec(drawing))
    for shape in top.shapes(index).each():
        if not shape.is_text():
            continue
        text = shape.text
        gds_pins.add(text.string)
        point = text.trans.disp
        # Include one DBU around labels on polygon edges.
        probe = kdb.Box(point.x-1, point.y-1, point.x+1, point.y+1)
        if metal.interacting(kdb.Region(probe)).is_empty():
            missing_geometry.append((text.string, info.layer))
assert gds_pins == lef_pins, ("GDS/LEF pin mismatch", gds_pins ^ lef_pins)
assert gds_pins, "no macro pins"
assert not missing_geometry, ("pins without matching metal", missing_geometry)
print(f"PASS: {top.name} {len(gds_pins)} GDS/LEF pins on matching metal layers")
