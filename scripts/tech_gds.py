#!/usr/bin/env python3
"""Generate the compiler's ASAP7 cell libraries into tech/gds and check them by digest.

The 8T bitcell family, the wordline arrays, the 8T and 6T IO blocks and the
wordline driver slices are generator output, not sources: the build writes
them (CMake target ``tech_gds``) and git tracks only their digests,
``tech/gds/digests.json``.  A digest covers each cell's geometry - polygons,
labels and references at 0.01 nm - not the file's bytes, which a generator
may order differently for the same layout.

    scripts/tech_gds.py                  generate every library, compare digests
    scripts/tech_gds.py --if-missing     generate only when one is missing
    scripts/tech_gds.py --check A.gds..  compare files against their libraries' digests
    scripts/tech_gds.py --update         record the digests of tech/gds's libraries

Needs gdstk and chipforge_asap7 (the IO blocks' and driver slices' devices).
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
GDS = REPO / "tech/gds"
DIGESTS = GDS / "digests.json"

#: Generators in dependency order, the libraries each writes to tech/gds,
#: and whether it also writes SPICE (sent to scratch; tech/spice is tracked).
STEPS = (
    ("generate_asap7_8t_bitcell.py", ("sram_cell_8t", "sram_cell_8t_edges", "sram_cell_8t_tap"), False),
    ("generate_asap7_wordline_arrays.py", ("sram_wordline_arrays",), False),
    ("generate_asap7_8t_iocolumn.py", ("sram_8t_iocolumn",), True),
    ("generate_asap7_8t_wl_slices.py", ("sram_8t_wl_slices",), True),
    ("generate_asap7_6t_iocolumn.py", ("sram_6t_iocolumn",), True),
)
LIBRARIES = tuple(name for _, names, _ in STEPS for name in names)


def digest(path: Path) -> str:
    """SHA-256 of a GDS file's geometry, independent of record order."""
    import gdstk

    def q(value):
        return round(value * 1e5)

    cells = []
    for cell in gdstk.read_gds(str(path)).cells:
        polygons = sorted((p.layer, p.datatype, [(q(x), q(y)) for x, y in p.points]) for p in cell.polygons)
        labels = sorted((l.text, l.layer, l.texttype, q(l.origin[0]), q(l.origin[1])) for l in cell.labels)
        references = sorted(
            (r.cell.name if hasattr(r.cell, "name") else str(r.cell), q(r.origin[0]), q(r.origin[1]),
             round(r.rotation or 0, 9), r.magnification or 1, bool(r.x_reflection),
             [(q(x), q(y)) for x, y in r.repetition.get_offsets()] if r.repetition is not None
             and r.repetition.size else [])
            for r in cell.references
        )
        cells.append((cell.name, polygons, labels, references))
    cells.sort(key=lambda cell: cell[0])
    return hashlib.sha256(json.dumps(cells, separators=(",", ":")).encode()).hexdigest()


def generate(python: str) -> None:
    with tempfile.TemporaryDirectory() as scratch:
        for script, names, spice in STEPS:
            command = [python, str(REPO / "scripts" / script), "--output", str(GDS / f"{names[0]}.gds")]
            if spice:
                command += ["--spice-output", str(Path(scratch) / f"{names[0]}.sp")]
            print(f"tech_gds: {script} -> {', '.join(n + '.gds' for n in names)}", flush=True)
            subprocess.run(command, check=True, cwd=REPO, stdout=subprocess.DEVNULL)
        # The arrays' IO pitch check needs the IO columns, which need the arrays.
        subprocess.run([python, str(REPO / "scripts/generate_asap7_wordline_arrays.py"),
                        "--verify", str(GDS / "sram_wordline_arrays.gds")],
                       check=True, cwd=REPO, stdout=subprocess.DEVNULL)


def check(paths: list[Path]) -> int:
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        print(f"SKIP: not generated (build the tech_gds target): {' '.join(missing)}", file=sys.stderr)
        return 77
    recorded = json.loads(DIGESTS.read_text())
    stale = []
    for path in paths:
        name = path.stem
        if name not in recorded:
            stale.append(f"{path}: no recorded digest for {name}")
        elif digest(path) != recorded[name]:
            stale.append(f"{path}: differs from the recorded {name}")
    if stale:
        print("FAIL: generated geometry changed:\n  " + "\n  ".join(stale), file=sys.stderr)
        print("If the change is intended, regenerate and record it:\n"
              "  scripts/tech_gds.py && scripts/tech_gds.py --update", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--if-missing", action="store_true", help="generate only when a library is missing")
    mode.add_argument("--check", nargs="+", type=Path, metavar="GDS",
                      help="compare these files against the digests of the libraries they are named after")
    mode.add_argument("--update", action="store_true", help="record the digests of tech/gds's libraries")
    parser.add_argument("--python", default=sys.executable, help="interpreter for the generators")
    args = parser.parse_args()
    if args.check:
        return check(args.check)
    if args.update:
        DIGESTS.write_text(json.dumps({name: digest(GDS / f"{name}.gds") for name in LIBRARIES},
                                      indent=2) + "\n")
        print(f"wrote {DIGESTS.relative_to(REPO)}")
        return 0
    if args.if_missing and all((GDS / f"{name}.gds").is_file() for name in LIBRARIES):
        return 0
    generate(args.python)
    # A digest change is not a build failure: a generator being edited
    # changes its output.  The tests hold the line.
    if check([GDS / f"{name}.gds" for name in LIBRARIES]):
        print("tech_gds: WARNING: generated libraries differ from tech/gds/digests.json", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
