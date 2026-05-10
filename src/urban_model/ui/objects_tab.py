"""Streamlit-вкладка «Объекты» — табличный редактор пользовательских объектов.

Использует `st.data_editor` для редактирования списка объектов в табличной
форме (имя, площадь ЗУ, ВРИ, общая площадь). Список хранится в
`st.session_state.custom_objects`; сайдбар-форма расчёта читает его оттуда.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

# Допустимые ВРИ-коды для пользовательских объектов.
# Соответствует справочнику dict_vri.yaml; можно расширять.
ALLOWED_VRI = [
    "3.4", "3.5", "3.6", "3.7", "4.0", "4.1", "4.4", "4.5", "4.6", "5.1",
]
VRI_LABELS = {
    "3.4": "3.4 — здравоохранение",
    "3.5": "3.5 — образование",
    "3.6": "3.6 — культура",
    "3.7": "3.7 — религия",
    "4.0": "4.0 — обслуживание (общее)",
    "4.1": "4.1 — деловое управление (офисы)",
    "4.4": "4.4 — магазины",
    "4.5": "4.5 — банки/связь",
    "4.6": "4.6 — общепит",
    "5.1": "5.1 — спорт (ФОК)",
}


def render_objects_tab() -> None:
    st.markdown("## 📦 Произвольные объекты на территории квартала")
    st.caption(
        "Размещение объектов вне базовых классов (ДОО / СОШ / ВПП): офис, "
        "ФОК, поликлиника, торговля и т.п. Каждый объект занимает свой ЗУ "
        "(вычитается из доступной территории квартала). Парковки и "
        "озеленение считаются по ВРИ-коду. Все изменения автоматически "
        "применяются к расчёту на вкладке «Расчёт»."
    )

    # Инициализация session_state
    if "custom_objects" not in st.session_state:
        st.session_state.custom_objects = []

    # Преобразуем в DataFrame
    rows = []
    for obj in st.session_state.custom_objects:
        rows.append({
            "Название": obj.get("name", "Объект"),
            "Площадь ЗУ, м²": float(obj.get("plot_area_m2", 1000.0)),
            "ВРИ-код": obj.get("vri_code", "4.4"),
            "Общая площадь, м²": (
                float(obj["floor_area_m2"])
                if obj.get("floor_area_m2") is not None
                else float(obj.get("plot_area_m2", 1000.0))
            ),
        })

    # Если пусто — стартовая заготовка
    if not rows:
        rows = [{
            "Название": "Офис",
            "Площадь ЗУ, м²": 2_000.0,
            "ВРИ-код": "4.1",
            "Общая площадь, м²": 2_000.0,
        }]

    df = pd.DataFrame(rows)

    edited_df = st.data_editor(
        df,
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "Название": st.column_config.TextColumn(
                "Название", required=True, max_chars=50,
            ),
            "Площадь ЗУ, м²": st.column_config.NumberColumn(
                "Площадь ЗУ, м²",
                help="Территория объекта; вычитается из доступной площади квартала",
                min_value=10.0,
                max_value=200_000.0,
                step=100.0,
                format="%.0f",
                required=True,
            ),
            "ВРИ-код": st.column_config.SelectboxColumn(
                "ВРИ-код",
                help="Определяет нормативы парковок и озеленения для объекта",
                options=ALLOWED_VRI,
                required=True,
            ),
            "Общая площадь, м²": st.column_config.NumberColumn(
                "Общая площадь, м²",
                help=(
                    "Сумма площадей этажных перекрытий (для расчёта парковок "
                    "и озеленения). Для одноэтажного объекта = площади ЗУ."
                ),
                min_value=10.0,
                max_value=500_000.0,
                step=100.0,
                format="%.0f",
            ),
        },
        key="objects_editor",
    )

    # Кнопка «Применить»
    c1, c2 = st.columns([1, 5])
    if c1.button("💾 Применить изменения", type="primary", use_container_width=True):
        new_list = []
        for _, row in edited_df.iterrows():
            try:
                new_list.append({
                    "name": str(row["Название"]).strip() or "Объект",
                    "plot_area_m2": float(row["Площадь ЗУ, м²"]),
                    "vri_code": str(row["ВРИ-код"]),
                    "floor_area_m2": (
                        float(row["Общая площадь, м²"])
                        if pd.notna(row["Общая площадь, м²"]) else None
                    ),
                })
            except (ValueError, TypeError, KeyError):
                continue  # пропускаем некорректные строки
        st.session_state.custom_objects = new_list
        st.toast(f"✅ Применено: {len(new_list)} объект(а/ов)", icon="📦")
        st.rerun()

    if c2.button("🗑️ Очистить все", use_container_width=False):
        st.session_state.custom_objects = []
        st.rerun()

    # Подсказка по ВРИ-кодам
    with st.expander("ℹ️ Расшифровка ВРИ-кодов", expanded=False):
        for code, label in VRI_LABELS.items():
            st.markdown(f"- **{label}**")
        st.caption(
            "Полный справочник: `dict/dict_vri.yaml` (Приказ Росреестра № П-0412)"
        )

    # Сводка по текущим объектам
    if st.session_state.custom_objects:
        st.markdown("### Применено к расчёту")
        applied_df = pd.DataFrame(st.session_state.custom_objects)
        applied_df = applied_df.rename(columns={
            "name": "Название",
            "plot_area_m2": "Площадь ЗУ, м²",
            "vri_code": "ВРИ-код",
            "floor_area_m2": "Общая площадь, м²",
        })
        st.dataframe(applied_df, hide_index=True, use_container_width=True)
        total_plot = sum(o["plot_area_m2"] for o in st.session_state.custom_objects)
        st.caption(f"**Итого: {len(st.session_state.custom_objects)} объект(а/ов), {total_plot:,.0f} м² ЗУ**".replace(",", " "))
    else:
        st.info("Объекты не заданы — расчёт идёт без них.")
