"""Интеграционные тесты обратного расчёта."""

from __future__ import annotations

import pytest

from urban_model.core.forward import compute_tep_for_kit
from urban_model.models import CalculationOptions, Site
from urban_model.modes.inverse import solve_max_kit
from urban_model.normatives import load_normatives


@pytest.fixture(scope="module")
def spb():
    return load_normatives("spb")


class TestForward:
    def test_density_check_kicks_in_for_huge_kit(self, spb):
        # Берём КИТ на верхней границе (для маленького квартала плотность зашкалит)
        site = Site(area_m2=10_000)
        opts = CalculationOptions(floors=20, planning_doc=True)
        res = compute_tep_for_kit(2.4, site, opts, spb)
        # ratio 0.75 → S_квартир = 2.4*10000*0.75=18000, нас. по 20 = 900 → 900 чел/га
        assert res.density_chel_per_ga.status.value == "error"
        assert any("Плотность" in w for w in res.warnings)

    def test_balance_negative_for_huge_kit(self, spb):
        site = Site(area_m2=10_000)
        opts = CalculationOptions(floors=10)
        res = compute_tep_for_kit(2.5, site, opts, spb)
        assert not res.balance.is_feasible
        assert res.balance.surplus < 0

    def test_zero_population_when_kit_zero(self, spb):
        site = Site(area_m2=10_000)
        opts = CalculationOptions(floors=10)
        res = compute_tep_for_kit(0.0, site, opts, spb)
        assert res.population.value == 0
        assert res.kindergarten_places_required.value == 0


class TestInverse:
    def test_returns_kit_in_range(self, spb):
        site = Site(area_m2=20_000)
        opts = CalculationOptions(floors=10, planning_doc=True)
        res = solve_max_kit(site, opts, spb)
        assert 0 < res.kit.value <= 2.5

    def test_higher_kit_gives_more_apartments(self, spb):
        site_small = Site(area_m2=20_000)
        site_big = Site(area_m2=100_000)
        opts = CalculationOptions(floors=10, planning_doc=True)
        r_small = solve_max_kit(site_small, opts, spb)
        r_big = solve_max_kit(site_big, opts, spb)
        # На большем квартале можно достичь большего КИТ
        assert r_big.kit.value >= r_small.kit.value

    def test_no_ppt_lowers_max(self, spb):
        site = Site(area_m2=200_000)
        r_yes = solve_max_kit(site, CalculationOptions(planning_doc=True), spb)
        r_no = solve_max_kit(site, CalculationOptions(planning_doc=False), spb)
        # Без ППТ потолок 1.4 — не может превышать
        assert r_no.kit.value <= 1.4 + 1e-6
        assert r_no.kit_normative_max.value == 1.4
        assert r_yes.kit_normative_max.value == 2.5

    def test_limiting_factor_set(self, spb):
        site = Site(area_m2=20_000)
        res = solve_max_kit(site, CalculationOptions(), spb)
        assert res.limiting_factor is not None
        assert len(res.limiting_factor) > 0

    def test_disabling_school_increases_kit(self, spb):
        # Если выключить СОШ — освободится много территории, КИТ растёт
        site = Site(area_m2=30_000)
        with_school = solve_max_kit(
            site, CalculationOptions(include_school=True), spb
        )
        without_school = solve_max_kit(
            site, CalculationOptions(include_school=False), spb
        )
        assert without_school.kit.value > with_school.kit.value

    def test_balance_feasible_at_solution(self, spb):
        site = Site(area_m2=50_000)
        res = solve_max_kit(site, CalculationOptions(), spb)
        # либо подобран КИТ с положительным балансом,
        # либо помечено, что даже минимальный не проходит
        assert res.balance.is_feasible or "минимальный" in (res.limiting_factor or "")

    def test_audit_trail_in_fields(self, spb):
        """Каждое ключевое поле несёт source/formula — это требование ТЗ."""
        site = Site(area_m2=30_000)
        res = solve_max_kit(site, CalculationOptions(), spb)
        assert res.population.source is not None
        assert res.population.formula is not None
        assert res.znop_per_person.formula is not None
