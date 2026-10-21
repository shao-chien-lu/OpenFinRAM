"""Functional decode, synthesized logic, and physical pin-contract checks."""

import dataclasses
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import gdstk

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
from generate_asap7_8t_decoder import (  # noqa: E402
    DecoderConfig,
    interface_plan,
    predecode_groups,
    synthesize,
    verify_physical,
    TOP,
)
import generate_asap7_wordline_arrays as arrays  # noqa: E402


def bench(config, mapped=False):
    n, bits = config.wordlines, config.address_bits
    params = (
        ""
        if mapped
        else f"#(.NUM_WL({n}), .PREDECODE_BITS({config.predecode_bits}), .DRIVE({config.drive}))"
    )
    return f"""
module tb;
  reg [{bits - 1}:0] A_A=0, A_B=0;
  reg EN_A=0, EN_B=0;
  wire [{n - 1}:0] WLA, WLB;
  reg [{n - 1}:0] expected_A, expected_B;
  integer a, b, en, row;
  {TOP} {params} dut(.A_A(A_A),.A_B(A_B),.EN_A(EN_A),.EN_B(EN_B),.WLA(WLA),.WLB(WLB));
  task check;
    begin
      #1;
      for (row=0; row<{n}; row=row+1) begin
        expected_A[row] = EN_A && (A_A == row);
        expected_B[row] = EN_B && (A_B == row);
      end
      if (WLA !== expected_A || WLB !== expected_B)
        $fatal(1, "decode mismatch A=%0d B=%0d enables=%0d%0d", A_A,A_B,EN_A,EN_B);
    end
  endtask
  initial begin
    for (a=0; a<{1 << bits}; a=a+1) begin
      for (b=0; b<{(1 << bits) if n <= 18 else 3}; b=b+1) begin
        EN_A=0; EN_B=0;
        A_A=a;
        A_B={("b" if n <= 18 else "(b == 0 ? a : (b == 1 ? ~a : 3*a+1))")};
        check;
        for (en=0; en<4; en=en+1) begin
          EN_A=en & 1; EN_B=(en >> 1) & 1;
          check;
        end
        EN_A=0; EN_B=0; check;
      end
    end
    $display("PASS decoder N={n}");
    $finish;
  end
endmodule
"""


def simulate(config, work, sources, mapped=False):
    (work / "tb.sv").write_text(bench(config, mapped))
    result = subprocess.run(
        [
            "iverilog",
            "-g2012",
            "-s",
            "tb",
            "-o",
            str(work / "sim"),
            str(work / "tb.sv"),
            *map(str, sources),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise AssertionError(result.stderr)
    result = subprocess.run(
        ["vvp", str(work / "sim")], capture_output=True, text=True, timeout=60
    )
    if result.returncode or "PASS decoder" not in result.stdout:
        raise AssertionError(result.stdout + result.stderr)


class DecoderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bitcell = gdstk.read_gds(str(REPO / "tech/gds/sram_cell_8t.gds")).cells[0]
        cls.tap = next(
            c
            for c in gdstk.read_gds(str(REPO / "tech/gds/sram_cell_8t_tap.gds")).cells
            if c.name == "tapcell_sram_8t"
        )

    def test_balanced_predecode_covers_each_address_bit_once(self):
        for bits in range(1, 17):
            for maximum in (2, 3):
                groups = predecode_groups(bits, maximum)
                covered = [
                    b for g in groups for b in range(g["lsb"], g["lsb"] + g["bits"])
                ]
                self.assertEqual(covered, list(range(bits)))
                self.assertLessEqual(max(g["bits"] for g in groups), maximum)
                self.assertLessEqual(
                    max(g["bits"] for g in groups) - min(g["bits"] for g in groups), 1
                )
        self.assertEqual([g["bits"] for g in predecode_groups(7, 3)], [3, 2, 2])

    def test_invalid_config_rejected(self):
        for fields in (
            {"wordlines": 0},
            {"wordlines": 1.5},
            {"tap_pitch": -1},
            {"tap_pitch": 3},
            {"drive": 3},
            {"predecode_bits": 4},
            {"wl_load_ff": 0},
            {"wl_load_ff": float("nan")},
            {"delay_ns": float("inf")},
            {"utilization": 0.99},
        ):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                dataclasses.replace(DecoderConfig(), **fields)

    def test_pin_verifier_rejects_shifted_shorted_and_stub_wordlines(self):
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "decoder.gds"
            lib = gdstk.Library()
            cell = lib.new_cell(TOP)
            plan = {"outputs": []}
            for index, x in enumerate((0.05, 0.25)):
                name = f"WLA[{index}]"
                for layer in (19, 20, 30, 21, 25):
                    cell.add(
                        gdstk.rectangle((x - 0.02, 0), (x + 0.02, 0.1), layer=layer)
                    )
                cell.add(gdstk.Label(name, (x, 0.05), layer=30))
                plan["outputs"].append({"name": name, "x": x, "layer": 30})
            lib.write_gds(str(path))
            self.assertEqual(verify_physical(path, plan)["wordline_pins"], 2)
            cell.labels[0].origin = (0.062, 0.05)
            lib.write_gds(str(path))
            with self.assertRaisesRegex(RuntimeError, "not aligned"):
                verify_physical(path, plan)
            cell.labels[0].origin = (0.05, 0.05)
            bridge = gdstk.rectangle((0.06, 0), (0.24, 0.1), layer=30)
            cell.add(bridge)
            lib.write_gds(str(path))
            with self.assertRaisesRegex(RuntimeError, "shorted"):
                verify_physical(path, plan)
            cell.remove(bridge)
            cell.remove(*(p for p in cell.polygons if p.layer == 25))
            lib.write_gds(str(path))
            with self.assertRaisesRegex(RuntimeError, "isolated output stub"):
                verify_physical(path, plan)

    def test_output_pins_match_real_rows_including_taps_and_mirrors(self):
        for count, taps in ((1, 0), (3, 0), (18, 6), (32, 16), (64, 16), (128, 16)):
            config = DecoderConfig(count, taps)
            lib = gdstk.Library()
            row = arrays.build_row(
                lib, self.bitcell, arrays.CONTRACTS[0], count, self.tap, taps
            )
            for mx in (False, True):
                plan = interface_plan(config, self.bitcell, mx)
                reference = gdstk.Reference(
                    row,
                    origin=(plan["array_width_um"] if mx else 0, 0),
                    rotation=math.pi if mx else 0,
                    x_reflection=mx,
                )
                labels = {
                    label.text: label
                    for label in reference.get_labels()
                    if "[" in label.text
                }
                self.assertEqual(len(plan["outputs"]), 2 * count)
                for pin in plan["outputs"]:
                    self.assertAlmostEqual(
                        pin["x"], labels[pin["name"]].origin[0], places=7
                    )
                    self.assertEqual(pin["layer"], labels[pin["name"]].layer)

    @unittest.skipUnless(
        shutil.which("iverilog") and shutil.which("vvp"), "Icarus unavailable"
    )
    def test_rtl_all_rows_unused_codes_and_independent_enables(self):
        for n, group, drive in (
            (1, 3, 4),
            (2, 2, 2),
            (3, 3, 8),
            (18, 3, 4),
            (32, 2, 4),
            (64, 3, 4),
            (128, 3, 4),
            (256, 3, 4),
        ):
            with (
                self.subTest(n=n, group=group, drive=drive),
                tempfile.TemporaryDirectory() as scratch,
            ):
                simulate(
                    DecoderConfig(n, 0, group, drive),
                    Path(scratch),
                    [
                        REPO / "tech/verilog_dp/row_decoder.v",
                        REPO / "tests/prim_models.v",
                    ],
                )

    @unittest.skipUnless(
        shutil.which("yosys") and shutil.which("iverilog") and shutil.which("vvp"),
        "synthesis/simulation tools unavailable",
    )
    def test_mapped_decoder_preserves_function_and_final_driver_stages(self):
        with tempfile.TemporaryDirectory() as scratch:
            work = Path(scratch)
            libs = sorted((REPO / "tech/lib").glob("*.lib"))
            model_script = (
                "\n".join(
                    f"read_liberty -ignore_miss_func -ignore_miss_data_latch {p}"
                    for p in libs
                )
                + f"\nwrite_verilog {work}/models.v\n"
            )
            (work / "models.ys").write_text(model_script)
            subprocess.run(
                ["yosys", "-Q", "-s", str(work / "models.ys")],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                check=True,
            )
            for n, drive in ((1, 4), (3, 2), (18, 4), (32, 8), (128, 4)):
                with self.subTest(n=n, drive=drive):
                    config = DecoderConfig(n, 0, drive=drive)
                    design = synthesize(config, work, "yosys")
                    cells = design["modules"][TOP]["cells"]
                    drivers = [
                        c
                        for c in cells.values()
                        if "physical_wl_driver" in c.get("attributes", {})
                    ]
                    self.assertEqual(len(drivers), 2 * n)
                    self.assertEqual(
                        {c["type"] for c in drivers}, {f"BUFx{drive}_ASAP7_75t_R"}
                    )
                    predecode = [
                        c
                        for c in cells.values()
                        if "physical_predecode" in c.get("attributes", {})
                    ]
                    self.assertEqual(
                        len(predecode),
                        2
                        * sum(
                            g["terms"]
                            for g in predecode_groups(
                                config.address_bits, config.predecode_bits
                            )
                        ),
                    )
                    self.assertNotIn(
                        ".INCLUDE", (work / "decoder.sp").read_text().upper()
                    )
                    simulate(
                        config,
                        work,
                        [work / "models.v", work / "decoder.v"],
                        mapped=True,
                    )


if __name__ == "__main__":
    unittest.main()
