"""Математика кластеров этажности (v0.9.28).

Чистые функции без побочных эффектов. На вход — список FloorCluster и
fallback-этажность (когда кластеров нет), на выход — числа.

Все величины масштабно-инвариантны: абсолютные площади кластеров влияют
только относительно друг друга (через доли). Поэтому точное равенство
Σ area_m2 = S_квартала не требуется для корректности — но желательно для
наглядности (forward.py выдаёт warning при существенном расхождении).
"""

from __future__ import annotations

from collections.abc import Sequence

from urban_model.models.cluster import FloorCluster


def effective_floors(clusters: Sequence[FloorCluster], fallback_floors: float) -> float:
    """Средневзвешенная этажность floors_eff = Σ(A_i·F_i)/ΣA_i.

    Если кластеров нет — возвращает fallback_floors (одиночная этажность).
    """
    if not clusters:
        return float(fallback_floors)
    total_a = sum(c.area_m2 for c in clusters)
    if total_a <= 0:
        return float(fallback_floors)
    return sum(c.area_m2 * c.floors for c in clusters) / total_a


def max_floors(clusters: Sequence[FloorCluster], fallback_floors: float) -> float:
    """Максимальная этажность среди кластеров (задаёт потолок КИТ)."""
    if not clusters:
        return float(fallback_floors)
    return float(max(c.floors for c in clusters))


def area_weights(clusters: Sequence[FloorCluster]) -> list[float]:
    """Доли площади A_i/ΣA_i. Совпадают с долями пятна застройки.

    Используются для взвешивания piecewise-проездов по кластерам.
    """
    if not clusters:
        return []
    total_a = sum(c.area_m2 for c in clusters)
    if total_a <= 0:
        return [0.0 for _ in clusters]
    return [c.area_m2 / total_a for c in clusters]


def gfa_weights(clusters: Sequence[FloorCluster]) -> list[float]:
    """Доли GFA w_i = A_i·F_i / Σ(A_j·F_j). Используются для разбивки
    площади квартир / себестоимости по кластерам."""
    if not clusters:
        return []
    denom = sum(c.area_m2 * c.floors for c in clusters)
    if denom <= 0:
        return [0.0 for _ in clusters]
    return [(c.area_m2 * c.floors) / denom for c in clusters]
