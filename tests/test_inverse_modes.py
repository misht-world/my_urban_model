"""Тесты режимов подбора КИТ v0.2: с резервом, с принудительным ЗНОП."""

from __future__ import annotations

import pytest

from urban_model import (
    solve_max_kit,
    solve_max_kit_with_reserve,
    solve_max_kit_with_znop,
)
from urban_model.models import CalculationOptions, Site
from urban_model.normatives import load_normatives


@pytest.fixture(scope="module")
def spb():
    return load_normatives("spb")


# ---------------------------------------------------------------------------
# Режим «с резервом»
# ---------------------------------------------------------------------------

class TestSolveMaxKitWithReserve:
    def test_zero_reserve_equals_solve_max_kit(self, spb):
        """target_surplus=0 эквивалентен solve_max_kit."""
        site = Site(area_m2=50_000)
        opts = CalculationOptions(floors=12, planning_doc=True)
        r_max = solve_max_kit(site, opts, spb)
        r_zero = solve_max_kit_with_reserve(site, 0.0, opts, spb)
        assert abs(r_zero.kit.value - r_max.kit.value) < 1e-3

    def test_high_reserve_lowers_kit(self, spb):
        """Большой целевой резерв сжимает максимально допустимый КИТ."""
        site = Site(area_m2=50_000)
        opts = CalculationOptions(floors=12, planning_doc=True)
        r_max = solve_max_kit(site, opts, spb)
        # Цель: резерв = 80% от полученного при solve_max_kit
        target = r_max.balance.surplus * 0.8 if r_max.balance.surplus > 0 else 5_000
        r_with = solve_max_kit_with_reserve(site, target, opts, spb)
        # КИТ ≤ макс., балансовый резерв ≥ target (с допуском бисекции)
        assert r_with.kit.value <= r_max.kit.value + 1e-3
        assert r_with.balance.surplus >= target - 1.0  # ~допуск 1 м²

    def test_unfeasible_reserve(self, spb):
        """Если требуемый резерв заведомо недостижим — limiting_factor об этом."""
        site = Site(area_m2=10_000)
        # 10 га-резерв на 1-га квартале — невозможно
        r = solve_max_kit_with_reserve(site, 100_000, CalculationOptions(), spb)
        assert "не даёт резерв" in (r.limiting_factor or "")

    def test_limiting_factor_mentions_reserve(self, spb):
        site = Site(area_m2=50_000)
        r = solve_max_kit_with_reserve(site, 5_000, CalculationOptions(), spb)
        assert "резерв" in (r.limiting_factor or "")


# ---------------------------------------------------------------------------
# Режим «с увеличенным ЗНОП»
# ---------------------------------------------------------------------------

class TestSolveMaxKitWithZnop:
    def test_override_value_used(self, spb):
        """Возвращённый ЗНОП = заданному override."""
        site = Site(area_m2=100_000)
        r = solve_max_kit_with_znop(
            site, target_znop_per_person=6, options=CalculationOptions(floors=15), norms=spb
        )
        assert r.znop_per_person.value == 6

    def test_status_manual(self, spb):
        """ЗНОП-поле имеет статус manual (явно ручное значение)."""
        site = Site(area_m2=100_000)
        r = solve_max_kit_with_znop(site, 5, CalculationOptions(), spb)
        assert r.znop_per_person.status.value == "manual"

    def test_higher_znop_lowers_or_equal_kit(self, spb):
        """Принудительно высокий ЗНОП ≥ нормативного → КИТ ≤ КИТ_max."""
        site = Site(area_m2=100_000)
        opts = CalculationOptions(floors=15, planning_doc=True)
        r_norm = solve_max_kit(site, opts, spb)
        # Принудительный ЗНОП=6 (что в нормативе случается только при КИТ≥2.0)
        r_high = solve_max_kit_with_znop(site, 6, opts, spb)
        assert r_high.kit.value <= r_norm.kit.value + 1e-3

    def test_zero_znop_yields_zero_znop_area(self, spb):
        """Override=0 принудительно обнуляет ЗНОП.

        Замечание: после введения нормативного контроля 25% озеленения
        квартала, обнуление ЗНОП не обязательно «облегчает» баланс — наоборот,
        ЗНОП был основным источником озеленения при высоком КИТ. Поэтому
        block_density при znop=0 может оказаться меньше, чем при нормативе.
        Главное — znop_area реально становится 0.
        """
        site = Site(area_m2=300_000)
        opts = CalculationOptions(floors=15, planning_doc=True)
        r_zero = solve_max_kit_with_znop(site, 0, opts, spb)
        assert r_zero.znop_area.value == 0
        assert r_zero.znop_per_person.value == 0

    def test_does_not_mutate_input_options(self, spb):
        """solve_max_kit_with_znop не меняет переданный options."""
        opts = CalculationOptions(floors=15)
        original_override = opts.znop_per_person_override
        _ = solve_max_kit_with_znop(Site(area_m2=100_000), 5, opts, spb)
        assert opts.znop_per_person_override == original_override
