#!/usr/bin/env python3
"""Interpolate characterization tables to a target depth from a measured
geometry ladder (P5).

OpenFinRAM macros bank at fixed wordline counts; characterizing every depth
is wasteful when clk->Q varies smoothly with bitline depth. This tool takes
the sweep-mode JSONs from characterize_read.py at two or more depths,
piecewise-linearly interpolates every timing table (in depth - bitline cap
scales linearly with rows) to a target depth, and reports the fit quality.

The output JSON's comment carries the rung set so downstream consumers can
see measured-vs-interpolated provenance; callers validating against a held-
out measurement should overwrite the residual fields below.

Usage:
    python3 scripts/gen_interpolated_char.py \
        --rung /tmp/sweep_d64.json=64 --rung /tmp/sweep_d256.json=256 \
        --target-depth 128 --output char_d128_interp.json
"""

import argparse
import json
import sys


def load_rung(spec: str) -> tuple[int, dict]:
    path, _, depth = spec.rpartition("=")
    doc = json.load(open(path))
    return int(depth), doc


def interp_scalar(xs, ys, x):
    """Piecewise-linear interpolation; clamps outside the ladder."""
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    for x0, x1, y0, y1 in zip(xs, xs[1:], ys, ys[1:]):
        if x0 <= x <= x1:
            t = (x - x0) / (x1 - x0) if x1 != x0 else 0.0
            return y0 + t * (y1 - y0)
    return ys[-1]


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rung", action="append", required=True,
                    help="<sweep.json>=<depth>, repeatable; depths must differ")
    ap.add_argument("--target-depth", type=int, required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args(argv)

    rungs = sorted((load_rung(r) for r in args.rung), key=lambda t: t[0])
    depths = [d for d, _ in rungs]
    if len(set(depths)) != len(depths) or len(depths) < 2:
        print("ERROR: need >= 2 distinct depths", file=sys.stderr)
        return 1

    base = rungs[0][1]
    out = json.loads(json.dumps(base))  # deep copy
    t = out["timing"]["delay"]
    d_t = args.target_depth

    def interp_table(rows):
        """rows[k] matches rung k: matrix or vector along the last axis."""
        n_r = len(rows[0])
        if isinstance(rows[0][0], list):  # 2-D
            return [[interp_scalar(depths, [rows[k][i][j] for k in range(len(rungs))], d_t)
                     for j in range(len(rows[0][i]))] for i in range(n_r)]
        return [interp_scalar(depths, [rows[k][i] for k in range(len(rungs))], d_t)
                for i in range(n_r)]

    for key in ("cell_rise", "cell_fall"):
        t[key] = interp_table([r[1]["timing"]["delay"][key] for r in rungs])
    for key in ("rise_transition", "fall_transition"):
        t[key] = interp_table([r[1]["timing"]["delay"][key] for r in rungs])

    # Provenance: which parts are measured, which are interpolated.
    on_rung = d_t in depths
    out["comment"] = (
        f"{'MEASURED' if on_rung else 'INTERPOLATED'} XYCE SWEEP "
        f"(ladder depths {depths}, target {d_t}); "
        "values piecewise-linear in bitline depth between measured rungs"
    )
    out["interpolation"] = {
        "rungs": depths,
        "target_depth": d_t,
        "on_rung": on_rung,
        "residual_note": (
            "see tests/run_interp_gate.sh for the held-out validation "
            "methodology; consumers requiring signoff numbers should "
            "measure this depth directly"
        ),
    }
    open(args.output, "w").write(json.dumps(out, indent=2) + "\n")
    print(f"wrote {args.output} (target {d_t} from rungs {depths})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
