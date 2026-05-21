"""session_state-обёртки и вспомогательные расчётные хелперы."""

from __future__ import annotations

from typing import Any

import streamlit as st

from urban_model import solve_max_kit, solve_max_kit_with_reserve, verify_kit
from urban_model.models import CalculationOptions, Site
from urban_model.models.built_in import BuiltInArea
from urban_model.models.result import TEPResult
from urban_model.normatives import Normatives, load_normatives


# ---------------------------------------------------------------------------
# Загрузка нормативов с кэшированием
# ---------------------------------------------------------------------------

@st.cache_resource
def get_norms(profile: str = "spb") -> Normatives:
    return load_normatives(profile)


# ---------------------------------------------------------------------------
# Накопление сценариев для сравнения
# ---------------------------------------------------------------------------

def init_session() -> None:
    if "scenarios" not in st.session_state:
        st.session_state.scenarios = []  # list[tuple[str, TEPResult]]


def add_scenario(name: str, result: TEPResult) -> None:
    st.session_state.scenarios.append((name, result))


def remove_scenario(idx: int) -> None:
    if 0 <= idx < len(st.session_state.scenarios):
        st.session_state.scenarios.pop(idx)


def clear_scenarios() -> None:
    st.session_state.scenarios = []


# ---------------------------------------------------------------------------
# Расчёт с автоподбором ВПП = площадь 1 этажа (двухпроходный)
# ---------------------------------------------------------------------------

def run_calculation(
    site: Site,
    options: CalculationOptions,
    norms: Normatives,
    mode: str,
    target_surplus_m2: float = 0.0,
    verify_kit_value: float = 1.0,
    vpp_auto_one_floor: bool = False,
    vpp_request: Any = None,
) -> TEPResult:
    """Унифицированный запуск расчёта по выбранному режиму.

    Двухпроходные расчёты ВПП:
    - vpp_auto_one_floor=True + options.built_in: legacy single, ВПП = footprint.
    - vpp_request (VppRequest): новый механизм 5 вариантов (v0.7.1).
      Проход 1 → footprint + population → vpp.build_built_ins → проход 2.
    """
    from urban_model.calculations import vpp as _vpp

    # v0.9.2: запоминаем ОРИГИНАЛЬНЫЕ опции (до двухпроходного VPP-расчёта),
    # чтобы хэш на вкладке «Оптимизация» совпал с тем, что приходит из
    # inputs.options. Если этого не сделать, Оптимизатор будет считать
    # базу с РАЗНЫМИ опциями (без ВПП), что даёт ложное расхождение
    # площади квартир между Расчётом и Оптимизацией.
    options_for_hash = options.model_copy(deep=True)

    if vpp_auto_one_floor and options.built_in is not None:
        # Legacy: 1 ВПП = footprint
        opts_step1 = options.model_copy(deep=True)
        opts_step1.built_in = None
        r0 = solve_max_kit(site, opts_step1, norms)
        bi_area = r0.housing_footprint.value or 0.0
        opts_step2 = options.model_copy(deep=True)
        opts_step2.built_in = BuiltInArea(
            area_m2=max(bi_area, 1.0),
            vri_code=options.built_in.vri_code,
            label="1 этаж жилого дома",
        )
        options = opts_step2

    if vpp_request is not None:
        # v0.7.1: двухпроходный расчёт для списка ВПП.
        # Проход 1 — БЕЗ ВПП → получаем footprint и population.
        opts_step1 = options.model_copy(deep=True)
        opts_step1.built_in = None
        opts_step1.built_in_list = []
        r0 = solve_max_kit(site, opts_step1, norms)
        footprint = r0.housing_footprint.value or 0.0
        pop = r0.population.value or 0.0

        # Сохраняем для UI-превью на следующем рендере
        st.session_state["_last_population"] = pop

        # Собираем список ВПП по выбранному режиму
        build_res = _vpp.build_built_ins(
            mode=vpp_request.mode,
            population=pop,
            footprint=footprint,
            norms=norms,
            custom_4_4_m2=vpp_request.custom_4_4_m2,
            custom_4_6_m2=vpp_request.custom_4_6_m2,
        )

        # Проход 2 — с готовым списком ВПП
        opts_step2 = options.model_copy(deep=True)
        opts_step2.built_in = None
        opts_step2.built_in_list = build_res.built_ins
        options = opts_step2

    if mode == "max_kit":
        result = solve_max_kit(site, options, norms)
    elif mode == "with_reserve":
        result = solve_max_kit_with_reserve(site, target_surplus_m2, options, norms)
    elif mode == "verify":
        result = verify_kit(verify_kit_value, site, options, norms)
    else:
        raise ValueError(f"Неизвестный режим: {mode}")

    if result.parking_required_places.value is not None:
        st.session_state["_last_total_required"] = int(result.parking_required_places.value)
    # Сохраняем население для VPP-превью в UI
    if result.population.value is not None:
        st.session_state["_last_population"] = float(result.population.value)
    # v0.9.0/0.9.2: ТЭП-результат с вкладки Расчёт используется на вкладке
    # Оптимизация как «база» для сравнения. Сохраняем result (он уже учитывает
    # ВПП-постобработку) + ОРИГИНАЛЬНЫЕ опции (до vpp.build_built_ins), чтобы
    # хэш сошёлся с inputs.options в Оптимизаторе.
    st.session_state["last_calc_result"] = result
    st.session_state["last_calc_options"] = options_for_hash
    st.session_state["last_calc_site_area"] = float(site.area_m2)
    return result


# ---------------------------------------------------------------------------
# Имя сценария по умолчанию
# ---------------------------------------------------------------------------

def auto_scenario_name(site: Site, options: CalculationOptions, mode: str, **extras: Any) -> str:
    parts = [f"{int(site.area_m2/1000)}тыс м²", f"{options.floors}эт"]
    if options.planning_doc:
        parts.append("ПД")
    if options.built_in is not None:
        parts.append(f"ВПП {int(options.built_in.area_m2)}м²")
    if options.znop_per_person_override is not None:
        parts.append(f"ЗНОП={options.znop_per_person_override}")
    if mode == "with_reserve":
        parts.append(f"резерв≥{int(extras.get('target_surplus_m2', 0))}")
    if mode == "verify":
        parts.append(f"verify КИТ={extras.get('verify_kit_value', 0):.2f}")
    return " · ".join(parts)
