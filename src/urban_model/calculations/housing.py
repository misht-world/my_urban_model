"""Жильё: КИТ → общая площадь → площадь квартир, площадь застройки."""

from __future__ import annotations


def gfa_from_kit(kit: float, site_area_m2: float) -> float:
    """Общая площадь жилых зданий по КИТ. Определение КИТ:
    КИТ = общая площадь жилых зданий / площадь квартала."""
    return kit * site_area_m2


def apartments_area(gfa: float, vpp_share: float, ratio: float) -> float:
    """Площадь квартир.

    Доля ВПП от общей площади выкусывается из жилой части,
    к жилой применяется коэффициент перевода (по умолчанию 0.75).
    """
    if not 0 <= vpp_share < 1:
        raise ValueError(f"vpp_share должен быть в [0,1): {vpp_share}")
    residential_gfa = gfa * (1 - vpp_share)
    return residential_gfa * ratio


def vpp_floor_area(gfa: float, vpp_share: float) -> float:
    return gfa * vpp_share


def housing_footprint(gfa: float, floors: int) -> float:
    """Грубая оценка площади застройки = GFA / этажность.
    Не учитывает разноэтажность; для v0.1 этого достаточно (ТЗ — единая высотность)."""
    if floors <= 0:
        raise ValueError(f"floors > 0, дано {floors}")
    return gfa / floors
