"""Расчёт выручки от продажи объектов в условных единицах.

Формулы:
    R_residential = площадь_квартир × цена_по_классу
    R_parking_*   = м/м × цена_за_место
    R_vpp         = площадь_ВПП × цена_коммерции
    ДОО / СОШ     = 0 (соцнагрузка, не продаётся)
"""

from __future__ import annotations

from urban_model.economy.result import RevenueBreakdown
from urban_model.normatives import Normatives


def calc_revenue(tep, options, norms: Normatives) -> RevenueBreakdown:
    """Расчёт выручки по результатам ТЭП."""
    # Цена м² квартир — по классу жилья
    p_res = float(norms.resolve(
        "economy.sale_prices.residential_by_class",
        residential_class=options.residential_class,
    ))
    p_ug = float(norms.resolve("economy.sale_prices.parking_underground"))
    p_ml = float(norms.resolve("economy.sale_prices.parking_multilevel"))
    p_open = float(norms.resolve("economy.sale_prices.parking_surface"))
    p_vpp = float(norms.resolve("economy.sale_prices.vpp_commercial"))

    apt = (tep.apartments_area.value or 0.0)
    bi_area = (tep.built_in_area.value or 0.0)
    n_open = int(tep.parking_open_places.value or 0)
    n_ml = int(tep.parking_multilevel_places.value or 0)
    n_ug = int(tep.parking_underground_places.value or 0)

    r_res = apt * p_res
    r_open = n_open * p_open
    r_ml = n_ml * p_ml
    r_ug = n_ug * p_ug
    r_vpp = bi_area * p_vpp

    # AUDIT P0-6: кастомные коммерческие объекты (ВРИ 4.x) дают выручку
    # по той же ставке, что ВПП commercial. Соцобъекты (3.x) — соцнагрузка.
    r_custom = 0.0
    for obj in (getattr(options, "custom_objects", None) or []):
        vri = (obj.vri_code or "").strip()
        floor_area = float(obj.floor_area_m2 or obj.plot_area_m2 or 0.0)
        if vri.startswith("4."):
            r_custom += floor_area * p_vpp
        # 3.x → 0 (соцнагрузка); прочие — 0 (страхуемся)

    total = r_res + r_open + r_ml + r_ug + r_vpp + r_custom

    return RevenueBreakdown(
        residential=r_res,
        parking_open=r_open,
        parking_multilevel=r_ml,
        parking_underground=r_ug,
        vpp_commercial=r_vpp,
        custom_commercial=r_custom,
        total=total,
    )
