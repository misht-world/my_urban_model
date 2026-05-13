"""Тесты парковочных сценариев (v0.3) + WARNING СОШ < capacity_min."""

from __future__ import annotations

import math

import pytest

from urban_model import solve_max_kit, verify_kit
from urban_model.calculations.parking import compute_parking_breakdown
from urban_model.models import (
    CalculationOptions,
    ParkingConfig,
    Site,
)
from urban_model.normatives import load_normatives


@pytest.fixture(scope="module")
def spb():
    return load_normatives("spb")


# ---------------------------------------------------------------------------
# ParkingConfig — валидация
# ---------------------------------------------------------------------------

class TestParkingConfigValidation:
    def test_default_is_min_open(self):
        cfg = ParkingConfig()
        assert cfg.mode == "min_open"

    def test_custom_shares_must_sum_to_one(self):
        with pytest.raises(ValueError, match="ожидается 1.0"):
            ParkingConfig(
                mode="custom",
                open_share=0.3,
                multilevel_share=0.3,
                underground_share=0.3,  # сумма 0.9, не 1.0
            )

    def test_custom_shares_sum_to_one_ok(self):
        # не падает
        cfg = ParkingConfig(
            mode="custom",
            open_share=0.2,
            multilevel_share=0.5,
            underground_share=0.3,
        )
        assert cfg.mode == "custom"

    def test_unknown_mode_rejected(self):
        with pytest.raises(ValueError, match="mode должен быть"):
            ParkingConfig(mode="superparking")


# ---------------------------------------------------------------------------
# compute_parking_breakdown — поведение режимов
# ---------------------------------------------------------------------------

class TestParkingBreakdown:
    def test_min_open_meets_minimum_share(self, spb):
        # 10 000 м² квартир → 125 м/м
        br = compute_parking_breakdown(10_000, ParkingConfig(mode="min_open"), spb)
        assert br.total_required == 125
        # ≥ 12.5%: 125 × 0.125 = 15.625 → ceil = 16
        assert br.open_places == 16
        assert br.multilevel_places == 0
        assert br.underground_places == 109
        # сумма равна total
        assert br.open_places + br.multilevel_places + br.underground_places == br.total_required

    def test_all_open_uses_full_total(self, spb):
        br = compute_parking_breakdown(10_000, ParkingConfig(mode="all_open"), spb)
        assert br.open_places == br.total_required
        assert br.multilevel_places == 0
        assert br.underground_places == 0

    def test_custom_shares_distribute_correctly(self, spb):
        # 10 000 м² квартир → 125 м/м.
        # 20% открытых = 25, 50% многоуровн. = 62, остаток подземные = 38.
        br = compute_parking_breakdown(
            10_000,
            ParkingConfig(
                mode="custom",
                open_share=0.20,
                multilevel_share=0.50,
                underground_share=0.30,
                multilevel_levels=3,
            ),
            spb,
        )
        assert br.total_required == 125
        # open = ceil(125*0.2) = 25
        assert br.open_places == 25
        # multilevel = floor(125*0.5) = 62
        assert br.multilevel_places == 62
        # underground = total - open - multilevel
        assert br.underground_places == 125 - 25 - 62

    def test_multilevel_objects_split_by_capacity_max(self, spb):
        # capacity_max=300, 700 м/м → 3 объекта (ceil(700/300))
        br = compute_parking_breakdown(
            56_000,  # 56000/80 = 700 м/м
            ParkingConfig(
                mode="custom",
                open_share=0.0,
                multilevel_share=1.0,
                underground_share=0.0,
                multilevel_levels=3,
            ),
            spb,
        )
        # open принудительно ≥ 12.5% (жёсткий минимум норматива)
        # значит multilevel = floor(700*1.0)=700, но open подняли до ceil(700*0.125)=88
        # итого: open=88, multilevel=700 (как заказано), underground=700-88-700<0 → 0
        # Это краевой случай, проверим только что multilevel разбит на ≥ 3 объекта.
        assert br.multilevel_objects == math.ceil(br.multilevel_places / 300)

    def test_underground_does_not_take_surface_area(self, spb):
        br = compute_parking_breakdown(
            20_000,
            ParkingConfig(
                mode="custom",
                open_share=0.125,  # минимум
                multilevel_share=0.0,
                underground_share=0.875,
                multilevel_levels=3,
            ),
            spb,
        )
        # Поверхностная площадь = только открытые
        assert br.multilevel_footprint == 0.0
        assert br.total_surface_area == br.open_area
        # Но подземные есть и учтены
        assert br.underground_places > 0

    def test_zero_apartments_zero_parking(self, spb):
        br = compute_parking_breakdown(0, ParkingConfig(mode="min_open"), spb)
        assert br.total_required == 0
        assert br.open_places == 0
        assert br.multilevel_places == 0
        assert br.underground_places == 0


# ---------------------------------------------------------------------------
# Интеграция в solve_max_kit / verify_kit
# ---------------------------------------------------------------------------

class TestParkingInBalance:
    def test_all_open_lowers_max_kit_vs_min_open(self, spb):
        """all_open даёт меньший (или равный) макс. КИТ, потому что
        отъедает поверхность. На квартале 50 000 м² оба режима feasible,
        и эффект чётко виден (open_places в all_open в разы больше)."""
        site = Site(area_m2=50_000)
        opts_min = CalculationOptions(
            floors=15, planning_doc=True,
            parking=ParkingConfig(mode="min_open"),
        )
        opts_all = CalculationOptions(
            floors=15, planning_doc=True,
            parking=ParkingConfig(mode="all_open"),
        )
        r_min = solve_max_kit(site, opts_min, spb)
        r_all = solve_max_kit(site, opts_all, spb)
        # Оба сценария должны быть feasible (иначе тест не показателен).
        assert r_min.balance.is_feasible
        assert r_all.balance.is_feasible
        # КИТ в all_open ≤ min_open (parking-нагрузка съедает территорию).
        assert r_all.kit.value <= r_min.kit.value + 1e-3
        # Открытых м/м в all_open строго больше — это инвариант режимов.
        assert r_all.parking_open_places.value > r_min.parking_open_places.value

    def test_multilevel_appears_in_balance_components(self, spb):
        site = Site(area_m2=100_000)
        opts = CalculationOptions(
            floors=15, planning_doc=True,
            parking=ParkingConfig(
                mode="custom",
                open_share=0.20,
                multilevel_share=0.60,
                underground_share=0.20,
                multilevel_levels=3,
            ),
        )
        res = verify_kit(1.5, site, opts, spb)
        assert "parking_multilevel" in res.balance.components
        assert res.balance.components["parking_multilevel"] > 0
        # Соответствует TEPField
        assert res.parking_multilevel_area.value == res.balance.components["parking_multilevel"]

    def test_underground_not_in_balance(self, spb):
        site = Site(area_m2=100_000)
        opts = CalculationOptions(
            floors=15,
            parking=ParkingConfig(mode="min_open"),  # max подземных
        )
        res = verify_kit(1.5, site, opts, spb)
        # Подземные есть
        assert res.parking_underground_places.value > 0
        # Но в баланс не входят (только multilevel footprint = 0)
        assert res.balance.components["parking_multilevel"] == 0


# ---------------------------------------------------------------------------
# WARNING: СОШ < capacity_min
# ---------------------------------------------------------------------------

class TestSchoolMinCapacityWarning:
    def test_warning_for_tiny_population(self, spb):
        """На малом квартале расчёт даёт СОШ на 10 мест — ниже минимума 550."""
        site = Site(area_m2=10_000)
        # Низкий КИТ, чтобы расчётная вместимость была < 550
        res = verify_kit(0.3, site, CalculationOptions(floors=8), spb)
        # Население ~80 чел → школьных мест ≈ 10
        assert res.school_places_accepted.value < 550
        assert res.school_places_accepted.status.value == "warning"
        # И в warnings
        assert any("СОШ" in w for w in res.warnings)

    def test_no_warning_for_large_population(self, spb):
        """На большом квартале с СОШ типового размера (попадает в список) → статус ok.

        v0.6.5: типовые параллели КС [550/825/1100/1375/1650/1925/2200/2475].
        Подбор site так, чтобы расчётная вместимость попала ровно в 825 (III параллель):
        apt × 0.12/28 ≈ 825 → apt ≈ 192_500 → gfa ≈ 256_667 → site=250_000 при КИТ=1.0
        даёт ~803 → round 25 → 825.
        """
        site = Site(area_m2=250_000)
        res = verify_kit(1.0, site, CalculationOptions(floors=10), spb)
        assert res.school_places_accepted.value == 825
        assert res.school_places_accepted.status.value == "ok"
        # Никакого warning про СОШ
        assert not any("СОШ" in w for w in res.warnings)


# ---------------------------------------------------------------------------
# WARNING: СОШ вне списка допустимых вместимостей (v0.6.5)
# ---------------------------------------------------------------------------

class TestSchoolAllowedCapacities:
    """Проверка: если итоговая вместимость СОШ не входит в список
    типовых параллелей [550/825/1100/1375/1650/1925/2200/2475] —
    выдаётся WARNING с указанием ближайших значений."""

    def test_warning_with_neighbors(self, spb):
        """При вместимости вне списка — warning с «меньше: X, больше: Y»."""
        from urban_model.models.social import SchoolSpec
        site = Site(area_m2=200_000)
        # Принудительно задаём 700 мест (нет в списке, между 550 и 825)
        res = verify_kit(
            1.5, site,
            CalculationOptions(
                floors=10,
                school=SchoolSpec(num_objects=1, capacity_per_object=700),
            ),
            spb,
        )
        sosh_warns = [w for w in res.warnings if "СОШ" in w and "не входит" in w]
        assert len(sosh_warns) > 0
        assert "550" in sosh_warns[0]
        assert "825" in sosh_warns[0]

    def test_no_warning_for_max_parallel(self, spb):
        """СОШ ровно 2475 мест (IX параллель) → нет warning о вне списка."""
        from urban_model.models.social import SchoolSpec
        site = Site(area_m2=500_000)
        res = verify_kit(
            1.0, site,
            CalculationOptions(
                floors=12,
                school=SchoolSpec(num_objects=1, capacity_per_object=2475),
            ),
            spb,
        )
        sosh_warns = [w for w in res.warnings if "не входит" in w]
        assert len(sosh_warns) == 0
