"""Тесты для optimize/scans.py (v0.9.0)."""

from __future__ import annotations

import pytest

from urban_model.models import CalculationOptions, Site
from urban_model.models.social import KindergartenSpec, SchoolSpec
from urban_model.normatives import load_normatives
from urban_model.optimize.scans import (
    ScanPoint,
    ScanResult,
    scan_floors,
    scan_parking_underground_share,
    scan_znop_steps,
)


@pytest.fixture(scope="module")
def norms():
    return load_normatives("spb")


@pytest.fixture
def site_large():
    return Site(area_m2=50_000)


@pytest.fixture
def base_options():
    return CalculationOptions(
        floors=12,
        kindergarten=KindergartenSpec(only_demand=True),
        school=SchoolSpec(only_demand=True),
    )


class TestScanFloors:
    def test_returns_scan_result(self, site_large, base_options, norms):
        r = scan_floors(site_large, base_options, norms)
        assert isinstance(r, ScanResult)
        assert r.factor == "floors"
        assert len(r.points) == 21  # 5..25 включительно
        assert all(isinstance(p, ScanPoint) for p in r.points)

    def test_base_point_marked(self, site_large, base_options, norms):
        r = scan_floors(site_large, base_options, norms)
        assert r.base_point is not None
        assert r.base_point.x_value == 12.0  # base_options.floors=12
        # Только ОДНА точка с is_base
        assert sum(1 for p in r.points if p.is_base) == 1

    def test_recommended_is_argmax(self, site_large, base_options, norms):
        r = scan_floors(site_large, base_options, norms)
        feasible = [p for p in r.points if p.feasible]
        assert feasible, "Должны быть feasible точки на квартале 50 000 м²"
        max_apt = max(p.apartments_area for p in feasible)
        assert r.recommended_point.apartments_area == pytest.approx(max_apt)
        assert r.recommended_point.is_recommended


class TestScanZnop:
    def test_four_steps(self, site_large, base_options, norms):
        r = scan_znop_steps(site_large, base_options, norms)
        assert r.factor == "znop"
        assert len(r.points) == 4
        x_values = {p.x_value for p in r.points}
        assert x_values == {0.0, 3.0, 4.0, 6.0}

    def test_has_base_and_recommended(self, site_large, base_options, norms):
        r = scan_znop_steps(site_large, base_options, norms)
        assert r.base_point is not None
        # Должна быть хотя бы одна feasible точка (на большом квартале)
        if any(p.feasible for p in r.points):
            assert r.recommended_point is not None


class TestScanParking:
    def test_eleven_points(self, site_large, base_options, norms):
        r = scan_parking_underground_share(site_large, base_options, norms)
        assert r.factor == "parking_underground"
        assert len(r.points) == 11  # 0.0..1.0 шагом 0.1

    def test_x_values_span_zero_to_one(self, site_large, base_options, norms):
        r = scan_parking_underground_share(site_large, base_options, norms)
        x_values = sorted(p.x_value for p in r.points)
        assert x_values[0] == pytest.approx(0.0)
        assert x_values[-1] == pytest.approx(1.0)

    def test_base_marked_for_default_min_open(self, site_large, base_options, norms):
        # min_open → underground_share = 0.875, ближайший шаг — 0.9
        r = scan_parking_underground_share(site_large, base_options, norms)
        assert r.base_point is not None
        assert r.base_point.is_base
        # Ближайший к 0.875 шаг из {0.0..1.0 шаг 0.1} — это 0.9
        assert r.base_point.x_value == pytest.approx(0.9, abs=0.01)
