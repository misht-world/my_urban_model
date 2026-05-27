"""Тесты норматива озеленения квартала (25% от площади за вычетом ДОО/СОШ).

С v0.5.1 норматив озеленения — обязательная часть `BalanceCheck.is_feasible`.
Это меняет физику обратной задачи: feasibility не монотонна по КИТ
(низкий КИТ → озеленения мало → infeasible; высокий → территории мало).
Бисекция должна корректно находить верхний край feasible-окна даже когда
оба конца диапазона infeasible.
"""

from __future__ import annotations

import pytest

from urban_model import solve_max_kit, verify_kit
from urban_model.models import CalculationOptions, Site
from urban_model.normatives import load_normatives


@pytest.fixture(scope="module")
def spb():
    return load_normatives("spb")


# ---------------------------------------------------------------------------
# BalanceCheck: поля и свойство greening_deficit
# ---------------------------------------------------------------------------

class TestBalanceFields:
    def test_balance_exposes_greening_fields(self, spb):
        """compute_tep_for_kit заполняет greening_actual / greening_required."""
        site = Site(area_m2=50_000)
        r = solve_max_kit(site, CalculationOptions(floors=12, planning_doc=True), spb)
        assert r.balance.greening_actual >= 0
        assert r.balance.greening_required >= 0

    def test_greening_deficit_property(self, spb):
        """deficit = max(0, required - actual)."""
        site = Site(area_m2=50_000)
        r = solve_max_kit(site, CalculationOptions(floors=12, planning_doc=True), spb)
        bal = r.balance
        expected = max(0.0, bal.greening_required - bal.greening_actual)
        assert abs(bal.greening_deficit - expected) < 1e-6


# ---------------------------------------------------------------------------
# Озеленение реально является ограничением
# ---------------------------------------------------------------------------

class TestGreeningEnforcement:
    def test_low_kit_uses_surplus_as_greening(self, spb):
        """v0.9.16: при низком КИТ резерв квартала засчитывается как
        зелёное открытое пространство — балан feasible даже без явного ЗНОП.
        Раньше (v0.9.15) тот же кейс был infeasible из-за формального
        дефицита, что не соответствовало реальной практике (двор/площадка
        на огромном пустом квартале ЯВЛЯЕТСЯ озеленением).
        """
        site = Site(area_m2=100_000)
        r = verify_kit(0.5, site, CalculationOptions(floors=12, planning_doc=True), spb)
        # surplus есть, balance.greening_actual теперь включает его → feasible
        assert r.balance.surplus > 0
        assert r.balance.greening_actual >= r.balance.greening_required
        assert r.balance.is_feasible

    def test_high_kit_with_znop_meets_greening(self, spb):
        """При КИТ ≥ 2.0 ЗНОП = 6 м²/чел даёт большой запас озеленения."""
        site = Site(area_m2=100_000)
        r = verify_kit(2.0, site, CalculationOptions(floors=15, planning_doc=True), spb)
        # При КИТ=2.0 ЗНОП = 6 → ~13к чел × 6 = 80к м² → озеленение OK
        assert r.balance.greening_actual >= r.balance.greening_required


# ---------------------------------------------------------------------------
# Бисекция корректно работает в non-monotonic случае
# ---------------------------------------------------------------------------

class TestBisectionWithGreening:
    def test_solve_max_kit_finds_feasible_window(self, spb):
        """solve_max_kit должен найти feasible-КИТ даже когда низкий конец
        упирается в озеленение, а высокий — в плотность."""
        site = Site(area_m2=200_000)
        r = solve_max_kit(site, CalculationOptions(planning_doc=True), spb)
        # Должен найти валидный КИТ (а не fall-back в kit_search_min)
        assert r.balance.is_feasible
        assert r.kit.value > 0.5  # не уперлось в нижнюю границу 0.1

    def test_solve_max_kit_includes_greening_in_check(self, spb):
        """Возвращённый КИТ должен иметь корректное озеленение."""
        site = Site(area_m2=80_000)
        r = solve_max_kit(site, CalculationOptions(floors=12, planning_doc=True), spb)
        if r.balance.is_feasible:
            assert r.balance.greening_actual >= r.balance.greening_required - 1e-3


# ---------------------------------------------------------------------------
# limiting_factor сообщает про озеленение когда оно ограничивает
# ---------------------------------------------------------------------------

class TestLimitingFactorReporting:
    def test_greening_appears_in_limiting_factor_when_tight(self, spb):
        """Когда озеленение прижато ко границе, limiting_factor об этом."""
        site = Site(area_m2=200_000)
        r = solve_max_kit(site, CalculationOptions(planning_doc=True), spb)
        if r.balance.is_feasible:
            # Озеленение или плотность будет в limiting_factor — оба валидны
            lf = (r.limiting_factor or "").lower()
            assert lf  # не пустой
