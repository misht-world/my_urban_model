"""Расчёт ДОО: места, площадь участка, площадь здания."""

from __future__ import annotations

from urban_model.calculations.distribute import (
    choose_n_objects,
    distribute_places_evenly,
    split_to_allowed_capacities,
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
    allowed_capacities: list[int] | None = None,
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

    # v0.9.31 (аудит P1): если задано только число объектов (без явной
    # вместимости) — распределяем места ровно на это число. Раньше spec_count
    # без spec_capacity молча игнорировался (число объектов авто-выбиралось),
    # из-за чего Optuna-размерность `kg_num_objects` и скан числа ДОО были no-op.
    if spec_count:
        return distribute_places_evenly(total_places, int(spec_count), multiple)

    # v0.12.4: режим «только нормативная наполняемость» — вместимости строго
    # из типового списка СП/КОБр (allowed_capacities), а не произвольным кратным.
    if allowed_capacities:
        return split_to_allowed_capacities(total_places, allowed_capacities, capacity_min)

    n = choose_n_objects(total_places, capacity_min, capacity_max)
    return distribute_places_evenly(total_places, n, multiple)


def plot_area_for_capacity(
    capacity: int,
    norms: Normatives,
    building_type: str = "detached",
) -> float:
    """Площадь ЗУ одного ДОО.

    Для встроенно-пристроенного (built_in) — норматив 24 м²/место (ПЗЗ СПб).
    Для отдельно стоящего (detached) — piecewise по вместимости (РМД 31-07-2009).
    """
    if building_type == "built_in":
        per_place = norms.resolve(
            "social_objects.kindergarten.plot_area_per_place_built_in"
        )
    else:
        per_place = norms.resolve(
            "social_objects.kindergarten.plot_area_per_place", capacity=capacity
        )
    return per_place * capacity


def building_area_for_capacity(capacity: int, norms: Normatives) -> float:
    per_place = norms.resolve(
        "social_objects.kindergarten.building_area_per_place", capacity=capacity
    )
    return per_place * capacity


def total_areas(
    capacities: list[int],
    norms: Normatives,
    building_type: str = "detached",
) -> tuple[float, float]:
    """Сумма площадей участков и зданий по списку вместимостей объектов."""
    plot = sum(plot_area_for_capacity(c, norms, building_type) for c in capacities)
    bld = sum(building_area_for_capacity(c, norms) for c in capacities)
    return plot, bld


def round_places(places: float, multiple: int) -> int:
    return round_up_to_multiple(places, multiple)
