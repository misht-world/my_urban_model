"""Баланс территории квартала.

`is_feasible` объединяет два ограничения:
1. Территориальный баланс: сумма компонентов не превышает площадь квартала.
2. Норматив озеленения квартала: фактическое озеленение ≥ 25% площади
   квартала без участков ДОО/СОШ (СП 42.13330.2016 → spb.yaml/russia.yaml).

Если одно из них нарушено — `is_feasible = False`.
"""

from __future__ import annotations

from urban_model.models.result import BalanceCheck


def compute_balance(
    site_area: float,
    components: dict[str, float],
    greening_actual: float = 0.0,
    greening_required: float = 0.0,
) -> BalanceCheck:
    required = sum(components.values())
    surplus = site_area - required
    territorial_ok = surplus >= 0
    greening_ok = greening_actual >= greening_required - 1e-6  # допуск 1 мм²
    return BalanceCheck(
        site_area=site_area,
        required_total=required,
        surplus=surplus,
        components=components,
        is_feasible=territorial_ok and greening_ok,
        greening_actual=greening_actual,
        greening_required=greening_required,
    )
