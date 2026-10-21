#!/usr/bin/env python3
"""How long the 6T macro's sense enable must wait after the wordline, by bitline length.

    .venv/bin/python scripts/sense_timing_6t.py --cells 16,64,256 --out tmp/sense6t

The controller fires the sense amplifier a fixed chain of buffers
(``SAE_BUF``) after the wordline; a longer bitline develops its split more
slowly, so past some length that chain fires before the split clears the
amplifier's offset.  The simulated amplifier has no offset (its devices
match), so it resolves any split, however small: what is measured instead is
how long, after the wordline crosses VDD/2, the bitline pair takes to split
by each sense margin (``--margins-mv``), in Xyce, on a column built from the
macro deck's own subcircuits (the released 6T cell, its dummy and cap, the
staggered IO block):

* four bitline rows of `cells` cells (the IO block's 4:1 mux), every
  wordline but the read one held low;
* the cell read (the one farthest from the IO) stores one value and every
  other cell on its bitline the opposite one: their leakage works against
  the read;
* the bitlines carry a wire capacitance per cell (``--wire-ff``; the deck
  has none, the layout's M2 bar is ~0.1 um a cell);
* ideal sources: precharge released, the column selected, the wordline
  raised, the sense enable raised ``--window`` later, the output enabled;
  Q must still read the stored value (a check on the column).

Both stored values are read; the slower one counts.  The result is
``sense_timing.json``: per length and margin, the wordline-to-split time.
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import simulate_macro as sm  # noqa: E402

VDD = 0.7
MUX = 4
EDGE, T_WL = 20e-12, 240e-12
#: The subcircuits the column takes from a compiled 6T macro's deck.
NEEDED = ("sram_cell_6t_122", "dummy_sram_6t122", "dummy_topbot_v1", "dummy_topbot_v2")


def subckt_text(deck: str, name: str) -> str:
    match = re.search(rf"^\.SUBCKT {re.escape(name)} .*?^\.ENDS[^\n]*\n", deck, re.M | re.S | re.I)
    if not match:
        raise ValueError(f"no .SUBCKT {name}")
    return match.group(0)


def column_deck(cells: int, delay_ps: float, stored: int, deck: str, io: str, model: Path, wire_ff: float) -> str:
    """A deck reading row 0's cell `cells - 1` (the one farthest from the IO) with the sense enable `delay_ps` after its wordline."""
    read = cells - 1
    lines = [f"* 6T column: {cells} cells a bitline, read row 0 cell {read} (stores {stored}), SAE +{delay_ps} ps",
             f".include {model}"]  # fmt: skip
    lines += [subckt_text(deck, name) for name in NEEDED]
    lines += [io.replace(".END\n", "")]
    for r in range(MUX):
        for i in range(cells):
            lines.append(f"Xc{r}_{i} wl{i} bln{r} bl{r} vdd 0 sram_cell_6t_122")
            lines.append(f"Cw{r}_{i} bl{r} 0 {wire_ff}f")
            lines.append(f"Cwn{r}_{i} bln{r} 0 {wire_ff}f")
        lines.append(f"Xd{r} bln{r} vdd 0 dummy_sram_6t122")
        lines.append(f"Xcap{r} bln{r} vdd 0 dummy_topbot_v{1 + r % 2}")
    io_nets = []
    for r in range(MUX):
        io_nets += [f"bl{r}", f"bln{r}", f"ysel{r}", f"yseln{r}"]
    io_nets += ["prechn", "sae", "d", "wrena", "wrenan", "oe", "oeb", "q", "vdd", "0"]
    lines.append("Xio " + " ".join(io_nets) + " iocol_sram_6t")
    edge, t_release, t_wl = EDGE, 200e-12, T_WL
    t_sae = t_wl + delay_ps * 1e-12
    stop = t_sae + 250e-12

    def step(name, t, high_first=False):
        lo, hi = (VDD, 0) if high_first else (0, VDD)
        return f"V{name} {name} 0 PWL(0 {lo} {t:.4g} {lo} {t + edge:.4g} {hi})"

    lines += [
        f"Vvdd vdd 0 {VDD}",
        step("prechn", t_release),  # precharge (low) released
        step("ysel0", t_release), step("yseln0", t_release, high_first=True),
        *[f"Vysel{r} ysel{r} 0 0\nVyseln{r} yseln{r} 0 {VDD}" for r in range(1, MUX)],
        f"Vwl wl{read} 0 PWL(0 0 {t_wl:.4g} 0 {t_wl + edge:.4g} {VDD})",
        *[f"Vwl{i} wl{i} 0 0" for i in range(cells) if i != read],
        step("sae", t_sae),
        step("oe", t_sae), step("oeb", t_sae, high_first=True),
        "Vd d 0 0", "Vwrena wrena 0 0", f"Vwrenan wrenan 0 {VDD}",
        "Cq q 0 1f", "Rq q 0 1T",
    ]  # fmt: skip
    for r in range(MUX):
        for i in range(cells):
            value = stored if (r == 0 and i == read) else 1 - stored
            lines.append(f".IC V(Xc{r}_{i}:Q)={VDD * value} V(Xc{r}_{i}:QB)={VDD * (1 - value)}")
    lines += [f".tran 1p {stop:.4g} NOOP", ".options timeint reltol=1e-3 abstol=1e-9",
              f".print tran format=csv V(q) V(bl0) V(bln0) V(sae)", ".end"]  # fmt: skip
    return "\n".join(lines) + "\n", stop


def split_times(cells, stored, deck, io, model, wire_ff, window, margins, work: Path):
    """(the bit Q settles to or None, {margin mV: ps from the wordline's VDD/2 to that split, or None})."""
    tag = f"c{cells}_s{stored}"
    work.mkdir(parents=True, exist_ok=True)
    text, stop = column_deck(cells, window, stored, deck, io, model, wire_ff)
    path = work / f"{tag}.cir"
    path.write_text(sm.for_xyce(text))
    subprocess.run([str(sm.find_xyce()), path.name], cwd=work, stdout=subprocess.DEVNULL,
                   stderr=subprocess.STDOUT, check=False, timeout=36000)  # fmt: skip
    waves = sm.read_csv(work / f"{tag}.cir.csv")
    time, bl, bln = waves["TIME"], waves["V(BL0)"], waves["V(BLN0)"]
    q = sm.at(time, waves["V(Q)"], stop - 10e-12)
    bit = None if 0.2 * VDD < q < 0.8 * VDD else int(q > VDD / 2)
    t_wl = T_WL + EDGE / 2
    t_sae = T_WL + window * 1e-12
    found = {}
    for mv in margins:
        cross = next((t for t, a, b in zip(time, bl, bln) if t_wl <= t <= t_sae and abs(a - b) >= mv * 1e-3), None)
        found[mv] = None if cross is None else round((cross - t_wl) * 1e12, 1)
    return bit, found


def measure(cells, deck, io, model, wire_ff, window, margins, work):
    """Per margin, the slower stored value's time; None if a value reads wrong or never splits that far."""
    runs = {s: split_times(cells, s, deck, io, model, wire_ff, window, margins, work) for s in (0, 1)}
    if runs[0][0] == runs[1][0] or None in (runs[0][0], runs[1][0]):
        return {mv: None for mv in margins}, {s: r[0] for s, r in runs.items()}
    times = {}
    for mv in margins:
        each = [runs[s][1][mv] for s in (0, 1)]
        times[mv] = None if None in each else max(each)
    return times, {s: r[0] for s, r in runs.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--cells", default="16,32,64,128,256,512", help="bitline lengths (cells)")
    parser.add_argument("--deck", type=Path, default=None, help="a compiled 6T macro's .sp (default: the newest)")
    parser.add_argument("--wire-ff", type=float, default=0.02, help="bitline wire capacitance per cell, fF")
    parser.add_argument("--corner", choices=("TT", "SS", "FF"), default="TT")
    parser.add_argument("--out", type=Path, default=REPO / "tmp/sense_timing_6t")
    parser.add_argument("--margins-mv", default="25,50,100", help="bitline splits to time, mV")
    parser.add_argument("--window", type=float, default=400, help="wordline to sense enable in the testbench, ps")
    parser.add_argument("--jobs", type=int, default=6)
    args = parser.parse_args(argv)

    deck_path = args.deck or max((p for p in (REPO / "results").glob("sram_x*/*.sp")
                                  if "sram_cell_6t_122" in p.read_text()), key=lambda p: p.stat().st_mtime)  # fmt: skip
    deck = deck_path.read_text()
    io = (REPO / "tech/spice/sram_6t_iocolumn.sp").read_text()
    io = subckt_text(io, "iocol_block_6t") + subckt_text(io, "iocol_sram_6t")
    args.out = args.out.resolve()  # the decks include the model by path; Xyce runs in their folder
    args.out.mkdir(parents=True, exist_ok=True)
    model = args.out / f"asap7_{args.corner}.xyce.pm"
    model.write_text(re.sub(r"level\s*=\s*72", "level = 107",
                            (REPO / f"tech/models/hspice/7nm_{args.corner}.pm").read_text()))  # fmt: skip
    margins = [float(m) for m in args.margins_mv.split(",")]
    lengths = [int(c) for c in args.cells.split(",")]
    with futures.ThreadPoolExecutor(args.jobs) as pool:
        found = dict(zip(lengths, pool.map(lambda c: measure(c, deck, io, model, args.wire_ff, args.window,
                                                             margins, args.out / f"c{c}"), lengths)))  # fmt: skip
    result = {"corner": args.corner, "vdd": VDD, "wire_ff_per_cell": args.wire_ff, "deck": str(deck_path),
              "window_ps": args.window, "reads": {str(c): bits for c, (_, bits) in found.items()},
              "wl_to_split_ps": {str(c): {f"{mv:g}mV": t for mv, t in times.items()}
                                 for c, (times, _) in found.items()}}  # fmt: skip
    (args.out / "sense_timing.json").write_text(json.dumps(result, indent=1) + "\n")
    for c, (times, bits) in found.items():
        cols = "  ".join(f"{mv:g} mV {'-' if t is None else f'{t:g} ps'}" for mv, t in times.items())
        print(f"{c:5d} cells: {cols}  (reads {bits[0]}/{bits[1]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
