"""Проверка вместимости объектов по списку допустимых (типовых) значений.

Используется для ДОО и СОШ: если вместимость не входит в нормативный список,
выдаём WARNING с указанием ближайших меньшего и большего значений.

Логика одинаковая для обоих типов, поэтому вынесена в общий хелпер.
"""

from __future__ import annotations

from typing import Callable, Iterable


def build_warnings(
    buckets: Iterable[int],
    allowed: list[int],
    label: str,
    source: str | None = None,
    skip: Callable[[int], bool] | None = None,
) -> list[str]:
    """Сформировать список WARNING-строк по buckets, не входящим в allowed.

    Args:
        buckets: фактические вместимости объектов (по корпусам).
        allowed: отсортированный список типовых вместимостей.
        label: префикс сообщения, например «ДОО» или «СОШ».
        source: подпись источника списка (источник из YAML, через source_of).
        skip: предикат — если возвращает True, бакет пропускается
              (например, по нему уже выдан другой WARNING про cap_min/cap_max).

    Returns:
        Список готовых WARNING-строк. Не модифицирует входы.
    """
    out: list[str] = []
    for c in buckets:
        if skip and skip(c):
            continue
        if c in allowed:
            continue
        smaller = [a for a in allowed if a < c]
        larger = [a for a in allowed if a > c]
        nearest_lo = max(smaller) if smaller else None
        nearest_hi = min(larger) if larger else None
        parts = []
        if nearest_lo is not None:
            parts.append(f"меньше: {nearest_lo}")
        if nearest_hi is not None:
            parts.append(f"больше: {nearest_hi}")
        hint = "; ".join(parts) if parts else "нет ближайших"
        src_str = f" (источник списка: {source})" if source else ""
        out.append(
            f"{label}: вместимость {c} мест не входит в список типовых{src_str}. "
            f"Ближайшие — {hint}. "
            "Рекомендуется привести к одной из типовых вместимостей."
        )
    return out
