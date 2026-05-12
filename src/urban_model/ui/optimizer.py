"""Streamlit-вкладка «Оптимизация» — Galapagos-аналог на Optuna.

Принцип:
  - Пользователь выбирает галочками, какие параметры варьировать
  - Задаёт диапазоны
  - Нажимает «Запустить» — внизу появляется таблица топ-N сценариев
  - Любой можно «Добавить в сравнение» одной кнопкой
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from urban_model.models import CalculationOptions, Site
from urban_model.normatives import Normatives
from urban_model.optimize import SearchSpace, optimize_max_apartments
from urban_model.optimize.runner import OptimizationReport
from urban_model.ui.formatting import fmt_int, fmt_m2


# ---------------------------------------------------------------------------
# Форма «что варьировать»
# ---------------------------------------------------------------------------

def _render_search_space_form(base_options: CalculationOptions) -> SearchSpace:
    st.markdown(
        "Отметьте параметры, которые **подбирать автоматически**. "
        "Остальные берутся из текущих настроек слева."
    )

    c1, c2 = st.columns(2)

    # --- Этажность ---
    with c1:
        vary_floors = st.checkbox(
            "🏠 Этажность",
            value=True,
            key="opt_vary_floors",
        )
        floors_range = None
        if vary_floors:
            lo, hi = st.slider(
                "Диапазон этажности",
                min_value=4,
                max_value=30,
                value=(8, 25),
                key="opt_floors_range",
            )
            floors_range = (int(lo), int(hi))

    # --- Парковки ---
    PARK_MODE_OPTIM_LABELS = {
        "Минимум открытых, остальное подземные": "min_open",
        "Все парковки открытые наземные": "all_open",
        "Задать доли вручную (custom)": "custom",
    }
    with c2:
        vary_parking = st.checkbox(
            "🅿️ Режим парковок",
            value=False,
            key="opt_vary_parking_mode",
            help=(
                "Если отмечено, оптимизатор перебирает разные режимы размещения "
                "парковок и выбирает тот, где площадь квартир максимальна. "
                "Иначе используется режим из настроек слева."
            ),
        )
        parking_modes = None
        if vary_parking:
            picked_labels = st.multiselect(
                "Какие режимы перебирать",
                list(PARK_MODE_OPTIM_LABELS.keys()),
                default=[
                    "Минимум открытых, остальное подземные",
                    "Все парковки открытые наземные",
                ],
                key="opt_parking_modes_labels",
                help="Можно выбрать любое подмножество. Custom активирует слайдеры долей ниже.",
            )
            parking_modes = [PARK_MODE_OPTIM_LABELS[lbl] for lbl in picked_labels] or None

    # --- Парковки: подробности custom ---
    parking_open_range = None
    parking_ml_range = None
    multilevel_levels_range = None
    if vary_parking and parking_modes and "custom" in parking_modes:
        with st.expander("⚙️ Детали custom-парковок", expanded=True):
            st.caption(
                "При custom-режиме оптимизатор подбирает доли каждого типа "
                "из заданных диапазонов. Подземные = остаток до 100%."
            )
            open_lo, open_hi = st.slider(
                "Диапазон доли открытых наземных, %",
                0, 100, (10, 50),
                key="opt_parking_open",
                help="Открытые м/м занимают площадь квартала пропорционально количеству.",
            )
            parking_open_range = (open_lo / 100, open_hi / 100)

            ml_lo, ml_hi = st.slider(
                "Диапазон доли многоуровневых наземных, %",
                0, 100, (0, 40),
                key="opt_parking_ml",
                help="Многоуровневые компактнее открытых: пятно делится на число этажей.",
            )
            parking_ml_range = (ml_lo / 100, ml_hi / 100)

            ll_lo, ll_hi = st.slider(
                "Диапазон этажности многоуровневого паркинга",
                1, 6, (1, 4),
                key="opt_ml_levels",
                help="Чем выше — тем компактнее пятно, но дороже строительство.",
            )
            multilevel_levels_range = (int(ll_lo), int(ll_hi))

    # --- Соцобъекты ---
    c3, c4 = st.columns(2)
    with c3:
        vary_kg = st.checkbox(
            "🎒 Кол-во ДОО",
            value=False,
            key="opt_vary_kg",
            disabled=not base_options.include_kindergarten,
        )
        kg_range = None
        if vary_kg and base_options.include_kindergarten:
            lo, hi = st.slider(
                "Диапазон кол-ва ДОО",
                min_value=1,
                max_value=10,
                value=(1, 4),
                key="opt_kg_range",
            )
            kg_range = (int(lo), int(hi))

    with c4:
        vary_school = st.checkbox(
            "🏫 Кол-во СОШ",
            value=False,
            key="opt_vary_school",
            disabled=not base_options.include_school,
        )
        school_range = None
        if vary_school and base_options.include_school:
            lo, hi = st.slider(
                "Диапазон кол-ва СОШ",
                min_value=1,
                max_value=5,
                value=(1, 2),
                key="opt_school_range",
            )
            school_range = (int(lo), int(hi))

    # --- ВПП ---
    try_built_in = st.checkbox(
        "🏪 Пробовать с ВПП и без",
        value=False,
        key="opt_try_vpp",
        help="Optuna будет сравнивать варианты с ВПП и без него (используется ВПП из настроек слева как шаблон).",
    )
    built_in_vri_codes = ["4.4"]
    if try_built_in:
        built_in_vri_codes = st.multiselect(
            "ВРИ-коды ВПП для перебора",
            ["2.6", "3.3", "3.6", "4.4", "4.6"],
            default=["4.4"],
            key="opt_vri_codes",
        )
        if not built_in_vri_codes:
            built_in_vri_codes = ["4.4"]

    return SearchSpace(
        floors_range=floors_range,
        parking_modes=parking_modes,
        parking_open_share_range=parking_open_range,
        parking_multilevel_share_range=parking_ml_range,
        multilevel_levels_range=multilevel_levels_range,
        kg_num_objects_range=kg_range,
        school_num_objects_range=school_range,
        try_built_in=try_built_in,
        built_in_vri_codes=built_in_vri_codes,
    )


# ---------------------------------------------------------------------------
# Таблица результатов
# ---------------------------------------------------------------------------

def _report_to_dataframe(report: OptimizationReport) -> pd.DataFrame:
    PARK_MODE_RU = {
        "min_open": "минимум открытых",
        "all_open": "все открытые",
        "custom": "вручную",
    }
    rows = []
    for r in report.top_n:
        row = {
            "#": r.rank,
            "Площадь квартир, м²": int(r.apartments_area),
            "КИТ": round(r.kit, 3),
            "Население, чел.": int(r.tep.population.value or 0),
        }
        # Параметры — каждый своим столбцом
        for k, v in r.params.items():
            label = {
                "floors": "Этажность",
                "parking_mode": "Режим парковок",
                "parking_open_share": "% открытых",
                "parking_ml_share": "% многоуровневых",
                "parking_ug_share": "% подземных",
                "multilevel_levels": "Этажей МП",
                "kg_num_objects": "ДОО, шт",
                "school_num_objects": "СОШ, шт",
                "use_vpp": "ВПП",
                "vpp_vri": "ВРИ ВПП",
            }.get(k, k)
            # Преобразование значений для удобочитаемости
            if k == "parking_mode":
                v = PARK_MODE_RU.get(v, v)
            elif k.startswith("parking_") and k.endswith("_share"):
                v = f"{v * 100:.0f}%"
            elif k == "use_vpp":
                v = "да" if v else "нет"
            row[label] = v
        row["Резерв, м²"] = int(r.tep.balance.surplus)
        rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Главная функция вкладки
# ---------------------------------------------------------------------------

def render_optimizer_tab(
    site: Site,
    base_options: CalculationOptions,
    norms: Normatives,
) -> None:
    st.markdown("## 🧬 Оптимизация — поиск лучших ТЭП")
    st.caption(
        "Аналог Galapagos из Grasshopper. Optuna перебирает значения "
        "выбранных параметров и находит комбинацию, при которой "
        "**площадь квартир максимальна** при выполнении всех ограничений."
    )

    # ------------------------------------------------------------------
    # Форма «что варьировать»
    # ------------------------------------------------------------------
    with st.expander("⚙️ Что варьировать", expanded=True):
        space = _render_search_space_form(base_options)

    # ------------------------------------------------------------------
    # Параметры запуска
    # ------------------------------------------------------------------
    c1, c2, c3 = st.columns([2, 1, 1])
    n_trials = c1.slider(
        "Число испытаний (trials)",
        min_value=10,
        max_value=300,
        value=50,
        step=10,
        key="opt_n_trials",
        help="Больше = точнее, но дольше. ~100 trials/сек на типовом квартале.",
    )
    top_n = c2.number_input(
        "Топ N",
        min_value=3,
        max_value=30,
        value=10,
        key="opt_top_n",
    )
    seed = c3.number_input(
        "Seed",
        min_value=0,
        max_value=99999,
        value=42,
        key="opt_seed",
    )

    if space.is_empty():
        st.info("Отметьте хотя бы один параметр для перебора.")
        return

    # ------------------------------------------------------------------
    # Кнопка запуска
    # ------------------------------------------------------------------
    if st.button("🚀 Запустить оптимизацию", type="primary", use_container_width=True):
        progress = st.progress(0.0, text="Запускаем оптимизацию...")

        def cb(current: int, total: int, best: float) -> None:
            pct = current / total
            progress.progress(
                pct,
                text=f"Trial {current}/{total} · лучшая площадь квартир: {best:,.0f} м²".replace(",", " "),
            )

        with st.spinner("Optuna перебирает варианты..."):
            report = optimize_max_apartments(
                site=site,
                base_options=base_options,
                norms=norms,
                space=space,
                n_trials=int(n_trials),
                top_n=int(top_n),
                seed=int(seed),
                progress_callback=cb,
            )
        progress.empty()
        st.session_state["optimization_report"] = report

    # ------------------------------------------------------------------
    # Показ последнего отчёта
    # ------------------------------------------------------------------
    report: OptimizationReport | None = st.session_state.get("optimization_report")
    if report is None:
        return

    st.markdown("---")

    # Предупреждения о пространстве поиска (например, ВПП-ловушка)
    for w in report.warnings:
        st.warning(f"⚠️ {w}")

    # Сводка
    c1, c2, c3 = st.columns(3)
    if report.best:
        c1.metric("Лучшая площадь квартир", fmt_m2(report.best.apartments_area))
        c1.caption(f"Trial #{report.best.rank}")
    base_apt = report.base_apartments_area
    if base_apt:
        delta_abs = (report.best.apartments_area - base_apt) if report.best else 0
        delta_rel = delta_abs / base_apt * 100 if base_apt else 0
        c2.metric(
            "Базовый вариант",
            fmt_m2(base_apt),
            delta=f"{delta_abs:+,.0f} м² ({delta_rel:+.1f}%)".replace(",", " "),
        )
    c3.metric("Допустимых испытаний", f"{report.n_trials_feasible} / {report.n_trials_total}")
    if report.n_trials_exception > 0:
        c3.caption(f"⚠️ {report.n_trials_exception} испытаний упали с ошибкой")

    # Подробности об ошибках, если были
    if report.exceptions:
        with st.expander(f"🔥 Ошибки в испытаниях ({report.n_trials_exception})", expanded=False):
            for line in report.exceptions:
                st.code(line, language=None)

    # Таблица топ-N
    if not report.top_n:
        st.error(
            "❌ Ни одно испытание не дало feasible-результата. "
            "Расширьте диапазоны или ослабьте ограничения."
        )
        return

    st.markdown("### Топ сценариев")
    df = _report_to_dataframe(report)
    st.dataframe(df, hide_index=True, use_container_width=True)

    # ------------------------------------------------------------------
    # Предпросмотр выбранного сценария
    # ------------------------------------------------------------------
    st.markdown("### Предпросмотр сценария")
    preview_options = [f"#{r.rank}" for r in report.top_n]
    selected = st.selectbox(
        "Выберите сценарий для просмотра",
        preview_options,
        index=0,
        key="opt_preview_rank",
    )
    selected_idx = preview_options.index(selected)
    preview = report.top_n[selected_idx]

    # Sub-KPI блок для выбранного сценария
    pc1, pc2, pc3, pc4 = st.columns(4)
    pc1.metric("КИТ ПЗЗ", f"{preview.kit:.3f}")
    pc2.metric("Население", fmt_int(preview.tep.population.value))
    pc3.metric("Площадь квартир", fmt_m2(preview.apartments_area))
    pc4.metric("Резерв баланса", fmt_m2(preview.tep.balance.surplus))

    # Параметры trial
    if preview.params:
        st.caption("**Параметры:** " + ", ".join(
            f"{k} = {v}" for k, v in preview.params.items()
        ))

    # Краткая таблица соцобъекты/парковки/ЗНОП
    tep = preview.tep
    pc5, pc6, pc7, pc8 = st.columns(4)
    kg_total = int(tep.kindergarten_places_accepted.value or 0)
    pc5.metric("ДОО, мест", kg_total if kg_total else "—")
    sch_total = int(tep.school_places_accepted.value or 0)
    pc6.metric("СОШ, мест", sch_total if sch_total else "—")
    pc7.metric(
        "Парковки, м/м",
        int(tep.parking_required_places.value or 0) or "—",
    )
    pc8.metric(
        "ЗНОП, м²",
        f"{int(tep.znop_area.value or 0):,}".replace(",", " ")
        if tep.znop_area.value else "—",
    )

    # Полный summary под expander
    with st.expander("Полные параметры и баланс сценария", expanded=False):
        st.code(tep.summary(), language=None)

    # ------------------------------------------------------------------
    # Добавить в сравнение
    # ------------------------------------------------------------------
    add1, add2 = st.columns([1, 1])
    with add1:
        if st.button(
            f"➕ Добавить #{preview.rank} в сравнение",
            type="primary", use_container_width=True,
        ):
            params_summary = ", ".join(f"{k}={v}" for k, v in preview.params.items())
            name = f"opt#{preview.rank} ({params_summary})"
            st.session_state.scenarios.append((name, preview.tep))
            st.toast(f"Добавлен сценарий #{preview.rank}", icon="✅")
    with add2:
        if st.button("➕ Добавить ВСЕ топ-N", use_container_width=True):
            for r in report.top_n:
                params_summary = ", ".join(f"{k}={v}" for k, v in r.params.items())
                name = f"opt#{r.rank} ({params_summary})"
                st.session_state.scenarios.append((name, r.tep))
            st.toast(f"Добавлено {len(report.top_n)} сценариев", icon="✅")
