"""Парковки v0.1: только жильё, открытые в уровне земли (минимум 12.5%).

Подземные парковки (по ТЗ §7.10) на этом этапе не учитываются в площади ЗУ.
Многоуровневые / комбинированные сценарии — v0.2/v0.3.
"""

from __future__ import annotations

import math

from urban_model.normatives import Normatives


def required_places_for_apartments(apartments_area: float, norms: Normatives) -> float:
    per_place = norms.resolve("parking.housing.m2_apartments_per_place")
    return apartments_area / per_place


def open_places_min(required_places: float, norms: Normatives) -> int:
    share = norms.resolve("parking.open_share_min")
    return int(math.ceil(required_places * share))


def open_parking_area(open_places: int, norms: Normatives) -> float:
    per_place = norms.resolve("parking.open_space_per_place")
    return open_places * per_place
