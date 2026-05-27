"""Тесты на экономику custom_objects по разным ВРИ (v0.9.12).

Закрывает AUDIT P2-4 (документация эвристики ВРИ→ставка) частично через
тесты — фиксируем ожидаемое поведение.

Правила (см. economy/cost.py и economy/revenue.py):
- ВРИ 3.x (социальные: ДОО/СОШ/спорт/медицина) → cost=c_kg, revenue=0
- Любой другой ВРИ (4.x торговля, 5.x спорт-открытый, 2.x жильё и т.п.)
    → cost=c_vpp, revenue=p_vpp (коммерческая ставка)
"""

from __future__ import annotations

import pytest

from urban_model import verify_kit
from urban_model.models import CalculationOptions, Site
from urban_model.models.custom_object import CustomObject
from urban_model.normatives import load_normatives


@pytest.fixture(scope="module")
def spb():
    return load_normatives("spb")


@pytest.fixture
def site():
    return Site(area_m2=50_000)


@pytest.fixture(scope="module")
def commercial_rate(spb):
    """c_vpp — себестоимость коммерческой ВПП за м² (для unit cost)."""
    return float(spb.resolve("economy.construction.vpp_commercial"))


@pytest.fixture(scope="module")
def social_rate(spb):
    """c_kg — себестоимость социального здания за м² (используется как c для 3.x)."""
    return float(spb.resolve("economy.construction.kindergarten"))


@pytest.fixture(scope="module")
def commercial_sale_price(spb):
    """p_vpp — цена продажи коммерческой площади за м²."""
    return float(spb.resolve("economy.sale_prices.vpp_commercial"))


# ---------------------------------------------------------------------------
# Симметрия cost↔revenue по ВРИ (AUDIT P0-2 в v0.9.8)
# ---------------------------------------------------------------------------

class TestCustomObjectVriSymmetry:
    """Проверяем что для одного и того же custom_object cost и revenue
    используют согласованные ставки (не «cost как commercial, revenue 0»).
    """

    def test_vri_3_social_zero_revenue(self, spb, site, commercial_sale_price):
        """ВРИ 3.x (соцобъект) — выручка = 0, себестоимость > 0 (соцнагрузка)."""
        obj = CustomObject(
            name="Поликлиника",
            vri_code="3.4.1",
            plot_area_m2=500.0,
            floor_area_m2=1000.0,
        )
        opts = CalculationOptions(floors=12, custom_objects=[obj])
        res = verify_kit(1.0, site, opts, spb)
        assert res.economy is not None
        # Себестоимость custom-объектов > 0
        assert res.economy.cost.custom_objects > 0
        # Выручка от соцобъекта = 0
        assert res.economy.revenue.custom_commercial == 0.0

    def test_vri_4_commercial_has_revenue(self, spb, site, commercial_sale_price):
        """ВРИ 4.x (коммерция) — есть и cost, и revenue."""
        obj = CustomObject(
            name="Магазин",
            vri_code="4.4",
            plot_area_m2=500.0,
            floor_area_m2=1000.0,
        )
        opts = CalculationOptions(floors=12, custom_objects=[obj])
        res = verify_kit(1.0, site, opts, spb)
        assert res.economy is not None
        assert res.economy.cost.custom_objects > 0
        # Выручка = floor_area × p_vpp
        expected_rev = 1000.0 * commercial_sale_price
        assert res.economy.revenue.custom_commercial == pytest.approx(expected_rev, rel=1e-3)

    def test_vri_5_sport_has_revenue_after_v098(self, spb, site, commercial_sale_price):
        """ВРИ 5.x (спорт-открытый) — после v0.9.8 имеет коммерческую выручку.
        Раньше (v0.9.7) cost списывался как commercial, а revenue был 0 →
        системный убыток для любого 5.x объекта."""
        obj = CustomObject(
            name="ФОК",
            vri_code="5.1.1",
            plot_area_m2=500.0,
            floor_area_m2=800.0,
        )
        opts = CalculationOptions(floors=12, custom_objects=[obj])
        res = verify_kit(1.0, site, opts, spb)
        assert res.economy is not None
        # Симметрия восстановлена: и cost, и revenue ненулевые
        expected_rev = 800.0 * commercial_sale_price
        assert res.economy.revenue.custom_commercial == pytest.approx(expected_rev, rel=1e-3)

    def test_multiple_custom_objects_sum_correctly(self, spb, site, commercial_sale_price):
        """Несколько объектов: revenue суммируется по non-(3.x)."""
        objs = [
            CustomObject(name="Магазин", vri_code="4.4",
                         plot_area_m2=300.0, floor_area_m2=500.0),
            CustomObject(name="ФОК", vri_code="5.1.1",
                         plot_area_m2=400.0, floor_area_m2=600.0),
            CustomObject(name="Поликлиника", vri_code="3.4.1",
                         plot_area_m2=500.0, floor_area_m2=1000.0),
        ]
        opts = CalculationOptions(floors=12, custom_objects=objs)
        res = verify_kit(1.0, site, opts, spb)
        # Только 4.4 + 5.1.1 идут в commercial revenue (3.4.1 = соц)
        expected_rev = (500.0 + 600.0) * commercial_sale_price
        assert res.economy.revenue.custom_commercial == pytest.approx(expected_rev, rel=1e-3)

    def test_no_custom_objects_zero_components(self, spb, site):
        """Без custom_objects соответствующие поля экономики = 0."""
        opts = CalculationOptions(floors=12, custom_objects=[])
        res = verify_kit(1.0, site, opts, spb)
        assert res.economy.cost.custom_objects == 0.0
        assert res.economy.revenue.custom_commercial == 0.0
