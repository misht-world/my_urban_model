"""Прямой расчёт ТЭП по заданной плотности квартала (block_density = GFA/S_кв).

Чистая функция: на вход — Site, Options, Normatives и block_density.
На выход — TEPResult со структурированными полями (TEPField).

Важно про КИТ:
    КИТ по ПЗЗ СПб = площадь квартир / площадь ЗУ жилой застройки.
    Это и есть `result.kit`. Внутренняя переменная функции (`block_density`)
    — это GFA / S_квартала; ей бисектится inverse-задача, и она НЕ совпадает
    с КИТ ПЗЗ. Все нормативные пороги (kit_max=2.5/1.4, ЗНОП piecewise)
    применяются к КИТ ПЗЗ, не к block_density.

Эта функция — фундамент. `solve_max_kit` (inverse.py) вызывает её много раз.
"""

from __future__ import annotations

import math

from urban_model.calculations import (
    add_education,
    balance,
    clusters,
    driveways,
    engineering,
    greening,
    housing,
    kindergarten,
    parking,
    polyclinic,
    population,
    school,
    social_parking,
    sport,
    vpp,
    znop,
)
from urban_model.calculations.allowed_capacities import (
    build_warnings as _build_allowed_warnings,
)
from urban_model.calculations.warning_codes import WC, prefix as _wcprefix
from urban_model.calculations.parking import compute_parking_breakdown
from urban_model.models.built_in import BuiltInArea
from urban_model.models.options import CalculationOptions
from urban_model.models.result import Status, TEPField, TEPResult
from urban_model.models.site import Site
from urban_model.normatives import Normatives


def _F(value, **kw) -> TEPField:
    return TEPField(value=value, **kw)


def compute_tep_for_kit(
    block_density: float,
    site: Site,
    options: CalculationOptions,
    norms: Normatives,
) -> TEPResult:
    """Расчёт всех ТЭП при заданной `block_density` = GFA / S_квартала.

    Обратите внимание: входной аргумент — НЕ КИТ ПЗЗ. Это техническая
    переменная плотности квартала (для бисекции). КИТ ПЗЗ = площадь
    квартир / ЗУ жилой застройки — вычисляется внутри по результатам.
    """
    # Сохраняем под старым именем для обратной совместимости read-only:
    # многие подстроки formula/source ссылаются на "КИТ".
    kit = block_density

    # === Кластеры этажности (v0.9.28) ===
    # floors_eff — средневзвешенная этажность; для баланса/озеленения квартал
    # схлопывается в одно число (вычитаемые территории распределены
    # пропорционально площади кластеров). При отсутствии кластеров = options.floors.
    _clusters = options.floor_clusters
    floors_eff = clusters.effective_floors(_clusters, options.floors)

    # === Нормативный максимум КИТ ПЗЗ ===
    kit_norm_max = norms.resolve(
        "kit_limits", planning_doc="yes" if options.planning_doc else "no"
    )

    # === Жильё + ВПП ===
    gfa_v = housing.gfa_from_kit(kit, site.area_m2)
    apt_ratio = norms.resolve("building_params.apartments_to_gfa_ratio")

    # ВПП — собираем общий список из legacy single (`built_in`) и нового
    # `built_in_list`. Все они вычитаются из GFA жилого дома (v0.7.1).
    all_built_ins: list[BuiltInArea] = list(options.built_in_list)
    if options.built_in is not None:
        all_built_ins.insert(0, options.built_in)

    if all_built_ins:
        # Сумма площадей всех ВПП (safety: не больше GFA)
        total_bi_area = sum(b.area_m2 for b in all_built_ins)
        bi_area = min(total_bi_area, gfa_v)
        residential_gfa = gfa_v - bi_area
        apartments_area_v = residential_gfa * apt_ratio
        # bi_vri: для legacy-совместимости поля built_in_vri_code в результате
        # берём первый ВРИ (если несколько — это поле информативное; полный
        # список — в audit-полях)
        bi_vri = all_built_ins[0].vri_code
    else:
        bi_area = 0.0
        residential_gfa = gfa_v * (1.0 - options.vpp_share)
        apartments_area_v = residential_gfa * apt_ratio
        bi_vri = None

    footprint_v = housing.housing_footprint(gfa_v, floors_eff)

    # === Население ===
    hp = norms.resolve("housing_provision")
    pop_v = population.population(apartments_area_v, hp)
    hp_check = norms.resolve("housing_provision_check")
    pop_check_v = population.population(apartments_area_v, hp_check)
    density_v = population.density_chel_per_ga(pop_v, site.area_m2)
    density_max = norms.resolve("population_density_max")

    # Проверка плотности (формальная, по 20 м²/чел).
    # v0.9.8 (AUDIT P1-5): при enforce_density_norm=False статус = OK
    # (норматив отключён пользователем — на Оптимизации сценарий feasible,
    # на Расчёте не нужно показывать WARNING как «опасно»). Информационный
    # warning остаётся в TEPResult.warnings с пометкой «отключён».
    density_check_v = population.density_chel_per_ga(pop_check_v, site.area_m2)
    _density_over = density_check_v > density_max
    if _density_over and options.enforce_density_norm:
        density_status = Status.ERROR
    else:
        density_status = Status.OK

    warnings: list[str] = []
    # v0.10.11 (фикс): предупреждение о плотности добавляется ПОЗЖЕ — после
    # возможной корректировки жилой GFA на здание встроенного ДОО (которая
    # снижает население/плотность). Иначе в warnings попадало устаревшее
    # значение (напр. 474), хотя итоговая плотность уже ≤ норматива (449.9).

    # === Стилобатная парковка (v0.12.2 / v0.12.9; перенесена ВЫШЕ в v0.12.10) ===
    # Считается ДО ДОО/СОШ, чтобы соцобъекты брали уже скорректированное
    # население (на крупном квартале с большим стилобатом потеря квартир
    # достигает нескольких % → иначе ДОО/СОШ были бы переоценены).
    # Стилобат — полноценный 4-й тип: доля от ОБЩЕГО числа м/м (жильё + ВПП +
    # доп-объекты). Физически обслуживает жильё: 25% деки стоит под жилыми
    # домами → там 1-й этаж = парковка, не квартиры (теряем 1 этаж жилья).
    # Размер стилобата зависит от парковки, парковка — от площади квартир,
    # площадь квартир — от стилобата: решаем локальной фикс-точкой.
    stylobate_places_v = 0
    stylobate_area_v = 0.0
    _styl_share = float(getattr(options.parking, "stylobate_share", 0.0) or 0.0)
    if _styl_share > 0 and apartments_area_v > 0 and options.include_parking:
        _styl_per_place = norms.resolve("parking.housing.m2_apartments_per_place")
        _styl_apl = norms.resolve("parking.stylobate_area_per_place")
        _styl_under = norms.resolve("parking.stylobate_under_buildings_share")
        # Нежилые парковки (ВПП + доп-объекты) для сайзинга стилобата от ОБЩЕГО
        # пула. Лёгкий предрасчёт (полные блоки — ниже), не зависит от площади
        # квартир, поэтому считаем один раз до фикс-точки.
        _vpp_pp = norms.resolve("parking.vpp.m2_per_place")
        _add_parking = 0
        for _b in all_built_ins:
            _add_parking += math.ceil(_b.area_m2 / _vpp_pp)
        for _obj in options.custom_objects:
            _fa = _obj.floor_area_m2 or _obj.plot_area_m2
            _add_parking += math.ceil(_fa / _vpp_pp)
        _rgfa_base = residential_gfa  # GFA жилья − ВПП (встроенный ДОО вычтется ниже)
        for _ in range(6):
            _hreq = math.ceil(apartments_area_v / _styl_per_place) if apartments_area_v > 0 else 0
            _total_demand = _hreq + _add_parking
            stylobate_places_v = round(_total_demand * _styl_share)
            stylobate_area_v = stylobate_places_v * _styl_apl
            apartments_area_v = max(0.0, (_rgfa_base - _styl_under * stylobate_area_v) * apt_ratio)
        residential_gfa = max(0.0, _rgfa_base - _styl_under * stylobate_area_v)
        pop_v = population.population(apartments_area_v, hp)
        pop_check_v = population.population(apartments_area_v, hp_check)
        density_v = population.density_chel_per_ga(pop_v, site.area_m2)
        density_check_v = population.density_chel_per_ga(pop_check_v, site.area_m2)
        _density_over = density_check_v > density_max
        density_status = (
            Status.ERROR if (_density_over and options.enforce_density_norm)
            else Status.OK
        )

    # === ДОО ===
    # Резолвим нормативы заранее, чтобы они были доступны для formula-строк
    # даже когда include_kindergarten=False (важно для аудит-трейла).
    kg_per_1000 = norms.resolve("social_objects.kindergarten.places_per_1000")
    kg_round = norms.resolve("social_objects.kindergarten.rounding")
    kg_btype = options.kindergarten.building_type  # нужно до conditional для formula/adj
    kg_status = Status.OK
    if options.include_kindergarten and pop_v > 0:
        kg_required_raw = kindergarten.required_places(pop_v, kg_per_1000)
        kg_accepted = kindergarten.round_places(kg_required_raw, kg_round)
        # Резолвим оба варианта capacity_min и общий capacity_max из YAML —
        # значения нужны для разных веток предупреждений (см. ниже).
        kg_cap_min_detached = norms.resolve(
            "social_objects.kindergarten.capacity_min", building_type="detached"
        )
        kg_cap_min_builtin = norms.resolve(
            "social_objects.kindergarten.capacity_min", building_type="built_in"
        )
        kg_cap_max = norms.resolve(
            "social_objects.kindergarten.capacity_max", building_type=kg_btype
        )
        kg_cap_min_active = (
            kg_cap_min_detached if kg_btype == "detached" else kg_cap_min_builtin
        )
        kg_src_min = norms.source_of(
            "social_objects.kindergarten.capacity_min", building_type=kg_btype
        )
        kg_src_max = norms.source_of(
            "social_objects.kindergarten.capacity_max", building_type=kg_btype
        )
        # v0.12.4: режим «только нормативная наполняемость» — вместимости
        # из типового списка СП/КОБр.
        kg_allowed_strict = (
            norms.resolve("social_objects.kindergarten.allowed_capacities")
            if options.kindergarten.strict_capacity else None
        )
        # v0.12.22: ЗУ-функция для выбора типовых вместимостей по минимуму ЗУ
        # (а не профицита) — учитывает ступени plot_area_per_place.
        _kg_cost_fn = (
            lambda caps: sum(
                kindergarten.plot_area_for_capacity(c, norms, kg_btype) for c in caps
            )
        )
        kg_buckets = kindergarten.split_into_objects(
            total_places=kg_accepted,
            spec_capacity=options.kindergarten.capacity_per_object,
            spec_count=options.kindergarten.num_objects,
            capacity_min=kg_cap_min_active,
            capacity_max=kg_cap_max,
            multiple=int(kg_round),
            allowed_capacities=kg_allowed_strict,
            cost_fn=_kg_cost_fn,
        )
        kg_plot_total, kg_bld_total = kindergarten.total_areas(kg_buckets, norms, kg_btype)
        # v0.12.4: «принято мест» = фактическая вместимость корпусов (Σ buckets).
        # В strict-режиме корпуса снапаются вверх к типовым размерам → принято
        # может превышать спрос (профицит виден). В обычном режиме Σ == kg_accepted.
        if kg_buckets:
            kg_accepted = sum(kg_buckets)
        # Предупреждения по вместимости ДОО (дифференцированы по типу и значению).
        # Пороги берутся из YAML (capacity_min/max — единый источник истины):
        #   c < kg_cap_min_builtin                          → меньше норм. наполняемости любого ДОО
        #   detached: kg_cap_min_builtin ≤ c < kg_cap_min_detached → рекомендовать built_in
        #   c > kg_cap_max                                  → превышен максимум
        for c in kg_buckets:
            if c < kg_cap_min_builtin:
                kg_status = Status.WARNING
                warnings.append(_wcprefix(WC.SOC_CAP_MIN_BELOW,
                    f"ДОО: расчётная вместимость {c} мест меньше минимальной "
                    f"нормативной наполняемости ({kg_cap_min_builtin} мест"
                    + (f", {kg_src_min}" if kg_src_min else "") + "). "
                    "Запроектировать ДОО на такое число мест невозможно."
                ))
            elif kg_btype == "detached" and c < kg_cap_min_detached:
                kg_status = Status.WARNING
                warnings.append(_wcprefix(WC.SOC_CAP_MIN_DETACHED_HINT,
                    f"ДОО: вместимость {c} мест меньше минимума отдельно стоящего "
                    f"ДОО ({kg_cap_min_detached} мест"
                    + (f", {kg_src_min}" if kg_src_min else "") + "). "
                    "Рекомендуется выбрать тип «встроенно-пристроенный ДОО» "
                    f"(минимум {kg_cap_min_builtin} мест)."
                ))
            elif c > kg_cap_max:
                kg_status = Status.WARNING
                warnings.append(_wcprefix(WC.SOC_CAP_MAX_ABOVE,
                    f"ДОО: вместимость {c} мест превышает принятый максимум "
                    f"{kg_cap_max} мест"
                    + (f" ({kg_src_max})" if kg_src_max else "")
                    + " — рекомендуется разбить на большее число объектов."
                ))
        # Проверка списка допустимых вместимостей (типовые по данным КС).
        try:
            kg_allowed = norms.resolve("social_objects.kindergarten.allowed_capacities")
            kg_allowed_src = norms.source_of("social_objects.kindergarten.allowed_capacities")
        except KeyError:
            kg_allowed = None
            kg_allowed_src = None
        if kg_allowed and kg_buckets:
            def _kg_skip(c: int) -> bool:
                # Уже пойманные «вне нормы» случаи не дублируем
                return (
                    c < kg_cap_min_builtin
                    or (kg_btype == "detached" and c < kg_cap_min_detached)
                    or c > kg_cap_max
                )
            kg_extra = _build_allowed_warnings(
                kg_buckets, kg_allowed, "ДОО",
                source=kg_allowed_src, skip=_kg_skip,
            )
            if kg_extra:
                kg_status = Status.WARNING
                warnings.extend(kg_extra)
    else:
        kg_required_raw = kg_accepted = 0
        kg_plot_total = kg_bld_total = 0.0
        kg_buckets = []

    # === Корректировка жилой GFA для встроенно-пристроенного ДОО ===
    # Здание ДОО физически входит в жилой дом → его площадь вычитается из жилой GFA.
    # Пересчитываем apartments_area и население (все downstream-расчёты используют
    # обновлённые значения). ДОО-места уже посчитаны по «доковскому» населению —
    # погрешность незначительна (kg_bld_total ≈ 3–5% от residential_gfa).
    # Если только_потребность — объект размещается ВНЕ квартала, корректировка не нужна.
    _kg_only_demand = options.include_kindergarten and options.kindergarten.only_demand
    _kg_builtin_adj = (
        options.include_kindergarten
        and kg_btype == "built_in"
        and kg_bld_total > 0
        and not _kg_only_demand
    )
    if _kg_builtin_adj:
        residential_gfa = max(0.0, residential_gfa - kg_bld_total)
        apartments_area_v = residential_gfa * apt_ratio
        pop_v = population.population(apartments_area_v, hp)
        pop_check_v = population.population(apartments_area_v, hp_check)
        density_v = population.density_chel_per_ga(pop_v, site.area_m2)
        density_check_v = population.density_chel_per_ga(pop_check_v, site.area_m2)
        # v0.10.11: статус с учётом enforce_density_norm (как в раннем блоке).
        _density_over = density_check_v > density_max
        density_status = (
            Status.ERROR if (_density_over and options.enforce_density_norm)
            else Status.OK
        )

    # (Блок стилобата перенесён ВЫШЕ — до ДОО/СОШ, v0.12.10 — чтобы соцобъекты
    #  считались от скорректированного населения. См. «Стилобатная парковка».)

    # v0.10.11: предупреждение о плотности — по ИТОГОВОМУ значению (после
    # корректировки на встроенный ДОО), чтобы не показывать устаревшее число.
    if _density_over:
        soft = "" if options.enforce_density_norm else " (норматив отключён пользователем)"
        warnings.append(_wcprefix(
            WC.DENSITY_ABOVE_LIMIT,
            f"Плотность {density_check_v:.0f} чел/га > норматива {density_max} (по 20 м²/чел){soft}",
        ))

    # === СОШ ===
    # Резолвим нормативы заранее (см. ДОО выше).
    sch_per_1000 = norms.resolve("social_objects.school.places_per_1000")
    sch_round = norms.resolve("social_objects.school.rounding")
    sch_status = Status.OK
    if options.include_school and pop_v > 0:
        sch_required_raw = school.required_places(pop_v, sch_per_1000)
        sch_accepted = school.round_places(sch_required_raw, sch_round)
        try:
            sch_cap_min = norms.resolve(
                "social_objects.school.capacity_min",
                building_type=options.school.building_type,
            )
        except KeyError:
            sch_cap_min = None
        try:
            sch_cap_max = norms.resolve(
                "social_objects.school.capacity_max",
                building_type=options.school.building_type,
            )
        except KeyError:
            sch_cap_max = None
        sch_cap_min_src = (
            norms.source_of(
                "social_objects.school.capacity_min",
                building_type=options.school.building_type,
            )
            if sch_cap_min else None
        )
        sch_allowed_strict = (
            norms.resolve("social_objects.school.allowed_capacities")
            if options.school.strict_capacity else None
        )
        # v0.12.22: ЗУ-функция (с бассейном/спорт-ядром) для выбора типовых
        # вместимостей по минимуму ЗУ — крупная школа в ступени 22 м²/место
        # может дать меньше ЗУ, чем пара средних по 24.
        _sch_cost_fn = (
            lambda caps: sum(
                school.plot_area_with_extras(
                    c, norms, options.school.has_pool, options.school.has_sport_core
                )
                for c in caps
            )
        )
        sch_buckets = school.split_into_objects(
            total_places=sch_accepted,
            spec_capacity=options.school.capacity_per_object,
            spec_count=options.school.num_objects,
            capacity_min=sch_cap_min,
            capacity_max=sch_cap_max,
            multiple=int(sch_round),
            allowed_capacities=sch_allowed_strict,
            cost_fn=_sch_cost_fn,
        )
        sch_plot_total = sum(
            school.plot_area_with_extras(
                c, norms, options.school.has_pool, options.school.has_sport_core
            )
            for c in sch_buckets
        )
        sch_bld_total = sum(school.building_area_for_capacity(c, norms) for c in sch_buckets)
        # v0.12.4: «принято мест» = фактическая вместимость корпусов (Σ buckets);
        # в strict-режиме снап вверх к типовым параллелям → виден профицит.
        if sch_buckets:
            sch_accepted = sum(sch_buckets)
        # Проверка минимальной вместимости: расчёт даёт меньше норматива.
        # Для СПб нет «built_in» школ → минимум распространяется на любую СОШ.
        if sch_cap_min and sch_buckets and any(c < sch_cap_min for c in sch_buckets):
            sch_status = Status.WARNING
            offenders_lo = [c for c in sch_buckets if c < sch_cap_min]
            src_str = f" ({sch_cap_min_src})" if sch_cap_min_src else ""
            warnings.append(_wcprefix(WC.SOC_CAP_MIN_BELOW,
                f"СОШ: расчётная вместимость {offenders_lo} < нормативного минимума "
                f"{sch_cap_min} мест{src_str} — стандартная отдельно стоящая СОШ невозможна, "
                "необходимо размещение начальной СОШ или стандартной СОШ "
                "вне границ территории."
            ))
        # Проверка списка допустимых вместимостей (параллели II–IX по данным КС).
        # Не дублируем для c < sch_cap_min (уже предупреждено выше).
        try:
            allowed_caps = norms.resolve("social_objects.school.allowed_capacities")
            allowed_caps_src = norms.source_of("social_objects.school.allowed_capacities")
        except KeyError:
            allowed_caps = None
            allowed_caps_src = None
        if allowed_caps and sch_buckets:
            def _sch_skip(c: int) -> bool:
                return bool(sch_cap_min) and c < sch_cap_min
            sch_extra = _build_allowed_warnings(
                sch_buckets, allowed_caps, "СОШ",
                source=allowed_caps_src, skip=_sch_skip,
            )
            if sch_extra:
                sch_status = Status.WARNING
                warnings.extend(sch_extra)
    else:
        sch_required_raw = sch_accepted = 0
        sch_plot_total = sch_bld_total = 0.0
        sch_buckets = []

    # === Организации доп. образования (ВРИ 3.5.1, v0.12.15) ===
    # Вынесены из обязательных ВПП в отдельный соцобъект. Размещение по порогу
    # 150 мест: <150 → встроенный (ВПП, здание из жилой GFA); ≥150 → отд. стоящий
    # (ЗУ 15 м²/место + 30% озеленения). Парковка — по формуле соцобъектов.
    ae_res = None
    ae_required_raw = 0.0
    _ae_only_demand = (
        options.include_add_education and options.add_education.only_demand
    )
    if options.include_add_education and pop_v > 0:
        ae_required_raw = add_education.required_places(
            pop_v, norms.resolve("social_objects.add_education.places_per_1000")
        )
        ae_res = add_education.compute(
            pop_v, norms,
            mode=options.add_education.mode,
            places_override=options.add_education.places_override,
            force_vpp=options.add_education.in_vpp,
        )
        # ВПП при местах ≥ порога → дробление на несколько встроенных объектов.
        if ae_res.built_in and ae_res.n_objects > 1 and not _ae_only_demand:
            warnings.append(_wcprefix(
                WC.ADD_EDU_VPP_SPLIT,
                f"Доп. образование во ВПП: {ae_res.places} мест ≥ "
                f"{ae_res.threshold} → размещается как {ae_res.n_objects} "
                f"встроенных объекта(ов) (порог 150 — на один объект)."
            ))
        # Встроенный (ВПП) и не «только потребность» → здание из жилой GFA.
        if ae_res.built_in and ae_res.building_area > 0 and not _ae_only_demand:
            residential_gfa = max(0.0, residential_gfa - ae_res.building_area)
            apartments_area_v = residential_gfa * apt_ratio
            pop_v = population.population(apartments_area_v, hp)
            pop_check_v = population.population(apartments_area_v, hp_check)
            density_v = population.density_chel_per_ga(pop_v, site.area_m2)
            density_check_v = population.density_chel_per_ga(pop_check_v, site.area_m2)
            _density_over = density_check_v > density_max
            density_status = (
                Status.ERROR if (_density_over and options.enforce_density_norm)
                else Status.OK
            )

    # Эффективные значения для баланса/озеленения (only_demand → вне квартала).
    if ae_res is not None and not _ae_only_demand:
        ae_plot_in_balance = ae_res.plot_area          # 0 для ВПП
        ae_greening_builtin = (                         # озеленение ВПП-здания
            ae_res.building_area * norms.resolve("greening.vpp_per_floor_area")
            if ae_res.built_in else 0.0
        )
        ae_parking_places = ae_res.parking_places if options.include_parking else 0
    else:
        ae_plot_in_balance = 0.0
        ae_greening_builtin = 0.0
        ae_parking_places = 0

    # === Амбулаторно-поликлинические учреждения (ВРИ 3.4.1, v0.12.28) ===
    # Вынесены из обязательных ВПП в отдельный соцобъект. Размещение по порогу
    # 150 посещений: <150 → ВПП (офис врача, 1 объект ≤100 → дробление, здание
    # из жилой GFA); ≥150 → отд. стоящая поликлиника (ЗУ 10 м²/посещ., мин. 2000,
    # +15% озеленения, здание 23 м²/посещ.). Парковка: 5 раб + 40 посетителей.
    poly_res = None
    poly_required_raw = 0.0
    _poly_only_demand = (
        options.include_polyclinic and options.polyclinic.only_demand
    )
    if options.include_polyclinic and pop_v > 0:
        poly_required_raw = polyclinic.required_visits(
            pop_v, norms.resolve("social_objects.polyclinic.visits_per_1000")
        )
        poly_res = polyclinic.compute(
            pop_v, norms,
            mode=options.polyclinic.mode,
            visits_override=options.polyclinic.visits_override,
            force_vpp=options.polyclinic.in_vpp,
        )
        if poly_res.built_in and poly_res.n_objects > 1 and not _poly_only_demand:
            warnings.append(_wcprefix(
                WC.POLYCLINIC_VPP_SPLIT,
                f"Поликлиника во ВПП: {poly_res.visits} посещ. → размещается как "
                f"{poly_res.n_objects} офиса(ов) врача общей практики "
                f"(1 объект ≤ 100 посещений)."
            ))
        # Встроенный (ВПП) и не «только потребность» → здание из жилой GFA.
        if poly_res.built_in and poly_res.building_area > 0 and not _poly_only_demand:
            residential_gfa = max(0.0, residential_gfa - poly_res.building_area)
            apartments_area_v = residential_gfa * apt_ratio
            pop_v = population.population(apartments_area_v, hp)
            pop_check_v = population.population(apartments_area_v, hp_check)
            density_v = population.density_chel_per_ga(pop_v, site.area_m2)
            density_check_v = population.density_chel_per_ga(pop_check_v, site.area_m2)
            _density_over = density_check_v > density_max
            density_status = (
                Status.ERROR if (_density_over and options.enforce_density_norm)
                else Status.OK
            )

    if poly_res is not None and not _poly_only_demand:
        poly_plot_in_balance = poly_res.plot_area
        poly_greening_builtin = (
            poly_res.building_area * norms.resolve("greening.vpp_per_floor_area")
            if poly_res.built_in else 0.0
        )
        poly_parking_places = poly_res.parking_places if options.include_parking else 0
    else:
        poly_plot_in_balance = 0.0
        poly_greening_builtin = 0.0
        poly_parking_places = 0

    # === Плоскостные спортивные сооружения (ВРИ 5.1.3, v0.6.8) ===
    # Норматив: 1000 м²/1000 чел + озеленение 40% от ЗУ.
    # До 49% озеленения замещается самой спортплощадкой (п.1.9.4 ПЗЗ).
    # ЗУ_спорта = sport_area + greening_extra.
    if options.include_sport_facilities:
        sport_br = sport.compute(
            pop_v, norms,
            area_override_m2=options.sport_facilities.area_override_m2,
        )
    else:
        sport_br = sport.SportBreakdown(0.0, 0.0, 0.0, 0.0, 0.0)

    # === Возможный двойной счёт: норматив + такой же польз. объект (v0.19.1) ===
    # Модель сама считает ДОО/СОШ/доп.обр/поликлинику/спорт. Если пользователь
    # ДОПОЛНИТЕЛЬНО завёл объект с тем же ВРИ, он попадёт в баланс и экономику
    # ВТОРОЙ раз. Намерение модели неизвестно (это может быть другой объект той
    # же категории), поэтому — предупреждение, а не запрет.
    if options.custom_objects:
        _dup_map: list[tuple[str, list[tuple[bool, str]]]] = [
            ("3.5", [(options.include_kindergarten, "ДОО"),
                     (options.include_school, "СОШ"),
                     (options.include_add_education, "доп. образование")]),
            ("3.4", [(options.include_polyclinic, "поликлиника")]),
            ("5.1", [(options.include_sport_facilities, "спортплощадки")]),
        ]
        for _o in options.custom_objects:
            _vri = (_o.vri_code or "").strip()
            for _prefix, _objs in _dup_map:
                if not _vri.startswith(_prefix):
                    continue
                _names = [nm for on, nm in _objs if on]
                if not _names:
                    continue
                warnings.append(_wcprefix(
                    WC.CUSTOM_OBJECT_MAY_DUPLICATE,
                    f"Объект «{_o.name}» (ВРИ {_vri}) относится к той же "
                    f"категории, что и нормативные: {', '.join(_names)}. "
                    f"Они уже посчитаны моделью — проверьте, не учтён ли "
                    f"объект дважды (в балансе территории и в экономике). "
                    f"Режим «только рассчитать потребность» экономику не "
                    f"отключает — для этого поставьте объекту «не за счёт "
                    f"застройщика»."
                ))

    # === Эффективные площади соцобъектов для баланса/озеленения ===
    # При only_demand=True объект размещается ВНЕ квартала и не занимает
    # территорию (но потребность считается и отображается как обычно).
    _sch_only_demand = options.include_school and options.school.only_demand
    _sport_only_demand = (
        options.include_sport_facilities and options.sport_facilities.only_demand
    )
    kg_plot_in_balance = 0.0 if _kg_only_demand else kg_plot_total
    sch_plot_in_balance = 0.0 if _sch_only_demand else sch_plot_total
    sport_plot_in_balance = 0.0 if _sport_only_demand else sport_br.plot_area

    # === Парковки соцобъектов (ДОО, СОШ) — v0.7.0 ===
    # Считаем ЗДЕСЬ (до greening), т.к. их ЗУ нужно вычесть из знаменателя
    # 25%-озеленения квартала. Парковки — открытые на собственном ЗУ
    # соцобъекта; в общий пул жилищных парковок НЕ вливаются.
    sp_kg_active = (
        options.include_kindergarten
        and not _kg_only_demand
        and options.include_parking
    )
    sp_sch_active = (
        options.include_school
        and not _sch_only_demand
        and options.include_parking
    )
    soc_park = social_parking.compute(
        kg_buckets, sch_buckets, norms,
        kg_include=sp_kg_active,
        sch_include=sp_sch_active,
    )
    open_space_per_place = norms.resolve("parking.open_space_per_place")
    # v0.12.28.2: парковка доп.обр/поликлиники зависит от размещения:
    #   ВСТРОЕННЫЕ (ВПП) → открытые на ЗУ ЖИЛОЙ застройки (residential lot);
    #   ОТДЕЛЬНО СТОЯЩИЕ → на собственном ЗУ соцобъекта (как ДОУ/СОШ).
    _ae_built = bool(ae_res.built_in) if ae_res is not None else False
    _poly_built = bool(poly_res.built_in) if poly_res is not None else False
    ae_park_standalone = 0 if _ae_built else ae_parking_places
    poly_park_standalone = 0 if _poly_built else poly_parking_places
    builtin_social_park_places = (
        (ae_parking_places if _ae_built else 0)
        + (poly_parking_places if _poly_built else 0)
    )
    builtin_social_park_area = builtin_social_park_places * open_space_per_place
    # Соц-парковки на СОБСТВЕННЫХ ЗУ (ДОУ + СОШ + отд. стоящие доп.обр/поликлиника):
    # отдельный компонент баланса, исключается из знаменателя 25%-озеленения.
    soc_park_places_total = (
        soc_park.total_places + ae_park_standalone + poly_park_standalone
    )
    soc_park_area = soc_park_places_total * open_space_per_place

    # === Инженерная инфраструктура (v0.12) ===
    # Считается здесь (после соцобъектов, до знаменателя озеленения), т.к. её
    # ЗУ исключается из базы 25%-норматива наравне с ДОО/СОШ/спортом.
    # ТП дополнительно ставится на каждый ФИЗИЧЕСКИ размещаемый соцобъект
    # (ДОО/СОШ/поликлиника). Объекты «только потребность» размещаются вне
    # квартала → ТП на них здесь не нужна. Поликлиника = CustomObject c ВРИ 3.4*.
    n_kg_obj = len(kg_buckets) if (options.include_kindergarten and not _kg_only_demand) else 0
    n_sch_obj = len(sch_buckets) if (options.include_school and not _sch_only_demand) else 0
    # Доп. образование: ТП нужна только отдельно стоящему (встроенный — в составе
    # жилого дома, питается от его ТП; «только потребность» — вне квартала).
    n_ae_obj = (
        1 if (ae_res is not None and not ae_res.built_in
              and not _ae_only_demand and ae_res.places > 0)
        else 0
    )
    # Поликлиника: ТП нужна только отдельно стоящей (ВПП-офис врача — в жилом доме).
    n_poly_obj = (
        1 if (poly_res is not None and not poly_res.built_in
              and not _poly_only_demand and poly_res.visits > 0)
        else 0
    )
    n_med_custom = sum(
        1 for o in options.custom_objects
        if (o.vri_code or "").strip().startswith("3.4")
    )
    eng_result = engineering.compute_engineering(
        apartments_area=apartments_area_v,
        population=pop_v,
        n_social_objects=n_kg_obj + n_sch_obj + n_ae_obj + n_poly_obj + n_med_custom,
        norms=norms,
        spec=options.engineering,
    )
    # v0.15.7: автономная инженерия ПО ЛОТАМ — встраивается в баланс/экономику.
    # Каждый лот получает свой комплект по собственному спросу; агрегат
    # замещает квартальную схему (объекты «— лот N», Σ площадей — в баланс,
    # объекты с мощностями — в экономику). Если очереди не строятся (авто-режим
    # без опоры) — остаётся квартальная схема.
    _ph_spec = getattr(options, "phasing", None)
    if _ph_spec is not None and getattr(_ph_spec, "engineering_by_lots", False):
        from urban_model.calculations.phasing import lot_engineering_totals
        _lot_eng = lot_engineering_totals(
            apartments_area_v, pop_v, kg_buckets, sch_buckets,
            kg_required_raw, sch_required_raw, _ph_spec, norms,
            options.engineering,
            count_kg=n_kg_obj > 0, count_sch=n_sch_obj > 0,
            n_extra_social=n_ae_obj + n_poly_obj + n_med_custom,
        )
        if _lot_eng is not None:
            eng_result = _lot_eng
    # В баланс/озеленение инженерка входит, только если include_engineering=True.
    eng_plot_in_balance = (
        eng_result.plot_in_balance if options.include_engineering else 0.0
    )

    # === Озеленение жилого ЗУ (нужно до housing_lot) ===
    green_ratio = norms.resolve("greening.housing_per_apartments")
    green_housing_v = greening.housing_greening_area(apartments_area_v, green_ratio)

    # === ВПП — парковки и озеленение по списку (v0.7.1.1: единый коэф) ===
    # Все ВПП считаются по среднему коэффициенту parking.vpp.m2_per_place
    # (1.56 м/м на 100 м² = 64 м²/м.м). Упрощение от детальных формул
    # по каждому ВРИ — практически не теряем точности, выигрываем в простоте.
    if all_built_ins and bi_area > 0:
        bi_per_place = norms.resolve("parking.vpp.m2_per_place")
        bi_greening_ratio = norms.resolve("greening.vpp_per_floor_area")
        bi_parking_places = 0
        bi_greening_v = 0.0
        for b in all_built_ins:
            bi_parking_places += math.ceil(b.area_m2 / bi_per_place)
            bi_greening_v += b.area_m2 * bi_greening_ratio
    else:
        bi_per_place = None
        bi_parking_places = 0
        bi_greening_v = 0.0

    # === Произвольные объекты (офис, ФОК, поликлиника и т.п.) ===
    # Каждый занимает свой ЗУ (вычитается из квартала), требует парковок
    # и даёт озеленение по своему ВРИ-коду.
    custom_total_plot = 0.0
    custom_total_parking_places = 0
    custom_total_greening = 0.0
    custom_objects_summary: list[dict] = []
    if options.custom_objects:
        green_ratio_vpp = norms.resolve("greening.vpp_per_floor_area")
        for obj in options.custom_objects:
            floor_area = obj.floor_area_m2 or obj.plot_area_m2
            # v0.7.1.1: единый коэффициент для всех нежилых объектов
            per_place = norms.resolve("parking.vpp.m2_per_place")
            obj_parking = math.ceil(floor_area / per_place)
            obj_greening = floor_area * green_ratio_vpp
            custom_total_plot += obj.plot_area_m2
            custom_total_parking_places += obj_parking
            custom_total_greening += obj_greening
            custom_objects_summary.append({
                "name": obj.name,
                "plot": obj.plot_area_m2,
                "floor": floor_area,
                "vri": obj.vri_code,
                "parking_places": obj_parking,
                "greening": obj_greening,
            })

    # === Парковки (разбивка по типам, включая ВПП и пользовательские объекты) ===
    # ВНИМАНИЕ: парковки ВПП и кастомных объектов «вливаются» в общий пул
    # м/м и распределяются в выбранном пользователем режиме (min_open / all_open
    # / custom). По нормативам ПЗЗ парковки нежилых объектов могут размещаться
    # на собственном ЗУ объекта, либо на стоянках-спутниках. Текущая модель —
    # упрощение: считаем суммарную нагрузку.

    # Жилищные парковки + ВПП + кастомные — общий пул (парковки соцобъектов
    # НЕ входят: см. блок «Парковки соцобъектов» выше).
    park = compute_parking_breakdown(
        apartments_area_v, options.parking, norms,
        additional_places=bi_parking_places + custom_total_parking_places,
        stylobate_places=stylobate_places_v,
    )

    # === Проверка норматива открытых м/м (v0.12.10) ===
    # ≥12.5% всех м/м должны быть открытыми (гостевые/МГН). Если закрытые типы
    # (особенно большой стилобат) забрали почти весь пул — открытых не хватает.
    if options.include_parking and park.total_required > 0:
        _open_min_share = norms.resolve("parking.open_share_min")
        _open_share_actual = park.open_places / park.total_required
        if _open_share_actual < _open_min_share - 1e-6:
            warnings.append(_wcprefix(
                WC.PARKING_OPEN_BELOW_MIN,
                f"Открытых м/м {park.open_places} из {park.total_required} "
                f"({_open_share_actual*100:.1f}%) — ниже норматива "
                f"{_open_min_share*100:.1f}%. Слишком большая доля закрытых "
                f"парковок (стилобат/подземка/МУ) не оставила места под открытые "
                f"гостевые/МГН. Уменьшите долю закрытых типов."
            ))

    # === Проверка реалистичности подземного паркинга (v0.8.5, AUDIT P0-7) ===
    # v0.9.17: на больших проектах подземка разбивается на НЕСКОЛЬКО СЕКЦИЙ
    # (под разными корпусами/дворами). Норматив 400 м/м/уровень — это потолок
    # ОДНОЙ секции. Поэтому при общем количестве > 400 × levels не сразу
    # бьём warning — допускаем до ~4 секций (16× потолок). Реалистично:
    # 5 га, 1 уровень, 1600 м/м = 4 секции по 400.
    if park.underground_places > 0:
        try:
            ug_cap_per_level = norms.resolve("parking.underground_capacity_per_level")
        except KeyError:
            ug_cap_per_level = None
        ug_levels = getattr(options.parking, "underground_levels", 1) or 1
        # 4 секции × levels — типичный максимум для крупных проектов СПб
        _UG_MAX_SECTIONS = 4
        soft_limit = ug_cap_per_level * ug_levels * _UG_MAX_SECTIONS if ug_cap_per_level else None
        if soft_limit and park.underground_places > soft_limit:
            warnings.append(_wcprefix(WC.PARKING_UG_OVERPACKED,
                f"Подземный паркинг: {park.underground_places} м/м на {ug_levels} "
                f"уровн. — это >{_UG_MAX_SECTIONS} секций по {ug_cap_per_level} м/м. "
                f"Уточните допустимость такой концентрации на квартале или "
                f"увеличьте `underground_levels`."
            ))

    # === Проезды ===
    # v0.18.0: две схемы внутриквартальных проездов (`driveways_intra_mode`):
    #   by_objects (по умолчанию) — гибрид: территориальная база (магистральная
    #       сеть между лотами) + подъезды к КАЖДОМУ объекту. Калибровка базы
    #       0.06 — по фактическим проектам (см. driveways в spb.yaml).
    #   quarter_share — только доля от квартала (схема до v0.18, для сверки).
    # Override доли работает в обоих режимах: заменяет базовую долю.
    _by_objects = options.driveways_intra_mode == "by_objects"
    _base_key = ("driveways.intra_quarter_base_share" if _by_objects
                 else "driveways.intra_quarter_share")
    drive_intra_share = (
        options.driveways_intra_share_override
        if options.driveways_intra_share_override is not None
        else norms.resolve(_base_key)
    )
    if options.driveways_lot_share_override is not None:
        drive_lot_share = options.driveways_lot_share_override
    elif _clusters:
        # Доля проездов на ЗУ — piecewise(floors). При кластерах берём
        # средневзвешенную по площади (= доле пятна застройки): проезды
        # каждого кластера ∝ его пятну, а пятно_i ∝ area_i.
        _w = clusters.area_weights(_clusters)
        drive_lot_share = sum(
            w * norms.resolve("driveways.housing_lot_share", floors=c.floors)
            for w, c in zip(_w, _clusters)
        )
    else:
        drive_lot_share = norms.resolve(
            "driveways.housing_lot_share", floors=options.floors
        )
    drive_intra_v = driveways.intra_quarter_area(site.area_m2, drive_intra_share)
    drive_lot_v = driveways.housing_lot_driveways_area(footprint_v, drive_lot_share)

    # Подъезды к объектам (v0.17.0 — соцобъекты; v0.18.0 — все группы).
    # Начисляются за КАЖДЫЙ физически размещаемый объект:
    #   600 — корпус ДОО (включая встроенный: подъезд/разворот нужен и ему) и СОШ;
    #   300 — иной отд. стоящий (доп. образование, поликлиника, пользовательские
    #         объекты) и крупная инженерия (котельная, ОСПС);
    #   150 — компактная инженерия (ТП, РТП, ГРП, насосная);
    #   120 — многоуровневый паркинг (по периметру застройки).
    # «Только потребность» → объект вне квартала, подъезд не нужен (n_*_obj и
    # in_balance это уже учитывают). Спортплощадки не считаются — обслуживаются
    # общей сетью.
    _soc_access_m2 = norms.resolve("driveways.social_object_access_m2")
    n_drive_soc = n_kg_obj + n_sch_obj
    n_drive_other = n_ae_obj + n_poly_obj + len(options.custom_objects)
    n_drive_eng_small = n_drive_eng_big = 0
    if options.include_engineering and eng_result is not None:
        for _o in eng_result.objects:
            if not _o.in_balance or _o.count <= 0:
                continue
            if _o.key in ("tp", "rtp", "grp", "pump"):
                n_drive_eng_small += _o.count
            elif _o.key in ("boiler", "osps"):
                n_drive_eng_big += _o.count
    n_drive_ml = int(park.multilevel_objects or 0) if options.include_parking else 0

    if _by_objects:
        _other_m2 = norms.resolve("driveways.other_object_access_m2")
        _eng_m2 = norms.resolve("driveways.engineering_object_access_m2")
        _ml_m2 = norms.resolve("driveways.parking_multilevel_access_m2")
        drive_soc_access_v = (
            _soc_access_m2 * n_drive_soc
            + _other_m2 * (n_drive_other + n_drive_eng_big)
            + _eng_m2 * n_drive_eng_small
            + _ml_m2 * n_drive_ml
        )
    else:
        # Схема до v0.18: подъезды только к соцобъектам, все по 600.
        drive_soc_access_v = _soc_access_m2 * (n_drive_soc + n_drive_other)
    drive_intra_v += drive_soc_access_v

    # === Эффективные значения с учётом include_* флагов (v0.6.7) ===
    # Если компонент отключён — он размещается за пределами квартала и не
    # занимает территорию (но информационные поля по нему рассчитываются).
    parking_open_in_lot = 0.0 if not options.include_parking else park.open_area
    parking_ml_footprint_in_balance = (
        0.0 if not options.include_parking else park.multilevel_footprint
    )
    drive_intra_in_balance = 0.0 if not options.include_intra_driveways else drive_intra_v

    # === Площадь жилого ЗУ (ЗУ жилой застройки) ===
    # ЗУ жилья = площадь застройки + проезды на ЗУ + озеленение жилого
    #            + озеленение ВПП + открытые парковки (если учитываются)
    # v0.12.28.2: + открытые парковки ВСТРОЕННЫХ доп.обр/поликлиники (они
    # размещаются на ЗУ жилья в открытом исполнении).
    builtin_social_park_in_lot = (
        0.0 if not options.include_parking else builtin_social_park_area
    )
    housing_lot_v = (
        footprint_v + drive_lot_v + green_housing_v + bi_greening_v
        + ae_greening_builtin + poly_greening_builtin + parking_open_in_lot
        + builtin_social_park_in_lot
    )

    # === КИТ ПЗЗ = площадь квартир / ЗУ жилой застройки ===
    # Это и есть «правильный» КИТ по нормативам ПЗЗ СПб. ИМЕННО ОН сравнивается
    # с пороговыми значениями ЗНОП (1.6/1.8/2.0) и нормативным максимумом
    # КИТ_max (1.4 без ДПТ; 2.5 с ДПТ). Внутренний block_density (gfa/site)
    # — лишь техническая переменная бисекции.
    kit_developed = (apartments_area_v / housing_lot_v) if housing_lot_v > 0 else 0.0

    # === Покластерные КИТ и население (v0.11.0, для ЗНОП и detail) ===
    # Та же формула КИТ_i, что в floor_clusters_detail ниже: чтобы ЗНОП
    # при кластерах считался от КИТ КАЖДОЙ зоны (по запросу заказчика).
    _cluster_kits: list[float] = []
    _cluster_pops: list[float] = []
    if _clusters:
        _gw0 = clusters.gfa_weights(_clusters)
        for c, w in zip(_clusters, _gw0):
            gfa_i = gfa_v * w
            apt_i = apartments_area_v * w
            footprint_i = gfa_i / c.floors if c.floors > 0 else 0.0
            if options.driveways_lot_share_override is not None:
                lot_share_i = options.driveways_lot_share_override
            else:
                lot_share_i = norms.resolve(
                    "driveways.housing_lot_share", floors=c.floors)
            green_i = green_housing_v * w
            bi_green_i = bi_greening_v * w
            open_park_i = (parking_open_in_lot + builtin_social_park_in_lot) * w
            lot_i = (footprint_i + footprint_i * lot_share_i
                     + green_i + bi_green_i + open_park_i)
            _cluster_kits.append((apt_i / lot_i) if lot_i > 0 else 0.0)
            _cluster_pops.append(pop_v * w)

    # === ЗНОП — теперь по КИТ ПЗЗ, а не по block_density ===
    # Приоритет источника: фиксированная площадь > м²/чел > норматив piecewise.
    if options.znop_total_area_override is not None:
        # Пользователь задал общую площадь ЗНОП напрямую (м²)
        znop_area_v = float(options.znop_total_area_override)
        znop_pp = znop_area_v / pop_v if pop_v > 0 else 0.0
        znop_source_label = (
            f"override = {znop_area_v:,.0f} м² (общая площадь, заменяет норматив)"
        )
    elif options.znop_per_person_override is not None:
        # Ручной override м²/чел (для режима solve_max_kit_with_znop)
        znop_pp = options.znop_per_person_override
        znop_area_v = pop_v * znop_pp
        znop_source_label = f"override = {znop_pp} м²/чел (заменяет норматив)"
    elif _clusters:
        # v0.11.0: при кластерах ЗНОП считается ПОКЛАСТЕРНО — каждая зона
        # берёт свою ступень ЗНОП по своему КИТ (население_i × норматив_i).
        znop_area_v, znop_pp = znop.znop_total_area_clustered(
            _cluster_pops, _cluster_kits, norms)
        znop_source_label = (
            "ПЗЗ СПб (piecewise по КИТ каждой зоны; средневзвеш. "
            f"{znop_pp:.1f} м²/чел)"
        )
    else:
        # Защитный clamp: piecewise ZNOP заканчивается ступенью kit ≤ 2.50.
        # Если бисекция дала kit > 2.5 (формально невалидно, kit_status станет
        # ERROR ниже), берём ZNOP с последней ступени.
        znop_kit_for_lookup = min(kit_developed, 2.50)
        znop_pp = znop.znop_per_person(znop_kit_for_lookup, norms)
        znop_area_v = znop.znop_total_area(pop_v, znop_kit_for_lookup, norms)
        znop_source_label = norms.source_of(
            "znop_per_person", kit=znop_kit_for_lookup
        )

    # === Проверка ЗНОП ≥ норматива ПЗЗ для КИТ (v0.12.11) ===
    # Актуально при ручном override (фиксированная площадь / м²-чел): если
    # задано меньше, чем требует ПЗЗ для достигнутого КИТ — норматив нарушен.
    # (В нормативном режиме ЗНОП считается ровно по минимуму → проверка пройдёт.)
    if options.include_znop and not options.znop_only_demand and pop_v > 0:
        _znop_pp_req = znop.znop_per_person(min(kit_developed, 2.50), norms)
        _znop_req = pop_v * _znop_pp_req
        if znop_area_v < _znop_req - 1.0:
            _msg = (
                f"ЗНОП {znop_area_v:,.0f} м² ({znop_pp:.1f} м²/чел) ниже норматива "
                f"ПЗЗ для КИТ {kit_developed:.2f}: требуется ≥ {_znop_req:,.0f} м² "
                f"({_znop_pp_req:.1f} м²/чел). Увеличьте площадь ЗНОП или снизьте КИТ."
            ).replace(",", " ")
            warnings.append(_wcprefix(WC.ZNOP_BELOW_MIN, _msg))

    # === Баланс квартала ===
    # При include_*=False / only_demand компоненты в баланс не входят (см. выше).
    # v0.8.8: znop_only_demand — площадь считается, но не в балансе/озеленении.
    _znop_off = (not options.include_znop) or options.znop_only_demand
    znop_in_balance = 0.0 if _znop_off else znop_area_v
    components = {
        "housing_lot": housing_lot_v,
        "kindergarten_plot": kg_plot_in_balance,
        "school_plot": sch_plot_in_balance,
        "sport_facilities": sport_plot_in_balance,
        "social_parking_plot": soc_park_area,
        "znop": znop_in_balance,
        "intra_quarter_driveways": drive_intra_in_balance,
        "parking_multilevel": parking_ml_footprint_in_balance,
        "engineering_plot": eng_plot_in_balance,
    }
    if ae_plot_in_balance > 0:
        components["add_education_plot"] = ae_plot_in_balance
    if poly_plot_in_balance > 0:
        components["polyclinic_plot"] = poly_plot_in_balance
    if custom_total_plot > 0:
        components["custom_objects"] = custom_total_plot
    # Фактическое озеленение квартала: ЗНОП + озеленение жилого ЗУ + ВПП + кастомные.
    # === Контроль озеленения ТОП (СП 42.13330.2026, прим. 4 табл. М.1) ===
    # Требование: озеленённые территории ОБЩЕГО ПОЛЬЗОВАНИЯ ≥ 6 м²/чел.
    # В зачёт идёт ЗНОП (это и есть ТОП) + свободный резерв квартала (его
    # можно отвести под озеленённые ТОП — balance добавляет surplus). Придомовое
    # озеленение жилого ЗУ, ВПП и озеленение на ЗУ доп. объектов — НЕ ТОП, в
    # зачёт 6 м²/чел не входят. Региональный ЗНОП (ПЗЗ) считается как прежде.
    # Стилобатный штраф касается придомового озеленения (не ТОП) — здесь не нужен.
    top_greening_actual = znop_in_balance
    _znop_top_min = norms.resolve("greening.znop_top_min_per_person")
    green_quarter_req_v = _znop_top_min * pop_v      # 6 м²/чел × население
    # greening_required=0 → фактическое значение записывается в TEPResult для
    # аудита, но feasible не блокируется (галочка выключена).
    _greening_required_check = (
        green_quarter_req_v if options.enforce_quarter_greening_norm else 0.0
    )
    bal = balance.compute_balance(
        site.area_m2,
        components,
        greening_actual=top_greening_actual,
        greening_required=_greening_required_check,
    )

    # === Формула парковки для аудит-трейла ===
    park_mode_label = {
        "min_open": f"≥{norms.resolve('parking.open_share_min')*100:.1f}% открытых, остаток подземные",
        "all_open": "100% открытые",
        "custom": (
            f"открытые {options.parking.open_share*100:.0f}% / "
            f"многоуровневые {options.parking.multilevel_share*100:.0f}% / "
            f"подземные {options.parking.underground_share*100:.0f}%"
        ),
    }.get(options.parking.mode, options.parking.mode)

    park_source = norms.source_of("parking.housing.m2_apartments_per_place")
    per_place = norms.resolve("parking.housing.m2_apartments_per_place")

    # === Эффективный потолок КИТ ===
    # Базовый — kit_limits (1.4 без ДПТ / 2.5 с ДПТ).
    # При override ЗНОП piecewise-обратная даёт более жёсткий предел:
    #   ЗНОП=0 → КИТ ≤ 1.59; =3 → ≤ 1.79; =4 → ≤ 1.99; =6 → ≤ 2.50.
    # Если override ЗНОП фиксирован, КИТ должен соответствовать ступени.
    effective_kit_max = kit_norm_max
    znop_cap = None
    if options.znop_per_person_override is not None:
        znop_cap = znop.kit_cap_for_znop(
            float(options.znop_per_person_override), norms
        )
        if znop_cap is not None:
            effective_kit_max = min(kit_norm_max, znop_cap)

    # v0.9.28.1: КИТ — ОБЩИЙ (по всему кварталу), проверяется против
    # нормативного потолка как обычно. Ранее (v0.9.28) потолок поджимался
    # под самый высокий кластер по линейной формуле КИТ_i ∝ F_i — но эта
    # формула неверна (КИТ определяется средневзвешенной этажностью, а не
    # спредом кластеров; озеленение/парковки ∝ площади квартир насыщают КИТ
    # с ростом этажей). Поджатие давало ложный «огромный резерв». Покластерный
    # КИТ остаётся, но только как СПРАВОЧНАЯ величина (см. floor_clusters_detail).

    # === Покластерная разбивка (v0.9.28) — СПРАВОЧНО ===
    # КИТ_i считается честно: площадь квартир_i / ЗУ_i, где ЗУ_i собирается
    # из реальных компонентов своей этажности (пятно_i + проезды(F_i) +
    # озеленение_i + открытые парковки_i). Σ ЗУ_i = ЗУ общий, поэтому КИТ_i —
    # корректная декомпозиция общего КИТ, а не линейная аппроксимация.
    floor_clusters_detail: list[dict] = []
    if _clusters:
        _gw = clusters.gfa_weights(_clusters)
        # KIT_i / pop_i уже посчитаны выше (_cluster_kits/_cluster_pops) — той
        # же формулой; переиспользуем, добавляя ЗНОП каждой зоны по её КИТ.
        for idx_c, (c, w) in enumerate(zip(_clusters, _gw)):
            gfa_i = gfa_v * w
            apt_i = apartments_area_v * w
            footprint_i = gfa_i / c.floors if c.floors > 0 else 0.0
            kit_i = _cluster_kits[idx_c] if idx_c < len(_cluster_kits) else 0.0
            znop_pp_i = znop.znop_per_person(min(kit_i, 2.50), norms)
            floor_clusters_detail.append({
                "label": c.label or f"Кластер {len(floor_clusters_detail) + 1}",
                "area_m2": c.area_m2,
                "floors": c.floors,
                "gfa": gfa_i,
                "apartments_area": apt_i,
                "footprint": footprint_i,
                "kit": kit_i,
                "znop_per_person": znop_pp_i,
            })
        # Σ площадей кластеров vs S_квартала — мягкая проверка (математика
        # масштабно-инвариантна, но расхождение почти всегда означает опечатку).
        _cl_total = sum(c.area_m2 for c in _clusters)
        if site.area_m2 > 0 and abs(_cl_total - site.area_m2) / site.area_m2 > 0.02:
            warnings.append(_wcprefix(WC.CLUSTER_AREA_MISMATCH,
                f"Σ площадей кластеров {_cl_total:,.0f} м² ≠ площади квартала "
                f"{site.area_m2:,.0f} м² (расхождение "
                f"{abs(_cl_total - site.area_m2) / site.area_m2 * 100:.0f}%). "
                f"Этажность усреднена по долям; проверьте ввод площадей."
            ).replace(",", " "))

    # === Сборка TEPResult ===
    # Статус КИТ: ERROR, если он превышает effective_kit_max
    kit_status = Status.ERROR if kit_developed > effective_kit_max + 1e-6 else Status.OK
    if kit_status == Status.ERROR:
        if znop_cap is not None and effective_kit_max < kit_norm_max:
            warnings.append(_wcprefix(WC.KIT_ABOVE_LIMIT,
                f"КИТ {kit_developed:.3f} превышает предел {effective_kit_max} "
                f"для ЗНОП={options.znop_per_person_override} м²/чел "
                f"(норматив piecewise по ПЗЗ СПб)."
            ))
        else:
            warnings.append(_wcprefix(WC.KIT_ABOVE_LIMIT,
                f"КИТ {kit_developed:.3f} превышает нормативный максимум "
                f"{kit_norm_max} (ПЗЗ СПб, ДПТ={'да' if options.planning_doc else 'нет'})"
            ))

    result = TEPResult(
        profile=norms.profile,
        kit=_F(
            kit_developed,
            status=kit_status,
            normative=effective_kit_max,
            formula=(
                f"Площадь квартир / ЗУ жилой застройки = "
                f"{apartments_area_v:,.0f} / {housing_lot_v:,.0f}".replace(",", " ")
                + (
                    f" — потолок снижен до {effective_kit_max} из-за ЗНОП "
                    f"= {options.znop_per_person_override} м²/чел (piecewise ПЗЗ)"
                    if znop_cap is not None and effective_kit_max < kit_norm_max
                    else ""
                )
            ),
            source="ПЗЗ СПб",
        ),
        block_density=_F(
            kit,
            formula=(
                f"Общая площадь зданий (GFA) / Площадь квартала = "
                f"{gfa_v:,.0f} / {site.area_m2:,.0f} — внутренняя величина для бисекции"
            ).replace(",", " "),
        ),
        kit_normative_max=_F(
            kit_norm_max,
            source=norms.source_of(
                "kit_limits", planning_doc="yes" if options.planning_doc else "no"
            ),
            formula=f"Нормативный потолок по ПЗЗ СПб; ДПТ={'да' if options.planning_doc else 'нет'}",
        ),
        gfa=_F(
            gfa_v,
            unit="m2",
            formula=(
                f"Внутренняя плотность × площадь квартала = "
                f"{kit:.3f} × {site.area_m2:,.0f}"
            ).replace(",", " "),
        ),
        apartments_area=_F(
            apartments_area_v,
            unit="m2",
            formula=(
                f"(GFA − ВПП {bi_area:,.0f} м² − встроенный ДОО {kg_bld_total:,.0f} м²) "
                f"× коэф {apt_ratio} (квартиры / общая)"
                if bi_area > 0 and _kg_builtin_adj
                else f"(GFA − встроенный ДОО {kg_bld_total:,.0f} м²) × коэф {apt_ratio}"
                if _kg_builtin_adj
                else f"(GFA − ВПП {bi_area:,.0f} м²) × коэф {apt_ratio}"
                if bi_area > 0
                else f"GFA × (1 − доля ВПП {options.vpp_share}) × коэф {apt_ratio}"
            ).replace(",", " "),
            source=norms.source_of("building_params.apartments_to_gfa_ratio"),
        ),
        built_in_area=_F(
            bi_area,
            unit="m2",
            formula=(
                f"Сумма площадей ВПП: {bi_area:,.0f} м² (первичный ВРИ {bi_vri})".replace(",", " ")
                if bi_area > 0 else "ВПП не задано"
            ),
        ),
        built_in_parking_places=_F(
            bi_parking_places,
            unit="м/м",
            formula=(
                f"ВПП Σ{bi_area:.0f} м² / {bi_per_place} м²/м.м. "
                f"(средний коэф для всех ВПП), вверх по корпусам"
                if bi_area > 0 else "—"
            ),
            source=(
                norms.source_of("parking.vpp.m2_per_place")
                if bi_area > 0 else None
            ),
        ),
        built_in_greening_area=_F(
            bi_greening_v,
            unit="m2",
            formula=(
                f"ВПП {bi_area:.0f} × {norms.resolve('greening.vpp_per_floor_area')}"
                if bi_area > 0 else "—"
            ),
            source=norms.source_of("greening.vpp_per_floor_area") if bi_area > 0 else None,
        ),
        population=_F(
            pop_v, unit="чел", formula=f"S_квартир / {hp}", source=norms.source_of("housing_provision")
        ),
        population_check_20=_F(
            pop_check_v,
            unit="чел",
            formula=f"S_квартир / {hp_check} (формально, для проверки плотности)",
            source=norms.source_of("housing_provision_check"),
        ),
        density_chel_per_ga=_F(
            density_check_v,
            unit="чел/га",
            normative=density_max,
            status=density_status,
            formula=(
                f"Формальная плотность по СП 42.13330: "
                f"S_квартир / {hp_check:g} м²/чел / (S_квартала / 10000) "
                f"= {pop_check_v:.0f} / {site.area_m2/10000:.2f} га. "
                f"Не должна превышать {density_max:g} чел/га."
            ),
            source=norms.source_of("population_density_max"),
        ),
        kindergarten_places_required=_F(
            kg_required_raw,
            unit="мест",
            formula=f"население × {kg_per_1000} / 1000"
            if options.include_kindergarten
            else "ДОО отключены",
            source=norms.source_of("social_objects.kindergarten.places_per_1000")
            if options.include_kindergarten
            else None,
        ),
        kindergarten_places_accepted=_F(
            kg_accepted,
            unit="мест",
            status=kg_status,
            normative=kg_required_raw if options.include_kindergarten else None,
            formula=f"вверх кратно {kg_round} → разбивка по объектам {kg_buckets}"
            if options.include_kindergarten
            else None,
        ),
        kindergarten_plot_area=_F(
            kg_plot_total,
            unit="m2",
            formula=(
                (
                    f"Σ 24 м²/место × вместимость (встроенный ДОО, ПЗЗ СПб) = {kg_plot_total:.0f}"
                    if kg_btype == "built_in" and kg_plot_total > 0
                    else "Σ piecewise(plot_per_place, capacity) по объектам ДОО"
                )
                + (" — только потребность, в баланс не входит" if _kg_only_demand else "")
            ),
            source=(
                norms.source_of("social_objects.kindergarten.plot_area_per_place_built_in")
                if kg_btype == "built_in" and kg_plot_total > 0
                else None
            ),
        ),
        kindergarten_building_area=_F(
            kg_bld_total,
            unit="m2",
            formula=(
                f"Σ piecewise(bld_per_place, capacity) по ДОО"
                + (" — вычтено из жилой GFA (встроенный)" if _kg_builtin_adj else "")
            ),
        ),
        school_places_required=_F(
            sch_required_raw,
            unit="мест",
            formula=f"население × {sch_per_1000} / 1000"
            if options.include_school
            else "СОШ отключены",
            source=norms.source_of("social_objects.school.places_per_1000")
            if options.include_school
            else None,
        ),
        school_places_accepted=_F(
            sch_accepted,
            unit="мест",
            normative=sch_required_raw if options.include_school else None,
            status=sch_status,
            formula=f"вверх кратно {sch_round} → объекты {sch_buckets}"
            if options.include_school
            else None,
        ),
        school_plot_area=_F(
            sch_plot_total,
            unit="m2",
            formula=(
                f"plot(capacity) + бассейн={options.school.has_pool} + ядро={options.school.has_sport_core}"
                + (" — только потребность, в баланс не входит" if _sch_only_demand else "")
            ),
        ),
        school_building_area=_F(sch_bld_total, unit="m2"),
        # ── Организации доп. образования (ВРИ 3.5.1, v0.12.15) ────────────
        add_education_places_required=_F(
            ae_required_raw,
            unit="мест",
            formula=(
                f"население × {norms.resolve('social_objects.add_education.places_per_1000')} / 1000"
                if options.include_add_education else "Доп. образование отключено"
            ),
            source=(
                norms.source_of("social_objects.add_education.places_per_1000")
                if options.include_add_education else None
            ),
        ),
        add_education_places_accepted=_F(
            ae_res.places if ae_res else 0,
            unit="мест",
            normative=ae_required_raw if options.include_add_education else None,
            formula=(
                (
                    f"вверх кратно {norms.resolve('social_objects.add_education.rounding')} → "
                    + (
                        (f"встроенное (ВПП, {ae_res.n_objects} об.)"
                         if ae_res.n_objects > 1 else "встроенное (ВПП)")
                        if ae_res.built_in else "отдельно стоящее"
                    )
                    + (" — только потребность" if _ae_only_demand else "")
                )
                if ae_res else None
            ),
        ),
        add_education_plot_area=_F(
            ae_res.plot_area if ae_res else 0.0,
            unit="m2",
            formula=(
                (
                    f"15 м²/место × {ae_res.places} (отд. стоящее, РМД 15-26-2017)"
                    + (" — только потребность, в баланс не входит" if _ae_only_demand else "")
                )
                if ae_res and not ae_res.built_in
                else "встроенное (ВПП) — ЗУ не выделяется"
                if ae_res else "—"
            ),
            source=(
                norms.source_of("social_objects.add_education.plot_area_per_place")
                if ae_res and not ae_res.built_in else None
            ),
        ),
        add_education_building_area=_F(
            ae_res.building_area if ae_res else 0.0,
            unit="m2",
            formula=(
                (
                    f"17 м²/место × {ae_res.places}"
                    + (" — вычтено из жилой GFA (встроенное)"
                       if ae_res.built_in and not _ae_only_demand else "")
                )
                if ae_res else "—"
            ),
            source=(
                norms.source_of("social_objects.add_education.building_area_per_place")
                if ae_res else None
            ),
        ),
        add_education_parking_places=_F(
            ae_parking_places,
            unit="м/м",
            formula=(
                f"max(2, ⌈{ae_res.workers}/5⌉ + ⌈{ae_res.places}/100⌉) (формула соцобъектов)"
                if ae_res and ae_parking_places > 0 else "—"
            ),
            source=(
                norms.source_of("parking.social_objects.per_worker")
                if ae_parking_places > 0 else None
            ),
        ),
        add_education_built_in=bool(ae_res.built_in) if ae_res else False,
        # ── Амбулаторно-поликлинические учреждения (ВРИ 3.4.1, v0.12.28) ──
        polyclinic_visits_required=_F(
            poly_required_raw,
            unit="посещ.",
            formula=(
                f"население × {norms.resolve('social_objects.polyclinic.visits_per_1000')} / 1000"
                if options.include_polyclinic else "Поликлиника отключена"
            ),
            source=(
                norms.source_of("social_objects.polyclinic.visits_per_1000")
                if options.include_polyclinic else None
            ),
        ),
        polyclinic_visits_accepted=_F(
            poly_res.visits if poly_res else 0,
            unit="посещ.",
            normative=poly_required_raw if options.include_polyclinic else None,
            formula=(
                (
                    (f"ВПП (офис врача, {poly_res.n_objects} об.)"
                     if poly_res.n_objects > 1 else "встроенное (ВПП, офис врача)")
                    if poly_res.built_in else "отдельно стоящая поликлиника"
                )
                + (" — только потребность" if _poly_only_demand else "")
                if poly_res else None
            ),
        ),
        polyclinic_plot_area=_F(
            poly_res.plot_area if poly_res else 0.0,
            unit="m2",
            formula=(
                (
                    f"max(2000, 10 м²/посещ. × {poly_res.visits}) (отд. стоящее, СП 158.13330)"
                    + (" — только потребность, в баланс не входит" if _poly_only_demand else "")
                )
                if poly_res and not poly_res.built_in
                else "ВПП (офис врача) — ЗУ не выделяется"
                if poly_res else "—"
            ),
            source=(
                norms.source_of("social_objects.polyclinic.plot_per_visit")
                if poly_res and not poly_res.built_in else None
            ),
        ),
        polyclinic_building_area=_F(
            poly_res.building_area if poly_res else 0.0,
            unit="m2",
            formula=(
                (
                    f"{8 if poly_res.built_in else 23} м²/посещ. × {poly_res.visits}"
                    + (" — вычтено из жилой GFA (ВПП)"
                       if poly_res.built_in and not _poly_only_demand else "")
                )
                if poly_res else "—"
            ),
            source=(
                norms.source_of("social_objects.polyclinic.building_area_per_visit_vpp")
                if poly_res else None
            ),
        ),
        polyclinic_parking_places=_F(
            poly_parking_places,
            unit="м/м",
            formula=(
                f"max(2, ⌈{poly_res.workers}/5⌉ + ⌈{poly_res.visits}/40⌉) (раб + посетители)"
                if poly_res and poly_parking_places > 0 else "—"
            ),
            source=(
                norms.source_of("social_objects.polyclinic.parking_per_worker")
                if poly_parking_places > 0 else None
            ),
        ),
        polyclinic_built_in=bool(poly_res.built_in) if poly_res else False,
        # ── Парковки соцобъектов (v0.7.0) ────────────────────────────────
        social_parking_total=_F(
            soc_park_places_total,
            unit="м/м",
            formula=(
                f"ДОО: {soc_park.kindergarten_details} + "
                f"СОШ: {soc_park.school_details}"
                + (f" + доп.обр: {ae_parking_places}" if ae_parking_places > 0 else "")
                if soc_park_places_total > 0
                else "—"
            ),
            source=(
                norms.source_of("parking.social_objects.per_worker")
                if soc_park.total_places > 0 else None
            ),
        ),
        social_parking_kindergarten=_F(
            soc_park.kindergarten_places,
            unit="м/м",
            formula=(
                "Σ max(2, ceil(work/5) + ceil(cap/100)) по ДОО: "
                + str([(c, w, p) for c, w, p in soc_park.kindergarten_details])
                if soc_park.kindergarten_details
                else "ДОО парковки не учитываются"
            ),
        ),
        social_parking_school=_F(
            soc_park.school_places,
            unit="м/м",
            formula=(
                "Σ max(2, ceil(work/5) + ceil(cap/100)) по СОШ: "
                + str([(c, w, p) for c, w, p in soc_park.school_details])
                if soc_park.school_details
                else "СОШ парковки не учитываются"
            ),
        ),
        social_parking_area=_F(
            soc_park_area,
            unit="m2",
            formula=(
                f"{soc_park_places_total} м/м × {open_space_per_place} м²/место "
                f"(открытые на ЗУ соцобъекта; вкл. доп. образование)"
                if soc_park_places_total > 0 else "—"
            ),
            source=(
                norms.source_of("parking.open_space_per_place")
                if soc_park.total_places > 0 else None
            ),
        ),
        # ── Плоскостные спортивные сооружения (v0.6.8) ──────────────────
        sport_facilities_area=_F(
            sport_br.sport_area,
            unit="m2",
            formula=(
                f"override = {sport_br.sport_area:.0f} м² (задано вручную)"
                if options.include_sport_facilities
                    and options.sport_facilities.area_override_m2 is not None
                else f"население × {norms.resolve('sport_facilities.area_per_1000')} / 1000"
                if options.include_sport_facilities and sport_br.sport_area > 0
                else "Спорт. сооружения отключены"
            ),
            source=(
                "пользовательский ввод"
                if options.include_sport_facilities
                    and options.sport_facilities.area_override_m2 is not None
                else norms.source_of("sport_facilities.area_per_1000")
                if options.include_sport_facilities and sport_br.sport_area > 0
                else None
            ),
        ),
        sport_facilities_plot_area=_F(
            sport_br.plot_area,
            unit="m2",
            formula=(
                (
                    f"sport={sport_br.sport_area:.0f} + extra_greening="
                    f"{sport_br.greening_extra:.0f} (после substitution "
                    f"{norms.resolve('sport_facilities.greening_substitution_max') * 100:.0f}%)"
                )
                + (" — только потребность, в баланс не входит" if _sport_only_demand else "")
                if options.include_sport_facilities and sport_br.plot_area > 0
                else "—"
            ),
            source=(
                norms.source_of("sport_facilities.greening_ratio")
                if options.include_sport_facilities and sport_br.plot_area > 0
                else None
            ),
        ),
        sport_facilities_greening_required=_F(
            sport_br.greening_required,
            unit="m2",
            formula=(
                f"sport_area × {norms.resolve('sport_facilities.greening_ratio')} (ПЗЗ для ВРИ 5.1.3)"
                if options.include_sport_facilities and sport_br.sport_area > 0
                else "—"
            ),
            source=(
                norms.source_of("sport_facilities.greening_ratio")
                if options.include_sport_facilities and sport_br.sport_area > 0
                else None
            ),
        ),
        sport_facilities_greening_extra=_F(
            sport_br.greening_extra,
            unit="m2",
            formula=(
                f"greening_required − substituted = "
                f"{sport_br.greening_required:.0f} − {sport_br.greening_substituted:.0f}"
                if options.include_sport_facilities and sport_br.sport_area > 0
                else "—"
            ),
            source=(
                norms.source_of("sport_facilities.greening_substitution_max")
                if options.include_sport_facilities and sport_br.sport_area > 0
                else None
            ),
        ),
        znop_per_person=_F(
            znop_pp,
            unit="m2/чел",
            status=(
                Status.MANUAL
                if (options.znop_per_person_override is not None
                    or options.znop_total_area_override is not None)
                else Status.OK
            ),
            formula=(
                f"S_ЗНОП / население = {options.znop_total_area_override:.0f} / {pop_v:.0f}"
                if options.znop_total_area_override is not None
                else f"override = {options.znop_per_person_override}"
                if options.znop_per_person_override is not None
                else f"норматив(КИТ={kit_developed:.3f}) — ступени 0/3/4/6 м²/чел"
            ),
            source=znop_source_label,
        ),
        znop_area=_F(znop_area_v, unit="m2", formula=f"население × {znop_pp}"),
        greening_housing_area=_F(
            green_housing_v,
            unit="m2",
            formula=f"S_квартир × {green_ratio}",
            source=norms.source_of("greening.housing_per_apartments"),
        ),
        greening_quarter_required=_F(
            green_quarter_req_v,
            unit="m2",
            formula=f"{_znop_top_min:g} м²/чел × {pop_v:.0f} чел (озеленённые ТОП)",
            source=norms.source_of("greening.znop_top_min_per_person"),
        ),
        # --- Парковки ---
        parking_required_places=_F(
            park.total_required,
            unit="м/м",
            formula=f"S_квартир / {per_place} = {apartments_area_v:.0f} / {per_place}, вверх",
            source=park_source,
        ),
        parking_open_places=_F(
            park.open_places,
            unit="м/м",
            formula=f"сценарий '{options.parking.mode}': {park_mode_label}",
            source=norms.source_of("parking.open_share_min"),
        ),
        parking_open_area=_F(
            park.open_area,
            unit="m2",
            formula=f"{park.open_places} × {norms.resolve('parking.open_space_per_place')} м²/м.м.",
        ),
        parking_multilevel_places=_F(
            park.multilevel_places,
            unit="м/м",
            formula=f"сценарий '{options.parking.mode}'",
        ),
        parking_multilevel_objects=_F(
            park.multilevel_objects,
            unit="шт",
            formula=(
                f"{park.multilevel_places} м/м / {norms.resolve('parking.multilevel_capacity_max')} макс."
                if park.multilevel_places > 0 else "—"
            ),
            source=norms.source_of("parking.multilevel_capacity_max") if park.multilevel_places > 0 else None,
        ),
        parking_multilevel_area=_F(
            park.multilevel_footprint,
            unit="m2",
            formula=(
                f"{park.multilevel_places} м/м × "
                f"{norms.resolve('parking.multilevel_area_per_place', levels=park.multilevel_levels) if park.multilevel_levels > 0 else '—'} "
                f"м²/м.м. ({park.multilevel_levels} уровн.)"
                if park.multilevel_places > 0 else "—"
            ),
            source=norms.source_of("parking.multilevel_area_per_place", levels=park.multilevel_levels)
            if park.multilevel_levels > 0 else None,
        ),
        parking_underground_places=_F(
            park.underground_places,
            unit="м/м",
            formula="не занимают поверхностную площадь квартала",
        ),
        parking_stylobate_places=_F(
            park.stylobate_places,
            unit="м/м",
            formula="доля жилищной парковки в стилобате",
        ),
        parking_stylobate_area=_F(
            park.stylobate_area,
            unit="m2",
            formula=f"{park.stylobate_places} м/м × {norms.resolve('parking.stylobate_area_per_place')} м²",
        ),
        # --- Проезды ---
        driveways_intra_quarter_area=_F(
            drive_intra_v, unit="m2",
            formula=(
                f"S_квартала × {drive_intra_share}"
                + (f" + подъезды к объектам {drive_soc_access_v:,.0f} м²".replace(",", " ")
                   if drive_soc_access_v > 0 else "")
            ),
            source=norms.source_of(_base_key),
        ),
        driveways_housing_lot_area=_F(
            drive_lot_v, unit="m2", formula=f"S_застройки × {drive_lot_share}"
        ),
        driveways_social_access_area=_F(
            drive_soc_access_v, unit="m2",
            formula=(
                " + ".join(_p for _p in [
                    (f"{_soc_access_m2:g}×{n_drive_soc} ДОО/СОШ"
                     if n_drive_soc else ""),
                    (f"{norms.resolve('driveways.other_object_access_m2'):g}×"
                     f"{n_drive_other + n_drive_eng_big} отд. стоящих"
                     if _by_objects and (n_drive_other + n_drive_eng_big) else ""),
                    (f"{norms.resolve('driveways.engineering_object_access_m2'):g}×"
                     f"{n_drive_eng_small} инж."
                     if _by_objects and n_drive_eng_small else ""),
                    (f"{norms.resolve('driveways.parking_multilevel_access_m2'):g}×"
                     f"{n_drive_ml} МУ-паркинг"
                     if _by_objects and n_drive_ml else ""),
                    (f"{_soc_access_m2:g}×{n_drive_other} иных"
                     if not _by_objects and n_drive_other else ""),
                ] if _p) or "—"
            ),
            source=norms.source_of("driveways.social_object_access_m2"),
        ),
        housing_lot_area=_F(
            housing_lot_v,
            unit="m2",
            formula="застройка + проезды + озеленение + открытые парковки",
        ),
        housing_footprint=_F(
            footprint_v,
            unit="m2",
            formula=(
                f"GFA / этажность = {gfa_v:.0f} / {options.floors}"
                if not _clusters
                else f"GFA / средневзвеш. этажность = {gfa_v:.0f} / {floors_eff:.2f} "
                     f"(кластеров: {len(_clusters)})"
            ),
        ),
        balance=bal,
        built_in_vri_code=bi_vri,
        effective_floors=floors_eff,
        floor_clusters_detail=floor_clusters_detail,
        engineering=eng_result,
        warnings=warnings,
    )

    # === Очерёдность застройки (v0.15.0) ===
    # Раскладка готового результата по этапам; предупреждения о дефицитах
    # соцобъектов на конец очередей добавляются к warnings результата.
    if getattr(options, "phasing", None) is not None:
        from urban_model.calculations.phasing import compute_phasing
        result.phasing = compute_phasing(
            result, options.phasing, norms=norms,
            eng_spec=getattr(options, "engineering", None),
            count_kg=n_kg_obj > 0, count_sch=n_sch_obj > 0,
            n_extra_social=n_ae_obj + n_poly_obj + n_med_custom,
        )
        result.warnings.extend(result.phasing.warnings)

    # === Экономика (v0.8.0) ===
    # После того как ТЭП собран — считаем стоимость / выручку / прибыль
    # и присоединяем к результату. Экономика никак не влияет на сами ТЭП.
    if not getattr(options, "include_economy", True):
        # v0.9.29: экономика отключена пользователем — не считаем, UI скроет блоки.
        result.economy = None
        return result
    try:
        from urban_model.economy import calc_economy
        result.economy = calc_economy(result, options, site, norms)
    except KeyError:
        # Если секция economy не задана в YAML — пропускаем (результат остаётся
        # совместимым с тестами, написанными до v0.8.0).
        result.economy = None
    return result
