#!/usr/bin/env python3
"""Sweep an existing 8T schematic macro and export per-port read-timing Liberty.

Clock slew is 20-80% in ns; output load is pF. Every grid point must pass
the whole-macro read/write checks and exercise both polarities of every Q.
This is an early-PPA read model, not complete timing/power characterization.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
from pathlib import Path
import subprocess

import simulate_macro as sm


def axis(text: str) -> list[float]:
    values = [float(v) for v in text.split(",")]
    if not values or any(not math.isfinite(v) or v <= 0 for v in values):
        raise argparse.ArgumentTypeError("axis must contain positive finite values")
    if any(a >= b for a, b in zip(values, values[1:])):
        raise argparse.ArgumentTypeError("axis must be strictly increasing")
    return values


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def summarize_point(verdict: dict) -> dict:
    """Reject failed or incomplete runs, then take worst observed delay/slew per port."""
    if (
        not verdict.get("passed")
        or verdict.get("unproven")
        or verdict.get("hazards")
        or verdict.get("cells_wrong")
    ):
        raise ValueError(
            "whole-macro simulation failed; see simulation.json and xyce.log"
        )
    tables = {}
    for port in "AB":
        reads = [r for r in verdict["reads"] if r["port"] == port]
        if not reads or not all(r["ok"] for r in reads):
            raise ValueError(f"port {port}: missing or failed reads")
        tables[port] = {}
        for field, measurement in (
            ("cell_rise", "clk_to_q_rise_ps"),
            ("cell_fall", "clk_to_q_fall_ps"),
            ("rise_transition", "q_rise_ps"),
            ("fall_transition", "q_fall_ps"),
        ):
            values = [r[measurement] for r in reads if r.get(measurement) is not None]
            if not values or any(not math.isfinite(v) or v <= 0 for v in values):
                raise ValueError(f"port {port}: missing or invalid {measurement}")
            tables[port][field] = max(values) / 1000  # ps -> ns
    return tables


def characterization(
    cell: str,
    slews: list[float],
    loads: list[float],
    points: dict,
    *,
    corner: str,
    vdd: float,
    temperature: float,
    period_ns: float,
) -> dict:
    ports = {}
    for port in "AB":
        ports[port] = {"index_1": slews, "index_2": loads}
        for field in ("cell_rise", "cell_fall", "rise_transition", "fall_transition"):
            ports[port][field] = [
                [points[i, j][port][field] for j in range(len(loads))]
                for i in range(len(slews))
            ]
    return {
        "schema": "openfinram-characterization-2",
        "cell": cell,
        "comment": (
            "PARTIALLY CHARACTERIZED 8T SCHEMATIC READ MODEL. Xyce clk-to-Q and output "
            "20-80% slew measured separately for A and B; worst observed bit/pattern at each "
            "clock-slew/load point. Both ports active in the read/write test program. "
            "No extracted interconnect. Setup/hold, reset recovery/removal and input "
            "capacitances retain estimated defaults; leakage/internal power uncharacterized. "
            "Clock min_period conservatively uses the validated simulation period, not a "
            "measured minimum. Address/pattern and other-port activity coverage is not exhaustive."
        ),
        "operating_conditions": {
            "name": corner,
            "voltage": vdd,
            "temperature": temperature,
        },
        "clock_min_period": period_ns,
        "output_max_capacitance": loads[-1],
        "timing": {"port_delays": ports},
    }


def run(args: argparse.Namespace) -> Path:
    result = args.result_dir.resolve()
    manifests = list(result.glob("*.physical.json"))
    if len(manifests) != 1:
        raise ValueError("result directory must contain exactly one physical manifest")
    physical = json.loads(manifests[0].read_text())
    cell = physical["cell"]
    netlist, lef = result / f"{cell}.sp", result / f"{cell}.lef"
    subckts = sm.parse_subckts(netlist.read_text())
    if (
        physical.get("bitcell") != "8t"
        or sm.BITCELL not in subckts
        or sm.BITCELL_6T in subckts
    ):
        raise ValueError("characterize_8t requires a true dual-port 8T result")
    if not lef.is_file():
        raise ValueError(f"missing macro LEF: {lef}")
    if not args.openfinram.is_file():
        raise ValueError(f"missing OpenFinRAM executable: {args.openfinram}")
    if (
        not math.isfinite(args.period_ns)
        or args.period_ns <= 0
        or not math.isfinite(args.vdd)
        or args.vdd <= 0
        or not math.isfinite(args.temperature)
        or args.temperature <= -273.15
        or args.jobs < 1
        or not math.isfinite(args.timeout)
        or args.timeout <= 0
    ):
        raise ValueError("invalid period, voltage, temperature, jobs or timeout")
    if args.slews[-1] >= 0.3 * args.period_ns:
        raise ValueError("clock slew must be shorter than 0.3 * period")

    out = args.out.resolve()
    # Never overwrite an earlier characterized result on a failed sweep.
    out.mkdir(parents=True, exist_ok=False)
    model = sm.REPO_ROOT / f"tech/models/hspice/7nm_{args.corner}.pm"
    source_paths = [
        netlist,
        lef,
        manifests[0],
        model,
        Path(sm.__file__),
        Path(__file__),
    ]
    provenance = {str(path.resolve()): digest(path) for path in source_paths}
    xyce = sm.find_xyce()
    version = subprocess.run(
        [str(xyce), "-v"], capture_output=True, text=True, check=True
    )
    records = []

    def point(key: tuple[int, int]) -> tuple[tuple[int, int], dict, dict]:
        i, j = key
        point_out = out / f"slew_{i}_load_{j}"
        print(
            f"start {point_out.name}: slew={args.slews[i]:g} ns, load={args.loads[j]:g} pF",
            flush=True,
        )
        verdict = sm.simulate(
            result,
            point_out,
            period=args.period_ns * 1e-9,
            vdd=args.vdd,
            corner=args.corner,
            load=args.loads[j] * 1e-12,
            clock_slew=args.slews[i] * 1e-9,
            temperature=args.temperature,
            timeout=args.timeout,
            sample_interval=min(
                0.5e-12, args.slews[i] * 1e-9 / 20, args.period_ns * 1e-9 / 400
            ),
        )
        summary = summarize_point(verdict)
        print(f"pass {point_out.name}: {verdict['xyce_seconds']:g} s", flush=True)
        record = {
            "slew_ns": args.slews[i],
            "load_pf": args.loads[j],
            "simulation": str(point_out / "simulation.json"),
            "timing_ns": summary,
        }
        return key, summary, record

    keys = [(i, j) for i in range(len(args.slews)) for j in range(len(args.loads))]
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        points = {}
        for key, summary, record in pool.map(point, keys):
            points[key] = summary
            records.append(record)
    if any(digest(path) != provenance[str(path.resolve())] for path in source_paths):
        raise ValueError("characterization sources changed during the sweep; rerun")
    data = characterization(
        cell,
        args.slews,
        args.loads,
        points,
        corner=args.corner,
        vdd=args.vdd,
        temperature=args.temperature,
        period_ns=args.period_ns,
    )
    data["provenance"] = {
        "sha256": provenance,
        "xyce": str(xyce),
        "xyce_version": (version.stdout + version.stderr).strip(),
        "points": records,
    }
    json_path = out / f"{cell}.timing.json"
    json_path.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")
    lib_path = out / f"{cell}.lib"
    command = [
        str(args.openfinram.resolve()),
        "--liberty-only",
        str(lef),
        "--liberty-output",
        str(lib_path),
        "--liberty-from",
        str(json_path),
        "--num-wls",
        str(physical["wordlines_per_half"]),
        "--num-data-bits",
        str(physical["bits"]),
        "--num-banks",
        str(physical["banks"]),
        "--num-rows-per-mux",
        str(physical.get("column_mux", 4)),
    ]
    with (out / "liberty.log").open("w") as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
    print(f"wrote {lib_path}", flush=True)
    return lib_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result_dir", type=Path)
    parser.add_argument(
        "--out", type=Path, required=True, help="New output directory (must not exist)."
    )
    parser.add_argument(
        "--openfinram", type=Path, default=sm.REPO_ROOT / "build/OpenFinRAM"
    )
    parser.add_argument(
        "--slews",
        type=axis,
        default=axis("0.01,0.02,0.04"),
        help="20-80%% clock slew, ns.",
    )
    parser.add_argument(
        "--loads",
        type=axis,
        default=axis("0.0005,0.001,0.002"),
        help="Output capacitance, pF.",
    )
    parser.add_argument("--period-ns", type=float, default=2.0)
    parser.add_argument("--vdd", type=float, default=0.7)
    parser.add_argument("--temperature", type=float, default=25.0)
    parser.add_argument("--corner", choices=("TT", "SS", "FF"), default="TT")
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument(
        "--timeout", type=float, default=7200, help="Seconds per simulation."
    )
    args = parser.parse_args()
    try:
        run(args)
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, f"characterize_8t: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
