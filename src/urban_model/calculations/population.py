"""Население — через жилищную обеспеченность."""

from __future__ import annotations


def population(apartments_area_m2: float, m2_per_person: float) -> float:
    if m2_per_person <= 0:
        raise ValueError(f"m2_per_person должен быть > 0, дано {m2_per_person}")
    return apartments_area_m2 / m2_per_person


def density_chel_per_ga(population_count: float, site_area_m2: float) -> float:
    """Плотность населения, чел./га."""
    if site_area_m2 <= 0:
        raise ValueError(f"site_area_m2 > 0, дано {site_area_m2}")
    return population_count / (site_area_m2 / 10_000)
