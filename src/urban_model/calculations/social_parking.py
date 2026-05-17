"""Парковки соцобъектов (ДОО, СОШ) — v0.7.0.

Формула ПЗЗ СПб: 1 м/м на 5 работников + 1 м/м на 100 учащихся,
но не менее 2 м/м на каждый объект.

Работники резолвятся из piecewise по capacity объекта
(`social_objects.kindergarten.workers_per_capacity` и
 `social_objects.school.workers_per_capacity` — данные КС).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from urban_model.normatives import Normatives


@dataclass(frozen=True)
class SocialParkingBreakdown:
    """Сводка парковок по соцобъектам."""
    total_places: int
    kindergarten_places: int
    school_places: int
    # Детализация по объектам (capacity, workers, places) — для аудит-трейла
    kindergarten_details: list[tuple[int, int, int]]   # (capacity, workers, places)
    school_details: list[tuple[int, int, int]]


def parking_for_object(
    workers: int,
    students: int,
    norms: Normatives,
) -> int:
    """Парковка одного соцобъекта по формуле ПЗЗ СПб."""
    per_worker = norms.resolve("parking.social_objects.per_worker")
    per_student = norms.resolve("parking.social_objects.per_student")
    minimum = int(norms.resolve("parking.social_objects.minimum"))
    places = math.ceil(workers / per_worker) + math.ceil(students / per_student)
    return max(minimum, int(places))


def compute(
    kg_buckets: list[int],
    sch_buckets: list[int],
    norms: Normatives,
    kg_include: bool = True,
    sch_include: bool = True,
) -> SocialParkingBreakdown:
    """Расчёт парковок для всех ДОО и СОШ объектов на квартале.

    Args:
        kg_buckets: вместимости отдельных корпусов ДОО.
        sch_buckets: вместимости отдельных корпусов СОШ.
        norms: загруженные нормативы.
        kg_include: учитывать парковки ДОО (False → 0).
        sch_include: учитывать парковки СОШ (False → 0).
    """
    kg_details: list[tuple[int, int, int]] = []
    kg_total = 0
    if kg_include:
        for cap in kg_buckets:
            workers = int(norms.resolve(
                "social_objects.kindergarten.workers_per_capacity", capacity=cap
            ))
            places = parking_for_object(workers, cap, norms)
            kg_details.append((cap, workers, places))
            kg_total += places

    sch_details: list[tuple[int, int, int]] = []
    sch_total = 0
    if sch_include:
        for cap in sch_buckets:
            workers = int(norms.resolve(
                "social_objects.school.workers_per_capacity", capacity=cap
            ))
            places = parking_for_object(workers, cap, norms)
            sch_details.append((cap, workers, places))
            sch_total += places

    return SocialParkingBreakdown(
        total_places=kg_total + sch_total,
        kindergarten_places=kg_total,
        school_places=sch_total,
        kindergarten_details=kg_details,
        school_details=sch_details,
    )
