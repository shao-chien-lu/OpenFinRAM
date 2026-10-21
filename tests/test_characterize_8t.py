"""Read-characterization acceptance and units, without running a simulator."""

import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import characterize_8t as char
import simulate_macro as sm


def passing():
    return {
        "passed": True,
        "unproven": [],
        "hazards": [],
        "cells_wrong": [],
        "reads": [
            {
                "port": "A",
                "ok": True,
                "clk_to_q_rise_ps": 210,
                "clk_to_q_fall_ps": None,
                "q_rise_ps": 12,
                "q_fall_ps": None,
            },
            {
                "port": "A",
                "ok": True,
                "clk_to_q_rise_ps": 220,
                "clk_to_q_fall_ps": 230,
                "q_rise_ps": 13,
                "q_fall_ps": 14,
            },
            {
                "port": "B",
                "ok": True,
                "clk_to_q_rise_ps": 310,
                "clk_to_q_fall_ps": 320,
                "q_rise_ps": 22,
                "q_fall_ps": 23,
            },
        ],
    }


def test_worst_observed_timing_is_separate_by_port_and_polarity_in_ns():
    assert char.summarize_point(passing()) == {
        "A": {
            "cell_rise": 0.220,
            "cell_fall": 0.230,
            "rise_transition": 0.013,
            "fall_transition": 0.014,
        },
        "B": {
            "cell_rise": 0.310,
            "cell_fall": 0.320,
            "rise_transition": 0.022,
            "fall_transition": 0.023,
        },
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("passed", False),
        ("unproven", ["Q_B[0] to 0"]),
        ("hazards", [{"what": "write enable"}]),
        ("cells_wrong", [{"address": 1}]),
    ],
)
def test_failed_functional_or_coverage_checks_cannot_produce_timing(field, value):
    verdict = passing()
    verdict[field] = value
    with pytest.raises(ValueError, match="simulation failed"):
        char.summarize_point(verdict)


@pytest.mark.parametrize(
    "measurement", ["clk_to_q_rise_ps", "clk_to_q_fall_ps", "q_rise_ps", "q_fall_ps"]
)
@pytest.mark.parametrize("bad", [None, float("nan"), -1, 0])
def test_each_port_requires_complete_finite_rise_and_fall_measurements(
    measurement, bad
):
    verdict = passing()
    verdict["reads"][-1][measurement] = bad
    with pytest.raises(ValueError, match="port B"):
        char.summarize_point(verdict)


def test_grid_preserves_slew_then_load_order_and_documents_estimates():
    a = char.summarize_point(passing())
    b = copy.deepcopy(a)
    b["A"]["cell_rise"] = 0.9
    grid = {(0, 0): a, (0, 1): b, (1, 0): b, (1, 1): a}
    data = char.characterization(
        "sram_x4x2x1",
        [0.01, 0.04],
        [0.0005, 0.002],
        grid,
        corner="SS",
        vdd=0.63,
        temperature=125,
        period_ns=2,
    )
    assert data["timing"]["port_delays"]["A"]["cell_rise"] == [[0.22, 0.9], [0.9, 0.22]]
    assert data["timing"]["port_delays"]["B"]["cell_rise"] == [
        [0.31, 0.31],
        [0.31, 0.31],
    ]
    assert data["operating_conditions"] == {
        "name": "SS",
        "voltage": 0.63,
        "temperature": 125,
    }
    assert data["clock_min_period"] == 2
    assert data["output_max_capacitance"] == 0.002
    assert "estimated defaults" in data["comment"]
    assert "power" not in data


@pytest.mark.parametrize(
    "axis", ["", "0.01,0.01", "0.04,0.01", "0,0.01", "nan,1", "1,inf"]
)
def test_invalid_sweep_axes_are_rejected(axis):
    with pytest.raises((ValueError, argparse.ArgumentTypeError)):
        char.axis(axis)


def test_clock_slew_is_20_to_80_percent_and_preserves_the_clock_midpoint():
    text, plan = sm.build_deck(
        Path("dut.sp"),
        Path("model.pm"),
        "top",
        (),
        [],
        sm.Geometry(2, 4, 2),
        [],
        period=2e-9,
        vdd=0.7,
        load=1e-15,
        clock_slew=30e-12,
        temperature=125,
        sample_interval=0.5e-12,
    )
    clock = next(line for line in text.splitlines() if line.startswith("Vclk"))
    pulse = [float(value) for value in clock.split("PULSE(")[1].rstrip(")").split()]
    assert pulse[3] == pytest.approx(50e-12)  # full swing corresponding to 30 ps 20-80%
    assert pulse[2] + pulse[3] / 2 == pytest.approx(2e-9)
    assert plan["clock_slew"] == pytest.approx(30e-12)
    assert ".temp 125" in text
    assert "initial_interval=5e-13" in text


def test_6t_result_is_rejected_before_starting_simulation(tmp_path):
    (tmp_path / "sram_x4x2x1.physical.json").write_text(
        json.dumps({"cell": "sram_x4x2x1", "bitcell": "6t"})
    )
    (tmp_path / "sram_x4x2x1.sp").write_text(
        ".SUBCKT sram_cell_6t_122 vdd vss\n.ENDS\n"
    )
    with pytest.raises(ValueError, match="true dual-port 8T"):
        char.run(argparse.Namespace(result_dir=tmp_path))


def test_liberty_only_exports_port_tables_that_opensta_uses(tmp_path):
    binary = Path(os.environ.get("OPENFINRAM_BIN", sm.REPO_ROOT / "build/OpenFinRAM"))
    sta = shutil.which("sta")
    if not binary.is_file() or not sta:
        pytest.skip("requires built OpenFinRAM and OpenSTA")
    cell = "sram_x4x2x1"
    pins = ["vdd", "vss", "clk", "rst_n"]
    for port in "AB":
        pins += [f"{p}_n_{port}" for p in ("ce", "we", "oe")]
        pins += [f"A_{port}[{i}]" for i in range(4)]
        pins += [f"{p}_{port}[{i}]" for p in "DQ" for i in range(2)]
    lef = tmp_path / "macro.lef"
    lef.write_text(
        f"MACRO {cell}\n SIZE 10 BY 20 ;\n"
        + "".join(f" PIN {pin}\n END {pin}\n" for pin in pins)
        + f"END {cell}\n"
    )
    a, b = char.summarize_point(passing()), char.summarize_point(passing())
    b["A"]["cell_rise"] = 0.9
    data = char.characterization(
        cell,
        [0.01, 0.04],
        [0.0005, 0.002],
        {(0, 0): a, (0, 1): b, (1, 0): b, (1, 1): a},
        corner="TT",
        vdd=0.7,
        temperature=25,
        period_ns=2,
    )
    source, lib = tmp_path / "timing.json", tmp_path / "macro.lib"
    source.write_text(json.dumps(data))
    command = [
        str(binary.resolve()),
        "--liberty-only",
        str(lef),
        "--liberty-from",
        str(source),
        "--liberty-output",
        str(lib),
    ]
    generated = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
    assert generated.returncode == 0, generated.stdout + generated.stderr
    assert not (tmp_path / "results").exists()  # no compilation/P&R side effects
    wrapper = tmp_path / "top.v"
    wrapper.write_text(f"""module top(input clk, output [1:0] Q_A, Q_B);
      {cell} mem(.clk(clk), .Q_A(Q_A), .Q_B(Q_B));
      endmodule
    """)
    tcl = tmp_path / "check.tcl"
    tcl.write_text(f"""read_liberty {{{lib}}}
read_verilog {{{wrapper}}}
link_design top
create_clock -name clk -period 2 [get_ports clk]
set_clock_transition 0.025 [get_clocks clk]
set_load 0.00125 [all_outputs]
report_dcalc -from mem/clk -to {{mem/Q_A[0]}} -digits 6
report_dcalc -from mem/clk -to {{mem/Q_B[0]}} -digits 6
exit
""")
    checked = subprocess.run([sta, "-exit", str(tcl)], capture_output=True, text=True)
    report = checked.stdout + checked.stderr
    assert checked.returncode == 0 and "Error:" not in report, report
    # Bilinear interpolation at the center of the grid, independent A/B arcs.
    assert "Delay = 0.560000" in report, report
    assert "Delay = 0.310000" in report, report
    assert "Slew = 0.013000" in report, report
    assert "Slew = 0.022000" in report, report

    # An explicitly requested malformed JSON must fail, never silently emit defaults.
    source.write_text('{"timing":{"port_delays":{"A":{}}}}')
    failed = subprocess.run(
        command[:-1] + [str(tmp_path / "invalid.lib")], capture_output=True, text=True
    )
    assert failed.returncode != 0
    assert not (tmp_path / "invalid.lib").exists()
