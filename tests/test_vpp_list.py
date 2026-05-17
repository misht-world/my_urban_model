"""Тесты v0.7.1 — список ВПП с 5 вариантами размещения.

Нормативы НГП СПб для обязательных ВПП:
- 4.4 (торговля):  460.10 м²/1000 чел (сразу в м²)
- 4.6 (общепит):   105.20 посад.мест/1000 чел × 6 м²/место = 631.2 м²/1000
- 3.3 (быт.обсл.): 19.00 раб.мест/1000 чел × 20 м²/раб.место = 380 м²/1000
- 3.4.1 (поликл.): 26.33 посещ.в смену/1000 × 8 м²/посещ. = 210.64 м²/1000
- 3.5.1 (искусство): 7.20 мест/1000 × 15 м²/место = 108 м²/1000

Парковки 3.4.1 (1 м/м на 5 раб + 1 м/м на 40 посет) и 3.5.1
(1 м/м на 5 раб + 1 м/м на 100 учащ, min 2) — отдельная формула.
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
        assert m.medical_3_4_1 == pytest.approx(26.33 * 8)
        assert m.arts_3_5_1 == pytest.approx(7.20 * 15)

    def test_total_1000_people(self, spb):
        """Сумма обязательных ВПП для 1000 чел ≈ 1789.94 м²."""
        m = vpp.compute_mandatory_areas(1000.0, spb)
        assert m.total == pytest.approx(460.10 + 631.2 + 380 + 210.64 + 108, abs=0.01)

    def test_zero_population(self, spb):
        m = vpp.compute_mandatory_areas(0.0, spb)
        assert m.total == 0.0


# ---------------------------------------------------------------------------
# build_built_ins — 5 вариантов
# ---------------------------------------------------------------------------

class TestVppModes:
    def test_min_only(self, spb):
        """Вариант 3: только обязательный минимум по всем 5 ВРИ."""
        res = vpp.build_built_ins("min_only", population=1000, footprint=0, norms=spb)
        # 5 объектов
        assert len(res.built_ins) == 5
        vri_codes = sorted([b.vri_code for b in res.built_ins])
        assert vri_codes == ["3.3", "3.4.1", "3.5.1", "4.4", "4.6"]
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
# Продвинутая парковка для 3.4.1 / 3.5.1
# ---------------------------------------------------------------------------

class TestAdvancedParking:
    def test_3_4_1_typical(self, spb):
        """Поликлиника на 1000 м²: visits=125, workers=18.75
        → ceil(18.75/5) + ceil(125/40) = 4 + 4 = 8."""
        places = vpp.advanced_parking_for_vri("3.4.1", 1000.0, spb)
        # visits = 1000/8 = 125; workers = 125 × 0.15 = 18.75
        # ceil(18.75/5) = 4; ceil(125/40) = 4; sum = 8
        assert places == 8

    def test_3_5_1_typical(self, spb):
        """Школа искусств на 300 м²: students=20, workers=2
        → ceil(2/5)+ceil(20/100) = 1+1 = 2 → max(2, 2)=2."""
        places = vpp.advanced_parking_for_vri("3.5.1", 300.0, spb)
        # students = 300/15 = 20; workers = 20 × 0.10 = 2
        # ceil(2/5) = 1; ceil(20/100) = 1; sum = 2; min = 2 → 2
        assert places == 2

    def test_3_5_1_minimum_kicks_in(self, spb):
        """Очень маленькая школа искусств: всё равно min 2."""
        places = vpp.advanced_parking_for_vri("3.5.1", 10.0, spb)
        assert places == 2

    def test_other_vri_returns_none(self, spb):
        """Для 4.4 / 4.6 / 3.3 продвинутая формула не применяется."""
        assert vpp.advanced_parking_for_vri("4.4", 1000.0, spb) is None
        assert vpp.advanced_parking_for_vri("4.6", 1000.0, spb) is None
        assert vpp.advanced_parking_for_vri("3.3", 1000.0, spb) is None


# ---------------------------------------------------------------------------
# Интеграция: список ВПП в CalculationOptions
# ---------------------------------------------------------------------------

class TestBuiltInListIntegration:
    def test_built_in_list_field_default_empty(self):
        opts = CalculationOptions(floors=12)
        assert opts.built_in_list == []

    def test_built_in_list_works(self, spb, site):
        """Список из двух ВПП: и площади и парковки суммируются."""
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
        # Общая площадь ВПП = 1500
        assert res.built_in_area.value == 1500
        # Парковки ВПП: 4.4 → 1000/50 = 20; 4.6 → 500/25 = 20; всего 40
        assert res.built_in_parking_places.value == 40

    def test_legacy_single_still_works(self, spb, site):
        """Legacy single built_in продолжает работать."""
        res = verify_kit(
            1.5, site,
            CalculationOptions(
                floors=12,
                built_in=BuiltInArea(area_m2=1000, vri_code="4.4"),
            ),
            spb,
        )
        assert res.built_in_area.value == 1000
        assert res.built_in_parking_places.value == 20

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

    def test_vri_341_uses_advanced_parking(self, spb, site):
        """ВПП с ВРИ 3.4.1 использует продвинутую формулу парковок."""
        res = verify_kit(
            1.5, site,
            CalculationOptions(
                floors=12,
                built_in_list=[BuiltInArea(area_m2=1000, vri_code="3.4.1")],
            ),
            spb,
        )
        # Должна быть применена формула: 8 м/м (см. test_3_4_1_typical)
        assert res.built_in_parking_places.value == 8
