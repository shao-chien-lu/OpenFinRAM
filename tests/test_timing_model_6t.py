"""The 6T timing model (tech/timing/sram_6t_timing.json) and its estimate for the Liberty emitter."""

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MODEL = REPO / "tech/timing/sram_6t_timing.json"
sys.path.insert(0, str(REPO / "scripts"))

import timing_model_6t as tm  # noqa: E402


def model():
    return json.loads(MODEL.read_text())


def test_the_fit_reproduces_every_simulated_macro():
    m = model()
    assert m["schema"] == tm.SCHEMA
    assert len(m["points"]) >= 6
    for point in m["points"]:
        predicted = tm.predict(m, point)
        assert abs(predicted["clk_to_q_ps"] - point["clk_to_q_ps"]) < 10, point
        assert abs(predicted["clk_to_wl_ps"] - point["clk_to_wl_ps"]) < 10, point


def test_bigger_macros_are_slower_and_cost_more():
    m = model()
    def macro(rows, bits, segments=1, mux=4):
        segment = bits // 2 // segments
        return {"bitline_cells": rows, "slice_inputs": tm.slice_inputs(rows, segments),
                "wordline_cells": mux * segment, "mux": mux, "bits": bits,
                "bitline_cells_switched": bits * mux * rows}

    small = macro(4, 2)
    for grown in (macro(64, 2), macro(4, 64), macro(4, 64, segments=4), macro(64, 64, segments=4)):
        assert tm.predict(m, grown)["clk_to_q_ps"] > tm.predict(m, small)["clk_to_q_ps"], grown
        assert tm.predict(m, grown)["read_fJ"] > tm.predict(m, small)["read_fJ"], grown
    # The read runs in the clock-high phase: the period is more than twice clk->Q.
    p = tm.predict(m, small)
    assert p["min_period_ps"] > 2 * p["clk_to_q_ps"]


def test_estimate_writes_the_characterization_the_emitter_reads(tmp_path):
    out = tmp_path / "timing.json"
    subprocess.run([sys.executable, str(REPO / "scripts/timing_model_6t.py"), "estimate", "--wordlines", "32",
                    "--bits", "64", "--segment-bits", "8", "--output", str(out)], check=True)  # fmt: skip
    doc = json.loads(out.read_text())
    assert doc["schema"] == "openfinram-characterization-1"
    delay = doc["timing"]["delay"]
    assert len(delay["cell_rise"]) == len(delay["index_1"]) == 1
    assert 0.2 < delay["cell_rise"][0][0] < 1.0
    assert doc["clock_min_period"] > 2 * delay["cell_rise"][0][0]
    assert doc["power"]["read_access_pj"] > 0
