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
) -> TEPResult:
    """Унифицированный запуск расчёта по выбранному режиму.

    Если vpp_auto_one_floor=True и options.built_in задан с area_m2=0, делаем
    двухпроходный расчёт: первый проход — без ВПП, чтобы получить footprint;
    второй — с built_in.area_m2 = footprint первого этажа.
    """
    if vpp_auto_one_floor and options.built_in is not None:
        # 1-й проход: без ВПП
        opts_step1 = options.model_copy(deep=True)
        opts_step1.built_in = None
        r0 = solve_max_kit(site, opts_step1, norms)
        bi_area = r0.housing_footprint.value or 0.0
        # 2-й проход: с ВПП = footprint
        opts_step2 = options.model_copy(deep=True)
        opts_step2.built_in = BuiltInArea(
            area_m2=max(bi_area, 1.0),
            vri_code=options.built_in.vri_code,
            label="1 этаж жилого дома",
        )
        options = opts_step2

    if mode == "max_kit":
        return solve_max_kit(site, options, norms)
    elif mode == "with_reserve":
        return solve_max_kit_with_reserve(site, target_surplus_m2, options, norms)
    elif mode == "verify":
        return verify_kit(verify_kit_value, site, options, norms)
    else:
        raise ValueError(f"Неизвестный режим: {mode}")


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
