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

    # v0.9.14: компенсация ДОО/СОШ городом — в реальности застройщик
    # передаёт соцобъекты по бюджетной цене либо получает компенсацию
    # затрат через КОТ-соглашения. Без этого расчёт всегда даёт убыток
    # (соцнагрузка), что не соответствует практике рынка.
    # При `only_demand=True` объект НЕ строится застройщиком — компенсация
    # не применяется (как и cost этого объекта).
    try:
        comp_share = float(norms.resolve("economy.social_compensation.share"))
    except (KeyError, TypeError, ValueError):
        comp_share = 0.0
    c_kg = float(norms.resolve("economy.construction.kindergarten"))
    c_sch = float(norms.resolve("economy.construction.school"))
    kg_bld = float(tep.kindergarten_building_area.value or 0.0)
    sch_bld = float(tep.school_building_area.value or 0.0)
    kg_only_demand = bool(getattr(options.kindergarten, "only_demand", False)) \
        if getattr(options, "include_kindergarten", True) else True
    sch_only_demand = bool(getattr(options.school, "only_demand", False)) \
        if getattr(options, "include_school", True) else True
    kg_billable = 0.0 if kg_only_demand else kg_bld * c_kg
    sch_billable = 0.0 if sch_only_demand else sch_bld * c_sch
    r_social_comp = (kg_billable + sch_billable) * comp_share

    # v0.9.8 (AUDIT P0-2): кастомные объекты дают выручку по любому
    # ВРИ КРОМЕ 3.x (социальные — поликлиника/ФОК — соцнагрузка, 0).
    # Раньше прибыль шла ТОЛЬКО для 4.x, а в cost.py списывались любые
    # non-(3.x) как commercial — это создавало системный убыток для
    # объектов с ВРИ 5.x (спорт), 2.x и т.п. Симметричная логика
    # с cost.py: 3.x → 0, остальное → коммерческая ставка.
    r_custom = 0.0
    for obj in (getattr(options, "custom_objects", None) or []):
        vri = (obj.vri_code or "").strip()
        floor_area = float(obj.floor_area_m2 or obj.plot_area_m2 or 0.0)
        if not vri.startswith("3."):
            r_custom += floor_area * p_vpp
        # 3.x → 0 (соцнагрузка)

    total = r_res + r_open + r_ml + r_ug + r_vpp + r_custom + r_social_comp

    return RevenueBreakdown(
        residential=r_res,
        parking_open=r_open,
        parking_multilevel=r_ml,
        parking_underground=r_ug,
        vpp_commercial=r_vpp,
        custom_commercial=r_custom,
        social_compensation=r_social_comp,
        total=total,
    )
