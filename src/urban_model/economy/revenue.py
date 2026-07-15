"""Расчёт выручки от продажи объектов в условных единицах.

Формулы:
    R_residential = площадь_квартир × цена_по_классу
    R_parking_*   = м/м × цена_за_место
    R_vpp         = площадь_ВПП × цена_коммерции
    ДОО / СОШ     = 0 (соцнагрузка, не продаётся)
"""

from __future__ import annotations

from urban_model.economy.result import RevenueBreakdown
from urban_model.models.funding import resolve_funding, resolve_funding_spec
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
    p_styl = float(norms.resolve("economy.sale_prices.parking_stylobate"))

    # v0.9.14: доля реализации м/м по классу жилья. Не все построенные
    # места продаются — особенно в эконом/комфорт классе. Непроданные
    # места приносят 0 выручки, но их себестоимость уже в cost.total.
    try:
        park_sale_rate = float(norms.resolve(
            "economy.sale_rates.parking_by_class",
            residential_class=options.residential_class,
        ))
    except (KeyError, TypeError, ValueError):
        park_sale_rate = 1.0

    apt = (tep.apartments_area.value or 0.0)
    bi_area = (tep.built_in_area.value or 0.0)
    n_open = int(tep.parking_open_places.value or 0)
    n_ml = int(tep.parking_multilevel_places.value or 0)
    n_ug = int(tep.parking_underground_places.value or 0)
    n_styl = int(getattr(tep, "parking_stylobate_places", None).value or 0) \
        if getattr(tep, "parking_stylobate_places", None) is not None else 0

    r_res = apt * p_res
    r_open = n_open * p_open * park_sale_rate
    r_ml = n_ml * p_ml * park_sale_rate
    r_ug = n_ug * p_ug * park_sale_rate
    r_styl = n_styl * p_styl * park_sale_rate
    r_vpp = bi_area * p_vpp

    # Компенсация соцобъектов городом — застройщик передаёт объекты по
    # бюджетной цене либо возвращает затраты через КОТ-соглашения.
    #
    # v0.19.0: доля компенсации считается ПО КАЖДОМУ объекту (свой режим
    # финансирования), а не одной глобальной ставкой. `only_demand` больше не
    # влияет: объект вне квартала может строиться застройщиком и точно так же
    # компенсироваться. Компенсируется только то, что застройщик оплатил →
    # база = та же, что в cost.py (режим not_developer → 0).
    c_kg = float(norms.resolve("economy.construction.kindergarten"))
    c_sch = float(norms.resolve("economy.construction.school"))
    try:
        c_add_edu = float(norms.resolve("economy.construction.add_education"))
    except (KeyError, TypeError, ValueError):
        c_add_edu = c_sch
    try:
        c_poly = float(norms.resolve("economy.construction.polyclinic"))
    except (KeyError, TypeError, ValueError):
        c_poly = c_sch

    def _fld(name: str) -> float:
        f = getattr(tep, name, None)
        return float(f.value or 0.0) if f is not None else 0.0

    _soc = [
        ("kindergarten", _fld("kindergarten_building_area"), c_kg, "include_kindergarten"),
        ("school", _fld("school_building_area"), c_sch, "include_school"),
        ("add_education", _fld("add_education_building_area"), c_add_edu,
         "include_add_education"),
        ("polyclinic", _fld("polyclinic_building_area"), c_poly, "include_polyclinic"),
    ]
    r_social_comp = 0.0
    for _key, _bld, _rate, _inc in _soc:
        if not getattr(options, _inc, True):
            continue
        _mode, _share = resolve_funding(options, _key, norms)
        if _mode == "compensated":
            r_social_comp += _bld * _rate * _share

    # v0.9.8 (AUDIT P0-2): кастомные объекты дают выручку по любому
    # ВРИ КРОМЕ 3.x (социальные — поликлиника/ФОК — соцнагрузка, 0).
    # Раньше прибыль шла ТОЛЬКО для 4.x, а в cost.py списывались любые
    # non-(3.x) как commercial — это создавало системный убыток для
    # объектов с ВРИ 5.x (спорт), 2.x и т.п. Симметричная логика
    # с cost.py: 3.x → 0, остальное → коммерческая ставка.
    # v0.19.0: объект в режиме «не за счёт застройщика» не даёт ни затрат,
    # ни выручки — его строит город/другой инвестор либо он уже существует.
    r_custom = 0.0
    for obj in (getattr(options, "custom_objects", None) or []):
        _mode, _ = resolve_funding_spec(getattr(obj, "funding", None), options, norms)
        if _mode == "not_developer":
            continue
        vri = (obj.vri_code or "").strip()
        floor_area = float(obj.floor_area_m2 or obj.plot_area_m2 or 0.0)
        if not vri.startswith("3."):
            r_custom += floor_area * p_vpp
        # 3.x → 0 (соцнагрузка)

    total = r_res + r_open + r_ml + r_ug + r_styl + r_vpp + r_custom + r_social_comp

    return RevenueBreakdown(
        residential=r_res,
        parking_open=r_open,
        parking_multilevel=r_ml,
        parking_underground=r_ug,
        parking_stylobate=r_styl,
        vpp_commercial=r_vpp,
        custom_commercial=r_custom,
        social_compensation=r_social_comp,
        total=total,
    )
