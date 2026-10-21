"""Check delivered GDS/LEF/Liberty/SPICE agree on a routed 2RW macro."""

import argparse
import json
from pathlib import Path
import re
import sys

import gdstk

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from compile_asap7_2rw import check_route_drc, verify


def check(folder):
    (report_path,) = folder.glob("*.physical.json")
    report = json.loads(report_path.read_text())
    name = report["cell"]
    work = Path(report["reports"])
    manifest = json.loads((work / "connectivity.json").read_text())
    verify(folder / f"{name}.gds", manifest)
    check_route_drc(work / "macro_drc.rpt")
    check_route_drc(
        Path(str(work).replace("macro_2rw_", "openroad_")) / "detailed_route_drc.rpt"
    )
    lef = (folder / f"{name}.lef").read_text()
    pins = set(re.findall(r"^\s+PIN (\S+)", lef, re.M))
    assert pins == set(manifest["external"]), (
        "LEF pin interface differs from physical ports"
    )
    obstruction = lef.split("  OBS")[1]
    blocked = {int(layer) for layer in re.findall(r"LAYER M(\d) ;", obstruction)}
    # Every layer the macro routes on is blocked; M8/M9 stay the chip's, whose
    # grid lands on the macro's M7 supply stripes.
    assert blocked == set(range(1, 8)), f"macro LEF blocks M{sorted(blocked)}, not M1-M7"
    size = re.search(r"SIZE ([\d.]+) BY ([\d.]+)", lef)
    assert all(
        abs(float(size[i + 1]) - v) < 1e-6 for i, v in enumerate(report["size_um"])
    )
    liberty = (folder / f"{name}.lib").read_text()
    for pin in ("we_n_A", "we_n_B"):
        assert f"pin ({pin})" in liberty
    for bus in ("D_A", "D_B", "Q_A", "Q_B", "A_A", "A_B"):
        assert f"bus ({bus})" in liberty
    assert "contention_condition" in liberty

    def include(path):
        text = path.read_text()
        for inc in re.findall(r'^\.INCLUDE "([^"]+)"', text, re.M | re.I):
            text += "\n" + include(path.parent / inc)
        return text

    spice = include(folder / f"{name}.sp")
    # Older delivered decks keep the standard cells only in the flat deck.
    if not re.search(r"^\.SUBCKT INVx1_ASAP7", spice, re.M | re.I):
        spice += (
            "\n"
            + (
                Path(__file__).resolve().parents[2] / "tech/cdl/asap7sc7p5t_28_R.cdl"
            ).read_text()
        )
    spice = re.sub(r"\n\s*\+", " ", spice)
    subckts, body = {}, None
    for line in spice.splitlines():
        fields = line.lower().split()
        if not fields or fields[0].startswith("*"):
            continue
        if fields[0] == ".subckt":
            assert fields[1] not in subckts, f"duplicate subckt {fields[1]}"
            body = {"ports": fields[2:], "instances": []}
            subckts[fields[1]] = body
        elif fields[0] == ".ends":
            body = None
        elif fields[0].startswith("x") and body is not None:
            body["instances"].append(fields)
    assert set(subckts[name]["ports"]) == {p.lower() for p in pins}
    assert len(subckts["ctrl_decode"]["instances"]) > 30, "empty/stub controller"
    for subckt, data in subckts.items():
        for instance in data["instances"]:
            assert instance[-1] in subckts, f"{subckt}: unresolved {instance[-1]}"
            assert len(instance) - 2 == len(subckts[instance[-1]]["ports"]), (
                f"{subckt}: arity {instance}"
            )

    def devices(cell):
        if cell == "sram_cell_8t":
            return 1
        return sum(devices(inst[-1]) for inst in subckts[cell]["instances"])

    expected = 8 * report["wordlines_per_half"] * report["bits"] * report["banks"]
    assert devices(name) == expected, "SPICE storage-cell count mismatch"
    lib = gdstk.read_gds(str(folder / f"{name}.gds"))

    # The bitcell and its mirrored-slot variants (generate_asap7_wordline_arrays).
    storage = {"sram_cell_8t", "sram_cell_8t_b", "sram_cell_8t_b_end"}

    def cells(cell):
        if cell.name in storage:
            return 1
        return sum(
            cells(ref.cell) * max(1, ref.repetition.size) for ref in cell.references
        )

    assert cells(next(c for c in lib.cells if c.name == name)) == expected, (
        "GDS storage-cell count mismatch"
    )
    print(
        f"PASS {name}: {expected} storage cells, {len(pins)} pins, both write ports; GDS/LEF/Liberty/SPICE agree"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    check(parser.parse_args().folder)
