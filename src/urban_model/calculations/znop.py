"""ЗНОП — кусочно-постоянная функция от КИТ."""

from __future__ import annotations

import math

from urban_model.normatives import Normatives


def znop_per_person(kit: float, norms: Normatives) -> float:
    return norms.resolve("znop_per_person", kit=kit)


def znop_total_area(population: float, kit: float, norms: Normatives) -> float:
    per_person = znop_per_person(kit, norms)
    return population * per_person


def znop_total_area_clustered(
    cluster_pops: list[float], cluster_kits: list[float], norms: Normatives,
) -> tuple[float, float]:
    """ЗНОП покластерно: для каждой зоны — ступень ЗНОП по ЕЁ КИТ.

    Возвращает (суммарная площадь ЗНОП, средневзвешенный ЗНОП/чел).
    Каждая зона: население_i × znop_per_person(КИТ_i, ≤2.50). Точнее
    единого ЗНОП от среднего КИТ — зоны разной плотности нормируются
    раздельно (v0.11.0)."""
    total_area = 0.0
    total_pop = 0.0
    for pop_i, kit_i in zip(cluster_pops, cluster_kits):
        total_area += pop_i * znop_per_person(min(kit_i, 2.50), norms)
        total_pop += pop_i
    pp_avg = (total_area / total_pop) if total_pop > 0 else 0.0
    return total_area, pp_avg


def kit_cap_for_znop(znop_pp: float, norms: Normatives) -> float | None:
    """Верхний предел КИТ для заданного ЗНОП (обратная piecewise ПЗЗ).

    Прямая piecewise: КИТ ≤ 1.59 → ЗНОП=0; ≤ 1.79 → 3; ≤ 1.99 → 4; ≤ 2.50 → 6.
    Обратная: для заданного ЗНОП берём ПОСЛЕДНЮЮ ступень, чьё нормативное
    значение ЗНОП ≤ заданного, и возвращаем её потолок КИТ (up_to).

    v0.11.0 (фикс): раньше искалось ТОЧНОЕ совпадение значения ЗНОП со
    ступенью — поэтому промежуточные (напр. 3.99) давали None, и потолок
    КИТ не применялся вовсе (баг: «вручную ЗНОП<порога» вело себя как
    «без ограничения»). Теперь 3.99 трактуется как ступень «3» (ЗНОП ещё
    не дотянул до 4) → КИТ ≤ 1.79. Это соответствует смыслу piecewise.

    Возвращает потолок КИТ или None (если ступеней нет / ЗНОП покрывает
    последнюю ступень с up_to = inf).
    """
    try:
        node = norms.get("znop_per_person")
    except KeyError:
        return None
    breakpoints = getattr(node, "breakpoints", None)
    if not breakpoints:
        return None
    # ступени отсортированы по возрастанию up_to (и value). Берём последнюю,
    # чьё value <= znop_pp (+epsilon на дробную погрешность).
    cap = None
    for bp in breakpoints:
        if float(bp.value) <= float(znop_pp) + 1e-6:
            cap = None if math.isinf(bp.up_to) else float(bp.up_to)
        else:
            break
    return cap
