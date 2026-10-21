#!/usr/bin/env python3
"""What should drive a wordline: the compiler's standard cells, or a sized slice?

    .venv/bin/python scripts/characterize_wl_driver.py [--cells 16,32,64,128,256]

Today a wordline is ``AND2x2`` + ``BUFx4`` inside the synthesized controller
(`tech/verilog_dp/row_decoder.v`).  chipforge_asap7 can draw the released
arrangement instead -- a post-decode NAND and a wide inverter per wordline, on
the wordline pitch (`DriverSliceSpec`) -- and size it for a load by logical
effort (`size_decoder`).  The macros built so far have four cells on a
wordline, so nothing has ever asked either of them to drive anything.  This
does, in Xyce, before any layout is touched.

The wordline is what the 8T array draws: 18 nm M3 over 0.594 um of cell, and on
it the two port-A access gates of a real `sram_cell_8t` (preloaded, bitlines
held at VDD, the other port off), as an RC ladder of that many cells driven
from one end.  For each length:

  ideal        a voltage source: the wordline's capacitance, and the delay the
               wire alone imposes, which no driver can beat
  BUFx4/8/24   ``AND2x2`` + that buffer: the compiler today, and how far
               upsizing the library cell gets
  slice        `DriverSliceSpec.from_sizing(size_decoder(C))` for the measured C
  slice/lumped the same slice into a capacitor of that C, no wire: what
               `size_decoder` predicts, checked apart from the wire

Measured from the input's 50 % to the far end's 50 %, the far end's 10-90 %,
the energy the driver's own supply delivers per pulse, and the capacitance the
driver presents to whatever feeds it.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from simulate_macro import at, crossings, find_xyce, for_xyce, read_csv  # noqa: E402

VDD = 0.7
CELL_PITCH_UM = 0.594  # sram_cell_8t along its wordline
#: 18 nm M3, two ways.  chipforge_asap7's stack has 3.03 ohm/sq, its M1 value
#: calibrated on the released xACT extraction and carried to M3: 168 ohm/um.
#: tech/setRC.tcl, which the compiler's STA uses, has 31.3 ohm/um, which is
#: bulk copper at that cross-section.  They differ by 5.4, and it decides how
#: much a driver can matter, so the study runs under either.
WIRE_OHM_PER_UM = {"xact": 3.03147 / 0.018, "setrc": 31.287}
M3_FF_PER_UM = 0.155554  # tech/setRC.tcl (ORFS ASAP7), as scripts/extract_parasitics.py uses
STD_CELLS = REPO_ROOT / "tech/cdl/asap7sc7p5t_28_R.cdl"
EDGE = 20e-12
START = 100e-12

BITCELL = """\
.SUBCKT sram_cell_8t WLA WLB BLA BLAN BLB BLBN VDD VSS
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


def subckt(library: str, name: str) -> str:
    match = re.search(rf"(?ims)^\.SUBCKT\s+{re.escape(name)}\s.*?^\.ENDS[^\n]*\n", library)
    if not match:
        raise KeyError(f"{name} is not in {STD_CELLS}")
    return match[0]


def ladder(cells: int, ohm_per_um: float) -> list[str]:
    """`cells` segments of wire, each with a preloaded bitcell's port-A access gates on it."""
    r, c = ohm_per_um * CELL_PITCH_UM, M3_FF_PER_UM * CELL_PITCH_UM * 1e-15
    lines = []
    for k in range(1, cells + 1):
        lines += [f"Rw{k} w{k - 1} w{k} {r:.4f}", f"Cw{k} w{k} 0 {c:.5g}",
                  f"Xc{k} w{k} 0 varr varr varr varr varr 0 sram_cell_8t",
                  f".IC V(Xc{k}:Q)={VDD * (k & 1):g} V(Xc{k}:QB)={VDD * (1 - (k & 1)):g}"]  # fmt: skip
    return lines


def deck(kind: str, cells: int, model: Path, *, width: float, ohm_per_um: float, slice_netlist: str = "",
         slice_name: str = "", lumped_fF: float = 0.0) -> str:  # fmt: skip
    lines = [f"* wordline driver study: {kind}, {cells} cells", f".include {model}", BITCELL,
             f"Vdut vdut 0 {VDD}", f"Varr varr 0 {VDD}",
             f"Vin in 0 PWL(0 0 {START:.4g} 0 {START + EDGE:.4g} {VDD} {START + width:.4g} {VDD} "
             f"{START + width + EDGE:.4g} 0)"]  # fmt: skip
    if kind == "ideal":
        lines.append("Rsrc in w0 1")
    elif kind.startswith("BUF"):
        library = STD_CELLS.read_text()
        lines += [for_xyce(subckt(library, "AND2x2_ASAP7_75t_R")), for_xyce(subckt(library, f"{kind}_ASAP7_75t_R")),
                  "Xand in vdut vdut 0 en AND2x2_ASAP7_75t_R", f"Xbuf en vdut 0 w0 {kind}_ASAP7_75t_R"]  # fmt: skip
    else:
        lines += [slice_netlist, f"Xslice in vdut 0 0 0 w0 u1 u2 u3 vdut 0 {slice_name}"]
    if lumped_fF:
        lines += [f"Cload w0 0 {lumped_fF * 1e-15:.5g}", "Rfar w0 wfar 1m"]
    else:
        lines += [*ladder(cells, ohm_per_um), f"Rfar w{cells} wfar 1m"]
    stop = START + 2 * width + 4 * EDGE
    lines += [f".tran {stop / 4000:.4g} {stop:.5g}", ".options timeint reltol=1e-3",
              f".options output initial_interval={stop / 2000:.4g}",
              ".print tran format=csv V(in) V(w0) V(wfar) I(Vdut) I(Vin)", ".end"]  # fmt: skip
    return "\n".join(lines) + "\n"


def measure(csv_path: Path, width: float) -> dict:
    waves = read_csv(csv_path)
    t = waves["TIME"]
    mid_rise, mid_fall = START + EDGE / 2, START + width + EDGE / 2
    found = {}
    for edge, start, stop in (("rise", START, START + width), ("fall", START + width, t[-1])):
        ref = mid_rise if edge == "rise" else mid_fall
        for node in ("W0", "WFAR"):
            half = crossings(t, waves[f"V({node})"], VDD / 2, start, stop)
            lo = crossings(t, waves[f"V({node})"], 0.1 * VDD, start, stop)
            hi = crossings(t, waves[f"V({node})"], 0.9 * VDD, start, stop)
            key = "near" if node == "W0" else "far"
            found[f"{key}_{edge}_delay_ps"] = round((half[0] - ref) * 1e12, 2) if half else None
            found[f"{key}_{edge}_slew_ps"] = round(abs(hi[0] - lo[0]) * 1e12, 2) if lo and hi else None
    found["far_high_V"] = round(at(t, waves["V(WFAR)"], START + width), 4)

    def charge(signal: str, start: float, stop: float) -> float:
        i = waves[signal]
        return sum((t[k] - t[k - 1]) * -(i[k] + i[k - 1]) / 2 for k in range(1, len(t)) if start <= t[k] <= stop)

    found["energy_fJ"] = round(charge("I(VDUT)", 0, t[-1]) * VDD * 1e15, 3)
    found["input_fF"] = round(charge("I(VIN)", START - EDGE, START + width) / VDD * 1e15, 4)
    return found


def run(tag: str, text: str, out: Path, width: float) -> dict:
    folder = out / tag
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "deck.cir").write_text(text)
    started = time.time()
    with (folder / "xyce.log").open("w") as log:
        done = subprocess.run([str(find_xyce()), "deck.cir"], cwd=folder, stdout=log, stderr=subprocess.STDOUT,
                              check=False, timeout=7200)  # fmt: skip
    csv_path = folder / "deck.cir.csv"
    if done.returncode != 0 or not csv_path.is_file():
        raise RuntimeError(f"Xyce failed for {tag}; see {folder / 'xyce.log'}")
    return {**measure(csv_path, width), "xyce_seconds": round(time.time() - started, 1)}


def study(cell_counts: list[int], out: Path, *, buffers: tuple[str, ...] = ("BUFx4", "BUFx8", "BUFx24"),
          corner: str = "TT", jobs: int = 8, wire: str = "xact") -> dict:  # fmt: skip
    from chipforge_asap7.devices import DriverSliceSpec, InverterSpec, NandSpec, size_decoder
    from chipforge_asap7.devices.finfet import MAX_FINS

    out = out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    model = out / f"asap7_{corner}.xyce.pm"
    card = (REPO_ROOT / f"tech/models/hspice/7nm_{corner}.pm").read_text()
    model.write_text(re.sub(r"level\s*=\s*72", "level = 107", card))
    ohm_per_um = WIRE_OHM_PER_UM[wire]
    r_cell, c_wire = ohm_per_um * CELL_PITCH_UM, M3_FF_PER_UM * CELL_PITCH_UM
    # Generous: three wire time constants, with a guess of 0.26 fF a cell until it is measured.
    widths = {n: max(400e-12, 3 * (r_cell * n) * (0.26e-15 * n)) for n in cell_counts}

    results: dict = {"corner": corner, "vdd": VDD, "wire": wire, "wire_ohm_per_cell": round(r_cell, 2),
                     "wire_fF_per_cell": round(c_wire, 4), "lengths": {}}  # fmt: skip
    with ThreadPoolExecutor(jobs) as pool:
        ideal = {n: pool.submit(run, f"n{n}_ideal", deck("ideal", n, model, width=widths[n], ohm_per_um=ohm_per_um), out, widths[n])
                 for n in cell_counts}  # fmt: skip
        ideal = {n: future.result() for n, future in ideal.items()}
        pending = {}
        for n in cell_counts:
            load = ideal[n]["input_fF"]
            entry = {"cells": n, "wordline_fF": load, "wordline_ohm": round(r_cell * n, 1), "ideal": ideal[n]}
            results["lengths"][n] = entry
            for buffer in buffers:
                pending[(n, buffer)] = pool.submit(run, f"n{n}_{buffer}", deck(buffer, n, model, width=widths[n], ohm_per_um=ohm_per_um),
                                                   out, widths[n])  # fmt: skip
            sizing = size_decoder(load, 32)
            stages = {name: sizing.stages[name] for name in ("post_nand", "driver")}
            entry["sizing"] = {name: {"drive": s.drive, "n_fins": s.n_fins, "p_fins": s.p_fins,
                                      "c_in_fF": round(s.c_in_fF, 3), "predicted_ps": round(s.delay_ps, 2)}
                               for name, s in stages.items()}  # fmt: skip
            entry["predicted_ps"] = round(sum(s.delay_ps for s in stages.values()), 2)
            try:
                spec = DriverSliceSpec.from_sizing(sizing)
            except ValueError as error:
                # `NandSpec` is one row: past 18 fins a device it cannot be drawn as sized.
                # Take the largest that can, and let the NAND work harder than the rest.
                entry["nand_capped"] = f"{error} -- simulated with the largest drawable NAND instead"
                spec = DriverSliceSpec(nand=NandSpec(rows=((MAX_FINS, MAX_FINS // 2),), fingers=2),
                                       inverter=InverterSpec(rows=sizing.driver_rows, fingers=2))  # fmt: skip
            entry["slice_cell"] = spec.cell_name
            netlist = for_xyce(spec.netlist().replace(".END\n", ""))
            for kind, lumped in (("slice", 0.0), ("slice_lumped", load)):
                text = deck("slice", n, model, width=widths[n], ohm_per_um=ohm_per_um, slice_netlist=netlist,
                            slice_name=spec.cell_name, lumped_fF=lumped)  # fmt: skip
                pending[(n, kind)] = pool.submit(run, f"n{n}_{kind}", text, out, widths[n])
        for (n, kind), future in pending.items():
            results["lengths"][n][kind] = future.result()
    (out / "wl_driver_study.json").write_text(json.dumps(results, indent=1) + "\n")
    return results


def describe(results: dict) -> str:
    lines = [f"wire ({results['wire']}): {results['wire_ohm_per_cell']} ohm and {results['wire_fF_per_cell']} fF per cell "
             f"(8T, 0.594 um of 18 nm M3); {results['corner']}, {results['vdd']} V"]  # fmt: skip
    for n, entry in results["lengths"].items():
        lines.append(f"\n{n} cells: {entry['wordline_fF']:.2f} fF, {entry['wordline_ohm']:.0f} ohm"
                     f"  ({entry['wordline_fF'] / int(n):.3f} fF a cell)")  # fmt: skip
        if "sizing" in entry:
            d, g = entry["sizing"]["driver"], entry["sizing"]["post_nand"]
            lines.append(f"  size_decoder: driver {d['n_fins']}n/{d['p_fins']}p, NAND {g['n_fins']}n/{g['p_fins']}p, "
                         f"predicts {entry['predicted_ps']} ps into a lumped load")  # fmt: skip
        if "nand_capped" in entry:
            lines.append(f"  NAND capped at the 18-fin band: {entry['slice_cell']}")
        lines.append(f"  {'':13s} {'near':>7s} {'far':>8s} {'far slew':>9s} {'far fall':>9s} {'energy':>8s} {'input':>7s}")
        for kind in ("ideal", "BUFx4", "BUFx8", "BUFx24", "slice", "slice_lumped"):
            m = entry.get(kind)
            if not m:
                continue
            lines.append(f"  {kind:13s} {m['near_rise_delay_ps']!s:>7s} {m['far_rise_delay_ps']!s:>8s} "
                         f"{m['far_rise_slew_ps']!s:>9s} {m['far_fall_delay_ps']!s:>9s} {m['energy_fJ']:>8.2f} "
                         f"{m['input_fF']:>7.3f}" + ("" if m["far_high_V"] > 0.95 * VDD else f"   far end only reached {m['far_high_V']} V"))  # fmt: skip
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--cells", default="16,32,64,128,256", help="Cells on the wordline, comma-separated.")
    parser.add_argument("--corner", choices=("TT", "SS", "FF"), default="TT")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "tmp/wl_driver_study")
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--wire", choices=tuple(WIRE_OHM_PER_UM), default="xact",
                        help="M3 resistance: chipforge's xACT-calibrated stack, or the STA table.")
    args = parser.parse_args(argv)
    results = study([int(n) for n in args.cells.split(",")], args.out / args.wire, corner=args.corner,
                    jobs=args.jobs, wire=args.wire)  # fmt: skip
    print(describe(results))
    print(f"\nwrote {args.out / args.wire / 'wl_driver_study.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
