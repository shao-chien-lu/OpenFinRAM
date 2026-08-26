#!/usr/bin/env python3
"""Convert Xyce table3 .measure output into an OpenFinRAM characterization JSON.

Bridges scripts/characterize_read.py --mode table3 (real read-path transient
through the sense amp at the MOST-report stimulus point) to the emitter's
--liberty-from path, so measured clk->Q numbers land in the .lib instead of
the estimated constants.

The tables carry a SINGLE measured point (1x1 delay matrix, one transition
value per direction) tagged with its stimulus conditions: no interpolation is
claimed. Swept slew x load grids are P2 work; this file only proves the
measured-value plumbing end to end.

Usage:
    python3 scripts/gen_characterization_json.py \
        --xyce-mt0 /tmp/ofr_charread/table3_d256.sp.mt0 \
        --depth-words 256 --output /tmp/char_d256.json
"""

import argparse
import json
import re
import sys
from pathlib import Path

# The table3 stimulus point (matches TABLE3_SLEW_S / TABLE3_CQ_F in
# characterize_read.py).
STIMULUS_SLEW_NS = 0.04
STIMULUS_LOAD_PF = 0.04608


def parse_xyce(mt0: Path) -> dict[str, float]:
    """Xyce .measure results (seconds -> ns), same mapping as
    gen_table3_report.py."""
    text = mt0.read_text()

    def get(name: str) -> float | None:
        m = re.search(rf"^{name}\s*=\s*([-+\d.eE]+)", text, re.M | re.I)
        return float(m.group(1)) * 1e9 if m else None

    out: dict[str, float] = {}
    if (r := get("t_cell_rise")) is not None:
        out["cell_rise"] = r
    if (f := get("t_cell_fall")) is not None:
        out["cell_fall"] = f
    qr_hi, qr_lo = get("t_qr_hi"), get("t_qr_lo")
    qf_hi, qf_lo = get("t_qf_hi"), get("t_qf_lo")
    if None not in (qr_hi, qr_lo):
        out["rise_transition"] = abs(qr_hi - qr_lo)
    if None not in (qf_hi, qf_lo):
        out["fall_transition"] = abs(qf_lo - qf_hi)
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--xyce-mt0", type=Path, required=True,
                    help=".mt0 from characterize_read.py --mode table3")
    ap.add_argument("--depth-words", type=int, default=256,
                    help="bitline depth of the measurement (for provenance)")
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args(argv)

    m = parse_xyce(args.xyce_mt0)
    missing = [k for k in ("cell_rise", "cell_fall",
                           "rise_transition", "fall_transition") if k not in m]
    if missing:
        print(f"ERROR: {args.xyce_mt0} missing measures: {', '.join(missing)}",
              file=sys.stderr)
        return 1

    doc = {
        "schema": "openfinram-characterization-1",
        "source": (
            f"xyce transient, real ASAP7 BSIM-CMG, TT 0.70V 25C; "
            f"{args.depth_words}-word bitline depth; stimulus slew "
            f"{STIMULUS_SLEW_NS} ns / load {STIMULUS_LOAD_PF} pF "
            f"(MOST-report Table III point)"
        ),
        "comment": (
            "MEASURED XYCE TRANSIENT CHARACTERIZATION (TT, single stimulus "
            f"point, depth={args.depth_words}); NOT interpolated across "
            "slew/load"
        ),
        "timing": {
            "delay": {
                "index_1": [STIMULUS_SLEW_NS],
                "index_2": [STIMULUS_LOAD_PF],
                "cell_rise": [[m["cell_rise"]]],
                "cell_fall": [[m["cell_fall"]]],
                "rise_transition": [m["rise_transition"]],
                "fall_transition": [m["fall_transition"]],
            }
        },
    }
    args.output.write_text(json.dumps(doc, indent=2) + "\n")
    print(f"wrote {args.output}")
    for k, v in m.items():
        print(f"  {k:>16}: {v:.4f} ns")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
