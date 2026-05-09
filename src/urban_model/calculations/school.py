"""Расчёт СОШ: места, площадь участка (с надбавками), площадь здания."""

from __future__ import annotations

from urban_model.calculations.rounding import round_up_to_multiple
from urban_model.normatives import Normatives


def required_places(population: float, places_per_1000: float) -> float:
    return population * places_per_1000 / 1000


def round_places(places: float, multiple: int) -> int:
    return round_up_to_multiple(places, multiple)


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
