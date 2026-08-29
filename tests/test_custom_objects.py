"""Тесты CustomObject (v0.5.5): произвольные объекты на территории квартала."""

from __future__ import annotations

import pytest

from urban_model import solve_max_kit
from urban_model.core.forward import compute_tep_for_kit
from urban_model.models import CalculationOptions, CustomObject, Site
from urban_model.normatives import load_normatives


@pytest.fixture(scope="module")
def spb():
    return load_normatives("spb")


@pytest.fixture(scope="module")
def site():
    return Site(area_m2=50_000)


# ---------------------------------------------------------------------------
# Pydantic-модель
# ---------------------------------------------------------------------------

class TestCustomObjectModel:
    def test_required_plot_area(self):
        with pytest.raises(ValueError):
            CustomObject(plot_area_m2=0)

    def test_default_floor_area_equals_plot(self):
        obj = CustomObject(name="ФОК", plot_area_m2=2_000, vri_code="5.1")
        assert obj.floor_area_m2 == 2_000

    def test_explicit_floor_area(self):
        obj = CustomObject(plot_area_m2=2_000, floor_area_m2=5_000, vri_code="4.1")
        assert obj.floor_area_m2 == 5_000


# ---------------------------------------------------------------------------
# Интеграция в баланс
# ---------------------------------------------------------------------------

class TestBalanceIntegration:
    def test_object_appears_in_balance_components(self, spb, site):
        """Объект должен попасть в balance.components."""
        opts = CalculationOptions(
            floors=12, planning_doc=True,
            custom_objects=[
                CustomObject(name="Офис", plot_area_m2=3_000, vri_code="4.1"),
            ],
        )
        r = compute_tep_for_kit(1.0, site, opts, spb)
        assert "custom_objects" in r.balance.components
        assert r.balance.components["custom_objects"] == 3_000

    def test_multiple_objects_summed(self, spb, site):
        opts = CalculationOptions(
            floors=12, planning_doc=True,
            custom_objects=[
                CustomObject(name="Офис", plot_area_m2=2_000, vri_code="4.1"),
                CustomObject(name="ФОК", plot_area_m2=4_000, vri_code="5.1"),
            ],
        )
        r = compute_tep_for_kit(1.0, site, opts, spb)
        assert r.balance.components["custom_objects"] == 6_000

    def test_object_reduces_max_density(self, spb):
        """Кастомный объект отнимает территорию → block_density падает."""
        site = Site(area_m2=50_000)
        opts_no = CalculationOptions(floors=15, planning_doc=True)
        opts_with = CalculationOptions(
            floors=15, planning_doc=True,
            custom_objects=[
                CustomObject(name="ФОК", plot_area_m2=5_000, vri_code="5.1"),
            ],
        )
        r_no = solve_max_kit(site, opts_no, spb)
        r_with = solve_max_kit(site, opts_with, spb)
        # Если оба feasible — с объектом плотность ≤
        if r_no.balance.is_feasible and r_with.balance.is_feasible:
            assert r_with.block_density.value <= r_no.block_density.value + 1e-3


# ---------------------------------------------------------------------------
# Парковки и озеленение по ВРИ
# ---------------------------------------------------------------------------

class TestParkingAndGreening:
    def test_object_adds_parking_places(self, spb, site):
        """Парковки кастомного объекта добавляются к жилым."""
        opts_no = CalculationOptions(floors=12, planning_doc=True)
        opts_with = CalculationOptions(
            floors=12, planning_doc=True,
            custom_objects=[
                CustomObject(plot_area_m2=2_000, floor_area_m2=4_000, vri_code="4.4"),
            ],
        )
        r_no = compute_tep_for_kit(1.0, site, opts_no, spb)
        r_with = compute_tep_for_kit(1.0, site, opts_with, spb)
        assert r_with.parking_required_places.value > r_no.parking_required_places.value

    def test_object_occupies_land_not_top_greening(self, spb, site):
        """v0.20.2: кастомный объект занимает ЗУ квартала (компонент баланса),
        но его придомовое озеленение НЕ входит в озеленение ТОП (общего
        пользования). Раньше (контроль «25% всего озеленения») озеленение
        объекта капало в greening_actual; теперь метрика — только ТОП
        (ЗНОП + резерв), поэтому объект резерв УМЕНЬШАЕТ, а не увеличивает
        ТОП-озеленение."""
        opts_with = CalculationOptions(
            floors=12, planning_doc=True,
            custom_objects=[
                CustomObject(plot_area_m2=5_000, floor_area_m2=5_000, vri_code="4.4"),
            ],
        )
        opts_no = CalculationOptions(floors=12, planning_doc=True)
        r_with = compute_tep_for_kit(1.0, site, opts_with, spb)
        r_no = compute_tep_for_kit(1.0, site, opts_no, spb)
        # объект занимает ЗУ квартала (компонент баланса)
        assert r_with.balance.components.get("custom_objects", 0) == pytest.approx(5_000)
        # его ЗУ съедает резерв → ТОП-озеленение (ЗНОП + резерв) не растёт
        assert r_with.balance.greening_actual <= r_no.balance.greening_actual + 1e-6


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_any_vri_code_works(self, spb, site):
        """v0.7.1.1: единый коэф парковки → любой ВРИ-код работает без warning.

        Раньше неизвестный ВРИ давал warning. Теперь parking.vpp.m2_per_place —
        scalar (среднее 64 м²/м.м), не зависит от vri_code.
        """
        opts = CalculationOptions(
            floors=12, planning_doc=True,
            custom_objects=[
                CustomObject(plot_area_m2=1_000, vri_code="9.99"),
            ],
        )
        r = compute_tep_for_kit(1.0, site, opts, spb)
        assert r.balance.components["custom_objects"] == 1_000

    def test_empty_list_acts_like_no_objects(self, spb, site):
        """Пустой список — как если бы custom_objects не задавались."""
        opts_empty = CalculationOptions(
            floors=12, planning_doc=True, custom_objects=[],
        )
        opts_default = CalculationOptions(floors=12, planning_doc=True)
        r1 = compute_tep_for_kit(1.0, site, opts_empty, spb)
        r2 = compute_tep_for_kit(1.0, site, opts_default, spb)
        assert "custom_objects" not in r1.balance.components
        assert r1.balance.required_total == r2.balance.required_total
