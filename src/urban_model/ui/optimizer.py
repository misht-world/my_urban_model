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
    ParetoConstraints,
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
    """База (snapshot вкладки Расчёт) в едином формате KPI (v0.9.6).

    Тот же `_render_kpi_block`, что и в карточках рекомендаций — это даёт
    возможность сравнить визуально по строкам.
    """
    with st.container(border=True):
        if synced:
            st.markdown("##### 📋 База (с вкладки «Расчёт»)")
        else:
            st.markdown("##### 📋 База (рассчитана здесь)")
            st.caption(
                "Параметры на вкладке «Параметры» не совпадают с последним "
                "результатом на «Расчёте». Откройте «Расчёт» для синхронизации."
            )
        _render_kpi_block(base_tep, base_options)


# ---------------------------------------------------------------------------
# Секция 2: Топ-3 рекомендации
# ---------------------------------------------------------------------------

def _render_pareto_constraints() -> ParetoConstraints:
    """Сворачиваемый блок с ограничениями подбора Парето (v0.9.3).

    Возвращает ParetoConstraints. Все значения по умолчанию = без ограничений
    (5..25 этажей, все типы парковок разрешены).
    """
    with st.expander("⚙ Настройки подбора (необязательно)", expanded=False):
        st.caption(
            "Здесь можно ограничить пространство перебора — например, задать "
            "узкий диапазон этажности или запретить подземные парковки."
        )

        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Этажность**")
            lo, hi = st.slider(
                "Диапазон", 4, 30, (5, 25),
                key="pareto_floors_range",
                help="Optuna будет рассматривать только этажность в этом диапазоне.",
            )
            floors_range = (int(lo), int(hi))

        with c2:
            st.markdown("**Разрешённые типы парковок**")
            allow_open = st.checkbox(
                "🅿 Открытые наземные", value=True, key="pareto_allow_open",
                help="Самые дешёвые, но требуют пятна на квартале (≥12.5% по нормативу).",
            )
            allow_multilevel = st.checkbox(
                "🏗 Многоуровневые наземные", value=True, key="pareto_allow_ml",
                help="Компактнее открытых, средняя себестоимость.",
            )
            allow_underground = st.checkbox(
                "🚇 Подземные", value=True, key="pareto_allow_ug",
                help=(
                    "Не занимают пятно квартала, но дороже всех. Можно исключить, "
                    "если по проекту или нормативам не разрешены."
                ),
            )
            restrict_combos = st.checkbox(
                "Реалистичные сочетания парковок",
                value=True, key="pareto_restrict_combos",
                help=(
                    "Включает два типологических фильтра: "
                    "(1) не сочетать МУ + подземные одновременно (>10% каждого) "
                    "— на рынке обычно строят либо МУ, либо подземку, не обе; "
                    "(2) не предлагать «символическое» количество мест: "
                    "МУ < 50 мест или подземка < 30 мест = меньше одной секции, "
                    "на практике не строится."
                ),
            )

        return ParetoConstraints(
            floors_range=floors_range,
            allow_open=allow_open,
            allow_multilevel=allow_multilevel,
            allow_underground=allow_underground,
            restrict_parking_combos=restrict_combos,
        )


def _render_recommendations_section(
    site: Site, base_options: CalculationOptions, norms: Normatives, base_tep: TEPResult,
) -> None:
    st.markdown("### 🎯 Топ-3 рекомендации")
    st.caption(
        "Optuna в широком диапазоне параметров находит 3 лучших сценария по "
        "разным критериям. Дельты — относительно базы выше."
    )

    constraints = _render_pareto_constraints()

    # Ключ для определения «устарел ли bundle»: hash от base_options + site_area
    # + constraints (если поменялся диапазон/разрешения — пересчитываем)
    bundle_key = (
        base_options.model_dump_json()
        + f"|site={site.area_m2}"
        + f"|floors={constraints.floors_range}"
        + f"|park={constraints.allow_open}{constraints.allow_multilevel}{constraints.allow_underground}"
        + f"|combos={constraints.restrict_parking_combos}"
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
            st.caption("⚠️ Параметры/ограничения изменились — пересчитайте рекомендации.")
        else:
            st.caption("Нажмите кнопку, чтобы запустить подбор.")

    if clicked:
        with st.spinner("Optuna ищет лучшие сценарии (~12 сек)..."):
            bundle = generate_pareto_recommendations(
                site=site, base_options=base_options, norms=norms,
                base_tep=base_tep, n_trials=400, seed=42,
                constraints=constraints,
            )
        st.session_state["pareto_bundle"] = bundle
        st.session_state["pareto_bundle_key"] = bundle_key
        cached_bundle = bundle

    if cached_bundle is None:
        return

    # v0.9.11 (AUDIT S-1/P1-7): если Парето вернул 0 рекомендаций,
    # вместо пустых карточек показываем прицельное объяснение причины.
    if not cached_bundle.recommendations:
        if cached_bundle.no_feasible_reason:
            st.error(
                "❌ Нет рекомендаций.\n\n" + cached_bundle.no_feasible_reason
            )
        else:
            st.warning(
                f"⚠ Парето не вернул рекомендаций "
                f"({cached_bundle.n_trials_feasible}/{cached_bundle.n_trials_total} "
                f"feasible). Попробуйте изменить настройки подбора."
            )
        return

    cols = st.columns(len(cached_bundle.recommendations))
    for i, rec in enumerate(cached_bundle.recommendations):
        with cols[i]:
            _render_recommendation_card(rec, i, base_options)

    # v0.9.12 (AUDIT S-3): если у нескольких рекомендаций ИДЕНТИЧНАЯ
    # apartments_area — обычно это значит «КИТ упёрт в нормативный потолок
    # ПЗЗ=2.5» (площадь становится фиксированной функцией от площади квартала
    # и нормативов). Объясняем пользователю, что он видит.
    apts = [int(r.tep.apartments_area.value or 0) for r in cached_bundle.recommendations]
    if len(apts) >= 2 and len(set(apts)) < len(apts):
        st.caption(
            "ℹ️ У нескольких рекомендаций одинаковая площадь квартир — это "
            "значит, что КИТ упёрт в нормативный потолок ПЗЗ (с ДПТ = 2.5). "
            "Площадь не увеличивается, варианты различаются ТОЛЬКО парковкой "
            "и прибылью."
        )

    # v0.9.6: сравнительная таблица «База ↔ Рекомендации» — все варианты
    # рядом по столбцам, с подсветкой лучшего значения в каждой строке.
    _render_comparison_table(cached_bundle, base_options)


def _render_comparison_table(
    bundle: ParetoBundle, base_options: CalculationOptions,
) -> None:
    """Сводная таблица «База ↔ Рекомендации» с подсветкой лучшего (v0.9.6).

    Все 4 варианта рядом по столбцам, по строкам — те же KPI что в карточках.
    В каждой строке выделяется ячейка с лучшим значением (зелёный фон).
    """
    base_tep = bundle.base_tep

    # Какие поля — и направление «лучшего» (max/min/none)
    # Используем те же KPI что и в _extract_kpi_fields, но с числовыми значениями
    # для подсветки.
    def _val(tep: TEPResult, opts: CalculationOptions, key: str):
        op = int(tep.parking_open_places.value or 0)
        ml = int(tep.parking_multilevel_places.value or 0)
        ug = int(tep.parking_underground_places.value or 0)
        if key == "apt":         return float(tep.apartments_area.value or 0.0)
        if key == "kit":         return float(tep.kit.value or 0.0)
        if key == "floors":      return int(opts.floors)
        if key == "open":        return op
        if key == "ml":          return ml
        if key == "ug":          return ug
        if key == "kg":          return int(tep.kindergarten_places_accepted.value or 0)
        if key == "sch":         return int(tep.school_places_accepted.value or 0)
        if key == "znop":        return float(tep.znop_per_person.value or 0.0)
        if key == "profit":      return (
            float(tep.economy.profit) if tep.economy is not None else None
        )
        return None

    # Список вариантов: (label, tep, options)
    variants: list[tuple[str, TEPResult, CalculationOptions]] = [
        ("База", base_tep, base_options),
    ]
    for rec in bundle.recommendations:
        opts = _rec_options_from_params(base_options, rec.params)
        variants.append((rec.label, rec.tep, opts))

    # Описание строк: (заголовок, ключ, формат, best_direction)
    # best_direction: "max" — больше лучше, "min" — меньше лучше, None — без выделения.
    fields = [
        ("Площадь квартир, м²",        "apt",    lambda v: f"{int(v):,}".replace(",", " "), "max"),
        ("КИТ ПЗЗ",                    "kit",    lambda v: f"{v:.3f}",                      None),
        ("Этажность",                  "floors", lambda v: str(v),                          None),
        ("Парковки — открытые, м/м",   "open",   lambda v: f"{int(v):,}".replace(",", " "), None),
        ("    — многоуровневые, м/м",  "ml",     lambda v: f"{int(v):,}".replace(",", " "), None),
        ("    — подземные, м/м",       "ug",     lambda v: f"{int(v):,}".replace(",", " "), "min"),
        ("ДОО, мест",                  "kg",     lambda v: f"{int(v):,}".replace(",", " "), None),
        ("СОШ, мест",                  "sch",    lambda v: f"{int(v):,}".replace(",", " "), None),
        ("ЗНОП, м²/чел",               "znop",   lambda v: f"{v:.0f}",                      None),
        ("Прибыль, у.е.",              "profit", lambda v: f"{int(v):,}".replace(",", " ") if v is not None else "—", "max"),
    ]

    # DataFrame: индекс = показатель, колонки = варианты, ячейки = строки.
    data = {label: [] for label, _, _ in variants}
    best_idx_per_row: list[int | None] = []  # индекс лучшего в каждой строке
    row_labels: list[str] = []
    for row_label, key, fmt, best_dir in fields:
        raw_values = [_val(tep, opts, key) for _, tep, opts in variants]
        row_labels.append(row_label)
        # Подсветка лучшего
        valid = [(i, v) for i, v in enumerate(raw_values) if v is not None]
        best_i: int | None = None
        # v0.9.11 (AUDIT P1-10): не подсвечивать «лучшее», если все значения
        # одинаковые (включая все нули) — нечего сравнивать, выделение сбивает.
        all_equal = (
            len(valid) >= 2
            and all(abs(v - valid[0][1]) < 1e-9 for _, v in valid)
        )
        if all_equal:
            best_i = None
        elif best_dir == "max" and valid:
            best_i = max(valid, key=lambda iv: iv[1])[0]
        elif best_dir == "min" and valid:
            best_i = min(valid, key=lambda iv: iv[1])[0]
        best_idx_per_row.append(best_i)
        for (label, _, _), v in zip(variants, raw_values):
            data[label].append(fmt(v) if v is not None else "—")

    df = pd.DataFrame(data, index=row_labels)

    # Styler: подсветка лучшей ячейки в каждой строке
    def _highlight(row):
        styles = [""] * len(row)
        ridx = row_labels.index(row.name)
        bi = best_idx_per_row[ridx]
        if bi is not None:
            styles[bi] = "background-color: #DCFCE7; font-weight: 700;"  # светло-зелёный
        # База — серый фон во всех ячейках столбца, кроме подсвеченных
        if styles[0] == "":
            styles[0] = "background-color: #F1F5F9;"  # светло-серый
        return styles

    styler = df.style.apply(_highlight, axis=1)

    with st.container(border=True):
        st.markdown("##### 📊 Сравнительная таблица")
        st.caption(
            "Все варианты рядом. Зелёным выделено лучшее значение в строке "
            "(где это применимо: max для площади/прибыли, min для подземки). "
            "База — серый фон."
        )
        st.dataframe(styler, use_container_width=True)


def _extract_kpi_fields(
    tep: TEPResult, options: CalculationOptions | None = None,
) -> list[tuple[str, str]]:
    """Единый набор KPI-полей для сценария (v0.9.6).

    Возвращает [(label, formatted_value), ...] в фиксированном порядке.
    Используется во всех типовых карточках (база, рекомендации, scan)
    чтобы пользователь мог сравнивать визуально.
    """
    def _fmt_int(v: float | int | None) -> str:
        if v is None:
            return "—"
        return f"{int(v):,}".replace(",", " ")

    op = int(tep.parking_open_places.value or 0)
    ml = int(tep.parking_multilevel_places.value or 0)
    ug = int(tep.parking_underground_places.value or 0)
    kg = int(tep.kindergarten_places_accepted.value or 0)
    sch = int(tep.school_places_accepted.value or 0)
    zpp = tep.znop_per_person.value or 0
    floors = options.floors if options is not None else "—"
    profit = (
        _fmt_int(tep.economy.profit) + " у.е."
        if tep.economy is not None else "—"
    )

    return [
        ("Площадь квартир",      f"{_fmt_int(tep.apartments_area.value)} м²"),
        ("КИТ ПЗЗ",              f"{(tep.kit.value or 0):.3f}"),
        ("Этажность",            str(floors)),
        ("Парковки — открытые",  f"{op} м/м" if op + ml + ug > 0 else "—"),
        ("    многоуровневые",   f"{ml} м/м"),
        ("    подземные",        f"{ug} м/м"),
        ("ДОО",                  f"{kg} мест" if kg > 0 else "—"),
        ("СОШ",                  f"{sch} мест" if sch > 0 else "—"),
        ("ЗНОП",                 f"{zpp:.0f} м²/чел" if zpp > 0 else "0 м²/чел"),
        ("Прибыль",              profit),
    ]


def _render_kpi_block(
    tep: TEPResult, options: CalculationOptions | None = None,
) -> None:
    """Единая компактная карточка KPI (v0.9.6) — markdown-таблица из
    `_extract_kpi_fields`. Один и тот же формат в snapshot базы и в
    карточках рекомендаций → можно сравнивать визуально по строкам.
    """
    rows = _extract_kpi_fields(tep, options)
    md = "| Показатель | Значение |\n|---|---|\n"
    for label, value in rows:
        md += f"| {label} | **{value}** |\n"
    st.markdown(md)


def _rec_options_from_params(
    base_options: CalculationOptions, params: dict,
) -> CalculationOptions:
    """Восстановить CalculationOptions сценария из base + sampled params.

    v0.9.8 (AUDIT P1-4/9): раньше восстанавливалось только `floors`.
    Теперь восстанавливаются ещё парковки (mode + custom-доли), иначе
    KPI-карточка рекомендации показывает парковки ИЗ БАЗЫ, а не из
    реального сценария рекомендации.
    """
    from urban_model.models.parking import ParkingConfig

    opts = base_options.model_copy(deep=True)
    if "floors" in params:
        opts.floors = int(params["floors"])

    # Парковки: режим + (для custom) доли + этажность МУ/UG
    if "parking_mode" in params:
        mode = str(params["parking_mode"])
        # v0.9.11 (AUDIT P1-5): сохраняем multilevel_levels / underground_levels
        # ВСЕГДА (даже для не-custom режимов) — иначе при переходе между
        # сценариями уровни сбрасываются в дефолты, что путает в KPI-карточке.
        ml_lvl = int(params.get("multilevel_levels", base_options.parking.multilevel_levels))
        ug_lvl = int(params.get("underground_levels", base_options.parking.underground_levels))
        if mode == "custom":
            # v0.9.10: нормализация долей перед созданием ParkingConfig.
            # Optuna в `_build_options_for_trial` сэмплирует доли независимо,
            # в `sampled` (params) попадает раундед-версия с суммой 1.001/0.999.
            o = float(params.get("parking_open_share", base_options.parking.open_share))
            m = float(params.get("parking_ml_share", base_options.parking.multilevel_share))
            u = float(params.get("parking_ug_share", base_options.parking.underground_share))
            s = o + m + u
            if s > 0:
                o, m, u = o / s, m / s, u / s
            opts.parking = ParkingConfig(
                mode="custom",
                open_share=o, multilevel_share=m, underground_share=u,
                multilevel_levels=ml_lvl, underground_levels=ug_lvl,
            )
        else:
            opts.parking = ParkingConfig(
                mode=mode, multilevel_levels=ml_lvl, underground_levels=ug_lvl,
            )
    return opts


def _render_recommendation_card(
    rec: Recommendation, idx: int, base_options: CalculationOptions,
) -> None:
    """Одна карточка рекомендации — единый формат KPI (v0.9.6)."""
    with st.container(border=True):
        st.markdown(f"#### {rec.label}")
        st.caption(rec.rationale)

        # Краткая «верхняя» сводка дельт — чтобы за секунду понять «лучше/хуже»
        d = rec.delta_vs_base
        delta_lines = [f"Δ площадь: **{d.d_apt_pct:+.1f}%**"]
        if d.d_profit_pct is not None:
            delta_lines.append(f"Δ прибыль: **{d.d_profit_pct:+.1f}%**")
        delta_lines.append(f"Δ КИТ: **{d.d_kit_abs:+.3f}**")
        st.markdown("  ·  ".join(delta_lines))

        # Унифицированный KPI-блок — тот же набор полей что в snapshot базы.
        rec_options = _rec_options_from_params(base_options, rec.params)
        _render_kpi_block(rec.tep, rec_options)

        # Что отличается от базы (текстом, как было)
        if d.key_changes:
            with st.expander("📝 Что изменено vs база", expanded=False):
                for c in d.key_changes:
                    st.markdown(f"• {c}")

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
    """ScanResult → DataFrame для altair-графика.

    v0.9.2: is_base/is_recommended сохраняются как int (0/1), а не bool —
    vega-lite корректно фильтрует int, но с pandas-bool иногда даёт
    непредсказуемое поведение в transform_filter.
    """
    rows = []
    for p in scan.points:
        rows.append({
            "x": p.x_value,
            "x_label": p.x_label,
            "apt": p.apartments_area,
            "profit": p.profit if p.profit is not None else 0.0,
            "kit": p.kit,
            "feasible": bool(p.feasible),
            "is_base": 1 if p.is_base else 0,
            "is_recommended": 1 if p.is_recommended else 0,
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
    # v0.9.2: фильтр по int 0/1 — vega-lite надёжнее обрабатывает,
    # чем pandas-bool после JSON-сериализации.
    base_dot = (
        base_chart.transform_filter("datum.is_base == 1")
        .mark_point(color="#D32F2F", size=200, filled=True)
    )
    rec_dot = (
        base_chart.transform_filter("datum.is_recommended == 1")
        .mark_point(color="#2E7D32", size=300, shape="diamond", filled=True,
                    stroke="white", strokeWidth=2)
    )
    chart = (line + base_dot + rec_dot).properties(height=240)
    st.altair_chart(chart, use_container_width=True)


def _render_scan_summary(scan: ScanResult) -> None:
    """Текстовое резюме скана (v0.9.7).

    Показывает ДВА оптимума:
      • Лучший по ПЛОЩАДИ квартир (это `recommended_point` в ScanResult)
      • Лучший по ПРИБЫЛИ (вычисляется здесь среди feasible-точек)
    Иногда они совпадают, иногда нет — пользователю важно видеть оба,
    чтобы понять компромисс этого фактора в локальном контексте базы.
    """
    base = scan.base_point
    if base is None:
        st.info("Не удалось вычислить базовую точку.")
        return

    feasible = [p for p in scan.points if p.feasible]
    best_apt = max(feasible, key=lambda p: p.apartments_area) if feasible else None
    feasible_with_profit = [p for p in feasible if p.profit is not None]
    best_profit = (
        max(feasible_with_profit, key=lambda p: p.profit)
        if feasible_with_profit else None
    )

    st.markdown(f"**База:** {base.x_label}")
    st.markdown("---")

    # Лучший по площади
    if best_apt is not None:
        d_apt = best_apt.apartments_area - base.apartments_area
        d_apt_pct = (d_apt / base.apartments_area * 100.0) if base.apartments_area > 1e-9 else 0.0
        st.markdown(
            f"🟢 **Лучший по площади:** {best_apt.x_label}  \n"
            f"Δ площадь: {d_apt:+,.0f} м² ({d_apt_pct:+.1f}%)".replace(",", " ")
        )

    # Лучший по прибыли — если отличается
    if best_profit is not None and base.profit is not None and abs(base.profit) > 1e-9:
        d_profit = best_profit.profit - base.profit
        d_profit_pct = d_profit / abs(base.profit) * 100.0
        # Если оптимумы совпадают — упрощаем
        same_as_apt = (
            best_apt is not None
            and abs(best_profit.x_value - best_apt.x_value) < 1e-6
        )
        if same_as_apt:
            st.caption(f"💰 По прибыли — то же значение: {best_profit.x_label}")
        else:
            st.markdown(
                f"💰 **Лучший по прибыли:** {best_profit.x_label}  \n"
                f"Δ прибыль: {d_profit:+,.0f} ({d_profit_pct:+.1f}%) у.е.".replace(",", " ")
            )

    # Кнопка добавить в сравнение — добавляет «лучший по площади» (как и раньше)
    rec_for_btn = best_apt
    if rec_for_btn is not None and not rec_for_btn.is_base:
        if st.button(
            "➕ Лучший по площади в сравнение",
            key=f"add_scan_{scan.factor}",
            use_container_width=True,
        ):
            st.session_state.scenarios.append(
                (f"scan:{scan.factor}={rec_for_btn.x_label}", rec_for_btn.tep)
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
        "Каждая карточка варьирует **ОДИН параметр**, остальные — как в базе. "
        "Это **локальный** анализ; Парето-рекомендации сверху могут давать другие "
        "значения, т.к. меняют параметры в комбинации. 🔴 — база, 🟢 — лучшее "
        "по площади квартир."
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
