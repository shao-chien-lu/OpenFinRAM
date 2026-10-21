#!/usr/bin/env python3
"""Static noise margin from a Xyce DC butterfly sweep.

Reads the `.prn` written by `.print dc` for tests/spice/asap7_8t_snm.sp and
reports the SNM in millivolts.

Why the square, and where the geometry comes from
-------------------------------------------------
SNM is the side of the largest axis-aligned square that fits inside a lobe of
the butterfly.  The usual recipe is "rotate the curves 45 degrees and take the
maximum separation", which is correct but leaves a factor of sqrt(2) that is
easy to get backwards.  This implementation avoids the ambiguity by working
the geometry directly:

An axis-aligned square with corners (x0, y0) and (x0+s, y0+s) has its diagonal
along the direction (1, 1), i.e. along a line ``y = x + c``.  The largest such
square inscribed in a lobe has both of those corners on the lobe's boundary --
one on each curve.  So for each 45-degree line ``y = x + c``:

    find where each curve crosses that line,
    the two crossings are the square's opposite corners,
    and the side is simply the difference in x (equivalently in y),
    because both points satisfy y = x + c.

No sqrt(2) appears at all: the side is a coordinate difference, not a distance.
SNM is then the largest such side, and because a cell has two lobes and fails
at whichever is weaker, the reported value is the smaller of the two.

`_self_test` checks this against a case whose answer is known exactly: a pair
of infinite-gain inverters, whose lobe is the square [0, VDD/2] x [VDD/2, VDD]
and whose SNM is therefore exactly VDD/2.
"""
from __future__ import annotations

import argparse
import html
import json
import math
from pathlib import Path
import sys


def read_prn(path: str) -> tuple[list[float], list[float], list[float]]:
    """Return (sweep, qaout, qbout) from a Xyce .print dc output.

    The format is a header naming the columns, whitespace-separated rows, and a
    trailing "End of Xyce(TM) Simulation" line.  Columns are located by name so
    a reordered .print does not silently transpose the curves.
    """
    rows: list[list[float]] = []
    header: list[str] = []
    complete = False
    with open(path) as handle:
        for line in handle:
            fields = line.split()
            if not fields:
                continue
            if fields[0] == "Index":
                if header:
                    raise ValueError(f"{path}: multiple sweep headers")
                header = fields
                continue
            if not header:
                continue
            if fields[0].lower().startswith("end"):
                complete = True
                break
            try:
                row = [float(v) for v in fields]
            except ValueError as exc:
                raise ValueError(f"{path}: invalid sweep row: {line.strip()}") from exc
            if len(row) != len(header) or not all(math.isfinite(v) for v in row):
                raise ValueError(f"{path}: malformed or non-finite sweep row")
            rows.append(row)
    if len(rows) < 3 or not complete:
        raise ValueError(f"{path}: incomplete sweep")

    def column(name: str) -> list[float]:
        try:
            index = header.index(name)
        except ValueError as exc:
            raise ValueError(
                f"{path}: no column {name!r} in {header}") from exc
        return [row[index] for row in rows]

    sweep = column("V(SW)")
    if any(b <= a for a, b in zip(sweep, sweep[1:])):
        raise ValueError(f"{path}: sweep must be strictly increasing")
    return sweep, column("V(QAOUT)"), column("V(QBOUT)")


def _crossing_x(curve: list[tuple[float, float]], c: float) -> float | None:
    """Where the polyline crosses the 45-degree line y = x + c, as an x value.

    Returns None when the line misses the curve. The caller requires a
    monotonic VTC, so the crossing is unique.
    """
    best: float | None = None
    for (x0, y0), (x1, y1) in zip(curve, curve[1:]):
        d0 = (y0 - x0) - c
        d1 = (y1 - x1) - c
        if d0 == 0.0:
            candidate = x0
        elif d1 == 0.0:
            candidate = x1
        elif d0 * d1 < 0.0:
            # Linear interpolation along the segment to the sign change.
            t = d0 / (d0 - d1)
            candidate = x0 + t * (x1 - x0)
        else:
            continue
        if best is None:
            best = candidate
    return best


def snm(curve1: list[tuple[float, float]],
        curve2: list[tuple[float, float]]) -> tuple[float, float, float]:
    """Return (snm, lobe_a, lobe_b) in volts for monotonic butterfly curves.

    For piecewise-linear curves, x(c) and their difference are piecewise
    linear. Their extrema occur at vertex offsets c=y-x, including the
    common domain endpoints. Evaluating those offsets avoids an additional
    diagonal-grid quantisation error; DC sweep interpolation error remains.
    """
    for curve in (curve1, curve2):
        if len(curve) < 2 or not all(math.isfinite(v) for pt in curve for v in pt):
            raise ValueError("butterfly requires finite curves with at least two points")
        ordered = curve if curve[0][0] <= curve[-1][0] else curve[::-1]
        # Allow 10 nV rail noise on either axis (including the reflected VTC),
        # far below the 1 mV sweep-convergence bound. Reject real reversals.
        if any(x1 < x0 - 1e-8 or y1 > y0 + 1e-8 or (x0, y0) == (x1, y1)
               for (x0, y0), (x1, y1) in zip(ordered, ordered[1:])):
            raise ValueError("butterfly VTC must be monotonically decreasing")
    cs = sorted({y - x for x, y in curve1 + curve2})
    lobe_a = 0.0
    lobe_b = 0.0
    for c in cs:
        x1 = _crossing_x(curve1, c)
        x2 = _crossing_x(curve2, c)
        if x1 is None or x2 is None:
            continue
        # Both crossings sit on y = x + c, so the side of the square whose
        # diagonal joins them is exactly this coordinate difference.
        side = x1 - x2
        if side > lobe_a:
            lobe_a = side
        if -side > lobe_b:
            lobe_b = -side
    return min(lobe_a, lobe_b), lobe_a, lobe_b


def write_svg(path: str, curve1: list[tuple[float, float]],
              curve2: list[tuple[float, float]], vdd: float, value: float,
              title: str) -> None:
    """Dependency-free butterfly plot with equal voltage scales on both axes."""
    def points(curve: list[tuple[float, float]]) -> str:
        return " ".join(f"{65 + x / vdd * 440:.3f},{505 - y / vdd * 440:.3f}"
                        for x, y in curve)

    ticks = []
    for i in range(5):
        p, label = 65 + 110 * i, f"{vdd * i / 4:.3f}"
        ticks.append(f'<path d="M {p} 65 V 505 M 65 {570-p} H 505" stroke="#ddd"/>'
                     f'<text x="{p}" y="525" text-anchor="middle">{label}</text>'
                     f'<text x="58" y="{574-p}" text-anchor="end">{label}</text>')
    Path(path).write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="570" height="590" '
        'viewBox="0 0 570 590"><rect width="570" height="590" fill="white"/>'
        '<g font-family="sans-serif" font-size="12" fill="#222">'
        f'<text x="285" y="24" text-anchor="middle">{html.escape(title)}</text>'
        f'<text x="285" y="44" text-anchor="middle">SNM = {value*1000:.3f} mV</text>'
        + "".join(ticks)
        + '<path d="M 65 65 V 505 H 505" fill="none" stroke="#222"/>'
        + f'<polyline points="{points(curve1)}" fill="none" stroke="#1767ad" stroke-width="2"/>'
        + f'<polyline points="{points(curve2)}" fill="none" stroke="#c55224" stroke-width="2"/>'
        + '<text x="285" y="550" text-anchor="middle">Q (V)</text>'
        + '<text transform="translate(15 285) rotate(-90)" text-anchor="middle">QB (V)</text>'
        + '<text x="285" y="575" text-anchor="middle">Blue: QB=f(Q); orange: Q=g(QB)</text>'
        + '</g></svg>\n')


def _self_test() -> None:
    """Infinite-gain inverters: the lobe is a square of side VDD/2.

    curve1 is the ideal VTC (output high until the input passes VDD/2, then
    low); curve2 is the same inverter measured the other way, i.e. curve1
    mirrored about y = x.  The upper-left lobe is exactly
    [0, VDD/2] x [VDD/2, VDD], so the answer must be VDD/2.
    """
    vdd = 0.7
    half = vdd / 2.0
    curve1 = [(0.0, vdd), (half, vdd), (half, 0.0), (vdd, 0.0)]
    curve2 = [(y, x) for x, y in curve1]
    value, lobe_a, lobe_b = snm(curve1, curve2)
    if abs(value - half) > 1e-3:
        raise AssertionError(
            f"ideal butterfly should give SNM = VDD/2 = {half:.4f} V, "
            f"got {value:.4f} V (lobes {lobe_a:.4f}, {lobe_b:.4f})")

    # A degenerate butterfly -- both inverters identical and linear, so the
    # curves coincide -- has no lobe at all and must report zero rather than
    # some artifact of the grid.
    line = [(0.0, vdd), (vdd, 0.0)]
    value, _, _ = snm(line, line)
    if abs(value) > 1e-6:
        raise AssertionError(
            f"coincident curves should give SNM = 0, got {value:.6f} V")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prn", nargs="?", help="Xyce .print dc output")
    parser.add_argument("--self-test", action="store_true",
                        help="check the square fit against known geometry")
    parser.add_argument("--symmetry-tol", type=float, default=1e-6,
                        help="max |QAOUT - QBOUT| before the cell is called "
                             "asymmetric (volts)")
    parser.add_argument("--prefix", default="",
                        help="prepended to every emitted name, so several "
                             "configurations stay distinguishable in one log")
    parser.add_argument("--vdd", type=float, help="require a complete 0-to-VDD sweep")
    parser.add_argument("--step", type=float, help="require this uniform sweep step (V)")
    parser.add_argument("--json", help="write full-precision measurements as JSON")
    parser.add_argument("--svg", help="write a butterfly plot as SVG")
    args = parser.parse_args(argv)

    if args.self_test:
        _self_test()
        print("PASS snm_from_sweep self-test")
        return 0

    if not args.prn:
        parser.error("a .prn path is required unless --self-test is given")

    sweep, qaout, qbout = read_prn(args.prn)
    if args.vdd is not None:
        if not math.isfinite(args.vdd) or args.vdd <= 0:
            parser.error("--vdd must be finite and positive")
        if abs(sweep[0]) > 1e-8 or abs(sweep[-1] - args.vdd) > 1e-8:
            raise ValueError("sweep does not cover 0 through VDD")
    if args.step is not None:
        if not math.isfinite(args.step) or args.step <= 0:
            parser.error("--step must be finite and positive")
        if any(abs(b-a-args.step) > 1e-8 for a, b in zip(sweep, sweep[1:])):
            raise ValueError("sweep has missing points or an unexpected step")
    if not math.isfinite(args.symmetry_tol) or args.symmetry_tol < 0:
        parser.error("--symmetry-tol must be finite and nonnegative")

    # Forcing Q and forcing QB must give the same response on a symmetric
    # nominal cell. This is a separate regression check, not a requirement of
    # the SNM method: mismatched inverters can legitimately have unequal lobes.
    worst = max(abs(a - b) for a, b in zip(qaout, qbout))
    if worst > args.symmetry_tol:
        print(f"FAIL: cell is asymmetric, max |QAOUT - QBOUT| = {worst:.6f} V",
              file=sys.stderr)
        return 1

    # curve1 forces Q and observes QB; curve2 is the same inverter measured in
    # the other direction, so its coordinates are swapped.
    curve1 = list(zip(sweep, qaout))
    curve2 = [(y, x) for x, y in zip(sweep, qbout)]

    value, lobe_a, lobe_b = snm(curve1, curve2)
    p = args.prefix
    print(f"{p}SNM_MV = {value * 1000:.3f}")
    print(f"{p}SNM_LOBE_A_MV = {lobe_a * 1000:.3f}")
    print(f"{p}SNM_LOBE_B_MV = {lobe_b * 1000:.3f}")
    print(f"{p}SYMMETRY_UV = {worst * 1e6:.3f}")
    if args.json:
        Path(args.json).write_text(json.dumps({
            "source": str(Path(args.prn).resolve()), "snm_mv": value * 1000,
            "lobe_a_mv": lobe_a * 1000, "lobe_b_mv": lobe_b * 1000,
            "symmetry_uv": worst * 1e6, "samples": len(sweep),
            "max_step_v": max(b-a for a, b in zip(sweep, sweep[1:])),
        }, indent=2, allow_nan=False) + "\n")
    if args.svg:
        write_svg(args.svg, curve1, curve2, args.vdd or sweep[-1], value, Path(args.prn).name)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except (ValueError, OSError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
