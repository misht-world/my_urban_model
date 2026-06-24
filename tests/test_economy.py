"""Тесты v0.8.0 — экономика в условных единицах."""

from __future__ import annotations

import pytest

from urban_model import verify_kit
from urban_model.economy import (
    CostBreakdown,
    EconomicMetrics,
    RevenueBreakdown,
    calc_cost,
    calc_economy,
    calc_metrics,
    calc_revenue,
)
from urban_model.models import CalculationOptions, Site
from urban_model.normatives import load_normatives


@pytest.fixture(scope="module")
def spb():
    return load_normatives("spb")


@pytest.fixture
def site():
    return Site(area_m2=50_000)


# ---------------------------------------------------------------------------
# Нормативы — sanity check
# ---------------------------------------------------------------------------

class TestEconomyNormatives:
    """Стартовые коэффициенты из SPEC.md загружены."""

    def test_base_unit_is_1(self, spb):
        assert spb.resolve("economy.base_unit") == 1.0

    def test_residential_cost_by_floors(self, spb):
        # 9 эт. monolith — референс 1.00
        assert spb.resolve("economy.construction.residential_by_floors", floors=9) == 1.00
        # 12 эт. → 1.10
        assert spb.resolve("economy.construction.residential_by_floors", floors=12) == 1.10
        # 25 эт. → 1.48
        assert spb.resolve("economy.construction.residential_by_floors", floors=25) == 1.48

    def test_underground_progression(self, spb):
        # Прогрессия уровней
        assert spb.resolve("economy.construction.underground_progression", level=1) == 1.00
        assert spb.resolve("economy.construction.underground_progression", level=2) == 1.30
        assert spb.resolve("economy.construction.underground_progression", level=3) == 1.65

    def test_residential_sale_prices(self, spb):
        # Цены по классам
        assert spb.resolve(
            "economy.sale_prices.residential_by_class", residential_class="economy"
        ) == 1.70
        assert spb.resolve(
            "economy.sale_prices.residential_by_class", residential_class="comfort"
        ) == 1.95
        assert spb.resolve(
            "economy.sale_prices.residential_by_class", residential_class="business"
        ) == 2.80


# ---------------------------------------------------------------------------
# Cost / Revenue / Metrics — ручные проверки формул
# ---------------------------------------------------------------------------

class TestEconomyInResult:
    """TEPResult.economy заполняется автоматически в forward.py."""

    def test_economy_present_in_result(self, spb, site):
        """После verify_kit поле economy не None."""
        res = verify_kit(1.5, site, CalculationOptions(floors=12), spb)
        assert res.economy is not None
        assert isinstance(res.economy, EconomicMetrics)
        assert isinstance(res.economy.cost, CostBreakdown)
        assert isinstance(res.economy.revenue, RevenueBreakdown)

    def test_cost_residential_formula(self, spb, site):
        """Жильё: residential_gfa × C_base(floors)."""
        # include_add_education=False: встроенное доп. обр. иначе вычло бы своё
        # здание из жилой GFA (v0.12.15) и формула residential_gfa = gfa−bi сломалась.
        res = verify_kit(
            1.5, site, CalculationOptions(floors=12, include_add_education=False), spb
        )
        # Для 12 эт. C_base = 1.10
        gfa = res.gfa.value
        bi = res.built_in_area.value or 0.0
        residential_gfa = gfa - bi
        expected = residential_gfa * 1.10
        assert res.economy.cost.residential == pytest.approx(expected, rel=0.001)

    def test_revenue_residential_by_class(self, spb, site):
        """Выручка от жилья = apartments_area × P_class."""
        # comfort = 1.95
        res = verify_kit(
            1.5, site,
            CalculationOptions(floors=12, residential_class="comfort"),
            spb,
        )
        assert res.economy.revenue.residential == pytest.approx(
            res.apartments_area.value * 1.95, rel=0.001
        )

    def test_revenue_changes_with_class(self, spb, site):
        """Бизнес-класс → выручка выше эконом-класса."""
        res_eco = verify_kit(
            1.5, site,
            CalculationOptions(floors=12, residential_class="economy"),
            spb,
        )
        res_biz = verify_kit(
            1.5, site,
            CalculationOptions(floors=12, residential_class="business"),
            spb,
        )
        # Площадь та же → только цена м² различается
        assert res_biz.economy.revenue.residential > res_eco.economy.revenue.residential
        # Отношение цен 2.80/1.70 ≈ 1.647
        ratio = res_biz.economy.revenue.residential / res_eco.economy.revenue.residential
        assert ratio == pytest.approx(2.80 / 1.70, rel=0.001)


# ---------------------------------------------------------------------------
# Прогрессия подземных парковок
# ---------------------------------------------------------------------------

class TestUndergroundProgression:
    """2 уровня подземных = level_1 + level_1 × 1.30, не 2 × level_1."""

    def test_two_levels_not_double(self, spb, site):
        """Стоимость 2 уровней = (1.0 + 1.3) × площадь_1_уровня × C_ug."""
        from urban_model.economy.cost import _cost_underground_parking
        # 200 м/м, 2 уровня, 35 м²/место, C_ug = 1.00
        cost = _cost_underground_parking(
            places=200, levels=2, m2_per_space=35,
            base_per_m2=1.00, norms=spb,
        )
        # Каждый уровень = 100 мест × 35 = 3500 м²
        # Уровень 1: 3500 × 1.00 × 1.00 = 3500
        # Уровень 2: 3500 × 1.00 × 1.30 = 4550
        # Итого = 8050
        assert cost == pytest.approx(8050, rel=0.001)
        # Точно НЕ равно 2 × level_1 = 7000
        assert cost != pytest.approx(7000, rel=0.001)


# ---------------------------------------------------------------------------
# Инвариантность ранжирования при масштабировании
# ---------------------------------------------------------------------------

class TestRankingInvariance:
    """При умножении всех цен и стоимостей на одну константу ранжирование
    сценариев не должно меняться (свойство относительной модели)."""

    def test_profit_per_m2_scales_linearly(self, spb, site):
        """Если умножить все цены × 10, profit_per_site_m2 × 10."""
        res = verify_kit(1.5, site, CalculationOptions(floors=12), spb)
        # Берём оригинальные cost / revenue
        cost = res.economy.cost
        revenue = res.economy.revenue
        # Создаём «масштабированные» вручную через calc_metrics
        cost_x10 = cost.model_copy(update={"total": cost.total * 10})
        revenue_x10 = revenue.model_copy(update={"total": revenue.total * 10})
        scaled = calc_metrics(cost_x10, revenue_x10, site.area_m2)
        # profit ×10, margin/ROI без изменений
        assert scaled.profit == pytest.approx(res.economy.profit * 10)
        assert scaled.margin == pytest.approx(res.economy.margin)
        assert scaled.roi == pytest.approx(res.economy.roi)


# ---------------------------------------------------------------------------
# Optuna: режим profit
# ---------------------------------------------------------------------------

class TestOptunaProfitObjective:
    def test_objective_field_default(self):
        """SearchSpace.objective по умолчанию = apartments_area."""
        from urban_model.optimize import SearchSpace
        space = SearchSpace(floors_range=(10, 15))
        assert space.objective == "apartments_area"

    def test_optimizer_uses_profit(self, spb, site):
        """С objective='profit' лучший top.tep.economy.profit ≥ профита worst-сценария."""
        from urban_model.optimize import SearchSpace, optimize_max_apartments
        base = CalculationOptions(floors=12)
        space = SearchSpace(
            floors_range=(8, 25),
            objective="profit",
        )
        report = optimize_max_apartments(site, base, spb, space, n_trials=15)
        if report.top_n and len(report.top_n) >= 2:
            # Топ-1 должен иметь профит >= топ-N
            top1_profit = report.top_n[0].tep.economy.profit
            for other in report.top_n[1:]:
                assert top1_profit >= other.tep.economy.profit - 1e-3


# ---------------------------------------------------------------------------
# v0.9.14 — sale_rate парковок + соц. метрики
# ---------------------------------------------------------------------------

class TestParkingSaleRate:
    """Доля реализации м/м по классу применяется к выручке парковок."""

    def test_sale_rate_loaded_by_class(self, spb):
        assert spb.resolve(
            "economy.sale_rates.parking_by_class", residential_class="economy"
        ) == 0.20
        assert spb.resolve(
            "economy.sale_rates.parking_by_class", residential_class="business"
        ) == 0.75

    def test_business_parking_revenue_higher_than_economy(self, spb, site):
        """При одинаковых ТЭП выручка парковок бизнес-класса выше эконома
        ровно из-за разной доли реализации (0.75 vs 0.20)."""
        opts_e = CalculationOptions(floors=12, residential_class="economy")
        opts_b = CalculationOptions(floors=12, residential_class="business")
        rev_e = verify_kit(1.5, site, opts_e, spb).economy.revenue
        rev_b = verify_kit(1.5, site, opts_b, spb).economy.revenue
        park_e = rev_e.parking_open + rev_e.parking_multilevel + rev_e.parking_underground
        park_b = rev_b.parking_open + rev_b.parking_multilevel + rev_b.parking_underground
        if park_e > 0:
            assert park_b > park_e


class TestSocialMetrics:
    """net_social_burden / profit_before_social / sellable_ratio."""

    def test_profit_before_social_identity(self, spb, site):
        e = verify_kit(1.5, site, CalculationOptions(floors=12), spb).economy
        assert e.profit_before_social == pytest.approx(e.profit + e.net_social_burden)

    def test_net_social_burden_definition(self, spb, site):
        e = verify_kit(1.5, site, CalculationOptions(floors=12), spb).economy
        cb, rb = e.cost, e.revenue
        expected = (cb.kindergarten + cb.school + cb.social_parking) - rb.social_compensation
        assert e.net_social_burden == pytest.approx(expected)

    def test_sellable_ratio_in_range(self, spb, site):
        e = verify_kit(1.5, site, CalculationOptions(floors=12), spb).economy
        assert 0.0 < e.sellable_ratio <= 1.0

    def test_profit_before_land_equals_profit_when_no_land(self, spb, site):
        e = verify_kit(1.5, site, CalculationOptions(floors=12), spb).economy
        # fixed (земля/ТУ/снос) = 0 → profit_before_land == profit
        assert e.profit_before_land == pytest.approx(e.profit)


class TestEconomyIndex:
    """v0.12.1: стабильный economy_index = 100 × выручка / себестоимость."""

    def test_index_definition(self, spb, site):
        e = verify_kit(1.5, site, CalculationOptions(floors=12), spb).economy
        expected = 100.0 * e.revenue.total / e.cost.total
        assert e.economy_index == pytest.approx(expected)

    def test_index_equals_100_plus_roi(self, spb, site):
        # economy_index = 100 × (1 + ROI) = 100 + ROI%
        e = verify_kit(1.5, site, CalculationOptions(floors=12), spb).economy
        assert e.economy_index == pytest.approx(100.0 * (1.0 + e.roi))

    def test_index_above_100_when_profitable(self, spb, site):
        e = verify_kit(1.5, site, CalculationOptions(floors=12), spb).economy
        if e.profit > 0:
            assert e.economy_index > 100.0
        elif e.profit < 0:
            assert e.economy_index < 100.0


class TestIncludeEconomyToggle:
    """v0.9.29: include_economy=False → result.economy is None."""

    def test_economy_computed_by_default(self, spb, site):
        r = verify_kit(1.5, site, CalculationOptions(floors=12), spb)
        assert r.economy is not None

    def test_economy_skipped_when_excluded(self, spb, site):
        r = verify_kit(
            1.5, site, CalculationOptions(floors=12, include_economy=False), spb
        )
        assert r.economy is None
