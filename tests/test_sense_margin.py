"""The two-sided IO block's sense-margin testbench: the deck without Xyce, the read with it (slow)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

pytest.importorskip("chipforge_asap7")
import characterize_sense_margin as margin  # noqa: E402


def _deck(two_sided: bool, stored: int = 0) -> str:
    return margin.build_deck(margin.port_b_spec(two_sided), 16, 20e-12, stored, Path("model.pm"))


def test_the_two_decks_differ_only_by_the_far_group_and_its_wire():
    one, two = _deck(False), _deck(True)
    # The far group: its leaves' devices, a column of cells, and each sense line's run across the core.
    assert "M0R_" not in one and "M0R_" in two
    assert "Cwsa SA VSS" in two and "Cwsan SAN VSS" in two and "Cwsa" not in one
    assert two.count(" sram_cell_8t\n") == 2 * one.count(" sram_cell_8t\n") == 32
    # ... deselected and precharged, so only the near group's leaf 0 is ever on the sense lines.
    for line in ("VYSEL_R_0 YSEL_R_0 0 0.0", "VYSELN_R_0 YSELN_R_0 0 0.7", "VPRECHN_R PRECHN_R 0 0.0"):
        assert line in two
    for line in ("VYSEL_0 YSEL_0 0 0.7", "VYSELN_0 YSELN_0 0 0.0", "VYSELN_1 YSELN_1 0 0.7"):
        assert line in one and line in two


def test_only_the_read_cell_sees_the_wordline_and_the_rest_store_the_other_value():
    for stored in (0, 1):
        deck = _deck(False, stored)
        assert deck.count(" wlb0 ") == 2  # its source and cell 0
        assert f"V(Xc0:Q)={0.7 * stored}" in deck
        assert f"V(Xc1:Q)={0.7 * (1 - stored)}" in deck


@pytest.mark.skipif(not os.environ.get("OPENFINRAM_SLOW_TESTS"), reason="runs Xyce; set OPENFINRAM_SLOW_TESTS=1")
def test_the_shared_block_reads_right_with_a_smaller_split(tmp_path: Path):
    try:
        margin.sm.find_xyce()
    except FileNotFoundError as error:
        pytest.skip(str(error))
    results = margin.characterize(tmp_path, [16], [10e-12, 20e-12], jobs=4)
    assert all(r["read_ok"] for r in results)
    for delta in (10, 20):
        one = next(r for r in results if not r["two_sided"] and r["delta_ps"] == delta and r["stored"] == 0)
        two = next(r for r in results if r["two_sided"] and r["delta_ps"] == delta and r["stored"] == 0)
        # The far group and the core wire load the sense lines: a smaller, not a vanishing, split.
        assert 0.5 * one["sense_split_mv"] < two["sense_split_mv"] < one["sense_split_mv"]
