"""Расчёт себестоимости проекта в условных единицах.

Все коэффициенты берутся из `economy.*` секции в `spb.yaml`.

Базовая формула:
    C_residential       = GFA_жил × C_base(этажность)
    C_vpp               = S_ВПП × C_commercial
    C_kindergarten      = S_здания_ДОО × C_kindergarten
    C_school            = S_здания_СОШ × C_school
    C_parking_open      = N_open × m²/место × C_surface
    C_parking_ml        = N_ml   × m²/место × C_multilevel
    C_parking_ug        = N_ug   × m²/место × C_underground × прогрессия(уровни)
    C_shell_total       = Σ всех C_*
    Overhead:
        networks    = C_shell × pct_networks
        landscaping = C_shell × pct_landscaping
        design      = C_shell × pct_design
        contingency = (shell + networks + landscape + design) × pct_contingency
    C_total = shell + overhead + fixed (земля/ТУ/снос)
"""

from __future__ import annotations

from urban_model.economy.result import CostBreakdown
from urban_model.models.funding import resolve_funding, resolve_funding_spec
from urban_model.normatives import Normatives


def _resolve_residential_base(floors: int, norms: Normatives) -> float:
    """Базовая стоимость м² жилой GFA по этажности (piecewise)."""
    return float(norms.resolve("economy.construction.residential_by_floors", floors=floors))


def _cost_underground_parking(
    places: int, levels: int, m2_per_space: float, base_per_m2: float, norms: Normatives,
) -> float:
    """Подземные парковки: сумма по уровням с прогрессией удорожания.

    Места распределяются равномерно по уровням; на каждом уровне берётся
    собственный множитель прогрессии (1.00 / 1.30 / 1.65 / 2.05+).
    """
    if places <= 0 or levels <= 0:
        return 0.0
    places_per_level = places / levels
    area_per_level = places_per_level * m2_per_space
    total = 0.0
    for lvl in range(1, levels + 1):
        mult = float(norms.resolve(
            "economy.construction.underground_progression", level=lvl
        ))
        total += area_per_level * base_per_m2 * mult
    return total


def calc_cost(tep, options, norms: Normatives) -> CostBreakdown:
    """Расчёт стоимости проекта по результатам ТЭП.

    Args:
        tep: TEPResult — но импорт оставлен «дакетипированным», чтобы
            избежать круговой зависимости (forward.py → calc_economy → cost).
        options: CalculationOptions — для floors, parking config, residential_class.
        norms: загруженные нормативы.

    Returns:
        CostBreakdown со всеми подытогами.
    """
    # --- Параметры из норм ---
    c_vpp = float(norms.resolve("economy.construction.vpp_commercial"))
    c_kg = float(norms.resolve("economy.construction.kindergarten"))
    c_sch = float(norms.resolve("economy.construction.school"))
    c_surface = float(norms.resolve("economy.construction.parking_surface"))
    c_ml = float(norms.resolve("economy.construction.parking_multilevel"))
    c_ug = float(norms.resolve("economy.construction.parking_underground"))

    # P0-1: площадь открытого м/м берём из основного норматива parking.*,
    # единый источник истины. (Дубль `economy.parking_areas.surface_m2_per_space`
    # удалён из YAML в v0.9.11.)
    m2_open = float(norms.resolve("parking.open_space_per_place"))
    m2_ml = float(norms.resolve("economy.parking_areas.multilevel_m2_per_space"))
    m2_ug = float(norms.resolve("economy.parking_areas.underground_m2_per_space"))

    pct_networks = float(norms.resolve("economy.overhead.pct_networks"))
    pct_landscape = float(norms.resolve("economy.overhead.pct_landscaping"))
    pct_design = float(norms.resolve("economy.overhead.pct_design"))
    pct_cont = float(norms.resolve("economy.overhead.pct_contingency"))

    # --- Жильё ---
    gfa_v = (tep.gfa.value or 0.0)
    bi_area = (tep.built_in_area.value or 0.0)
    # AUDIT v0.10.17 (двойной учёт встроенного ДОО): здание встроенно-
    # пристроенного ДОО физически входит в общую GFA (gfa_v), а в forward.py
    # вычитается из жилой GFA ТОЛЬКО при расчёте площади квартир. Здесь же
    # `residential_gfa` считался от полной GFA → здание ДОО облагалось дважды:
    # как жильё (×1.00) и как ДОО (×1.40). Симметрично логике forward.py
    # вычитаем площадь здания встроенного ДОО (не only_demand) из жилой GFA.
    _kg_included = getattr(options, "include_kindergarten", True)
    kg_btype = (
        getattr(options.kindergarten, "building_type", "detached")
        if _kg_included else "detached"
    )
    kg_only_demand = (
        bool(getattr(options.kindergarten, "only_demand", False))
        if _kg_included else True
    )
    kg_bld_in_gfa = (
        float(tep.kindergarten_building_area.value or 0.0)
        if (kg_btype == "built_in" and not kg_only_demand) else 0.0
    )
    # v0.12.15: здание встроенного доп. образования также входит в gfa_v и
    # вычитается из жилой GFA (симметрично built-in ДОО — иначе оболочка
    # облагается и как жильё ×C_base, и как доп. обр. ×c_add_edu).
    _ae_included = getattr(options, "include_add_education", True)
    _ae_only_demand0 = (
        bool(getattr(options.add_education, "only_demand", False))
        if _ae_included else True
    )
    ae_bld_in_gfa = (
        float(getattr(tep, "add_education_building_area", None).value or 0.0)
        if (_ae_included and not _ae_only_demand0
            and bool(getattr(tep, "add_education_built_in", False))
            and getattr(tep, "add_education_building_area", None) is not None)
        else 0.0
    )
    # v0.12.20 (аудит): этаж, замещённый стилобатом под домами (доля
    # `stylobate_under_buildings_share` × площадь деки), — это парковка, а не
    # жильё. Он считается стоимостью стилобата (cost_stylobate ниже), поэтому
    # вычитается из жилой GFA — иначе двойной счёт (этаж как жильё ×C_base И
    # как стилобат ×c_styl). Симметрично forward.py (residential_gfa там тоже
    # уменьшается на эту деку).
    styl_area_in_gfa = float(getattr(tep, "parking_stylobate_area", None).value or 0.0) \
        if getattr(tep, "parking_stylobate_area", None) is not None else 0.0
    try:
        _styl_under = float(norms.resolve("parking.stylobate_under_buildings_share"))
    except (KeyError, TypeError, ValueError):
        _styl_under = 0.25
    styl_deck_in_gfa = _styl_under * styl_area_in_gfa
    # v0.12.28: встроенная поликлиника (офис врача) — здание из жилой GFA.
    _poly_included = getattr(options, "include_polyclinic", True)
    _poly_only_demand0 = (
        bool(getattr(options.polyclinic, "only_demand", False))
        if _poly_included else True
    )
    poly_bld_in_gfa = (
        float(getattr(tep, "polyclinic_building_area", None).value or 0.0)
        if (_poly_included and not _poly_only_demand0
            and bool(getattr(tep, "polyclinic_built_in", False))
            and getattr(tep, "polyclinic_building_area", None) is not None)
        else 0.0
    )
    # GFA жилья = общая GFA − ВПП − ДОО − доп.обр − поликлиника − дека стилобата
    residential_gfa = max(
        0.0, gfa_v - bi_area - kg_bld_in_gfa - ae_bld_in_gfa
        - poly_bld_in_gfa - styl_deck_in_gfa
    )
    # v0.9.28: при кластерах этажности себестоимость считается покластерно
    # (C_base нелинейна по этажам): residential_gfa делится по долям GFA,
    # каждая часть умножается на ставку своей этажности.
    _clusters = getattr(options, "floor_clusters", None) or []
    if _clusters:
        from urban_model.calculations import clusters as _cl
        _gw = _cl.gfa_weights(_clusters)
        cost_residential = sum(
            residential_gfa * w * _resolve_residential_base(int(c.floors), norms)
            for w, c in zip(_gw, _clusters)
        )
    else:
        c_base_res = _resolve_residential_base(int(options.floors), norms)
        cost_residential = residential_gfa * c_base_res

    # v0.10.17 (аудит #2): повышающий коэффициент себестоимости по классу
    # жилья (бизнес дороже строить). Применяется ко всей жилой себестоимости,
    # включая покластерный случай. Если ключа нет (старый профиль) → 1.0.
    try:
        class_factor = float(norms.resolve(
            "economy.construction.residential_class_factor",
            residential_class=options.residential_class,
        ))
    except (KeyError, TypeError, ValueError):
        class_factor = 1.0
    cost_residential *= class_factor

    # --- ВПП ---
    cost_vpp = bi_area * c_vpp

    # --- ДОО / СОШ ---
    # v0.19.0: себестоимость соцобъекта определяет РЕЖИМ ФИНАНСИРОВАНИЯ
    # (resolve_funding), а НЕ `only_demand`.
    # Раньше only_demand обнулял cost — считалось, что объект вне квартала
    # строит кто-то другой. Это неверно: объект может строиться застройщиком
    # за пределами площадки. Теперь only_demand влияет только на баланс
    # территории; «не за счёт застройщика» задаётся явно (режим not_developer).
    # Компонент выключен целиком (include_*=False) → площадь здания 0 → cost 0.
    kg_bld = (tep.kindergarten_building_area.value or 0.0)
    sch_bld = (tep.school_building_area.value or 0.0)
    _kg_mode, _ = resolve_funding(options, "kindergarten", norms)
    _sch_mode, _ = resolve_funding(options, "school", norms)
    cost_kg = 0.0 if (_kg_mode == "not_developer"
                      or not getattr(options, "include_kindergarten", True)) else kg_bld * c_kg
    cost_sch = 0.0 if (_sch_mode == "not_developer"
                       or not getattr(options, "include_school", True)) else sch_bld * c_sch

    # --- Доп. образование (ВРИ 3.5.1, v0.12.15) ---
    # Здание строит застройщик (и для ВПП, и для отд. стоящего), кроме режима
    # «только потребность». Для встроенного оболочка вычтена из жилой GFA в
    # forward.py → двойного счёта нет (здесь добавляем спец-ставку доп. обр.).
    ae_bld = float(getattr(tep, "add_education_building_area", None).value or 0.0) \
        if getattr(tep, "add_education_building_area", None) is not None else 0.0
    try:
        c_add_edu = float(norms.resolve("economy.construction.add_education"))
    except KeyError:
        c_add_edu = c_sch
    _ae_mode, _ = resolve_funding(options, "add_education", norms)
    cost_add_edu = 0.0 if (_ae_mode == "not_developer"
                           or not getattr(options, "include_add_education", True)) \
        else ae_bld * c_add_edu

    # --- Поликлиника (ВРИ 3.4.1, v0.12.28) ---
    poly_bld = float(getattr(tep, "polyclinic_building_area", None).value or 0.0) \
        if getattr(tep, "polyclinic_building_area", None) is not None else 0.0
    try:
        c_poly = float(norms.resolve("economy.construction.polyclinic"))
    except KeyError:
        c_poly = c_sch
    _poly_mode, _ = resolve_funding(options, "polyclinic", norms)
    cost_poly = 0.0 if (_poly_mode == "not_developer"
                        or not getattr(options, "include_polyclinic", True)) \
        else poly_bld * c_poly

    # --- Парковки ---
    n_open = int(tep.parking_open_places.value or 0)
    n_ml = int(tep.parking_multilevel_places.value or 0)
    n_ug = int(tep.parking_underground_places.value or 0)

    cost_open = n_open * m2_open * c_surface
    cost_ml = n_ml * m2_ml * c_ml
    # Подземные — учёт прогрессии уровней. Если уровней не задано — 1 уровень.
    ug_levels = getattr(options.parking, "underground_levels", None) or 1
    cost_ug = _cost_underground_parking(n_ug, int(ug_levels), m2_ug, c_ug, norms)

    # --- Стилобат (v0.12.2): себестоимость = площадь деки × ставка. ---
    styl_area = float(getattr(tep, "parking_stylobate_area", None).value or 0.0) \
        if getattr(tep, "parking_stylobate_area", None) is not None else 0.0
    c_styl = float(norms.resolve("economy.construction.parking_stylobate"))
    cost_stylobate = styl_area * c_styl

    # --- Парковки соцобъектов (P0-6): открытые на ЗУ соцобъекта, та же ---
    # удельная стоимость, что у обычных открытых парковок (c_surface).
    soc_park_area = float(tep.social_parking_area.value or 0.0)
    _sp_mode, _ = resolve_funding(options, "social_parking", norms)
    cost_soc_park = 0.0 if _sp_mode == "not_developer" else soc_park_area * c_surface

    # --- Спортивные сооружения (P0-6): плоскостные, ВРИ 5.1.3. ---
    # Берём ту же удельную стоимость, что у парковки surface — это
    # покрытие/благоустройство без капитальных конструкций. Подходящего
    # отдельного норматива в economy/* пока нет; уточним при v0.9.
    sport_area = float(tep.sport_facilities_area.value or 0.0)
    _sport_mode, _ = resolve_funding(options, "sport", norms)
    cost_sport = 0.0 if _sport_mode == "not_developer" else sport_area * c_surface

    # --- Кастомные объекты (P0-6): площадь × коммерческая ставка ВПП. ---
    # Кастомные объекты бывают коммерческими (офис, торговля) и социальными
    # (поликлиника, ФОК). Ставка — по ВРИ; v0.19.0: режим финансирования
    # задаётся на самом объекте (`CustomObject.funding`, дефолт — застройщик).
    cost_custom = 0.0
    for obj in (getattr(options, "custom_objects", None) or []):
        _mode, _ = resolve_funding_spec(
            getattr(obj, "funding", None), options, norms)
        if _mode == "not_developer":
            continue  # строит город / другой инвестор / уже существует
        floor_area = float(obj.floor_area_m2 or obj.plot_area_m2 or 0.0)
        # Коммерческие → как ВПП commercial; некоммерческие → как ДОО.
        # Эвристика по ВРИ: 3.x = соц (медицина/спорт), 4.x = коммерция.
        vri = (obj.vri_code or "").strip()
        if vri.startswith("4."):
            cost_custom += floor_area * c_vpp
        elif vri.startswith("3."):
            cost_custom += floor_area * c_kg  # социальные — по ставке ДОО
        else:
            cost_custom += floor_area * c_vpp  # по умолчанию — коммерция

    # --- Инженерная инфраструктура (v0.12) ---
    # Себестоимость по объектам (ориентиры в у.е., уточнить по НЦС).
    # v0.19.0: `in_balance=False` («только потребность») означает лишь то, что
    # ЗУ объекта вне баланса квартала, — себестоимость при этом остаётся на
    # застройщике (объект может строиться за пределами площадки). Кто платит,
    # решает режим финансирования строки «Инженерная инфраструктура».
    cost_engineering = 0.0
    eng = getattr(tep, "engineering", None)
    _eng_mode, _ = resolve_funding(options, "engineering", norms)
    if eng is not None and _eng_mode != "not_developer" \
            and getattr(options, "include_engineering", True):
        _ec = "economy.engineering_cost"
        rate_tp = float(norms.resolve(f"{_ec}.tp_per_object"))
        rate_rtp = float(norms.resolve(f"{_ec}.rtp_per_object"))
        rate_boiler = float(norms.resolve(f"{_ec}.boiler_per_mw"))
        rate_grp = float(norms.resolve(f"{_ec}.grp_per_object"))
        rate_osps = float(norms.resolve(f"{_ec}.osps_per_m3day"))
        rate_pump = float(norms.resolve(f"{_ec}.pump_per_object"))
        for o in eng.objects:
            if o.key == "tp":
                cost_engineering += o.count * rate_tp
            elif o.key == "rtp":
                cost_engineering += o.count * rate_rtp
            elif o.key == "boiler":
                cost_engineering += o.count * (o.capacity or 0.0) * rate_boiler
            elif o.key == "grp":
                cost_engineering += o.count * rate_grp
            elif o.key == "osps":
                cost_engineering += o.count * (o.capacity or 0.0) * rate_osps
            elif o.key == "pump":
                cost_engineering += o.count * rate_pump

    # --- Подытоги ---
    shell_total = (
        cost_residential + cost_vpp + cost_kg + cost_sch + cost_add_edu + cost_poly
        + cost_open + cost_ml + cost_ug + cost_stylobate
        + cost_soc_park + cost_sport + cost_custom + cost_engineering
    )
    networks = shell_total * pct_networks
    landscaping = shell_total * pct_landscape
    design = shell_total * pct_design
    contingency = (shell_total + networks + landscaping + design) * pct_cont

    # --- Фиксированные затраты ---
    # P0-5: поля land_cost/connection_costs/demolition_costs в CalculationOptions
    # не объявлены, getattr всегда возвращал 0. Блок зарезервирован под v0.8.1
    # (земля/ТУ/снос с UI). Пока — 0.
    fixed = 0.0

    total = shell_total + networks + landscaping + design + contingency + fixed

    return CostBreakdown(
        residential=cost_residential,
        vpp=cost_vpp,
        kindergarten=cost_kg,
        school=cost_sch,
        add_education=cost_add_edu,
        polyclinic=cost_poly,
        parking_open=cost_open,
        parking_multilevel=cost_ml,
        parking_underground=cost_ug,
        parking_stylobate=cost_stylobate,
        social_parking=cost_soc_park,
        sport=cost_sport,
        custom_objects=cost_custom,
        engineering=cost_engineering,
        shell_total=shell_total,
        networks=networks,
        landscaping=landscaping,
        design=design,
        contingency=contingency,
        fixed=fixed,
        total=total,
    )
