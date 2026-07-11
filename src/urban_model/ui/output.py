"""Рендер главной области: KPI-карточки + раскрывающиеся секции + сравнение."""

from __future__ import annotations

import io
import os
import re
import tempfile

import pandas as pd
import streamlit as st

from urban_model.export import build_variant_xlsx, results_to_dataframe, to_xlsx
from urban_model.export.table import results_to_audit_dataframe
from urban_model.models.result import Status, TEPResult
from urban_model.ui.formatting import (
    fmt_float,
    fmt_ga,
    fmt_int,
    fmt_m2,
    status_badge_html,
)


# ---------------------------------------------------------------------------
# Заголовок-индикатор
# ---------------------------------------------------------------------------

def _spec_alert(kind: str, icon: str, md_text: str) -> None:
    """Кастомный алерт в стиле «Спецификация» с Material Symbols-иконкой.

    Зачем не st.success/warning: их тело НЕ парсит `:material/...:`
    (на Streamlit Cloud показывалось литеральное «info»/«check_circle»).
    Здесь иконка — это ligature-текст в span.material-symbols-sharp,
    который рендерит уже загруженный шрифт (как в плитках/кнопках).
    """
    import html as _html
    accent = {
        "success": "#15803d", "warning": "#F5A623",
        "error": "#c0392b", "info": "#1A1A1A",
    }.get(kind, "#888")
    # Экранируем HTML (в тексте бывает «<», напр. «[175] < 550») и только
    # потом разворачиваем **bold** → <b>.
    safe = _html.escape(md_text)
    safe = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", safe)
    st.markdown(
        f'<div style="display:flex;gap:10px;align-items:flex-start;'
        f'background:#fcfcfc;border:1px solid #ededed;border-left:3px solid {accent};'
        f'border-radius:2px;padding:10px 14px;margin:6px 0 12px;color:#1a1a1a;'
        f'font-size:0.9rem;line-height:1.5;">'
        f'<span class="material-symbols-sharp" '
        f'style="font-size:20px;color:#1a1a1a;flex:none;line-height:1.4;">{icon}</span>'
        f'<span>{safe}</span></div>',
        unsafe_allow_html=True,
    )


def render_header(result: TEPResult) -> None:
    kit_v = result.kit.value or 0
    kit_max = result.kit_normative_max.value or 0
    balance_feasible = result.balance.is_feasible
    kit_ok = result.kit.status != Status.ERROR
    feasible_all = balance_feasible and kit_ok

    if feasible_all:
        _spec_alert(
            "success", "check_circle",
            f"**Все нормативы выполняются.** "
            f"КИТ = {kit_v:.3f} (≤ {kit_max})"
            f"  ·  Резерв территории: {fmt_int(result.balance.surplus)} м²"
        )
    elif not kit_ok:
        # КИТ ПЗЗ превышает потолок — основная причина (часто из-за выключенного ДПТ)
        _spec_alert(
            "error", "error",
            f"**КИТ ПЗЗ ({kit_v:.3f}) превышает нормативный потолок ({kit_max}).** "
            f"Жилой дом при выбранных параметрах не «помещается» в норматив. "
            f"См. рекомендации ниже."
        )
    else:
        # v0.8.7: surplus > 0, но feasible=False → нарушен норматив озеленения
        # квартала. Это самая частая ловушка на малых кварталах.
        bal = result.balance
        if bal.surplus >= 0 and bal.greening_actual < bal.greening_required - 1e-3:
            deficit = bal.greening_required - bal.greening_actual
            _spec_alert(
                "error", "error",
                f"**Норматив озеленения квартала не выполняется.** "
                f"Требуется ≥ {fmt_int(bal.greening_required)} м² "
                f"(25% от квартала), факт {fmt_int(bal.greening_actual)} м² — "
                f"дефицит {fmt_int(deficit)} м². "
                f"Резерв территории есть ({fmt_int(bal.surplus)} м²), но "
                f"норматив не пускает увеличивать жильё."
            )
            _spec_alert(
                "info", "lightbulb",
                "Возможные действия: (1) включите **ЗНОП** в левой колонке "
                "— добавит озеленение по нормативу; "
                "(2) увеличьте площадь квартала; "
                "(3) отключите **«Соблюдать норматив 25% озеленения»** — "
                "если зелень компенсируется вне границ территории."
            )
        else:
            _spec_alert(
                "error", "error",
                f"**Дефицит баланса территории.** КИТ = {kit_v:.3f}"
                f"  ·  Не хватает: {fmt_int(-bal.surplus)} м²"
            )

    if result.limiting_factor:
        st.caption(f"**Ограничивающий фактор:** {result.limiting_factor}")
    # v0.8.6: префиксы [CODE] из warning_codes.WC прячем от пользователя —
    # они служат для машинной фильтрации (Optuna feasibility, тесты).
    from urban_model.calculations.warning_codes import strip_code
    # v0.11.0 (#3): не повторять идентичные предупреждения (напр. одинаковая
    # вместимость ДОО и СОШ даёт текстуально совпадающие сообщения).
    _seen_warn: set[str] = set()
    for w in result.warnings:
        _txt = strip_code(w)
        if _txt in _seen_warn:
            continue
        _seen_warn.add(_txt)
        _spec_alert("warning", "info", _txt)

    # v0.10.9 (#1): когда ограничивает норматив плотности 450 чел/га и при этом
    # есть заметный резерв территории — объясняем, что земля высвобождена
    # плотностью (её нельзя застроить жильём) и как её использовать.
    dens = result.density_chel_per_ga
    surplus = result.balance.surplus
    site_a = result.balance.site_area or 1.0
    density_limited = (
        dens.normative and dens.value is not None
        and dens.value >= dens.normative - 1.0
        and surplus > site_a * 0.02
    )
    if density_limited:
        _spec_alert(
            "info", "lightbulb",
            f"**Резерв обусловлен нормативом плотности.** Население достигло "
            f"потолка {dens.normative:.0f} чел/га — больше жилья разместить нельзя "
            f"без превышения плотности. Резерв {fmt_int(surplus)} м² уходит в "
            f"озеленение/двор. Чтобы задействовать его под застройку: разместите "
            f"**нежилые объекты** (офис/коммерция во вкладке «Параметры» → "
            f"«Дополнительные объекты») — они занимают землю, но не дают населения; "
            f"либо отключите норматив плотности (для экспериментов)."
        )


# ---------------------------------------------------------------------------
# KPI-карточки
# ---------------------------------------------------------------------------

def _kpi_floors_label(result: TEPResult, options=None) -> str:
    """Подпись этажности с учётом кластеров (дубль _floors_label из optimizer,
    чтобы не вводить импорт-связность). При зонах: «9 / 21 эт. (ср. 15.0)»."""
    if result.floor_clusters_detail:
        fl = " / ".join(str(d["floors"]) for d in result.floor_clusters_detail)
        eff = result.effective_floors or 0.0
        return f"{fl} эт. (ср. {eff:.1f})"
    if options is not None and getattr(options, "floors", None):
        return f"{int(options.floors)} эт."
    if result.effective_floors:
        return f"{result.effective_floors:.0f} эт."
    return "—"


def _buckets_delta(formula_str: str | None, total: int) -> str | None:
    """«N объектов по lo–hi мест» из formula-строки ДОО/СОШ (для delta метрики)."""
    if not formula_str or total == 0:
        return None
    m = re.search(r'\[([^\]]+)\]', formula_str)
    if not m:
        return None
    try:
        vals = [int(x.strip()) for x in m.group(1).split(",") if x.strip()]
    except ValueError:
        return None
    n = len(vals)
    if n == 0:
        return None
    lo, hi = min(vals), max(vals)
    per_str = f"{lo} мест" if lo == hi else f"{lo}–{hi} мест"
    ending = "объект" if n == 1 else ("объекта" if 2 <= n <= 4 else "объектов")
    return f"{n} {ending} по {per_str}"


def render_main_kpi_grid(result: TEPResult, options=None) -> None:
    """Единый KPI-блок (v0.12.16): одинаков на «Расчёте» («Основные показатели»)
    и в «Оптимизации» («База»).

    2 ряда по 5 метрик:
      КИТ (ПЗЗ) · Население · Площадь квартир · Этажность · Эконом-индекс
      ДОО · СОШ · Доп. образование · Парковки · ЗНОП
    + (при наличии экономики) ряд «Выход жилья / Соц. нагрузка».
    Технический expander с сырыми баллами не выводится (по запросу).
    """
    kit_max = result.kit_normative_max.value or 0

    # ── Ряд 1 ────────────────────────────────────────────────────────────
    # v0.12.28.2: ЗНОП перенесён в ряд 1 (5-я ячейка), освободив место под
    # Поликлинику в ряду 2.
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric(
        "КИТ (ПЗЗ)", f"{result.kit.value:.3f}",
        help=(
            "КИТ по ПЗЗ СПб = площадь квартир / ЗУ жилой застройки. "
            f"Норм. потолок: {kit_max} (ДПТ: {'да' if kit_max == 2.5 else 'нет'})"
        ),
    )
    if result.floor_clusters_detail:
        _zk = " · ".join(
            f"**{d['label']}**: {d['kit']:.3f}" for d in result.floor_clusters_detail
        )
        c1.caption(f":material/apartment: КИТ зон: {_zk}")
    c2.metric(
        "Население", f"{fmt_int(result.population.value)} чел.",
        help="Жилищная обеспеченность: 28 м²/чел (НГП СПб).",
    )
    c3.metric("Площадь квартир", fmt_m2(result.apartments_area.value))
    c4.metric("Этажность", _kpi_floors_label(result, options))
    znop_pp = result.znop_per_person.value or 0
    znop_area = int(result.znop_area.value or 0)
    c5.metric(
        "ЗНОП", f"{znop_area:,} м²".replace(",", " ") if znop_area > 0 else "—",
        delta=f"{znop_pp:.1f} м²/чел" if znop_pp > 0 else None, delta_color="off",
        help="Общая площадь ЗНОП и норма на жителя.",
    )
    if result.floor_clusters_detail and any(
        "znop_per_person" in d for d in result.floor_clusters_detail
    ):
        _zz = " · ".join(
            f"**{d['label']}**: {d.get('znop_per_person', 0):.0f}"
            for d in result.floor_clusters_detail
        )
        c5.caption(f":material/park: ЗНОП зон, м²/чел: {_zz}")

    # ── Ряд 2 (соцобъекты + парковки) ────────────────────────────────────
    c6, c7, c8, c9, c10 = st.columns(5)
    kg_total = int(result.kindergarten_places_accepted.value or 0)
    c6.metric(
        "ДОО", f"{kg_total} мест" if kg_total > 0 else "—",
        delta=_buckets_delta(result.kindergarten_places_accepted.formula, kg_total),
        delta_color="off", help="Принятая вместимость и число объектов ДОО.",
    )
    sch_total = int(result.school_places_accepted.value or 0)
    c7.metric(
        "СОШ", f"{sch_total} мест" if sch_total > 0 else "—",
        delta=_buckets_delta(result.school_places_accepted.formula, sch_total),
        delta_color="off", help="Принятая вместимость и число объектов СОШ.",
    )
    ae_total = int(result.add_education_places_accepted.value or 0)
    ae_place = (
        "встроенное (ВПП)" if getattr(result, "add_education_built_in", False)
        else "отд. стоящее"
    )
    c8.metric(
        "Доп. образование", f"{ae_total} мест" if ae_total > 0 else "—",
        delta=(ae_place if ae_total > 0 else None), delta_color="off",
        help="Организации доп. образования (ВРИ 3.5.1).",
    )
    poly_v = int(getattr(result, "polyclinic_visits_accepted", None).value or 0) \
        if getattr(result, "polyclinic_visits_accepted", None) is not None else 0
    poly_place = (
        "ВПП (офис врача)" if getattr(result, "polyclinic_built_in", False)
        else "отд. стоящая"
    )
    c9.metric(
        "Поликлиника", f"{poly_v} посещ." if poly_v > 0 else "—",
        delta=(poly_place if poly_v > 0 else None), delta_color="off",
        help="Амбулаторно-поликлинические (ВРИ 3.4.1): посещений/смену.",
    )
    op_pl = int(result.parking_open_places.value or 0)
    ml_pl = int(result.parking_multilevel_places.value or 0)
    ug_pl = int(result.parking_underground_places.value or 0)
    styl_pl = int(getattr(result, "parking_stylobate_places", None).value or 0) \
        if getattr(result, "parking_stylobate_places", None) is not None else 0
    total_pl = int(result.parking_required_places.value or 0)
    _parts = []
    if op_pl:   _parts.append(f"откр. {op_pl}")
    if ml_pl:   _parts.append(f"многоур. {ml_pl}")
    if styl_pl: _parts.append(f"стилоб. {styl_pl}")
    if ug_pl:   _parts.append(f"подз. {ug_pl}")
    c10.metric(
        "Парковки", f"{total_pl} м/м" if total_pl > 0 else "—",
        delta=" · ".join(_parts) or None, delta_color="off",
        help="Всего машино-мест и разбивка по типам.",
    )

    # ── Ряд 3: экономика (выход жилья / соц. нагрузка) ───────────────────
    if result.economy is not None:
        e = result.economy
        _social_cost = (
            e.cost.kindergarten + e.cost.school + e.cost.social_parking
            + getattr(e.cost, "add_education", 0.0)
        )
        _has_social = _social_cost > 0.5 or e.revenue.social_compensation > 0.5
        # v0.12.23: ряд экономики — та же 5-колоночная сетка, что ряды 1 и 2
        # (первые 3 ячейки), чтобы метрики стояли РОВНО под колонками сверху,
        # а не «разъезжались» по третям ширины (st.columns(3)).
        d1, d2, d3, _d4, _d5 = st.columns(5)
        d1.metric(
            "Эконом-индекс", f"{e.economy_index:.0f} / 100",
            help=(
                "100 × выручка / себестоимость. 100 = окупаемость; выше — "
                "эффективнее. Стабильный показатель модели."
            ),
        )
        d2.metric(
            "Выход жилья",
            f"{e.sellable_ratio * 100:.0f}%" if e.sellable_ratio else "—",
            help="Площадь квартир / общая GFA — доля продаваемого жилья.",
        )
        if _has_social:
            d3.metric(
                "Соц. нагрузка",
                f"{-e.net_social_burden:+,.0f}".replace(",", " "),
                delta=("в минус" if e.net_social_burden > 0 else "плюс"),
                delta_color=("inverse" if e.net_social_burden > 0 else "normal"),
                help="Себестоимость ДОО/СОШ/доп.обр/соц.парковок за вычетом "
                     "компенсации города (условные баллы).",
            )
        else:
            d3.metric(
                "ROI", f"{e.roi * 100:.1f}%" if e.cost.total > 0 else "—",
                help="profit / cost (условные баллы).",
            )

    # ── Очерёдность (v0.15.1): подпись под сеткой, если очереди заданы ──
    ph = getattr(result, "phasing", None)
    if ph is not None and ph.stages:
        _shares = "/".join(f"{s.share * 100:.0f}" for s in ph.stages)
        _ok = all(s.is_ok for s in ph.stages)
        _status = ("обеспеченность выдержана" if _ok
                   else "⚠ есть дефициты соцобъектов")
        _auto = " · авто" if getattr(ph, "mode", "") == "auto" else ""
        _n_lots = max((s.lot for s in ph.stages), default=1)
        _lots = f" · {_n_lots} лот(а)" if _n_lots > 1 else ""
        st.caption(
            f":material/stairs: Очерёдность: {len(ph.stages)} очереди "
            f"({_shares}%{_auto}){_lots} — {_status}. "
            f"Детали — в «Очерёдность застройки» ниже."
        )
    elif ph is not None and getattr(ph, "note", None):
        # v0.15.4: авто-режим решил не делить (единственный корпус соцобъектов).
        st.caption(f":material/stairs: Очерёдность (авто): {ph.note}")


def render_kpi(result: TEPResult, *, scenario_default_name: str | None = None,
               options=None) -> None:
    """KPI-блок с возможностью встроить кнопки «Добавить в сравнение» и xlsx.

    Если scenario_default_name задан — внутри того же блока (под линией
    разделителя) рисуются actions: text_input + добавить/xlsx/альбом.
    `options` (CalculationOptions) нужен для слайда «Исходные условия» альбома.
    """
    # === Основные показатели в едином блоке с возможностью копирования ===
    with st.container(border=True):
        # v0.9.20: заголовок + компактная popover-сводка на одной строке.
        # Раньше expander «📋 Сводка (с кнопкой копирования)» занимал всю
        # ширину, кнопка копирования была далеко справа. Теперь popover
        # открывается прямо под кнопкой — кнопка копирования рядом.
        header_col, copy_col = st.columns([10, 2])
        with header_col:
            st.markdown("##### :material/bar_chart: Основные показатели")

        # Развёрнутая сводка (с разбивкой парковок/ДОО/СОШ по типам/объектам)
        import re as _re
        kit_v = result.kit.value or 0
        kit_max = result.kit_normative_max.value or 0
        pop_v = int(result.population.value or 0)
        apt_v = result.apartments_area.value or 0
        surplus_v = result.balance.surplus
        kg_total = int(result.kindergarten_places_accepted.value or 0)
        sch_total = int(result.school_places_accepted.value or 0)
        op_pl = int(result.parking_open_places.value or 0)
        ml_pl = int(result.parking_multilevel_places.value or 0)
        ug_pl = int(result.parking_underground_places.value or 0)
        total_pl = int(result.parking_required_places.value or 0)
        znop_pp = result.znop_per_person.value or 0
        znop_area = int(result.znop_area.value or 0)

        # Разбивка ДОО/СОШ по объектам — из formula
        def _buckets_text(formula: str | None) -> str:
            if not formula:
                return ""
            m = _re.search(r'\[([^\]]+)\]', formula)
            if not m:
                return ""
            try:
                vals = [int(x.strip()) for x in m.group(1).split(",") if x.strip()]
            except ValueError:
                return ""
            n = len(vals)
            if n == 0:
                return ""
            lo, hi = min(vals), max(vals)
            cap = f"{lo}" if lo == hi else f"{lo}–{hi}"
            ending = "объект" if n == 1 else ("объекта" if 2 <= n <= 4 else "объектов")
            return f" ({n} {ending} по {cap} мест)"

        kg_breakdown = _buckets_text(result.kindergarten_places_accepted.formula)
        sch_breakdown = _buckets_text(result.school_places_accepted.formula)

        summary_text = (
            f"КИТ (ПЗЗ): {kit_v:.3f} (потолок {kit_max})\n"
            f"Население: {pop_v:,} чел.\n"
            f"Площадь квартир: {apt_v:,.0f} м²\n"
            f"Резерв баланса: {surplus_v:+,.0f} м²\n"
            f"\n"
            f"ДОО: {kg_total} мест{kg_breakdown}\n"
            f"СОШ: {sch_total} мест{sch_breakdown}\n"
            f"\n"
            f"Парковки всего: {total_pl} м/м\n"
            f"  — открытые наземные: {op_pl} м/м\n"
            f"  — многоуровневые: {ml_pl} м/м\n"
            f"  — подземные: {ug_pl} м/м\n"
            f"\n"
            f"ЗНОП: {znop_area:,} м² ({znop_pp:.1f} м²/чел)"
        ).replace(",", " ")
        # Инженерная инфраструктура (v0.12)
        if result.engineering is not None and result.engineering.objects:
            _eng = result.engineering
            _eng_parts = ", ".join(
                f"{o.label.split(' (')[0]} ×{o.count}"
                for o in _eng.objects if o.count > 0
            )
            summary_text += (
                f"\nИнженерия (ЗУ в балансе {_eng.plot_in_balance:,.0f} м²): {_eng_parts}"
            ).replace(",", " ")
        # v0.9.28.2: разбивка территорий по зонам (если заданы кластеры).
        if result.floor_clusters_detail:
            _total_a = sum(d["area_m2"] for d in result.floor_clusters_detail) or 1.0
            summary_text += (
                f"\n\n— Кластеры этажности —\n"
                f"Средневзвеш. этажность: {result.effective_floors:.1f}\n"
            )
            for d in result.floor_clusters_detail:
                _sh = d["area_m2"] / _total_a * 100
                summary_text += (
                    f"  {d['label']}: {d['area_m2']:,.0f} м² ({_sh:.0f}%), "
                    f"{d['floors']} эт., КИТ {d['kit']:.3f}, "
                    f"квартиры {d['apartments_area']:,.0f} м²\n"
                ).replace(",", " ")
            # ЗНОП и прочие территории распределяются пропорционально площади зон
            znop_total = int(result.znop_area.value or 0)
            if znop_total > 0:
                znop_split = " / ".join(
                    f"{d['label']} {znop_total * d['area_m2'] / _total_a:,.0f}".replace(",", " ")
                    for d in result.floor_clusters_detail
                )
                summary_text += (
                    f"  ЗНОП по зонам (пропорц.): {znop_split} м²\n"
                )

        if result.economy is not None:
            e = result.economy
            summary_text += (
                f"\n\n— Экономика —\n"
                f"Эконом-индекс: {e.economy_index:.0f} / 100 (100 = окупаемость)\n"
                f"Условная прибыль: {e.profit:+,.0f} баллов\n"
            ).replace(",", " ")
            _soc = e.cost.kindergarten + e.cost.school + e.cost.social_parking
            if _soc > 0.5 or e.revenue.social_compensation > 0.5:
                summary_text += (
                    f"Без соц. нагрузки: {e.profit_before_social:+,.0f}\n"
                    f"Соц. нагрузка (ДОО/СОШ): {-e.net_social_burden:+,.0f}\n"
                ).replace(",", " ")
            summary_text += (
                f"Маржа: {e.margin * 100:.1f}%; ROI: {e.roi * 100:.1f}%"
            )

        with copy_col:
            with st.popover(":material/description: Сводка", use_container_width=True):
                # st.code рисует блок со ВСТРОЕННОЙ кнопкой копирования
                # в правом верхнем углу. Streamlit-нативный механизм.
                st.code(summary_text, language=None)

        # v0.9.21: layout «KPI слева 60% + donut справа 40%» — пользователь
        # хочет видеть распределение территории на одном уровне с метриками.
        # Все ряды st.columns(4) создаются ВНУТРИ kpi_col; затем
        # c1.metric/c5.metric/... рисуют в этой колонке. Donut —
        # отдельно в donut_col, на одном вертикальном уровне.
        # v0.10.18: KPI занимают всю ширину, treemap «Баланс территории»
        # рендерится отдельной секцией НИЖЕ (как в утверждённом макете).
        # v0.12.16: единый KPI-блок (render_main_kpi_grid) — идентичен «Базе»
        # на вкладке «Оптимизация». Метрики — слева (kpi_col), treemap
        # «Баланс территории» — отдельной секцией ниже (donut_col).
        kpi_col = st.container()
        donut_col = st.container()
        with kpi_col:
            render_main_kpi_grid(result, options)

        # v0.10.18: donut/treemap — теперь отдельной секцией под KPI,
        # с собственным заголовком в стиле «Спецификация».
        with donut_col:
            st.markdown("##### :material/balance: Баланс территории")
            _render_balance_bar(result)

        # (Экономический ряд «Выход жилья / Соц. нагрузка» — внутри
        #  render_main_kpi_grid; технический expander убран по запросу v0.12.16.)

        # === Inline-actions: «Добавить в сравнение» + xlsx (внутри блока) ===
        if scenario_default_name is not None:
            st.markdown("---")
            _render_actions_inline(result, scenario_default_name, options=options)


# ---------------------------------------------------------------------------
# Утилита: красивая строка-таблица «label / значение / статус»
# ---------------------------------------------------------------------------

def _show_rows(rows: list[dict]) -> None:
    df = pd.DataFrame(rows)
    # Скрываем колонки если все пустые
    if (df["Источник"] == "").all():
        df = df.drop(columns=["Источник"])
    if (df["Формула"] == "").all():
        df = df.drop(columns=["Формула"])
    st.dataframe(df, hide_index=True, use_container_width=True)


# ---------------------------------------------------------------------------
# Раскрывающиеся секции — детализация
# ---------------------------------------------------------------------------

def render_details(result: TEPResult) -> None:
    # Топ-таблицы (Жильё…Баланс…Кластеры) — из ЕДИНОГО билдера
    # (export/variant_tables): тот же источник, что у альбома концепции, поэтому
    # таблицы Базы и вариантов гарантированно идентичны.
    from urban_model.export.variant_tables import build_variant_table_blocks
    for blk in build_variant_table_blocks(result):
        with st.expander(f":material/{blk.icon}: {blk.title}", expanded=False):
            if blk.columns is None:
                _show_rows(blk.rows)
            else:
                st.dataframe(
                    pd.DataFrame(blk.rows, columns=blk.columns),
                    hide_index=True, use_container_width=True,
                )
            for _note in blk.notes:
                st.caption(_note)

    # 💰 Экономика
    if result.economy is not None:
        with st.expander(":material/payments: Экономика (детализация)", expanded=False):
            st.caption(
                "Все значения — в условных **баллах выгодности проекта** "
                "(безразмерный индикатор для сравнения вариантов). "
                "Стартовые коэффициенты — `configs/spb.yaml`, секция `economy:`. "
                "Парковки с нулевыми местами в строки таблицы не включены."
            )
            e = result.economy
            cb = e.cost
            rb = e.revenue

            # v0.9.17: скрываем строки с 0 — таблица становится короче и
            # сосредоточенной на актуальных статьях расходов/доходов.
            # v0.9.18: переименовано в `_eco_row` чтобы не конфликтовать
            # с глобальной `_row(label, field, fmt_fn, ...)` из этого же
            # модуля (Python считает обе локальными по правилу scoping).
            def _eco_row(name: str, val: float) -> dict | None:
                if abs(val) < 0.5:
                    return None
                return {"Статья": name, "Баллы": f"{val:,.0f}".replace(",", " ")}

            st.markdown("**Себестоимость**")
            cost_rows = [
                _eco_row("Жильё", cb.residential),
                _eco_row("ВПП (коммерция)", cb.vpp),
                _eco_row("ДОО (детские сады)", cb.kindergarten),
                _eco_row("СОШ (школы)", cb.school),
                _eco_row("Парковки открытые", cb.parking_open),
                _eco_row("Парковки многоуровневые", cb.parking_multilevel),
                _eco_row("Парковки подземные", cb.parking_underground),
                _eco_row("Парковки стилобатные", cb.parking_stylobate),
                _eco_row("Парковки соцобъектов", cb.social_parking),
                _eco_row("Спортивные сооружения", cb.sport),
                _eco_row("Пользовательские объекты", cb.custom_objects),
                _eco_row("Инженерная инфраструктура", cb.engineering),
                {"Статья": "— Σ зданий и сооружений", "Баллы": f"{cb.shell_total:,.0f}".replace(",", " ")},
                _eco_row("Сети", cb.networks),
                _eco_row("Благоустройство", cb.landscaping),
                _eco_row("Проектирование", cb.design),
                _eco_row("Непредвиденные", cb.contingency),
                _eco_row("Земля + ТУ + снос", cb.fixed),
                {"Статья": "Итого себестоимость", "Баллы": f"{cb.total:,.0f}".replace(",", " ")},
            ]
            cost_rows = [r for r in cost_rows if r is not None]
            st.dataframe(pd.DataFrame(cost_rows), hide_index=True, use_container_width=True)

            st.markdown("**Выручка**")
            rev_rows = [
                _eco_row("Жильё (м² квартир)", rb.residential),
                _eco_row("Парковки открытые", rb.parking_open),
                _eco_row("Парковки многоуровневые", rb.parking_multilevel),
                _eco_row("Парковки подземные", rb.parking_underground),
                _eco_row("Парковки стилобатные", rb.parking_stylobate),
                _eco_row("ВПП коммерческая", rb.vpp_commercial),
                _eco_row("Пользовательские (коммерческие)", rb.custom_commercial),
                _eco_row("Компенсация ДОО/СОШ городом", rb.social_compensation),
                {"Источник": "Итого выручка", "Баллы": f"{rb.total:,.0f}".replace(",", " ")},
            ]
            rev_rows = [
                {"Источник": r.get("Статья", r.get("Источник", "")), "Баллы": r["Баллы"]}
                for r in rev_rows if r is not None
            ]
            st.dataframe(pd.DataFrame(rev_rows), hide_index=True, use_container_width=True)

            # v0.9.14: социальная нагрузка отдельным блоком — наглядно
            # показывает, сколько «стоит» застройщику социалка после
            # компенсации города и какой была бы прибыль без неё.
            social_cost = cb.kindergarten + cb.school + cb.social_parking
            if social_cost > 0.5 or rb.social_compensation > 0.5:
                st.markdown("**Социальная нагрузка**")
                sc1, sc2, sc3 = st.columns(3)
                sc1.metric(
                    "Себестоимость соц.",
                    f"{social_cost:,.0f}".replace(",", " "),
                    help="ДОО + СОШ + парковки соцобъектов",
                )
                sc2.metric(
                    "Компенсация города",
                    f"{rb.social_compensation:,.0f}".replace(",", " "),
                    help="Выкуп / КОТ / бюджетные субсидии",
                )
                sc3.metric(
                    "Чистая нагрузка",
                    f"{e.net_social_burden:+,.0f}".replace(",", " "),
                    delta=("минус" if e.net_social_burden > 0 else "плюс"),
                    delta_color=("inverse" if e.net_social_burden > 0 else "normal"),
                    help="Себестоимость соц. − компенсация. >0 — соцобъекты в убыток.",
                )

            st.markdown("**Метрики**")
            mc1, mc2, mc3 = st.columns(3)
            mc1.metric(
                "Эконом-индекс",
                f"{e.economy_index:.0f} / 100",
                help="100 × выручка / себестоимость. 100 = окупаемость; выше — эффективнее.",
            )
            mc2.metric(
                "Условная прибыль",
                f"{e.profit:+,.0f}".replace(",", " "),
                help="Безразмерный индикатор (revenue − cost), баллы.",
            )
            mc3.metric(
                "Прибыль без соц.",
                f"{e.profit_before_social:+,.0f}".replace(",", " "),
                help="Прибыль проекта без социальных обязательств "
                     "(profit + чистая соц. нагрузка). «Чистый» девелопмент.",
            )

            mc4, mc5, mc6 = st.columns(3)
            mc4.metric("Маржа", f"{e.margin * 100:.1f}%" if rb.total > 0 else "—",
                       help="profit / revenue")
            mc5.metric("ROI", f"{e.roi * 100:.1f}%" if cb.total > 0 else "—",
                       help="profit / cost")
            mc6.metric(
                "Выход жилья",
                f"{e.sellable_ratio * 100:.0f}%" if e.sellable_ratio > 0 else "—",
                help="Площадь квартир / общая GFA — доля продаваемого жилья от всей застройки.",
            )
            # v0.10.20 (аудит): интерпретация sellable_ratio.
            _sr = e.sellable_ratio
            if _sr > 0:
                if _sr < 0.70:
                    _sr_txt = "слабая эффективность"
                elif _sr < 0.75:
                    _sr_txt = "погранично"
                elif _sr <= 0.82:
                    _sr_txt = "норма"
                else:
                    _sr_txt = "высокая"
                mc6.caption(_sr_txt)

    # 📋 Полный аудит
    with st.expander(":material/fact_check: Полный аудит (все TEP-поля + источники)", expanded=False):
        df = results_to_audit_dataframe([("Текущий", result)])
        # v0.11.0: колонку «формула» в экранной таблице не показываем (длинная,
        # засоряет). Полные формулы доступны в Excel-выгрузке аудита.
        _df_ui = df.drop(columns=[c for c in df.columns if c == "формула"])
        st.dataframe(_df_ui, hide_index=True, use_container_width=True)
        st.caption("Полные формулы расчёта — в Excel-выгрузке (кнопка «Скачать xlsx»).")


def _squarify(values: list[float], x: float, y: float, w: float, h: float
              ) -> list[tuple[float, float, float, float]]:
    """Простой squarified-treemap (slice-and-dice вариант, v0.9.26).

    Возвращает список (x, y, w, h) для каждого values[i]. Алгоритм:
    отсортированный по убыванию массив рекурсивно делится на две группы
    (первая ≥ половины суммарной площади), каждая занимает соответствующую
    половину прямоугольника. Деление по большему измерению.

    Не оптимизирует aspect-ratio до конца как Брюлс, но даёт визуально
    приемлемый результат для типового набора 5-10 компонентов.
    """
    n = len(values)
    if n == 0:
        return []
    if n == 1:
        return [(x, y, w, h)]
    total = sum(values)
    if total <= 0:
        # все нули — равные доли
        return [(x, y, w / n, h)] * n
    half = total / 2.0
    cum = 0.0
    split = 1
    for i, v in enumerate(values):
        cum += v
        if cum >= half:
            split = i + 1
            break
    left_vals = values[:split]
    right_vals = values[split:]
    left_sum = sum(left_vals)
    if w >= h:
        # делим по горизонтали (left | right)
        w_left = w * left_sum / total
        return (
            _squarify(left_vals, x, y, w_left, h)
            + _squarify(right_vals, x + w_left, y, w - w_left, h)
        )
    else:
        # делим по вертикали (top / bottom)
        h_top = h * left_sum / total
        return (
            _squarify(left_vals, x, y, w, h_top)
            + _squarify(right_vals, x, y + h_top, w, h - h_top)
        )


def _render_balance_bar(result: TEPResult) -> None:
    """Treemap-диаграмма баланса территории (v0.9.26).

    Прямоугольные плитки пропорционально площади компонентов. Размер
    плитки сразу показывает «куда уходит территория». Подписи внутри
    крупных плиток (≥6%), мелкие — только tooltip.
    """
    import altair as alt
    b = result.balance
    site_area = b.site_area
    if site_area <= 0:
        return

    # v0.10.18: подписи сокращены — мелкие плитки треемапа не вмещали
    # длинные тексты, обрезались или не показывались. Короче → читаемее.
    pretty = {
        "housing_lot": "ЗУ жилья",
        "kindergarten_plot": "ДОО",
        "school_plot": "СОШ",
        "sport_facilities": "Спорт. пл.",
        "social_parking_plot": "Р. соц.",
        "add_education_plot": "Доп. обр.",
        "polyclinic_plot": "Поликлиника",
        "znop": "ЗНОП",
        "intra_quarter_driveways": "Проезды",
        "parking_multilevel": "Р. многоур.",
        "custom_objects": "Доп. об.",
        "engineering_plot": "Инж. инфр.",
    }
    # Цвета по типам — деловая палитра
    colors = {
        "ЗУ жилья":                "#4A90E2",
        "ДОО":                     "#F5A623",
        "СОШ":                     "#E94B3C",
        "Спорт. пл.":              "#7ED321",
        "Р. соц.":                 "#9B9B9B",
        "Доп. обр.":               "#00ACC1",
        "Поликлиника":             "#D0021B",
        "ЗНОП":                    "#417505",
        "Проезды":                 "#B8B8B8",
        "Р. многоур.":             "#50E3C2",
        "Доп. об.":                "#BD10E0",
        "Инж. инфр.":              "#8B5E3C",
        "Резерв":                  "#D5E8D4",
    }
    rows = []
    for name, val in sorted(b.components.items(), key=lambda kv: -kv[1]):
        if val <= 0:
            continue
        label = pretty.get(name, name)
        pct = val / site_area * 100
        rows.append({"Компонент": label, "Площадь": val, "Доля": f"{pct:.1f}%"})
    surplus = max(0.0, b.surplus)
    if surplus > 0:
        rows.append({
            "Компонент": "Резерв",
            "Площадь": surplus,
            "Доля": f"{surplus/site_area*100:.1f}%",
        })

    if not rows:
        return

    df = pd.DataFrame(rows)
    df["Pct"] = df["Площадь"] / site_area * 100
    domain = list(colors.keys())
    range_ = [colors[d] for d in domain]

    # v0.9.26: treemap вместо donut. Прямоугольные «плитки» размером
    # пропорционально площади компонента — нагляднее для сравнения
    # 9+ категорий, чем сектора круга. Реализация: squarified-treemap
    # алгоритм inline (без новых зависимостей), рисуем через mark_rect.
    sorted_rows = df.sort_values("Площадь", ascending=False).to_dict("records")
    values = [r["Площадь"] for r in sorted_rows]
    rects = _squarify(values, 0.0, 0.0, 100.0, 60.0)
    tree_rows = []
    for r, (x, y, w, h) in zip(sorted_rows, rects):
        # Подпись внутри плитки: название+% для крупных (≥4%),
        # только % для средних (2–4%), пусто для совсем мелких.
        if r["Pct"] >= 4:
            label = f"{r['Компонент']}\n{r['Pct']:.0f}%"
        elif r["Pct"] >= 2:
            label = f"{r['Pct']:.0f}%"
        else:
            label = ""
        tree_rows.append({
            "Компонент": r["Компонент"],
            "Площадь": r["Площадь"],
            "Pct": r["Pct"],
            "Доля": r["Доля"],
            "x": x, "y": y, "x2": x + w, "y2": y + h,
            "cx": x + w / 2, "cy": y + h / 2,
            "label": label,
        })
    tree_df = pd.DataFrame(tree_rows)

    rect = (
        alt.Chart(tree_df)
        .mark_rect(stroke="white", strokeWidth=2)
        .encode(
            x=alt.X("x:Q", axis=None, scale=alt.Scale(domain=[0, 100])),
            y=alt.Y("y:Q", axis=None, scale=alt.Scale(domain=[0, 60])),
            x2="x2:Q",
            y2="y2:Q",
            color=alt.Color(
                "Компонент:N",
                scale=alt.Scale(domain=domain, range=range_),
                legend=None,
            ),
            tooltip=[
                alt.Tooltip("Компонент:N"),
                alt.Tooltip("Площадь:Q", title="м²", format=",.0f"),
                alt.Tooltip("Доля:N"),
            ],
        )
    )
    labels = (
        alt.Chart(tree_df)
        .mark_text(
            color="white", fontSize=12, fontWeight=600,
            lineBreak="\n", align="center", baseline="middle",
        )
        .encode(
            x=alt.X("cx:Q", scale=alt.Scale(domain=[0, 100])),
            y=alt.Y("cy:Q", scale=alt.Scale(domain=[0, 60])),
            text="label:N",
        )
    )
    chart = (rect + labels).properties(height=340).configure_view(strokeWidth=0)
    # v0.10.18: внутренний заголовок убран — секционный «Баланс территории»
    # уже стоит выше (см. render_kpi). Не дублируем.
    st.altair_chart(chart, use_container_width=True)


# ---------------------------------------------------------------------------
# Кнопки внизу: добавить в сравнение, скачать xlsx
# ---------------------------------------------------------------------------

def _result_sig(result: TEPResult) -> tuple:
    """Лёгкая сигнатура результата — меняется только при изменении расчёта.
    Используется для мемоизации тяжёлых отчётов (не пересобирать на каждый ре-рендер)."""
    return (
        round(result.kit.value or 0.0, 4),
        round(result.apartments_area.value or 0.0, 1),
        round(result.balance.surplus, 1),
        round(result.economy.profit, 1) if result.economy is not None else None,
        round(result.effective_floors or 0.0, 2),
    )


def _variant_xlsx_bytes(name: str, result: TEPResult, options) -> bytes:
    """Комплексный «паспорт варианта» (xlsx) → байты для download_button.

    Лёгкий (без matplotlib), мемоизация не нужна — генерится быстро.
    """
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        build_variant_xlsx(name, result, options, tmp_path)
        with open(tmp_path, "rb") as f:
            return f.read()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _variant_album_bytes(name: str, result: TEPResult, options) -> bytes:
    """PPTX-альбом по варианту с мемоизацией по (имя + сигнатура расчёта +
    опции). Пересобирается только при изменении расчёта — download_button
    отдаёт готовые байты в ОДИН клик."""
    try:
        opt_sig = hash(options.model_dump_json()) if options is not None else 0
    except Exception:  # noqa: BLE001
        opt_sig = 0
    sig = (name, _result_sig(result), opt_sig)
    if st.session_state.get("_album_sig") != sig:
        from urban_model.export import build_variant_album
        with tempfile.NamedTemporaryFile(suffix=".pptx", delete=False) as tmp_v:
            vpath = tmp_v.name
        try:
            build_variant_album(name, result, options, vpath)
            with open(vpath, "rb") as f:
                st.session_state["_album_bytes"] = f.read()
        finally:
            try:
                os.unlink(vpath)
            except OSError:
                pass
        st.session_state["_album_sig"] = sig
    return st.session_state["_album_bytes"]


def _render_actions_inline(result: TEPResult, default_name: str, options=None) -> None:
    """Inline-actions: имя + «Добавить в сравнение», ниже — выгрузки рядом."""
    # v0.10.9 (#5): всё поджато влево — имя + кнопки кластеризованы в левой
    # части, чтобы не «уезжали» вправо и не терялись из виду.
    # v0.10.10 (#3): кнопки заполняют свои узкие колонки (use_container_width) —
    # стоят вплотную друг к другу слева, без разрывов и без растяжки на весь экран.
    # v0.10.15: единая сетка действий. Имя сценария занимает ширину двух
    # кнопок (3+3 = 6 ед.), под ним — три кнопки ОДИНАКОВОЙ ширины (по 3 ед.)
    # с хвостовым спейсером (3 ед.), чтобы не растягиваться на весь экран.
    # Кнопки привязаны по ширине к полю ввода — единообразная сетка.
    name_col, _name_sp = st.columns([6, 3])
    with name_col:
        # v0.9.29: text_input с key сохраняет значение и игнорирует value=
        # после первого показа — поэтому при смене параметров пушим авто-имя.
        if st.session_state.get("_scenario_name_auto") != default_name:
            st.session_state["scenario_name_input"] = default_name
            st.session_state["_scenario_name_auto"] = default_name
        scenario_name = st.text_input(
            "Имя сценария для сравнения",
            placeholder="Введите название расчёта",
            key="scenario_name_input",
            label_visibility="collapsed",
        )

    b_add, b_xlsx, b_doc, _btn_sp = st.columns([3, 3, 3, 3])
    with b_add:
        if st.button(":material/add: В сравнение", use_container_width=True):
            st.session_state.scenarios.append((scenario_name, result))
            st.toast(f"Сценарий «{scenario_name}» добавлен", icon="✅")
            st.rerun()
    with b_xlsx:
        # v0.12.25: комплексный «паспорт варианта» (Сводка по категориям +
        # Баланс территории) вместо узкого to_xlsx.
        xlsx_bytes = _variant_xlsx_bytes(scenario_name, result, options)
        st.download_button(
            ":material/download: Скачать xlsx",
            xlsx_bytes,
            file_name=f"{scenario_name}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
    with b_doc:
        # v0.11.0: альбом-презентация пока в отладке — кнопка отключена.
        st.button(
            ":material/slideshow: Сформировать альбом",
            disabled=True, use_container_width=True,
            help="Альбом-презентация (PPTX) — в разработке, скоро будет доступен.",
        )


def render_actions(result: TEPResult, default_name: str) -> None:
    """DEPRECATED: actions теперь встроены в render_kpi через scenario_default_name."""
    st.markdown("---")
    _render_actions_inline(result, default_name)


# ---------------------------------------------------------------------------
# Вкладка «Сравнение сценариев»
# ---------------------------------------------------------------------------

def render_comparison_tab() -> None:
    # v0.10.18: h1-заголовок вкладки в стиле макета.
    st.markdown("# Сравнение **вариантов**")
    pairs = st.session_state.get("scenarios", [])
    if not pairs:
        st.info("Сценариев пока нет. Перейдите на вкладку «Расчёт», "
                "настройте параметры и нажмите «Добавить в сравнение».")
        return

    st.subheader(f"Сценарии в сравнении: {len(pairs)}")

    # v0.13.1: служебный префикс «opt:» (из карточек оптимизатора) в отображении
    # убираем — в списке, сводке, xlsx и альбоме.
    def _clean(nm: str) -> str:
        return str(nm).removeprefix("opt:").strip()

    clean_pairs = [(_clean(name), tep) for name, tep in pairs]

    # Список с кнопками удаления
    for idx, (name, _) in enumerate(pairs):
        c1, c2 = st.columns([10, 1])
        c1.write(f"**{idx + 1}.** {_clean(name)}")
        if c2.button("🗑️", key=f"del_{idx}"):
            st.session_state.scenarios.pop(idx)
            st.rerun()

    if st.button("Очистить всё"):
        st.session_state.scenarios = []
        st.rerun()

    if len(pairs) >= 1:
        st.markdown("---")
        st.subheader("Сводка")
        df = results_to_dataframe(clean_pairs)
        st.dataframe(df, use_container_width=True)

        # Скачать xlsx-сравнение
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            to_xlsx(clean_pairs, tmp_path)
            with open(tmp_path, "rb") as f:
                xlsx_bytes = f.read()
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        # v0.13.0: сравнительный XLSX + мульти-вариантный «альбом концепции» PPTX.
        dl1, dl2 = st.columns([3, 3])
        dl1.download_button(
            ":material/download: Скачать xlsx",
            xlsx_bytes,
            file_name="comparison.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
        # Альбом концепции (v0.13.5): Титул → Общая информация о территории →
        # Сравнение → карточки вариантов → детальные таблицы. Первый выбранный
        # сценарий трактуется как Базовый вариант.
        # Выбор вариантов: можно собрать альбом по 1–3 выбранным, не по всем.
        _opt_labels = [f"{i + 1}. {nm}" for i, (nm, _) in enumerate(clean_pairs)]
        # v0.15.1 (баг «в альбоме только База»): multiselect с key запоминает
        # СТАРЫЙ выбор — при добавлении новых сценариев они не попадали в
        # выбор (default игнорируется, когда key уже в session_state).
        # Пуш-паттерн (как pareto_floors_range): при изменении СПИСКА сценариев
        # сбрасываем выбор на «все»; ручные правки между изменениями живут.
        if st.session_state.get("_album_sel_options") != _opt_labels:
            st.session_state["album_variant_select"] = _opt_labels
            st.session_state["_album_sel_options"] = _opt_labels
        _sel = st.multiselect(
            "Варианты в альбом (по умолчанию — все)",
            options=_opt_labels, default=_opt_labels,
            key="album_variant_select",
        )
        _sel_idx = [i for i, lb in enumerate(_opt_labels) if lb in _sel]
        album_pairs = [clean_pairs[i] for i in _sel_idx] or clean_pairs
        if dl2.button(":material/slideshow: Сформировать альбом (PPTX)",
                      use_container_width=True):
            from urban_model.export.album.concept import build_concept_album
            with tempfile.NamedTemporaryFile(suffix=".pptx", delete=False) as tmp:
                _pp = tmp.name
            try:
                build_concept_album(
                    album_pairs, _pp,
                    base_options=st.session_state.get("last_calc_options"),
                    site_area=st.session_state.get("last_calc_site_area"),
                )
                with open(_pp, "rb") as f:
                    st.session_state["_concept_album_bytes"] = f.read()
            finally:
                try:
                    os.unlink(_pp)
                except OSError:
                    pass
        if st.session_state.get("_concept_album_bytes"):
            st.download_button(
                ":material/download: Скачать альбом концепции (PPTX)",
                st.session_state["_concept_album_bytes"],
                file_name="concept_album.pptx",
                mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                use_container_width=True,
            )
        st.caption(
            "Альбом концепции: общая информация о территории, сводное сравнение, "
            "карточки выбранных вариантов и детальные таблицы по каждому. "
            "Первый выбранный вариант — Базовый."
        )
