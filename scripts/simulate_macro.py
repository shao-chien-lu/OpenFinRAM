#!/usr/bin/env python3
"""Transistor-level read/write simulation of a whole macro, in Xyce.

    .venv/bin/python scripts/simulate_macro.py results/sram_x4x2x1_<stamp>

A single-port 6T macro (``--bitcell 6t``, recognised by its deck's bitcell)
runs the same program on its one port, each pair of operations below one
after the other.

The decks under `tests/spice/` exercise the bitcell and the IO column with ideal
wordline, select and sense-enable sources.  Nothing simulated the macro: the
synthesized controller generating those signals, in its own time, for the array
it is wired to.  This does.  The DUT is the `.sp` the compiler writes beside the
GDS, every transistor of it, driven only at its pins.

Every bitcell is preloaded with a background pattern (`.IC` on its storage
nodes), so that a read can be checked before anything has been written, and the
two ports then run a short program against a software model of the memory:

  0  A and B read an all-zeros word                        (settles the latches)
  1  A and B read all-ones words, one in each half         (read path alone)
  2  A and B write zeros over all-ones words, at once      (two-port write)
  3  each port reads what the other wrote                  (cross-port, cross-half)
  4  A and B write 1010.. and 0101.. over them, at once
  5  each port reads its own word back                     (bit order)
  6  each port reads the other's
  7  A and B read the same all-zeros word                  (same-address read)
  8  A and B read column 0, one in each half
  9  ... and the last column, halves swapped

The output latch holds the last word a port read, and Q shows it again the
moment the output enables, some 140 ps before sense enable.  A read that
expects what its port read last therefore cannot fail, and the latch powers up
holding something.  So every read here differs from the one before it on that
port, a bit only counts as proven when Q moved *after* sense enable, and the
run is only accepted if each port's every bit was proven both ways.

Checked: every Q at the end of its read, against the model; the final state of
*every* cell against the model, so a disturb anywhere shows; that coverage; how
long after the clock edge the wordline, sense enable and Q arrive; and two
hazards by name, because a corrupted cell does not say why: write enable
moving in a cycle that is not a write, and an unselected wordline lifting.

``--spaced`` puts an idle cycle after every write.  Operations back to back
are legitimate use and the default; the spaced program is for telling what
else works while something that depends on the previous cycle does not.

A command and its address are captured on a rising edge with ``ce_n`` low, and
the operation runs in the clock-high phase that follows (`tech/verilog_dp/
sram_control.v`), so inputs change on falling edges and Q is sampled just
before the next one.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BITCELL = "sram_cell_8t"
#: The single-port macro's bitcell (``WL BLN BL VDD VSS``); its storage nodes are Q and QB too.
BITCELL_6T = "sram_cell_6t_122"
XYCE_CANDIDATES = (
    os.environ.get("XYCE"),
    "/home/jeff/iv4/local/xyce-14.4/bin/Xyce",
    shutil.which("Xyce"),
)


# ── Netlist ──────────────────────────────────────────────────────────────────
def fold(text: str) -> list[str]:
    """SPICE lines with continuations joined and comments dropped."""
    lines: list[str] = []
    for raw in text.splitlines():
        if raw.startswith("+") and lines:
            lines[-1] += " " + raw[1:].strip()
        elif raw.strip() and not raw.lstrip().startswith("*"):
            lines.append(raw.strip())
    return lines


def for_xyce(text: str) -> str:
    """Xyce's BSIM-CMG (level 107) takes its width from ``nfin``: ``W``, ``nf`` and ``m`` are errors.

    ``nf`` and ``m`` are width, so they are folded into ``nfin`` rather than
    dropped: ``nfin=18 nf=2`` is a 36-fin device, and losing the ``nf`` would
    quietly halve it.
    """
    out = []
    for line in text.splitlines():
        if line[:1] in "Mm":
            copies = 1
            for match in re.finditer(
                r"\b(?:nf|m)\s*=\s*(\d+)\b", line, flags=re.IGNORECASE
            ):
                copies *= int(match[1])
            line = re.sub(r"\s+(?:w|nf|m)\s*=\s*\S+", "", line, flags=re.IGNORECASE)
            if copies > 1:
                line = re.sub(r"\bnfin\s*=\s*(\d+)", lambda m: f"nfin={int(m[1]) * copies}", line,
                              flags=re.IGNORECASE)  # fmt: skip
        out.append(line)
    return "\n".join(out) + "\n"


@dataclass(frozen=True)
class Subckt:
    pins: tuple[str, ...]
    instances: tuple[tuple[str, tuple[str, ...], str], ...]  # (name, nets, subckt)


def parse_subckts(text: str) -> dict[str, Subckt]:
    found: dict[str, Subckt] = {}
    name, pins, instances = None, (), []
    for line in fold(text):
        fields = line.split()
        head = fields[0].upper()
        if head == ".SUBCKT":
            name, pins, instances = fields[1], tuple(fields[2:]), []
        elif head == ".ENDS" and name:
            found[name.lower()] = Subckt(pins, tuple(instances))
            name = None
        elif head[0] == "X" and name:
            nets = [f for f in fields[1:] if "=" not in f]
            instances.append((fields[0], tuple(nets[:-1]), nets[-1]))
    return found


def bitcells(subckts: dict[str, Subckt], top: str, bitcell: str = BITCELL) -> list[dict]:
    """Every bitcell under `top`: its instance path and the top-level nets on its pins."""
    found = []

    def walk(cell: str, path: list[str], bound: dict[str, str]) -> None:
        for inst, nets, ref in subckts[cell.lower()].instances:
            child = subckts.get(ref.lower())
            if child is None:
                continue
            here = [*path, inst]
            local = {
                pin.lower(): bound.get(net.lower(), ":".join([*path, net]))
                for pin, net in zip(child.pins, nets)
            }
            if ref.lower() == bitcell:
                found.append({"path": ":".join(here), "nets": local})
            else:
                walk(ref, here, local)

    walk(top, [], {pin.lower(): pin for pin in subckts[top.lower()].pins})
    return found


def top_level_net(
    subckts: dict[str, Subckt], top: str, path: str, pin: str
) -> str | None:
    """The top-level net the instance at `path` (``Xa:Xb``) has on its `pin`, or None if internal."""
    names = path.split(":")
    cell, trail = top.lower(), []
    for name in names:
        inst = next(
            (i for i in subckts[cell].instances if i[0].lower() == name.lower()), None
        )
        if inst is None:
            return None
        trail.append((subckts[cell], inst))
        cell = inst[2].lower()
    pins = [p.lower() for p in subckts[cell].pins]
    if pin.lower() not in pins:
        return None
    net = pin.lower()
    for parent, (_, nets, ref) in reversed(trail):
        child_pins = [p.lower() for p in subckts[ref.lower()].pins]
        net = nets[child_pins.index(net)].lower()
        parent_pins = [p.lower() for p in parent.pins]
        if parent is subckts[top.lower()]:
            return net
        if net not in parent_pins:
            return None
    return net


# ── Address map ──────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Geometry:
    wordlines: (
        int  # NUM_WL: rows one row-select field covers, half the array's wordlines
    )
    mux: int
    bits: int
    banks: int = 1
    #: Banks in pairs share port B's IO: its sense and write enables are one per pair.
    shared_b: bool = False
    #: The macro's ports: "AB", or "A" for the single-port macro.
    ports: str = "AB"

    def io(self, port: str, bank: int) -> int:
        """Index of the sense/write enable serving `bank` on `port`."""
        return bank // 2 if self.shared_b and port == "B" else bank

    def ios(self, port: str) -> int:
        return self.banks // 2 if self.shared_b and port == "B" else self.banks

    @property
    def bank_shift(self) -> int:
        """The address bit the bank index starts at: above the row, column and half bits."""
        return self.row_bits + self.col_bits + 1

    def bank(self, address: int) -> int:
        return address >> self.bank_shift

    @property
    def row_bits(self) -> int:
        return max(1, math.ceil(math.log2(self.wordlines)))

    @property
    def col_bits(self) -> int:
        return max(1, math.ceil(math.log2(self.mux)))

    @property
    def words(self) -> int:
        return 2 * self.wordlines * self.mux * self.banks

    def split(self, address: int) -> tuple[int, int, int]:
        """``(upper half?, column, row)`` as `sram_control.v` slices an address.

        A column is one unsplit array; "half" is only the address bit above the
        column select, the top bit of the wordline index (`wordline`).
        """
        row = address & ((1 << self.row_bits) - 1)
        col = (address >> self.row_bits) & ((1 << self.col_bits) - 1)
        half = (address >> (self.row_bits + self.col_bits)) & 1
        return half, col, row

    def wordline(self, address: int) -> int:
        """Index of the wordline an address opens, in the ``wl_a_lo`` etc. buses (all banks)."""
        half, _, row = self.split(address)
        return self.bank(address) * 2 * self.wordlines + half * self.wordlines + row


def locate(cells: list[dict], wordlines: int, ports: str = "AB") -> None:
    """Add ``half``, ``col``, ``row``, ``group`` and ``stack`` to each cell, from what it is wired to.

    The wordline a cell sits on is a driver strip output (``wl_a_lo[i]``, one
    bus over the stack of data bits below the controller band, ``wl_a_hi[i]``
    the one above) and its bitline is a net of its column group (``BL_A[c]``),
    so neither the instance names nor their order are assumed.  Both ports are
    checked: a cell on ``wl_a_lo[i]`` has to be on ``wl_b_lo[i]``.
    """
    for cell in cells:
        single = ports == "A"  # the 6T cell's pins are WL and BL
        wordline = re.fullmatch(
            r"(?:.*:)?wl_a_((?:lo|hi)(?:_s\d+)?)\[(\d+)\]", cell["nets"]["wl" if single else "wla"], re.IGNORECASE
        )
        other = wordline if single else re.fullmatch(
            r"(?:.*:)?wl_b_(lo|hi)\[(\d+)\]", cell["nets"]["wlb"], re.IGNORECASE
        )
        bitline = re.fullmatch(
            r"(.*):bl_a\[(\d+)\]", cell["nets"]["bl" if single else "bla"], re.IGNORECASE
        )
        if (not wordline or not bitline or not other or other[2] != wordline[2]
                or other[1].lower() != wordline[1].lower()):  # fmt: skip
            raise ValueError(f"cannot place {cell['path']}: {cell['nets']}")
        index = int(wordline[2])  # bank * 2 * NUM_WL + half * NUM_WL + row
        cell.update(half=(index // wordlines) % 2, row=index % wordlines, col=int(bitline[2]),
                    bank=index // (2 * wordlines), group=bitline[1], stack=wordline[1].lower())  # fmt: skip


# ── Testbench ────────────────────────────────────────────────────────────────
PATTERNS = ("zeros", "ones", "0101", "1010")
#: The two stacks of data bits, below and above the controller band, each with its own wordlines.
STACKS = ("lo", "hi")


def buses(cells: list[dict]) -> list[str]:
    """The wordline buses the cells sit on: a stack's (``lo``), or with divided wordlines its segments' (``lo_s1``)."""
    return sorted({cell["stack"] for cell in cells})


def pattern_of(address: int) -> str:
    """Which of four words an address is preloaded with: a hash, so no stuck line reproduces it."""
    return PATTERNS[(((address + 3) * 2654435761) >> 9) & 3]


def background(address: int, bit: int) -> int:
    return {"zeros": 0, "ones": 1, "0101": (bit + 1) & 1, "1010": bit & 1}[
        pattern_of(address)
    ]


@dataclass(frozen=True)
class Op:
    kind: str  # "R", "W" or "-"
    address: int = 0
    data: int = 0


def col0_top_hint(g: Geometry) -> int:
    return next(
        addr
        for addr in range(g.words)
        if g.split(addr)[:2] == (1, 0) and pattern_of(addr) != "zeros"
    )


def default_program(g: Geometry, *, spaced: bool = False) -> list[tuple[Op, Op]]:
    mask = (1 << g.bits) - 1
    alternating = sum(1 << bit for bit in range(1, g.bits, 2))  # 1010..

    def find(
        pattern: str, half: int, *, col: int | None = None, avoid: tuple[int, ...] = ()
    ) -> int:
        for address in range(g.words):
            where = g.split(address)
            if (pattern_of(address) == pattern and where[0] == half and address not in avoid
                    and (col is None or where[1] == col)):  # fmt: skip
                return address
        raise ValueError(
            f"no {pattern} word in half {half}; change the background hash"
        )

    ones_top, ones_bottom = find("ones", 1), find("ones", 0)
    a, b = find("ones", 0, avoid=(ones_bottom,)), find("ones", 1, avoid=(ones_top,))
    zeros = find("zeros", 1)
    col0_bottom = next(
        addr
        for addr in range(g.words)
        if g.split(addr)[:2] == (0, 0) and pattern_of(addr) != "zeros"
    )
    last = (
        g.mux - 1
    )  # ... and the last column, the other way round, on a word that is not what Q holds
    # With few rows a column half may hold no word of another pattern: then any of its words.
    def in_column(half: int, avoid: str) -> int:
        words = [addr for addr in range(g.words) if g.split(addr)[:2] == (half, last)]
        return next((addr for addr in words if pattern_of(addr) != avoid), words[0])

    last_bottom = in_column(0, pattern_of(col0_top_hint(g)))
    last_top = in_column(1, pattern_of(col0_bottom))
    col0_top = next(
        addr
        for addr in range(g.words)
        if g.split(addr)[:2] == (1, 0) and pattern_of(addr) != "zeros"
    )
    program = [
        (
            Op("R", zeros),
            Op("R", zeros),
        ),  # whatever the latches woke up holding, this settles it
        (Op("R", ones_top), Op("R", ones_bottom)),
        (Op("W", a, 0), Op("W", b, 0)),
        (Op("R", b), Op("R", a)),
        (Op("W", a, alternating), Op("W", b, ~alternating & mask)),
        (Op("R", a), Op("R", b)),
        (Op("R", b), Op("R", a)),
        (Op("R", zeros), Op("R", zeros)),
        (Op("R", col0_bottom), Op("R", col0_top)),
        (Op("R", last_top), Op("R", last_bottom)),
    ]
    if g.banks > 1:
        # The same words one bank up: a write each port, reads back across
        # ports, and a read of each bank at once on the same row and column.
        up = (g.banks - 1) << g.bank_shift
        program += [
            (Op("W", a | up, ~alternating & mask), Op("W", b | up, alternating)),
            (Op("R", b | up), Op("R", a | up)),
            (Op("R", a), Op("R", a | up)),
            (Op("R", b | up), Op("R", b)),
        ]
    if g.ports == "A":
        # One port: the pairs' operations one after the other, B's on A.
        program = [(op,) for ops in program for op in ops if op.kind != "-"]
    if spaced:
        program = [step for ops in program
                   for step in ([ops, tuple(Op("-") for _ in ops)] if any(op.kind == "W" for op in ops) else [ops])]  # fmt: skip
    return program


def pwl(points: list[tuple[float, float]]) -> str:
    return "PWL(" + " ".join(f"{t:.6g} {v:.4g}" for t, v in points) + ")"


def stimulus(
    name: str,
    levels: list[int],
    *,
    period: float,
    vdd: float,
    edge: float,
    first: float,
) -> str:
    """One input: ``levels[k]`` holds around rising edge k, changing on the falling edge before it."""
    points, last = [(0.0, levels[0] * vdd)], levels[0]
    for k, level in enumerate(levels[1:], start=1):
        if level != last:
            at = first + k * period - period / 2
            points += [(at, last * vdd), (at + edge, level * vdd)]
            last = level
    return f"V{name.replace('[', '_').replace(']', '')} {name} 0 {pwl(points)}"


def port_a_name(pin: str) -> str:
    """Port A's name for a single-port macro pin: ``ce_n`` -> ``ce_n_A``, ``A[3]`` -> ``A_A[3]``; others as they are."""
    if pin in ("ce_n", "we_n", "oe_n"):
        return f"{pin}_A"
    return re.sub(r"^([ADQ])\[", r"\1_A[", pin, flags=re.IGNORECASE)


def supply_network(source: str, ohms: float) -> tuple[str, dict[str, tuple[int, int]]]:
    """The deck with each stack's tiles on their own supply nodes, `ohms` apart, fed at the stack's ends.

    A middle column tile takes its supply only through its neighbours (the
    IO block's M3 straps: two minimum-width straps a net, ~135 ohms a 4:1
    tile); only the end tiles' supply is routed.  Each tile ``X<b>_<k>`` of
    a ``stacked_colgrp`` subckt gets ``vdd_<b>_<k>``/``vss_<b>_<k>``, joined
    to the next by `ohms`; the first and last tile (and the end rows) are
    tied to the subckt's VDD/VSS.  Returns the deck and, by stack subckt,
    the (bank, tile) to watch: the middle of the chain.
    """
    out, block, watch = [], None, {}

    def flush(block):
        name, lines = block
        chains: dict[int, list[int]] = {}
        for line in lines:
            m = re.match(r"X(\d+)_(\d+)\s", line, re.I)
            if m and line.split()[-1].lower().startswith("colgrp"):
                chains.setdefault(int(m[1]), []).append(int(m[2]))
        result = []
        for line in lines:
            tokens = line.split()
            tile = re.fullmatch(r"X(\d+)_(\d+)", tokens[0], re.I) if tokens else None
            end = re.fullmatch(r"XE(\d+)_([BT])", tokens[0], re.I) if tokens else None
            if (tile or end) and [t.upper() for t in tokens[-3:-1]] == ["VDD", "VSS"]:
                if tile:
                    b, k = int(tile[1]), int(tile[2])
                else:
                    b = int(end[1])
                    k = min(chains[b]) if end[2].upper() == "B" else max(chains[b])
                tokens[-3:-1] = [f"vdd_{b}_{k}", f"vss_{b}_{k}"]
                line = " ".join(tokens)
            if line.upper().startswith(".ENDS"):
                for b, ks in chains.items():
                    ks = sorted(ks)
                    for net in ("vdd", "vss"):
                        for k0, k1 in zip(ks, ks[1:]):
                            result.append(f"R{net}_strap_{b}_{k0} {net}_{b}_{k0} {net}_{b}_{k1} {ohms:g}")
                        for k in {ks[0], ks[-1]}:
                            result.append(f"R{net}_feed_{b}_{k} {net}_{b}_{k} {net.upper()} 1m")
                    watch[name] = (b, ks[len(ks) // 2])
            result.append(line)
        return result

    for line in fold(source):
        if block is None and re.match(r"\.SUBCKT\s+stacked_colgrp", line, re.I):
            block = (line.split()[1].lower(), [line])
            continue
        if block is not None:
            block[1].append(line)
            if line.upper().startswith(".ENDS"):
                out += flush(block)
                block = None
            continue
        out.append(line)
    return "\n".join(out) + "\n", watch


def build_deck(netlist: Path, model: Path, top: str, pins: tuple[str, ...], cells: list[dict], g: Geometry,
               program: list[tuple[Op, Op]], *, period: float, vdd: float, load: float,
               probes: tuple[str, ...] = (), clock_slew: float | None = None,
               temperature: float = 25.0, sample_interval: float | None = None) -> tuple[str, dict]:  # fmt: skip
    edge, first = (
        min(20e-12, period / 50),
        period,
    )  # first rising edge after one period of reset
    clock_edge = edge if clock_slew is None else clock_slew / 0.6  # Liberty's 20-80% slew
    if not 0 < clock_edge < period / 2:
        raise ValueError("clock slew must be positive and shorter than 0.3 * period")
    interval = period / 400 if sample_interval is None else sample_interval
    if not 0 < interval <= period / 400:
        raise ValueError("sample interval must be positive and at most period / 400")
    cycles = len(program) + 2  # an idle cycle either side
    # The single-port macro names its pins without the port (ce_n, A[], D[],
    # Q[]); the bench drives and reads them as port A's, by position.
    pins = tuple(port_a_name(p) for p in pins)
    levels: dict[str, list[int]] = {}
    address_bits = sum(1 for p in pins if p.lower().startswith("a_a["))

    def drive(name: str, cycle: int, level: int) -> None:
        levels.setdefault(name, [idle[name]] * (cycles + 1))[cycle] = level

    idle = {}
    for port in g.ports:
        idle.update({f"ce_n_{port}": 1, f"we_n_{port}": 1, f"oe_n_{port}": 1})
        idle.update({f"A_{port}[{i}]": 0 for i in range(address_bits)})
        idle.update({f"D_{port}[{i}]": 0 for i in range(g.bits)})
    for name in idle:
        levels[name] = [idle[name]] * (cycles + 1)
    for cycle, ops in enumerate(program, start=1):
        for port, op in zip(g.ports, ops):
            if op.kind == "-":
                continue
            drive(f"ce_n_{port}", cycle, 0)
            drive(f"we_n_{port}" if op.kind == "W" else f"oe_n_{port}", cycle, 0)
            for i in range(address_bits):
                drive(f"A_{port}[{i}]", cycle, (op.address >> i) & 1)
            for i in range(g.bits):
                drive(f"D_{port}[{i}]", cycle, (op.data >> i) & 1)

    stop = first + cycles * period
    lines = [
        f"* {top}: whole-macro read/write, period {period * 1e9:g} ns, VDD {vdd:g} V",
        f".include {model}",
        f".include {netlist}",
        f"VVDD vdd 0 {vdd}",
        "VVSS vss 0 0",
        f".temp {temperature:g}",
        f"Vclk clk 0 PULSE(0 {vdd} {first - clock_edge / 2:.9g} {clock_edge:.9g} {clock_edge:.9g} {period / 2 - clock_edge:.9g} {period:.9g})",
        f"Vrst rst_n 0 {pwl([(0, 0), (period / 4, 0), (period / 4 + edge, vdd)])}",
    ]
    lines += [
        stimulus(name, series, period=period, vdd=vdd, edge=edge, first=first)
        for name, series in levels.items()
    ]
    outputs = [p for p in pins if p.lower().startswith("q_")]
    for q in (
        outputs
    ):  # Q is tri-stated between reads: a load to hold it, a bleed so DC has a solution
        tag = q.replace("[", "_").replace("]", "")
        lines += [f"C{tag} {q} 0 {load:g}", f"R{tag} {q} 0 1T"]
    lines.append("Xdut " + " ".join(pins) + f" {top}")
    for cell in cells:
        value = background(cell["address"], cell["bit"])
        lines.append(
            f".IC V(Xdut:{cell['path']}:Q)={vdd * value:g} V(Xdut:{cell['path']}:QB)={vdd * (1 - value):g}"
        )
    watched = ["clk", *outputs]
    watched += [f"Xdut:wl_{port}_{stack}[{index}]" for port in g.ports.lower() for stack in buses(cells)
                for index in range(2 * g.wordlines * g.banks)]  # fmt: skip
    watched += [f"Xdut:{net}_{port}[{io}]" for net in ("sae", "wrena") for port in g.ports
                for io in range(g.ios(port))]  # fmt: skip
    watched += [f"Xdut:{cell['path']}:Q" for cell in cells]
    watched += [f"Xdut:{node}" for node in probes]
    lines += [
        # No DC operating point: 32 bistable cells, the flops and the output latches
        # have no unique one, and Xyce does not find any.  Start from the preload,
        # everything else at zero, and let reset settle it before the first edge.
        f".tran {period / 2000:.4g} {stop:.6g} NOOP",
        ".options timeint reltol=1e-3 abstol=1e-9",
        ".options output initial_interval=" + f"{interval:.9g}",
        ".print tran format=csv "
        + " ".join(f"V({node})" for node in watched)
        + " I(VVDD)",
        ".end",
    ]
    plan = {
        "first_edge": first,
        "period": period,
        "stop": stop,
        "edge": edge,
        "clock_slew": clock_edge * 0.6,
        "outputs": outputs,
    }
    return "\n".join(lines) + "\n", plan


# ── Results ──────────────────────────────────────────────────────────────────
def read_csv(path: Path) -> dict[str, list[float]]:
    with path.open() as stream:
        rows = list(csv.reader(stream))
    header = [h.strip().upper() for h in rows[0]]
    columns = {name: [] for name in header}
    for row in rows[1:]:
        if len(row) == len(header):
            for name, value in zip(header, row):
                columns[name].append(float(value))
    return columns


def at(time_axis: list[float], values: list[float], when: float) -> float:
    for i in range(1, len(time_axis)):
        if time_axis[i] >= when:
            t0, t1 = time_axis[i - 1], time_axis[i]
            return values[i - 1] + (values[i] - values[i - 1]) * (
                (when - t0) / (t1 - t0) if t1 > t0 else 0
            )
    return values[-1]


def crossings(
    time_axis: list[float], values: list[float], level: float, start: float, stop: float
) -> list[float]:
    found = []
    for i in range(1, len(time_axis)):
        if start <= time_axis[i] <= stop:
            lo, hi = values[i - 1], values[i]
            if (lo - level) * (hi - level) < 0:
                found.append(
                    time_axis[i - 1]
                    + (time_axis[i] - time_axis[i - 1]) * (level - lo) / (hi - lo)
                )
    return found


def evaluate(waves: dict[str, list[float]], plan: dict, program: list[tuple[Op, Op]], cells: list[dict],
             g: Geometry, vdd: float) -> dict:  # fmt: skip
    t = waves["TIME"]
    memory = {
        (c["address"], c["bit"]): background(c["address"], c["bit"]) for c in cells
    }
    reads, writes = [], []
    first, period = plan["first_edge"], plan["period"]
    seen = {
        (port, bit, to): False
        for port in g.ports
        for bit in range(g.bits)
        for to in (0, 1)
    }
    hazards = []

    def peak(signal: str, start: float, stop: float) -> float:
        return max(
            (v for when, v in zip(t, waves[signal]) if start <= when <= stop),
            default=0.0,
        )

    def after_clock(signal: str, rise: float, *, last: bool = False) -> float | None:
        found = crossings(t, waves[signal], vdd / 2, rise, rise + 0.45 * period)
        return None if not found else (found[-1] if last else found[0]) - rise

    supply_trace = waves.get("I(VVDD)", [])

    def cycle_energy_fj(rise: float) -> float | None:
        """Supply energy over the clock period that starts at `rise`."""
        if not supply_trace:
            return None
        charge = sum((t[i] - t[i - 1]) * -(supply_trace[i] + supply_trace[i - 1]) / 2
                     for i in range(1, len(t)) if rise <= t[i - 1] and t[i] <= rise + period)  # fmt: skip
        return round(charge * vdd * 1e15, 2)

    def slew_ps(signal: str, rising: bool, rise: float) -> float | None:
        """20-80 % transition of `signal` in the clock-high phase after `rise` (its last such edge)."""
        lo = crossings(t, waves[signal], 0.2 * vdd, rise, rise + 0.45 * period)
        hi = crossings(t, waves[signal], 0.8 * vdd, rise, rise + 0.45 * period)
        if not lo or not hi:
            return None
        start, stop = (lo[-1], hi[-1]) if rising else (hi[-1], lo[-1])
        return ps(stop - start) if stop > start else None

    for cycle, ops in enumerate(program, start=1):
        rise = first + cycle * period
        sample = rise + 0.45 * period
        for port, op in zip(g.ports, ops):
            # Write enable only for a write, and only its bank's (its pair's, shared).
            for io in range(g.ios(port)):
                if op.kind == "W" and io == g.io(port, g.bank(op.address)):
                    continue
                lifted = peak(
                    f"V(XDUT:WRENA_{port}[{io}])",
                    rise - period / 4,
                    rise + period / 2,
                )
                if lifted > 0.3 * vdd:
                    hazards.append({"cycle": cycle, "port": port, "volts": round(lifted, 3),
                                    "what": f"write enable {io} in a cycle that does not write it"})  # fmt: skip
            if op.kind == "-":
                continue
            selected = g.wordline(op.address)
            for stack in buses(cells):
                for other in range(2 * g.wordlines * g.banks):
                    if other == selected:
                        continue
                    lifted = peak(
                        f"V(XDUT:WL_{port}_{stack.upper()}[{other}])",
                        rise - period / 4,
                        rise + period / 2,
                    )
                    if lifted > 0.3 * vdd:
                        hazards.append({"cycle": cycle, "port": port, "volts": round(lifted, 3),
                                        "what": f"unselected wordline wl_{port.lower()}_{stack}[{other}]"})  # fmt: skip
            # Both stacks' strips decode the same address; the later one is the wordline time.
            fired_each = [
                after_clock(f"V(XDUT:WL_{port}_{stack.upper()}[{selected}])", rise)
                for stack in buses(cells)
            ]
            fired = None if any(f is None for f in fired_each) else max(fired_each)
            if op.kind == "W":
                writes.append({"cycle": cycle, "port": port, "address": op.address, "data": op.data,
                               "clk_to_wl_ps": ps(fired), "energy_fJ": cycle_energy_fj(rise)})  # fmt: skip
                continue
            expected = [memory[(op.address, bit)] for bit in range(g.bits)]
            volts = [
                at(t, waves[f"V(Q_{port}[{bit}])"], sample) for bit in range(g.bits)
            ]
            clean = all(v < 0.2 * vdd or v > 0.8 * vdd for v in volts)
            got = [int(v > vdd / 2) for v in volts]
            sensed = after_clock(f"V(XDUT:SAE_{port}[{g.io(port, g.bank(op.address))}])", rise)
            arrivals = [
                after_clock(f"V(Q_{port}[{bit}])", rise, last=True)
                for bit in range(g.bits)
            ]
            # Proven: Q moved after sense enable, to the right value.  Before it, Q is the latch's memory.
            flipped = [bit for bit, when in enumerate(arrivals)
                       if when is not None and sensed is not None and when > sensed]  # fmt: skip
            directional = {
                direction: ps(max((arrivals[bit] for bit in flipped if expected[bit] == value), default=None))
                for direction, value in (("rise", 1), ("fall", 0))
            }
            arrivals = [x for x in arrivals if x is not None]
            if clean and got == expected:
                for bit in flipped:
                    seen[(port, bit, expected[bit])] = True
            reads.append({"cycle": cycle, "port": port, "address": op.address,
                          "expected": "".join(map(str, reversed(expected))), "read": "".join(map(str, reversed(got))),
                          "volts": [round(v, 4) for v in volts], "bits_sensed": len(flipped),
                          "clk_to_wl_ps": ps(fired), "clk_to_sae_ps": ps(sensed),
                          "clk_to_q_ps": ps(max(arrivals)) if arrivals else None,
                          "clk_to_q_rise_ps": directional["rise"], "clk_to_q_fall_ps": directional["fall"],
                          # Q's 20-80 % edges, rising and falling, over the bits that moved after SAE.
                          "q_rise_ps": max((x for x in (slew_ps(f"V(Q_{port}[{bit}])", True, rise)
                                                         for bit in flipped if expected[bit]) if x), default=None),
                          "q_fall_ps": max((x for x in (slew_ps(f"V(Q_{port}[{bit}])", False, rise)
                                                         for bit in flipped if not expected[bit]) if x), default=None),
                          "energy_fJ": cycle_energy_fj(rise),
                          "ok": clean and got == expected})  # fmt: skip
        for op in ops:  # writes land after this cycle's reads are judged
            if op.kind == "W":
                for bit in range(g.bits):
                    memory[(op.address, bit)] = (op.data >> bit) & 1

    end = plan["stop"] - plan["period"] / 4
    wrong = []
    for cell in cells:
        volts = at(t, waves[f"V(XDUT:{cell['path'].upper()}:Q)"], end)
        want = memory[(cell["address"], cell["bit"])]
        if (volts > vdd / 2) != bool(want) or 0.2 * vdd < volts < 0.8 * vdd:
            wrong.append({"address": cell["address"], "bit": cell["bit"], "path": cell["path"],
                          "expected": want, "volts": round(volts, 4)})  # fmt: skip
    supply = waves.get("I(VVDD)", [])
    charge = (
        sum(
            (t[i] - t[i - 1]) * -(supply[i] + supply[i - 1]) / 2
            for i in range(1, len(t))
        )
        if supply
        else 0.0
    )
    unproven = sorted(
        f"Q_{port}[{bit}] to {to}"
        for (port, bit, to), proven in seen.items()
        if not proven
    )
    return {"reads": reads, "writes": writes, "cells_checked": len(cells), "cells_wrong": wrong,
            "unproven": unproven, "hazards": hazards, "energy_fJ": round(charge * vdd * 1e15, 2),
            "passed": all(r["ok"] for r in reads) and not wrong and not unproven and not hazards}  # fmt: skip


def ps(seconds: float | None) -> float | None:
    return None if seconds is None else round(seconds * 1e12, 1)


def find_xyce() -> Path:
    for candidate in XYCE_CANDIDATES:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise FileNotFoundError("Xyce was not found; set XYCE")


def simulate(result_dir: Path, out: Path, *, period: float = 2e-9, vdd: float = 0.7, corner: str = "TT",
             load: float = 1e-15, netlist: Path | None = None, timeout: float = 7200,
             program: list[tuple[Op, Op]] | None = None, probes: tuple[str, ...] = (),
             spaced: bool = False, strap_ohms: float = 0.0, clock_slew: float | None = None,
             temperature: float = 25.0, sample_interval: float | None = None) -> dict:  # fmt: skip
    result_dir = result_dir.resolve()
    described = json.loads(next(result_dir.glob("*.physical.json")).read_text())
    top = described["cell"]
    source = (netlist or result_dir / f"{top}.sp").read_text()
    subckts = parse_subckts(source)
    single = BITCELL_6T in subckts  # the single-port 6T macro's deck
    g = Geometry(
        wordlines=described["wordlines_per_half"], mux=described.get("column_mux", 4), bits=described["bits"],
        banks=described.get("banks", 1), shared_b=described.get("shared_port_b", False),
        ports="A" if single else "AB",
    )  # fmt: skip
    pins = subckts[top.lower()].pins
    cells = bitcells(subckts, top, BITCELL_6T if single else BITCELL)
    locate(cells, g.wordlines, g.ports)
    groups = sorted({cell["group"] for cell in cells})
    for (
        cell
    ) in cells:  # a column group's data bit is the top-level D_A (D) its DA pin reaches
        net = top_level_net(subckts, top, cell["group"], "da")
        bit = re.fullmatch(r"d_a\[(\d+)\]", port_a_name(net or ""), re.IGNORECASE)
        if not bit:
            raise ValueError(
                f"column group {cell['group']}: DA reaches {net}, not a D_A bit"
            )
        cell["bit"] = int(bit[1])
        cell["address"] = (
            (cell["bank"] << g.bank_shift)
            | (cell["half"] << (g.row_bits + g.col_bits))
            | (cell["col"] << g.row_bits)
            | cell["row"]
        )
    if len(cells) != g.words * g.bits or len(
        {(c["address"], c["bit"]) for c in cells}
    ) != len(cells):
        raise ValueError(
            f"expected {g.words * g.bits} distinct cells, placed {len(cells)} in {len(groups)} groups"
        )

    out = out.resolve()  # the deck includes by path, and Xyce runs from there
    out.mkdir(parents=True, exist_ok=True)
    dut, model = out / f"{top}.xyce.sp", out / f"asap7_{corner}.xyce.pm"
    supply_probes: list[str] = []
    if strap_ohms:
        source, watch = supply_network(source, strap_ohms)
        stacks = [(i[0], i[2].lower()) for i in parse_subckts(source)[top.lower()].instances if i[2].lower() in watch]
        for inst, ref in stacks:
            b, k = watch[ref]
            supply_probes += [f"{inst}:vdd_{b}_{k}", f"{inst}:vss_{b}_{k}"]
        probes = tuple(probes) + tuple(supply_probes)
    dut.write_text(for_xyce(source))
    card = (REPO_ROOT / f"tech/models/hspice/7nm_{corner}.pm").read_text()
    model.write_text(re.sub(r"level\s*=\s*72", "level = 107", card))
    program = program or default_program(g, spaced=spaced)
    deck_text, plan = build_deck(dut, model, top, pins, cells, g, program, period=period, vdd=vdd, load=load,
                                 probes=probes, clock_slew=clock_slew, temperature=temperature,
                                 sample_interval=sample_interval)  # fmt: skip
    deck = out / "macro_rw.cir"
    deck.write_text(deck_text)

    started = time.time()
    with (out / "xyce.log").open("w") as log:
        completed = subprocess.run([str(find_xyce()), deck.name], cwd=out, stdout=log, stderr=subprocess.STDOUT,
                                   check=False, timeout=timeout)  # fmt: skip
    waves_path = out / "macro_rw.cir.csv"
    if completed.returncode != 0 or not waves_path.is_file():
        tail = (out / "xyce.log").read_text()[-1500:]
        raise RuntimeError(
            f"Xyce failed (exit {completed.returncode}); see {out / 'xyce.log'}\n{tail}"
        )
    waves = read_csv(waves_path)
    verdict = evaluate(waves, plan, program, cells, g, vdd)
    if supply_probes:
        # The narrowest rail-to-rail voltage any watched tile saw, once the
        # rails are up: the transient starts every node at 0 V (NOOP), so the
        # first period is the supply's own ramp.
        worst = {}
        settled = [i for i, t in enumerate(waves["TIME"]) if t >= period]
        for vdd_node, vss_node in zip(supply_probes[::2], supply_probes[1::2]):
            hi, lo = waves[f"V(XDUT:{vdd_node.upper()})"], waves[f"V(XDUT:{vss_node.upper()})"]
            worst[vdd_node.rsplit(":", 1)[0]] = round(min(hi[i] - lo[i] for i in settled), 4)
        verdict["supply"] = {"strap_ohms": strap_ohms, "min_rail_V": worst}
    verdict.update(cell=top, result_dir=str(result_dir), period_ns=period * 1e9, vdd=vdd, corner=corner, spaced=spaced,
                   clock_slew_ns=plan["clock_slew"] * 1e9, load_pf=load * 1e12, temperature=temperature,
                   program=[[f"{op.kind}{op.address}" + (f"={op.data:0{g.bits}b}" if op.kind == "W" else "")
                             for op in ops] for ops in program],
                   xyce_seconds=round(time.time() - started, 1), deck=str(deck))  # fmt: skip
    (out / "simulation.json").write_text(json.dumps(verdict, indent=1) + "\n")
    return verdict


def describe(verdict: dict) -> str:
    lines = [f"{verdict['cell']}: {'PASS' if verdict['passed'] else 'FAIL'}  "
             f"({'spaced' if verdict.get('spaced') else 'back to back'}, {verdict['corner']} {verdict['vdd']} V, {verdict['period_ns']:g} ns clock, "
             f"Xyce {verdict['xyce_seconds']:.0f} s, {verdict['energy_fJ']:.0f} fJ)"]  # fmt: skip
    for read in verdict["reads"]:
        lines.append(f"  cycle {read['cycle']} {read['port']} read  {read['address']:>3}: {read['read']} "
                     f"(expected {read['expected']}, {read['bits_sensed']} bit(s) moved after SAE)  "
                     f"clk->WL {read['clk_to_wl_ps']}  ->SAE {read['clk_to_sae_ps']}  ->Q {read['clk_to_q_ps']} ps"
                     f"  {'ok' if read['ok'] else 'WRONG ' + str(read['volts'])}")  # fmt: skip
    for write in verdict["writes"]:
        lines.append(f"  cycle {write['cycle']} {write['port']} write {write['address']:>3} = {write['data']:b}  "
                     f"clk->WL {write['clk_to_wl_ps']} ps")  # fmt: skip
    lines.append(
        f"  final state of {verdict['cells_checked']} cells: {len(verdict['cells_wrong'])} wrong"
    )
    lines += [f"      address {w['address']} bit {w['bit']}: {w['volts']} V, expected {w['expected']}"
              for w in verdict["cells_wrong"][:8]]  # fmt: skip
    if verdict["unproven"]:
        lines.append("  never proven: " + ", ".join(verdict["unproven"]))
    if verdict.get("supply"):
        worst = verdict["supply"]["min_rail_V"]
        lines.append(f"  supply ({verdict['supply']['strap_ohms']:g} ohm a tile): narrowest rail-to-rail "
                     f"{min(worst.values()):.3f} V at the middle tile of {min(worst, key=worst.get)}")
    for hazard in verdict["hazards"]:
        lines.append(
            f"  HAZARD cycle {hazard['cycle']} port {hazard['port']}: {hazard['what']} ({hazard['volts']} V)"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("result_dir", type=Path)
    parser.add_argument(
        "--period", type=float, default=2e-9, help="Clock period in seconds."
    )
    parser.add_argument("--vdd", type=float, default=0.7)
    parser.add_argument("--corner", choices=("TT", "SS", "FF"), default="TT")
    parser.add_argument(
        "--netlist",
        type=Path,
        default=None,
        help="Simulate this netlist instead of <cell>.sp.",
    )
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--spaced", action="store_true", help="An idle cycle after every write."
    )
    parser.add_argument("--timeout", type=float, default=7200,
                        help="Seconds Xyce may take (a large macro takes days).")
    parser.add_argument("--strap-ohms", type=float, default=0.0,
                        help="supply resistance between neighbouring column tiles (6T 4:1: ~135); 0 = ideal")
    parser.add_argument("--probe", action="append", default=[], metavar="NODE",
                        help="Also print V(Xdut:NODE), e.g. XDATA_LO:X0_0:XIO_A:X_block:SA (repeatable).")
    args = parser.parse_args(argv)
    tag = "_spaced" if args.spaced else ""
    out = (
        args.out
        or REPO_ROOT / "tmp" / f"simulate_{args.result_dir.resolve().name}{tag}"
    )
    verdict = simulate(args.result_dir, out, period=args.period, vdd=args.vdd, corner=args.corner,
                       netlist=args.netlist, spaced=args.spaced, timeout=args.timeout,
                       probes=tuple(args.probe), strap_ohms=args.strap_ohms)  # fmt: skip
    print(describe(verdict))
    print(f"wrote {out / 'simulation.json'}")
    return 0 if verdict["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
