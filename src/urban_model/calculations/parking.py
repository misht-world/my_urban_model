"""Расчёт парковок: открытые, многоуровневые, подземные.

Нормативная база:
  parking.housing.m2_apartments_per_place  — норма обеспеченности, м²/м.м.
  parking.open_share_min                   — минимальная доля открытых (0.125)
  parking.open_space_per_place             — площадь открытого м/м (20.75 м²)
  parking.multilevel_area_per_place        — пятно на м.м. по этажности (piecewise)
  parking.multilevel_capacity_max          — макс. вместимость одного паркинга (300 м.м.)
"""

from __future__ import annotations

import math

from urban_model.normatives import Normatives


# ---------------------------------------------------------------------------
# Базовые функции (использовались в v0.1/v0.2, сохраняем для обратной совместимости)
# ---------------------------------------------------------------------------

def required_places_for_apartments(apartments_area: float, norms: Normatives) -> float:
    """Норматив: 1 м.м. на N м² квартир."""
    per_place = norms.resolve("parking.housing.m2_apartments_per_place")
    return apartments_area / per_place


def open_places_min(required_places: float, norms: Normatives) -> int:
    """Минимальное количество открытых м.м. (≥ open_share_min × total)."""
    share = norms.resolve("parking.open_share_min")
    return int(math.ceil(required_places * share))


def open_parking_area(open_places: int, norms: Normatives) -> float:
    """Площадь открытой парковки, м²."""
    per_place = norms.resolve("parking.open_space_per_place")
    return open_places * per_place


# ---------------------------------------------------------------------------
# Полный расчёт с разбивкой по типам
# ---------------------------------------------------------------------------

def compute_parking_breakdown(
    apartments_area: float,
    config,          # ParkingConfig — импорт снизу, чтобы избежать циклов
    norms: Normatives,
):
    """Рассчитать разбивку парковок по типам.

    Args:
        apartments_area: площадь квартир, м².
        config:          ParkingConfig — режим и доли.
        norms:           нормативная база.

    Returns:
        ParkingBreakdown с открытыми, многоуровневыми и подземными м/м и площадями.
    """
    from urban_model.models.parking import ParkingBreakdown

    per_place = norms.resolve("parking.housing.m2_apartments_per_place")
    open_share_min_v = norms.resolve("parking.open_share_min")
    open_space_per_v = norms.resolve("parking.open_space_per_place")
    cap_max = norms.resolve("parking.multilevel_capacity_max")

    total_required = math.ceil(apartments_area / per_place)

    if config.mode == "min_open":
        # Минимум открытых, остаток — подземные (текущее v0.1 поведение)
        open_pl = max(math.ceil(total_required * open_share_min_v), 1 if total_required > 0 else 0)
        multilevel_pl = 0
        underground_pl = max(0, total_required - open_pl)
        ml_levels = 0

    elif config.mode == "all_open":
        # Всё открытое
        open_pl = total_required
        multilevel_pl = 0
        underground_pl = 0
        ml_levels = 0

    else:  # custom
        # open_share уже проверен валидатором ≥ open_share_min
        # Но гарантируем жёсткий минимум
        open_raw = math.ceil(total_required * config.open_share)
        open_min = math.ceil(total_required * open_share_min_v)
        open_pl = max(open_raw, open_min)

        multilevel_pl = math.floor(total_required * config.multilevel_share)
        underground_pl = max(0, total_required - open_pl - multilevel_pl)
        ml_levels = config.multilevel_levels

    # === Площадь открытых ===
    open_area = open_pl * open_space_per_v

    # === Многоуровневый паркинг ===
    if multilevel_pl > 0:
        ml_objects = max(1, math.ceil(multilevel_pl / cap_max))
        ml_area_per_place = norms.resolve(
            "parking.multilevel_area_per_place", levels=ml_levels
        )
        # Пятно = число м/м × площадь пятна на м/м при данной этажности
        # (норматив уже учитывает, что площадь уменьшается с числом уровней)
        ml_footprint = multilevel_pl * ml_area_per_place
    else:
        ml_objects = 0
        ml_footprint = 0.0
        ml_levels = ml_levels if config.mode == "custom" else 0

    return ParkingBreakdown(
        total_required=total_required,
        open_places=open_pl,
        multilevel_places=multilevel_pl,
        underground_places=underground_pl,
        multilevel_objects=ml_objects,
        multilevel_levels=ml_levels,
        open_area=open_area,
        multilevel_footprint=ml_footprint,
    )
