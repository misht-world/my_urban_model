"""Streamlit-вкладка «Оптимизация» (v0.9.0 — редизайн).

Идея: вместо 20 ручек + Optuna 2000 trials — показываем
  1) snapshot базы из вкладки «Расчёт»;
  2) 3 готовых рекомендации (Optuna по 400 trials, выборка 3 лучших);
  3) 3 карточки one-factor (детерминированные сканы парковки/ЗНОП/этажности);
  4) старый UI Optuna — в свёрнутом expander «Продвинутый режим».

См. план v0.9.0 в `~/.claude/plans/parsed-kindling-wombat.md`.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from urban_model import solve_max_kit
from urban_model.models import CalculationOptions, Site
from urban_model.models.result import TEPResult
from urban_model.normatives import Normatives
from urban_model.optimize import SearchSpace, optimize_max_apartments
from urban_model.optimize.pareto import (
    ParetoBundle,
    Recommendation,
    generate_pareto_recommendations,
)
from urban_model.optimize.runner import OptimizationReport
from urban_model.optimize.scans import (
    ScanResult,
    scan_floors,
    scan_parking_multilevel_share,
    scan_parking_underground_share,
    scan_znop_steps,
)
from urban_model.ui.formatting import fmt_int, fmt_m2


# ---------------------------------------------------------------------------
# Старая Optuna (для «Продвинутого режима»)
# ---------------------------------------------------------------------------

_DEFAULT_TRIALS = 2000
_DEFAULT_TOP_N = 10
_DEFAULT_SEED = 42


# ---------------------------------------------------------------------------
# Секция 1: snapshot «База»
# ---------------------------------------------------------------------------

def _get_base_tep(
    site: Site, base_options: CalculationOptions, norms: Normatives,
) -> tuple[TEPResult, bool]:
    """Возвращает (base_tep, synced_with_calc_tab).

    Если на вкладке Расчёт уже посчитан результат и параметры совпадают —
    используем его. Иначе считаем сами и помечаем баннером «не синхронно».
    """
    last_result = st.session_state.get("last_calc_result")
    last_opts = st.session_state.get("last_calc_options")
    last_site_area = st.session_state.get("last_calc_site_area")
    if (
        last_result is not None
        and last_opts is not None
        and last_site_area is not None
        and last_opts.model_dump_json() == base_options.model_dump_json()
        and abs(last_site_area - site.area_m2) < 1e-3
    ):
        return last_result, True
    # Fallback: считаем сами
    return solve_max_kit(site, base_options, norms), False


def _render_base_snapshot(
    base_tep: TEPResult, base_options: CalculationOptions, synced: bool,
) -> None:
    """5 метрик базы + indicator синхронизации."""
    with st.container(border=True):
        if synced:
            st.markdown("##### 📋 База (с вкладки «Расчёт»)")
        else:
            st.markdown("##### 📋 База (рассчитана здесь)")
            st.caption(
                "Параметры на вкладке «Параметры» не совпадают с последним "
                "результатом на «Расчёте». Откройте «Расчёт» для синхронизации."
            )

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("КИТ ПЗЗ", f"{base_tep.kit.value:.3f}")
        c2.metric("Площадь квартир", fmt_m2(base_tep.apartments_area.value))
        if base_tep.economy is not None:
            c3.metric("Прибыль, у.е.", f"{int(base_tep.economy.profit):,}".replace(",", " "))
        else:
            c3.metric("Прибыль, у.е.", "—")
        c4.metric("Этажность", str(base_options.floors))
        ug = float(base_options.parking.underground_share)
        c5.metric("Подземн. парк.", f"{ug*100:.0f}%")


# ---------------------------------------------------------------------------
# Секция 2: Топ-3 рекомендации
# ---------------------------------------------------------------------------

def _render_recommendations_section(
    site: Site, base_options: CalculationOptions, norms: Normatives, base_tep: TEPResult,
) -> None:
    st.markdown("### 🎯 Топ-3 рекомендации")
    st.caption(
        "Optuna в широком диапазоне параметров находит 3 лучших сценария по "
        "разным критериям. Дельты — относительно базы выше."
    )

    # Ключ для определения «устарел ли bundle»: hash от base_options + site_area
    bundle_key = (
        base_options.model_dump_json()
        + f"|site={site.area_m2}"
    )
    cached_bundle: ParetoBundle | None = st.session_state.get("pareto_bundle")
    cached_key: str | None = st.session_state.get("pareto_bundle_key")
    is_stale = cached_bundle is None or cached_key != bundle_key

    col_btn, col_msg = st.columns([1, 2])
    with col_btn:
        clicked = st.button(
            "🎯 Подобрать сценарии",
            type="primary",
            use_container_width=True,
            help="Optuna 400 испытаний, около 10-15 секунд.",
        )
    with col_msg:
        if cached_bundle is not None and not is_stale:
            st.caption(
                f"✅ Рекомендации актуальны "
                f"({cached_bundle.n_trials_feasible}/{cached_bundle.n_trials_total} feasible)."
            )
        elif cached_bundle is not None and is_stale:
            st.caption("⚠️ Параметры изменились — пересчитайте рекомендации.")
        else:
            st.caption("Нажмите кнопку, чтобы запустить подбор.")

    if clicked:
        with st.spinner("Optuna ищет лучшие сценарии (~12 сек)..."):
            bundle = generate_pareto_recommendations(
                site=site, base_options=base_options, norms=norms,
                base_tep=base_tep, n_trials=400, seed=42,
            )
        st.session_state["pareto_bundle"] = bundle
        st.session_state["pareto_bundle_key"] = bundle_key
        cached_bundle = bundle

    if cached_bundle is None or not cached_bundle.recommendations:
        return

    cols = st.columns(len(cached_bundle.recommendations))
    for i, rec in enumerate(cached_bundle.recommendations):
        with cols[i]:
            _render_recommendation_card(rec, i)


def _render_recommendation_card(rec: Recommendation, idx: int) -> None:
    """Одна карточка рекомендации."""
    with st.container(border=True):
        st.markdown(f"#### {rec.label}")
        st.caption(rec.rationale)

        d = rec.delta_vs_base
        # Δ площади
        st.metric(
            "Площадь квартир",
            fmt_m2(rec.tep.apartments_area.value),
            delta=f"{d.d_apt_abs:+,.0f} м² ({d.d_apt_pct:+.1f}%)".replace(",", " "),
        )
        # Δ прибыли
        if d.d_profit_abs is not None and rec.tep.economy is not None:
            st.metric(
                "Прибыль, у.е.",
                f"{int(rec.tep.economy.profit):,}".replace(",", " "),
                delta=f"{d.d_profit_abs:+,.0f} ({d.d_profit_pct:+.1f}%)".replace(",", " "),
            )
        # КИТ
        st.metric("КИТ ПЗЗ", f"{rec.tep.kit.value:.3f}",
                  delta=f"{d.d_kit_abs:+.3f}")

        # Список изменений
        if d.key_changes:
            st.markdown("**Что изменено:**")
            for c in d.key_changes:
                st.caption(f"• {c}")
        else:
            st.caption("Параметры почти совпадают с базой.")

        if st.button("➕ В сравнение", key=f"add_rec_{idx}", use_container_width=True):
            st.session_state.scenarios.append((f"opt:{rec.label}", rec.tep))
            st.toast(f"Добавлено: {rec.label}", icon="✅")


# ---------------------------------------------------------------------------
# Секция 3: One-factor карточки (Парковки / ЗНОП / Этажность)
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def _cached_scan_parking(_norms_key: str, opts_json: str, site_area: float):
    """Кэш по полному JSON опций + площади квартала. _norms_key — для инвалидации
    при смене профиля (через `load_normatives.cache_clear`)."""
    norms = _get_norms_resolver()
    opts = CalculationOptions.model_validate_json(opts_json)
    site = Site(area_m2=site_area)
    return scan_parking_underground_share(site, opts, norms)


@st.cache_data(show_spinner=False)
def _cached_scan_parking_ml(_norms_key: str, opts_json: str, site_area: float):
    norms = _get_norms_resolver()
    opts = CalculationOptions.model_validate_json(opts_json)
    site = Site(area_m2=site_area)
    return scan_parking_multilevel_share(site, opts, norms)


@st.cache_data(show_spinner=False)
def _cached_scan_znop(_norms_key: str, opts_json: str, site_area: float):
    norms = _get_norms_resolver()
    opts = CalculationOptions.model_validate_json(opts_json)
    site = Site(area_m2=site_area)
    return scan_znop_steps(site, opts, norms)


@st.cache_data(show_spinner=False)
def _cached_scan_floors(_norms_key: str, opts_json: str, site_area: float):
    norms = _get_norms_resolver()
    opts = CalculationOptions.model_validate_json(opts_json)
    site = Site(area_m2=site_area)
    return scan_floors(site, opts, norms)


def _get_norms_resolver():
    """Кэшированная загрузка нормативов (профиль spb)."""
    from urban_model.normatives import load_normatives
    return load_normatives("spb")


def _scan_to_dataframe(scan: ScanResult) -> pd.DataFrame:
    """ScanResult → DataFrame для altair-графика."""
    rows = []
    for p in scan.points:
        rows.append({
            "x": p.x_value,
            "x_label": p.x_label,
            "apt": p.apartments_area,
            "profit": p.profit if p.profit is not None else 0.0,
            "kit": p.kit,
            "feasible": p.feasible,
            "is_base": p.is_base,
            "is_recommended": p.is_recommended,
        })
    return pd.DataFrame(rows)


def _render_scan_chart(scan: ScanResult) -> None:
    """Altair-график одного скана."""
    import altair as alt

    df = _scan_to_dataframe(scan)
    if df.empty:
        st.info("Нет точек для отображения.")
        return

    base_chart = alt.Chart(df).encode(
        x=alt.X("x:Q", title=scan.x_axis_label),
        y=alt.Y("apt:Q", title="Площадь квартир, м²"),
        tooltip=[
            alt.Tooltip("x_label:N", title=scan.x_axis_label),
            alt.Tooltip("apt:Q", title="Площадь квартир", format=",.0f"),
            alt.Tooltip("profit:Q", title="Прибыль, у.е.", format=",.0f"),
            alt.Tooltip("kit:Q", title="КИТ", format=".3f"),
            alt.Tooltip("feasible:N", title="Допустимо"),
        ],
    )
    line = base_chart.mark_line(color="#1565C0", point=True)
    base_dot = (
        base_chart.transform_filter("datum.is_base")
        .mark_point(color="#D32F2F", size=200, filled=True)
    )
    rec_dot = (
        base_chart.transform_filter("datum.is_recommended")
        .mark_point(color="#2E7D32", size=300, shape="star", filled=True)
    )
    chart = (line + base_dot + rec_dot).properties(height=240)
    st.altair_chart(chart, use_container_width=True)


def _render_scan_summary(scan: ScanResult) -> None:
    """Текстовое резюме скана + кнопка «в сравнение»."""
    base = scan.base_point
    rec = scan.recommended_point
    if base is None or rec is None:
        st.info("Не удалось вычислить базовую или рекомендованную точку.")
        return

    # Дельта по apt
    d_apt = rec.apartments_area - base.apartments_area
    d_apt_pct = (d_apt / base.apartments_area * 100.0) if base.apartments_area > 1e-9 else 0.0

    # Дельта по profit
    d_profit_str = "—"
    if rec.profit is not None and base.profit is not None and abs(base.profit) > 1e-9:
        d_profit_pct = (rec.profit - base.profit) / abs(base.profit) * 100.0
        d_profit_str = f"{rec.profit - base.profit:+,.0f} ({d_profit_pct:+.1f}%)".replace(",", " ")

    st.markdown(f"**База:** {base.x_label}")
    st.markdown(f"**Рекомендация:** {rec.x_label}")
    st.markdown("---")
    st.markdown(f"**Δ площадь квартир:** {d_apt:+,.0f} м² ({d_apt_pct:+.1f}%)".replace(",", " "))
    if d_profit_str != "—":
        st.markdown(f"**Δ прибыль:** {d_profit_str} у.е.")

    if rec.is_recommended and not rec.is_base:
        if st.button(
            "➕ Лучший вариант в сравнение",
            key=f"add_scan_{scan.factor}",
            use_container_width=True,
        ):
            st.session_state.scenarios.append(
                (f"scan:{scan.factor}={rec.x_label}", rec.tep)
            )
            st.toast(f"Добавлен лучший вариант скана «{scan.title}»", icon="✅")


def _render_scan_card(scan: ScanResult) -> None:
    """Одна карточка one-factor: график слева + резюме справа."""
    col_chart, col_text = st.columns([3, 2])
    with col_chart:
        _render_scan_chart(scan)
    with col_text:
        _render_scan_summary(scan)


def _render_what_to_improve_section(
    site: Site, base_options: CalculationOptions, norms: Normatives,
) -> None:
    """3 expander'а с one-factor сканами."""
    st.markdown("### 🔬 Что улучшить — пофакторный анализ")
    st.caption(
        "Каждая карточка варьирует ОДИН параметр при остальных зафиксированных. "
        "Красная точка — текущее значение базы, зелёная звезда — рекомендуемое."
    )

    opts_json = base_options.model_dump_json()
    norms_key = "spb"  # пока один профиль; при смене сменится через cache_clear

    with st.expander("🅿 Парковки: доля подземных", expanded=True):
        try:
            scan = _cached_scan_parking(norms_key, opts_json, site.area_m2)
            _render_scan_card(scan)
        except Exception as e:
            st.error(f"Ошибка скана парковок: {e}")

    with st.expander("🏗 Парковки: доля многоуровневых", expanded=False):
        try:
            scan = _cached_scan_parking_ml(norms_key, opts_json, site.area_m2)
            _render_scan_card(scan)
        except Exception as e:
            st.error(f"Ошибка скана многоуровневых: {e}")

    with st.expander("🌳 ЗНОП: норматив м²/чел", expanded=False):
        try:
            scan = _cached_scan_znop(norms_key, opts_json, site.area_m2)
            _render_scan_card(scan)
        except Exception as e:
            st.error(f"Ошибка скана ЗНОП: {e}")

    with st.expander("🏢 Этажность", expanded=False):
        try:
            scan = _cached_scan_floors(norms_key, opts_json, site.area_m2)
            _render_scan_card(scan)
        except Exception as e:
            st.error(f"Ошибка скана этажности: {e}")


# ---------------------------------------------------------------------------
# Секция 4: «Продвинутый режим (полный перебор)»
# Старый UI с 20 ручками, спрятан в expander
# ---------------------------------------------------------------------------

def _render_advanced_optuna_mode(
    site: Site, base_options: CalculationOptions, norms: Normatives,
) -> None:
    """Старая логика v0.8.x — полная форма SearchSpace + таблица топ-10.

    Не вырезаем — нужна для нестандартных диапазонов и экспериментов.
    """
    st.caption(
        "Полный перебор Optuna с произвольными диапазонами. Используйте для "
        "экспериментов; для типовых задач достаточно «Топ-3 рекомендации»."
    )
    space = _render_search_space_form(base_options)
    if space.is_empty():
        st.info("⬅ Отметьте хотя бы один параметр для перебора.")
        return

    if st.button("🚀 Запустить полный перебор", type="primary", use_container_width=True):
        progress = st.progress(0.0, text="Запускаем оптимизацию...")

        def cb(current: int, total: int, best: float) -> None:
            progress.progress(
                current / total,
                text=f"Trial {current}/{total} · лучшая площадь: {best:,.0f} м²".replace(",", " "),
            )

        with st.spinner(f"Optuna перебирает варианты (до {_DEFAULT_TRIALS} испытаний)..."):
            report = optimize_max_apartments(
                site=site, base_options=base_options, norms=norms, space=space,
                n_trials=_DEFAULT_TRIALS, top_n=_DEFAULT_TOP_N,
                seed=_DEFAULT_SEED, progress_callback=cb,
            )
        progress.empty()
        st.session_state["optimization_report"] = report

    report: OptimizationReport | None = st.session_state.get("optimization_report")
    if report is None:
        return

    st.markdown("---")
    for w in report.warnings:
        st.warning(f"⚠️ {w}")

    c1, c2, c3 = st.columns(3)
    if report.best:
        c1.metric("Лучшая площадь квартир", fmt_m2(report.best.apartments_area))
        c1.caption(f"Trial #{report.best.rank}")
    base_apt = report.base_apartments_area
    if base_apt:
        delta_abs = (report.best.apartments_area - base_apt) if report.best else 0
        delta_rel = delta_abs / base_apt * 100 if base_apt else 0
        c2.metric(
            "Базовый вариант", fmt_m2(base_apt),
            delta=f"{delta_abs:+,.0f} м² ({delta_rel:+.1f}%)".replace(",", " "),
        )
    c3.metric("Допустимых испытаний", f"{report.n_trials_feasible} / {report.n_trials_total}")

    if not report.top_n:
        st.error("❌ Ни одно испытание не дало feasible-результата.")
        return

    st.markdown("### Топ сценариев")
    df = _report_to_dataframe(report)
    st.dataframe(df, hide_index=True, use_container_width=True)

    st.markdown("### Предпросмотр сценария")
    preview_options = [f"#{r.rank}" for r in report.top_n]
    selected = st.selectbox(
        "Сценарий", preview_options, index=0, key="opt_preview_rank",
    )
    preview = report.top_n[preview_options.index(selected)]

    pc1, pc2, pc3, pc4 = st.columns(4)
    pc1.metric("КИТ ПЗЗ", f"{preview.kit:.3f}")
    pc2.metric("Население", fmt_int(preview.tep.population.value))
    pc3.metric("Площадь квартир", fmt_m2(preview.apartments_area))
    pc4.metric("Резерв баланса", fmt_m2(preview.tep.balance.surplus))

    if preview.params:
        st.caption("**Параметры:** " + ", ".join(
            f"{k} = {v}" for k, v in preview.params.items()
        ))

    from urban_model.ui.output import render_details
    render_details(preview.tep)

    if st.button(f"➕ Добавить #{preview.rank} в сравнение", use_container_width=True):
        params_summary = ", ".join(f"{k}={v}" for k, v in preview.params.items())
        name = f"opt#{preview.rank} ({params_summary})"
        st.session_state.scenarios.append((name, preview.tep))
        st.toast(f"Добавлен сценарий #{preview.rank}", icon="✅")


# ---------------------------------------------------------------------------
# Старая форма SearchSpace + сборка DataFrame top_n (для «Продвинутого»).
# Перенесено как есть из v0.8.x.
# ---------------------------------------------------------------------------

def _render_search_space_form(base_options: CalculationOptions) -> SearchSpace:
    """Двухколоночная форма «что варьировать»."""
    col_left, col_right = st.columns([1, 2], gap="medium")

    with col_left:
        with st.container(border=True):
            st.markdown("##### Цель оптимизации")
            obj_label = st.radio(
                "Что максимизировать",
                ["Максимум площади квартир (ТЭП)", "Максимум прибыли (у.е.)"],
                index=0, key="opt_objective_label",
            )
            optimizer_objective = (
                "profit" if obj_label.startswith("Максимум прибыли") else "apartments_area"
            )
            strict_social = st.checkbox(
                "Строгий отсев соцобъектов", value=False, key="opt_strict_social",
            )

        with st.container(border=True):
            st.markdown("##### Варьируемые параметры")
            vary_floors = st.checkbox("🏠 Этажность", value=True, key="opt_vary_floors")
            vary_parking = st.checkbox("🅿️ Парковки", value=True, key="opt_vary_parking_mode")
            vary_kg = st.checkbox(
                "🎒 Кол-во ДОО", value=True, key="opt_vary_kg",
                disabled=not base_options.include_kindergarten,
            )
            vary_school = st.checkbox(
                "🏫 Кол-во СОШ", value=True, key="opt_vary_school",
                disabled=not base_options.include_school,
            )
            try_built_in = st.checkbox("🏪 ВПП (с/без)", value=True, key="opt_try_vpp")
            vary_znop = st.checkbox("🌳 ЗНОП", value=True, key="opt_vary_znop")

    floors_range = None
    parking_modes = None
    parking_open_range = None
    parking_ml_range = None
    parking_ug_range = None
    multilevel_levels_range = None
    underground_levels_range = None
    kg_range = None
    school_range = None
    vpp_modes: list[str] | None = None
    znop_choices: list[float] | None = None

    with col_right:
        if vary_floors:
            with st.container(border=True):
                st.markdown("##### 🏠 Этажность")
                lo, hi = st.slider(
                    "Диапазон", min_value=4, max_value=30, value=(8, 25),
                    key="opt_floors_range",
                )
                floors_range = (int(lo), int(hi))

        if vary_parking:
            with st.container(border=True):
                st.markdown("##### 🅿️ Парковки")
                use_min_open = st.checkbox("Минимум открытых, остальное подземные", value=True, key="opt_park_min_open")
                use_all_open = st.checkbox("Все открытые наземные", value=True, key="opt_park_all_open")
                use_custom = st.checkbox("Вручную (custom)", value=False, key="opt_park_custom")
                modes = []
                if use_min_open: modes.append("min_open")
                if use_all_open: modes.append("all_open")
                if use_custom: modes.append("custom")
                parking_modes = modes if modes else None
                if use_custom:
                    open_lo, open_hi = st.slider("Открытых, %", 0.0, 100.0, (12.5, 50.0), step=0.5, key="opt_parking_open")
                    parking_open_range = (open_lo / 100, open_hi / 100)
                    ml_lo, ml_hi = st.slider("Многоуровневых, %", 0.0, 100.0, (0.0, 40.0), step=0.5, key="opt_parking_ml")
                    parking_ml_range = (ml_lo / 100, ml_hi / 100)
                    ug_lo, ug_hi = st.slider("Подземных, %", 0.0, 100.0, (0.0, 100.0), step=0.5, key="opt_parking_ug")
                    parking_ug_range = (ug_lo / 100, ug_hi / 100)
                    ll_lo, ll_hi = st.slider("Этажность МП", 1, 9, (1, 4), key="opt_ml_levels")
                    multilevel_levels_range = (int(ll_lo), int(ll_hi))
                    ug_levels_lo, ug_levels_hi = st.slider("Этажность подземки", 1, 5, (1, 2), key="opt_ug_levels")
                    underground_levels_range = (int(ug_levels_lo), int(ug_levels_hi))

        if vary_kg and base_options.include_kindergarten:
            with st.container(border=True):
                st.markdown("##### 🎒 Кол-во ДОО")
                lo, hi = st.slider("Диапазон", 1, 10, (1, 4), key="opt_kg_range")
                kg_range = (int(lo), int(hi))

        if vary_school and base_options.include_school:
            with st.container(border=True):
                st.markdown("##### 🏫 Кол-во СОШ")
                lo, hi = st.slider("Диапазон", 1, 5, (1, 2), key="opt_school_range")
                school_range = (int(lo), int(hi))

        if try_built_in:
            with st.container(border=True):
                st.markdown("##### 🏪 ВПП — варианты")
                use_min_only = st.checkbox("Минимум (все 5 ВРИ)", value=False, key="opt_vpp_min_only")
                use_min_plus = st.checkbox("Минимум + допы 4.4/4.6", value=False, key="opt_vpp_min_plus")
                use_custom_only = st.checkbox("Только 4.4/4.6 вручную", value=False, key="opt_vpp_custom_only")
                use_full_floor = st.checkbox("Весь 1 этаж", value=True, key="opt_vpp_full_floor")
                use_half_floor = st.checkbox("50% 1 этажа", value=True, key="opt_vpp_half_floor")
                mode_list = []
                if use_min_only: mode_list.append("min_only")
                if use_min_plus: mode_list.append("min_plus")
                if use_custom_only: mode_list.append("custom_only")
                if use_full_floor: mode_list.append("full_floor")
                if use_half_floor: mode_list.append("half_floor")
                vpp_modes = mode_list if mode_list else None

        if vary_znop:
            with st.container(border=True):
                st.markdown("##### 🌳 ЗНОП")
                cc1, cc2, cc3, cc4 = st.columns(4)
                use_z0 = cc1.checkbox("0", value=True, key="opt_znop_0")
                use_z3 = cc2.checkbox("3", value=True, key="opt_znop_3")
                use_z4 = cc3.checkbox("4", value=True, key="opt_znop_4")
                use_z6 = cc4.checkbox("6", value=True, key="opt_znop_6")
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
        built_in_vri_codes=["4.4"],
        vpp_modes=vpp_modes,
        znop_per_person_choices=znop_choices,
        objective=optimizer_objective,
        strict_social_validation=strict_social,
    )


def _report_to_dataframe(report: OptimizationReport) -> pd.DataFrame:
    """Сводная таблица топ-N для «Продвинутого режима»."""
    import re as _re

    PARK_MODE_RU = {"min_open": "минимум открытых", "all_open": "все открытые", "custom": "вручную"}
    VPP_MODE_RU = {
        "off": "без ВПП", "min_only": "минимум", "min_plus": "минимум + допы",
        "custom_only": "только 4.4/4.6", "full_floor": "весь 1 этаж", "half_floor": "50% 1 этажа",
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
        row["ДОО, шт"] = _count_buckets(r.tep.kindergarten_places_accepted.formula)
        row["ДОО, мест"] = int(r.tep.kindergarten_places_accepted.value or 0)
        row["СОШ, шт"] = _count_buckets(r.tep.school_places_accepted.formula)
        row["СОШ, мест"] = int(r.tep.school_places_accepted.value or 0)
        for k, v in r.params.items():
            if k in params_skip:
                continue
            label = {
                "floors": "Этажность", "parking_mode": "Режим парковок",
                "parking_open_share": "% открытых", "parking_ml_share": "% многоуровневых",
                "parking_ug_share": "% подземных", "multilevel_levels": "Этажей МП",
                "underground_levels": "Уровней подземки", "use_vpp": "ВПП",
                "vpp_vri": "ВРИ ВПП", "vpp_mode": "ВПП режим",
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
    site: Site, base_options: CalculationOptions, norms: Normatives,
) -> None:
    st.markdown("## 🧬 Оптимизация")
    st.caption(
        "Сверху — рекомендации Optuna по площади/прибыли/балансу. "
        "Ниже — пофакторный анализ (что даст изменение ОДНОГО параметра). "
        "Полный перебор — в свёрнутом блоке внизу."
    )

    # 1. База
    base_tep, synced = _get_base_tep(site, base_options, norms)
    _render_base_snapshot(base_tep, base_options, synced)

    st.markdown("")  # отступ

    # 2. Рекомендации (по кнопке)
    _render_recommendations_section(site, base_options, norms, base_tep)

    st.markdown("")

    # 3. One-factor сканы (автоматически)
    _render_what_to_improve_section(site, base_options, norms)

    st.markdown("---")

    # 4. Старый Optuna UI — спрятан
    with st.expander("⚙ Продвинутый режим (полный перебор)", expanded=False):
        _render_advanced_optuna_mode(site, base_options, norms)
