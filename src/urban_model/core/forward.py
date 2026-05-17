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
    balance,
    driveways,
    greening,
    housing,
    kindergarten,
    parking,
    population,
    school,
    social_parking,
    sport,
    znop,
)
from urban_model.calculations.allowed_capacities import (
    build_warnings as _build_allowed_warnings,
)
from urban_model.calculations.parking import compute_parking_breakdown
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

    # === Нормативный максимум КИТ ПЗЗ ===
    kit_norm_max = norms.resolve(
        "kit_limits", planning_doc="yes" if options.planning_doc else "no"
    )

    # === Жильё + ВПП ===
    gfa_v = housing.gfa_from_kit(kit, site.area_m2)
    apt_ratio = norms.resolve("building_params.apartments_to_gfa_ratio")

    # Если задан BuiltInArea — площадь ВПП вычитается из GFA до перевода в квартиры.
    # Иначе используется legacy `vpp_share`.
    if options.built_in is not None:
        bi_area = min(options.built_in.area_m2, gfa_v)  # safety: ВПП не больше GFA
        residential_gfa = gfa_v - bi_area
        apartments_area_v = residential_gfa * apt_ratio
        bi_vri = options.built_in.vri_code
    else:
        bi_area = 0.0
        residential_gfa = gfa_v * (1.0 - options.vpp_share)
        apartments_area_v = residential_gfa * apt_ratio
        bi_vri = None

    footprint_v = housing.housing_footprint(gfa_v, options.floors)

    # === Население ===
    hp = norms.resolve("housing_provision")
    pop_v = population.population(apartments_area_v, hp)
    hp_check = norms.resolve("housing_provision_check")
    pop_check_v = population.population(apartments_area_v, hp_check)
    density_v = population.density_chel_per_ga(pop_v, site.area_m2)
    density_max = norms.resolve("population_density_max")

    # Проверка плотности (формальная, по 20 м²/чел)
    density_check_v = population.density_chel_per_ga(pop_check_v, site.area_m2)
    density_status = Status.OK if density_check_v <= density_max else Status.ERROR

    warnings: list[str] = []
    if density_status == Status.ERROR:
        warnings.append(
            f"Плотность {density_check_v:.0f} чел/га > норматива {density_max} (по 20 м²/чел)"
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
        kg_buckets = kindergarten.split_into_objects(
            total_places=kg_accepted,
            spec_capacity=options.kindergarten.capacity_per_object,
            spec_count=options.kindergarten.num_objects,
            capacity_min=kg_cap_min_active,
            capacity_max=kg_cap_max,
            multiple=int(kg_round),
        )
        kg_plot_total, kg_bld_total = kindergarten.total_areas(kg_buckets, norms, kg_btype)
        # Предупреждения по вместимости ДОО (дифференцированы по типу и значению).
        # Пороги берутся из YAML (capacity_min/max — единый источник истины):
        #   c < kg_cap_min_builtin                          → меньше норм. наполняемости любого ДОО
        #   detached: kg_cap_min_builtin ≤ c < kg_cap_min_detached → рекомендовать built_in
        #   c > kg_cap_max                                  → превышен максимум
        for c in kg_buckets:
            if c < kg_cap_min_builtin:
                kg_status = Status.WARNING
                warnings.append(
                    f"ДОО: расчётная вместимость {c} мест меньше минимальной "
                    f"нормативной наполняемости ({kg_cap_min_builtin} мест"
                    + (f", {kg_src_min}" if kg_src_min else "") + "). "
                    "Запроектировать ДОО на такое число мест невозможно."
                )
            elif kg_btype == "detached" and c < kg_cap_min_detached:
                kg_status = Status.WARNING
                warnings.append(
                    f"ДОО: вместимость {c} мест меньше минимума отдельно стоящего "
                    f"ДОО ({kg_cap_min_detached} мест"
                    + (f", {kg_src_min}" if kg_src_min else "") + "). "
                    "Рекомендуется выбрать тип «встроенно-пристроенный ДОО» "
                    f"(минимум {kg_cap_min_builtin} мест)."
                )
            elif c > kg_cap_max:
                kg_status = Status.WARNING
                warnings.append(
                    f"ДОО: вместимость {c} мест превышает принятый максимум "
                    f"{kg_cap_max} мест"
                    + (f" ({kg_src_max})" if kg_src_max else "")
                    + " — рекомендуется разбить на большее число объектов."
                )
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
        density_status = Status.OK if density_check_v <= density_max else Status.ERROR

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
        sch_buckets = school.split_into_objects(
            total_places=sch_accepted,
            spec_capacity=options.school.capacity_per_object,
            spec_count=options.school.num_objects,
            capacity_min=sch_cap_min,
            capacity_max=sch_cap_max,
            multiple=int(sch_round),
        )
        sch_plot_total = sum(
            school.plot_area_with_extras(
                c, norms, options.school.has_pool, options.school.has_sport_core
            )
            for c in sch_buckets
        )
        sch_bld_total = sum(school.building_area_for_capacity(c, norms) for c in sch_buckets)
        # Проверка минимальной вместимости: расчёт даёт меньше норматива.
        # Для СПб нет «built_in» школ → минимум распространяется на любую СОШ.
        if sch_cap_min and sch_buckets and any(c < sch_cap_min for c in sch_buckets):
            sch_status = Status.WARNING
            offenders_lo = [c for c in sch_buckets if c < sch_cap_min]
            src_str = f" ({sch_cap_min_src})" if sch_cap_min_src else ""
            warnings.append(
                f"СОШ: расчётная вместимость {offenders_lo} < нормативного минимума "
                f"{sch_cap_min} мест{src_str} — стандартная отдельно стоящая СОШ невозможна, "
                "необходимо размещение начальной СОШ или стандартной СОШ "
                "вне границ территории."
            )
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

    # === Озеленение жилого ЗУ (нужно до housing_lot) ===
    green_ratio = norms.resolve("greening.housing_per_apartments")
    green_housing_v = greening.housing_greening_area(apartments_area_v, green_ratio)
    quarter_share = norms.resolve("greening.quarter_min_share")
    # Спорт-ЗУ исключаем из знаменателя 25% (внутри него своё озеленение).
    green_quarter_req_v = greening.quarter_greening_required(
        site.area_m2,
        kg_plot_in_balance + sch_plot_in_balance + sport_plot_in_balance,
        quarter_share,
    )

    # === ВПП — парковки и озеленение по своему ВРИ ===
    if bi_area > 0 and bi_vri:
        bi_per_place = norms.resolve("parking.vpp.m2_per_place", vri_code=bi_vri)
        bi_parking_places = math.ceil(bi_area / bi_per_place)
        bi_greening_ratio = norms.resolve("greening.vpp_per_floor_area")
        bi_greening_v = bi_area * bi_greening_ratio
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
            try:
                per_place = norms.resolve("parking.vpp.m2_per_place", vri_code=obj.vri_code)
                obj_parking = math.ceil(floor_area / per_place)
            except KeyError:
                # Если для ВРИ нет норматива парковок — считаем 0 + warning
                obj_parking = 0
                warnings.append(
                    f"Объект «{obj.name}» (ВРИ {obj.vri_code}): нет норматива "
                    "парковки — м/м не учтены."
                )
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

    # === Парковки соцобъектов (ДОО, СОШ) — v0.7.0 ===
    # Формула ПЗЗ СПб: ceil(работники/5) + ceil(учащиеся/100), не менее 2.
    # При only_demand или include_*=False объект «вне квартала» → не учитываем.
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

    park = compute_parking_breakdown(
        apartments_area_v, options.parking, norms,
        additional_places=(
            bi_parking_places + custom_total_parking_places + soc_park.total_places
        ),
    )

    # === Проезды ===
    drive_intra_share = (
        options.driveways_intra_share_override
        if options.driveways_intra_share_override is not None
        else norms.resolve("driveways.intra_quarter_share")
    )
    drive_lot_share = (
        options.driveways_lot_share_override
        if options.driveways_lot_share_override is not None
        else norms.resolve("driveways.housing_lot_share", floors=options.floors)
    )
    drive_intra_v = driveways.intra_quarter_area(site.area_m2, drive_intra_share)
    drive_lot_v = driveways.housing_lot_driveways_area(footprint_v, drive_lot_share)

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
    housing_lot_v = (
        footprint_v + drive_lot_v + green_housing_v + bi_greening_v + parking_open_in_lot
    )

    # === КИТ ПЗЗ = площадь квартир / ЗУ жилой застройки ===
    # Это и есть «правильный» КИТ по нормативам ПЗЗ СПб. ИМЕННО ОН сравнивается
    # с пороговыми значениями ЗНОП (1.6/1.8/2.0) и нормативным максимумом
    # КИТ_max (1.4 без ДПТ; 2.5 с ДПТ). Внутренний block_density (gfa/site)
    # — лишь техническая переменная бисекции.
    kit_developed = (apartments_area_v / housing_lot_v) if housing_lot_v > 0 else 0.0

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
    else:
        znop_pp = znop.znop_per_person(kit_developed, norms)
        znop_area_v = znop.znop_total_area(pop_v, kit_developed, norms)
        znop_source_label = norms.source_of("znop_per_person", kit=kit_developed)

    # === Баланс квартала ===
    # При include_*=False / only_demand компоненты в баланс не входят (см. выше).
    znop_in_balance = 0.0 if not options.include_znop else znop_area_v
    components = {
        "housing_lot": housing_lot_v,
        "kindergarten_plot": kg_plot_in_balance,
        "school_plot": sch_plot_in_balance,
        "sport_facilities": sport_plot_in_balance,
        "znop": znop_in_balance,
        "intra_quarter_driveways": drive_intra_in_balance,
        "parking_multilevel": parking_ml_footprint_in_balance,
    }
    if custom_total_plot > 0:
        components["custom_objects"] = custom_total_plot
    # Фактическое озеленение квартала: ЗНОП + озеленение жилого ЗУ + ВПП + кастомные.
    # ЗНОП учитывается, только если include_znop=True.
    # Норматив (25% площади квартала за вычетом ДОО/СОШ) — обязательная проверка.
    greening_actual_total = (
        znop_in_balance + green_housing_v + bi_greening_v + custom_total_greening
    )
    bal = balance.compute_balance(
        site.area_m2,
        components,
        greening_actual=greening_actual_total,
        greening_required=green_quarter_req_v,
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

    # === Сборка TEPResult ===
    # Статус КИТ: ERROR, если он превышает нормативный потолок (1.4 без ДПТ; 2.5 с ДПТ)
    kit_status = Status.ERROR if kit_developed > kit_norm_max + 1e-6 else Status.OK
    if kit_status == Status.ERROR:
        warnings.append(
            f"КИТ {kit_developed:.3f} превышает нормативный максимум "
            f"{kit_norm_max} (ПЗЗ СПб, ДПТ={'да' if options.planning_doc else 'нет'})"
        )

    return TEPResult(
        profile=norms.profile,
        kit=_F(
            kit_developed,
            status=kit_status,
            normative=kit_norm_max,
            formula=(
                f"S_квартир / S_ЗУ_жилой = {apartments_area_v:.0f} / {housing_lot_v:.0f}"
            ),
            source="ПЗЗ СПб",
        ),
        block_density=_F(
            kit,
            formula=f"GFA / S_квартала = {gfa_v:.0f} / {site.area_m2:.0f} (внутр. бисекция)",
        ),
        kit_normative_max=_F(
            kit_norm_max,
            source=norms.source_of(
                "kit_limits", planning_doc="yes" if options.planning_doc else "no"
            ),
            formula=f"ПЗЗ СПб, ДПТ={'да' if options.planning_doc else 'нет'}",
        ),
        gfa=_F(
            gfa_v,
            unit="m2",
            formula=f"плотность × S_квартала = {kit:.3f} × {site.area_m2}",
        ),
        apartments_area=_F(
            apartments_area_v,
            unit="m2",
            formula=(
                f"(GFA − ВПП={bi_area:.0f} − ДОО_встр={kg_bld_total:.0f}) × {apt_ratio}"
                if bi_area > 0 and _kg_builtin_adj
                else f"(GFA − ДОО_встр={kg_bld_total:.0f}) × {apt_ratio}"
                if _kg_builtin_adj
                else f"(GFA − ВПП={bi_area:.0f}) × {apt_ratio}"
                if bi_area > 0
                else f"GFA × (1 − vpp_share={options.vpp_share}) × {apt_ratio}"
            ),
            source=norms.source_of("building_params.apartments_to_gfa_ratio"),
        ),
        built_in_area=_F(
            bi_area,
            unit="m2",
            formula=(
                f"BuiltInArea(area_m2={bi_area:.0f}, vri={bi_vri})"
                if bi_area > 0 else "ВПП не задано"
            ),
        ),
        built_in_parking_places=_F(
            bi_parking_places,
            unit="м/м",
            formula=(
                f"ВПП {bi_area:.0f} м² / {bi_per_place} м²/м.м. (ВРИ {bi_vri}), вверх"
                if bi_area > 0 else "—"
            ),
            source=(
                norms.source_of("parking.vpp.m2_per_place", vri_code=bi_vri)
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
            density_v,
            unit="чел/га",
            normative=density_max,
            status=density_status,
            formula="население / (S_квартала / 10000)",
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
        # ── Парковки соцобъектов (v0.7.0) ────────────────────────────────
        social_parking_total=_F(
            soc_park.total_places,
            unit="м/м",
            formula=(
                f"ДОО: {soc_park.kindergarten_details} + "
                f"СОШ: {soc_park.school_details}"
                if soc_park.total_places > 0
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
            formula=f"(S_квартала − S_ДОО − S_СОШ) × {quarter_share}",
            source=norms.source_of("greening.quarter_min_share"),
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
        # --- Проезды ---
        driveways_intra_quarter_area=_F(
            drive_intra_v, unit="m2", formula=f"S_квартала × {drive_intra_share}"
        ),
        driveways_housing_lot_area=_F(
            drive_lot_v, unit="m2", formula=f"S_застройки × {drive_lot_share}"
        ),
        housing_lot_area=_F(
            housing_lot_v,
            unit="m2",
            formula="застройка + проезды + озеленение + открытые парковки",
        ),
        housing_footprint=_F(
            footprint_v, unit="m2", formula=f"GFA / этажность = {gfa_v:.0f} / {options.floors}"
        ),
        balance=bal,
        built_in_vri_code=bi_vri,
        warnings=warnings,
    )
