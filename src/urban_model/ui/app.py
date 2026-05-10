"""Streamlit-приложение: обратный расчёт ТЭП.

Запуск:  uv run streamlit run src/urban_model/ui/app.py
"""

from __future__ import annotations

import streamlit as st

from urban_model.ui.inputs import render_sidebar
from urban_model.ui.optimizer import render_optimizer_tab
from urban_model.ui.output import (
    render_actions,
    render_comparison_tab,
    render_details,
    render_header,
    render_kpi,
)
from urban_model.ui.state import (
    auto_scenario_name,
    get_norms,
    init_session,
    run_calculation,
)

# ---------------------------------------------------------------------------
# Конфигурация страницы
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Модель застройки — обратный расчёт ТЭП",
    page_icon="🏙️",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("🏙️ Модель застройки территории")
st.caption(
    "Обратный расчёт КИТ по площади квартала.  "
    "Профиль нормативов: **Санкт-Петербург** (СП 42.13330.2016 + НГП + ПЗЗ + РМД)."
)

init_session()
norms = get_norms("spb")


# ---------------------------------------------------------------------------
# Sidebar — параметры
# ---------------------------------------------------------------------------

inputs = render_sidebar()


# ---------------------------------------------------------------------------
# Главная область — две вкладки: Расчёт + Сравнение
# ---------------------------------------------------------------------------

tab_calc, tab_optimize, tab_compare = st.tabs([
    "📊 Расчёт",
    "🧬 Оптимизация",
    f"🔀 Сравнение ({len(st.session_state.scenarios)})",
])

with tab_calc:
    try:
        result = run_calculation(
            site=inputs.site,
            options=inputs.options,
            norms=norms,
            mode=inputs.mode,
            target_surplus_m2=inputs.target_surplus_m2,
            verify_kit_value=inputs.verify_kit_value,
            vpp_auto_one_floor=inputs.vpp_auto_one_floor,
        )
    except Exception as e:
        st.error(f"Ошибка расчёта: {e}")
        with st.expander("Подробно (traceback)"):
            import traceback
            st.code(traceback.format_exc())
        st.stop()

    render_header(result)
    render_kpi(result)
    render_details(result)
    render_actions(
        result,
        default_name=auto_scenario_name(
            inputs.site,
            inputs.options,
            inputs.mode,
            target_surplus_m2=inputs.target_surplus_m2,
            verify_kit_value=inputs.verify_kit_value,
        ),
    )

with tab_optimize:
    render_optimizer_tab(
        site=inputs.site,
        base_options=inputs.options,
        norms=norms,
    )

with tab_compare:
    render_comparison_tab()


# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------

st.markdown("---")
st.caption(
    "Источник истины: `info/ТЗ_обратный_расчет_ТЭП.docx`.  "
    "Нормативы: `configs/spb.yaml` (parent: `russia.yaml`).  "
    "Все цифры в YAML — никаких magic numbers в коде."
)
