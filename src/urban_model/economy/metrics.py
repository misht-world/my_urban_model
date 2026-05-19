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
) -> EconomicMetrics:
    """Финальные метрики проекта."""
    profit = revenue.total - cost.total
    margin = profit / revenue.total if revenue.total > 1e-9 else 0.0
    roi = profit / cost.total if cost.total > 1e-9 else 0.0
    profit_per_m2 = profit / site_area_m2 if site_area_m2 > 1e-9 else 0.0
    return EconomicMetrics(
        cost=cost,
        revenue=revenue,
        profit=profit,
        margin=margin,
        roi=roi,
        profit_per_site_m2=profit_per_m2,
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
    return calc_metrics(cost, revenue, float(site.area_m2))
