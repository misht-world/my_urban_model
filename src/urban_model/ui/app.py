"""Streamlit-приложение: обратный расчёт ТЭП.

Запуск:  uv run streamlit run src/urban_model/ui/app.py

С v0.5.8 — деловой светлый дизайн: параметры на отдельной вкладке,
sidebar свёрнут по умолчанию.
"""

from __future__ import annotations

import streamlit as st

from urban_model.ui.inputs import render_params_tab
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
    page_title="Модель застройки территории",
    page_icon="🏙",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Глобальный CSS: крупнее вкладки + плотнее вертикальные интервалы.
# Версионный маркер /* v0.8.2 */ помогает отследить обновление CSS при
# отладке. Если ты видишь старый стиль — это значит браузер кэшировал
# страницу. Hard refresh (Ctrl+F5) сбрасывает кэш.
st.markdown("""
<style>
  /* v0.9.15 stylesheet marker — bump на каждом релизе, чтобы инвалидировать кэш */
  /* Уменьшаем верхний отступ всего блока */
  div.block-container {padding-top: 1.5rem; padding-bottom: 1rem;}
  /* Плотнее интервалы между виджетами */
  div[data-testid="stVerticalBlock"] {gap: 0.5rem;}
  /* Вкладки — более выразительные */
  div[data-baseweb="tab-list"] {
      gap: 0.25rem;
      border-bottom: 2px solid #CBD5E1;
  }
  button[data-baseweb="tab"] {
      font-size: 1.1rem !important;
      padding: 0.65rem 1.6rem !important;
      font-weight: 500;
      border-radius: 6px 6px 0 0 !important;
      border: 1px solid transparent !important;
      border-bottom: none !important;
      background: transparent;
      color: #475569;
  }
  button[data-baseweb="tab"]:hover {
      background: #F1F5F9 !important;
      color: #1E40AF !important;
  }
  button[data-baseweb="tab"][aria-selected="true"] {
      font-weight: 700 !important;
      font-size: 1.13rem !important;
      background: #EBF3FF !important;
      color: #1565C0 !important;
      border: 1px solid #BFDBFE !important;
      border-bottom: 2px solid #FFFFFF !important;
  }
  /* Цветовая дифференциация левой/правой колонок на «Параметрах»:
     • левая (вводные данные) — голубой акцент слева + светло-голубой фон
     • правая (настройки компонентов) — зелёный акцент слева + светло-зелёный фон
     Усилено !important + border-left для надёжной видимости. */
  /* v0.9.15: цветовая дифференциация ВСЕЙ колонки «Параметры»
     через `:has()`-селектор по невидимым CSS-маркерам, которые
     `inputs.py` вставляет в каждую колонку. Работает в современных
     браузерах (Chrome 105+, Firefox 121+, Safari 15.4+).
     Деловые приглушённые цвета, не яркие.

     Также сохранены fallback-селекторы по testid на случай отсутствия
     `:has` (старые браузеры).
  */
  /* Главный селектор через :has — окрашивает ВСЮ колонку */
  [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:has(.params-col-input),
  [data-testid="stHorizontalBlock"] > [data-testid="column"]:has(.params-col-input) {
      background-color: #EBF3FA !important;
      border: 1px solid #C9D9E8 !important;
      border-left: 3px solid #5285B3 !important;
      border-radius: 6px;
      padding: 14px !important;
  }
  [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:has(.params-col-settings),
  [data-testid="stHorizontalBlock"] > [data-testid="column"]:has(.params-col-settings) {
      background-color: #EDF5EE !important;
      border: 1px solid #C9DBCC !important;
      border-left: 3px solid #5B8C66 !important;
      border-radius: 6px;
      padding: 14px !important;
  }
  /* Fallback (legacy): окрашиваем по nth-position если :has не поддерживается */
  @supports not (selector(:has(*))) {
      [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-of-type(1) {
          background-color: #EBF3FA !important;
          border-left: 3px solid #5285B3 !important;
          border-radius: 6px;
          padding: 14px !important;
      }
      [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-of-type(2) {
          background-color: #EDF5EE !important;
          border-left: 3px solid #5B8C66 !important;
          border-radius: 6px;
          padding: 14px !important;
      }
  }
  /* Слайдеры внутри border-блоков — ограничение ширины до ~65%.
     Перечисляем разные селекторы для надёжности. */
  [data-testid="stVerticalBlockBorderWrapper"] [data-testid="stSlider"],
  [data-testid="stVerticalBlockBorderWrapper"] div[data-baseweb="slider"] {
      max-width: 65% !important;
  }
  /* Чуть меньше пустоты у containers с border */
  div[data-testid="stVerticalBlockBorderWrapper"] {padding: 0.6rem 0.9rem;}

  /* v0.9.15: единый деловой стиль для ВСЕХ вкладок.
     Контейнеры st.container(border=True) получают тонкий приглушённый
     border и едва заметный фон. Subheader'ы становятся ненавязчивыми. */
  div[data-testid="stVerticalBlockBorderWrapper"] {
      border: 1px solid #DFE5EC !important;
      background-color: #FAFBFC;
      border-radius: 6px;
  }
  /* Однотипное оформление H5-заголовков внутри контейнеров — деловой look */
  div[data-testid="stVerticalBlockBorderWrapper"] h5 {
      color: #334155;
      font-size: 1.0rem;
      font-weight: 600;
      margin-top: 0.1rem;
      margin-bottom: 0.5rem;
      padding-bottom: 0.3rem;
      border-bottom: 1px solid #E5E9EF;
  }
</style>
""", unsafe_allow_html=True)

# Заголовок и краткая подпись
col_title, col_meta = st.columns([3, 1])
with col_title:
    st.title("Модель застройки территории")
    st.caption(
        "Обратный расчёт КИТ по площади квартала. "
        "Профиль нормативов — Санкт-Петербург."
    )
with col_meta:
    from urban_model import __version__
    st.markdown(
        f"<div style='text-align:right;padding-top:1.5rem;color:#6B7280;'>"
        f"<small>v{__version__}</small></div>",
        unsafe_allow_html=True,
    )

init_session()
norms = get_norms("spb")


# ---------------------------------------------------------------------------
# Sidebar — минимальный (можно развернуть для статуса/сброса)
# ---------------------------------------------------------------------------

with st.sidebar:
    st.markdown("### Управление")
    st.caption(
        "Параметры — на вкладке «Параметры». Здесь — служебные действия."
    )
    if st.button("Сбросить сравнение", use_container_width=True):
        st.session_state.scenarios = []
        st.toast("Сценарии очищены", icon="🗑")
    if st.button("Сбросить объекты", use_container_width=True):
        st.session_state.custom_objects = []
        st.toast("Объекты очищены", icon="📦")

    st.markdown("---")
    st.caption(
        f"Сценариев в сравнении: **{len(st.session_state.scenarios)}**  \n"
        f"Объектов: **{len(st.session_state.get('custom_objects', []))}**"
    )


# ---------------------------------------------------------------------------
# Вкладки
# ---------------------------------------------------------------------------

_n_scenarios = len(st.session_state.scenarios)

tab_params, tab_calc, tab_optimize, tab_compare = st.tabs([
    "Параметры",
    "Расчёт",
    "Оптимизация",
    f"Сравнение ({_n_scenarios})" if _n_scenarios else "Сравнение",
])

# --- Параметры (новая вкладка с полной формой) ---
with tab_params:
    inputs = render_params_tab()

# --- Расчёт (только результаты) ---
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
            vpp_request=inputs.vpp_request,
        )
    except Exception as e:
        st.error(f"Ошибка расчёта: {e}")
        with st.expander("Подробно (traceback)"):
            import traceback
            st.code(traceback.format_exc())
        st.stop()

    render_header(result)
    # v0.7.3: «Добавить в сравнение» теперь ВНУТРИ блока «Основные показатели»
    # — render_kpi с scenario_default_name делает actions inline.
    render_kpi(
        result,
        scenario_default_name=auto_scenario_name(
            inputs.site,
            inputs.options,
            inputs.mode,
            target_surplus_m2=inputs.target_surplus_m2,
            verify_kit_value=inputs.verify_kit_value,
        ),
    )
    render_details(result)

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
    "Источник истины: `info/ТЗ_обратный_расчет_ТЭП.docx`. "
    "Нормативы: `configs/spb.yaml` (parent: `russia.yaml`). "
    "Все цифры — в YAML, никаких magic numbers в коде."
)
