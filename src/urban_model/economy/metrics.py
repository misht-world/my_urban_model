"""Финансовые метрики (profit, margin, ROI, profit/site_m2).

NPV / IRR / cash flow — отложены на v0.8.1.
"""

from __future__ import annotations

from urban_model.economy.cost import calc_cost
from urban_model.economy.result import CostBreakdown, EconomicMetrics, RevenueBreakdown
from urban_model.economy.revenue import calc_revenue
from urban_model.normatives import Normatives


def calc_metrics(
    cost: CostBreakdown,
    revenue: RevenueBreakdown,
    site_area_m2: float,
    apartments_area: float = 0.0,
    gfa_total: float = 0.0,
) -> EconomicMetrics:
    """Финальные метрики проекта."""
    profit = revenue.total - cost.total
    margin = profit / revenue.total if revenue.total > 1e-9 else 0.0
    roi = profit / cost.total if cost.total > 1e-9 else 0.0
    profit_per_m2 = profit / site_area_m2 if site_area_m2 > 1e-9 else 0.0

    # v0.9.14: социальная нагрузка отдельным блоком.
    social_cost = cost.kindergarten + cost.school + cost.social_parking
    social_revenue = revenue.social_compensation
    net_social_burden = social_cost - social_revenue
    profit_before_social = profit + net_social_burden
    profit_before_land = profit + cost.fixed
    # v0.9.31 (аудит P2): «выход жилья» = площадь квартир / ОБЩАЯ GFA.
    # Раньше знаменатель был gfa−ВПП, что включало здание встроенного ДОО и
    # давало почти константу. Теперь метрика честно падает при росте ВПП/соц.
    sellable_ratio = (
        apartments_area / gfa_total if gfa_total > 1e-9 else 0.0
    )

    return EconomicMetrics(
        cost=cost,
        revenue=revenue,
        profit=profit,
        margin=margin,
        roi=roi,
        profit_per_site_m2=profit_per_m2,
        net_social_burden=net_social_burden,
        profit_before_social=profit_before_social,
        profit_before_land=profit_before_land,
        sellable_ratio=sellable_ratio,
    )


def calc_economy(tep, options, site, norms: Normatives) -> EconomicMetrics:
    """Полный расчёт экономики проекта по результатам ТЭП.

    Используется в `forward.py` сразу после построения TEPResult, чтобы
    дополнить его экономическими метриками.

    Args:
        tep: TEPResult
        options: CalculationOptions (нужен residential_class, floors, parking)
        site: Site (для area_m2)
        norms: загруженные нормативы.
    """
    cost = calc_cost(tep, options, norms)
    revenue = calc_revenue(tep, options, norms)
    apt = float(tep.apartments_area.value or 0.0)
    gfa = float(tep.gfa.value or 0.0)
    return calc_metrics(
        cost, revenue, float(site.area_m2),
        apartments_area=apt, gfa_total=gfa,
    )
