"""Streamlit-вкладка «Оптимизация» (v0.7.3 — 2-колоночный редизайн).

Принцип:
  • Слева — галочки «что варьировать». Справа — настройки для каждой
    включённой галочки (плитки, как на «Параметрах»).
  • Число испытаний (trials) скрыто — задано с запасом (≤ 1 минуты).
  • Нажимает «Запустить» → таблица топ-N сценариев + предпросмотр.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from urban_model.models import CalculationOptions, Site
from urban_model.normatives import Normatives
from urban_model.optimize import SearchSpace, optimize_max_apartments
from urban_model.optimize.runner import OptimizationReport
from urban_model.ui.formatting import fmt_int, fmt_m2

# Число испытаний фиксировано на ~1 минуту работы на типовом квартале
# (Optuna ~100 trials/сек). Если расчёт упирается в скорость — снизим.
_DEFAULT_TRIALS = 2000
_DEFAULT_TOP_N = 10
_DEFAULT_SEED = 42


# ---------------------------------------------------------------------------
# Форма «что варьировать»
# ---------------------------------------------------------------------------

def _render_search_space_form(base_options: CalculationOptions) -> SearchSpace:
    """Двухколоночная форма: слева чекбоксы, справа плитки настроек."""
    col_left, col_right = st.columns([1, 2], gap="medium")

    # ── ЛЕВАЯ КОЛОНКА: цель + чекбоксы «что варьировать» ──────────────────
    with col_left:
        with st.container(border=True):
            st.markdown("##### Цель оптимизации")
            obj_label = st.radio(
                "Что максимизировать",
                [
                    "Максимум площади квартир (ТЭП)",
                    "Максимум прибыли (у.е.)",
                ],
                index=0, key="opt_objective_label",
                help=(
                    "Прибыль учитывает себестоимость соцобъектов, парковок и пр. "
                    "Иногда выгоднее меньше квартир, но без подземного паркинга."
                ),
            )
            optimizer_objective = (
                "profit"
                if obj_label.startswith("Максимум прибыли")
                else "apartments_area"
            )

        with st.container(border=True):
            st.markdown("##### Варьируемые параметры")
            vary_floors = st.checkbox(
                "🏠 Этажность", value=True, key="opt_vary_floors",
            )
            vary_parking = st.checkbox(
                "🅿️ Парковки", value=True, key="opt_vary_parking_mode",
            )
            vary_kg = st.checkbox(
                "🎒 Кол-во ДОО", value=True, key="opt_vary_kg",
                disabled=not base_options.include_kindergarten,
            )
            vary_school = st.checkbox(
                "🏫 Кол-во СОШ", value=True, key="opt_vary_school",
                disabled=not base_options.include_school,
            )
            try_built_in = st.checkbox(
                "🏪 ВПП (с/без)", value=True, key="opt_try_vpp",
            )
            vary_znop = st.checkbox(
                "🌳 ЗНОП (ступени по КИТ)", value=True, key="opt_vary_znop",
            )

    # ── ПРАВАЯ КОЛОНКА: плитки настроек включённых параметров ──────
    floors_range = None
    parking_modes = None
    parking_open_range = None
    parking_ml_range = None
    parking_ug_range = None
    multilevel_levels_range = None
    underground_levels_range = None
    kg_range = None
    school_range = None
    built_in_vri_codes = ["4.4"]
    vpp_modes: list[str] | None = None
    znop_choices: list[float] | None = None

    with col_right:
        # Этажность
        if vary_floors:
            with st.container(border=True):
                st.markdown("##### 🏠 Этажность")
                sld_col, _ = st.columns([2, 1])
                with sld_col:
                    lo, hi = st.slider(
                        "Диапазон этажности",
                        min_value=4, max_value=30, value=(8, 25),
                        key="opt_floors_range",
                    )
                    floors_range = (int(lo), int(hi))

        # Парковки — галочками по режимам (вместо selectbox)
        if vary_parking:
            with st.container(border=True):
                st.markdown("##### 🅿️ Парковки")
                st.caption("Выберите режимы для перебора — отмечайте галочками.")
                use_min_open = st.checkbox(
                    "Минимум открытых, остальное подземные",
                    value=True, key="opt_park_min_open",
                )
                use_all_open = st.checkbox(
                    "Все парковки открытые наземные",
                    value=True, key="opt_park_all_open",
                )
                use_custom = st.checkbox(
                    "Вручную (custom — с настройкой долей ниже)",
                    value=False, key="opt_park_custom",
                )
                modes = []
                if use_min_open: modes.append("min_open")
                if use_all_open: modes.append("all_open")
                if use_custom: modes.append("custom")
                parking_modes = modes if modes else None

                if use_custom:
                    st.markdown("**Диапазоны для custom-режима**")
                    # v0.8.0: step=0.5% чтобы можно было задать ровно 12.5%
                    # Слайдеры — в 2/3 ширины контейнера для компактности
                    sld_col, _spacer = st.columns([2, 1])
                    with sld_col:
                        open_lo, open_hi = st.slider(
                            "Доля открытых наземных, %",
                            0.0, 100.0, (12.5, 50.0), step=0.5,
                            key="opt_parking_open",
                        )
                        parking_open_range = (open_lo / 100, open_hi / 100)

                        ml_lo, ml_hi = st.slider(
                            "Доля многоуровневых наземных, %",
                            0.0, 100.0, (0.0, 40.0), step=0.5,
                            key="opt_parking_ml",
                        )
                        parking_ml_range = (ml_lo / 100, ml_hi / 100)

                        ug_lo, ug_hi = st.slider(
                            "Доля подземных, %",
                            0.0, 100.0, (0.0, 100.0), step=0.5,
                            key="opt_parking_ug",
                            help=(
                                "Подземные считаются как остаток после открытых и "
                                "многоуровневых. Можно ограничить (например, "
                                "верхнюю границу = 0% для запрета подземных)."
                            ),
                        )
                        parking_ug_range = (ug_lo / 100, ug_hi / 100)

                        ll_lo, ll_hi = st.slider(
                            "Этажность многоуровневого паркинга",
                            1, 9, (1, 4),
                            key="opt_ml_levels",
                            help="Максимум 9 этажей (типовое для надземных паркингов).",
                        )
                        multilevel_levels_range = (int(ll_lo), int(ll_hi))

                        ug_levels_lo, ug_levels_hi = st.slider(
                            "Этажность подземного паркинга",
                            1, 5, (1, 2),
                            key="opt_ug_levels",
                            help="Максимум 5 уровней; каждый следующий дороже предыдущего.",
                        )
                        underground_levels_range = (int(ug_levels_lo), int(ug_levels_hi))

        # ДОО
        if vary_kg and base_options.include_kindergarten:
            with st.container(border=True):
                st.markdown("##### 🎒 Кол-во ДОО")
                sld_col, _ = st.columns([2, 1])
                with sld_col:
                    lo, hi = st.slider(
                        "Диапазон кол-ва ДОО",
                        min_value=1, max_value=10, value=(1, 4),
                        key="opt_kg_range",
                    )
                    kg_range = (int(lo), int(hi))

        # СОШ
        if vary_school and base_options.include_school:
            with st.container(border=True):
                st.markdown("##### 🏫 Кол-во СОШ")
                sld_col, _ = st.columns([2, 1])
                with sld_col:
                    lo, hi = st.slider(
                        "Диапазон кол-ва СОШ",
                        min_value=1, max_value=5, value=(1, 2),
                        key="opt_school_range",
                    )
                    school_range = (int(lo), int(hi))

        # ВПП — галочки по РЕЖИМАМ размещения (как на вкладке Параметры)
        if try_built_in:
            with st.container(border=True):
                st.markdown("##### 🏪 ВПП — варианты размещения")
                st.caption(
                    "Выберите режимы ВПП для перебора (те же, что на вкладке "
                    "«Параметры»). Площади ВРИ собираются автоматически."
                )
                use_min_only = st.checkbox(
                    "Минимум по нормативу (все 5 ВРИ)",
                    value=False, key="opt_vpp_min_only",
                )
                use_min_plus = st.checkbox(
                    "Минимум + дополнительные 4.4/4.6",
                    value=False, key="opt_vpp_min_plus",
                )
                use_custom_only = st.checkbox(
                    "Только 4.4 и/или 4.6 вручную",
                    value=False, key="opt_vpp_custom_only",
                )
                use_full_floor = st.checkbox(
                    "Весь 1 этаж",
                    value=True, key="opt_vpp_full_floor",
                )
                use_half_floor = st.checkbox(
                    "50% 1 этажа",
                    value=True, key="opt_vpp_half_floor",
                )
                mode_list = []
                if use_min_only: mode_list.append("min_only")
                if use_min_plus: mode_list.append("min_plus")
                if use_custom_only: mode_list.append("custom_only")
                if use_full_floor: mode_list.append("full_floor")
                if use_half_floor: mode_list.append("half_floor")
                vpp_modes = mode_list if mode_list else None

        # ЗНОП — нормативные ступени по КИТ
        if vary_znop:
            with st.container(border=True):
                st.markdown("##### 🌳 ЗНОП — значения для перебора")
                st.caption(
                    "Нормативные ступени ПЗЗ СПб по КИТ: 0 / 3 / 4 / 6 м²/чел. "
                    "Отметьте, какие значения перебирать."
                )
                cc1, cc2, cc3, cc4 = st.columns(4)
                use_z0 = cc1.checkbox("0 м²/чел", value=True, key="opt_znop_0")
                use_z3 = cc2.checkbox("3 м²/чел", value=True, key="opt_znop_3")
                use_z4 = cc3.checkbox("4 м²/чел", value=True, key="opt_znop_4")
                use_z6 = cc4.checkbox("6 м²/чел", value=True, key="opt_znop_6")
                choices = []
                if use_z0: choices.append(0.0)
                if use_z3: choices.append(3.0)
                if use_z4: choices.append(4.0)
                if use_z6: choices.append(6.0)
                znop_choices = choices if choices else None

    return SearchSpace(
        floors_range=floors_range,
        parking_modes=parking_modes,
        parking_open_share_range=parking_open_range,
        parking_multilevel_share_range=parking_ml_range,
        parking_underground_share_range=parking_ug_range,
        multilevel_levels_range=multilevel_levels_range,
        underground_levels_range=underground_levels_range,
        kg_num_objects_range=kg_range,
        school_num_objects_range=school_range,
        try_built_in=try_built_in,
        built_in_vri_codes=built_in_vri_codes,
        vpp_modes=vpp_modes,
        znop_per_person_choices=znop_choices,
        objective=optimizer_objective,
        strict_social_validation=True,  # v0.8.0: UI отсекает невалидные ДОО/СОШ
    )


# ---------------------------------------------------------------------------
# Таблица результатов
# ---------------------------------------------------------------------------

def _report_to_dataframe(report: OptimizationReport) -> pd.DataFrame:
    """Сводная таблица топ-N.

    v0.8.4: число ДОО/СОШ берётся из ФАКТИЧЕСКОГО результата расчёта
    (formula = «… → разбивка по объектам [c1, c2, ...]»), а не из
    params (там попытка Optuna kg_num_objects, которая может быть
    проигнорирована split_into_objects при отсутствии spec_capacity).
    """
    import re as _re

    PARK_MODE_RU = {
        "min_open": "минимум открытых",
        "all_open": "все открытые",
        "custom": "вручную",
    }
    VPP_MODE_RU = {
        "off": "без ВПП",
        "min_only": "минимум",
        "min_plus": "минимум + допы",
        "custom_only": "только 4.4/4.6",
        "full_floor": "весь 1 этаж",
        "half_floor": "50% 1 этажа",
    }

    def _count_buckets(formula: str | None) -> int:
        if not formula:
            return 0
        m = _re.search(r'\[([^\]]+)\]', formula)
        if not m:
            return 0
        try:
            return len([x for x in m.group(1).split(",") if x.strip()])
        except Exception:
            return 0

    # Колонки, которые из params НЕ выводим (числа объектов берём из tep)
    params_skip = {"kg_num_objects", "school_num_objects"}

    rows = []
    for r in report.top_n:
        row = {
            "#": r.rank,
            "Площадь квартир, м²": int(r.apartments_area),
            "КИТ": round(r.kit, 3),
            "Население, чел.": int(r.tep.population.value or 0),
        }
        if r.tep.economy is not None:
            row["Прибыль, у.е."] = int(r.tep.economy.profit)

        # Фактическое число объектов из formula (не из params!)
        row["ДОО, шт"] = _count_buckets(r.tep.kindergarten_places_accepted.formula)
        row["ДОО, мест"] = int(r.tep.kindergarten_places_accepted.value or 0)
        row["СОШ, шт"] = _count_buckets(r.tep.school_places_accepted.formula)
        row["СОШ, мест"] = int(r.tep.school_places_accepted.value or 0)

        # Остальные параметры — из sampled (исключая ДОО/СОШ количества)
        for k, v in r.params.items():
            if k in params_skip:
                continue
            label = {
                "floors": "Этажность",
                "parking_mode": "Режим парковок",
                "parking_open_share": "% открытых",
                "parking_ml_share": "% многоуровневых",
                "parking_ug_share": "% подземных",
                "multilevel_levels": "Этажей МП",
                "underground_levels": "Уровней подземки",
                "use_vpp": "ВПП",
                "vpp_vri": "ВРИ ВПП",
                "vpp_mode": "ВПП режим",
                "znop_per_person": "ЗНОП, м²/чел",
            }.get(k, k)
            if k == "parking_mode":
                v = PARK_MODE_RU.get(v, v)
            elif k == "vpp_mode":
                v = VPP_MODE_RU.get(v, v)
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
        "Optuna перебирает значения выбранных параметров и находит комбинацию, "
        "при которой **площадь квартир максимальна** при выполнении всех ограничений."
    )

    # ── Форма «что варьировать» (не сворачиваемая) ─────────────────
    space = _render_search_space_form(base_options)

    if space.is_empty():
        st.info("⬅ Отметьте хотя бы один параметр для перебора.")
        return

    st.markdown("---")

    # ── Кнопка запуска (trials/seed зафиксированы) ─────────────────
    if st.button("🚀 Запустить оптимизацию", type="primary", use_container_width=True):
        progress = st.progress(0.0, text="Запускаем оптимизацию...")

        def cb(current: int, total: int, best: float) -> None:
            pct = current / total
            progress.progress(
                pct,
                text=f"Trial {current}/{total} · лучшая площадь квартир: {best:,.0f} м²".replace(",", " "),
            )

        with st.spinner(f"Optuna перебирает варианты (до {_DEFAULT_TRIALS} испытаний)..."):
            report = optimize_max_apartments(
                site=site,
                base_options=base_options,
                norms=norms,
                space=space,
                n_trials=_DEFAULT_TRIALS,
                top_n=_DEFAULT_TOP_N,
                seed=_DEFAULT_SEED,
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

    # v0.8.3: те же раскрывающиеся секции, что и на вкладке «Расчёт»
    # (🏠 Жильё / 🎒 ДОО / 🏫 СОШ / 🏃 Спорт / 🌳 ЗНОП / 🅿️ Парковки /
    #  🛣 Проезды / ⚖️ Баланс / 💰 Экономика / 📋 Полный аудит).
    # Через прямой вызов render_details — никаких дублей кода.
    from urban_model.ui.output import render_details
    render_details(tep)

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
