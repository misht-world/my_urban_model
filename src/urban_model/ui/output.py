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
from urban_model.models.result import Status, TEPField, TEPResult
from urban_model.ui.formatting import (
    STATUS_LABEL_RU,
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

    # ── Ряд 1 (эконом-индекс перенесён в ряд 3, v0.12.21) ────────────────
    # v0.12.24: 5 колонок (5-я пустая) — чтобы КИТ/Население/Площадь/Этажность
    # стояли РОВНО под ДОО/СОШ/Доп.обр/Парковки ряда 2 (тоже 5 колонок).
    c1, c2, c3, c4, _c5 = st.columns(5)
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

    # ── Ряд 2 ───────────────────────────────────────────────────────────
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
    c9.metric(
        "Парковки", f"{total_pl} м/м" if total_pl > 0 else "—",
        delta=" · ".join(_parts) or None, delta_color="off",
        help="Всего машино-мест и разбивка по типам.",
    )
    znop_pp = result.znop_per_person.value or 0
    znop_area = int(result.znop_area.value or 0)
    c10.metric(
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
        c10.caption(f":material/park: ЗНОП зон, м²/чел: {_zz}")

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

def _row(label: str, field: TEPField, fmt_fn=fmt_int, suffix: str = "") -> dict:
    if field.value is None:
        val_str = "—"
    elif callable(fmt_fn):
        val_str = fmt_fn(field.value)
    else:
        val_str = str(field.value)
    return {
        "Показатель": label,
        "Значение": f"{val_str}{suffix}",
        "Статус": STATUS_LABEL_RU.get(field.status, field.status.value),
        "Источник": field.source or "",
        "Формула": field.formula or "",
    }


def _text_row(label: str, text: str) -> dict:
    """Строка таблицы с произвольным текстовым значением (без TEPField)."""
    return {
        "Показатель": label, "Значение": text,
        "Статус": "", "Источник": "", "Формула": "",
    }


def _parse_object_buckets(formula: str | None) -> tuple[int, list[int]]:
    """Из formula-строки соцобъекта вытащить (число объектов, вместимости).

    Формула содержит «… [160, 160]» или «… [550]». Возвращает (N, [caps]).
    """
    import re
    if not formula:
        return 0, []
    m = re.search(r"\[([\d,\s]+)\]", formula)
    if not m:
        return 0, []
    caps = [int(x) for x in m.group(1).split(",") if x.strip()]
    return len(caps), caps


def _format_buckets(n: int, caps: list[int]) -> str:
    """«2 объекта (по 160 мест)» или «2 объекта (160 + 240 мест)»."""
    if n <= 0:
        return "—"
    word = "объект" if n == 1 else ("объекта" if 2 <= n <= 4 else "объектов")
    if len(set(caps)) == 1:
        return f"{n} {word} (по {caps[0]} мест)" if n > 1 else f"{n} {word} ({caps[0]} мест)"
    return f"{n} {word} ({' + '.join(str(c) for c in caps)} мест)"


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
    # 🏠 Жильё
    with st.expander(":material/home: Жильё", expanded=False):
        # v0.10.19: «Плотность (СП 42.13330)» поднята на 2-ю строку.
        rows = [
            _row("КИТ ПЗЗ (площадь квартир / ЗУ жилой застройки)", result.kit, fmt_float),
            _row("Плотность (по СП 42.13330, для 20 м²/чел)",
                 result.density_chel_per_ga, lambda x: f"{x:.1f}", " чел./га"),
            _row("Население", result.population, fmt_int, " чел."),
            _row("Общая площадь жилых зданий (GFA)", result.gfa, fmt_m2),
            _row("Площадь квартир", result.apartments_area, fmt_m2),
        ]
        if result.built_in_area.value and result.built_in_area.value > 0:
            rows.append(_row(
                f"Встроенно-пристроенные помещения (ВПП, ВРИ {result.built_in_vri_code})",
                result.built_in_area, fmt_m2,
            ))
            rows.append(_row("Парковки ВПП", result.built_in_parking_places,
                             fmt_int, " м/м"))
            rows.append(_row("Озеленение ВПП", result.built_in_greening_area, fmt_m2))
        rows += [
            _row("Площадь застройки", result.housing_footprint, fmt_m2),
            _row("ЗУ жилой застройки", result.housing_lot_area, fmt_m2),
            _row("Плотность квартала (внутренняя, GFA / площадь квартала)",
                 result.block_density, fmt_float),
        ]
        _show_rows(rows)

    # 🎒 ДОО
    with st.expander(":material/child_care: ДОО (детские сады)", expanded=False):
        rows = [
            _row("Мест требуется", result.kindergarten_places_required, fmt_float),
            _row("Мест принято (округлено)", result.kindergarten_places_accepted, fmt_int),
        ]
        _kg_n, _kg_caps = _parse_object_buckets(result.kindergarten_places_accepted.formula)
        if _kg_n:
            rows.append(_text_row("Принято объектов", _format_buckets(_kg_n, _kg_caps)))
        rows += [
            _row("Площадь участков", result.kindergarten_plot_area, fmt_m2),
            _row("Площадь зданий", result.kindergarten_building_area, fmt_m2),
        ]
        _show_rows(rows)

    # 🏫 СОШ
    with st.expander(":material/school: СОШ (школы)", expanded=False):
        rows = [
            _row("Мест требуется", result.school_places_required, fmt_float),
            _row("Мест принято (округлено)", result.school_places_accepted, fmt_int),
        ]
        _sch_n, _sch_caps = _parse_object_buckets(result.school_places_accepted.formula)
        if _sch_n:
            rows.append(_text_row("Принято объектов", _format_buckets(_sch_n, _sch_caps)))
        rows += [
            _row("Площадь участков", result.school_plot_area, fmt_m2),
            _row("Площадь зданий", result.school_building_area, fmt_m2),
        ]
        _show_rows(rows)

    # 🎨 Организации доп. образования (ВРИ 3.5.1, v0.12.15)
    if (result.add_education_places_accepted.value or 0) > 0:
        _ae_built_in = bool(getattr(result, "add_education_built_in", False))
        _ae_label = "встроенное (ВПП)" if _ae_built_in else "отдельно стоящее"
        with st.expander(
            f":material/palette: Организации доп. образования — {_ae_label}",
            expanded=False,
        ):
            rows = [
                _row("Мест требуется", result.add_education_places_required, fmt_float),
                _row("Мест принято", result.add_education_places_accepted, fmt_int),
                _row("Площадь здания (17 м²/место)",
                     result.add_education_building_area, fmt_m2),
            ]
            if not _ae_built_in:
                rows.append(_row("Площадь ЗУ (15 м²/место)",
                                 result.add_education_plot_area, fmt_m2))
            rows.append(_row("Парковка (как у ДОУ/СОШ)",
                             result.add_education_parking_places, fmt_int))
            _show_rows(rows)

    # 🏥 Амбулаторно-поликлинические учреждения (ВРИ 3.4.1, v0.12.28)
    if (result.polyclinic_visits_accepted.value or 0) > 0:
        _poly_bi = bool(getattr(result, "polyclinic_built_in", False))
        _poly_label = "ВПП (офис врача)" if _poly_bi else "отдельно стоящая"
        with st.expander(
            f":material/local_hospital: Поликлиника — {_poly_label}", expanded=False,
        ):
            rows = [
                _row("Посещений требуется", result.polyclinic_visits_required, fmt_float),
                _row("Посещений принято", result.polyclinic_visits_accepted, fmt_int),
                _row(f"Площадь здания ({8 if _poly_bi else 23} м²/посещ.)",
                     result.polyclinic_building_area, fmt_m2),
            ]
            if not _poly_bi:
                rows.append(_row("Площадь ЗУ (10 м²/посещ., мин. 2000)",
                                 result.polyclinic_plot_area, fmt_m2))
            rows.append(_row("Парковка (5 раб + 40 посетит.)",
                             result.polyclinic_parking_places, fmt_int))
            _show_rows(rows)

    # Плоскостные спортивные сооружения
    if (result.sport_facilities_plot_area.value or 0) > 0:
        with st.expander(":material/directions_run: Плоскостные спортивные сооружения", expanded=False):
            rows = [
                _row("Площадь сооружений", result.sport_facilities_area, fmt_m2),
                _row("Озеленение требуется (40%)",
                     result.sport_facilities_greening_required, fmt_m2),
                _row("Доп. озеленение на ЗУ (после substitution)",
                     result.sport_facilities_greening_extra, fmt_m2),
                _row("Полный ЗУ (sport + доп. озеленение)",
                     result.sport_facilities_plot_area, fmt_m2),
            ]
            _show_rows(rows)

    # 🌳 ЗНОП и озеленение
    with st.expander(":material/park: ЗНОП и озеленение", expanded=False):
        rows = [
            _row("ЗНОП на человека", result.znop_per_person, fmt_float, " м²/чел"),
            _row("Площадь ЗНОП", result.znop_area, fmt_m2),
            _row("Озеленение жилья", result.greening_housing_area, fmt_m2),
            _row("Минимум озеленения квартала (норматив)",
                 result.greening_quarter_required, fmt_m2),
        ]
        _show_rows(rows)

    # 🅿️ Парковки
    with st.expander(":material/local_parking: Парковки", expanded=False):
        rows = [
            _row("Всего м/м требуется", result.parking_required_places, fmt_int),
            _row("Открытые м/м", result.parking_open_places, fmt_int),
            _row("Площадь открытых", result.parking_open_area, fmt_m2),
        ]
        if result.parking_multilevel_places.value and result.parking_multilevel_places.value > 0:
            rows += [
                _row("Многоуровневые м/м", result.parking_multilevel_places, fmt_int),
                _row("Объектов МП", result.parking_multilevel_objects, fmt_int),
                _row("Пятно МП", result.parking_multilevel_area, fmt_m2),
            ]
        if result.parking_underground_places.value and result.parking_underground_places.value > 0:
            rows.append(_row("Подземные м/м", result.parking_underground_places, fmt_int))
        if (getattr(result, "parking_stylobate_places", None)
                and (result.parking_stylobate_places.value or 0) > 0):
            rows += [
                _row("Стилобатные м/м", result.parking_stylobate_places, fmt_int),
                _row("Площадь деки стилобата", result.parking_stylobate_area, fmt_m2),
            ]
        # Парковки соцобъектов — отдельные открытые на ЗУ соцобъектов (v0.7.0)
        if (result.social_parking_total.value or 0) > 0:
            rows += [
                _row("СОЦ: всего м/м (отдельные открытые на ЗУ)",
                     result.social_parking_total, fmt_int),
                _row("В т.ч. ДОО (ceil(раб/5) + ceil(уч/100), min 2)",
                     result.social_parking_kindergarten, fmt_int),
                _row("В т.ч. СОШ (та же формула)",
                     result.social_parking_school, fmt_int),
            ]
            if (result.add_education_parking_places.value or 0) > 0:
                rows.append(_row("В т.ч. доп. образование (та же формула)",
                                 result.add_education_parking_places, fmt_int))
            if (result.polyclinic_parking_places.value or 0) > 0:
                rows.append(_row("В т.ч. поликлиника (5 раб + 40 посетит.)",
                                 result.polyclinic_parking_places, fmt_int))
            rows.append(_row("Площадь парковок соцобъектов на квартале",
                             result.social_parking_area, fmt_m2))
        _show_rows(rows)

    # Проезды и инженерия — формулы скрыты (v0.10.19, на время тестирования).
    with st.expander(":material/route: Проезды", expanded=False):
        rows = [
            _row("Внутриквартальные", result.driveways_intra_quarter_area, fmt_m2),
            _row("На ЗУ жилой застройки", result.driveways_housing_lot_area, fmt_m2),
        ]
        for r in rows:
            r["Формула"] = ""
            r["Источник"] = ""
        _show_rows(rows)

    # 🔌 Инженерная инфраструктура (v0.12)
    if result.engineering is not None and result.engineering.objects:
        eng = result.engineering
        with st.expander(
            ":material/bolt: Инженерная инфраструктура",
            expanded=False,
        ):
            eng_rows = []
            for o in eng.objects:
                if o.count <= 0:
                    continue
                cap = (
                    f"{o.capacity:g} {o.capacity_unit}"
                    if o.capacity and o.capacity_unit else "—"
                )
                eng_rows.append({
                    "Объект": o.label,
                    "Кол-во": o.count,
                    "Мощность (1 шт.)": cap,
                    "ЗУ (1 шт.), м²": f"{o.plot_each:,.0f}".replace(",", " "),
                    "ЗУ всего, м²": f"{o.plot_total:,.0f}".replace(",", " "),
                    "В балансе": "да" if o.in_balance else "только потребность",
                })
            st.dataframe(pd.DataFrame(eng_rows), hide_index=True, use_container_width=True)
            cooking_lbl = "электроплиты" if eng.cooking == "electric" else "газовые плиты"
            st.caption(
                f"Приготовление пищи: **{cooking_lbl}**. В баланс входит "
                f"**{fmt_m2(eng.plot_in_balance)}** "
                f"(всего по объектам {fmt_m2(eng.plot_total_all)}). "
                f"«Только потребность» — объект считается, но ЗУ вне баланса."
            )

    # ⚖️ Баланс территории (детализация). v0.9.17: диаграмма теперь в
    # KPI-блоке, здесь — табличная детализация на случай нужды.
    with st.expander(":material/balance: Баланс территории (таблица)", expanded=False):
        b = result.balance
        comp_rows = []
        site_area = b.site_area
        # v0.9.28.2: при кластерах добавляем колонки с разбивкой по зонам.
        # Все территории распределяются пропорционально площади зон
        # (доступная под застройку доля одинакова для всех зон), поэтому
        # доля_зоны_i = площадь_зоны_i / Σ площадей зон.
        _cl = result.floor_clusters_detail
        _cl_total = sum(d["area_m2"] for d in _cl) if _cl else 0.0
        _cl_shares = (
            [(d["label"], d["area_m2"] / _cl_total) for d in _cl]
            if _cl and _cl_total > 0 else []
        )

        def _with_zones(row: dict, val: float) -> dict:
            for label, sh in _cl_shares:
                row[f"{label}, м²"] = f"{val * sh:,.0f}".replace(",", " ")
            return row

        # v0.10.8 (#8): «Требуется» — фактическая площадь компонента, даже если
        # он в режиме «только потребность» и в баланс НЕ входит (тогда «В балансе»=0).
        # Источник — соответствующие TEP-поля (а не balance.components).
        required_map = {
            "housing_lot": result.housing_lot_area.value,
            "kindergarten_plot": result.kindergarten_plot_area.value,
            "school_plot": result.school_plot_area.value,
            "sport_facilities": result.sport_facilities_plot_area.value,
            "social_parking_plot": result.social_parking_area.value,
            "add_education_plot": result.add_education_plot_area.value,
            "polyclinic_plot": result.polyclinic_plot_area.value,
            "znop": result.znop_area.value,
            "intra_quarter_driveways": result.driveways_intra_quarter_area.value,
            "parking_multilevel": result.parking_multilevel_area.value,
            "engineering_plot": (
                result.engineering.plot_total_all if result.engineering else 0.0
            ),
        }

        for name, val in sorted(b.components.items(), key=lambda kv: -kv[1]):
            pct = val / site_area * 100 if site_area > 0 else 0
            pretty = {
                "housing_lot": "ЗУ жилой застройки",
                "kindergarten_plot": "Участки ДОО",
                "school_plot": "Участки СОШ",
                "sport_facilities": "Спортивные сооружения",
                "social_parking_plot": "Парковки соцобъектов (ДОО/СОШ)",
                "add_education_plot": "Доп. образование (ЗУ)",
                "polyclinic_plot": "Поликлиника (ЗУ)",
                "znop": "ЗНОП",
                "intra_quarter_driveways": "Внутриквартальные проезды",
                "parking_multilevel": "Многоуровневые паркинги",
                "built_in_greening": "Озеленение ВПП",
                "custom_objects": "Пользовательские объекты",
                "engineering_plot": "Инженерная инфраструктура",
            }.get(name, name)
            req = required_map.get(name, val)
            if req is None:
                req = val
            comp_rows.append(_with_zones({
                "Компонент": pretty,
                "Требуется, м²": f"{req:,.0f}".replace(",", " "),
                "В балансе, м²": f"{val:,.0f}".replace(",", " "),
                "Доля квартала, %": f"{pct:.1f}%",
            }, val))
        comp_rows.append(_with_zones({
            "Компонент": "— Итого занято",
            "Требуется, м²": "",
            "В балансе, м²": f"{b.required_total:,.0f}".replace(",", " "),
            "Доля квартала, %": f"{b.required_total / site_area * 100:.1f}%",
        }, b.required_total))
        comp_rows.append(_with_zones({
            "Компонент": "— Резерв (surplus)",
            "Требуется, м²": "",
            "В балансе, м²": f"{b.surplus:,.0f}".replace(",", " "),
            "Доля квартала, %": f"{b.surplus / site_area * 100:.1f}%",
        }, b.surplus))
        st.dataframe(pd.DataFrame(comp_rows), hide_index=True, use_container_width=True)
        cap = (
            f"Площадь квартала: **{fmt_m2(site_area)}**  ·  {fmt_ga(site_area)}  ·  "
            f"«Требуется» — фактическая площадь; «В балансе» = 0 у компонентов в "
            f"режиме «только потребность» (размещаются вне квартала)."
        )
        if _cl_shares:
            cap += "  ·  колонки по зонам — пропорционально площади зон"
        st.caption(cap)

    # 🏗 Кластеры этажности (v0.9.28)
    if result.floor_clusters_detail:
        with st.expander(":material/apartment: Кластеры этажности (по зонам)", expanded=False):
            st.caption(
                f"Норматив проверяется по **общему КИТ {result.kit.value:.3f}** "
                f"(средневзвешенная этажность **{result.effective_floors:.1f}**). "
                f"КИТ по зонам ниже — **справочно**: показывает локальную "
                f"плотность каждой зоны (Σ площадей квартир сходится с общей)."
            )
            cl_rows = []
            for d in result.floor_clusters_detail:
                cl_rows.append({
                    "Зона": d["label"],
                    "Площадь, м²": f"{d['area_m2']:,.0f}".replace(",", " "),
                    "Этажей": d["floors"],
                    "КИТ зоны (справ.)": f"{d['kit']:.3f}",
                    "Площадь квартир, м²": f"{d['apartments_area']:,.0f}".replace(",", " "),
                    "Пятно застройки, м²": f"{d['footprint']:,.0f}".replace(",", " "),
                })
            st.dataframe(pd.DataFrame(cl_rows), hide_index=True, use_container_width=True)
            kit_norm = result.kit.normative
            max_kit = max((d["kit"] for d in result.floor_clusters_detail), default=0.0)
            if kit_norm and max_kit > kit_norm + 1e-6:
                st.caption(
                    f"ℹ Локальный КИТ верхней зоны {max_kit:.3f} выше норматива "
                    f"{kit_norm} — на отдельном ЗУ такая зона потребовала бы более "
                    f"высокого предела. В общем балансе квартала это компенсируется "
                    f"низкими зонами (общий КИТ {result.kit.value:.3f} в норме)."
                )

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

    # Список с кнопками удаления
    for idx, (name, _) in enumerate(pairs):
        c1, c2 = st.columns([10, 1])
        c1.write(f"**{idx + 1}.** {name}")
        if c2.button("🗑️", key=f"del_{idx}"):
            st.session_state.scenarios.pop(idx)
            st.rerun()

    if st.button("Очистить всё"):
        st.session_state.scenarios = []
        st.rerun()

    if len(pairs) >= 1:
        st.markdown("---")
        st.subheader("Сводка")
        df = results_to_dataframe(pairs)
        st.dataframe(df, use_container_width=True)

        # Скачать xlsx-сравнение
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            to_xlsx(pairs, tmp_path)
            with open(tmp_path, "rb") as f:
                xlsx_bytes = f.read()
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        # v0.11: DOCX-отчёт сравнения убран (по ТЗ — только XLSX; сравнительный
        # альбом-презентация PPTX будет в фазе 2).
        dl1, _ = st.columns([3, 9])
        dl1.download_button(
            ":material/download: Скачать xlsx",
            xlsx_bytes,
            file_name="comparison.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
        st.caption("Сравнительный альбом-презентация (PPTX) — в разработке.")
