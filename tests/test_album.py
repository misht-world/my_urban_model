"""Тесты альбома-презентации (PPTX) — Фаза 1."""
from __future__ import annotations

import tempfile

import pytest

from urban_model import solve_max_kit
from urban_model.export.album import build_variant_album
from urban_model.export.album.narrative import economy_verdict, overall_verdict
from urban_model.export.album.risks import detect_risks
from urban_model.models import (
    CalculationOptions,
    KindergartenSpec,
    ParkingConfig,
    Site,
)
from urban_model.normatives import load_normatives


@pytest.fixture(scope="module")
def norms():
    return load_normatives("spb")


def _opts(**kw):
    base = dict(floors=9, residential_class="comfort")
    base.update(kw)
    return CalculationOptions(**base)


def _n_slides(path: str) -> int:
    from pptx import Presentation
    return len(Presentation(path).slides._sldIdLst)


def test_album_builds_12_slides(norms):
    opts = _opts(include_school=False,
                 kindergarten=KindergartenSpec(building_type="built_in"),
                 parking=ParkingConfig(mode="all_open"))
    r = solve_max_kit(Site(area_m2=50_000), opts, norms)
    p = tempfile.mktemp(suffix=".pptx")
    build_variant_album("тест", r, opts, p)
    assert _n_slides(p) == 12


def test_album_does_not_crash_on_small_site(norms):
    opts = _opts(floors=12)
    r = solve_max_kit(Site(area_m2=10_000), opts, norms)
    p = tempfile.mktemp(suffix=".pptx")
    build_variant_album("малый", r, opts, p)
    assert _n_slides(p) == 12


def test_album_without_economy(norms):
    opts = _opts(include_economy=False, include_school=False)
    r = solve_max_kit(Site(area_m2=50_000), opts, norms)
    p = tempfile.mktemp(suffix=".pptx")
    build_variant_album("без экономики", r, opts, p)
    assert _n_slides(p) == 12


def test_economy_verdict_profitable(norms):
    opts = _opts(residential_class="business", include_school=False,
                 kindergarten=KindergartenSpec(building_type="built_in"),
                 parking=ParkingConfig(mode="all_open"))
    r = solve_max_kit(Site(area_m2=50_000), opts, norms)
    assert r.economy.profit > 0
    assert "положительный" in economy_verdict(r).lower()


def test_overall_verdict_keys(norms):
    opts = _opts(include_school=False,
                 kindergarten=KindergartenSpec(building_type="built_in"),
                 parking=ParkingConfig(mode="all_open"))
    r = solve_max_kit(Site(area_m2=50_000), opts, norms)
    v = overall_verdict(r)
    for k in ("status", "status_color", "headline", "pros", "cons", "checks"):
        assert k in v
    assert isinstance(v["pros"], list) and v["pros"]


def test_risks_detect_underground_heavy(norms):
    opts = _opts(floors=12, parking=ParkingConfig(mode="min_open"))
    r = solve_max_kit(Site(area_m2=60_000), opts, norms)
    titles = [x.title for x in detect_risks(r)]
    assert any("подземн" in t.lower() for t in titles)


def test_risks_sorted_high_first(norms):
    opts = _opts(floors=12)
    r = solve_max_kit(Site(area_m2=10_000), opts, norms)
    risks = detect_risks(r)
    order = {"высокий": 0, "средний": 1, "низкий": 2}
    levels = [order[x.level] for x in risks]
    assert levels == sorted(levels)
