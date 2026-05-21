"""Рендер главной области: KPI-карточки + раскрывающиеся секции + сравнение."""

from __future__ import annotations

import io
import os
import re
import tempfile

import pandas as pd
import streamlit as st

from urban_model.export import results_to_dataframe, to_xlsx
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

def render_header(result: TEPResult) -> None:
    kit_v = result.kit.value or 0
    kit_max = result.kit_normative_max.value or 0
    balance_feasible = result.balance.is_feasible
    kit_ok = result.kit.status != Status.ERROR
    feasible_all = balance_feasible and kit_ok

    if feasible_all:
        st.success(
            f"✅ **Все нормативы выполняются.** КИТ = {kit_v:.3f} (≤ {kit_max})"
            f"  ·  Резерв территории: {fmt_int(result.balance.surplus)} м²"
        )
    elif not kit_ok:
        # КИТ ПЗЗ превышает потолок — основная причина (часто из-за выключенного ДПТ)
        st.error(
            f"❌ **КИТ ПЗЗ ({kit_v:.3f}) превышает нормативный потолок ({kit_max}).** "
            f"Жилой дом при выбранных параметрах не «помещается» в норматив. "
            f"См. рекомендации ниже."
        )
    else:
        st.error(
            f"❌ **Дефицит баланса территории.** КИТ = {kit_v:.3f}"
            f"  ·  Не хватает: {fmt_int(-result.balance.surplus)} м²"
        )

    if result.limiting_factor:
        st.caption(f"🔻 **Ограничивающий фактор:** {result.limiting_factor}")
    # v0.8.6: префиксы [CODE] из warning_codes.WC прячем от пользователя —
    # они служат для машинной фильтрации (Optuna feasibility, тесты).
    from urban_model.calculations.warning_codes import strip_code
    for w in result.warnings:
        st.warning(f"⚠️ {strip_code(w)}")


# ---------------------------------------------------------------------------
# KPI-карточки
# ---------------------------------------------------------------------------

def render_kpi(result: TEPResult, *, scenario_default_name: str | None = None) -> None:
    """KPI-блок с возможностью встроить кнопки «Добавить в сравнение» и xlsx.

    Если scenario_default_name задан — внутри того же блока (под линией
    разделителя) рисуются actions: text_input + добавить/скачать.
    """
    # === Основные показатели в едином блоке с возможностью копирования ===
    with st.container(border=True):
        header_col, copy_col = st.columns([10, 2])
        with header_col:
            st.markdown("##### 📊 Основные показатели")
        with copy_col:
            # Сводка в виде plain-text для копирования через JS clipboard API
            kit_v = result.kit.value or 0
            kit_max = result.kit_normative_max.value or 0
            pop_v = int(result.population.value or 0)
            apt_v = result.apartments_area.value or 0
            surplus_v = result.balance.surplus
            kg_total = int(result.kindergarten_places_accepted.value or 0)
            sch_total = int(result.school_places_accepted.value or 0)
            total_pl = int(result.parking_required_places.value or 0)
            znop_pp = result.znop_per_person.value or 0
            znop_area = int(result.znop_area.value or 0)
            summary_text = (
                f"КИТ (ПЗЗ): {kit_v:.3f} (потолок {kit_max})\n"
                f"Население: {pop_v:,} чел.\n"
                f"Площадь квартир: {apt_v:,.0f} м²\n"
                f"Резерв баланса: {surplus_v:+,.0f} м²\n"
                f"ДОО: {kg_total} мест\n"
                f"СОШ: {sch_total} мест\n"
                f"Парковки: {total_pl} м/м\n"
                f"ЗНОП: {znop_area:,} м² ({znop_pp:.1f} м²/чел)"
            ).replace(",", " ")
            # v0.8.0: экономика добавляется в сводку, если есть
            if result.economy is not None:
                e = result.economy
                summary_text += (
                    f"\n\n— Экономика (у.е.) —\n"
                    f"Прибыль: {e.profit:+,.0f}\n"
                    f"Выручка: {e.revenue.total:,.0f}\n"
                    f"Себестоимость: {e.cost.total:,.0f}\n"
                    f"Маржа: {e.margin * 100:.1f}%; ROI: {e.roi * 100:.1f}%\n"
                    f"Прибыль/м² участка: {e.profit_per_site_m2:+,.2f}"
                ).replace(",", " ")
            # JS-кнопка: navigator.clipboard.writeText + inline-сообщение
            import json
            js_text = json.dumps(summary_text)
            uid = f"copy_{id(result)}"
            st.html(f"""
            <div style="display:flex;align-items:center;gap:8px;">
              <button id="btn_{uid}" type="button"
                  onclick='navigator.clipboard.writeText({js_text}).then(()=>{{
                      var s=document.getElementById("ok_{uid}");
                      s.style.opacity="1";
                      setTimeout(()=>{{s.style.opacity="0";}},2000);
                  }});'
                  style="background:#1565C0;color:white;border:none;
                         padding:6px 12px;border-radius:4px;cursor:pointer;
                         font-size:0.9rem;font-weight:500;">
                📋 Копировать
              </button>
              <span id="ok_{uid}"
                  style="color:#107C10;font-weight:500;opacity:0;
                         transition:opacity 0.2s;font-size:0.85rem;">
                ✓ Скопировано
              </span>
            </div>
            """)

        # === Ряд 1: главные показатели жилья ===
        c1, c2, c3, c4 = st.columns(4)
        kit_help = (
            "КИТ по ПЗЗ СПб = площадь квартир / ЗУ жилой застройки. "
            f"Норматив. потолок: {result.kit_normative_max.value} "
            f"(ДПТ: {'да' if result.kit_normative_max.value == 2.5 else 'нет'})"
        )
        c1.metric("КИТ (ПЗЗ)", f"{result.kit.value:.3f}", help=kit_help)
        c2.metric(
            "Население", f"{fmt_int(result.population.value)} чел.",
            help="Жилищная обеспеченность: 28 м²/чел (НГП СПб).",
        )
        c3.metric("Площадь квартир", fmt_m2(result.apartments_area.value))
        surplus = result.balance.surplus
        delta_color = "normal" if surplus >= 0 else "inverse"
        c4.metric(
            "Резерв баланса",
            fmt_m2(surplus),
            delta=("OK" if surplus >= 0 else "ДЕФИЦИТ"),
            delta_color=delta_color,
        )

        # === Ряд 2: социалка / парковки / ЗНОП (внутри того же контейнера) ===
        c5, c6, c7, c8 = st.columns(4)

        # Вспомогательная функция: парсим список вместимостей из formula-строки
        def _buckets_delta(formula_str: str, total: int) -> str | None:
            m = re.search(r'\[([^\]]+)\]', formula_str)
            if not m or total == 0:
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

        kg_buckets_str = result.kindergarten_places_accepted.formula or ""
        kg_total = int(result.kindergarten_places_accepted.value or 0)
        c5.metric(
            "ДОО",
            f"{kg_total} мест" if kg_total > 0 else "—",
            delta=_buckets_delta(kg_buckets_str, kg_total),
            delta_color="off",
            help="Принятая суммарная вместимость и число объектов ДОО.",
        )

        sch_buckets_str = result.school_places_accepted.formula or ""
        sch_total = int(result.school_places_accepted.value or 0)
        c6.metric(
            "СОШ",
            f"{sch_total} мест" if sch_total > 0 else "—",
            delta=_buckets_delta(sch_buckets_str, sch_total),
            delta_color="off",
            help="Принятая суммарная вместимость и число корпусов СОШ.",
        )

        # Парковки — сводка по типам
        open_pl = int(result.parking_open_places.value or 0)
        ml_pl = int(result.parking_multilevel_places.value or 0)
        ug_pl = int(result.parking_underground_places.value or 0)
        total_pl = int(result.parking_required_places.value or 0)
        breakdown_parts = []
        if open_pl: breakdown_parts.append(f"откр. {open_pl}")
        if ml_pl:   breakdown_parts.append(f"многоур. {ml_pl}")
        if ug_pl:   breakdown_parts.append(f"подз. {ug_pl}")
        c7.metric(
            "Парковки",
            f"{total_pl} м/м" if total_pl > 0 else "—",
            delta=" · ".join(breakdown_parts) or None,
            delta_color="off",
            help="Всего машино-мест и разбивка по типам.",
        )

        znop_pp = result.znop_per_person.value or 0
        znop_area = int(result.znop_area.value or 0)
        c8.metric(
            "ЗНОП",
            f"{znop_area:,} м²".replace(",", " ") if znop_area > 0 else "—",
            delta=f"{znop_pp:.1f} м²/чел" if znop_pp > 0 else None,
            delta_color="off",
            help="Общая площадь ЗНОП и норма на жителя.",
        )

        # === Ряд 3: экономика (v0.8.0) — отображается, если result.economy не None ===
        if result.economy is not None:
            st.markdown("---")
            e1, e2, e3, e4 = st.columns(4)
            e = result.economy
            profit_help = (
                "Условные единицы. 1.0 у.е. ≈ себестоимость м² жилья "
                "9-эт. монолита со standard-отделкой."
            )
            e1.metric(
                "💰 Прибыль",
                f"{e.profit:+,.0f} у.е.".replace(",", " "),
                delta=("OK" if e.profit >= 0 else "УБЫТОК"),
                delta_color=("normal" if e.profit >= 0 else "inverse"),
                help=profit_help,
            )
            e2.metric(
                "Маржа",
                f"{e.margin * 100:.1f}%" if e.revenue.total > 0 else "—",
                help="profit / revenue",
            )
            e3.metric(
                "ROI",
                f"{e.roi * 100:.1f}%" if e.cost.total > 0 else "—",
                help="profit / cost",
            )
            e4.metric(
                "Прибыль / м² участка",
                f"{e.profit_per_site_m2:+,.2f} у.е./м²".replace(",", " "),
                help="Основная метрика для ранжирования",
            )

        # === Inline-actions: «Добавить в сравнение» + xlsx (внутри блока) ===
        if scenario_default_name is not None:
            st.markdown("---")
            _render_actions_inline(result, scenario_default_name)


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
    with st.expander("🏠 Жильё", expanded=False):
        rows = [
            _row("КИТ ПЗЗ (площадь квартир / ЗУ жилой застройки)", result.kit, fmt_float),
            _row("Плотность квартала (внутренняя, GFA / площадь квартала)",
                 result.block_density, fmt_float),
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
            _row("Население", result.population, fmt_int, " чел."),
            _row("Плотность (по СП 42.13330, для 20 м²/чел)",
                 result.density_chel_per_ga, lambda x: f"{x:.1f}", " чел./га"),
        ]
        _show_rows(rows)

    # 🎒 ДОО
    with st.expander("🎒 ДОО (детские сады)", expanded=False):
        rows = [
            _row("Мест требуется", result.kindergarten_places_required, fmt_float),
            _row("Мест принято (округлено)", result.kindergarten_places_accepted, fmt_int),
            _row("Площадь участков", result.kindergarten_plot_area, fmt_m2),
            _row("Площадь зданий", result.kindergarten_building_area, fmt_m2),
        ]
        _show_rows(rows)

    # 🏫 СОШ
    with st.expander("🏫 СОШ (школы)", expanded=False):
        rows = [
            _row("Мест требуется", result.school_places_required, fmt_float),
            _row("Мест принято (округлено)", result.school_places_accepted, fmt_int),
            _row("Площадь участков", result.school_plot_area, fmt_m2),
            _row("Площадь зданий", result.school_building_area, fmt_m2),
        ]
        _show_rows(rows)

    # 🏃 Плоскостные спортивные сооружения
    if (result.sport_facilities_plot_area.value or 0) > 0:
        with st.expander("🏃 Плоскостные спортивные сооружения", expanded=False):
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
    with st.expander("🌳 ЗНОП и озеленение", expanded=False):
        rows = [
            _row("ЗНОП на человека", result.znop_per_person, fmt_float, " м²/чел"),
            _row("Площадь ЗНОП", result.znop_area, fmt_m2),
            _row("Озеленение жилья", result.greening_housing_area, fmt_m2),
            _row("Минимум озеленения квартала (норматив)",
                 result.greening_quarter_required, fmt_m2),
        ]
        _show_rows(rows)

    # 🅿️ Парковки
    with st.expander("🅿️ Парковки", expanded=False):
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
        # Парковки соцобъектов — отдельные открытые на ЗУ соцобъектов (v0.7.0)
        if (result.social_parking_total.value or 0) > 0:
            rows += [
                _row("СОЦ: всего м/м (отдельные открытые на ЗУ)",
                     result.social_parking_total, fmt_int),
                _row("В т.ч. ДОО (ceil(раб/5) + ceil(уч/100), min 2)",
                     result.social_parking_kindergarten, fmt_int),
                _row("В т.ч. СОШ (та же формула)",
                     result.social_parking_school, fmt_int),
                _row("Площадь парковок соцобъектов на квартале",
                     result.social_parking_area, fmt_m2),
            ]
        _show_rows(rows)

    # 🛣️ Проезды
    with st.expander("🛣️ Проезды", expanded=False):
        rows = [
            _row("Внутриквартальные", result.driveways_intra_quarter_area, fmt_m2),
            _row("На ЗУ жилой застройки", result.driveways_housing_lot_area, fmt_m2),
        ]
        _show_rows(rows)

    # ⚖️ Баланс территории
    with st.expander("⚖️ Баланс территории", expanded=True):
        b = result.balance
        comp_rows = []
        site_area = b.site_area
        for name, val in sorted(b.components.items(), key=lambda kv: -kv[1]):
            pct = val / site_area * 100 if site_area > 0 else 0
            pretty = {
                "housing_lot": "ЗУ жилой застройки",
                "kindergarten_plot": "Участки ДОО",
                "school_plot": "Участки СОШ",
                "sport_facilities": "Спортивные сооружения",
                "social_parking_plot": "Парковки соцобъектов (ДОО/СОШ)",
                "znop": "ЗНОП",
                "intra_quarter_driveways": "Внутриквартальные проезды",
                "parking_multilevel": "Многоуровневые паркинги",
                "built_in_greening": "Озеленение ВПП",
                "custom_objects": "Пользовательские объекты",
            }.get(name, name)
            comp_rows.append({
                "Компонент": pretty,
                "Площадь, м²": f"{val:,.0f}".replace(",", " "),
                "Доля квартала, %": f"{pct:.1f}%",
            })
        comp_rows.append({
            "Компонент": "— Итого занято",
            "Площадь, м²": f"{b.required_total:,.0f}".replace(",", " "),
            "Доля квартала, %": f"{b.required_total / site_area * 100:.1f}%",
        })
        comp_rows.append({
            "Компонент": "— Резерв (surplus)",
            "Площадь, м²": f"{b.surplus:,.0f}".replace(",", " "),
            "Доля квартала, %": f"{b.surplus / site_area * 100:.1f}%",
        })
        st.dataframe(pd.DataFrame(comp_rows), hide_index=True, use_container_width=True)
        st.caption(f"Площадь квартала: **{fmt_m2(site_area)}**  ·  {fmt_ga(site_area)}")

    # 💰 Экономика (v0.8.0)
    if result.economy is not None:
        with st.expander("💰 Экономика (себестоимость / выручка / метрики)", expanded=False):
            st.caption(
                "Все значения — в условных единицах. **1.0 у.е. ≈ себестоимость "
                "м² жилья 9-эт. монолита со standard-отделкой.** Стартовые "
                "коэффициенты — `configs/spb.yaml`, секция `economy:` (источник SPEC.md). "
                "Переход к рублям — через множитель (v0.8.x)."
            )
            e = result.economy
            cb = e.cost
            rb = e.revenue

            # Себестоимость по статьям
            st.markdown("**Себестоимость**")
            cost_rows = [
                {"Статья": "Жильё", "у.е.": f"{cb.residential:,.0f}".replace(",", " ")},
                {"Статья": "ВПП (коммерция)", "у.е.": f"{cb.vpp:,.0f}".replace(",", " ")},
                {"Статья": "ДОО (детские сады)", "у.е.": f"{cb.kindergarten:,.0f}".replace(",", " ")},
                {"Статья": "СОШ (школы)", "у.е.": f"{cb.school:,.0f}".replace(",", " ")},
                {"Статья": "Парковки открытые", "у.е.": f"{cb.parking_open:,.0f}".replace(",", " ")},
                {"Статья": "Парковки многоуровневые", "у.е.": f"{cb.parking_multilevel:,.0f}".replace(",", " ")},
                {"Статья": "Парковки подземные", "у.е.": f"{cb.parking_underground:,.0f}".replace(",", " ")},
                {"Статья": "— Σ зданий и сооружений", "у.е.": f"{cb.shell_total:,.0f}".replace(",", " ")},
                {"Статья": "Сети", "у.е.": f"{cb.networks:,.0f}".replace(",", " ")},
                {"Статья": "Благоустройство", "у.е.": f"{cb.landscaping:,.0f}".replace(",", " ")},
                {"Статья": "Проектирование", "у.е.": f"{cb.design:,.0f}".replace(",", " ")},
                {"Статья": "Непредвиденные", "у.е.": f"{cb.contingency:,.0f}".replace(",", " ")},
                {"Статья": "Земля + ТУ + снос", "у.е.": f"{cb.fixed:,.0f}".replace(",", " ")},
                {"Статья": "**Итого себестоимость**", "у.е.": f"**{cb.total:,.0f}**".replace(",", " ")},
            ]
            st.dataframe(pd.DataFrame(cost_rows), hide_index=True, use_container_width=True)

            # Выручка по источникам
            st.markdown("**Выручка**")
            rev_rows = [
                {"Источник": "Жильё (м² квартир)", "у.е.": f"{rb.residential:,.0f}".replace(",", " ")},
                {"Источник": "Парковки открытые", "у.е.": f"{rb.parking_open:,.0f}".replace(",", " ")},
                {"Источник": "Парковки многоуровневые", "у.е.": f"{rb.parking_multilevel:,.0f}".replace(",", " ")},
                {"Источник": "Парковки подземные", "у.е.": f"{rb.parking_underground:,.0f}".replace(",", " ")},
                {"Источник": "ВПП коммерческая", "у.е.": f"{rb.vpp_commercial:,.0f}".replace(",", " ")},
                {"Источник": "**Итого выручка**", "у.е.": f"**{rb.total:,.0f}**".replace(",", " ")},
            ]
            st.dataframe(pd.DataFrame(rev_rows), hide_index=True, use_container_width=True)

            # Метрики
            st.markdown("**Метрики**")
            mc1, mc2, mc3, mc4 = st.columns(4)
            mc1.metric("Прибыль", f"{e.profit:+,.0f} у.е.".replace(",", " "))
            mc2.metric("Маржа", f"{e.margin * 100:.1f}%" if rb.total > 0 else "—")
            mc3.metric("ROI", f"{e.roi * 100:.1f}%" if cb.total > 0 else "—")
            mc4.metric(
                "Прибыль/м² участка",
                f"{e.profit_per_site_m2:+,.2f}".replace(",", " "),
            )

    # 📋 Полный аудит
    with st.expander("📋 Полный аудит (все TEP-поля + источники)", expanded=False):
        df = results_to_audit_dataframe([("Текущий", result)])
        st.dataframe(df, hide_index=True, use_container_width=True)


# ---------------------------------------------------------------------------
# Кнопки внизу: добавить в сравнение, скачать xlsx
# ---------------------------------------------------------------------------

def _render_actions_inline(result: TEPResult, default_name: str) -> None:
    """Inline-actions: text_input + кнопки «Добавить» / «Скачать xlsx».

    Используется внутри блока «Основные показатели» — без отдельного разделителя
    сверху (его рисует caller, чтобы actions были ВНУТРИ container).
    """
    c1, c2, c3 = st.columns([2, 1, 1])

    with c1:
        scenario_name = st.text_input(
            "Имя сценария для сравнения",
            value=default_name,
            placeholder="Введите название расчёта",
            key="scenario_name_input",
            label_visibility="collapsed",
        )
    with c2:
        if st.button("➕ Добавить в сравнение", use_container_width=True):
            st.session_state.scenarios.append((scenario_name, result))
            st.toast(f"Сценарий «{scenario_name}» добавлен", icon="✅")
            st.rerun()
    with c3:
        # xlsx-экспорт текущего сценария
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            to_xlsx([(scenario_name, result)], tmp_path)
            with open(tmp_path, "rb") as f:
                xlsx_bytes = f.read()
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        st.download_button(
            "💾 Скачать xlsx",
            xlsx_bytes,
            file_name=f"{scenario_name}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )


def render_actions(result: TEPResult, default_name: str) -> None:
    """DEPRECATED: actions теперь встроены в render_kpi через scenario_default_name."""
    st.markdown("---")
    _render_actions_inline(result, default_name)


# ---------------------------------------------------------------------------
# Вкладка «Сравнение сценариев»
# ---------------------------------------------------------------------------

def render_comparison_tab() -> None:
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
        st.download_button(
            "💾 Скачать xlsx-сравнение",
            xlsx_bytes,
            file_name="comparison.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
