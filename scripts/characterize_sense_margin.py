#!/usr/bin/env python3
"""What sharing a port's IO between two facing banks costs the read: a two-sided block against a one-sided one.

    .venv/bin/python scripts/characterize_sense_margin.py [--cells 16,64] [--deltas 5,10,20,30,50] [--out DIR]

chipforge_asap7's `IoColumnSpec(two_sided=True)` gives two arrays that face
each other (two banks of one port) one sense amplifier, write driver and
output latch.  The amplifier's sense lines then also carry the far group's
unselected leaves and the wire across the logic column.  This asks Xyce what
that does to a read, before any macro is built on it.

The block is port B's as the compiler builds it (`io_block_specs`, from the
bitcell), one-sided or two-sided, and nothing else differs.  On leaf 0's
bitlines hang N real `sram_cell_8t` (with M4 bitline wire per cell pitch);
cell 0 is read through port B and every other cell stores the opposite value,
the worst case for leakage onto the other bitline.  In the two-sided block the
far group is deselected and precharged, with its own column of cells, and each
sense line carries the extra wire across the core.  Precharge releases, the
wordline rises, and sense enable (one phase: low precharges the amplifier,
high evaluates) rises DELTA later.  Both stored values are read.

Measured when sense enable rises: the bitline split and the amplifier-node
split (``SA`` - ``SAN``); then how long the amplifier's outputs take to be
80 % apart, and whether ``Q`` came out right for both values.

Nominal devices at TT, 0.7 V: no mismatch, so a correct read at a few
millivolts shows function, not margin; the amplifier's offset sets the real
minimum split.  Wires are lumped capacitance (0.178 fF/um, `tech/setRC.tcl`).
"""

from __future__ import annotations

import argparse
import itertools
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import gdstk

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import generate_asap7_8t_iocolumn as columns  # noqa: E402
import simulate_macro as sm  # noqa: E402
from chipforge_asap7.devices.io_column import block_netlist, io_column_pins  # noqa: E402

VDD = 0.7
T_PRECHARGE_OFF, T_WORDLINE, EDGE = 50e-12, 70e-12, 5e-12
WIRE_FF_PER_UM = 0.178  # tech/setRC.tcl, M4
CORE_WIRE_FF = 0.9 * WIRE_FF_PER_UM  # each sense line's extra run across the logic column
BITLINE_FF_PER_CELL = 0.108 * WIRE_FF_PER_UM
CELL = """.SUBCKT sram_cell_8t WLA WLB BLA BLAN BLB BLBN VDD VSS
M0 Q  QB VSS VSS nmos_sram L=2e-08 nfin=2
M1 QB Q  VSS VSS nmos_sram L=2e-08 nfin=2
M2 Q  QB VDD VDD pmos_sram L=2e-08 nfin=1
M3 QB Q  VDD VDD pmos_sram L=2e-08 nfin=1
M4 Q  WLA BLA  VSS nmos_sram L=2e-08 nfin=2
M5 QB WLA BLAN VSS nmos_sram L=2e-08 nfin=2
M6 Q  WLB BLB  VSS nmos_sram L=2e-08 nfin=2
M7 QB WLB BLBN VSS nmos_sram L=2e-08 nfin=2
.ENDS
"""


def port_b_spec(two_sided: bool):
    """Port B's block as the compiler builds it from the bitcell, one- or two-sided."""
    bitcell = next(c for c in gdstk.read_gds(str(REPO / "tech/gds/sram_cell_8t.gds")).cells if c.name == "sram_cell_8t")
    return replace(columns.io_block_specs(bitcell)["B"], two_sided=two_sided)


def block_text(spec) -> str:
    text = sm.for_xyce(block_netlist(spec, name="ioblk")).replace(".END\n", "")
    if spec.two_sided:
        extra = f"Cwsa SA VSS {CORE_WIRE_FF:.4f}f\nCwsan SAN VSS {CORE_WIRE_FF:.4f}f\n"
        text = text.replace(".ENDS ioblk", extra + ".ENDS ioblk")
    return text


def net(pin: str) -> str:
    return pin.replace("[", "_").replace("]", "")


def build_deck(spec, cells: int, delta: float, stored: int, model: Path) -> str:
    """The read of a `stored` bit from cell 0 of `cells`, sense enable `delta` after the wordline."""
    pins = io_column_pins(spec)
    level = dict.fromkeys(pins, 0)
    # Leaf 0 selected; the other leaves, and the far group, deselected; the far
    # group precharged (PRECHN_R low); output enabled; not writing.
    level.update({p: 1 for p in pins if p.startswith(("YSELN[", "YSELN_R["))})
    level.update({"YSEL[0]": 1, "YSELN[0]": 0, "OE": 1, "WRENAN": 1})
    driven = [p for p in pins if p not in ("VDD", "VSS", "PRECHN", "SAE", "Q") and not p.startswith("BL")]
    lines = [f"* sense margin: two_sided={spec.two_sided} cells={cells} delta={delta:g} stored={stored}",
             f".include {model}", block_text(spec), CELL, f"Vdd VDD 0 {VDD}", "Vss VSS 0 0"]  # fmt: skip
    lines += [f"V{net(p)} {net(p)} 0 {VDD * level[p]}" for p in driven]
    lines += [
        f"VPRECHN PRECHN 0 PWL(0 0 {T_PRECHARGE_OFF} 0 {T_PRECHARGE_OFF + EDGE} {VDD})",
        f"VWLB wlb0 0 PWL(0 0 {T_WORDLINE} 0 {T_WORDLINE + EDGE} {VDD})",
        f"VSAE SAE 0 PWL(0 0 {T_WORDLINE + delta} 0 {T_WORDLINE + delta + EDGE} {VDD})",
        "Xio " + " ".join(net(p) for p in pins) + " ioblk",
        "Cq Q 0 1f",
    ]
    ics = [f"V({net(p)})={VDD}" for p in pins if p.startswith("BL")]  # every bitline precharged
    for side in ("", "_R") if spec.two_sided else ("",):
        bl, bln = f"BL{side}_0", f"BLN{side}_0"
        for k in range(cells):
            read = k == 0 and side == ""
            lines.append(f"Xc{side}{k} 0 {'wlb0' if read else '0'} na{side}{k} nan{side}{k} {bl} {bln} VDD VSS sram_cell_8t")
            q = VDD * stored if read else VDD * (1 - stored)
            ics.append(f"V(Xc{side}{k}:Q)={q} V(Xc{side}{k}:QB)={VDD - q}")
        load = cells * BITLINE_FF_PER_CELL
        lines += [f"Cbl{side} {bl} 0 {load:.4f}f", f"Cbln{side} {bln} 0 {load:.4f}f"]
    lines += [".IC " + " ".join(ics), f".tran 0.2p {T_WORDLINE + delta + 150e-12} NOOP",
              ".print tran format=csv v(BL_0) v(BLN_0) v(Xio:SA) v(Xio:SAN) v(Xio:QA) v(Xio:QAN) v(Q)", ".end"]  # fmt: skip
    return "\n".join(lines) + "\n"


def run_case(out: Path, model: Path, two_sided: bool, cells: int, delta: float, stored: int) -> dict:
    tag = f"{'two' if two_sided else 'one'}_n{cells}_d{round(delta * 1e12)}_s{stored}"
    work = out / tag
    work.mkdir(parents=True, exist_ok=True)
    (work / "tb.cir").write_text(build_deck(port_b_spec(two_sided), cells, delta, stored, model))
    with (work / "xyce.log").open("w") as log:
        subprocess.run([str(sm.find_xyce()), "tb.cir"], cwd=work, stdout=log, stderr=subprocess.STDOUT,
                       timeout=900, check=True)  # fmt: skip
    waves = sm.read_csv(work / "tb.cir.csv")
    time_axis = waves["TIME"]
    sense = T_WORDLINE + delta

    def at(name: str) -> float:
        return sm.at(time_axis, waves[name], sense)

    qa, qan = waves["V(XIO:QA)"], waves["V(XIO:QAN)"]
    resolved = next((t - sense for t, a, b in zip(time_axis, qa, qan) if t >= sense and abs(a - b) > 0.8 * VDD), None)
    q_end = waves["V(Q)"][-1]
    return {
        "two_sided": two_sided, "cells": cells, "delta_ps": round(delta * 1e12), "stored": stored,
        "bitline_split_mv": round(abs(at("V(BLN_0)") - at("V(BL_0)")) * 1000, 1),
        "sense_split_mv": round(abs(at("V(XIO:SAN)") - at("V(XIO:SA)")) * 1000, 1),
        "resolve_ps": None if resolved is None else round(resolved * 1e12, 1),
        "read_ok": abs(q_end - VDD * stored) < 0.1 * VDD,
    }  # fmt: skip


def characterize(out: Path, cells: list[int], deltas: list[float], jobs: int = 8) -> list[dict]:
    out.mkdir(parents=True, exist_ok=True)
    model = out / "asap7_TT.xyce.pm"
    model.write_text(re.sub(r"level\s*=\s*72", "level = 107", (REPO / "tech/models/hspice/7nm_TT.pm").read_text()))
    cases = list(itertools.product((False, True), cells, deltas, (0, 1)))
    with ThreadPoolExecutor(jobs) as pool:
        return list(pool.map(lambda case: run_case(out, model, *case), cases))


def table(results: list[dict]) -> str:
    """One row per (cells, delta): the split at sense time, one-sided against two-sided."""
    rows = ["cells  delta   bitline mV (1s / 2s)   sense mV (1s / 2s)   loss   resolve ps (1s / 2s)   reads"]
    key = lambda r: (r["cells"], r["delta_ps"])  # noqa: E731
    for (cells, delta), group in itertools.groupby(sorted(results, key=key), key=key):
        group = list(group)
        one = next(r for r in group if not r["two_sided"] and r["stored"] == 0)
        two = next(r for r in group if r["two_sided"] and r["stored"] == 0)
        loss = 100 * (1 - two["sense_split_mv"] / one["sense_split_mv"]) if one["sense_split_mv"] else 0
        ok = "all right" if all(r["read_ok"] for r in group) else "WRONG"
        rows.append(f"{cells:5d} {delta:5d}   {one['bitline_split_mv']:7.1f} / {two['bitline_split_mv']:7.1f}"
                    f"     {one['sense_split_mv']:7.1f} / {two['sense_split_mv']:7.1f}   {loss:4.0f}%"
                    f"   {one['resolve_ps']} / {two['resolve_ps']}          {ok}")  # fmt: skip
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cells", default="16,64", help="cells on the bitline, comma separated")
    parser.add_argument("--deltas", default="5,10,20,30,50", help="ps from wordline to sense enable")
    parser.add_argument("--out", type=Path, default=REPO / "tmp/sense_margin")
    parser.add_argument("--jobs", type=int, default=8)
    args = parser.parse_args(argv)
    results = characterize(args.out, [int(c) for c in args.cells.split(",")],
                           [float(d) * 1e-12 for d in args.deltas.split(",")], args.jobs)  # fmt: skip
    (args.out / "results.json").write_text(json.dumps(results, indent=1) + "\n")
    print(table(results))
    return 0 if all(r["read_ok"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
