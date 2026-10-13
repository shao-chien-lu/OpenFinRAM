#!/usr/bin/env python3
"""Convert mapped Verilog using Yosys parsing and the actual CDL pin order.

Only structural mapped cells are accepted. Unknown cells, unknown constants,
and missing required inputs are errors, never empty controller stubs.
"""

import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile


def port_nodes(module):
    """Expand ports, including one-bit vectors preserved as Yosys attributes."""
    for name, info in module["ports"].items():
        bits = info["bits"]
        attributes = module.get("netnames", {}).get(name, {}).get("attributes", {})
        vector = (
            len(bits) > 1
            or "offset" in info
            or "upto" in info
            or bool(int(attributes.get("single_bit_vector", "0"), 2))
        )
        for i, bit in enumerate(bits):
            index = info.get("offset", 0) + (
                len(bits) - 1 - i if info.get("upto") else i
            )
            yield (f"{name}[{index}]" if vector else name), bit


def convert(design, cdl, top="ctrl_decode"):
    signatures = {}
    logical = re.sub(r"\n\s*\+", " ", cdl)
    for line in logical.splitlines():
        tokens = line.split()
        if tokens and tokens[0].lower() == ".subckt":
            signatures[tokens[1]] = tokens[2:]
    module = design["modules"][top]
    if not module["cells"]:
        raise RuntimeError("empty controller netlist")
    names = {"0": "VSS", "1": "VDD"}
    ports, aliases = [], []
    for port, bit in port_nodes(module):
        ports.append(port)
        if bit in names and names[bit] != port:
            aliases.append((port, names[bit]))
        else:
            names[bit] = port

    def node(bit):
        if bit not in names:
            if not isinstance(bit, int):
                raise RuntimeError(f"unsupported constant {bit}")
            names[bit] = f"net_{bit}"
        return names[bit]

    lines = [
        "* Routed mapped controller; Yosys structural parse, CDL pin order.",
        f".SUBCKT {top} "
        + " ".join(ports + [pin for pin in ("VDD", "VSS") if pin not in ports]),
    ]
    for i, (a, b) in enumerate(aliases):
        lines.append(f"Valias{i} {a} {b} 0")
    for index, (name, cell) in enumerate(module["cells"].items()):
        kind = cell["type"]
        if (
            kind
            in ("FILLER_ASAP7_75t_R", "FILLERxp5_ASAP7_75t_R", "TAPCELL_ASAP7_75t_R")
            and not cell["connections"]
        ):
            continue  # Device-free process continuity, not an electrical cell.
        if kind not in signatures:
            raise RuntimeError(f"{name}: no CDL definition for {kind}")
        conns = {pin.upper(): bits for pin, bits in cell["connections"].items()}
        if len(conns) != len(cell["connections"]):
            raise RuntimeError(f"{name}: case-aliased formal pins")
        if set(conns) - {p.upper() for p in signatures[kind]}:
            raise RuntimeError(f"{name}: unknown formal pins")
        nodes = []
        for pin in signatures[kind]:
            pin = pin.upper()  # CDL RESETn/SETn versus Liberty RESETN/SETN.
            if pin not in conns and pin.upper() in ("VDD", "VSS"):
                nodes.append(pin.upper())
            elif pin not in conns and any(
                p.upper() == pin and info["direction"] == "output"
                for p, info in design["modules"].get(kind, {}).get("ports", {}).items()
            ):
                nodes.append(f"NC_{index}_{pin}")  # CTS dummy-load output.
            elif pin not in conns or len(conns[pin]) != 1:
                raise RuntimeError(f"{name}: missing/non-scalar pin {pin}")
            else:
                nodes.append(node(conns[pin][0]))
        lines.append(f"* {name}")
        lines.append(f"X{index} " + " ".join(nodes) + f" {kind}")
    lines.append(f".ENDS {top}")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verilog", type=Path, required=True)
    parser.add_argument("--cdl", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as scratch:
        path = Path(scratch)
        script = path / "parse.ys"
        liberty = sorted((args.cdl.resolve().parent.parent / "lib").glob("*.lib"))
        script.write_text(
            "".join(
                f'read_liberty -lib -ignore_miss_func -ignore_miss_data_latch "{p}"\n'
                for p in liberty
            )
            + f'read_verilog "{args.verilog.resolve()}"\nwrite_json "{path}/mapped.json"\n'
        )
        subprocess.run(
            ["yosys", "-Q", "-T", "-s", str(script)],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        design = json.loads((path / "mapped.json").read_text())
        # Preserve one-bit bus declarations which Yosys JSON otherwise
        # represents identically to scalars (e.g. bank select [0:0]).
        source = args.verilog.read_text()
        for name in re.findall(
            r"(?:input|output|inout)\s+\[\s*0\s*:\s*0\s*\]\s+(\w+)\s*;", source
        ):
            design["modules"]["ctrl_decode"]["ports"][name]["offset"] = 0
        content = convert(design, args.cdl.read_text())
        args.output.write_text(content)
        print(
            f"Converted {len(design['modules']['ctrl_decode']['cells'])} controller cells to {args.output}"
        )


if __name__ == "__main__":
    main()
