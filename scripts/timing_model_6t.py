#!/usr/bin/env python3
"""A size model of the single-port 6T macro's timing and energy, fit to whole-macro simulations.

The estimated Liberty used one clk->Q and one minimum period for every
macro.  This fits how they move with the geometry, from `simulate_macro.py`
runs of small macros, and turns the fit into the characterization JSON the
Liberty emitter already reads (``--liberty-from``) for any size::

    # fit: simulation folders (each holding simulation.json), their results
    .venv/bin/python scripts/timing_model_6t.py fit tmp/sweep6t/sim_* \\
        --period-sweep tmp/sweep6t/period_x4_* --output tech/timing/sram_6t_timing.json
    # estimate: one macro
    .venv/bin/python scripts/timing_model_6t.py estimate --wordlines 32 --bits 64 \\
        --mux 4 --segment-bits 8 --output results/.../timing.json

The model follows the read path:

* clock to wordline: the controller's predecode, its lines to every
  slice input they drive (a slice per four rows in a strip, and a strip
  per stack end and two per segment boundary), a slice and the wordline
  wire (linear in the cells along a segment);
* wordline to Q: the sense enable's delay chain, its broadcast to every
  data bit's IO block along a stack (linear in the bits), the sense
  amplifier and the output latch (and the mux's leaves on its lines);
* the minimum period: the read runs in the clock-high phase, so the period
  is twice clk->Q plus a margin, the margin fit to a period sweep;
* Q's transition: the output latch's own edge, the worst one seen;
* energy per access: a fixed part, a part per data bit (its IO), and a
  part per bitline cell switched (every column of the bank precharges).

Every simulated point is a TT, 0.7 V run with a 1 fF load on Q and 20 ps
input edges, so the tables are one point, as `gen_characterization_json.py`
writes them: a scalar model, not a slew/load grid.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

SCHEMA = "openfinram-6t-timing-model-1"
#: The stimulus every simulate_macro.py run uses.
STIMULUS_SLEW_NS, STIMULUS_LOAD_PF = 0.020, 0.001


def geometry(described: dict) -> dict:
    """The model's variables from a result's physical.json."""
    rows = 2 * described["wordlines_per_half"]
    mux = described.get("column_mux", 4)
    segments = described.get("wordline_segments_per_stack", 1)
    return {
        "bitline_cells": rows,
        "slice_inputs": slice_inputs(rows, segments),
        "wordline_cells": described.get("cells_along_wordline", mux * (described["bits"] // 2)),
        "mux": mux,
        "bits": described["bits"],
        "bitline_cells_switched": described["bits"] * mux * rows,
    }


def slice_inputs(rows: int, segments: int) -> int:
    """Slices a predecode line reaches in a bank: one per four rows in each strip.

    A stack has its band strip and two (a mid pair) at each boundary
    between its segments; there are two stacks.
    """
    return (rows // 4) * 2 * (2 * segments - 1)


def point(spec: str) -> dict:
    """One simulated macro: its geometry and what the run measured (ps, fJ).

    `spec` is a simulate_macro.py output folder, or ``folder@result`` when
    the run did not record its result folder (``result_dir``).
    """
    folder, _, result = spec.partition("@")
    folder = Path(folder)
    verdict = json.loads((folder / "simulation.json").read_text())
    if not verdict["passed"]:
        raise ValueError(f"{folder}: the simulation did not pass; a failing run is no timing")
    result_dir = Path(result or verdict.get("result_dir", ""))
    physical = next(result_dir.glob("*.physical.json"), None) if result_dir.is_dir() else None
    if physical is None:
        raise ValueError(f"{folder}: name its result folder (folder@result)")
    reads = [r for r in verdict["reads"] if r["clk_to_q_ps"] is not None]
    # A cycle's first read starts from reset: the first access is not the steady state.
    steady = [r for r in reads if r["cycle"] > 1] or reads
    return {
        "folder": str(folder),
        **geometry(json.loads(physical.read_text())),
        "period_ns": verdict["period_ns"],
        "clk_to_wl_ps": max(r["clk_to_wl_ps"] for r in steady if r["clk_to_wl_ps"]),
        "clk_to_q_ps": max(r["clk_to_q_ps"] for r in steady),
        # Runs before simulate_macro.py measured these carry none: None.
        "q_rise_ps": max((r.get("q_rise_ps") or 0) for r in steady) or None,
        "q_fall_ps": max((r.get("q_fall_ps") or 0) for r in steady) or None,
        "read_fJ": _mean([r.get("energy_fJ") for r in steady]),
        "write_fJ": _mean([w.get("energy_fJ") for w in verdict["writes"]]),
    }


def _mean(values: list) -> float | None:
    values = [v for v in values if v]
    return float(np.mean(values)) if values else None


def least_squares(rows: list[list[float]], values: list[float]) -> list[float]:
    coefficients, *_ = np.linalg.lstsq(np.array(rows, dtype=float), np.array(values, dtype=float), rcond=None)
    return [float(c) for c in coefficients]


def fit(points: list[dict], periods: list[tuple[float, bool, float]]) -> dict:
    """Coefficients of each part of the model, and how well they reproduce the points."""
    wl = least_squares([[1, p["slice_inputs"], p["wordline_cells"]] for p in points],
                       [p["clk_to_wl_ps"] for p in points])  # fmt: skip
    wl_to_q = least_squares([[1, p["bits"], p["mux"]] for p in points],
                            [p["clk_to_q_ps"] - p["clk_to_wl_ps"] for p in points])  # fmt: skip
    measured = [p for p in points if p["read_fJ"] and p["write_fJ"]]
    if len(measured) < 3:
        raise ValueError(f"energy needs three runs that measured it per cycle, have {len(measured)}")
    energy = {
        kind: least_squares([[1, p["bits"], p["bitline_cells_switched"]] for p in measured],
                            [p[f"{kind}_fJ"] for p in measured])  # fmt: skip
        for kind in ("read", "write")
    }
    model = {
        "schema": SCHEMA,
        "clk_to_wl_ps": {"base": wl[0], "per_slice_input": wl[1], "per_wordline_cell": wl[2]},
        "wl_to_q_ps": {"base": wl_to_q[0], "per_bit": wl_to_q[1], "per_mux_row": wl_to_q[2]},
        "q_transition_ps": {"rise": max(p["q_rise_ps"] or 0 for p in points),
                            "fall": max(p["q_fall_ps"] or 0 for p in points)},  # fmt: skip
        "energy_fJ": {kind: {"base": c[0], "per_bit": c[1], "per_bitline_cell": c[2]}
                      for kind, c in energy.items()},  # fmt: skip
    }
    # The minimum period: the smallest passing period of the sweep, as twice
    # clk->Q plus a margin (the read must be done in the high phase).
    passing = [(period, q) for period, ok, q in periods if ok]
    if passing:
        period, q = min(passing)
        model["min_period"] = {"margin_ps": round(1000 * period - 2 * q, 1), "sweep_min_ns": period}
    else:
        model["min_period"] = {"margin_ps": None}
    model["points"] = points
    model["residuals_ps"] = [round(predict(model, p)["clk_to_q_ps"] - p["clk_to_q_ps"], 1) for p in points]
    return model


def predict(model: dict, g: dict) -> dict:
    w = model["clk_to_wl_ps"]
    wl = w["base"] + w["per_slice_input"] * g["slice_inputs"] + w["per_wordline_cell"] * g["wordline_cells"]
    m = model["wl_to_q_ps"]
    q = wl + m["base"] + m["per_bit"] * g["bits"] + m["per_mux_row"] * g["mux"]
    out = {"clk_to_wl_ps": wl, "clk_to_q_ps": q}
    if model.get("min_period", {}).get("margin_ps") is not None:
        out["min_period_ps"] = 2 * q + model["min_period"]["margin_ps"]
    bits_cells = g["bits"], g["bitline_cells_switched"]
    for kind, c in model["energy_fJ"].items():
        out[f"{kind}_fJ"] = c["base"] + c["per_bit"] * bits_cells[0] + c["per_bitline_cell"] * bits_cells[1]
    return out


def characterization(model: dict, g: dict, label: str) -> dict:
    """The characterization JSON (`--liberty-from`) the model gives a macro of geometry `g`."""
    p = predict(model, g)
    q_ns = p["clk_to_q_ps"] / 1000
    doc = {
        "schema": "openfinram-characterization-1",
        "source": f"timing_model_6t.py fit to whole-macro Xyce runs (TT 0.70 V 25 C); {label}",
        "comment": (
            "MODELLED 6T TIMING (fit to whole-macro transient simulations of small macros, TT, "
            "one stimulus point; NOT a slew/load grid). Single-port 6T macro (one read/write port, A)."
        ),
        "timing": {"delay": {
            "index_1": [STIMULUS_SLEW_NS], "index_2": [STIMULUS_LOAD_PF],
            "cell_rise": [[q_ns]], "cell_fall": [[q_ns]],
            "rise_transition": [model["q_transition_ps"]["rise"] / 1000],
            "fall_transition": [model["q_transition_ps"]["fall"] / 1000],
        }},  # fmt: skip
    }
    if "min_period_ps" in p:
        doc["clock_min_period"] = round(p["min_period_ps"] / 1000, 4)
    # Energy per access as the emitter takes it: pJ, at a reference cycle.
    doc["power"] = {"read_access_pj": round(p["read_fJ"] / 1000, 6),
                    "write_access_pj": round(p["write_fJ"] / 1000, 6),
                    "reference_cycle_ns": round(p.get("min_period_ps", 2000) / 1000, 4)}  # fmt: skip
    return doc


def periods_of(folders: list[Path]) -> list[tuple[float, bool, float]]:
    out = []
    for folder in folders:
        path = folder / "simulation.json"
        if not path.is_file():
            continue
        verdict = json.loads(path.read_text())
        qs = [r["clk_to_q_ps"] for r in verdict["reads"] if r["clk_to_q_ps"] is not None and r["cycle"] > 1]
        out.append((verdict["period_ns"], bool(verdict["passed"]), max(qs) if qs else float("nan")))
    return sorted(out)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="mode", required=True)
    f = sub.add_parser("fit")
    f.add_argument("simulations", nargs="+", help="simulate_macro.py output folders (folder or folder@result)")
    f.add_argument("--period-sweep", type=Path, nargs="*", default=[], help="one macro's runs at several periods")
    f.add_argument("--output", type=Path, required=True)
    e = sub.add_parser("estimate")
    e.add_argument("--model", type=Path, default=Path(__file__).resolve().parents[1] / "tech/timing/sram_6t_timing.json")
    e.add_argument("--wordlines", type=int, required=True, help="NUM_WL (the array has twice that)")
    e.add_argument("--bits", type=int, required=True)
    e.add_argument("--mux", type=int, default=4)
    e.add_argument("--segment-bits", type=int, default=0)
    e.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.mode == "fit":
        points = [point(folder) for folder in args.simulations]
        model = fit(points, periods_of(args.period_sweep))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(model, indent=1) + "\n")
        print(f"wrote {args.output}: {len(points)} points, clk->Q residuals {model['residuals_ps']} ps")
        return 0
    model = json.loads(args.model.read_text())
    segment = args.segment_bits or args.bits // 2
    g = {"bitline_cells": 2 * args.wordlines, "wordline_cells": args.mux * segment, "mux": args.mux,
         "slice_inputs": slice_inputs(2 * args.wordlines, (args.bits // 2) // segment),
         "bits": args.bits, "bitline_cells_switched": args.bits * args.mux * 2 * args.wordlines}  # fmt: skip
    doc = characterization(model, g, f"x{2 * args.wordlines}x{args.bits}, {args.mux}:1, {segment}-bit segments")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(doc, indent=2) + "\n")
    print(f"wrote {args.output}: clk->Q {doc['timing']['delay']['cell_rise'][0][0]:.4f} ns, "
          f"min period {doc.get('clock_min_period')} ns")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
