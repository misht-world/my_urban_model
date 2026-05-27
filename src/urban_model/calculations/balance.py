"""Баланс территории квартала.

`is_feasible` объединяет два ограничения:
1. Территориальный баланс: сумма компонентов не превышает площадь квартала.
2. Норматив озеленения квартала: фактическое озеленение ≥ 25% площади
   квартала без участков ДОО/СОШ (СП 42.13330.2016 → spb.yaml/russia.yaml).

Если одно из них нарушено — `is_feasible = False`.

v0.9.16: свободный резерв квартала (surplus) автоматически
засчитывается в `greening_actual` как «потенциальное озеленение» —
территория, не занятая никакими компонентами, фактически становится
открытым благоустройством. Раньше при низкой плотности (КИТ ~0.1)
бисекция падала из-за дефицита формального ЗНОП, хотя на квартале
огромный пустой резерв.
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
    # v0.9.16: surplus входит в эффективное озеленение. Любая часть
    # квартала, не занятая жильём/соц/парковками/проездами, фактически
    # становится зелёным открытым пространством (двор, газон, площадка).
    effective_greening = greening_actual + max(0.0, surplus)
    greening_ok = effective_greening >= greening_required - 1e-6  # допуск 1 мм²
    return BalanceCheck(
        site_area=site_area,
        required_total=required,
        surplus=surplus,
        components=components,
        is_feasible=territorial_ok and greening_ok,
        # В TEPResult сохраняем effective (включающий surplus) — пользователь
        # видит реальную «всю зелень квартала», а не только заявленные
        # источники. Это даёт согласованный взгляд с нормативом 25%.
        greening_actual=effective_greening,
        greening_required=greening_required,
    )
