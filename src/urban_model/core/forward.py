"""Прямой расчёт ТЭП по заданному КИТ.

Чистая функция: на вход — Site, Options, Normatives и сам KIT.
На выход — TEPResult со структурированными полями (TEPField).

Эта функция — фундамент. `solve_max_kit` (inverse.py) вызывает её много раз.
"""

from __future__ import annotations

from urban_model.calculations import (
    balance,
    driveways,
    greening,
    housing,
    kindergarten,
    parking,
    population,
    school,
    znop,
)
from urban_model.calculations.parking import compute_parking_breakdown
from urban_model.models.options import CalculationOptions
from urban_model.models.result import Status, TEPField, TEPResult
from urban_model.models.site import Site
from urban_model.normatives import Normatives


def _F(value, **kw) -> TEPField:
    return TEPField(value=value, **kw)


def compute_tep_for_kit(
    kit: float,
    site: Site,
    options: CalculationOptions,
    norms: Normatives,
) -> TEPResult:
    # === Нормативный максимум КИТ ===
    kit_norm_max = norms.resolve(
        "kit_limits", planning_doc="yes" if options.planning_doc else "no"
    )

    # === Жильё ===
    gfa_v = housing.gfa_from_kit(kit, site.area_m2)
    apt_ratio = norms.resolve("building_params.apartments_to_gfa_ratio")
    apartments_area_v = housing.apartments_area(gfa_v, options.vpp_share, apt_ratio)
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
    if options.include_kindergarten and pop_v > 0:
        kg_per_1000 = norms.resolve("social_objects.kindergarten.places_per_1000")
        kg_round = norms.resolve("social_objects.kindergarten.rounding")
        kg_required_raw = kindergarten.required_places(pop_v, kg_per_1000)
        kg_accepted = kindergarten.round_places(kg_required_raw, kg_round)
        kg_btype = options.kindergarten.building_type
        kg_cap_max = norms.resolve(
            "social_objects.kindergarten.capacity_max", building_type=kg_btype
        )
        try:
            kg_cap_min = norms.resolve(
                "social_objects.kindergarten.capacity_min", building_type=kg_btype
            )
        except KeyError:
            kg_cap_min = None
        kg_buckets = kindergarten.split_into_objects(
            total_places=kg_accepted,
            spec_capacity=options.kindergarten.capacity_per_object,
            spec_count=options.kindergarten.num_objects,
            capacity_min=kg_cap_min,
            capacity_max=kg_cap_max,
        )
        kg_plot_total, kg_bld_total = kindergarten.total_areas(kg_buckets, norms)
    else:
        kg_required_raw = kg_accepted = 0
        kg_plot_total = kg_bld_total = 0.0
        kg_buckets = []

    # === СОШ ===
    sch_status = Status.OK
    if options.include_school and pop_v > 0:
        sch_per_1000 = norms.resolve("social_objects.school.places_per_1000")
        sch_round = norms.resolve("social_objects.school.rounding")
        sch_required_raw = school.required_places(pop_v, sch_per_1000)
        sch_accepted = school.round_places(sch_required_raw, sch_round)
        if options.school.num_objects and options.school.capacity_per_object:
            sch_buckets = [options.school.capacity_per_object] * options.school.num_objects
        else:
            sch_buckets = [sch_accepted] if sch_accepted > 0 else []
        sch_plot_total = sum(
            school.plot_area_with_extras(
                c, norms, options.school.has_pool, options.school.has_sport_core
            )
            for c in sch_buckets
        )
        sch_bld_total = sum(school.building_area_for_capacity(c, norms) for c in sch_buckets)
        # Проверка минимальной вместимости: расчёт даёт меньше норматива.
        # Для СПб нет «built_in» школ → минимум распространяется на любую СОШ.
        try:
            sch_cap_min = norms.resolve(
                "social_objects.school.capacity_min",
                building_type=options.school.building_type,
            )
        except (KeyError, TypeError):
            sch_cap_min = None
        if sch_cap_min and sch_buckets and any(c < sch_cap_min for c in sch_buckets):
            sch_status = Status.WARNING
            warnings.append(
                f"СОШ: расчётная вместимость {sch_buckets} < нормативного минимума "
                f"{sch_cap_min} мест — стандартная отдельно стоящая СОШ невозможна, "
                "нужна стоянка-спутник или ВПП-школа (учтётся в v0.2)."
            )
    else:
        sch_required_raw = sch_accepted = 0
        sch_plot_total = sch_bld_total = 0.0
        sch_buckets = []

    # === ЗНОП ===
    znop_pp = znop.znop_per_person(kit, norms)
    znop_area_v = znop.znop_total_area(pop_v, kit, norms)

    # === Озеленение жилого ЗУ ===
    green_ratio = norms.resolve("greening.housing_per_apartments")
    green_housing_v = greening.housing_greening_area(apartments_area_v, green_ratio)
    quarter_share = norms.resolve("greening.quarter_min_share")
    green_quarter_req_v = greening.quarter_greening_required(
        site.area_m2, kg_plot_total + sch_plot_total, quarter_share
    )

    # === Парковки (разбивка по типам) ===
    park = compute_parking_breakdown(apartments_area_v, options.parking, norms)

    # === Проезды ===
    drive_intra_share = norms.resolve("driveways.intra_quarter_share")
    drive_lot_share = norms.resolve("driveways.housing_lot_share")
    drive_intra_v = driveways.intra_quarter_area(site.area_m2, drive_intra_share)
    drive_lot_v = driveways.housing_lot_driveways_area(footprint_v, drive_lot_share)

    # === Площадь жилого ЗУ ===
    # ЗУ жилья = площадь застройки + проезды на ЗУ + озеленение жилого + открытые парковки
    housing_lot_v = footprint_v + drive_lot_v + green_housing_v + park.open_area

    # === Баланс квартала ===
    components = {
        "housing_lot": housing_lot_v,
        "kindergarten_plot": kg_plot_total,
        "school_plot": sch_plot_total,
        "znop": znop_area_v,
        "intra_quarter_driveways": drive_intra_v,
        "parking_multilevel": park.multilevel_footprint,
    }
    bal = balance.compute_balance(site.area_m2, components)

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
    return TEPResult(
        profile=norms.profile,
        kit=_F(kit, formula="вход в compute_tep_for_kit"),
        kit_normative_max=_F(
            kit_norm_max,
            source=norms.source_of(
                "kit_limits", planning_doc="yes" if options.planning_doc else "no"
            ),
            formula=f"ПЗЗ СПб, ППТ={'да' if options.planning_doc else 'нет'}",
        ),
        gfa=_F(gfa_v, unit="m2", formula=f"КИТ × S_квартала = {kit:.3f} × {site.area_m2}"),
        apartments_area=_F(
            apartments_area_v,
            unit="m2",
            formula=f"GFA × (1 − ВПП={options.vpp_share}) × {apt_ratio}",
            source=norms.source_of("building_params.apartments_to_gfa_ratio"),
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
            formula=f"население × {61 if options.include_kindergarten else 0} / 1000"
            if options.include_kindergarten
            else "ДОО отключены",
            source=norms.source_of("social_objects.kindergarten.places_per_1000")
            if options.include_kindergarten
            else None,
        ),
        kindergarten_places_accepted=_F(
            kg_accepted,
            unit="мест",
            normative=kg_required_raw if options.include_kindergarten else None,
            formula=f"вверх кратно {5} → разбивка по объектам {kg_buckets}"
            if options.include_kindergarten
            else None,
        ),
        kindergarten_plot_area=_F(
            kg_plot_total,
            unit="m2",
            formula="Σ piecewise(plot_per_place, capacity) по объектам ДОО",
        ),
        kindergarten_building_area=_F(
            kg_bld_total, unit="m2", formula="Σ piecewise(bld_per_place, capacity) по ДОО"
        ),
        school_places_required=_F(
            sch_required_raw,
            unit="мест",
            formula="население × 120 / 1000" if options.include_school else "СОШ отключены",
        ),
        school_places_accepted=_F(
            sch_accepted,
            unit="мест",
            normative=sch_required_raw if options.include_school else None,
            status=sch_status,
            formula=f"вверх кратно 10 → объекты {sch_buckets}"
            if options.include_school
            else None,
        ),
        school_plot_area=_F(
            sch_plot_total,
            unit="m2",
            formula=f"plot(capacity) + бассейн={options.school.has_pool} + ядро={options.school.has_sport_core}",
        ),
        school_building_area=_F(sch_bld_total, unit="m2"),
        znop_per_person=_F(
            znop_pp,
            unit="m2/чел",
            formula=f"piecewise(КИТ={kit:.3f})",
            source=norms.source_of("znop_per_person", kit=kit),
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
        warnings=warnings,
    )
