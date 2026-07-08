"""Streamlit-вкладка «Оптимизация» (v0.9.0 — редизайн).

Идея: вместо 20 ручек + Optuna 2000 trials — показываем
  1) snapshot базы из вкладки «Расчёт»;
  2) 3 готовых рекомендации (Optuna по 400 trials, выборка 3 лучших);
  3) 3 карточки one-factor (детерминированные сканы парковки/ЗНОП/этажности);
  4) старый UI Optuna — в свёрнутом expander «Продвинутый режим».

См. план v0.9.0 в `~/.claude/plans/parsed-kindling-wombat.md`.
"""

from __future__ import annotations

import re

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
    scan_kindergarten_objects,
    scan_parking_multilevel_share,
    scan_parking_underground_share,
    scan_school_objects,
    scan_znop_steps,
)
from urban_model.optimize.sensitivity import compute_sensitivity
from urban_model.ui.formatting import fmt_int, fmt_m2


def _flat_dot(color: str, stroke: str = "white") -> str:
    """Плоский кружок-маркер (без объёма/глянца) — единое обозначение
    в легенде и резюме сканов, совпадает с точками на графике."""
    return (
        f"<span style='display:inline-block;width:11px;height:11px;"
        f"border-radius:50%;background:{color};border:1px solid {stroke};"
        f"vertical-align:middle;margin:0 4px 2px 0;'></span>"
    )


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
    vpp_request=None,
) -> tuple[TEPResult, bool]:
    """Возвращает (base_tep, synced_with_calc_tab).

    Если на вкладке Расчёт уже посчитан результат и параметры совпадают —
    используем его. Иначе считаем сами и помечаем баннером «не синхронно».
    v0.12.14: при пересчёте базы тоже строим ВПП (по vpp_request), иначе база
    оказалась бы без ВПП, а рекомендации — с ВПП (несогласованно).
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
    # Fallback: считаем сами. С ВПП — тот же 2-проходный механизм, что на
    # «Расчёте», но БЕЗ записи в session_state (не затираем снапшот «Расчёта»).
    if vpp_request is not None and getattr(vpp_request, "mode", None):
        try:
            from urban_model.calculations import vpp as _vpp
            opts1 = base_options.model_copy(deep=True)
            opts1.built_in = None
            opts1.built_in_list = []
            r0 = solve_max_kit(site, opts1, norms)
            build = _vpp.build_built_ins(
                mode=vpp_request.mode,
                population=r0.population.value or 0.0,
                footprint=r0.housing_footprint.value or 0.0,
                norms=norms,
                custom_4_4_m2=getattr(vpp_request, "custom_4_4_m2", None),
                custom_4_6_m2=getattr(vpp_request, "custom_4_6_m2", None),
            )
            opts2 = base_options.model_copy(deep=True)
            opts2.built_in = None
            opts2.built_in_list = build.built_ins
            return solve_max_kit(site, opts2, norms), False
        except Exception:
            pass
    return solve_max_kit(site, base_options, norms), False


def _render_base_snapshot(
    base_tep: TEPResult, base_options: CalculationOptions, synced: bool,
) -> None:
    """База — 3 ряда st.metric для удобной читаемости в full-width (v0.9.13).

    Раньше был markdown-table из `_render_kpi_block` — узкая колонка
    значений, плохо использует горизонтальное пространство.
    В карточках рекомендаций таблица остаётся (там ширина 1/3 экрана).
    """
    from urban_model.ui.output import render_main_kpi_grid

    with st.container(border=True):
        if synced:
            st.markdown("##### :material/dashboard: База")
            st.caption("С вкладки «Расчёт».")
        else:
            st.markdown("##### :material/dashboard: База (рассчитана здесь)")
            st.caption(
                "Параметры на вкладке «Параметры» не совпадают с последним "
                "результатом на «Расчёте». Откройте «Расчёт» для синхронизации."
            )

        # v0.12.16: единый KPI-блок — тот же компонент, что «Основные показатели»
        # на вкладке «Расчёт» (одинаковое наполнение по запросу заказчика).
        render_main_kpi_grid(base_tep, base_options)


# ---------------------------------------------------------------------------
# Секция 2: Топ-3 рекомендации
# ---------------------------------------------------------------------------

def _render_pareto_constraints(base_options: CalculationOptions) -> ParetoConstraints:
    """Сворачиваемый блок с ограничениями подбора Парето (v0.9.3).

    Возвращает ParetoConstraints. Все значения по умолчанию = без ограничений
    (5..25 этажей, все типы парковок разрешены).

    v0.10.3: при кластерах вместо единой этажности — диапазон этажности ЗОН
    (опционально; если выключено — этажность зон берётся из базы).
    """
    has_clusters = bool(base_options.floor_clusters)
    with st.expander(":material/tune: Настройки подбора", expanded=False):
        st.caption(
            "Здесь можно ограничить пространство перебора — например, задать "
            "узкий диапазон этажности или запретить подземные парковки."
        )

        c1, c2 = st.columns(2)
        floors_range = (5, 25)
        cluster_floors_ranges: tuple[tuple[int, int], ...] | None = None
        with c1:
            if has_clusters:
                st.markdown("**Этажность зон**")
                vary_zones = st.checkbox(
                    "Подбирать этажность зон",
                    value=False, key="pareto_vary_zones",
                    help="Если выключено — этажность зон берётся из базового "
                         "варианта (вкладка «Параметры»). Включите, чтобы подбор "
                         "перебирал высотность КАЖДОЙ зоны в своём диапазоне.",
                )
                if vary_zones:
                    st.caption("Диапазон подбора для каждой зоны:")
                    rngs = []
                    for i, c in enumerate(base_options.floor_clusters):
                        label = c.label or f"Зона {i + 1}"
                        # v0.11.0: верхняя граница следует за этажностью зоны
                        # в Базе (slider с key игнорирует value= — пушим в
                        # session_state при смене этажности зоны).
                        _zhi = max(3, min(30, int(c.floors)))
                        _zk = f"pareto_zone_range_{i}"
                        if st.session_state.get(f"_pzbase_{i}") != _zhi:
                            st.session_state[_zk] = (3, _zhi)
                            st.session_state[f"_pzbase_{i}"] = _zhi
                        lo, hi = st.slider(
                            f"{label} (текущая {c.floors} эт.)",
                            3, 30, (3, _zhi),
                            key=_zk,
                        )
                        rngs.append((int(lo), int(hi)))
                    if len(rngs) == len(base_options.floor_clusters):
                        cluster_floors_ranges = tuple(rngs)
                else:
                    st.caption("Этажность зон фиксирована (как в базе).")
            else:
                st.markdown("**Этажность**")
                # v0.11.0: верхняя граница диапазона следует за этажностью Базы.
                # Слайдер с key= игнорирует value= после 1-го рендера, поэтому
                # при СМЕНЕ этажности Базы пушим новый диапазон в session_state.
                # Запоминаем, от какой базы он выставлен (_pareto_floors_base);
                # ручные правки между сменами Базы сохраняются.
                _bhi = max(3, min(30, int(base_options.floors)))
                if st.session_state.get("_pareto_floors_base") != _bhi:
                    st.session_state["pareto_floors_range"] = (3, _bhi)
                    st.session_state["_pareto_floors_base"] = _bhi
                lo, hi = st.slider(
                    "Диапазон", 3, 30, (3, _bhi),
                    key="pareto_floors_range",
                    help="Подбор будет рассматривать только этажность в этом диапазоне. "
                         "Меняется автоматически вслед за этажностью на вкладке «Расчёт».",
                )
                floors_range = (int(lo), int(hi))

        with c2:
            st.markdown("**Разрешённые типы парковок**")
            # v0.12.17 (#5): дефолт = набор парковок из «Параметров». Тип включён
            # по умолчанию, если его доля в базе > 0 (стилобат выключен в базе →
            # снят и здесь). При смене базового набора пушим новые дефолты в
            # session_state (как у floors_range); ручные правки сохраняются.
            _p = base_options.parking
            _styl0 = float(getattr(_p, "stylobate_share", 0.0) or 0.0)
            if _p.mode == "all_open":
                _d = (True, False, False, False)
            elif _p.mode == "custom":
                _d = (_p.open_share > 0, _p.multilevel_share > 0,
                      _p.underground_share > 0, _styl0 > 0)
            else:  # min_open: открытые (минимум) + подземные (остаток)
                _d = (True, False, True, False)
            _psig = f"{_p.mode}|{_d}"
            if st.session_state.get("_pareto_park_base") != _psig:
                st.session_state["pareto_allow_open"] = _d[0]
                st.session_state["pareto_allow_ml"] = _d[1]
                st.session_state["pareto_allow_ug"] = _d[2]
                st.session_state["pareto_allow_styl"] = _d[3]
                st.session_state["_pareto_park_base"] = _psig
            st.caption("По умолчанию — как на вкладке «Параметры»; можно расширить.")
            allow_open = st.checkbox(
                ":material/local_parking: Открытые наземные", key="pareto_allow_open",
                help="Самые дешёвые, но требуют пятна на квартале (≥12.5% по нормативу).",
            )
            allow_multilevel = st.checkbox(
                ":material/vertical_align_top: Многоуровневые наземные", key="pareto_allow_ml",
                help="Компактнее открытых, средняя себестоимость.",
            )
            allow_underground = st.checkbox(
                ":material/vertical_align_bottom: Подземные", key="pareto_allow_ug",
                help=(
                    "Не занимают пятно квартала, но дороже всех. Можно исключить, "
                    "если по проекту или нормативам не разрешены."
                ),
            )
            allow_stylobate = st.checkbox(
                ":material/last_page: Стилобатные", key="pareto_allow_styl",
                help=(
                    "Поднятый стилобат-паркинг (не заглубляется, не занимает ЗУ "
                    "квартала). 25% деки под домами = −1 этаж жилья там; "
                    "дворовая дека даёт ≤70% озеленения по ПЗЗ."
                ),
            )
            restrict_combos = st.checkbox(
                ":material/filter_alt: Реалистичные сочетания парковок",
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
            allow_stylobate=allow_stylobate,
            restrict_parking_combos=restrict_combos,
            cluster_floors_ranges=cluster_floors_ranges,
        )


def _render_recommendations_section(
    site: Site, base_options: CalculationOptions, norms: Normatives, base_tep: TEPResult,
    vpp_request=None,
) -> None:
    st.markdown("### :material/track_changes: Рекомендации — стратегии застройки")
    st.caption(
        "Подбор в широком диапазоне параметров находит несколько осмысленных "
        "стратегий по разным критериям (максимум площади, эконом-индекс, "
        "сбалансированный, девелоперский). Это не «единственный лучший», а "
        "направления для дальнейшей проработки. Дельты — относительно базы выше."
    )

    constraints = _render_pareto_constraints(base_options)

    # Ключ для определения «устарел ли bundle»: hash от base_options + site_area
    # + constraints (если поменялся диапазон/разрешения — пересчитываем)
    bundle_key = (
        base_options.model_dump_json()
        + f"|site={site.area_m2}"
        + f"|floors={constraints.floors_range}"
        + f"|zones={constraints.cluster_floors_ranges}"
        + f"|park={constraints.allow_open}{constraints.allow_multilevel}{constraints.allow_underground}{constraints.allow_stylobate}"
        + f"|combos={constraints.restrict_parking_combos}"
        + f"|vpp={getattr(vpp_request, 'mode', None)}"  # v0.12.14
    )
    cached_bundle: ParetoBundle | None = st.session_state.get("pareto_bundle")
    cached_key: str | None = st.session_state.get("pareto_bundle_key")
    is_stale = cached_bundle is None or cached_key != bundle_key

    col_btn, col_msg = st.columns([1, 2])
    with col_btn:
        clicked = st.button(
            ":material/track_changes: Подобрать сценарии",
            type="primary",
            help="700 испытаний. Длительность зависит от размера квартала и параметров (типично 1-2 мин).",
        )
    with col_msg:
        if cached_bundle is not None and not is_stale:
            st.caption(
                f"Рекомендации актуальны "
                f"({cached_bundle.n_trials_feasible}/{cached_bundle.n_trials_total} feasible)."
            )
        elif cached_bundle is not None and is_stale:
            st.caption("Параметры/ограничения изменились — пересчитайте рекомендации.")
        # else: лишний caption «Нажмите кнопку…» убран — кнопка говорит сама за себя.

    if clicked:
        # v0.9.15: реальный прогресс-бар вместо «спиннера на 12 сек».
        # Optuna может занимать 1-2 минуты (зависит от размера квартала
        # и preview-solve для ВПП). Callback показывает текущий trial.
        progress = st.progress(0.0, text="Запускаем подбор сценариев...")

        def _on_progress(current: int, total: int, best: float) -> None:
            pct = current / total
            best_str = f"{best:,.0f} м²".replace(",", " ") if best > 0 else "—"
            progress.progress(
                pct,
                text=f"Вариант {current}/{total} · лучшая площадь: {best_str}",
            )

        bundle = generate_pareto_recommendations(
            site=site, base_options=base_options, norms=norms,
            base_tep=base_tep, n_trials=700, seed=42,
            constraints=constraints,
            progress_callback=_on_progress,
            vpp_request=vpp_request,  # v0.12.14: фикс. режим ВПП
        )
        progress.empty()
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
                "Нет рекомендаций.\n\n" + cached_bundle.no_feasible_reason
            )
        else:
            st.warning(
                f"Подбор не вернул рекомендаций "
                f"({cached_bundle.n_trials_feasible}/{cached_bundle.n_trials_total} "
                f"feasible). Попробуйте изменить настройки подбора."
            )
        return

    # v0.9.13: TL;DR-строка над карточками — за секунду видно «есть ли
    # смысл смотреть детали». Показываем максимальные Δ% по двум главным
    # критериям среди всех рекомендаций.
    deltas_apt = [r.delta_vs_base.d_apt_pct for r in cached_bundle.recommendations]
    deltas_index = [
        r.delta_vs_base.d_index_abs for r in cached_bundle.recommendations
        if r.delta_vs_base.d_index_abs is not None
    ]
    if deltas_apt or deltas_index:
        parts = []
        if deltas_apt:
            best_apt = max(deltas_apt)
            parts.append(
                f"максимальное **Δ площадь**: {best_apt:+.1f}%"
            )
        if deltas_index:
            best_index = max(deltas_index)
            parts.append(
                f"максимальный **Δ эконом-индекс**: {best_index:+.0f}"
            )
        st.markdown("📈 " + "  ·  ".join(parts) + " — относительно базы.")

    # v0.12.3: карточки сеткой 2×2 (а не в один ряд из 4). В каждом ряду
    # карточки растягиваются до одинаковой высоты (CSS .rec-card → height:100%).
    recs = cached_bundle.recommendations
    for row_start in range(0, len(recs), 2):
        row = recs[row_start:row_start + 2]
        cols = st.columns(2)
        for j, rec in enumerate(row):
            with cols[j]:
                _render_recommendation_card(rec, row_start + j, base_options, vpp_request)

    # v0.12.11: ДИНАМИЧЕСКОЕ пояснение преимуществ «Девелоперского» —
    # конкретные сильные стороны именно этого варианта vs остальные.
    by_label = {r.label: r for r in recs}
    dev = by_label.get("Девелоперский")
    if dev is not None:
        advs = _developer_advantages(dev, recs)
        body = (
            "**Девелоперский** — практичный вариант для дальнейшей проработки. "
            "В отличие от «Порогового» (предельная застройка на грани окупаемости, "
            "выше риск) он берёт запас по экономике и устойчивости."
        )
        if advs:
            body += " Его сильные стороны в этом расчёте:\n\n" + "\n".join(f"• {a}" for a in advs)
        st.info(body, icon="💡")


def _developer_advantages(dev, recs) -> list[str]:
    """Динамический список преимуществ «Девелоперского» vs другие рекомендации."""
    out: list[str] = []
    by = {r.label: r for r in recs}
    dt = dev.tep
    di = dt.economy.economy_index if dt.economy else None
    # vs «Максимум площади» — обычно выше эконом-индекс
    ma = by.get("Максимум площади")
    if ma is not None and di is not None and ma.tep.economy is not None:
        if di > ma.tep.economy.economy_index + 1:
            out.append(
                f"эконом-индекс **{di:.0f}** против {ma.tep.economy.economy_index:.0f} "
                f"у «Максимум площади» — выше запас окупаемости"
            )
    # vs «Максимум эконом-индекса» — обычно больше площади
    mi = by.get("Максимум эконом-индекса")
    if mi is not None:
        da = float(dt.apartments_area.value or 0)
        ia = float(mi.tep.apartments_area.value or 0)
        if da > ia * 1.02:
            out.append(
                f"площадь квартир **{da:,.0f} м²** против {ia:,.0f} у "
                f"«Максимум эконом-индекса» — больше выход ТЭП".replace(",", " ")
            )
    # Парковки — состав
    op = int(dt.parking_open_places.value or 0); ml = int(dt.parking_multilevel_places.value or 0)
    ug = int(dt.parking_underground_places.value or 0)
    sp = int(getattr(dt, "parking_stylobate_places", None).value or 0) \
        if getattr(dt, "parking_stylobate_places", None) is not None else 0
    parts = [f"{n} {t}" for n, t in
             [(op, "откр."), (ml, "МУ"), (ug, "подз."), (sp, "стилоб.")] if n > 0]
    if parts:
        out.append("парковки уместны для класса: " + " / ".join(parts))
    # Инженерка — крупные объекты
    eng = getattr(dt, "engineering", None)
    if eng is not None:
        big = sum(o.count for o in eng.objects if o.key in ("boiler", "osps") and o.in_balance)
        if big <= 2:
            out.append("без перегруза инженерии (1 котельная + 1 ОСПС)")
    # ЗНОП — среда
    zpp = float(dt.znop_per_person.value or 0)
    others_z = [float(r.tep.znop_per_person.value or 0) for r in recs if r.label != "Девелоперский"]
    if zpp > 0 and others_z and zpp >= max(others_z) - 0.1:
        out.append(f"больше озеленения (ЗНОП {zpp:.1f} м²/чел) — качественнее среда, легче продавать")
    return out[:4]

    # v0.9.12 (AUDIT S-3): если у нескольких рекомендаций ИДЕНТИЧНАЯ
    # apartments_area — обычно это значит «КИТ упёрт в нормативный потолок
    # ПЗЗ=2.5» (площадь становится фиксированной функцией от площади квартала
    # и нормативов). Объясняем пользователю, что он видит.
    apts = [int(r.tep.apartments_area.value or 0) for r in cached_bundle.recommendations]
    if len(apts) >= 2 and len(set(apts)) < len(apts):
        st.caption(
            "У нескольких рекомендаций одинаковая площадь квартир — это "
            "значит, что КИТ упёрт в нормативный потолок ПЗЗ (с ДПТ = 2.5). "
            "Площадь не увеличивается, варианты различаются ТОЛЬКО парковкой "
            "и экономикой."
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
        # v0.9.29: этажность из TEP (учитывает кластеры) — строка-метка.
        if key == "floors":      return _floors_label(tep, opts)
        if key == "open":        return op
        if key == "ml":          return ml
        if key == "ug":          return ug
        if key == "kg":          return int(tep.kindergarten_places_accepted.value or 0)
        if key == "sch":         return int(tep.school_places_accepted.value or 0)
        if key == "znop":        return float(tep.znop_per_person.value or 0.0)
        if key == "profit":      return (
            float(tep.economy.profit) if tep.economy is not None else None
        )
        if key == "index":       return (
            float(tep.economy.economy_index) if tep.economy is not None else None
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
        # v0.9.14: для парковок «лучшее» субъективно — пользователь сам решает,
        # хочется ли ему минимум подземки или максимум. Подсветку убрали.
        ("Парковки — открытые, м/м",   "open",   lambda v: f"{int(v):,}".replace(",", " "), None),
        ("    — многоуровневые, м/м",  "ml",     lambda v: f"{int(v):,}".replace(",", " "), None),
        ("    — подземные, м/м",       "ug",     lambda v: f"{int(v):,}".replace(",", " "), None),
        ("ДОО, мест",                  "kg",     lambda v: f"{int(v):,}".replace(",", " "), None),
        ("СОШ, мест",                  "sch",    lambda v: f"{int(v):,}".replace(",", " "), None),
        ("ЗНОП, м²/чел",               "znop",   lambda v: f"{v:.0f}",                      None),
        ("Эконом-индекс (100=окуп.)",  "index",  lambda v: f"{v:.0f}" if v is not None else "—", "max"),
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
            and all(isinstance(v, (int, float)) for _, v in valid)
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

    # v0.9.13: ключевые строки выделяем bold по всей строке (визуально
    # ведут глаз). Подсветка лучшего значения остаётся.
    _KEY_ROWS = {"Площадь квартир, м²", "Эконом-индекс (100=окуп.)", "КИТ ПЗЗ"}

    def _highlight(row):
        styles = [""] * len(row)
        ridx = row_labels.index(row.name)
        bi = best_idx_per_row[ridx]
        # Подсветка лучшей ячейки
        if bi is not None:
            styles[bi] = "background-color: #DCFCE7; font-weight: 700;"  # светло-зелёный
        # База — серый фон во всех ячейках столбца, кроме подсвеченных
        if styles[0] == "":
            styles[0] = "background-color: #F1F5F9;"  # светло-серый
        # Bold для всей ключевой строки
        if row.name in _KEY_ROWS:
            for i in range(len(styles)):
                styles[i] = (styles[i] + " font-weight: 700;").strip()
        return styles

    styler = df.style.apply(_highlight, axis=1)

    with st.container(border=True):
        st.markdown("##### :material/table_chart: Сравнительная таблица")
        st.caption(
            "Все варианты рядом. Зелёным выделено лучшее значение в строке "
            "(где это применимо: max для площади/эконом-индекса). "
            "База — серый фон."
        )
        # v0.12.1: одинаковая ширина всех столбцов-вариантов.
        col_config = {
            col: st.column_config.Column(width="small") for col in df.columns
        }
        st.dataframe(styler, use_container_width=True, column_config=col_config)


def _floors_label(tep: TEPResult, options: CalculationOptions | None = None) -> str:
    """Подпись этажности с учётом кластеров (v0.9.29).

    При зонах: «9 / 21 эт. (ср. 15.0)». Иначе — «12 эт.» из options/tep.
    Источник истины — TEP (отражает реально посчитанный сценарий).
    """
    if tep.floor_clusters_detail:
        fl = " / ".join(str(d["floors"]) for d in tep.floor_clusters_detail)
        eff = tep.effective_floors or 0.0
        return f"{fl} эт. (ср. {eff:.1f})"
    if options is not None:
        return f"{int(options.floors)} эт."
    return "—"


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
    styl = int(getattr(tep, "parking_stylobate_places", None).value or 0) \
        if getattr(tep, "parking_stylobate_places", None) is not None else 0
    ml_obj = int(tep.parking_multilevel_objects.value or 0)
    kg = int(tep.kindergarten_places_accepted.value or 0)
    sch = int(tep.school_places_accepted.value or 0)
    zpp = tep.znop_per_person.value or 0

    # v0.12.17: число объектов ДОО/СОШ из formula-строки (как на «Расчёте»).
    def _nobj(formula: str | None) -> int:
        if not formula:
            return 0
        m = re.search(r'\[([^\]]+)\]', formula)
        if not m:
            return 0
        return len([x for x in m.group(1).split(",") if x.strip()])
    kg_n = _nobj(tep.kindergarten_places_accepted.formula)
    sch_n = _nobj(tep.school_places_accepted.formula)
    # Доп. образование (ВРИ 3.5.1, v0.12.15)
    ae = int(getattr(tep, "add_education_places_accepted", None).value or 0) \
        if getattr(tep, "add_education_places_accepted", None) is not None else 0
    ae_bi = bool(getattr(tep, "add_education_built_in", False))
    # Поликлиника (ВРИ 3.4.1, v0.12.28)
    poly = int(getattr(tep, "polyclinic_visits_accepted", None).value or 0) \
        if getattr(tep, "polyclinic_visits_accepted", None) is not None else 0
    poly_bi = bool(getattr(tep, "polyclinic_built_in", False))
    # v0.9.29: этажность берём из TEP (учитывает кластеры). При зонах —
    # «9 / 21 (ср. 15.0)»; иначе — одиночная этажность.
    floors = _floors_label(tep, options)
    # v0.12.1: headline — стабильный эконом-индекс (100 = окупаемость).
    # Сырые profit/ROI/маржа вынесены в технический expander карточки.
    econ_index = (
        f"{tep.economy.economy_index:.0f} / 100"
        if tep.economy is not None else "—"
    )
    # v0.12.3: краткая инженерия — крупные объекты (котельные/ОСПС) и итог.
    eng_str = "—"
    eng = getattr(tep, "engineering", None)
    if eng is not None and eng.objects:
        _cnt = {o.key: o.count for o in eng.objects}
        n_total = sum(o.count for o in eng.objects if o.count > 0)
        eng_str = (
            f"{n_total} об. (котельн. {_cnt.get('boiler', 0)}, "
            f"ОСПС {_cnt.get('osps', 0)})"
        )
    return [
        ("Площадь квартир",      f"{_fmt_int(tep.apartments_area.value)} м²"),
        ("КИТ ПЗЗ",              f"{(tep.kit.value or 0):.3f}"),
        ("Этажность",            str(floors)),
        ("Парковки — открытые",  f"{op} м/м" if op + ml + ug + styl > 0 else "—"),
        ("    многоуровневые",   f"{ml} м/м ({ml_obj} об.)" if ml > 0 else f"{ml} м/м"),
        ("    подземные",        f"{ug} м/м"),
        ("    стилобатные",      f"{styl} м/м"),
        ("ДОО",                  f"{kg} мест ({kg_n} об.)" if kg > 0 else "—"),
        ("СОШ",                  f"{sch} мест ({sch_n} об.)" if sch > 0 else "—"),
        ("Доп. образование",     f"{ae} мест ({'ВПП' if ae_bi else 'отд.'})" if ae > 0 else "—"),
        ("Поликлиника",          f"{poly} посещ. ({'ВПП' if poly_bi else 'отд.'})" if poly > 0 else "—"),
        ("ЗНОП",                 f"{zpp:.0f} м²/чел" if zpp > 0 else "0 м²/чел"),
        ("Инженерия",            eng_str),
        ("Эконом-индекс",        econ_index),
    ]


def _render_kpi_block(
    tep: TEPResult, options: CalculationOptions | None = None,
) -> None:
    """Единая компактная карточка KPI (v0.9.6) — markdown-таблица из
    `_extract_kpi_fields`. Один и тот же формат в snapshot базы и в
    карточках рекомендаций → можно сравнивать визуально по строкам.
    """
    # v0.10.16: st.dataframe(use_container_width) вместо markdown-таблицы —
    # markdown-таблица не растягивается на ширину карточки (оставляла
    # пустое место справа). dataframe заполняет блок целиком.
    rows = _extract_kpi_fields(tep, options)
    df = pd.DataFrame(
        [{"Показатель": label, "Значение": value} for label, value in rows]
    )
    st.dataframe(df, hide_index=True, use_container_width=True)


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
    # v0.9.29: восстановить этажность зон (кластеры) из sampled params.
    if "cluster_floors" in params and opts.floor_clusters:
        new_fl = [int(x) for x in params["cluster_floors"]]
        if len(new_fl) == len(opts.floor_clusters):
            opts.floor_clusters = [
                c.model_copy(update={"floors": f})
                for c, f in zip(opts.floor_clusters, new_fl)
            ]
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
        # v0.12.2/v0.12.9: стилобат — 4-й тип, восстанавливаем всегда.
        styl_sh = float(params.get(
            "parking_stylobate_share",
            getattr(base_options.parking, "stylobate_share", 0.0),
        ))
        if mode == "custom":
            # v0.12.9: нормализуем ВСЕ ЧЕТЫРЕ доли к сумме 1.0 (стилобат —
            # полноценный тип; sampled-доли могут быть округлены 0.999/1.001).
            o = float(params.get("parking_open_share", base_options.parking.open_share))
            m = float(params.get("parking_ml_share", base_options.parking.multilevel_share))
            u = float(params.get("parking_ug_share", base_options.parking.underground_share))
            s = o + m + u + styl_sh
            if s > 0:
                o, m, u, styl_sh = o / s, m / s, u / s, styl_sh / s
            opts.parking = ParkingConfig(
                mode="custom",
                open_share=o, multilevel_share=m, underground_share=u,
                multilevel_levels=ml_lvl, underground_levels=ug_lvl,
                stylobate_share=styl_sh,
            )
        else:
            opts.parking = ParkingConfig(
                mode=mode, multilevel_levels=ml_lvl, underground_levels=ug_lvl,
                stylobate_share=styl_sh,
            )
    return opts


def _render_recommendation_card(
    rec: Recommendation, idx: int, base_options: CalculationOptions,
    vpp_request=None,
) -> None:
    """Одна карточка рекомендации — единый формат KPI (v0.9.6)."""
    return _render_recommendation_card_impl(rec, idx, base_options, vpp_request)


def _rec_xlsx_bytes(label: str, tep, options) -> bytes:
    """«Паспорт варианта» (xlsx) рекомендации → байты для download_button."""
    import os
    import tempfile

    from urban_model.export import build_variant_xlsx
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        _p = tmp.name
    try:
        build_variant_xlsx(label, tep, options, _p)
        with open(_p, "rb") as f:
            return f.read()
    finally:
        try:
            os.unlink(_p)
        except OSError:
            pass


def _render_recommendation_card_impl(
    rec: Recommendation, idx: int, base_options: CalculationOptions,
    vpp_request=None,
) -> None:
    with st.container(border=True):
        # v0.12.3: невидимый маркер — по нему CSS растягивает карточку до
        # высоты соседней в ряду (равная высота сетки 2×2).
        st.markdown('<div class="rec-card" style="display:none"></div>',
                    unsafe_allow_html=True)
        st.markdown(f"#### {rec.label}")
        st.caption(rec.rationale)

        # Краткая «верхняя» сводка дельт — чтобы за секунду понять «лучше/хуже»
        d = rec.delta_vs_base
        delta_lines = [f"Δ площадь: **{d.d_apt_pct:+.1f}%**"]
        if d.d_index_abs is not None:
            delta_lines.append(f"Δ индекс: **{d.d_index_abs:+.0f}**")
        delta_lines.append(f"Δ КИТ: **{d.d_kit_abs:+.3f}**")
        st.markdown("  ·  ".join(delta_lines))

        # Унифицированный KPI-блок — тот же набор полей что в snapshot базы.
        rec_options = _rec_options_from_params(base_options, rec.params)
        _render_kpi_block(rec.tep, rec_options)

        # v0.12.17: технический expander с сырыми баллами убран (эконом-индекс
        # уже в KPI-таблице карточки) — короче карточка, меньше скролла.

        # Что отличается от базы (текстом, как было).
        # v0.12.28.3: экспандер показываем ВСЕГДА (даже при пустом списке) —
        # иначе карточка без изменений (напр. «Максимум площади» = база) короче
        # соседних, ряд «разъезжается» по высоте.
        with st.expander(":material/edit_note: Что изменено vs база", expanded=False):
            if d.key_changes:
                for c in d.key_changes:
                    st.markdown(f"• {c}")
            else:
                st.caption("Параметры совпадают с базой (изменений нет).")

        # v0.10.15: кнопки одинаковой ширины (use_container_width в равных
        # колонках), стоят вплотную — единый стиль с вкладкой «Расчёт».
        # v0.12.25: 3-я кнопка — «паспорт варианта» xlsx (Сводка + Баланс).
        bcol1, bcol2, bcol3 = st.columns(3)
        if bcol1.button(":material/add: В сравнение", key=f"add_rec_{idx}",
                        use_container_width=True):
            st.session_state.scenarios.append((f"opt:{rec.label}", rec.tep))
            st.toast(f"Добавлено: {rec.label}", icon="✅")
            st.rerun()  # v0.12.11: обновить счётчик вкладки «Сравнение» сразу
        # v0.9.30: «Применить к Расчёту» — переносит параметры сценария на
        # вкладку Расчёт через override (надёжнее патча виджетов: переносит
        # этажность/зоны/парковки целиком). Расчёт покажет баннер + «вернуть форму».
        if bcol2.button(":material/move_to_inbox: В расчёт", key=f"apply_rec_{idx}",
                        use_container_width=True):
            st.session_state["applied_options"] = rec_options
            st.session_state["applied_label"] = rec.label
            # v0.12.32: сохраняем режим ВПП подбора, чтобы вкладка «Расчёт»
            # ПЕРЕСОБРАЛА ВПП под этажность/парковку карточки (rec_options несёт
            # built_in_list БАЗЫ — без пересборки площадь разошлась бы на десятки
            # м² при иной этажности карточки).
            st.session_state["applied_vpp_request"] = vpp_request
            st.toast(f"Применено: {rec.label} → вкладка «Расчёт»", icon="📥")
            st.rerun()
        with bcol3:
            st.download_button(
                ":material/download: xlsx",
                _rec_xlsx_bytes(rec.label, rec.tep, rec_options),
                file_name=f"{rec.label}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"xlsx_rec_{idx}", use_container_width=True,
            )


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


@st.cache_data(show_spinner=False)
def _cached_scan_kg(_norms_key: str, opts_json: str, site_area: float):
    norms = _get_norms_resolver()
    opts = CalculationOptions.model_validate_json(opts_json)
    site = Site(area_m2=site_area)
    return scan_kindergarten_objects(site, opts, norms)


@st.cache_data(show_spinner=False)
def _cached_scan_sch(_norms_key: str, opts_json: str, site_area: float):
    norms = _get_norms_resolver()
    opts = CalculationOptions.model_validate_json(opts_json)
    site = Site(area_m2=site_area)
    return scan_school_objects(site, opts, norms)


def _get_norms_resolver():
    """Кэшированная загрузка нормативов (профиль spb)."""
    from urban_model.normatives import load_normatives
    return load_normatives("spb")


def _scan_to_dataframe(scan: ScanResult) -> pd.DataFrame:
    """ScanResult → DataFrame для altair-графика.

    v0.9.17: при совпадении точек база и рекомендация (одинаковая x) —
    помечаем отдельной категорией `is_both=1`, чтобы рисовать особый
    маркер (фиолетовый ромб с красным крестом), иначе они визуально
    сливаются — видна только одна из двух.
    """
    # v0.10.8: отметка «лучший по прибыли» среди feasible (отдельный маркер).
    feas_profit = [p for p in scan.points if p.feasible and p.profit is not None]
    best_profit_x = (
        max(feas_profit, key=lambda p: p.profit).x_value if feas_profit else None
    )
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
            "is_both": 1 if (p.is_base and p.is_recommended) else 0,
            "is_best_profit": 1 if (best_profit_x is not None
                                    and abs(p.x_value - best_profit_x) < 1e-9) else 0,
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
            alt.Tooltip("profit:Q", title="Выгодность, баллы", format=",.0f"),
            alt.Tooltip("kit:Q", title="КИТ", format=".3f"),
            alt.Tooltip("feasible:N", title="Допустимо"),
        ],
    )
    # v0.9.14: tight Y-axis (zero=False) — динамика чётче видна.
    # Раньше шкала начиналась с 0, и колебания apt 30k-180k м² выглядели
    # почти горизонтальной линией. Теперь масштаб подгоняется по данным.
    base_chart = base_chart.encode(
        y=alt.Y("apt:Q", title="Площадь квартир, м²",
                scale=alt.Scale(zero=False, padding=20)),
    )
    # v0.10.8: нейтральная серая линия, чтобы цветные маркеры были контрастны.
    line = base_chart.mark_line(color="#9AA7B4", point=alt.OverlayMarkDef(
        color="#9AA7B4", size=28, filled=True))
    # Три плоских маркера (filled, тонкая белая обводка, без теней):
    #   • жёлтый, самый КРУПНЫЙ — лучший по прибыли (нижний слой);
    #   • синий — база;
    #   • красный — лучший по площади (верхний слой).
    # Слои от большого к малому → при совпадении видны вложенные кольца.
    # Фильтр по int 0/1 — vega-lite надёжнее с int, чем с bool после JSON.
    profit_dot = (
        base_chart.transform_filter("datum.is_best_profit == 1")
        .mark_point(color="#F5B301", size=420, filled=True,
                    stroke="#7A5B00", strokeWidth=1)
    )
    base_dot = (
        base_chart.transform_filter("datum.is_base == 1")
        .mark_point(color="#1565C0", size=210, filled=True,
                    stroke="white", strokeWidth=1.5)
    )
    rec_dot = (
        base_chart.transform_filter("datum.is_recommended == 1")
        .mark_point(color="#D32F2F", size=110, filled=True,
                    stroke="white", strokeWidth=1.5)
    )
    chart = (line + profit_dot + base_dot + rec_dot).properties(height=240)
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

    st.markdown(f"{_flat_dot('#1565C0')} **База:** {base.x_label}", unsafe_allow_html=True)
    st.markdown("---")

    # Лучший по площади
    if best_apt is not None:
        d_apt = best_apt.apartments_area - base.apartments_area
        d_apt_pct = (d_apt / base.apartments_area * 100.0) if base.apartments_area > 1e-9 else 0.0
        st.markdown(
            f"{_flat_dot('#D32F2F')} **Лучший по площади:** {best_apt.x_label}  \n"
            + f"Δ площадь: {d_apt:+,.0f} м² ({d_apt_pct:+.1f}%)".replace(",", " "),
            unsafe_allow_html=True,
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
            st.markdown(
                f"{_flat_dot('#F5B301', '#7A5B00')} По прибыли — то же значение: "
                f"{best_profit.x_label}",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f"{_flat_dot('#F5B301', '#7A5B00')} **Лучший по прибыли:** {best_profit.x_label}  \n"
                + f"Δ выгодность: {d_profit:+,.0f} ({d_profit_pct:+.1f}%) баллов".replace(",", " "),
                unsafe_allow_html=True,
            )

    # Кнопка добавить в сравнение — добавляет «лучший по площади» (как и раньше)
    rec_for_btn = best_apt
    if rec_for_btn is not None and not rec_for_btn.is_base:
        if st.button(
            ":material/add: Лучший по площади в сравнение",
            key=f"add_scan_{scan.factor}",
        ):
            st.session_state.scenarios.append(
                (f"scan:{scan.factor}={rec_for_btn.x_label}", rec_for_btn.tep)
            )
            st.toast(f"Добавлен лучший вариант скана «{scan.title}»", icon="✅")
            st.rerun()


def _render_social_count_card(scan: ScanResult) -> None:
    """Карточка ДОО/СОШ (v0.9.30): таблица «число объектов → вместимость/ЗУ/валидно».

    Площадь квартир от числа объектов практически НЕ зависит (суммарный ЗУ
    соцобъектов ≈ const × число мест). Поэтому здесь не график «apt», а
    подсказка по реализуемости: при каком числе объектов вместимость
    остаётся в нормативных границах.
    """
    import re as _re

    from urban_model.calculations.warning_codes import WC, has_code

    is_kg = scan.factor == "kindergarten_objects"
    obj_word = "ДОО" if is_kg else "СОШ"

    def _buckets(formula: str | None) -> list[int]:
        if not formula:
            return []
        m = _re.search(r"\[([^\]]+)\]", formula)
        if not m:
            return []
        try:
            return [int(x.strip()) for x in m.group(1).split(",") if x.strip()]
        except ValueError:
            return []

    rows = []
    valid_counts: list[int] = []
    for p in scan.points:
        tep = p.tep
        if is_kg:
            accepted = int(tep.kindergarten_places_accepted.value or 0)
            plot = float(tep.kindergarten_plot_area.value or 0.0)
            formula = tep.kindergarten_places_accepted.formula
        else:
            accepted = int(tep.school_places_accepted.value or 0)
            plot = float(tep.school_plot_area.value or 0.0)
            formula = tep.school_places_accepted.formula
        buckets = _buckets(formula)
        cap_str = (
            f"{min(buckets)}–{max(buckets)}" if buckets and min(buckets) != max(buckets)
            else (str(buckets[0]) if buckets else "—")
        )
        apt = float(tep.apartments_area.value or 0.0)
        invalid = any(
            has_code(w, WC.SOC_CAP_MIN_BELOW, WC.SOC_CAP_MAX_ABOVE)
            for w in tep.warnings
        )
        atypical = any(has_code(w, WC.SOC_CAP_NOT_TYPICAL) for w in tep.warnings)
        if invalid:
            status = "❌ вне норматива"
        elif atypical:
            status = "⚠ нетиповая"
        else:
            status = "✅ ок"
            valid_counts.append((int(p.x_value), apt))
        rows.append({
            "Объектов": int(p.x_value),
            "Вместимости": cap_str,
            "ЗУ, м²": f"{plot:,.0f}".replace(",", " "),
            "Площадь квартир, м²": f"{apt:,.0f}".replace(",", " "),
            "Статус": status,
        })

    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    if valid_counts:
        # v0.12.27: рекомендуем вариант с МАКС. площадью квартир среди валидных
        # (= минимум ЗУ соцобъектов). Для СОШ это обычно минимум корпусов
        # (каждый тянет бассейн+спорт-ядро); для ДОО ЗУ почти не зависит от числа.
        best_n, best_apt = max(valid_counts, key=lambda t: t[1])
        st.markdown(
            f":material/check_circle: **Лучше: {best_n} {obj_word}** — "
            f"наибольшая площадь квартир ({best_apt:,.0f} м²) при допустимых "
            f"вместимостях.".replace(",", " ")
        )
    st.caption(
        f"Больше {obj_word} → больше суммарного ЗУ → меньше площади квартир "
        f"(для СОШ заметнее: бассейн + спорт-ядро на каждый корпус). "
        f"Вместимости выбираются по минимуму ЗУ из типовых (v0.12.22)."
    )


def _render_scan_card(scan: ScanResult) -> None:
    """Одна карточка one-factor: график слева + резюме справа."""
    if not scan.points:
        st.caption("Компонент отключён в «Параметрах» — скан недоступен.")
        return
    # v0.9.30: ДОО/СОШ — не график (apt не зависит от числа), а таблица валидности.
    if scan.factor in ("kindergarten_objects", "school_objects"):
        _render_social_count_card(scan)
        return
    # v0.10.18: график на всю ширину карточки (раньше его сжимала колонка
    # text-резюме справа). Резюме теперь — компактной строкой ПОД графиком.
    _render_scan_chart(scan)
    _render_scan_summary(scan)


def _render_what_to_improve_section(
    site: Site, base_options: CalculationOptions, norms: Normatives,
) -> None:
    """3 expander'а с one-factor сканами."""
    st.markdown("### :material/lightbulb: Что улучшить — пофакторный анализ")
    st.caption(
        "Каждая карточка варьирует **ОДИН параметр**, остальные — как в базе. "
        "Это **локальный** анализ; Парето-рекомендации сверху могут давать другие "
        "значения, т.к. меняют параметры в комбинации."
    )
    # Плоская легенда маркеров (без глянцевых emoji) — совпадает с графиком.
    st.markdown(
        f"{_flat_dot('#1565C0')} база  &nbsp; {_flat_dot('#D32F2F')} лучшее по площади  "
        f"&nbsp; {_flat_dot('#F5B301', '#7A5B00')} лучшее по прибыли",
        unsafe_allow_html=True,
    )

    opts_json = base_options.model_dump_json()
    norms_key = "spb"  # пока один профиль; при смене сменится через cache_clear

    # v0.9.14: сканы в 2 ряда (4 карточки в сетке 2×2), все раскрыты по
    # умолчанию. Раньше были последовательные expander'ы, графики видны
    # только при клике. Теперь визуально всё доступно сразу.
    scan_configs = [
        (":material/local_parking: Р — доля подземных", _cached_scan_parking),
        (":material/apartment: Р — доля многоуровневых", _cached_scan_parking_ml),
        (":material/park: ЗНОП: норматив м²/чел", _cached_scan_znop),
        ("🏢 Этажность", _cached_scan_floors),
        (":material/child_care: ДОО: наполняемость и ЗУ", _cached_scan_kg),
        (":material/school: СОШ: наполняемость и ЗУ", _cached_scan_sch),
    ]
    for row_start in range(0, len(scan_configs), 2):
        cols = st.columns(2, gap="medium")
        for i, (title, cached_fn) in enumerate(scan_configs[row_start:row_start+2]):
            with cols[i]:
                with st.container(border=True):
                    st.markdown(f"##### {title}")
                    try:
                        scan = cached_fn(norms_key, opts_json, site.area_m2)
                        _render_scan_card(scan)
                    except Exception as e:
                        st.error(f"Ошибка скана: {e}")


# ---------------------------------------------------------------------------
# Секция 3a-bis: Советы (v0.14.1) — текстовые рекомендации из сканов
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def _cached_advice(_norms_key: str, opts_json: str, site_area: float,
                   base_apartments: float | None):
    from urban_model.optimize.advice import build_advice
    norms = _get_norms_resolver()
    opts = CalculationOptions.model_validate_json(opts_json)
    site = Site(area_m2=site_area)
    return build_advice(site, opts, norms, base_apartments=base_apartments)


def _render_advice_section(site: Site, base_options: CalculationOptions,
                           base_tep: TEPResult | None = None) -> None:
    """«Советы»: что улучшить относительно Базы (из детерминированных сканов)."""
    st.markdown("### :material/lightbulb: Советы — что можно улучшить")
    st.caption(
        "Локальный анализ: каждый совет меняет ОДИН фактор (прочие — как в "
        "базе). Точную комбинацию ищет подбор выше — советы показывают "
        "направление и цену вопроса."
    )
    _base_apt = (float(base_tep.apartments_area.value or 0)
                 if base_tep is not None else None)
    try:
        advice = _cached_advice("spb", base_options.model_dump_json(),
                                site.area_m2, _base_apt)
    except Exception as e:  # noqa: BLE001 — диагностика в UI
        st.error(f"Ошибка советующего анализа: {e}")
        return
    if not advice:
        st.success(
            "База близка к локальному оптимуму по всем проверенным факторам "
            "(этажность, парковки, ЗНОП) — заметных улучшений одним параметром "
            "не найдено."
        )
        return
    for a in advice:
        st.markdown(f"• {a.text}")


# ---------------------------------------------------------------------------
# Секция 3b: Чувствительность (tornado) — v0.9.15
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def _cached_sensitivity(_norms_key: str, opts_json: str, site_area: float):
    norms = _get_norms_resolver()
    opts = CalculationOptions.model_validate_json(opts_json)
    site = Site(area_m2=site_area)
    return compute_sensitivity(site, opts, norms)


def _render_sensitivity_section(
    site: Site, base_options: CalculationOptions,
) -> None:
    """Tornado: факторы, ранжированные по размаху площади квартир."""
    import altair as alt

    st.markdown("### :material/bar_chart: Чувствительность — что сильнее влияет")
    st.caption(
        "Размах площади квартир при изменении ОДНОГО фактора во всём его "
        "диапазоне (прочие — как в базе). Длиннее полоса = сильнее влияние."
    )
    try:
        impacts = _cached_sensitivity(
            "spb", base_options.model_dump_json(), site.area_m2
        )
    except Exception as e:  # noqa: BLE001 — диагностика в UI
        st.error(f"Ошибка анализа чувствительности: {e}")
        return
    if not impacts:
        st.info("Недостаточно feasible-вариантов для анализа чувствительности.")
        return

    rows = [{
        "Фактор": im.label,
        "Размах, %": round(im.apt_swing_pct, 1),
        "Размах, м²": round(im.apt_swing),
        "Размах прибыли": (
            f"{im.profit_swing:,.0f}".replace(",", " ")
            if im.profit_swing is not None else "—"
        ),
        "Диапазон": f"{im.low_label} … {im.high_label}",
    } for im in impacts]
    df = pd.DataFrame(rows)

    chart = (
        alt.Chart(df)
        .mark_bar(color="#1A1A1A")
        .encode(
            x=alt.X("Размах, %:Q", title="Размах площади квартир, % от базы"),
            y=alt.Y("Фактор:N", sort="-x", title=None),
            tooltip=[
                alt.Tooltip("Фактор:N"),
                alt.Tooltip("Размах, %:Q", format=".1f"),
                alt.Tooltip("Размах, м²:Q", format=",.0f"),
                alt.Tooltip("Размах прибыли:N", title="Размах выгодности, баллы"),
                alt.Tooltip("Диапазон:N", title="Диапазон значений"),
            ],
        )
        .properties(height=max(120, 42 * len(impacts)))
    )
    # v0.10.18: tornado на всю ширину карточки + компактный итог строкой ниже.
    st.altair_chart(chart, use_container_width=True)
    top = impacts[0]
    st.caption(
        f":material/trophy: Сильнее всего: **{top.label}** · "
        f"±{top.apt_swing:,.0f} м² ({top.apt_swing_pct:.0f}% от базы). "
        f"Это локальный анализ при прочих равных.".replace(",", " ")
    )


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
        "Полный перебор с произвольными диапазонами. Используйте для "
        "экспериментов; для типовых задач достаточно «Топ-3 рекомендации»."
    )
    space = _render_search_space_form(base_options)
    if space.is_empty():
        st.info("Отметьте хотя бы один параметр для перебора.")
        return

    if st.button(":material/rocket_launch: Запустить полный перебор", type="primary"):
        progress = st.progress(0.0, text="Запускаем подбор...")

        def cb(current: int, total: int, best: float) -> None:
            progress.progress(
                current / total,
                text=f"Вариант {current}/{total} · лучшая площадь: {best:,.0f} м²".replace(",", " "),
            )

        with st.spinner(f"Перебор вариантов (до {_DEFAULT_TRIALS} испытаний)..."):
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
        st.warning(w)

    c1, c2, c3 = st.columns(3)
    if report.best:
        c1.metric("Лучшая площадь квартир", fmt_m2(report.best.apartments_area))
        c1.caption(f"Вариант #{report.best.rank}")
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
        st.error("Ни одно испытание не дало feasible-результата.")
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

    if st.button(f":material/add: Добавить #{preview.rank} в сравнение"):
        params_summary = ", ".join(f"{k}={v}" for k, v in preview.params.items())
        name = f"opt#{preview.rank} ({params_summary})"
        st.session_state.scenarios.append((name, preview.tep))
        st.toast(f"Добавлен сценарий #{preview.rank}", icon="✅")
        st.rerun()


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
                ["Максимум площади квартир (ТЭП)", "Максимум выгодности (баллы)"],
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
            vary_parking = st.checkbox(":material/local_parking: Парковки", value=True, key="opt_vary_parking_mode")
            vary_kg = st.checkbox(
                ":material/child_care: Кол-во ДОО", value=True, key="opt_vary_kg",
                disabled=not base_options.include_kindergarten,
            )
            vary_school = st.checkbox(
                ":material/school: Кол-во СОШ", value=True, key="opt_vary_school",
                disabled=not base_options.include_school,
            )
            try_built_in = st.checkbox("🏪 ВПП (с/без)", value=True, key="opt_try_vpp")
            vary_znop = st.checkbox(":material/park: ЗНОП", value=True, key="opt_vary_znop")

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
                    "Диапазон", min_value=3, max_value=30, value=(8, 25),
                    key="opt_floors_range",
                )
                floors_range = (int(lo), int(hi))

        if vary_parking:
            with st.container(border=True):
                st.markdown("##### :material/local_parking: Парковки")
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
                st.markdown("##### :material/child_care: Кол-во ДОО")
                lo, hi = st.slider("Диапазон", 1, 10, (1, 4), key="opt_kg_range")
                kg_range = (int(lo), int(hi))

        if vary_school and base_options.include_school:
            with st.container(border=True):
                st.markdown("##### :material/school: Кол-во СОШ")
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
                st.markdown("##### :material/park: ЗНОП")
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
            row["Выгодность, баллы"] = int(r.tep.economy.profit)
        row["ДОО, шт"] = _count_buckets(r.tep.kindergarten_places_accepted.formula)
        row["ДОО, мест"] = int(r.tep.kindergarten_places_accepted.value or 0)
        row["СОШ, шт"] = _count_buckets(r.tep.school_places_accepted.formula)
        row["СОШ, мест"] = int(r.tep.school_places_accepted.value or 0)
        for k, v in r.params.items():
            if k in params_skip:
                continue
            label = {
                "floors": "Этажность", "cluster_floors": "Этажность зон",
                "parking_mode": "Режим парковок",
                "parking_open_share": "% открытых", "parking_ml_share": "% многоуровневых",
                "parking_ug_share": "% подземных", "multilevel_levels": "Этажей МП",
                "underground_levels": "Уровней подземки", "use_vpp": "ВПП",
                "vpp_vri": "ВРИ ВПП", "vpp_mode": "ВПП режим",
                "znop_per_person": "ЗНОП, м²/чел",
            }.get(k, k)
            if k == "cluster_floors":
                v = " / ".join(str(int(x)) for x in v)
            elif k == "parking_mode":
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
    vpp_request=None,
) -> None:
    # v0.10.18: h1-заголовок вкладки в стиле макета.
    st.markdown("# Подбор **сценариев**")
    st.caption(
        "Ниже — ваш текущий вариант с вкладки «Расчёт» (База). Программа "
        "подберёт улучшенные сценарии и покажет, что даёт изменение каждого "
        "параметра по отдельности."
    )

    # 1. База
    base_tep, synced = _get_base_tep(site, base_options, norms, vpp_request)
    _render_base_snapshot(base_tep, base_options, synced)

    st.markdown("")  # отступ

    # 2. Рекомендации (по кнопке)
    _render_recommendations_section(site, base_options, norms, base_tep, vpp_request)

    st.markdown("")

    # 3. One-factor сканы (автоматически)
    _render_what_to_improve_section(site, base_options, norms)

    st.markdown("")

    # 3a-bis. Советы — текстовые рекомендации из сканов (v0.14.1)
    _render_advice_section(site, base_options, base_tep)

    st.markdown("")

    # 3b. Чувствительность — какой фактор сильнее влияет (tornado)
    _render_sensitivity_section(site, base_options)

    # 4. v0.9.14: «Продвинутый режим» полностью скрыт от пользователя
    # (по запросу — не используется в типовых сценариях).
    # Код функции `_render_advanced_optuna_mode` сохранён для возможного
    # возвращения; пока вызов закомментирован.
    # st.markdown("---")
    # with st.expander("⚙ Продвинутый режим (полный перебор)", expanded=False):
    #     _render_advanced_optuna_mode(site, base_options, norms)
