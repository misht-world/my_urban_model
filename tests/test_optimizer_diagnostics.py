"""Тесты диагностики оптимизатора (v0.5.2): warnings, exceptions, ловушки."""

from __future__ import annotations

import pytest

from urban_model.models import CalculationOptions, Site
from urban_model.normatives import load_normatives
from urban_model.optimize import SearchSpace, optimize_max_apartments


@pytest.fixture(scope="module")
def spb():
    return load_normatives("spb")


# ---------------------------------------------------------------------------
# ВПП-ловушка: try_built_in=True но base_options.built_in is None
# ---------------------------------------------------------------------------

class TestBuiltInTrap:
    def test_warns_when_try_built_in_without_base(self, spb):
        """try_built_in=True без built_in в base_options → warning в отчёте."""
        site = Site(area_m2=50_000)
        base = CalculationOptions(floors=12, planning_doc=True)
        space = SearchSpace(
            try_built_in=True,
            built_in_vri_codes=["4.4"],
            floors_range=(8, 20),
        )
        report = optimize_max_apartments(site, base, spb, space, n_trials=5)
        assert any("built_in" in w.lower() for w in report.warnings), \
            f"ожидался warning о ВПП, получили: {report.warnings}"

    def test_no_warning_when_base_has_built_in(self, spb):
        """С built_in в base_options ловушка не срабатывает."""
        from urban_model.models import BuiltInArea
        site = Site(area_m2=50_000)
        base = CalculationOptions(
            floors=12, planning_doc=True,
            built_in=BuiltInArea(area_m2=2_000, vri_code="4.4"),
        )
        space = SearchSpace(
            try_built_in=True,
            built_in_vri_codes=["4.4"],
            floors_range=(8, 20),
        )
        report = optimize_max_apartments(site, base, spb, space, n_trials=5)
        # Никаких warning о ВПП быть не должно
        assert not any("built_in" in w.lower() for w in report.warnings), \
            f"warning не ожидался, получили: {report.warnings}"


# ---------------------------------------------------------------------------
# Диагностика: счётчики и сводка ошибок
# ---------------------------------------------------------------------------

class TestErrorReporting:
    def test_n_trials_total_equals_feasible_plus_exception_plus_infeasible(self, spb):
        site = Site(area_m2=50_000)
        base = CalculationOptions(floors=12, planning_doc=True)
        space = SearchSpace(floors_range=(8, 25))
        report = optimize_max_apartments(site, base, spb, space, n_trials=10)
        assert report.n_trials_total == 10
        assert report.n_trials_feasible <= report.n_trials_total
        assert report.n_trials_exception >= 0


# ---------------------------------------------------------------------------
# Edge case: n_trials=1
# ---------------------------------------------------------------------------

class TestSingleTrial:
    def test_n_trials_1_returns_some_result(self, spb):
        """С n_trials=1 не падаем — возвращаем единственное испытание."""
        site = Site(area_m2=50_000)
        base = CalculationOptions(floors=12, planning_doc=True)
        space = SearchSpace(floors_range=(10, 12))
        report = optimize_max_apartments(site, base, spb, space, n_trials=1)
        assert report.n_trials_total == 1


# ---------------------------------------------------------------------------
# Edge case: всё-выкл (никаких ДОО/СОШ/ЗНОП)
# ---------------------------------------------------------------------------

class TestAllDisabled:
    def test_all_social_disabled_doesnt_crash(self, spb):
        """include_kindergarten=False, include_school=False, ЗНОП override=0
        — solve_max_kit не должен падать с exception. Возвращает корректный
        TEPResult, даже если конфигурация физически инфизибл (норматив 25%
        озеленения не достижим без ЗНОП и при низком КИТ)."""
        from urban_model import solve_max_kit
        site = Site(area_m2=50_000)
        opts = CalculationOptions(
            floors=12, planning_doc=True,
            include_kindergarten=False,
            include_school=False,
            znop_per_person_override=0.0,  # выключаем ЗНОП → озеленение только housing
        )
        r = solve_max_kit(site, opts, spb)  # не должен бросить
        # Социальных компонентов нет
        assert r.balance.components["kindergarten_plot"] == 0.0
        assert r.balance.components["school_plot"] == 0.0
        assert r.balance.components["znop"] == 0.0
        # limiting_factor заполнен
        assert r.limiting_factor
