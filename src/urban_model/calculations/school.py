"""Расчёт СОШ: места, площадь участка (с надбавками), площадь здания."""

from __future__ import annotations

from urban_model.calculations.distribute import (
    choose_n_objects,
    distribute_places_evenly,
)
from urban_model.calculations.rounding import round_up_to_multiple
from urban_model.normatives import Normatives


def required_places(population: float, places_per_1000: float) -> float:
    return population * places_per_1000 / 1000


def round_places(places: float, multiple: int) -> int:
    return round_up_to_multiple(places, multiple)


def split_into_objects(
    total_places: int,
    spec_capacity: int | None,
    spec_count: int | None,
    capacity_min: int | None,
    capacity_max: int | None,
    multiple: int = 10,
) -> list[int]:
    """Разбить общее число мест СОШ на отдельные корпуса максимально равномерно.

    Поведение симметрично `kindergarten.split_into_objects`:
      - spec_count + spec_capacity → ручное задание;
      - иначе минимизируем число объектов в [capacity_min, capacity_max]
        и распределяем места ровно с кратностью `multiple` (10 для СОШ).
    """
    if spec_count and spec_capacity:
        return [spec_capacity] * spec_count
    if total_places <= 0:
        return []
    cap_max = capacity_max if capacity_max is not None else total_places
    n = choose_n_objects(total_places, capacity_min, cap_max)
    return distribute_places_evenly(total_places, n, multiple)


def plot_area_for_capacity(capacity: int, norms: Normatives) -> float:
    per_place = norms.resolve(
        "social_objects.school.plot_area_per_place", capacity=capacity
    )
    return per_place * capacity


def plot_area_with_extras(
    capacity: int, norms: Normatives, has_pool: bool, has_sport_core: bool
) -> float:
    base = plot_area_for_capacity(capacity, norms)
    extras = 0.0
    if has_pool:
        extras += norms.resolve("social_objects.school.pool_extra_area")
    if has_sport_core:
        extras += norms.resolve("social_objects.school.sport_core_extra_area")
    return base + extras


def building_area_for_capacity(capacity: int, norms: Normatives) -> float:
    per_place = norms.resolve(
        "social_objects.school.building_area_per_place", capacity=capacity
    )
    return per_place * capacity
