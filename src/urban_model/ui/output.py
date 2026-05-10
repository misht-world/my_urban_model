"""Рендер главной области: KPI-карточки + раскрывающиеся секции + сравнение."""

from __future__ import annotations

import io

import pandas as pd
import streamlit as st

from urban_model.export import results_to_dataframe, to_xlsx
from urban_model.export.table import results_to_audit_dataframe
from urban_model.models.result import Status, TEPField, TEPResult
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

def render_header(result: TEPResult) -> None:
    feasible = result.balance.is_feasible
    if feasible:
        st.success(
            f"✅ **Баланс сходится.** КИТ = {result.kit.value:.3f}"
            f"  ·  Резерв: {fmt_int(result.balance.surplus)} м²"
        )
    else:
        st.error(
            f"❌ **Дефицит баланса.** КИТ = {result.kit.value:.3f}"
            f"  ·  Не хватает: {fmt_int(-result.balance.surplus)} м²"
        )

    if result.limiting_factor:
        st.caption(f"🔻 **Ограничивающий фактор:** {result.limiting_factor}")
    for w in result.warnings:
        st.warning(f"⚠️ {w}")


# ---------------------------------------------------------------------------
# KPI-карточки
# ---------------------------------------------------------------------------

def render_kpi(result: TEPResult) -> None:
    c1, c2, c3, c4 = st.columns(4)
    kit_help = (
        "КИТ по ПЗЗ СПб = площадь квартир / ЗУ жилой застройки. "
        f"Норматив. потолок: {result.kit_normative_max.value} "
        f"(ДПТ: {'да' if result.kit_normative_max.value == 2.5 else 'нет'})"
    )
    c1.metric("КИТ (ПЗЗ)", f"{result.kit.value:.3f}", help=kit_help)
    c2.metric("Население", fmt_int(result.population.value), help="чел.")
    c3.metric("Площадь квартир", fmt_m2(result.apartments_area.value))
    surplus = result.balance.surplus
    delta_color = "normal" if surplus >= 0 else "inverse"
    c4.metric(
        "Резерв баланса",
        fmt_m2(surplus),
        delta=("OK" if surplus >= 0 else "ДЕФИЦИТ"),
        delta_color=delta_color,
    )


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
        "Статус": field.status.value,
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
            _row("Плотность квартала (GFA / S_квартала, внутр.)",
                 result.block_density, fmt_float),
            _row("Общая площадь жилых зданий (GFA)", result.gfa, fmt_m2),
            _row("Площадь квартир", result.apartments_area, fmt_m2),
        ]
        if result.built_in_area.value and result.built_in_area.value > 0:
            rows.append(_row(
                f"ВПП (ВРИ {result.built_in_vri_code})",
                result.built_in_area, fmt_m2,
            ))
            rows.append(_row("Парковки ВПП", result.built_in_parking_places,
                             fmt_int, " м/м"))
            rows.append(_row("Озеленение ВПП", result.built_in_greening_area, fmt_m2))
        rows += [
            _row("Площадь застройки (footprint)", result.housing_footprint, fmt_m2),
            _row("ЗУ жилой застройки", result.housing_lot_area, fmt_m2),
            _row("Население", result.population, fmt_int, " чел."),
            _row("Плотность", result.density_chel_per_ga, lambda x: f"{x:.1f}", " чел./га"),
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
                "znop": "ЗНОП",
                "intra_quarter_driveways": "Внутриквартальные проезды",
                "parking_multilevel": "Многоуровневые паркинги",
                "built_in_greening": "Озеленение ВПП",
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

    # 📋 Полный аудит
    with st.expander("📋 Полный аудит (все TEP-поля + источники)", expanded=False):
        df = results_to_audit_dataframe([("Текущий", result)])
        st.dataframe(df, hide_index=True, use_container_width=True)


# ---------------------------------------------------------------------------
# Кнопки внизу: добавить в сравнение, скачать xlsx
# ---------------------------------------------------------------------------

def render_actions(result: TEPResult, default_name: str) -> None:
    st.markdown("---")
    c1, c2, c3 = st.columns([2, 1, 1])

    with c1:
        scenario_name = st.text_input(
            "Имя сценария для сравнения",
            value=default_name,
            key="scenario_name_input",
            label_visibility="collapsed",
        )
    with c2:
        if st.button("➕ Добавить в сравнение", use_container_width=True):
            st.session_state.scenarios.append((scenario_name, result))
            st.toast(f"Сценарий «{scenario_name}» добавлен", icon="✅")
    with c3:
        # xlsx-экспорт текущего сценария
        buf = io.BytesIO()
        # to_xlsx требует path → делаем временный путь и читаем
        import tempfile, os
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
        import tempfile, os
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
