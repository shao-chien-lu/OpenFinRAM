"""Fast physical compiler contract tests; no router or simulator required."""

import json
from collections import defaultdict
import re
from pathlib import Path
import sys
import tempfile
import unittest

import gdstk

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from chipforge_asap7.verification.connectivity import MetalGraph, METALS
from compile_asap7_2rw import abstract, build_leaf, check_route_drc, leaf_net, strip_net, supply, verify
from mapped_verilog_to_spice import convert


class PhysicalMacroTests(unittest.TestCase):
    def test_controller_access_uses_pin_landing_not_internal_upper_metal(self):
        cell = gdstk.Cell("ctrl")
        cell.add(
            gdstk.rectangle((0, 0), (0.1, 0.1), layer=50),
            gdstk.rectangle((0, 0), (0.1, 0.1), layer=60),
            gdstk.rectangle((0.02, 0.02), (0.04, 0.04), layer=55),
            gdstk.rectangle((0, 0), (0.1, 0.1), layer=50, datatype=251),
            gdstk.rectangle((0.2, 0), (0.3, 0.1), layer=19),
            gdstk.rectangle((0.4, 0), (0.5, 0.1), layer=19),
        )
        label = gdstk.Label("ysel_A[0]", (0.05, 0.05), layer=50, texttype=251)
        cell.add(
            label,
            gdstk.Label("vdd", (0.25, 0.05), layer=19),
            gdstk.Label("vss", (0.45, 0.05), layer=19),
        )
        _, pins, lef, _ = abstract(cell, "dp_controller", [label])
        self.assertEqual(
            next(p for p in pins.values() if p["net"] == label.text)["layer"], 50
        )
        self.assertIn("LAYER M6", lef.split("  OBS")[1])

    def test_abutting_rectangles_survive_hierarchy_roundoff(self):
        cell = gdstk.Cell("test")
        cell.add(
            gdstk.rectangle((0, 0), (0.1, 0.594), layer=30),
            gdstk.rectangle((0, 0.5940000000000001), (0.1, 1.188), layer=30),
            gdstk.rectangle((0, 1.189), (0.1, 1.5), layer=30),
        )
        graph = MetalGraph(cell)
        self.assertEqual(graph.at(30, (0.05, 0.3)), graph.at(30, (0.05, 0.9)))
        self.assertNotEqual(graph.at(30, (0.05, 0.9)), graph.at(30, (0.05, 1.3)))

    def test_mapped_spice_uses_cdl_order_and_expands_bus(self):
        design = {
            "modules": {
                "ctrl_decode": {
                    "ports": {"A": {"bits": [2, 3]}, "Q": {"bits": [4]}},
                    "netnames": {},
                    "cells": {
                        "inst": {
                            "type": "gate",
                            "connections": {"Y": [4], "RESETN": [3], "A": [2]},
                        }
                    },
                }
            }
        }
        cdl = ".SUBCKT gate RESETn A VDD VSS Y\n.ENDS gate\n"
        spice = convert(design, cdl)
        self.assertIn(".SUBCKT ctrl_decode A[0] A[1] Q VDD VSS", spice)
        self.assertIn("X0 A[1] A[0] VDD VSS Q gate", spice)
        with self.assertRaisesRegex(RuntimeError, "no CDL"):
            convert(design, "")
        del design["modules"]["ctrl_decode"]["cells"]["inst"]["connections"]["A"]
        with self.assertRaisesRegex(RuntimeError, "missing/non-scalar pin A"):
            convert(design, cdl)

    def test_vias_only_join_adjacent_layers(self):
        cell = gdstk.Cell("test")
        for layer in (19, 20, 30):
            cell.add(gdstk.rectangle((0, 0), (0.1, 0.1), layer=layer))
        cell.add(gdstk.rectangle((0.02, 0.02), (0.04, 0.04), layer=21))
        graph = MetalGraph(cell)
        self.assertEqual(graph.at(19, (0.03, 0.03)), graph.at(20, (0.03, 0.03)))
        self.assertNotEqual(graph.at(19, (0.03, 0.03)), graph.at(30, (0.03, 0.03)))

    def test_mapped_spice_preserves_single_bit_vectors_and_supply_ports(self):
        design = {
            "modules": {
                "ctrl_decode": {
                    "ports": {
                        "A": {"bits": [2]},
                        "Y": {"bits": [3]},
                        "VDD": {"bits": [4]},
                        "VSS": {"bits": [5]},
                    },
                    "netnames": {"A": {"attributes": {"single_bit_vector": "1"}}},
                    "cells": {
                        "inst": {
                            "type": "gate",
                            "connections": {"A": [2], "Y": [3], "VDD": [4], "VSS": [5]},
                        }
                    },
                }
            }
        }
        spice = convert(design, ".SUBCKT gate A VDD VSS Y\n.ENDS gate\n")
        self.assertIn(".SUBCKT ctrl_decode A[0] Y VDD VSS\n", spice)
        self.assertIn("X0 A[0] VDD VSS Y gate", spice)

    def test_topology_verifier_rejects_opens_and_shorts(self):
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "test.gds"
            manifest = {
                "cell": "test",
                "external": [],
                "nets": {
                    "a": [{"layer": 19, "point": [0.01, 0.01]}],
                    "b": [{"layer": 19, "point": [0.21, 0.01]}],
                },
            }
            lib = gdstk.Library()
            cell = lib.new_cell("test")
            cell.add(
                gdstk.rectangle((0, 0), (0.1, 0.1), layer=19),
                gdstk.rectangle((0.2, 0), (0.3, 0.1), layer=19),
            )
            lib.write_gds(str(path))
            verify(path, manifest)
            opened = json.loads(json.dumps(manifest))
            opened["nets"]["a"].extend(opened["nets"].pop("b"))
            with self.assertRaisesRegex(RuntimeError, "disconnected"):
                verify(path, opened)
            cell.add(gdstk.rectangle((0.09, 0), (0.21, 0.1), layer=19))
            lib.write_gds(str(path))
            with self.assertRaisesRegex(RuntimeError, "short"):
                verify(path, manifest)

    def test_route_report_fails_closed(self):
        with tempfile.TemporaryDirectory() as scratch:
            report = Path(scratch) / "drc.rpt"
            with self.assertRaisesRegex(RuntimeError, "missing"):
                check_route_drc(report)
            report.write_text("Short M3\n")
            with self.assertRaisesRegex(RuntimeError, "violations"):
                check_route_drc(report)
            report.write_text("")
            check_route_drc(report)

    def test_both_ports_and_banks_map_onto_one_unsplit_array(self):
        for port in "AB":
            self.assertEqual(leaf_net(f"D{port}", 1, 3, 4, 8), f"D_{port}[3]")
            self.assertEqual(leaf_net(f"wrena{port}", 1, 3, 4, 8), f"wrena_{port}[1]")
            self.assertEqual(leaf_net(f"wrenan{port}", 1, 3, 4, 8), f"wrenan_{port}[1]")
            self.assertEqual(leaf_net(f"blprechn{port}", 1, 3, 4, 8), f"blprechn_{port}[1]")
            # NUM_WL = 4: a bank's array has eight wordlines, the upper four
            # being the ones the address bit above the column select picks.
            # A wordline belongs to the stack its bit is in: bits 0..3 of
            # eight are below the controller band, 4..7 above.
            self.assertEqual(leaf_net(f"WL{port}[2]", 1, 3, 4, 8), f"wl_{port}_lo[10]")
            self.assertEqual(leaf_net(f"WL{port}[7]", 0, 3, 4, 8), f"wl_{port}_lo[7]")
            self.assertEqual(leaf_net(f"WL{port}[7]", 0, 4, 4, 8), f"wl_{port}_hi[7]")
            self.assertEqual(leaf_net(f"ysel{port}[2]", 1, 3, 4, 8), f"ysel_{port}[6]")
            self.assertEqual(leaf_net(f"yseln{port}[2]", 1, 3, 4, 8), f"yseln_{port}[6]")
            for split_era in (f"WLT{port}[2]", f"yselt{port}[2]", f"blprechtn{port}"):
                with self.assertRaisesRegex(RuntimeError, "unmapped"):
                    leaf_net(split_era, 0, 0, 4, 8)
        with self.assertRaisesRegex(RuntimeError, "unmapped"):
            leaf_net("typo", 0, 0, 2, 2)

    def test_a_pair_tile_maps_its_second_bank_and_shares_port_b(self):
        # NUM_WL = 4, pair tile of banks 2 and 3: the second bank's wordlines
        # and selects continue the first's, its controls carry an R; port B's
        # enables are the pair's, its precharges the bank's.
        net = lambda name: leaf_net(name, 2, 3, 4, 8, shared_b=True)  # noqa: E731
        self.assertEqual(net("WLA[9]"), "wl_A_lo[25]")  # bank 3's wordline 1
        self.assertEqual(net("yselB[6]"), "ysel_B[14]")
        self.assertEqual(net("wrenaAR"), "wrena_A[3]")
        self.assertEqual(net("sae_AR"), "sae_A[3]")
        self.assertEqual(net("sae_A"), "sae_A[2]")
        self.assertEqual(net("blprechnB"), "blprechn_B[2]")
        self.assertEqual(net("blprechnBR"), "blprechn_B[3]")
        for enable in ("wrena", "wrenan", "oe_out", "oeb_out"):
            self.assertEqual(net(f"{enable}B"), f"{enable}_B[1]")
        self.assertEqual(net("sae_B"), "sae_B[1]")
        self.assertEqual(net("QA"), "Q_A[3]")
        self.assertEqual(leaf_net("sae_B", 2, 3, 4, 8), "sae_B[2]")  # not shared

    def test_a_pair_tile_is_two_mirrored_halves_about_one_port_b_block(self):
        import generate_asap7_8t_iocolumn as columns

        tile = build_leaf(8, 8, bottom=True, top=False, shared_b=True)
        half, block, second = tile.references
        self.assertEqual((half.cell.name, second.cell.name), (half.cell.name, half.cell.name))
        self.assertEqual(block.cell.name, "iocol_sram_8t_b2")
        self.assertTrue(second.x_reflection)
        x0, y0, x1, y1 = columns.boundary_box(tile)
        hx0, hy0, hx1, hy1 = columns.boundary_box(half.cell)
        bx0, _, bx1, _ = columns.boundary_box(block.cell)
        self.assertAlmostEqual(x1 - x0, 2 * (hx1 - hx0) + bx1 - bx0, places=6)
        self.assertEqual((y0, y1), (hy0, hy1))
        labels = [label for label in tile.labels if not supply(label.text)]
        _, pins, _, _ = abstract(tile, "pair", labels)
        nets = {p["net"] for p in pins.values() if not p.get("private")}
        for port in "AB":
            self.assertTrue({f"WL{port}[{i}]" for i in range(16)} <= nets)
        self.assertTrue({"sae_A", "sae_AR", "sae_B", "blprechnB", "blprechnBR", "yselB[7]", "DB", "QB"} <= nets)
        # Both halves drive the bit's Q_A and take its D_A: two pins each.
        self.assertEqual(sum(p["net"] == "QA" for p in pins.values()), 2)

    def test_the_band_plan_puts_each_strip_at_its_stacks_edge_and_hands_it_the_selects(self):
        import subprocess

        with tempfile.TemporaryDirectory() as scratch:
            band = Path(scratch) / "band"
            subprocess.run([sys.executable, str(Path(__file__).resolve().parents[2] / "scripts/compile_asap7_2rw.py"),
                            "--wordlines", "2", "--bits", "2", "--banks", "2", "--share-port-b",
                            "--plan-band", str(band)], check=True, capture_output=True)  # fmt: skip
            plan = dict(line.split() for line in (band / "plan.txt").read_text().splitlines())
            self.assertAlmostEqual(float(plan["width"]), 7.344, places=3)  # one pair tile
            self.assertGreater(float(plan["reserved"]), 0)
            tcl = (band / "band.tcl").read_text()
            # Four strip pairs: each bank's lower and upper, the second bank's mirrored.
            for master in ("lo", "hi", "lo_r", "hi_r"):
                self.assertEqual(tcl.count(f"findMaster dp_wl_strips_{master}]"), 1)
            # The upper pairs hang from the band's top edge, the lower stand on its bottom.
            self.assertEqual(tcl.count("$die_height + ("), 2)
            # Every slice's SEL and B<j> of each port joins the controller's
            # select net, its dont-touch released for the connect:
            # 1 slice x (1 + 4) pins x 2 ports x 4 pairs.
            self.assertEqual(tcl.count("set net [[$block findBTerm {sel_"), 40)
            self.assertEqual(tcl.count("] connect $net"), 40)
            self.assertEqual(tcl.count("$net setDoNotTouch $kept"), 40)
            self.assertIn("{sel_hi_B[1]}", tcl)  # bank 1's slice
            self.assertNotIn("findBTerm {wl_", tcl)  # wordlines stay abutted
            self.assertIn("odb::dbInst_destroy", tcl)
            # The band's edges are the IO blocks' edges, a dummy row less a
            # half fin pitch into the lower tile, plus one into the upper.
            self.assertEqual((plan["inset"], plan["seam_lo"], plan["seam_hi"]), ("0", "0.5805", "0.6075"))
            # A keep-out on each half's dummy rows, at each edge.
            self.assertEqual(tcl.count("] KEEPOUT_"), 4)
            # The lower strips stand on the dummy row, on the nanometre grid.
            self.assertIn("round((0.5710) * $dbu)", tcl)

    def test_an_end_tile_stops_at_its_io_blocks_over_the_io_columns(self):
        import generate_asap7_8t_iocolumn as columns
        from compile_asap7_2rw import end_row_spans, notch_band_edges

        tile = build_leaf(4, 4, bottom=True, top=True, shared_b=True)
        spans = end_row_spans(tile, True)
        self.assertEqual(len(spans), 2)  # each half's dummy rows
        notch_band_edges(tile, spans)
        (outline,) = [p for p in tile.polygons if p.layer == 100]
        x0, y0, x1, y1 = columns.boundary_box(tile)
        self.assertEqual((y0, y1), (-0.594, 2.97))  # the dummy rows still bound it
        inside = lambda x, y: gdstk.inside([(x, y)], [outline])[0]  # noqa: E731
        self.assertTrue(inside(sum(spans[0]) / 2, 2.9))  # over the dummy row
        self.assertFalse(inside(0.5, 2.9))  # over port A's IO: the band's
        self.assertTrue(inside(0.5, 2.38))  # the IO block itself
        self.assertFalse(inside(3.6, -0.3))  # under port B's shared block
        self.assertTrue(inside(3.6, 0.02))

    def test_strip_pair_pins_map_onto_the_controller_and_its_stack(self):
        # NUM_WL = 4: eight wordlines a bank, two slices; bank 1's slices
        # are sel_hi[2..3], the low two bits are shared by every slice.
        for port in "AB":
            self.assertEqual(strip_net(f"SEL_{port}[1]", 1, "hi", 4), f"sel_hi_{port}[3]")
            self.assertEqual(strip_net(f"B_{port}[2]", 1, "hi", 4), f"sel_lo_{port}[2]")
            self.assertEqual(strip_net(f"WL_{port}[5]", 1, "lo", 4), f"wl_{port}_lo[13]")
            self.assertEqual(strip_net(f"WL_{port}[5]", 0, "hi", 4), f"wl_{port}_hi[5]")
        self.assertEqual(strip_net("vss", 0, "hi", 4), "vss")
        with self.assertRaisesRegex(RuntimeError, "unmapped"):
            strip_net("ysel_A[0]", 0, "hi", 4)

    def test_a_middle_tiles_supplies_are_one_vdd_and_one_vss(self):
        # The IO blocks' straps join their rails, the seam ties the array's
        # bars to them: what is left is the top row's upper VSS bar, tied to
        # the rail past the block that the next tile's strap lands on.
        for shared_b in (False, True):
            tile = build_leaf(8, 8, bottom=False, top=False, shared_b=shared_b)
            graph = MetalGraph(tile)
            roots = {}
            for lab in tile.get_labels(depth=None):
                if lab.layer in METALS and supply(lab.text):
                    roots.setdefault(graph.label_root(lab), supply(lab.text))
            top = max(q.bounding_box()[1][1] for q in tile.polygons if q.layer == 100)
            spans = defaultdict(lambda: [9.0, -9.0])
            for i, polygon in enumerate(graph.polygons):
                if graph.root(i) in roots:
                    (_, a), (_, b) = polygon.bounding_box()
                    span = spans[graph.root(i)]
                    span[0], span[1] = min(span[0], a), max(span[1], b)
            whole = sorted(roots[r] for r, (a, b) in spans.items() if a < 0.001)
            past = [roots[r] for r, (a, b) in spans.items() if a >= 0.001]
            self.assertEqual(whole, ["vdd", "vss"], tile.name)
            self.assertTrue(past and set(past) == {"vss"} and all(spans[r][1] > top for r in spans if spans[r][0] >= 0.001),
                            tile.name)  # fmt: skip

    def test_tiles_between_a_stacks_ends_carry_no_dummy_rows(self):
        import generate_asap7_8t_iocolumn as columns
        from chipforge_asap7.verification.connectivity import MetalGraph

        heights = {}
        for bottom in (False, True):
            for top in (False, True):
                tile = build_leaf(8, 8, bottom=bottom, top=top)
                _, y0, _, y1 = columns.boundary_box(tile)
                heights[(bottom, top)] = round(y1 - y0, 4)
        self.assertEqual(heights, {(False, False): 2.376, (True, False): 2.97,
                                   (False, True): 2.97, (True, True): 3.564})  # fmt: skip
        # Three tiles abutted: nothing a tile does not name crosses a seam
        # (a bit's sense lines once did, and a write driver's D reached the
        # next bit's), and no two per-bit nets meet.
        tiles = [build_leaf(8, 8, bottom=True, top=False), build_leaf(8, 8, bottom=False, top=False),
                 build_leaf(8, 8, bottom=False, top=True)]  # fmt: skip
        stack, y, seams, labels = gdstk.Cell("stack"), 0.0, [], []
        shared = re.compile(r"(ysel|yseln|blprechn|wrena|wrenan|oe_out|oeb_out)[AB](\[\d\])?|sae_[AB]|WL[AB]\[\d+\]")
        for k, tile in enumerate(tiles):
            _, y0, _, y1 = columns.boundary_box(tile)
            stack.add(gdstk.Reference(tile, (0, y - y0)))
            for lab in tile.labels:  # the tile's own pins
                if lab.layer in METALS:
                    text = lab.text if supply(lab.text) or shared.fullmatch(lab.text) else f"T{k}_{lab.text}"
                    labels.append(gdstk.Label(text, (lab.origin[0], lab.origin[1] + y - y0), layer=lab.layer))
            y += y1 - y0
            seams.append(y)
        graph = MetalGraph(stack)
        nets = defaultdict(set)
        for lab in labels:
            nets[graph.label_root(lab)].add(supply(lab.text) or lab.text)
        self.assertEqual([sorted(v) for v in nets.values() if len(v) > 1], [])
        extent = defaultdict(lambda: [9.0, -9.0])
        for i, polygon in enumerate(graph.polygons):
            if polygon.layer in METALS:
                (_, a), (_, b) = polygon.bounding_box()
                e = extent[graph.root(i)]
                e[0], e[1] = min(e[0], a), max(e[1], b)
        # The IO blocks' supply straps meet end to end across the seams.
        supplies = {graph.label_root(lab) for lab in stack.get_labels(depth=None)
                    if lab.layer in METALS and supply(lab.text)}  # fmt: skip
        crossing = [r for r, (a, b) in extent.items() if r not in nets and r not in supplies
                    and any(a < seam - 0.05 and b > seam + 0.05 for seam in seams[:-1])]  # fmt: skip
        self.assertEqual(crossing, [])

    def test_a_shifted_abstract_says_exactly_what_the_metal_covers_on_the_grid(self):
        from compile_asap7_2rw import HALF_NM

        cell = gdstk.Cell("half")
        # a signal pin on the grid, one half a nanometre off, an obstruction off it too, and supplies
        cell.add(gdstk.rectangle((0.1, 0.0), (0.118, 0.2), layer=20), gdstk.rectangle((0.2005, 0.0), (0.2185, 0.2), layer=20))
        cell.add(gdstk.rectangle((0.3005, 0.0), (0.3185, 0.2), layer=40))
        # supplies on the grid once shifted up half a nanometre
        cell.add(gdstk.rectangle((0, 0.2995), (0.4, 0.3175), layer=19), gdstk.rectangle((0, 0.3995), (0.4, 0.4175), layer=19))
        labels = [gdstk.Label("A", (0.109, 0.1), layer=20), gdstk.Label("B", (0.2095, 0.1), layer=20)]
        cell.add(*labels, gdstk.Label("VDD", (0.1, 0.309), layer=19), gdstk.Label("VSS", (0.1, 0.409), layer=19))
        _, _, lef, _ = abstract(cell, "half", labels, shift=(0.0, HALF_NM))
        values = [float(v) for v in re.findall(r"-?\d+\.\d+", lef.split("SIZE")[1])]
        self.assertTrue(all(abs(v * 1000 - round(v * 1000)) < 1e-6 for v in values))  # all on the grid
        pins = {m[0]: m[1] for m in re.findall(r"PIN (P\d+).*?POLYGON ([^;]*);", lef, re.S)}
        xs = lambda text: sorted({float(v) for v in text.split()[0::2]})  # noqa: E731
        self.assertEqual(xs(pins["P0"]), [0.1, 0.118])  # on the grid: as drawn
        # off it, 18 nm wide: inward would leave 17 nm, under M2's minimum width, so out
        self.assertEqual(xs(pins["P1"]), [0.2, 0.219])
        obs = lef.split("OBS")[1]
        # an obstruction half a nanometre off: out to cover all of it
        self.assertEqual(sorted({float(v) for v in re.findall(r"POLYGON ([^;]*)", obs)[0].split()[0::2]}), [0.3, 0.319])

    def test_short_parallel_runs_are_lengthened_along_their_own_tracks(self):
        from compile_asap7_2rw import fix_short_parallel_runs

        cell = gdstk.Cell("prl")
        # M4 tracks 48 nm apart: A runs 0-100, B on the next track 80-300 (a 20 nm run)
        cell.add(gdstk.rectangle((0.0, 0.0), (0.1, 0.024), layer=40))
        cell.add(gdstk.rectangle((0.08, 0.048), (0.3, 0.072), layer=40))
        # C and D likewise, but a wire sits right after C's end on its track
        cell.add(gdstk.rectangle((1.0, 0.0), (1.1, 0.024), layer=40), gdstk.rectangle((1.13, 0.0), (1.2, 0.024), layer=40))
        cell.add(gdstk.rectangle((1.08, 0.048), (1.3, 0.072), layer=40))
        fixed, left = fix_short_parallel_runs(cell)
        merged = sorted(tuple(round(v, 3) for xy in p.bounding_box() for v in xy)
                        for p in gdstk.boolean(cell.polygons, [], "or"))  # fmt: skip
        self.assertEqual((fixed, left), (2, 0))
        # A and B now run 44 nm side by side, one lengthened 24 nm on its own track
        a = next(b for b in merged if b[1] == 0.0 and b[0] < 0.5)
        b = next(b for b in merged if b[1] == 0.048 and b[0] < 0.5)
        self.assertEqual(round(min(a[2], b[2]) - max(a[0], b[0]), 3), 0.044)
        self.assertAlmostEqual((a[2] - a[0]) + (b[2] - b[0]), 0.344, places=6)
        # C cannot grow into its neighbour on its track; D grows back instead
        self.assertIn((1.0, 0.0, 1.1, 0.024), merged)
        self.assertIn((1.056, 0.048, 1.3, 0.072), merged)

    def test_abutted_nets_are_probed_but_not_pins(self):
        cell = gdstk.Cell("abutted")
        cell.add(gdstk.rectangle((0, 0), (0.018, 0.5), layer=30))  # a wordline stub
        cell.add(gdstk.rectangle((0.1, 0), (0.118, 0.5), layer=30))  # a routed pin
        cell.add(gdstk.rectangle((0, 0.6), (0.2, 0.618), layer=19), gdstk.rectangle((0, 0.7), (0.2, 0.718), layer=19))
        labels = [gdstk.Label("WLA[0]", (0.009, 0.25), layer=30), gdstk.Label("SEL_A[0]", (0.109, 0.25), layer=30)]
        cell.add(*labels, gdstk.Label("VDD", (0.1, 0.609), layer=19), gdstk.Label("VSS", (0.1, 0.709), layer=19))
        _, pins, lef, _ = abstract(cell, "abutted", labels, abutted=lambda net: net.startswith("WL"))
        by_net = {info["net"]: info for info in pins.values()}
        self.assertTrue(by_net["WLA[0]"].get("abutted"))
        self.assertFalse(by_net["SEL_A[0]"].get("abutted"))
        self.assertEqual(lef.count("  PIN "), 3)  # SEL_A[0], vdd, vss: the wordline is an obstruction
        self.assertIn("OBS", lef)

    def test_capped_tapped_leaf_has_isolated_named_pins(self):
        for wordlines in (2, 4, 18):
            with self.subTest(wordlines=wordlines):
                import math

                leaf = build_leaf(wordlines, math.gcd(wordlines, 16))
                labels = [
                    label
                    for label in leaf.labels
                    if label.text.lower() not in ("vdd", "vss")
                ]
                master, pins, lef, size = abstract(leaf, "column", labels)
                nets = {p["net"] for p in pins.values() if not p.get("private")}
                self.assertTrue({"DA", "DB", "QA", "QB", "vdd", "vss"} <= nets)
                self.assertTrue(any(p.get("private") for p in pins.values()))
                for port in "AB":
                    self.assertTrue(
                        {f"WL{port}[{i}]" for i in range(wordlines)} <= nets
                    )
                    self.assertTrue(
                        {f"{sel}{port}[{i}]" for sel in ("ysel", "yseln") for i in range(4)}
                        | {f"blprechn{port}"} <= nets
                    )
                self.assertFalse(
                    [n for n in nets if n.startswith(
                        ("WLTA", "WLTB", "WLBA", "WLBB", "yselt", "yselb", "blprecht", "blprechb")
                    )]
                )
                # The IO is the parametric block: no wrapper-era pins and no
                # tie-offs (the dummy end rows' vss on M3 is the array's).
                self.assertFalse([n for n in nets if n.startswith(("YSEL", "BLPRECH", "SAPRECHN"))])
                for port in "AB":
                    self.assertIn(f"sae_{port}", nets)
                    self.assertIn(f"oeb_out{port}", nets)

                def count(cell):
                    return (
                        1
                        if cell.name in ("sram_cell_8t", "sram_cell_8t_b", "sram_cell_8t_b_end")
                        else sum(count(ref.cell) for ref in cell.references)
                    )

                self.assertEqual(count(master), 4 * wordlines)
                self.assertIn("OBS", lef)
                self.assertGreater(size[1], 6 * 0.594)
                # The dummy rows' corner and tap-slot stubs are tied in the
                # tile: no supply terminal is a lone 162 nm bar on M2/M4, and
                # every supply pin offers all its layers, rails included.
                supply_pins = [(pin, info) for pin, info in pins.items() if info["net"] in ("vdd", "vss")]
                self.assertTrue(supply_pins)
                for pin, info in supply_pins:
                    block = lef[lef.index(f"  PIN {pin}\n"):]
                    block = block[: block.index(f"  END {pin}")]
                    polygons = [line for line in block.splitlines() if "POLYGON" in line]
                    if len(polygons) == 1 and ("LAYER M2" in block or "LAYER M4" in block):
                        xs = [float(v) for v in polygons[0].split()[1::2]]
                        self.assertGreater(max(xs) - min(xs), 0.2, f"{pin} is a lone stub")
                self.assertTrue(
                    any("LAYER M1" in lef[lef.index(f"  PIN {pin}\n"):lef.index(f"  END {pin}")]
                        and "LAYER M2" in lef[lef.index(f"  PIN {pin}\n"):lef.index(f"  END {pin}")]
                        for pin, _ in supply_pins),
                    "a supply pin with rail and bar on two layers",
                )


if __name__ == "__main__":
    unittest.main()
