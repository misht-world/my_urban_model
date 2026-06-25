"""Тесты v0.12.28 — амбулаторно-поликлинические учреждения (ВРИ 3.4.1)."""

from __future__ import annotations

import math

import pytest

from urban_model import solve_max_kit, verify_kit
from urban_model.calculations import polyclinic
from urban_model.models import CalculationOptions, Site
from urban_model.models.social import PolyclinicSpec
from urban_model.normatives import load_normatives


@pytest.fixture(scope="module")
def spb():
    return load_normatives("spb")


@pytest.fixture
def site():
    return Site(area_m2=120_000)


class TestCompute:
    def test_visits_from_norm(self, spb):
        r = polyclinic.compute(5000.0, spb)
        # 5000 × 26.33/1000 = 131.65 → вверх до 132 < 150 → ВПП
        assert r.visits == 132
        assert r.built_in is True

    def test_standalone_above_threshold(self, spb):
        r = polyclinic.compute(10000.0, spb)  # 263.3 → 264 ≥ 150 → отд. стоящая
        assert r.visits == 264
        assert r.built_in is False
        assert r.plot_area == pytest.approx(max(2000, 10 * 264))
        assert r.building_area == pytest.approx(23 * 264)
        assert r.greening_required == pytest.approx(0.15 * r.plot_area)

    def test_plot_min_2000(self, spb):
        # отд. стоящая с малым числом посещений (manual ≥150) → ЗУ не менее 2000
        r = polyclinic.compute(0.0, spb, mode="manual", visits_override=150)
        assert r.built_in is False
        assert r.plot_area == pytest.approx(max(2000, 10 * 150))  # = 2000

    def test_vpp_building_12(self, spb):
        r = polyclinic.compute(3000.0, spb)  # ~79 → ВПП
        assert r.built_in is True
        assert r.building_area == pytest.approx(12 * r.visits)
        assert r.plot_area == 0.0

    def test_vpp_split_over_100(self, spb):
        # ВПП с >100 посещений → дробление на офисы врача (≤100 каждый)
        r = polyclinic.compute(0.0, spb, mode="manual", visits_override=130, force_vpp=True)
        assert r.built_in is True
        assert r.n_objects == 2  # ceil(130/100)

    def test_parking_formula(self, spb):
        r = polyclinic.compute(10000.0, spb)
        workers = max(1, round(r.visits / 6))
        expected = max(2, math.ceil(workers / 5) + math.ceil(r.visits / 40))
        assert r.parking_places == expected

    def test_zero(self, spb):
        r = polyclinic.compute(0.0, spb)
        assert r.visits == 0 and r.parking_places == 0


class TestIntegration:
    def test_standalone_plot_in_balance(self, spb):
        site = Site(area_m2=300_000)
        r = solve_max_kit(site, CalculationOptions(floors=18, planning_doc=True), spb)
        assert r.polyclinic_visits_accepted.value >= 150
        assert r.polyclinic_built_in is False
        assert "polyclinic_plot" in r.balance.components
        assert r.balance.components["polyclinic_plot"] == pytest.approx(
            r.polyclinic_plot_area.value
        )

    def test_disabled(self, spb, site):
        r = verify_kit(
            1.5, site, CalculationOptions(floors=12, include_polyclinic=False), spb
        )
        assert (r.polyclinic_visits_accepted.value or 0) == 0
        assert "polyclinic_plot" not in r.balance.components

    def test_only_demand(self, spb):
        site = Site(area_m2=300_000)
        r = solve_max_kit(
            site,
            CalculationOptions(floors=18, planning_doc=True,
                               polyclinic=PolyclinicSpec(only_demand=True)),
            spb,
        )
        assert r.polyclinic_visits_accepted.value > 0
        assert "polyclinic_plot" not in r.balance.components
        assert r.economy.cost.polyclinic == 0.0

    def test_not_in_mandatory_vpp(self, spb):
        from urban_model.calculations import vpp
        m = vpp.compute_mandatory_areas(5000.0, spb)
        assert not hasattr(m, "medical_3_4_1")

    def test_economy_cost_and_compensation(self, spb):
        site = Site(area_m2=300_000)
        e = solve_max_kit(site, CalculationOptions(floors=18, planning_doc=True), spb).economy
        assert e.cost.polyclinic > 0
        comp_share = spb.resolve("economy.social_compensation.share")
        c_poly = spb.resolve("economy.construction.polyclinic")
        assert e.revenue.social_compensation >= e.cost.polyclinic * comp_share - 1e-3
