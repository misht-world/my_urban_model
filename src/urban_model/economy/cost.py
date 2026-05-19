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

    m2_open = float(norms.resolve("economy.parking_areas.surface_m2_per_space"))
    m2_ml = float(norms.resolve("economy.parking_areas.multilevel_m2_per_space"))
    m2_ug = float(norms.resolve("economy.parking_areas.underground_m2_per_space"))

    pct_networks = float(norms.resolve("economy.overhead.pct_networks"))
    pct_landscape = float(norms.resolve("economy.overhead.pct_landscaping"))
    pct_design = float(norms.resolve("economy.overhead.pct_design"))
    pct_cont = float(norms.resolve("economy.overhead.pct_contingency"))

    # --- Жильё ---
    gfa_v = (tep.gfa.value or 0.0)
    bi_area = (tep.built_in_area.value or 0.0)
    # GFA жилья = общая GFA − площадь ВПП (если есть)
    residential_gfa = max(0.0, gfa_v - bi_area)
    c_base_res = _resolve_residential_base(int(options.floors), norms)
    cost_residential = residential_gfa * c_base_res

    # --- ВПП ---
    cost_vpp = bi_area * c_vpp

    # --- ДОО / СОШ ---
    kg_bld = (tep.kindergarten_building_area.value or 0.0)
    sch_bld = (tep.school_building_area.value or 0.0)
    cost_kg = kg_bld * c_kg
    cost_sch = sch_bld * c_sch

    # --- Парковки ---
    n_open = int(tep.parking_open_places.value or 0)
    n_ml = int(tep.parking_multilevel_places.value or 0)
    n_ug = int(tep.parking_underground_places.value or 0)

    cost_open = n_open * m2_open * c_surface
    cost_ml = n_ml * m2_ml * c_ml
    # Подземные — учёт прогрессии уровней. Если уровней не задано — 1 уровень.
    ug_levels = getattr(options.parking, "underground_levels", None) or 1
    cost_ug = _cost_underground_parking(n_ug, int(ug_levels), m2_ug, c_ug, norms)

    # --- Подытоги ---
    shell_total = (
        cost_residential + cost_vpp + cost_kg + cost_sch
        + cost_open + cost_ml + cost_ug
    )
    networks = shell_total * pct_networks
    landscaping = shell_total * pct_landscape
    design = shell_total * pct_design
    contingency = (shell_total + networks + landscaping + design) * pct_cont

    # --- Фиксированные затраты ---
    # Поля earth/connection/demolition пока без UI — берём 0.
    fixed = (
        getattr(options, "land_cost", 0.0) or 0.0
    ) + (
        getattr(options, "connection_costs", 0.0) or 0.0
    ) + (
        getattr(options, "demolition_costs", 0.0) or 0.0
    )

    total = shell_total + networks + landscaping + design + contingency + fixed

    return CostBreakdown(
        residential=cost_residential,
        vpp=cost_vpp,
        kindergarten=cost_kg,
        school=cost_sch,
        parking_open=cost_open,
        parking_multilevel=cost_ml,
        parking_underground=cost_ug,
        shell_total=shell_total,
        networks=networks,
        landscaping=landscaping,
        design=design,
        contingency=contingency,
        fixed=fixed,
        total=total,
    )
