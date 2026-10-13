#!/bin/bash
# Smoke test for scripts/def_to_gds.py (the KLayout DEF -> merged GDS
# stream-out used by the open-source flow).  Streams a minimal two-instance
# ASAP7 DEF through the real technology LEF and standard-cell GDS, then
# validates the resulting GDS hierarchy.
#
# Skips (exit 77) when the KLayout Python bindings are unavailable, so the
# test suite stays green on machines without KLayout.
set -u
cd "$(dirname "$0")/.."

if ! python3 -c "import sys; sys.path.insert(0, 'scripts'); from def_to_gds import _load_klayout_db; _load_klayout_db()" 2>/dev/null; then
    echo "SKIP: klayout Python bindings not available"
    exit 77
fi

OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT

python3 scripts/def_to_gds.py \
    --def-file tests/fixtures/smoke.def \
    --tech-lef tech/lef/asap7_tech.lef \
    --cell-lef tech/lef/asap7sc7p5t_28_R.lef \
    --macro-gds tech/gds/asap7sc7p5t_28_R_220121a.gds \
    --output-gds "$OUT/ctrl_decode_smoke.gds" \
    --top-cell ctrl_decode_smoke || exit 1

[ -s "$OUT/ctrl_decode_smoke.gds" ] || { echo "FAIL: empty GDS output"; exit 1; }

python3 - "$OUT/ctrl_decode_smoke.gds" <<'EOF' || exit 1
import sys
sys.path.insert(0, "scripts")
from def_to_gds import _load_klayout_db
kdb = _load_klayout_db()

layout = kdb.Layout()
layout.read(sys.argv[1])
top = layout.cell("ctrl_decode_smoke")
assert top is not None, "top cell missing from streamed GDS"
instances = sum(1 for _ in top.each_inst())
assert layout.cells() > 1, "standard-cell GDS substitution failed"
assert instances >= 2, f"expected >=2 placed instances, found {instances}"
# Routed wires stream as drawing (0); DEF pin rectangles keep their pin
# purpose (251).
for layer in (40, 50):
    assert not top.begin_shapes_rec(layout.layer(layer, 0)).at_end(), f"no routed M{layer // 10} wire in output"
    assert not top.begin_shapes_rec(layout.layer(layer, 251)).at_end(), f"no M{layer // 10} pin geometry in output"
print(f"PASS: cells={layout.cells()} instances={instances} bbox={top.dbbox()}")
EOF

echo "DEF-TO-GDS SMOKE CHECK PASSED"
