"""Экономика проекта (v0.8.0) — в условных единицах.

1.0 у.е. = себестоимость м² жилья 9-эт. монолита со standard-отделкой.
Подробности — `D:\\Github\\my_urban_model\\my_development_calc\\SPEC.md`.
"""

from urban_model.economy.cost import calc_cost
from urban_model.economy.metrics import calc_economy, calc_metrics
from urban_model.economy.result import CostBreakdown, EconomicMetrics, RevenueBreakdown
from urban_model.economy.revenue import calc_revenue

__all__ = [
    "calc_cost",
    "calc_revenue",
    "calc_metrics",
    "calc_economy",
    "CostBreakdown",
    "RevenueBreakdown",
    "EconomicMetrics",
]
