"""Расчёт ДОО: места, площадь участка, площадь здания."""

from __future__ import annotations

from urban_model.calculations.distribute import (
    choose_n_objects,
    distribute_places_evenly,
)
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
    multiple: int = 5,
) -> list[int]:
    """Разбить общее число мест на отдельные ДОО максимально равномерно.

    Если задано `spec_count` и `spec_capacity` — используем их (ручной режим).
    Иначе:
      1. Определяем минимальное число объектов (`n`), при котором каждый
         помещается в `[capacity_min, capacity_max]`.
      2. Распределяем места между `n` объектами максимально равномерно,
         сохраняя кратность `multiple` (5 для ДОО / 10 для СОШ).
      3. Между объектами разница либо 0, либо ровно `multiple` мест.
    """
    if spec_count and spec_capacity:
        return [spec_capacity] * spec_count

    if total_places <= 0:
        return []

    n = choose_n_objects(total_places, capacity_min, capacity_max)
    return distribute_places_evenly(total_places, n, multiple)


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
