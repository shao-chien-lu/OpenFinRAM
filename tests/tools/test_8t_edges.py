"""8T frame geometry, asymmetric mirrors, and parameterized placement checks."""

import argparse
import math
from pathlib import Path
import sys
import subprocess
import xml.etree.ElementTree as ET
import tempfile
import unittest

import gdstk

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import generate_asap7_8t_bitcell as edges  # noqa: E402
import generate_asap7_wordline_arrays as arrays  # noqa: E402
from compile_asap7_2rw import build_leaf  # noqa: E402
from drc import find_gdscheck, run_device_drc  # noqa: E402


class EdgeFrameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bitcell = gdstk.read_gds(str(REPO / "tech/gds/sram_cell_8t.gds")).cells[0]
        cls.library = edges.build_edge_library(cls.bitcell)
        cls.cells = {cell.name: cell for cell in cls.library.cells}
        # The tap library also holds strapcell_sram_8t, so select by name
        # rather than by position.
        cls.tap = {
            cell.name: cell
            for cell in gdstk.read_gds(
                str(REPO / "tech/gds/sram_cell_8t_tap.gds")
            ).cells
        }["tapcell_sram_8t"]

    def assert_same_geometry(self, actual, expected):
        specs = {
            (p.layer, p.datatype)
            for p in actual.get_polygons() + expected.get_polygons()
        }
        for layer, datatype in specs:
            self.assertFalse(
                gdstk.boolean(
                    actual.get_polygons(layer=layer, datatype=datatype),
                    expected.get_polygons(layer=layer, datatype=datatype),
                    "xor",
                    precision=1e-7,
                ),
                f"geometry differs on {layer}/{datatype}",
            )

    def test_asymmetric_bands_and_pins_mirror_about_boundary(self):
        canonical = gdstk.Cell("asymmetric")
        edges.rect(canonical, (2, -3, 4, 7), edges.BOUNDARY)
        edges.rect(canonical, (1.5, -2, 3.25, 0), edges.WELL)
        edges.rect(canonical, (2.25, 1, 4.75, 5), edges.NSELECT)
        canonical.add(gdstk.Label("VDD", (2.5, -1), layer=20, texttype=251))
        for mx, my in edges.ORIENTATIONS:
            with self.subTest(mx=mx, my=my):
                cell = edges.oriented_cell(canonical, "oriented", mx, my)
                self.assertFalse(cell.references)
                self.assertEqual(edges.edge_boundary(cell), (2, -3, 4, 7))
                self.assertEqual(
                    edges.bbox(next(p for p in cell.polygons if p.layer == edges.WELL)),
                    (
                        2.75 if mx else 1.5,
                        4 if my else -2,
                        4.5 if mx else 3.25,
                        6 if my else 0,
                    ),
                )
                self.assertEqual(
                    edges.bbox(
                        next(p for p in cell.polygons if p.layer == edges.NSELECT)
                    ),
                    (
                        1.25 if mx else 2.25,
                        -1 if my else 1,
                        3.75 if mx else 4.75,
                        3 if my else 5,
                    ),
                )
                self.assertEqual(
                    tuple(cell.labels[0].origin), (3.5 if mx else 2.5, 5 if my else -1)
                )

    def test_dummy_rows_follow_real_array_slots_and_tap_parity(self):
        for count, tap_pitch in (
            (1, 0),
            (3, 0),
            (18, 0),
            (64, 0),
            (129, 0),
            (1, 1),
            (3, 1),
            (18, 6),
            (64, 16),
            (129, 3),
        ):
            lib = gdstk.Library()
            row = arrays.build_row(
                lib, self.bitcell, arrays.CONTRACTS[0], count, self.tap, tap_pitch
            )
            width = arrays.boundary_box(self.bitcell)[2] - arrays.boundary_box(self.bitcell)[0]
            # Where a slot ends up mirrored in x the end row takes variant B's
            # landings: its base is the x-mirrored master mirrored back.
            variant = {
                base: edges.oriented_cell(self.cells[edges.oriented_name(name, True, False)], base + "_b", True, False)
                for base, name in (("sram_cell_8t_row_cap", "dummy_vertical_8t"),
                                   ("sram_cell_8t_corner", "sram_cell_8t_corner"))
            }

            def expected_end(mx):
                end = gdstk.Cell("expected_end")

                def cap(base, mirrored):
                    return variant[base] if mirrored != mx else self.cells[base]

                for ref in row.references:
                    if ref.cell_name == edges.CELL_NAME:
                        end.add(gdstk.Reference(cap("sram_cell_8t_row_cap", bool(ref.x_reflection)),
                                                origin=ref.origin, rotation=ref.rotation,
                                                x_reflection=ref.x_reflection))  # fmt: skip
                        continue
                    # A tap is two slots: a corner over each, the second mirrored
                    # as the bitcell after it would be.
                    x, y = ref.origin
                    end.add(gdstk.Reference(cap("sram_cell_8t_corner", False), origin=(x, y)))
                    end.add(gdstk.Reference(cap("sram_cell_8t_corner", True),
                                            origin=(x + edges.TAP_SLOTS * width, y),
                                            rotation=math.pi, x_reflection=True))  # fmt: skip
                edges.rect(end, arrays.boundary_box(row), edges.BOUNDARY)
                return end

            boundary = arrays.boundary_box(row)
            for mx, my in edges.ORIENTATIONS:
                with self.subTest(count=count, tap_pitch=tap_pitch, mx=mx, my=my):
                    end = edges.build_dummy_vertical_array(
                        lib, self.cells, count, tap_pitch, mirror_x=mx, mirror_y=my
                    )
                    self.assertEqual(edges.edge_boundary(end), boundary)
                    self.assertEqual(
                        len(end.references),
                        count + (edges.TAP_SLOTS * (count // tap_pitch) if tap_pitch else 0),
                    )
                    self.assertTrue(
                        all(
                            not ref.rotation and not ref.x_reflection
                            for ref in end.references
                        )
                    )
                    expected = gdstk.Reference(
                        expected_end(mx),
                        origin=(boundary[2] if mx else 0, boundary[3] if my else 0),
                        rotation=math.pi if mx else 0,
                        x_reflection=mx != my,
                    )
                    self.assert_same_geometry(end, expected)

    def test_native_m4_rules_on_mirrored_arrays_and_capped_taps(self):
        try:
            binary = find_gdscheck()
        except FileNotFoundError as exc:
            self.skipTest(str(exc))
        with tempfile.TemporaryDirectory() as scratch:
            scratch = Path(scratch)

            OFF_GRID = {"M4.AUX.1", "M4.AUX.2"}

            def markers(cell, tag):
                library = gdstk.Library(unit=1e-6, precision=1e-10)
                library.add(cell, *cell.dependencies(True))
                gds, report = scratch / f"{tag}.gds", scratch / f"{tag}.lyrdb"
                library.write_gds(str(gds))
                # gdscheck's ASAP7 deck exempts every shape overlapping the
                # SRAMDRC marker, and the bitcell carries one, so a written
                # copy without it is what puts the cells' M4 under the rules.
                # The marker legitimately waives the M4 grid and track rules
                # (M4.AUX.1/2) inside an array, so those are not counted.
                copy = gdstk.read_gds(str(gds))
                for each in copy.cells:
                    each.filter([(edges.SRAMDRC, 0)], remove=True)
                copy.write_gds(str(gds))
                result = subprocess.run(
                    [str(binary), "run", "--input", str(gds), "--process", "asap7",
                     "--suite", "main", "--topcell", cell.name, "--threads", "1",
                     "--memory", "512M", "--report", str(report)],
                    capture_output=True, text=True, timeout=120,
                )
                self.assertIn(result.returncode, (0, 2), result.stdout + result.stderr)
                items = ET.parse(report).getroot().findall("./items/item")
                self.assertTrue(all(not i.findtext("tags", "").strip() for i in items))
                return {i.findtext("category", "").strip("'\"") for i in items} - OFF_GRID

            lib = arrays.build_library(
                REPO / "tech/gds/sram_cell_8t.gds",
                REPO / "tech/gds/srambank_32b_boundary_2.gds", [4], 4,
            )
            array = next(c for c in lib.cells if c.name == "array_x4x4_sram_8t")
            for tag, cell in (("array", array), ("taps", build_leaf(4, 2)),
                              ("straps", build_leaf(4, 2, strap_pitch=1))):
                with self.subTest(tag=tag):
                    self.assertFalse({rule for rule in markers(cell, tag) if "M4" in rule})

            # The untapped end master must keep the IO seam free of the
            # 5 nm M2 gap caused by using the interior B stack at the edge.
            seam = run_device_drc(
                REPO / "tech/gds/sram_8t_iocolumn.gds", "colgrp_x2x4_sram_8t",
                scratch / "io_seam", binary=binary,
            )
            self.assertNotIn("M2.S", seam["rules"])

            # Restore the original unstaggered 84 nm pads as a positive
            # control: the deck must find the mirrored 6 nm tip spacing.
            legacy = self.bitcell.copy("legacy_m4")
            for poly in list(legacy.polygons):
                if poly.layer == edges.M4 and not edges.is_bitline_m4_rail(poly):
                    _, y0, _, y1 = edges.bbox(poly)
                    legacy.remove(poly)
                    edges.rect(legacy, (0.003, y0, 0.087, y1), edges.M4)
            row = array.references[0].cell
            for ref in row.references:
                ref.cell = legacy
            self.assertIn("M4.S.2", markers(array, "original_landings"))

    def test_invalid_counts_and_tap_pitches_fail(self):
        for count, tap_pitch in (
            (0, 0),
            (-1, 0),
            (1.5, 0),
            (True, 0),
            (3, 2),
            (4, -1),
            (4, 1.5),
        ):
            with (
                self.subTest(count=count, tap_pitch=tap_pitch),
                self.assertRaises(ValueError),
            ):
                edges.build_dummy_vertical_array(
                    gdstk.Library(), self.cells, count, tap_pitch
                )
        for value in ("0", "-1,64", "", "3,no"):
            with (
                self.subTest(value=value),
                self.assertRaises(argparse.ArgumentTypeError),
            ):
                edges.parse_row_counts(value)

    def test_leaf_masters_have_matching_spice_interfaces(self):
        spice = (REPO / "tech/spice/sram_cell_8t_edges.sp").read_text()
        ports = {
            line.split()[1]: set(line.split()[2:])
            for line in spice.splitlines()
            if line.startswith(".SUBCKT ")
        }
        for name in edges.EDGE_CELL_NAMES:
            with self.subTest(name=name):
                cell = self.cells[name]
                pins = {label.text.upper().rstrip("!") for label in cell.get_labels()}
                self.assertIn(name, ports)
                self.assertEqual(pins, ports[name])

    def test_roundtrip_nonstandard_counts_and_reject_corrupt_mirror(self):
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "edges.gds"
            lib = edges.build_edge_library(self.bitcell, row_counts=(1, 3, 18))
            lib.write_gds(str(path))
            edges.verify_edge_gds(path, (1, 3, 18))
            corner = next(c for c in lib.cells if c.name == "sram_cell_8t_corner_v2_lr")
            next(p for p in corner.polygons if p.layer == edges.NSELECT).translate(
                0.001, 0
            )
            lib.write_gds(str(path))
            with self.assertRaisesRegex(ValueError, "canonical orientation"):
                edges.verify_edge_gds(path, (1, 3, 18))

    def test_compiler_places_corners_and_end_rows_without_parent_mirrors(self):
        for count in (2, 18):
            with self.subTest(count=count):
                leaf = build_leaf(count, math.gcd(count, 16))
                end_ref = next(
                    r for r in leaf.references if r.cell_name == "dp_array_end_rows"
                )
                self.assertFalse(end_ref.rotation or end_ref.x_reflection)
                # One unsplit array has one capped end, port A's on the left:
                # a B-track corner above and below it with the original process frame.
                corners = {
                    edges.oriented_name("sram_cell_8t_corner", False, my) + "_b"
                    for my in (False, True)
                }
                refs = [r for r in end_ref.cell.references if r.cell_name in corners]
                self.assertEqual({r.cell_name for r in refs}, corners)
                self.assertTrue(
                    all(not r.rotation and not r.x_reflection for r in refs)
                )
                rows = [
                    r
                    for r in end_ref.cell.references
                    if r.cell_name.startswith("dummy_vertical_array_")
                ]
                self.assertEqual(len(rows), 2)
                self.assertTrue(
                    all(not r.rotation and not r.x_reflection for r in rows)
                )
                colgrp = next(
                    r.cell for r in leaf.references if r.cell_name.startswith("colgrp_")
                )
                caps = [
                    r for r in colgrp.references if r.cell_name.startswith("col_cap_")
                ]
                self.assertEqual(len(caps), 1)
                self.assertTrue(
                    all(not r.rotation and not r.x_reflection for r in caps)
                )
                for ref in caps:
                    self.assertEqual(
                        ref.cell.references[0].cell_name, "FILLER_cgedge_8t"
                    )
                    self.assertTrue(
                        all(
                            not r.rotation and not r.x_reflection
                            for r in ref.cell.references
                        )
                    )


if __name__ == "__main__":
    unittest.main()
