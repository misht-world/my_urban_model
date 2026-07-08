"""Тесты советующего слоя (v0.14.1)."""

from __future__ import annotations

import pytest

from urban_model import solve_max_kit
from urban_model.models import CalculationOptions, Site
from urban_model.models.parking import ParkingConfig
from urban_model.normatives import load_normatives
from urban_model.optimize.advice import Advice, build_advice


@pytest.fixture(scope="module")
def norms():
    return load_normatives("spb")


def test_advice_returns_sorted(norms):
    site = Site(area_m2=100_000)
    o = CalculationOptions(
        floors=12, planning_doc=True,
        parking=ParkingConfig(mode="custom", open_share=0.5, multilevel_share=0.5,
                              underground_share=0.0, multilevel_levels=5),
    )
    bt = solve_max_kit(site, o, norms)
    adv = build_advice(site, o, norms, base_apartments=bt.apartments_area.value)
    assert len(adv) <= 5
    assert all(isinstance(a, Advice) for a in adv)
    # отсортированы по эффекту (убыв.)
    gains = [a.gain_pct for a in adv]
    assert gains == sorted(gains, reverse=True)
    # каждый совет — непустой текст с направлением («вместо»)
    for a in adv:
        assert "вместо" in a.text


def test_advice_no_crash_small_site(norms):
    site = Site(area_m2=15_000)
    o = CalculationOptions(floors=9, planning_doc=True)
    adv = build_advice(site, o, norms)
    assert isinstance(adv, list)


def test_advice_guard_skips_mismatched_base(norms):
    """При заведомо чужой базе (guard 5%) сканы отбрасываются молча."""
    site = Site(area_m2=100_000)
    o = CalculationOptions(floors=12, planning_doc=True)
    adv = build_advice(site, o, norms, base_apartments=1.0)  # абсурдная база
    assert adv == []
