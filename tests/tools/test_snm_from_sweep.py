#!/usr/bin/env python3
"""SNM geometry and fail-closed input tests; no simulator or packages needed."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from snm_from_sweep import _crossing_x, main, read_prn, snm


def ideal(vdd, trip):
    return [(0.0, vdd), (trip, vdd), (trip, 0.0), (vdd, 0.0)]


class GeometryTest(unittest.TestCase):
    def test_ideal_half_supply(self):
        for vdd in (0.63, 0.70, 0.77, 1.0):
            with self.subTest(vdd=vdd):
                a = ideal(vdd, vdd / 2)
                for margin in snm(a, [(y, x) for x, y in a]):
                    self.assertAlmostEqual(margin, vdd / 2, places=12)

    def test_smaller_asymmetric_lobe_wins(self):
        a = ideal(1.0, 0.3)
        b = [(y, x) for x, y in ideal(1.0, 0.6)]
        margin, large, small = snm(a, b)
        self.assertAlmostEqual(margin, 0.3)
        self.assertAlmostEqual(min(large, small), 0.3)
        self.assertAlmostEqual(max(large, small), 0.6)

    def test_curve_exchange_and_reversal(self):
        a = ideal(1.0, 0.3)
        b = [(y, x) for x, y in ideal(1.0, 0.6)]
        m, la, lb = snm(a, b)
        self.assertEqual(snm(a[::-1], b[::-1]), (m, la, lb))
        self.assertEqual(snm(b, a), (m, lb, la))

    def test_coincident_curves_have_zero_snm(self):
        a = [(0.0, 0.7), (0.7, 0.0)]
        self.assertEqual(snm(a, a), (0.0, 0.0, 0.0))

    def test_endpoint_crossing(self):
        a = [(0.0, 1.0), (1.0, 0.0)]
        self.assertEqual(_crossing_x(a, -1.0), 1.0)
        self.assertEqual(_crossing_x(a, 1.0), 0.0)
        self.assertIsNone(_crossing_x(a, 2.0))

    def test_picovolt_rail_noise_on_both_axes(self):
        a = [(0.0, 0.7), (0.35, 0.35), (0.69, 1e-9), (0.7, 1.01e-9)]
        self.assertGreaterEqual(snm(a, [(y, x) for x, y in a])[0], 0.0)

    def test_invalid_curves(self):
        for bad in ([], [(0.0, 1.0)], [(0.0, 0.0), (1.0, 1.0)],
                    [(0.0, 1.0), (1.0, float("nan"))],
                    [(0.0, 1.0), (0.0, 1.0)]):
            with self.subTest(curve=bad), self.assertRaises(ValueError):
                snm(bad, [(0.0, 1.0), (1.0, 0.0)])


class SweepTest(unittest.TestCase):
    header = "Index V(SW) V(QAOUT) V(QBOUT)\n"
    rows = "0 0 0.7 0.7\n1 0.35 0.35 0.35\n2 0.7 0 0\n"
    footer = "End of Xyce(TM) Simulation\n"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "test.prn"
        self.path.write_text(self.header + self.rows + self.footer)

    def test_named_columns(self):
        self.path.write_text("Index V(QBOUT) V(SW) V(QAOUT)\n"
                             "0 0.7 0 0.7\n1 0.4 0.35 0.3\n2 0 0.7 0\n" + self.footer)
        self.assertEqual(read_prn(self.path), ([0, 0.35, 0.7], [0.7, 0.3, 0], [0.7, 0.4, 0]))

    def test_bad_data_rejected(self):
        for content in (
            self.header + self.rows,  # interrupted simulation
            self.header + self.rows.replace("1 0.35 0.35 0.35", "1 0.35 nan 0.35") + self.footer,
            self.header + self.rows.replace("1 0.35 0.35 0.35", "1 0.35 0.35") + self.footer,
            self.header + self.rows.replace("1 0.35 0.35 0.35", "bad row") + self.footer,
            self.header + self.rows.replace("1 0.35", "1 0.0") + self.footer,
            self.header.replace("V(SW)", "V(OTHER)") + self.rows + self.footer,
            self.header + self.rows + self.header + self.rows + self.footer,
        ):
            with self.subTest(content=content), self.assertRaises(ValueError):
                self.path.write_text(content)
                read_prn(self.path)

    def test_endpoint_and_step_checks(self):
        for args in (["--vdd", "0.8"], ["--step", "0.1"]):
            with self.subTest(args=args), self.assertRaises(ValueError):
                main([str(self.path)] + args)

    def test_nominal_symmetry_failure(self):
        self.path.write_text(self.header + self.rows.replace("1 0.35 0.35 0.35", "1 0.35 0.35 0.4") + self.footer)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main([str(self.path)]), 1)

    def test_json_svg_and_measurements(self):
        summary, plot = self.path.with_suffix(".json"), self.path.with_suffix(".svg")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main([str(self.path), "--vdd", "0.7", "--step", "0.35",
                                   "--prefix", "HOLD_", "--json", str(summary),
                                   "--svg", str(plot)]), 0)
        data = json.loads(summary.read_text())
        self.assertEqual(data["snm_mv"], 0.0)
        self.assertEqual(data["samples"], 3)
        self.assertIn("HOLD_SNM_MV = 0.000", output.getvalue())
        self.assertEqual(len(ET.parse(plot).findall(".//{http://www.w3.org/2000/svg}polyline")), 2)


if __name__ == "__main__":
    unittest.main()
