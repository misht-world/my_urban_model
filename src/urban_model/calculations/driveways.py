"""Проезды (приняты условно): внутриквартальные и на ЗУ."""

from __future__ import annotations


def intra_quarter_area(site_area: float, share: float) -> float:
    return site_area * share


def housing_lot_driveways_area(footprint: float, share: float) -> float:
    return footprint * share
