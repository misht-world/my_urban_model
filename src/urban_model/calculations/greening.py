"""Озеленение жилого ЗУ и проверка по кварталу."""

from __future__ import annotations


def housing_greening_area(apartments_area: float, ratio: float) -> float:
    """Озеленение жилого участка: 23 м² на 100 м² площади квартир (доля 0.23)."""
    return apartments_area * ratio


def quarter_greening_required(site_area: float, social_plots_area: float, share: float) -> float:
    """25% от площади квартала без участков ДОО/СОШ."""
    base = max(0.0, site_area - social_plots_area)
    return base * share
