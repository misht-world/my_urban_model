"""Вкладка «Параметры»: полноэкранная форма ввода → UserInputs.

v0.5.8 — переехала из sidebar на отдельную вкладку. Параметры разложены
в 2 колонки для компактности. Sidebar свёрнут по умолчанию.
"""

from __future__ import annotations

from dataclasses import dataclass

import streamlit as st

from urban_model.models import CalculationOptions, Site
from urban_model.models.built_in import BuiltInArea
from urban_model.models.custom_object import CustomObject
from urban_model.models.parking import ParkingConfig
from urban_model.models.social import KindergartenSpec, SchoolSpec


@dataclass
class UserInputs:
    site: Site
    options: CalculationOptions
    mode: str  # "max_kit" | "with_reserve" | "verify"
    target_surplus_m2: float
    verify_kit_value: float
    vpp_auto_one_floor: bool


def render_params_tab() -> UserInputs:
    """Полноэкранная форма параметров на отдельной вкладке.

    Возвращает собранные UserInputs. Изменения в форме автоматически
    триггерят пересчёт во вкладке «Расчёт» (Streamlit rerun).
    """
    st.markdown("### Параметры расчёта")
    st.caption(
        "Настройте квартал и нормативные ограничения. Все изменения "
        "применяются автоматически — перейдите на вкладку «Расчёт», "
        "чтобы увидеть результат."
    )
    st.markdown("---")

    # ==================================================================
    # Верхняя строка: режим расчёта (полная ширина)
    # ==================================================================
    col_mode1, col_mode2 = st.columns([3, 2])
    with col_mode1:
        mode_label = st.radio(
            "Режим расчёта",
            [
                "Максимальный КИТ",
                "КИТ с целевым резервом территории",
                "Проверка конкретного КИТ",
            ],
            index=0,
            horizontal=True,
            key="calc_mode_label",
        )
    with col_mode2:
        target_surplus_m2 = 0.0
        verify_kit_value = 1.5
        if mode_label == "Максимальный КИТ":
            mode = "max_kit"
            st.caption("Бисекция: найти максимальный КИТ, при котором все нормативы выполняются.")
        elif mode_label == "КИТ с целевым резервом территории":
            mode = "with_reserve"
            target_surplus_m2 = float(
                st.number_input(
                    "Целевой резерв, м²",
                    min_value=0.0,
                    max_value=500_000.0,
                    value=5_000.0,
                    step=500.0,
                    key="target_surplus_m2",
                )
            )
        else:
            mode = "verify"
            verify_kit_value = float(
                st.number_input(
                    "Внутренняя плотность (block_density) для проверки",
                    min_value=0.1,
                    max_value=5.0,
                    value=1.5,
                    step=0.05,
                    key="verify_kit",
                    help=(
                        "Это плотность GFA/S_квартала. Фактический КИТ "
                        "(площадь квартир / ЗУ жилой) рассчитается."
                    ),
                )
            )

    st.markdown("---")

    # ==================================================================
    # Левая колонка: квартал + жильё + ЗНОП + проезды
    # Правая колонка: соцобъекты + парковки + ВПП
    # ==================================================================
    col_left, col_right = st.columns(2)

    # ─── ЛЕВАЯ КОЛОНКА ──────────────────────────────────────────────
    with col_left:
        # --- Квартал ---
        with st.container(border=True):
            st.markdown("##### Квартал")
            unit = st.radio(
                "Единицы площади",
                ["м²", "га"],
                horizontal=True,
                key="area_unit",
                label_visibility="collapsed",
            )
            if unit == "га":
                area_ga = st.number_input(
                    "Площадь квартала, га",
                    min_value=0.1,
                    max_value=200.0,
                    value=5.0,
                    step=0.1,
                    key="area_input_ga",
                )
                area_m2 = area_ga * 10_000
            else:
                area_m2 = st.number_input(
                    "Площадь квартала, м²",
                    min_value=1_000.0,
                    max_value=2_000_000.0,
                    value=50_000.0,
                    step=1_000.0,
                    key="area_input_m2",
                )
        site = Site(area_m2=area_m2)

        # --- Жильё ---
        with st.container(border=True):
            st.markdown("##### Жильё")
            c1, c2 = st.columns(2)
            floors = c1.number_input(
                "Этажность",
                min_value=1,
                max_value=40,
                value=12,
                step=1,
                key="floors",
            )
            with c2:
                st.write("")  # для выравнивания
                planning_doc = st.checkbox(
                    "ДПТ (документация по планировке)",
                    value=True,
                    key="planning_doc",
                    help="Без ДПТ нормативный потолок КИТ = 1.4; с ДПТ = 2.5.",
                )

        # --- ЗНОП ---
        with st.container(border=True):
            st.markdown("##### ЗНОП — зелёные насаждения общего пользования")
            znop_include = st.checkbox(
                "Учитывать ЗНОП",
                value=True,
                key="znop_include",
            )
            if znop_include:
                znop_mode = st.radio(
                    "Источник значения",
                    [
                        "По нормативу (зависит от КИТ ступенями: 0/3/4/6 м²/чел)",
                        "Задать вручную",
                    ],
                    key="znop_mode",
                )
                if znop_mode == "Задать вручную":
                    znop_value = st.number_input(
                        "ЗНОП, м²/чел",
                        min_value=0.0,
                        max_value=20.0,
                        value=6.0,
                        step=0.5,
                        key="znop_value",
                    )
                    znop_override = float(znop_value)
                else:
                    znop_override = None
            else:
                znop_override = 0.0
                st.caption("ЗНОП принудительно = 0 (не для нормативного режима).")

        # --- Проезды ---
        with st.container(border=True):
            st.markdown("##### Проезды")
            st.caption(
                "Значения по умолчанию приняты условно (точных нормативов "
                "СПб для этих долей нет). Переопределяйте под проект."
            )
            c1, c2 = st.columns(2)
            with c1:
                intra_use_override = st.checkbox(
                    "Внутриквартальные — задать",
                    value=False,
                    key="drive_intra_override",
                )
                intra_override = None
                if intra_use_override:
                    intra_pct = st.slider(
                        "Доля от S_квартала, %",
                        min_value=0, max_value=30,
                        value=10, step=1,
                        key="drive_intra_pct",
                    )
                    intra_override = intra_pct / 100
                else:
                    st.caption("По умолчанию: 10%")
            with c2:
                lot_use_override = st.checkbox(
                    "На ЗУ жилой — задать",
                    value=False,
                    key="drive_lot_override",
                )
                lot_override = None
                if lot_use_override:
                    lot_pct = st.slider(
                        "Доля от S_застройки, %",
                        min_value=0, max_value=300,
                        value=120, step=5,
                        key="drive_lot_pct",
                    )
                    lot_override = lot_pct / 100
                else:
                    st.caption("По умолчанию: 120%")

    # ─── ПРАВАЯ КОЛОНКА ─────────────────────────────────────────────
    with col_right:
        # --- ДОО ---
        with st.container(border=True):
            st.markdown("##### Дошкольные образовательные организации (ДОО)")
            include_kg = st.checkbox(
                "Учитывать ДОО",
                value=True,
                key="include_kg",
            )
            kg_btype_label = st.selectbox(
                "Тип здания ДОО",
                ["Отдельно стоящее", "Встроенно-пристроенное"],
                index=0,
                disabled=not include_kg,
                help=(
                    "Отдельно стоящее: вместимость 160–350 мест (РМД). "
                    "Встроенно-пристроенное: до 120 мест на 1-м этаже жилого дома."
                ),
                key="kg_btype_label",
            )
            kg_btype = "detached" if kg_btype_label == "Отдельно стоящее" else "built_in"
            kg_override = st.checkbox(
                "Задать число объектов вручную",
                value=False,
                disabled=not include_kg,
                key="kg_override",
            )
            kg_num_objects = None
            kg_capacity = None
            if include_kg and kg_override:
                c1, c2 = st.columns(2)
                kg_num_objects = c1.number_input(
                    "Кол-во ДОО",
                    min_value=1,
                    max_value=20,
                    value=2,
                    step=1,
                    key="kg_num_objects",
                )
                kg_capacity = c2.number_input(
                    "Мест в каждом",
                    min_value=40,
                    max_value=400,
                    value=160,
                    step=10,
                    key="kg_capacity",
                )

        # --- СОШ ---
        with st.container(border=True):
            st.markdown("##### Средние общеобразовательные школы (СОШ)")
            include_school = st.checkbox(
                "Учитывать СОШ",
                value=True,
                key="include_school",
            )
            c1, c2 = st.columns(2)
            with c1:
                school_pool = st.checkbox(
                    "С бассейном (+0.2 га)",
                    value=False,
                    disabled=not include_school,
                    key="school_pool",
                )
            with c2:
                school_sport = st.checkbox(
                    "Со спортивным ядром (+0.7 га)",
                    value=False,
                    disabled=not include_school,
                    key="school_sport",
                )
            sch_override = st.checkbox(
                "Задать число СОШ вручную",
                value=False,
                disabled=not include_school,
                key="sch_override",
            )
            sch_num_objects = None
            sch_capacity = None
            if include_school and sch_override:
                c1, c2 = st.columns(2)
                sch_num_objects = c1.number_input(
                    "Кол-во СОШ",
                    min_value=1,
                    max_value=10,
                    value=1,
                    step=1,
                    key="sch_num_objects",
                )
                sch_capacity = c2.number_input(
                    "Мест в каждой",
                    min_value=200,
                    max_value=2000,
                    value=550,
                    step=10,
                    key="sch_capacity",
                )

        # --- Парковки ---
        with st.container(border=True):
            st.markdown("##### Парковки")
            PARK_MODE_LABELS = {
                "Минимум открытых, остальное подземные (по умолчанию)": "min_open",
                "Все парковки открытые наземные": "all_open",
                "Задать доли вручную": "custom",
            }
            park_label = st.radio(
                "Размещение машино-мест",
                list(PARK_MODE_LABELS.keys()),
                index=0,
                key="park_mode_label",
            )
            park_mode = PARK_MODE_LABELS[park_label]
            if park_mode == "min_open":
                parking = ParkingConfig(mode="min_open")
                st.caption("12.5% открыто (норматив), 87.5% — подземные.")
            elif park_mode == "all_open":
                parking = ParkingConfig(mode="all_open")
                st.caption("Все м/м на поверхности — максимальная нагрузка на квартал.")
            else:
                st.caption("Сумма открытых и многоуровневых ≤ 100%. Подземные — остаток.")
                c1, c2, c3 = st.columns(3)
                open_pct = c1.slider(
                    "Открытые, %", 0, 100, 30, key="park_open_pct",
                )
                ml_pct = c2.slider(
                    "Многоуровневые, %", 0, 100, 30, key="park_ml_pct",
                )
                ug_pct = max(0, 100 - open_pct - ml_pct)
                c3.metric("Подземные, %", ug_pct)
                ml_levels = st.number_input(
                    "Этажность многоуровневого паркинга",
                    min_value=1,
                    max_value=10,
                    value=3,
                    key="park_ml_levels",
                )
                try:
                    parking = ParkingConfig(
                        mode="custom",
                        open_share=open_pct / 100,
                        multilevel_share=ml_pct / 100,
                        underground_share=ug_pct / 100,
                        multilevel_levels=ml_levels,
                    )
                except Exception as e:
                    st.error(f"Некорректная конфигурация парковок: {e}")
                    parking = ParkingConfig(mode="min_open")

        # --- ВПП (встроенно-пристроенные помещения) ---
        with st.container(border=True):
            st.markdown("##### ВПП — встроенно-пристроенные помещения")
            include_vpp = st.checkbox(
                "Учитывать ВПП",
                value=False,
                key="include_vpp",
                help="ВПП занимает часть GFA, требует своих парковок и озеленения по ВРИ.",
            )
            vpp_auto = False
            built_in = None
            if include_vpp:
                vpp_vri = st.selectbox(
                    "ВРИ-код ВПП",
                    [
                        "4.4 — магазины",
                        "4.6 — общепит",
                        "3.3 — бытовые услуги",
                        "3.6 — культура",
                        "3.7 — религия",
                    ],
                    index=0,
                    key="vpp_vri",
                )
                vri_code = vpp_vri.split(" ")[0]
                vpp_size_mode = st.radio(
                    "Площадь ВПП",
                    [
                        "Площадь застройки 1 этажа (рассчитать)",
                        "Задать вручную, м²",
                    ],
                    key="vpp_size_mode",
                )
                if vpp_size_mode.startswith("Площадь застройки"):
                    vpp_auto = True
                    built_in = BuiltInArea(area_m2=1.0, vri_code=vri_code, label="1 этаж")
                else:
                    vpp_area = st.number_input(
                        "Площадь ВПП, м²",
                        min_value=10.0,
                        max_value=100_000.0,
                        value=2_000.0,
                        step=100.0,
                        key="vpp_area",
                    )
                    built_in = BuiltInArea(area_m2=float(vpp_area), vri_code=vri_code)

    # ==================================================================
    # Произвольные объекты — индикатор на вкладку «Объекты»
    # ==================================================================
    if "custom_objects" not in st.session_state:
        st.session_state.custom_objects = []
    n_objects = len(st.session_state.custom_objects)
    if n_objects > 0:
        st.info(
            f"Применено объектов: **{n_objects}** "
            f"(управление — вкладка «Объекты»)."
        )

    custom_objects_list = [
        CustomObject(**obj) for obj in st.session_state.custom_objects
    ]

    # ==================================================================
    # Сборка спецификаций и опций
    # ==================================================================
    kg_spec = KindergartenSpec(
        building_type=kg_btype,
        num_objects=int(kg_num_objects) if kg_num_objects else None,
        capacity_per_object=int(kg_capacity) if kg_capacity else None,
    )
    school_spec = SchoolSpec(
        has_pool=school_pool,
        has_sport_core=school_sport,
        num_objects=int(sch_num_objects) if sch_num_objects else None,
        capacity_per_object=int(sch_capacity) if sch_capacity else None,
    )

    options = CalculationOptions(
        floors=int(floors),
        planning_doc=planning_doc,
        include_kindergarten=include_kg,
        include_school=include_school,
        kindergarten=kg_spec,
        school=school_spec,
        parking=parking,
        built_in=built_in,
        znop_per_person_override=znop_override,
        custom_objects=custom_objects_list,
        driveways_intra_share_override=intra_override,
        driveways_lot_share_override=lot_override,
    )

    return UserInputs(
        site=site,
        options=options,
        mode=mode,
        target_surplus_m2=target_surplus_m2,
        verify_kit_value=verify_kit_value,
        vpp_auto_one_floor=vpp_auto,
    )


# ---------------------------------------------------------------------------
# Совместимость со старым кодом
# ---------------------------------------------------------------------------

def render_sidebar() -> UserInputs:
    """Алиас для обратной совместимости.

    В v0.5.7 это вызывало render_sidebar (форма в sidebar). С v0.5.8
    параметры на отдельной вкладке; для совместимости со старыми
    интеграциями оставляем функцию-обёртку.
    """
    return render_params_tab()
