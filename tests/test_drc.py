"""DRC process failures, baseline isolation, and real geometric coverage."""

from __future__ import annotations

import math
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import drc
import check_device_drc
import verify_macro


def report(path, *, rule="M1.S", message="M1: space 0.0080 µm &lt; 0.02 µm", tags=""):
    path.write_text(f'''<report-database><items><item><category>{rule}</category>
        <cell>TOP</cell><tags>{tags}</tags><multiplicity>2</multiplicity>
        <values><value>text: '{message}'</value></values>
        </item></items></report-database>''')


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name)

    def test_report_uses_deck_limit_and_converts_um_to_nm(self):
        path = self.out / "drc.lyrdb"
        report(path)
        self.assertEqual(drc.parse_report(path, cell="TOP"), [
            dict(cell="TOP", rule="M1.S", count=2, worst_nm=8.0, limit_nm=18.0)])

    def test_port_preserves_every_reference_layer_threshold_and_notch_check(self):
        import json

        ruby = (drc.REPO / "tech/drc/asap7_device.drc").read_text()
        layer_map = {name: int(number) for name, number in
                     re.findall(r"name: (\w+), gds_layer: (\d+)", drc.PDK.read_text())}
        expected = set()
        for number, name, width, space in re.findall(
                r'(\d+)\s*=>\s*\["(\w+)",\s*([.\d]+)(?:,\s*([.\d]+))?\]', ruby):
            self.assertEqual(layer_map[name], int(number))
            expected.add((name + ".W", "min_width", name, float(width)))
            if space:
                expected.add((name + ".S", "min_space", name, float(space)))
                expected.add((name + ".S", "min_notch", name, float(space)))
        self.assertEqual(len(expected), 39)
        actual = {(r["id"], r["check"], r["layers"][0], r["value"])
                  for r in json.loads(drc.DECK.read_text())["rules"]}
        self.assertEqual(actual, expected)
        for rule in json.loads(drc.DECK.read_text())["rules"]:
            self.assertEqual(rule.get("params", {}),
                             {"facing": "y"} if rule["id"].startswith("GCUT.") else {})

    def test_unreadable_unknown_and_skipped_markers_fail_closed(self):
        path = self.out / "drc.lyrdb"
        for kwargs in ({"rule": "unknown"}, {"message": "unrecognized format"},
                       {"tags": "skipped"}, {"tags": "waived"}):
            with self.subTest(kwargs=kwargs):
                report(path, **kwargs)
                with self.assertRaises(ValueError):
                    drc.parse_report(path, cell="TOP")
        path.write_text("<wrong/>")
        with self.assertRaises(ValueError):
            drc.parse_report(path, cell="TOP")

    def test_exit_two_is_a_completed_drc_with_findings(self):
        def run(*args, **kwargs):
            report(self.out / "drc.lyrdb")
            return subprocess.CompletedProcess(args, 2, "findings", "")
        with patch.object(drc.subprocess, "run", side_effect=run):
            result = drc.run_device_drc(self.out / "input.gds", "TOP", self.out, binary=Path("gdscheck"))
        self.assertEqual(result["markers"], 2)

    def test_error_incomplete_and_missing_reports_cannot_reuse_stale_output(self):
        for status in (1, 3, 0):
            with self.subTest(status=status):
                report(self.out / "drc.lyrdb")
                with patch.object(drc.subprocess, "run", return_value=subprocess.CompletedProcess([], status, "", "")):
                    with self.assertRaises((RuntimeError, FileNotFoundError)):
                        drc.run_device_drc(self.out / "input.gds", "TOP", self.out, binary=Path("gdscheck"))
                self.assertFalse((self.out / "drc.lyrdb").exists())

    def test_success_status_cannot_hide_findings(self):
        def run(*args, **kwargs):
            report(self.out / "drc.lyrdb")
            return subprocess.CompletedProcess(args, 0, "", "")
        with patch.object(drc.subprocess, "run", side_effect=run):
            with self.assertRaisesRegex(ValueError, "exit status"):
                drc.run_device_drc(self.out / "input.gds", "TOP", self.out, binary=Path("gdscheck"))

    def test_stale_binary_cannot_silently_ignore_directional_rules(self):
        for stdout, status in (("", 1), ("GCUT.W.1 value=0.017\nGCUT.S.3 value=0.035", 0)):
            with self.subTest(stdout=stdout):
                drc.require_directional_gcut.cache_clear()
                with patch.object(drc.subprocess, "run", return_value=subprocess.CompletedProcess([], status, stdout, "")):
                    with self.assertRaisesRegex(RuntimeError, "directional gate-cut"):
                        drc.require_directional_gcut(Path("old-gdscheck"))
        drc.require_directional_gcut.cache_clear()

    def test_ratchet_checks_count_distance_and_rule_threshold_independently(self):
        baseline = {"cell|M1.S": dict(count=3, worst_nm=8.0, limit_nm=18.0)}
        for changes in (dict(count=4), dict(worst_nm=7.9), dict(limit_nm=17.0)):
            now = {"cell|M1.S": {**baseline["cell|M1.S"], **changes}}
            self.assertTrue(check_device_drc.compare(now, baseline)[0])
        self.assertEqual(check_device_drc.compare({}, baseline), ([], ["cell|M1.S"]))

    def test_macro_baseline_refuses_a_different_engine_or_deck(self):
        new, gone = verify_macro.compare({"drc_identity": {"engine": "gdscheck"}}, {})
        self.assertIn("engine/deck differs", new[0])
        self.assertEqual(gone, [])

    def test_cross_check_allows_marker_counts_but_rejects_lost_coverage(self):
        ref = {"cell|M1.S": dict(count=2, worst_nm=17.49, limit_nm=18.0)}
        now = {"cell|M1.S": dict(count=1, worst_nm=17.5, limit_nm=18.0)}
        self.assertEqual(check_device_drc.cross_check(now, ref), [])
        self.assertTrue(check_device_drc.cross_check({}, ref))
        now["cell|M1.S"]["worst_nm"] = 17.6
        self.assertTrue(check_device_drc.cross_check(now, ref))


class GeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import gdstk
            cls.gdstk = gdstk
            cls.binary = drc.find_gdscheck()
        except (ImportError, FileNotFoundError) as error:
            raise unittest.SkipTest(str(error))

    def test_width_spacing_notch_hierarchy_and_threshold_boundary(self):
        g = self.gdstk
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            library = g.Library(unit=1e-6, precision=1e-10)
            clean = library.new_cell("CLEAN")
            clean.add(g.rectangle((0, 0), (.018, .1), layer=19),
                      g.rectangle((.036, 0), (.054, .1), layer=19))
            narrow = library.new_cell("NARROW")
            narrow.add(g.rectangle((0, 0), (.017, .1), layer=19))
            close = library.new_cell("CLOSE")
            close.add(g.rectangle((0, 0), (.018, .1), layer=19),
                      g.rectangle((.035, 0), (.053, .1), layer=19))
            notch = library.new_cell("NOTCH")
            notch.add(g.Polygon([(0, 0), (.1, 0), (.1, .1), (.06, .1), (.06, .04),
                                 (.05, .04), (.05, .1), (0, .1)], layer=19))
            top = library.new_cell("TOP")
            top.add(g.Reference(narrow, rotation=math.pi / 2, x_reflection=True,
                                columns=2, rows=2, spacing=(.2, .2)))
            gds = out / "fixture.gds"
            library.write_gds(str(gds))
            counts = {}
            for cell, expected in ((clean, set()), (narrow, {"M1.W"}), (close, {"M1.S"}),
                                   (notch, {"M1.S"}), (top, {"M1.W"})):
                with self.subTest(cell=cell.name):
                    result = drc.run_device_drc(gds, cell.name, out / cell.name, binary=self.binary)
                    self.assertEqual(set(result["rules"]), expected)
                    counts[cell.name] = result["rules"].get("M1.W", 0)
            self.assertEqual(counts["TOP"], 4 * counts["NARROW"])

    def test_gate_cuts_use_vertical_projection_and_public_thresholds(self):
        # All values here are um. A legal 54 x 17 nm cut must not fail just
        # because the next gate's cut is diagonally 10 nm away. Conversely,
        # a vertically facing gap below 35 nm and a height below 17 must fail.
        g = self.gdstk
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            library = g.Library(unit=1e-6, precision=1e-10)
            def pair(name, x, gap):
                cell = library.new_cell(name)
                cell.add(g.rectangle((0, 0), (.054, .017), layer=10),
                         g.rectangle((x, .017 + gap), (x + .054, .034 + gap), layer=10))
                return cell
            pair("STAGGERED", .054, .010)
            pair("BOUNDARY", 0, .035)
            pair("TOO_CLOSE", 0, .03475)
            pair("SLIVER_OVERLAP", .0535, .010)
            thin = library.new_cell("TOO_THIN")
            thin.add(g.rectangle((0, 0), (.054, .01675), layer=10))
            sideways = library.new_cell("HORIZONTAL_NARROW")
            sideways.add(g.rectangle((0, 0), (.010, .054), layer=10))
            # Same-region notch: the two horizontal arms face vertically.
            notch = library.new_cell("NOTCH")
            notch.add(g.Polygon([(0, 0), (.054, 0), (.054, .017), (.017, .017),
                                 (.017, .05175), (.054, .05175), (.054, .06875),
                                 (0, .06875)], layer=10))
            mirrored = library.new_cell("MIRRORED")
            mirrored.add(g.Reference(library["STAGGERED"], x_reflection=True,
                                     columns=2, rows=2, spacing=(.3, .3)))
            rotated = library.new_cell("ROTATED")
            rotated.add(g.Reference(sideways, rotation=math.pi / 2))
            expected = {"STAGGERED": set(), "BOUNDARY": set(), "TOO_CLOSE": {"GCUT.S"},
                        "SLIVER_OVERLAP": {"GCUT.S"},
                        "TOO_THIN": {"GCUT.W"}, "HORIZONTAL_NARROW": set(),
                        "NOTCH": {"GCUT.S"}, "MIRRORED": set(), "ROTATED": {"GCUT.W"}}
            gds = out / "gcut.gds"
            library.write_gds(str(gds))
            for name, want in expected.items():
                with self.subTest(cell=name):
                    result = drc.run_device_drc(gds, name, out / name, binary=self.binary)
                    self.assertEqual(set(result["rules"]), want)
                    # Independent KLayout projection implementation, not a
                    # second use of the gdscheck deck under test.
                    reference = check_device_drc.reference(gds, out / name / "reference", (name,))
                    self.assertEqual({f["rule"] for f in reference}, want)
                    self.assertEqual(check_device_drc.cross_check(
                        check_device_drc.keyed("fixture", result["findings"]),
                        check_device_drc.keyed("fixture", reference)), [])

    def test_missing_top_cell_is_an_error(self):
        g = self.gdstk
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            library = g.Library()
            library.new_cell("TOP")
            library.write_gds(str(out / "input.gds"))
            with self.assertRaises(RuntimeError):
                drc.run_device_drc(out / "input.gds", "MISSING", out, binary=self.binary)


if __name__ == "__main__":
    unittest.main()
