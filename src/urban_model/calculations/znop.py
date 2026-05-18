"""ЗНОП — кусочно-постоянная функция от КИТ."""

from __future__ import annotations

import math

from urban_model.normatives import Normatives


def znop_per_person(kit: float, norms: Normatives) -> float:
    return norms.resolve("znop_per_person", kit=kit)


def znop_total_area(population: float, kit: float, norms: Normatives) -> float:
    per_person = znop_per_person(kit, norms)
    return population * per_person


def kit_cap_for_znop(znop_pp: float, norms: Normatives) -> float | None:
    """Верхний предел КИТ для заданного ЗНОП (обратная piecewise ПЗЗ).

    Norm.СПб: КИТ ≤ 1.59 → ЗНОП=0; ≤ 1.79 → 3; ≤ 1.99 → 4; ≤ 2.50 → 6.
    Если зафиксировать ЗНОП=3 (например override), КИТ автоматически
    ограничен 1.79 по нормативу. Возвращает None если значение ЗНОП
    не соответствует ни одной ступени piecewise.
    """
    try:
        node = norms.get("znop_per_person")
    except KeyError:
        return None
    breakpoints = getattr(node, "breakpoints", None)
    if not breakpoints:
        return None
    for bp in breakpoints:
        if abs(float(bp.value) - float(znop_pp)) < 1e-6:
            return None if math.isinf(bp.up_to) else float(bp.up_to)
    return None
