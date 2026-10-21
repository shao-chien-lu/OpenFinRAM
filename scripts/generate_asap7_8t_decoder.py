#!/usr/bin/env python3
"""Synthesize and optionally route a parameterized 2RW SRAM row decoder.

The decoder serves one half-array. Its two independent row addresses are
already registered by the controller; EN_A/B are the delayed wordline phases.
Outputs follow the actual 8T slot/pin coordinates, including taps and mirrors.
The initial implementation uses ASAP7 standard cells with explicit final
enable gates and selectable drivers. It is a baseline for custom narrow
driver slices, not a transistor-level signoff result.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import re
import subprocess
import sys

import gdstk

from chipforge_asap7.verification.connectivity import MetalGraph, METALS
from chipforge_asap7.layout.transform import placed_def_instances
from chipforge_asap7.verification.sram import verify_decoder_physical
from generate_asap7_wordline_arrays import boundary_box, direct_label, slot_layout
from mapped_verilog_to_spice import convert

REPO = Path(__file__).resolve().parents[1]
TOP = "sram_decoder_2rw"


@dataclass(frozen=True)
class DecoderConfig:
    wordlines: int = 32
    tap_pitch: int = 0
    predecode_bits: int = 3
    drive: int = 4
    wl_load_ff: float = 4.0
    delay_ns: float = 0.25
    utilization: float = 0.60

    def __post_init__(self):
        if type(self.wordlines) is not int or self.wordlines < 1:
            raise ValueError("wordlines must be a positive integer")
        if (
            type(self.tap_pitch) is not int
            or self.tap_pitch < 0
            or (self.tap_pitch and self.wordlines % self.tap_pitch)
        ):
            raise ValueError(
                "tap pitch must be zero or a positive divisor of wordlines"
            )
        if self.predecode_bits not in (2, 3) or self.drive not in (2, 4, 8):
            raise ValueError("predecode bits must be 2 or 3; drive must be 2, 4, or 8")
        if not all(
            math.isfinite(v) and v > 0 for v in (self.wl_load_ff, self.delay_ns)
        ):
            raise ValueError(
                "wordline load and delay target must be finite and positive"
            )
        if not 0 < self.utilization <= 0.80:
            raise ValueError("utilization must be in (0, 0.80]")

    @property
    def address_bits(self):
        return max(1, (self.wordlines - 1).bit_length())


def predecode_groups(bits: int, max_bits: int) -> list[dict]:
    count = math.ceil(bits / max_bits)
    base, extra = divmod(bits, count)
    groups, lsb = [], 0
    for index in range(count):
        width = base + (index < extra)
        groups.append({"lsb": lsb, "bits": width, "terms": 1 << width})
        lsb += width
    return groups


def interface_plan(config: DecoderConfig, bitcell: gdstk.Cell, mirror_x=False) -> dict:
    x0, _, x1, _ = boundary_box(bitcell)
    pitch = x1 - x0
    slots, slot_of = slot_layout(config.wordlines, config.tap_pitch)
    outputs = []
    for port, pin in (("A", "WLA"), ("B", "WLB")):
        label = direct_label(bitcell, pin)
        for row, slot in enumerate(slot_of):
            local_x = x1 - label.origin[0] if row % 2 else label.origin[0] - x0
            x = slot * pitch + local_x
            if mirror_x:
                x = slots * pitch - x
            outputs.append(
                {
                    "name": f"{pin}[{row}]",
                    "port": port,
                    "row": row,
                    "slot": slots - 1 - slot if mirror_x else slot,
                    "layer": label.layer,
                    "x": round(float(x), 7),
                }
            )
    return {
        "config": asdict(config),
        "address_bits": config.address_bits,
        "predecode_groups": predecode_groups(
            config.address_bits, config.predecode_bits
        ),
        "array_width_um": round(slots * pitch, 7),
        "mirror_x": mirror_x,
        "slots": slots,
        "outputs": outputs,
        "timing_contract": "Address stable before EN rises and until EN falls; EN=0 forces all WL low.",
        "invalid_addresses": "All wordlines remain low for row addresses >= wordlines.",
        "physical_status": "not_routed",
    }


def quote(path: Path) -> str:
    value = str(path.resolve())
    if any(c in value for c in ('"', "\n", "\r", "\\")):
        raise ValueError("tool paths cannot contain quotes, backslashes, or newlines")
    return f'"{value}"'


def run_tool(argv: list[str], work: Path, log_name: str):
    with (work / log_name).open("w") as log:
        result = subprocess.run(
            argv, cwd=work, stdout=log, stderr=subprocess.STDOUT, timeout=600
        )
    if result.returncode:
        raise RuntimeError(f"{argv[0]} failed; see {work / log_name}")


def synthesize(config: DecoderConfig, work: Path, yosys: str) -> dict:
    libraries = sorted((REPO / "tech/lib").glob("*.lib"))
    lib_flags = " ".join(f"-liberty {quote(p)}" for p in libraries)
    (work / "abc.constr").write_text(
        f"set_driving_cell BUFx2_ASAP7_75t_R\nset_load {config.wl_load_ff}\n"
    )
    script = (
        "\n".join(
            [
                f"read_liberty -lib -ignore_miss_func -ignore_miss_data_latch {quote(p)}"
                for p in libraries
            ]
            + [
                f"read_verilog -sv {quote(REPO / 'tech/verilog_dp/row_decoder.v')}",
                f"chparam -set NUM_WL {config.wordlines} -set PREDECODE_BITS {config.predecode_bits} -set DRIVE {config.drive} {TOP}",
                f"hierarchy -check -top {TOP}",
                f"synth -top {TOP} -flatten",
                f"abc {lib_flags} -constr abc.constr -D {config.delay_ns * 1000}",
                "hilomap -singleton -hicell TIEHIx1_ASAP7_75t_R H -locell TIELOx1_ASAP7_75t_R L",
                "opt_clean",
                "delete t:$scopeinfo",
                "check -assert",
                f"select -assert-count {2 * config.wordlines} a:physical_wl_driver",
                f"select -assert-count {2 * config.wordlines} a:physical_wl_gate",
                f"select -assert-count {2 * sum(g['terms'] for g in predecode_groups(config.address_bits, config.predecode_bits))} a:physical_predecode",
                "write_json decoder.json",
                "write_verilog -noattr -noexpr decoder.v",
            ]
        )
        + "\n"
    )
    (work / "synth.ys").write_text(script)
    run_tool([yosys, "-Q", "-T", "-s", "synth.ys"], work, "synth.log")
    design = json.loads((work / "decoder.json").read_text())
    cdl = (REPO / "tech/cdl/asap7sc7p5t_28_R.cdl").read_text()
    spice = convert(design, cdl, TOP)
    (work / "decoder.sp").write_text(spice + "\n" + cdl)
    return design


def mapped_area(design: dict) -> float:
    sizes = master_sizes()
    return sum(
        math.prod(sizes[cell["type"]])
        for cell in design["modules"][TOP]["cells"].values()
    )


def master_sizes() -> dict:
    lef = (REPO / "tech/lef/asap7sc7p5t_28_R.lef").read_text()
    sizes = {}
    for match in re.finditer(r"MACRO\s+(\S+)(.*?)\nEND\s+\1\b", lef, re.S):
        size = re.search(r"SIZE\s+([\d.]+)\s+BY\s+([\d.]+)", match[2])
        if size:
            sizes[match[1]] = (float(size[1]), float(size[2]))
    return sizes


def physical_script(config: DecoderConfig, plan: dict, area: float) -> str:
    # Production widths follow the array exactly. Tiny characterization cases
    # need at least 1.296 um to fit standard cells, taps, and address pins.
    width = max(1.296, plan["array_width_um"])
    core_width = width - 0.108
    core_height = math.ceil(area / (config.utilization * core_width) / 0.270) * 0.270
    height = round(max(1.620, core_height + 0.540), 6)
    plan["size_um"] = [width, height]
    plan["mapped_cell_area_um2"] = round(area, 7)
    plan["wordline_load_ff"] = config.wl_load_ff
    lines = [f"read_liberty {{{p}}}" for p in sorted((REPO / "tech/lib").glob("*.lib"))]
    lines += [
        f"read_lef {{{REPO}/tech/lef/asap7_tech.lef}}",
        f"read_lef {{{REPO}/tech/lef/asap7sc7p5t_28_R.lef}}",
        "read_verilog decoder.v",
        f"link_design {TOP}",
        "set_thread_count 2",
        f"source {{{REPO}/tech/setRC.tcl}}",
        f"set_max_delay {config.delay_ns} -from [all_inputs] -to [all_outputs]",
        "set_input_transition 0.01 [all_inputs]",
        f"set_load {config.wl_load_ff / 1000} [all_outputs]",
        "set_max_transition 0.15 [current_design]",
        f"initialize_floorplan -die_area {{0 0 {width} {height}}} -core_area {{0.054 0.270 {width - 0.054:.6f} {height - 0.270:.6f}}} -site asap7sc7p5t",
    ]
    # M3 wordlines use the half-track phase of the bitcell. Port-B centers
    # change 24 nm phase after an odd number of tap slots, so candidate M5
    # tracks use the common 12 nm grid. Legal wire spacing comes from the LEF.
    for layer, pitch, offset in (
        (1, 0.036, 0),
        (2, 0.036, 0),
        (3, 0.036, 0.018),
        (4, 0.048, 0),
        (5, 0.012, 0),
        (6, 0.064, 0),
        (7, 0.064, 0),
    ):
        lines.append(
            f"make_tracks M{layer} -x_offset {offset} -y_offset 0 -x_pitch {pitch} -y_pitch {pitch}"
        )
    for output in plan["outputs"]:
        layer = "M3" if output["layer"] == 30 else "M5"
        pin_width = 0.018 if layer == "M3" else 0.024
        lines.append(
            f"place_pin -pin_name {{{output['name']}}} -layer {layer} -location {{{output['x']} {height}}} -pin_size {{{pin_width} 0.072}} -force_to_die_boundary"
        )
    input_names = [
        f"A_{port}[{bit}]" for port in "AB" for bit in range(config.address_bits)
    ] + ["EN_A", "EN_B"]
    for index, name in enumerate(input_names):
        x = round((index + 1) * width / (len(input_names) + 1) / 0.036) * 0.036 + 0.018
        lines.append(
            f"place_pin -pin_name {{{name}}} -layer M3 -location {{{x:.6f} 0}} -pin_size {{0.018 0.072}} -force_to_die_boundary"
        )
    lines += [
        "add_global_connection -net VDD -pin_pattern {^VDD$} -power",
        "add_global_connection -net VSS -pin_pattern {^VSS$} -ground",
        "global_connect",
        "tapcell -distance 14 -tapcell_master TAPCELL_ASAP7_75t_R",
        "set_dont_touch [get_cells -hierarchical -quiet {*u_enable *u_driver}]",
        "global_placement -density 0.75",
        "estimate_parasitics -placement",
        "repair_design -max_utilization 90 -slew_margin 10 -cap_margin 10",
        "detailed_placement",
        "check_placement -verbose",
        "filler_placement {FILLER_ASAP7_75t_R FILLERxp5_ASAP7_75t_R}",
        "global_connect",
        "set_voltage_domain -name CORE -power VDD -ground VSS",
        "define_pdn_grid -name decoder_grid -voltage_domains CORE -pins {M3}",
        "add_pdn_stripe -grid decoder_grid -layer M1 -width 0.018 -followpins",
        "add_pdn_stripe -grid decoder_grid -layer M2 -width 0.018 -followpins",
        "add_pdn_stripe -grid decoder_grid -layer M3 -width 0.018 -pitch 0.864 -offset 0.054",
        "add_pdn_connect -grid decoder_grid -layers {M1 M2}",
        "add_pdn_connect -grid decoder_grid -layers {M2 M3}",
        "pdngen -failed_via_report pdn_failed_vias.rpt",
        "set_routing_layers -signal M1-M7",
        "global_route",
        "detailed_route -droute_end_iter 30 -output_drc decoder_drc.rpt",
        "if {![file exists decoder_drc.rpt] || [file size decoder_drc.rpt] != 0} { error {decoder routing DRC failed} }",
        "estimate_parasitics -global_routing",
        "report_checks -path_delay max -fields {slew capacitance fanout} -digits 4 > timing.rpt",
        "report_checks -from [get_ports {EN_A EN_B}] -path_delay max -digits 4 > enable_timing.rpt",
        "report_check_types -max_slew -max_capacitance -violators > electrical.rpt",
        "if {[llength [find_timing_paths -path_delay max]] == 0} { error {decoder has no constrained timing paths} }",
        "set decoder_slack [worst_slack -max]",
        "set timing_fd [open timing_summary.json w]",
        'puts $timing_fd [format {{"worst_slack_ns": %.9g}} $decoder_slack]',
        "close $timing_fd",
        "if {$decoder_slack < 0} { error {decoder exceeds requested delay target} }",
        "set electrical_fd [open electrical.rpt r]",
        "set electrical_text [read $electrical_fd]",
        "close $electrical_fd",
        'if {[string first "(VIOLATED)" $electrical_text] >= 0} { error {decoder slew/capacitance violations remain} }',
        "write_def decoder.def",
        "write_verilog decoder_routed.v",
        "write_db decoder.odb",
        "exit",
    ]
    return "\n".join(lines) + "\n"


def routed_netlist(work: Path, yosys: str) -> dict:
    script = (
        "\n".join(
            f"read_liberty -lib -ignore_miss_func -ignore_miss_data_latch {quote(p)}"
            for p in sorted((REPO / "tech/lib").glob("*.lib"))
        )
        + "\nread_verilog decoder_routed.v\nwrite_json decoder_routed.json\n"
    )
    (work / "parse_routed.ys").write_text(script)
    run_tool([yosys, "-Q", "-T", "-s", "parse_routed.ys"], work, "parse_routed.log")
    return json.loads((work / "decoder_routed.json").read_text())


def placed_instances(def_path: Path, cells: dict) -> dict:
    return placed_def_instances(def_path, cells, master_sizes())


def verify_physical(path: Path, plan: dict, design: dict | None = None) -> dict:
    return verify_decoder_physical(
        path, plan, design=design, master_sizes=master_sizes, top_name=TOP
    )



def finalize_gds(path: Path, plan: dict):
    """Publish the declared boundary and pin conductors with the streamed layout."""
    lib = gdstk.read_gds(str(path))
    top = next(c for c in lib.cells if c.name == TOP)
    top.add(gdstk.rectangle((0, 0), plan["size_um"], layer=100))
    for poly in list(top.polygons):
        if poly.datatype == 251 and poly.layer in METALS:
            conductor = poly.copy()
            conductor.datatype = 0
            top.add(conductor)
    lib.write_gds(str(path))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--word-lines", type=int, default=32)
    parser.add_argument("--tap-pitch", type=int, default=0)
    parser.add_argument("--predecode-bits", type=int, default=3)
    parser.add_argument("--drive", type=int, default=4)
    parser.add_argument("--wl-load-ff", type=float, default=4.0)
    parser.add_argument("--delay-ns", type=float, default=0.25)
    parser.add_argument("--utilization", type=float, default=0.60)
    parser.add_argument("--mirror-x", action="store_true")
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument(
        "--route", action="store_true", help="run OpenROAD and stream/verify GDS"
    )
    parser.add_argument("--yosys", default="yosys")
    parser.add_argument("--openroad", default="openroad")
    args = parser.parse_args(argv)
    try:
        config = DecoderConfig(
            args.word_lines,
            args.tap_pitch,
            args.predecode_bits,
            args.drive,
            args.wl_load_ff,
            args.delay_ns,
            args.utilization,
        )
        work = args.work.resolve()
        work.mkdir(parents=True, exist_ok=True)
        bitcell = gdstk.read_gds(str(REPO / "tech/gds/sram_cell_8t.gds")).cells[0]
        plan = interface_plan(config, bitcell, args.mirror_x)
        design = synthesize(config, work, args.yosys)
        (work / "route.tcl").write_text(
            physical_script(config, plan, mapped_area(design))
        )
        (work / "decoder.plan.json").write_text(json.dumps(plan, indent=2) + "\n")
        if args.route:
            run_tool([args.openroad, "-exit", "route.tcl"], work, "route.log")
            run_tool(
                [
                    "python3",
                    str(REPO / "scripts/def_to_gds.py"),
                    "--def-file",
                    str(work / "decoder.def"),
                    "--tech-lef",
                    str(REPO / "tech/lef/asap7_tech.lef"),
                    "--cell-lef",
                    str(REPO / "tech/lef/asap7sc7p5t_28_R.lef"),
                    "--macro-gds",
                    str(REPO / "tech/gds/asap7sc7p5t_28_R_220121a.gds"),
                    "--top-cell",
                    TOP,
                    "--output-gds",
                    str(work / "decoder.gds"),
                ],
                work,
                "stream.log",
            )
            finalize_gds(work / "decoder.gds", plan)
            routed = routed_netlist(work, args.yosys)
            plan["pin_verification"] = verify_physical(
                work / "decoder.gds", plan, routed
            )
            cdl = (REPO / "tech/cdl/asap7sc7p5t_28_R.cdl").read_text()
            (work / "decoder_routed.sp").write_text(
                convert(routed, cdl, TOP) + "\n" + cdl
            )
            plan["estimated_timing"] = {
                **json.loads((work / "timing_summary.json").read_text()),
                "model": "Liberty with global-route RC estimates, not extracted parasitics",
                "delay_target_ns": config.delay_ns,
                "electrical_violations": 0,
            }
            plan["physical_status"] = "routed_connectivity_checked"
            (work / "decoder.plan.json").write_text(json.dumps(plan, indent=2) + "\n")
        print(
            f"PASS: {config.wordlines}-row 2RW decoder, groups="
            f"{[g['bits'] for g in plan['predecode_groups']]}, "
            f"size={plan['size_um']}, status={plan['physical_status']}; {work}"
        )
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
