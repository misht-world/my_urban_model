"""Тесты v0.7.1 — список ВПП с 5 вариантами размещения.

Нормативы НГП СПб для обязательных ВПП:
- 4.4 (торговля):  460.10 м²/1000 чел (сразу в м²)
- 4.6 (общепит):   105.20 посад.мест/1000 чел × 6 м²/место = 631.2 м²/1000
- 3.3 (быт.обсл.): 19.00 раб.мест/1000 чел × 20 м²/раб.место = 380 м²/1000
- 3.4.1 (поликл.): 26.33 посещ.в смену/1000 × 8 м²/посещ. = 210.64 м²/1000

v0.12.15: ВРИ 3.5.1 (доп. образование) вынесен из ВПП в отдельный соцобъект
(social_objects.add_education) — в обязательных ВПП теперь 4 ВРИ.
"""

from __future__ import annotations

import pytest

from urban_model import verify_kit
from urban_model.calculations import vpp
from urban_model.models import BuiltInArea, CalculationOptions, Site
from urban_model.normatives import load_normatives


@pytest.fixture(scope="module")
def spb():
    return load_normatives("spb")


@pytest.fixture
def site():
    return Site(area_m2=100_000)


# ---------------------------------------------------------------------------
# compute_mandatory_areas — расчёт обязательных ВПП от населения
# ---------------------------------------------------------------------------

class TestMandatoryAreas:
    def test_1000_people(self, spb):
        """Точные значения от НГП × коэф пересчёта."""
        m = vpp.compute_mandatory_areas(1000.0, spb)
        assert m.shopping_4_4 == pytest.approx(460.10)
        assert m.catering_4_6 == pytest.approx(105.20 * 6)
        assert m.domestic_3_3 == pytest.approx(19.0 * 20)
        # v0.12.28: ВРИ 3.4.1 (поликлиника) вынесен из ВПП
        assert not hasattr(m, "medical_3_4_1")

    def test_total_1000_people(self, spb):
        """Сумма обязательных ВПП (3 ВРИ: 4.4/4.6/3.3) для 1000 чел ≈ 1471.30 м²."""
        m = vpp.compute_mandatory_areas(1000.0, spb)
        assert m.total == pytest.approx(460.10 + 631.2 + 380, abs=0.01)

    def test_zero_population(self, spb):
        m = vpp.compute_mandatory_areas(0.0, spb)
        assert m.total == 0.0


# ---------------------------------------------------------------------------
# build_built_ins — 5 вариантов
# ---------------------------------------------------------------------------

class TestVppModes:
    def test_min_only(self, spb):
        """Вариант 3: только обязательный минимум по 3 ВРИ (без 3.5.1 и 3.4.1)."""
        res = vpp.build_built_ins("min_only", population=1000, footprint=0, norms=spb)
        # 3 объекта (3.5.1 и 3.4.1 вынесены в отдельные соцобъекты, v0.12.15/28)
        assert len(res.built_ins) == 3
        vri_codes = sorted([b.vri_code for b in res.built_ins])
        assert vri_codes == ["3.3", "4.4", "4.6"]
        # 4.4 должен быть ровно 460.10
        s_44 = next(b.area_m2 for b in res.built_ins if b.vri_code == "4.4")
        assert s_44 == pytest.approx(460.10)
        assert not res.overflow

    def test_min_plus_with_extras(self, spb):
        """Вариант 4: min + дополнительная торговля и общепит."""
        res = vpp.build_built_ins(
            "min_plus", population=1000, footprint=0, norms=spb,
            custom_4_4_m2=500, custom_4_6_m2=300,
        )
        s_44 = next(b.area_m2 for b in res.built_ins if b.vri_code == "4.4")
        s_46 = next(b.area_m2 for b in res.built_ins if b.vri_code == "4.6")
        assert s_44 == pytest.approx(460.10 + 500)
        assert s_46 == pytest.approx(631.2 + 300)

    def test_min_plus_without_extras(self, spb):
        """min_plus без custom = min_only."""
        res = vpp.build_built_ins("min_plus", population=1000, footprint=0, norms=spb)
        s_44 = next(b.area_m2 for b in res.built_ins if b.vri_code == "4.4")
        s_46 = next(b.area_m2 for b in res.built_ins if b.vri_code == "4.6")
        assert s_44 == pytest.approx(460.10)
        assert s_46 == pytest.approx(631.2)

    def test_custom_only(self, spb):
        """Вариант 5: только пользовательские 4.4 и 4.6."""
        res = vpp.build_built_ins(
            "custom_only", population=1000, footprint=0, norms=spb,
            custom_4_4_m2=200, custom_4_6_m2=100,
        )
        assert len(res.built_ins) == 2
        assert next(b.area_m2 for b in res.built_ins if b.vri_code == "4.4") == 200
        assert next(b.area_m2 for b in res.built_ins if b.vri_code == "4.6") == 100

    def test_full_floor_fits(self, spb):
        """Вариант 1: весь этаж = ВПП с достаточным footprint."""
        # 1000 чел, минимум всех ≈ 1789.94 м². Возьмём footprint = 3000 м².
        # Остаток для 4.4+4.6: 3000 − (380+210.64+108) = 2301.36
        # min 4.4+4.6 = 460.10 + 631.2 = 1091.3
        # extra = 2301.36 − 1091.3 = 1210.06
        # 4.4 = 460.10 + 605.03; 4.6 = 631.2 + 605.03
        res = vpp.build_built_ins(
            "full_floor", population=1000, footprint=3000, norms=spb,
        )
        assert not res.overflow
        s_44 = next(b.area_m2 for b in res.built_ins if b.vri_code == "4.4")
        s_46 = next(b.area_m2 for b in res.built_ins if b.vri_code == "4.6")
        # 4.4 + 4.6 + 3.3 + 3.4.1 + 3.5.1 = footprint (с погрешностью)
        total = sum(b.area_m2 for b in res.built_ins)
        assert total == pytest.approx(3000, abs=0.1)
        # extra поделена поровну: разница 4.6-4.4 = min_4.6-min_4.4 = 631.2-460.10
        assert s_46 - s_44 == pytest.approx(631.2 - 460.10, abs=0.01)

    def test_half_floor(self, spb):
        """Вариант 2: 50% этажа. Сумма = footprint × 0.5."""
        res = vpp.build_built_ins(
            "half_floor", population=1000, footprint=4000, norms=spb,
        )
        # target = 2000 м²
        total = sum(b.area_m2 for b in res.built_ins)
        assert total == pytest.approx(2000, abs=0.1)

    def test_full_floor_overflow(self, spb):
        """Min не помещается → overflow, WARNING."""
        # footprint = 1000 м², min всего = 1789.94 → не помещается
        res = vpp.build_built_ins(
            "full_floor", population=1000, footprint=1000, norms=spb,
        )
        assert res.overflow
        assert any("превышает" in w for w in res.warnings)


# ---------------------------------------------------------------------------
# Парковки ВПП — единый коэффициент (v0.7.1.1)
# ---------------------------------------------------------------------------

class TestVppParkingAverage:
    """Все ВПП считаются по среднему коэффициенту 64 м²/м.м.
    Продвинутая формула для 3.4.1 / 3.5.1 убрана для простоты."""

    def test_uniform_coefficient(self, spb):
        """parking.vpp.m2_per_place = 64 (1.56 м/м на 100 м²)."""
        assert spb.resolve("parking.vpp.m2_per_place") == 64

    def test_no_advanced_parking_function(self):
        """Функция advanced_parking_for_vri удалена в v0.7.1.1."""
        assert not hasattr(vpp, "advanced_parking_for_vri")


# ---------------------------------------------------------------------------
# Интеграция: список ВПП в CalculationOptions
# ---------------------------------------------------------------------------

class TestBuiltInListIntegration:
    def test_built_in_list_field_default_empty(self):
        opts = CalculationOptions(floors=12)
        assert opts.built_in_list == []

    def test_built_in_list_works(self, spb, site):
        """Список из двух ВПП: площади и парковки суммируются (по ceil на корпус)."""
        import math
        res = verify_kit(
            1.5, site,
            CalculationOptions(
                floors=12,
                built_in_list=[
                    BuiltInArea(area_m2=1000, vri_code="4.4"),
                    BuiltInArea(area_m2=500, vri_code="4.6"),
                ],
            ),
            spb,
        )
        assert res.built_in_area.value == 1500
        # v0.7.1.1: ceil(1000/64) + ceil(500/64) = 16 + 8 = 24
        assert res.built_in_parking_places.value == math.ceil(1000/64) + math.ceil(500/64)

    def test_legacy_single_still_works(self, spb, site):
        """Legacy single built_in продолжает работать."""
        import math
        res = verify_kit(
            1.5, site,
            CalculationOptions(
                floors=12,
                built_in=BuiltInArea(area_m2=1000, vri_code="4.4"),
            ),
            spb,
        )
        assert res.built_in_area.value == 1000
        assert res.built_in_parking_places.value == math.ceil(1000/64)

    def test_legacy_plus_list_combined(self, spb, site):
        """Single + list складываются."""
        res = verify_kit(
            1.5, site,
            CalculationOptions(
                floors=12,
                built_in=BuiltInArea(area_m2=500, vri_code="4.4"),
                built_in_list=[
                    BuiltInArea(area_m2=300, vri_code="4.6"),
                ],
            ),
            spb,
        )
        assert res.built_in_area.value == 800

    def test_vri_independent_parking(self, spb, site):
        """v0.7.1.1: парковка ВПП не зависит от ВРИ-кода (единый коэф)."""
        import math
        res_44 = verify_kit(
            1.5, site,
            CalculationOptions(
                floors=12,
                built_in_list=[BuiltInArea(area_m2=1000, vri_code="4.4")],
            ),
            spb,
        )
        res_341 = verify_kit(
            1.5, site,
            CalculationOptions(
                floors=12,
                built_in_list=[BuiltInArea(area_m2=1000, vri_code="3.4.1")],
            ),
            spb,
        )
        # Парковки идентичны (зависят только от площади и среднего коэф)
        assert res_44.built_in_parking_places.value == math.ceil(1000/64)
        assert res_341.built_in_parking_places.value == math.ceil(1000/64)
