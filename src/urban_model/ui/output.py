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
        # v0.8.7: surplus > 0, но feasible=False → нарушен норматив озеленения
        # квартала. Это самая частая ловушка на малых кварталах.
        bal = result.balance
        if bal.surplus >= 0 and bal.greening_actual < bal.greening_required - 1e-3:
            deficit = bal.greening_required - bal.greening_actual
            st.error(
                f"❌ **Норматив озеленения квартала не выполняется.** "
                f"Требуется ≥ {fmt_int(bal.greening_required)} м² "
                f"(25% от квартала), факт {fmt_int(bal.greening_actual)} м² — "
                f"дефицит {fmt_int(deficit)} м². "
                f"Резерв территории есть ({fmt_int(bal.surplus)} м²), но "
                f"норматив не пускает увеличивать жильё."
            )
            st.info(
                "💡 Возможные действия: (1) включите **ЗНОП** в левой колонке "
                "— добавит озеленение по нормативу; "
                "(2) увеличьте площадь квартала; "
                "(3) отключите **«Соблюдать норматив 25% озеленения»** — "
                "если зелень компенсируется вне границ территории."
            )
        else:
            st.error(
                f"❌ **Дефицит баланса территории.** КИТ = {kit_v:.3f}"
                f"  ·  Не хватает: {fmt_int(-bal.surplus)} м²"
            )

    if result.limiting_factor:
        st.caption(f"🔻 **Ограничивающий фактор:** {result.limiting_factor}")
    # v0.8.6: префиксы [CODE] из warning_codes.WC прячем от пользователя —
    # они служат для машинной фильтрации (Optuna feasibility, тесты).
    from urban_model.calculations.warning_codes import WC, any_with_code, strip_code
    for w in result.warnings:
        st.warning(f"⚠️ {strip_code(w)}")

    # v0.9.11 (AUDIT S-2): если есть WARNING про вместимость соцобъекта меньше
    # нормативного минимума — предложить пользователю «Только потребность».
    # Это типичная ситуация на квартале < 5000 м² (3-50 чел населения),
    # где ДОО на 5 мест или СОШ на 25 мест физически невозможны.
    if any_with_code(result.warnings, WC.SOC_CAP_MIN_BELOW):
        st.info(
            "💡 **Подсказка:** на малых кварталах нормативные ДОО/СОШ "
            "невозможны (вместимость < минимума). Если объект размещается "
            "за пределами квартала — включите «Только рассчитать потребность» "
            "в плитках ДОО/СОШ на вкладке «Параметры». Тогда место будет "
            "учитываться, но ЗУ объекта не войдёт в баланс."
        )


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
        # v0.9.19: вместо кастомной HTML-кнопки (которая блокировалась
        # в Streamlit iframe) используем встроенный механизм Streamlit —
        # `st.code()` рисует код-блок со ВСТРОЕННОЙ кнопкой копирования
        # в правом верхнем углу. Она работает в любом окружении,
        # потому что Streamlit сам управляет правами буфера обмена.
        st.markdown("##### 📊 Основные показатели")
        # Сводка
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
        if result.economy is not None:
            e = result.economy
            summary_text += (
                f"\n\n— Оценка выгодности —\n"
                f"Баллы: {e.profit:+,.0f}\n"
                f"Маржа: {e.margin * 100:.1f}%; ROI: {e.roi * 100:.1f}%"
            ).replace(",", " ")
        with st.expander("📋 Сводка (с кнопкой копирования)", expanded=False):
            # st.code рисует blok-код с встроенной copy-кнопкой в углу
            st.code(summary_text, language=None)

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
            help=(
                "Свободная часть квартала после вычета всех компонентов "
                "(жильё, ДОО/СОШ, парковки, проезды, ЗНОП). "
                "Эта территория автоматически засчитывается как зелёное "
                "открытое пространство в нормативе 25% озеленения. "
                "0 = квартал использован полностью; положительный = "
                "запас под манёвр (двор/площадка/благоустройство)."
            ),
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

        # === Ряд «Баланс территории» (v0.9.17) — компактная stacked-bar ===
        # Раньше баланс был в свёрнутом expander внизу — пользователю
        # приходилось скроллить. Теперь горизонтальная диаграмма прямо
        # в KPI-блоке: сразу видно как разложена территория квартала.
        _render_balance_bar(result)

        # === Ряд 3: экономика (v0.9.17) — компактный блок «Оценка выгодности» ===
        # Раньше показывалась «Прибыль в у.е.» — пользователи воспринимали
        # это как рубли. Теперь — баллы выгодности проекта (безразмерный
        # индикатор для сравнения вариантов). Маржа/ROI убраны в expander
        # под блоком — не отвлекают, доступны если нужны.
        if result.economy is not None:
            st.markdown("---")
            e = result.economy
            score_help = (
                "Баллы выгодности проекта — безразмерный индикатор для "
                "сравнения вариантов. Положительные значения = проект "
                "выгоднее базовой ситуации; отрицательные = убыточнее."
            )
            ec1, ec2 = st.columns([1, 3])
            with ec1:
                st.metric(
                    "💰 Оценка выгодности",
                    f"{e.profit:+,.0f}".replace(",", " "),
                    delta=("плюс" if e.profit >= 0 else "минус"),
                    delta_color=("normal" if e.profit >= 0 else "inverse"),
                    help=score_help,
                )
            with ec2:
                with st.expander("Подробные финансовые метрики", expanded=False):
                    p1, p2, p3 = st.columns(3)
                    p1.metric(
                        "Маржа",
                        f"{e.margin * 100:.1f}%" if e.revenue.total > 0 else "—",
                        help="profit / revenue",
                    )
                    p2.metric(
                        "ROI",
                        f"{e.roi * 100:.1f}%" if e.cost.total > 0 else "—",
                        help="profit / cost",
                    )
                    p3.metric(
                        "Себестоимость / Выручка",
                        f"{e.cost.total:,.0f} / {e.revenue.total:,.0f}".replace(",", " "),
                        help="Сумма всех cost-компонентов и revenue-компонентов в баллах",
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

    # ⚖️ Баланс территории (детализация). v0.9.17: диаграмма теперь в
    # KPI-блоке, здесь — табличная детализация на случай нужды.
    with st.expander("⚖️ Баланс территории (таблица)", expanded=False):
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

    # 💰 Экономика
    if result.economy is not None:
        with st.expander("💰 Экономика (детализация)", expanded=False):
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
                _eco_row("Парковки соцобъектов", cb.social_parking),
                _eco_row("Спортивные сооружения", cb.sport),
                _eco_row("Пользовательские объекты", cb.custom_objects),
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

            st.markdown("**Метрики**")
            mc1, mc2, mc3 = st.columns(3)
            mc1.metric(
                "Оценка выгодности",
                f"{e.profit:+,.0f}".replace(",", " "),
                help="Безразмерный индикатор для сравнения вариантов.",
            )
            mc2.metric("Маржа", f"{e.margin * 100:.1f}%" if rb.total > 0 else "—")
            mc3.metric("ROI", f"{e.roi * 100:.1f}%" if cb.total > 0 else "—")

    # 📋 Полный аудит
    with st.expander("📋 Полный аудит (все TEP-поля + источники)", expanded=False):
        df = results_to_audit_dataframe([("Текущий", result)])
        st.dataframe(df, hide_index=True, use_container_width=True)


def _render_balance_bar(result: TEPResult) -> None:
    """Компактная stacked-bar диаграмма баланса территории (v0.9.17).

    Все компоненты + резерв в горизонтальной полосе. Помогает за секунду
    увидеть «куда уходит территория» — без необходимости открывать таблицу.
    """
    import altair as alt
    b = result.balance
    site_area = b.site_area
    if site_area <= 0:
        return

    pretty = {
        "housing_lot": "ЗУ жилой застройки",
        "kindergarten_plot": "ДОО",
        "school_plot": "СОШ",
        "sport_facilities": "Спорт. сооружения",
        "social_parking_plot": "Парковки соцобъектов",
        "znop": "ЗНОП",
        "intra_quarter_driveways": "Внутрикв. проезды",
        "parking_multilevel": "Многоуровн. паркинги",
        "custom_objects": "Доп. объекты",
    }
    # Цвета по типам — деловая палитра
    colors = {
        "ЗУ жилой застройки":      "#4A90E2",
        "ДОО":                     "#F5A623",
        "СОШ":                     "#E94B3C",
        "Спорт. сооружения":       "#7ED321",
        "Парковки соцобъектов":    "#9B9B9B",
        "ЗНОП":                    "#417505",
        "Внутрикв. проезды":       "#B8B8B8",
        "Многоуровн. паркинги":    "#50E3C2",
        "Доп. объекты":            "#BD10E0",
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
    df["dummy"] = "Квартал"
    domain = list(colors.keys())
    range_ = [colors[d] for d in domain]
    # v0.9.19: явный size bar'а (40px) + height chart-area = 100.
    # Раньше height=90 без size давало тонкую «линейку» — bar занимал
    # маленькую часть chart-area по высоте.
    chart = (
        alt.Chart(df)
        .mark_bar(stroke="white", strokeWidth=1.5, size=50)
        .encode(
            x=alt.X("Площадь:Q", stack="normalize",
                    axis=alt.Axis(format="%", title=None, labelFontSize=11)),
            y=alt.Y("dummy:N", title=None, axis=alt.Axis(labels=False, ticks=False)),
            color=alt.Color(
                "Компонент:N",
                scale=alt.Scale(domain=domain, range=range_),
                legend=alt.Legend(title=None, orient="bottom", columns=5,
                                  labelFontSize=12, symbolSize=120,
                                  rowPadding=4, columnPadding=12),
            ),
            order=alt.Order("Площадь:Q", sort="descending"),
            tooltip=[
                alt.Tooltip("Компонент:N"),
                alt.Tooltip("Площадь:Q", title="м²", format=",.0f"),
                alt.Tooltip("Доля:N"),
            ],
        )
        .properties(height=100)
        .configure_view(strokeWidth=0)
    )
    st.markdown("**⚖️ Распределение территории**")
    st.altair_chart(chart, use_container_width=True)
    st.caption("Наведите курсор на сегмент для подробностей. Цвета — в легенде ниже.")


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
