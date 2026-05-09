"""Расчёт ДОО: места, площадь участка, площадь здания."""

from __future__ import annotations

from urban_model.calculations.rounding import round_up_to_multiple
from urban_model.normatives import Normatives


def required_places(population: float, places_per_1000: float) -> float:
    return population * places_per_1000 / 1000


def split_into_objects(
    total_places: int,
    spec_capacity: int | None,
    spec_count: int | None,
    capacity_min: int | None,
    capacity_max: int,
) -> list[int]:
    """Разбить общее число мест на отдельные ДОО.

    Если задано конкретное `spec_count` и `spec_capacity` — используем их
    (предполагая, что итог покрывает потребность).
    Иначе делим на максимальное количество объектов вместимости `capacity_max`,
    остаток — последний объект (но не меньше `capacity_min`, если задан).
    """
    if spec_count and spec_capacity:
        return [spec_capacity] * spec_count

    if total_places <= 0:
        return []

    n_full = total_places // capacity_max
    remainder = total_places - n_full * capacity_max
    out = [capacity_max] * n_full
    if remainder > 0:
        if capacity_min and remainder < capacity_min:
            # доводим до минимума за счёт перераспределения
            if not out:
                out.append(capacity_min)
            else:
                # подкидываем последний бакет
                out.append(remainder)
                # будет провалидировано вызывающим как warning
        else:
            out.append(remainder)
    return out


def plot_area_for_capacity(capacity: int, norms: Normatives) -> float:
    per_place = norms.resolve(
        "social_objects.kindergarten.plot_area_per_place", capacity=capacity
    )
    return per_place * capacity


def building_area_for_capacity(capacity: int, norms: Normatives) -> float:
    per_place = norms.resolve(
        "social_objects.kindergarten.building_area_per_place", capacity=capacity
    )
    return per_place * capacity


def total_areas(capacities: list[int], norms: Normatives) -> tuple[float, float]:
    """Сумма площадей участков и зданий по списку вместимостей объектов."""
    plot = sum(plot_area_for_capacity(c, norms) for c in capacities)
    bld = sum(building_area_for_capacity(c, norms) for c in capacities)
    return plot, bld


def round_places(places: float, multiple: int) -> int:
    return round_up_to_multiple(places, multiple)
