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
    from urban_model.calculations.warning_codes import strip_code
    for w in result.warnings:
        st.warning(f"⚠️ {strip_code(w)}")

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
        st.info(
            f"💡 **Резерв обусловлен нормативом плотности.** Население достигло "
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

def render_kpi(result: TEPResult, *, scenario_default_name: str | None = None) -> None:
    """KPI-блок с возможностью встроить кнопки «Добавить в сравнение» и xlsx.

    Если scenario_default_name задан — внутри того же блока (под линией
    разделителя) рисуются actions: text_input + добавить/скачать.
    """
    # === Основные показатели в едином блоке с возможностью копирования ===
    with st.container(border=True):
        # v0.9.20: заголовок + компактная popover-сводка на одной строке.
        # Раньше expander «📋 Сводка (с кнопкой копирования)» занимал всю
        # ширину, кнопка копирования была далеко справа. Теперь popover
        # открывается прямо под кнопкой — кнопка копирования рядом.
        header_col, copy_col = st.columns([10, 2])
        with header_col:
            st.markdown("##### 📊 Основные показатели")

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
                f"\n\n— Оценка выгодности —\n"
                f"Баллы (итого): {e.profit:+,.0f}\n"
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
            with st.popover("📋 Сводка", use_container_width=True):
                # st.code рисует блок со ВСТРОЕННОЙ кнопкой копирования
                # в правом верхнем углу. Streamlit-нативный механизм.
                st.code(summary_text, language=None)

        # v0.9.21: layout «KPI слева 60% + donut справа 40%» — пользователь
        # хочет видеть распределение территории на одном уровне с метриками.
        # Все ряды st.columns(4) создаются ВНУТРИ kpi_col; затем
        # c1.metric/c5.metric/... рисуют в этой колонке. Donut —
        # отдельно в donut_col, на одном вертикальном уровне.
        kpi_col, donut_col = st.columns([3, 2], gap="medium")
        with kpi_col:
            c1, c2, c3, c4 = st.columns(4)  # ряд 1
            c5, c6, c7, c8 = st.columns(4)  # ряд 2
        kit_help = (
            "КИТ по ПЗЗ СПб = площадь квартир / ЗУ жилой застройки. "
            f"Норматив. потолок: {result.kit_normative_max.value} "
            f"(ДПТ: {'да' if result.kit_normative_max.value == 2.5 else 'нет'})"
        )
        c1.metric("КИТ (ПЗЗ)", f"{result.kit.value:.3f}", help=kit_help)
        # v0.10.10 (#1): КИТ по зонам — сразу под метрикой КИТ (в её колонке).
        if result.floor_clusters_detail:
            _zk = " · ".join(
                f"**{d['label']}**: {d['kit']:.3f}"
                for d in result.floor_clusters_detail
            )
            c1.caption(f"🏗 КИТ зон: {_zk}")
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

        # === Ряд 2: социалка / парковки / ЗНОП ===
        # c5..c8 уже созданы выше внутри kpi_col.

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
            help="Принятая суммарная вместимость и число объектов СОШ.",
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

        # v0.9.21: donut в правой колонке на одном уровне с метриками
        with donut_col:
            _render_balance_bar(result)

        # === Ряд 3: экономика — ВНУТРИ kpi_col, под метриками ===
        # v0.9.26: раньше «Оценка выгодности» рендерилась на всю ширину
        # под обеими колонками, оставляя пустое пространство под ДОО/СОШ
        # рядом с donut'ом. Теперь блок встаёт В ЛЕВУЮ колонку, заполняя
        # вертикаль на одном уровне с treemap'ом справа.
        if result.economy is not None:
            with kpi_col:
                st.markdown("---")
                e = result.economy
                score_help = (
                    "Баллы выгодности проекта — безразмерный индикатор для "
                    "сравнения вариантов. Положительные значения = проект "
                    "выгоднее базовой ситуации; отрицательные = убыточнее."
                )
                _social_cost = (
                    e.cost.kindergarten + e.cost.school + e.cost.social_parking
                )
                _has_social = _social_cost > 0.5 or e.revenue.social_compensation > 0.5
                ec1, ec2, ec3, ec4 = st.columns(4)
                ec1.metric(
                    "💰 Оценка выгодности",
                    f"{e.profit:+,.0f}".replace(",", " "),
                    delta=("плюс" if e.profit >= 0 else "минус"),
                    delta_color=("normal" if e.profit >= 0 else "inverse"),
                    help=score_help,
                )
                if _has_social:
                    # v0.9.28.2: соцобъекты вынесены отдельно от прибыли.
                    ec2.metric(
                        "Без соц. нагрузки",
                        f"{e.profit_before_social:+,.0f}".replace(",", " "),
                        delta=("плюс" if e.profit_before_social >= 0 else "минус"),
                        delta_color=("normal" if e.profit_before_social >= 0 else "inverse"),
                        help="Прибыль проекта без социальных обязательств "
                             "(profit + чистая соц. нагрузка).",
                    )
                    ec3.metric(
                        "Соц. нагрузка",
                        f"{-e.net_social_burden:+,.0f}".replace(",", " "),
                        delta=("убыток" if e.net_social_burden > 0 else "плюс"),
                        delta_color=("inverse" if e.net_social_burden > 0 else "normal"),
                        help="Себестоимость ДОО/СОШ/соц.парковок за вычетом "
                             "компенсации города. Отрицательное = тянет проект в минус.",
                    )
                    ec4.metric(
                        "Маржа",
                        f"{e.margin * 100:.1f}%" if e.revenue.total > 0 else "—",
                        help="profit / revenue",
                    )
                else:
                    ec2.metric(
                        "Маржа",
                        f"{e.margin * 100:.1f}%" if e.revenue.total > 0 else "—",
                        help="profit / revenue",
                    )
                    ec3.metric(
                        "ROI",
                        f"{e.roi * 100:.1f}%" if e.cost.total > 0 else "—",
                        help="profit / cost",
                    )
                    ec4.metric(
                        "Cost / Revenue",
                        f"{int(e.cost.total):,} / {int(e.revenue.total):,}".replace(",", " "),
                        help="Сумма cost-компонентов и revenue-компонентов в баллах",
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
            "znop": result.znop_area.value,
            "intra_quarter_driveways": result.driveways_intra_quarter_area.value,
            "parking_multilevel": result.parking_multilevel_area.value,
        }

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
        with st.expander("🏗 Кластеры этажности (по зонам)", expanded=False):
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
                "Оценка выгодности",
                f"{e.profit:+,.0f}".replace(",", " "),
                help="Безразмерный индикатор для сравнения вариантов (revenue − cost).",
            )
            mc2.metric(
                "Прибыль без соц.",
                f"{e.profit_before_social:+,.0f}".replace(",", " "),
                help="Прибыль проекта без социальных обязательств "
                     "(profit + чистая соц. нагрузка). Показывает «чистый» девелопмент.",
            )
            mc3.metric(
                "Запас / м²",
                f"{e.profit_per_site_m2:+.3f}",
                help="Прибыль на 1 м² участка — основная метрика для сравнения территорий.",
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

    # 📋 Полный аудит
    with st.expander("📋 Полный аудит (все TEP-поля + источники)", expanded=False):
        df = results_to_audit_dataframe([("Текущий", result)])
        st.dataframe(df, hide_index=True, use_container_width=True)


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
        # Подпись внутри плитки только если плитка большая (≥6%)
        if r["Pct"] >= 6:
            label = f"{r['Компонент']}\n{r['Pct']:.0f}%"
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
    st.markdown("**⚖️ Распределение территории**")
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


def _variant_report_bytes(name: str, result: TEPResult) -> bytes:
    """DOCX-отчёт по варианту с мемоизацией по (имя + сигнатура расчёта).
    Пересобирается только когда меняются параметры — поэтому download_button
    отдаёт готовые байты в ОДИН клик, без отдельной кнопки «Сформировать»."""
    sig = (name, _result_sig(result))
    if st.session_state.get("_vr_sig") != sig:
        from urban_model.export import build_variant_report
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp_v:
            vpath = tmp_v.name
        try:
            build_variant_report(name, result, vpath)
            with open(vpath, "rb") as f:
                st.session_state["_vr_bytes"] = f.read()
        finally:
            try:
                os.unlink(vpath)
            except OSError:
                pass
        st.session_state["_vr_sig"] = sig
    return st.session_state["_vr_bytes"]


def _render_actions_inline(result: TEPResult, default_name: str) -> None:
    """Inline-actions: имя + «Добавить в сравнение», ниже — выгрузки рядом."""
    # v0.10.9 (#5): всё поджато влево — имя + кнопки кластеризованы в левой
    # части, чтобы не «уезжали» вправо и не терялись из виду.
    # v0.10.10 (#3): кнопки заполняют свои узкие колонки (use_container_width) —
    # стоят вплотную друг к другу слева, без разрывов и без растяжки на весь экран.
    c1, c2, _c3 = st.columns([2.5, 2, 3.5])
    with c1:
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
    with c2:
        if st.button("➕ Добавить в сравнение", use_container_width=True):
            st.session_state.scenarios.append((scenario_name, result))
            st.toast(f"Сценарий «{scenario_name}» добавлен", icon="✅")
            st.rerun()

    # Выгрузки — вплотную, слева. Отчёт DOCX — в ОДИН клик (мемоизация).
    d1, d2, _ = st.columns([1.7, 2.3, 4.5])
    with d1:
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
    with d2:
        st.download_button(
            "📄 Отчёт по варианту (DOCX)",
            _variant_report_bytes(default_name, result),
            file_name=f"Отчёт — {default_name}.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
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
        dl1, dl2, _ = st.columns([1, 1, 2])
        dl1.download_button(
            "💾 Скачать xlsx-сравнение",
            xlsx_bytes,
            file_name="comparison.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        # v0.10.8 (#4): DOCX-отчёт сравнения — в ОДИН клик. Мемоизация по
        # сигнатуре всех сценариев: пересобирается только при изменении набора,
        # а не на каждый ре-рендер (matplotlib/docx не тормозят вкладку).
        cmp_sig = tuple((nm, _result_sig(t)) for nm, t in pairs)
        if st.session_state.get("_cmp_docx_sig") != cmp_sig:
            from urban_model.export import build_report
            with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp_d:
                docx_path = tmp_d.name
            try:
                build_report(pairs, docx_path)
                with open(docx_path, "rb") as f:
                    st.session_state["_cmp_docx_bytes"] = f.read()
            finally:
                try:
                    os.unlink(docx_path)
                except OSError:
                    pass
            st.session_state["_cmp_docx_sig"] = cmp_sig
        dl2.download_button(
            "📄 Скачать отчёт (DOCX)",
            st.session_state["_cmp_docx_bytes"],
            file_name="urban_report.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
