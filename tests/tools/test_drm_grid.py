"""The macro's blocks, tracks and pins on the ASAP7 DRM's absolute grids; no router required."""

import math
from pathlib import Path
import re
import sys
import unittest

import gdstk

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from compile_asap7_2rw import (  # noqa: E402
    DRM_TRACKS, FIN_GRID, GATE_GRID, HALF_NM, TRACK_LATTICE, bbox_origin, check_grid, grid_phase, grid_rules,
    grid_shift, io_block_grids, offset_rules, power_stripes, retrack_strip_vias, side_pin_placements, snap,
    strip_via_window, tag_hierarchy,
)


def block(fin_phase, gate_phase, below=0.0):
    """Fins every 27 nm `fin_phase` nm past a multiple, gates every 54 `gate_phase` past one,
    and an M4 track wire centred 12 nm up; `below` um of metal under the frame's origin."""
    cell = gdstk.Cell(f"block_{fin_phase}_{gate_phase}")
    for k in range(4):
        y = (fin_phase + 27 * k) / 1000
        cell.add(gdstk.rectangle((0, y - 0.0035), (0.216, y + 0.0035), layer=2))
    for k in range(4):
        x = (gate_phase + 54 * k) / 1000
        cell.add(gdstk.rectangle((x - 0.01, 0), (x + 0.01, 0.108), layer=7))
    cell.add(gdstk.rectangle((0, 0), (0.216, 0.024), layer=40))
    if below:
        cell.add(gdstk.rectangle((0, -below), (0.018, 0), layer=19))
    return cell


def placed(cell, origin, shift):
    """Fin y and gate x centres, nm, of `cell` in a master `abstract` would make, at `origin` (um)."""
    fx, fy = (o + s - b for o, s, b in zip(origin, shift, bbox_origin(cell)))
    fins = {round((1000 * (fy + (p.bounding_box()[0][1] + p.bounding_box()[1][1]) / 2)), 3)
            for p in cell.get_polygons(layer=2, datatype=0)}
    gates = {round((1000 * (fx + (p.bounding_box()[0][0] + p.bounding_box()[1][0]) / 2)), 3)
             for p in cell.get_polygons(layer=7, datatype=0)}
    return fins, gates


def on(values, pitch, offset):
    return all(abs((v - offset) / pitch - round((v - offset) / pitch)) < 1e-6 for v in values)


class DrmGridTests(unittest.TestCase):
    def test_an_array_frame_block_moves_half_a_nanometre_and_lands_on_the_grids(self):
        # The bitcell's frame: fins on multiples of 27, gates on multiples of 54.
        cell = block(0, 0)
        phase = grid_phase(cell)
        self.assertEqual(phase, (0.0, 0.0))
        shift = grid_shift(phase)
        self.assertEqual(shift, (0.0, HALF_NM))
        x_rules, y_rules = grid_rules(cell, phase, shift)
        origin = (snap(0.3, x_rules), snap(0.3, y_rules))
        self.assertTrue(all(v >= 0.3 for v in origin))
        fins, gates = placed(cell, origin, shift)
        self.assertTrue(on(fins, *FIN_GRID), fins)
        self.assertTrue(on(gates, *GATE_GRID), gates)

    def test_a_routed_block_lands_on_its_tracks_too(self):
        # The controller: the standard cells' frame, routed on the DRM tracks
        # in it, with a pin conductor 22 nm under its origin.
        cell = block(13.5, 27, below=0.022)
        phase = grid_phase(cell)
        self.assertEqual(grid_shift(phase), (0.0, 0.0))
        x_rules, y_rules = grid_rules(cell, phase, tracks=(4, 5))
        origin = (snap(0.3, x_rules), snap(0.3, y_rules))
        fins, gates = placed(cell, origin, (0.0, 0.0))
        self.assertTrue(on(fins, *FIN_GRID))
        self.assertTrue(on(gates, *GATE_GRID))
        frame_y = 1000 * (origin[1] - bbox_origin(cell)[1])
        self.assertTrue(on([frame_y + 12], *DRM_TRACKS[4]))
        self.assertEqual(round(frame_y) % TRACK_LATTICE, 0)
        check_grid([("CTRL", "c", origin, None)], {"c": (x_rules, y_rules)})
        with self.assertRaises(RuntimeError):
            check_grid([("CTRL", "c", (origin[0], origin[1] + 0.027), None)], {"c": (x_rules, y_rules)})

    def test_rules_offset_to_another_reference(self):
        rules = [(27, 13), (48, 4)]
        moved = offset_rules(rules, 0.1)
        self.assertEqual(snap(0.2, moved) + 0.1, snap(0.3, rules))

    def test_supply_stripes_and_side_pins_are_on_the_drm_tracks(self):
        for _, x, _ in power_stripes(10.0, 20.0, 7, 4.0):
            self.assertEqual((x - 16) % 64, 0)
        external = {"A_A[0]", "A_A[1]", "ce_n_A", "we_n_A", "oe_n_A", "clk", "rst_n", "D_A[0]", "Q_A[0]"}
        probes = {net: [{"instance": "CTRL", "point": [1.0, 2.0 + 0.1 * k]}] for k, net in enumerate(sorted(external))}
        for command in side_pin_placements(external, probes, 5.0, 10.0, 6, ports=("A",)):
            match = re.search(r"-location \{\S+ (\S+)\}", command)
            assert match, command
            y = float(match.group(1))
            self.assertEqual(round(1000 * y - 16) % 64, 0, command)

    def test_an_io_block_takes_the_tiles_m4_grid_through_its_references(self):
        block = gdstk.Cell("iocol_x4_r2_test")
        wrapper = gdstk.Cell("iocol_sram_test")
        wrapper.add(gdstk.Reference(block, (0.1, 0.2)))
        tile = gdstk.Cell("tile")
        tile.add(gdstk.Reference(wrapper, (1.0, 2.0), x_reflection=True))
        # (5, 30) nm in the tile: (-995, 1970) in the mirrored wrapper, less the block's origin.
        self.assertEqual(io_block_grids(tile, (5, 30)), {"iocol_x4_r2_test": (-1095 % 24, 1770 % 48)})
        # A half turn negates both.
        turned = gdstk.Cell("turned")
        turned.add(gdstk.Reference(block, (0.0, 0.0), rotation=math.pi))
        self.assertEqual(io_block_grids(turned, (5, 30)), {"iocol_x4_r2_test": (-5 % 24, -30 % 48)})
        # Two places on different tracks cannot share one block.
        twice = gdstk.Cell("twice")
        twice.add(gdstk.Reference(block, (0.0, 0.0)), gdstk.Reference(block, (0.0, 0.024)))
        with self.assertRaises(RuntimeError):
            io_block_grids(twice, (0, 12))

    def test_only_the_cells_holding_a_placed_block_are_renamed(self):
        placed = gdstk.Cell("iocol_x4_r2_test_g0x12")
        other = gdstk.Cell("array")
        wrapper = gdstk.Cell("iocol_sram_test")
        wrapper.add(gdstk.Reference(placed))
        tile = gdstk.Cell("tile")
        tile.add(gdstk.Reference(wrapper), gdstk.Reference(other))
        tag_hierarchy(tile, "_g0x12")
        self.assertEqual([c.name for c in (tile, wrapper, other, placed)],
                         ["tile_g0x12", "iocol_sram_test_g0x12", "array", "iocol_x4_r2_test_g0x12"])

    def test_a_strip_pairs_via_row_moves_onto_a_track_its_stubs_reach(self):
        pair = strip_pair()
        y, lo, hi, stacks = strip_via_window(pair)
        # Down to the pair's edge; up to a corner's reach (20 nm across 9) short of
        # the neighbouring column, less the M3 a V3 needs past it.
        self.assertEqual((y, lo), (0.0385, 0.017))
        self.assertAlmostEqual(hi, math.floor((0.0975 - math.sqrt(0.02**2 - 0.009**2) - 0.017) * 2000) / 2000)
        self.assertEqual(stacks, [(0.1, 0.106)])
        moved = retrack_strip_vias(pair, 12, "pair_g12")
        vias = {r.cell.name: r.origin[1] for r in moved.references}
        self.assertEqual(vias, {"wl_via34": 0.06, "wl_via45": 0.06})
        strap = max((q for q in moved.polygons if q.layer == 50), key=lambda q: q.area())
        self.assertAlmostEqual(strap.bounding_box()[0][1], 0.06 - 0.023)
        m3 = gdstk.boolean([q for q in moved.polygons if q.layer == 30 and q.bounding_box()[0][0] < 0.1], [], "or")
        self.assertTrue(any(q.bounding_box()[0][1] <= 0.06 - 0.017 and q.bounding_box()[1][1] >= 0.06 + 0.017
                            for q in m3))
        # No track between 13 and 17 nm and the window's top reaches a track.
        self.assertIsNone(retrack_strip_vias(pair, 15, "pair_g15"))

    def test_a_mirrored_strip_pair_moves_the_pair_it_mirrors(self):
        pair = strip_pair()
        wrapper = gdstk.Cell("pair_r")
        wrapper.add(gdstk.Reference(pair, (0.3, 0.0), rotation=math.pi, x_reflection=True))
        y, lo, hi, stacks = strip_via_window(wrapper)
        self.assertEqual(strip_via_window(pair)[:3], (y, lo, hi))
        self.assertEqual([tuple(round(x, 6) for x in stack) for stack in stacks], [(0.2, 0.194)])
        moved = retrack_strip_vias(wrapper, 12, "pair_r_g12")
        (ref,) = moved.references
        self.assertEqual((ref.cell.name, tuple(ref.origin)), ("pair_g12", (0.3, 0.0)))
        self.assertEqual({r.origin[1] for r in ref.cell.references}, {0.06})


def strip_pair():
    """A strip pair's port-B corner: a stub under a via stack at 38.5 nm, its strap, a
    neighbouring column 9 nm across starting at 97.5 nm, the pair's boundary."""
    via34, via45 = gdstk.Cell("wl_via34"), gdstk.Cell("wl_via45")
    via34.add(gdstk.rectangle((-0.020, -0.012), (0.020, 0.012), layer=40))
    via45.add(gdstk.rectangle((-0.023, -0.012), (0.023, 0.012), layer=40))
    pair = gdstk.Cell("pair")
    pair.add(gdstk.Reference(via34, (0.1, 0.0385)), gdstk.Reference(via45, (0.106, 0.0385)))
    pair.add(gdstk.rectangle((0.091, 0.0135), (0.109, 0.0635), layer=30))
    pair.add(gdstk.rectangle((0.118, 0.0975), (0.136, 0.3), layer=30))
    pair.add(gdstk.rectangle((0.094, 0.0155), (0.118, 0.6), layer=50))
    pair.add(gdstk.rectangle((0.0, 0.0), (0.3, 0.6), layer=100))
    return pair


if __name__ == "__main__":
    unittest.main()
