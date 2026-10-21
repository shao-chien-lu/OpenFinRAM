"""The wordline-driver bench: its deck, then (with Xyce) what it is for."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import characterize_wl_driver as wl  # noqa: E402


def test_the_ladder_is_the_wire_and_a_preloaded_cell_per_segment():
    lines = wl.ladder(3, wl.WIRE_OHM_PER_UM["xact"])
    assert lines[0] == "Rw1 w0 w1 100.0385" and lines[1].startswith("Cw1 w1 0 9.2399e-17")
    assert sum(line.startswith("Xc") for line in lines) == 3
    assert ".IC V(Xc1:Q)=0.7 V(Xc1:QB)=0" in lines and ".IC V(Xc2:Q)=0 V(Xc2:QB)=0.7" in lines
    assert wl.WIRE_OHM_PER_UM["xact"] / wl.WIRE_OHM_PER_UM["setrc"] == pytest.approx(5.38, abs=0.01)


def test_the_library_path_is_the_compilers_and_loses_nothing_to_xyce():
    text = wl.deck("BUFx4", 2, Path("model.pm"), width=4e-10, ohm_per_um=31.287)
    assert "Xand in vdut vdut 0 en AND2x2_ASAP7_75t_R" in text and "Xbuf en vdut 0 w0 BUFx4_ASAP7_75t_R" in text
    assert "MM3 Y AN VSS VSS nmos_rvt l=20n nfin=12" in text  # W dropped, fins kept
    assert " w=" not in text.lower().split(".subckt sram_cell_8t")[0]


def test_a_sized_slice_is_faster_than_the_library_path_and_as_predicted(tmp_path: Path):
    pytest.importorskip("chipforge_asap7")
    try:
        wl.find_xyce()
    except FileNotFoundError as error:
        pytest.skip(str(error))
    results = wl.study([16, 128], tmp_path, buffers=("BUFx4", "BUFx24"), wire="setrc")
    for n, entry in results["lengths"].items():
        far = {kind: entry[kind]["far_rise_delay_ps"] for kind in ("ideal", "BUFx4", "BUFx24", "slice")}
        assert far["ideal"] < far["slice"] < far["BUFx24"] + 1 and far["slice"] < far["BUFx4"] - 10, (n, far)
        assert entry["slice"]["far_high_V"] > 0.69
        assert entry["wordline_fF"] / int(n) == pytest.approx(0.18, abs=0.01)
        # size_decoder, against Xyce into the same lumped load: close, and not optimistic.
        lumped = entry["slice_lumped"]["far_rise_delay_ps"]
        assert 0.7 * entry["predicted_ps"] < lumped < 1.05 * entry["predicted_ps"], (n, lumped)
    long = results["lengths"][128]
    assert long["BUFx24"]["near_rise_delay_ps"] > 20  # upsizing does not shorten the library path
    assert long["slice"]["energy_fJ"] > long["BUFx4"]["energy_fJ"]  # and the slice is not free
