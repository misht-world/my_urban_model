"""Sidebar-форма: ввод параметров → (Site, CalculationOptions, mode_kwargs)."""

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


def render_sidebar() -> UserInputs:
    """Рисует форму в sidebar и возвращает собранные параметры."""
    st.sidebar.title("⚙️ Параметры")

    # ------------------------------------------------------------------
    # 1. Квартал
    # ------------------------------------------------------------------
    with st.sidebar.expander("📐 Квартал", expanded=True):
        unit = st.radio(
            "Единицы площади",
            ["м²", "га"],
            horizontal=True,
            key="area_unit",
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

    # ------------------------------------------------------------------
    # 2. Жильё
    # ------------------------------------------------------------------
    with st.sidebar.expander("🏠 Жильё", expanded=True):
        floors = st.number_input(
            "Этажность",
            min_value=1,
            max_value=40,
            value=12,
            step=1,
            key="floors",
        )
        planning_doc = st.checkbox(
            "Документация по планировке территории (ДПТ) → КИТ_max=2.5",
            value=True,
            key="planning_doc",
            help="Без ДПТ нормативный потолок КИТ = 1.4",
        )

    # ------------------------------------------------------------------
    # 3. ЗНОП (зелёные насаждения общего пользования)
    # ------------------------------------------------------------------
    with st.sidebar.expander("🌳 ЗНОП — зелёные насаждения общего пользования", expanded=False):
        znop_include = st.checkbox(
            "Учитывать ЗНОП",
            value=True,
            key="znop_include",
            help=(
                "Если выключено — площадь ЗНОП принудительно = 0 "
                "(не для соответствия нормативу!). Для нормативного "
                "режима оставьте включённым."
            ),
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

    # ------------------------------------------------------------------
    # 4. Соцобъекты
    # ------------------------------------------------------------------
    with st.sidebar.expander("🎒 Соцобъекты", expanded=False):
        include_kg = st.checkbox(
            "Учитывать ДОО (детские сады)",
            value=True,
            key="include_kg",
        )
        kg_btype_label = st.selectbox(
            "Тип здания ДОО",
            ["Отдельно стоящее", "Встроенно-пристроенное"],
            index=0,
            disabled=not include_kg,
            help=(
                "Отдельно стоящее: вместимость 160-350 мест по РМД. "
                "Встроенно-пристроенное: до 120 мест, размещается на 1-м этаже жилого дома."
            ),
            key="kg_btype_label",
        )
        kg_btype = "detached" if kg_btype_label == "Отдельно стоящее" else "built_in"
        kg_override = st.checkbox(
            "Задать число ДОО вручную",
            value=False,
            disabled=not include_kg,
            key="kg_override",
            help=(
                "Если выключено — число объектов и вместимость подбираются "
                "автоматически по нормативу. Включите для оптимизации или "
                "при заданном проекте."
            ),
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
                help="Если оставить по умолчанию — будет скорректировано под нагрузку",
            )

        st.markdown("---")
        include_school = st.checkbox(
            "Учитывать СОШ (школы)",
            value=True,
            key="include_school",
        )
        school_pool = st.checkbox(
            "СОШ с бассейном (+0.2 га)",
            value=False,
            disabled=not include_school,
            key="school_pool",
        )
        school_sport = st.checkbox(
            "СОШ со спортивным ядром (+0.7 га)",
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

    # ------------------------------------------------------------------
    # 5. Парковки
    # ------------------------------------------------------------------
    PARK_MODE_LABELS = {
        "Минимум открытых, остальное подземные (по умолчанию)": "min_open",
        "Все парковки открытые наземные": "all_open",
        "Задать доли вручную": "custom",
    }
    with st.sidebar.expander("🅿️ Парковки", expanded=False):
        park_label = st.radio(
            "Размещение машино-мест",
            list(PARK_MODE_LABELS.keys()),
            index=0,
            key="park_mode_label",
            help=(
                "Минимум открытых: 12.5% обязательно открытыми, "
                "остаток размещается под землёй (не отнимает поверхностной "
                "площади квартала). Все открытые: 100% м/м на поверхности — "
                "максимальная нагрузка на квартал. Вручную: задать доли каждого типа."
            ),
        )
        park_mode = PARK_MODE_LABELS[park_label]
        if park_mode == "min_open":
            parking = ParkingConfig(mode="min_open")
        elif park_mode == "all_open":
            parking = ParkingConfig(mode="all_open")
        else:
            st.caption("Сумма долей должна равняться 100%. Подземные считаются автоматически.")
            open_pct = st.slider(
                "Открытые наземные, %", 0, 100, 30, key="park_open_pct",
                help="Парковки на поверхности — отнимают площадь квартала.",
            )
            ml_pct = st.slider(
                "Многоуровневые наземные, %", 0, 100, 30, key="park_ml_pct",
                help="Многоуровневые наземные паркинги — занимают пятно, но компактнее открытых.",
            )
            ug_pct = max(0, 100 - open_pct - ml_pct)
            st.metric("Подземные (автоматически), %", ug_pct,
                      help="Подземные паркинги не занимают поверхностной площади квартала.")
            ml_levels = st.number_input(
                "Этажность многоуровневого паркинга",
                min_value=1,
                max_value=10,
                value=3,
                key="park_ml_levels",
                help="При 3 этажах площадь пятна в 3 раза меньше, чем у открытой стоянки.",
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

    # ------------------------------------------------------------------
    # 6. ВПП (встроенно-пристроенные помещения)
    # ------------------------------------------------------------------
    with st.sidebar.expander("🏪 ВПП (встроенно-пристроенные)", expanded=False):
        include_vpp = st.checkbox(
            "Учитывать ВПП",
            value=False,
            key="include_vpp",
            help="ВПП занимает часть GFA, требует своих парковок и озеленения по ВРИ",
        )
        vpp_auto = False
        built_in = None
        if include_vpp:
            vpp_vri = st.selectbox(
                "ВРИ-код ВПП",
                ["4.4 — магазины", "4.6 — общепит", "3.3 — бытовые услуги", "3.6 — культура", "3.7 — религия"],
                index=0,
                key="vpp_vri",
            )
            vri_code = vpp_vri.split(" ")[0]

            vpp_size_mode = st.radio(
                "Площадь ВПП",
                [
                    "= площадь застройки 1 этажа (auto)",
                    "Задать вручную, м²",
                ],
                key="vpp_size_mode",
            )
            if vpp_size_mode.startswith("="):
                vpp_auto = True
                # Заглушка с area=1.0 — реальное значение посчитается во 2-м проходе
                built_in = BuiltInArea(area_m2=1.0, vri_code=vri_code, label="1 этаж")
                st.caption("ℹ️ Площадь = footprint 1 этажа (рассчитывается двухпроходно)")
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

    # ------------------------------------------------------------------
    # 7. Проезды — оверрайд нормативных долей
    # ------------------------------------------------------------------
    with st.sidebar.expander("🛣️ Проезды", expanded=False):
        st.caption(
            "Значения по умолчанию приняты условно: 10% от площади квартала "
            "на внутриквартальные проезды; 120% от площади застройки — "
            "проезды на ЗУ жилой застройки. Точных нормативов СПб для этих "
            "долей нет — переопределяйте под конкретный проект."
        )
        intra_use_override = st.checkbox(
            "Переопределить внутриквартальные проезды",
            value=False,
            key="drive_intra_override",
        )
        intra_override = None
        if intra_use_override:
            intra_pct = st.slider(
                "Доля внутриквартальных проездов от S_квартала, %",
                min_value=0, max_value=30,
                value=10, step=1,
                key="drive_intra_pct",
            )
            intra_override = intra_pct / 100

        lot_use_override = st.checkbox(
            "Переопределить проезды на ЗУ жилой застройки",
            value=False,
            key="drive_lot_override",
        )
        lot_override = None
        if lot_use_override:
            lot_pct = st.slider(
                "Доля проездов на ЗУ от S_застройки, %",
                min_value=0, max_value=300,
                value=120, step=5,
                key="drive_lot_pct",
            )
            lot_override = lot_pct / 100

    # ------------------------------------------------------------------
    # 8. Произвольные объекты (управляются на вкладке «Объекты»)
    # ------------------------------------------------------------------
    if "custom_objects" not in st.session_state:
        st.session_state.custom_objects = []

    n_objects = len(st.session_state.custom_objects)
    if n_objects > 0:
        st.sidebar.info(
            f"📦 Применено объектов: **{n_objects}** "
            f"(управление — на вкладке «Объекты»)"
        )
    else:
        st.sidebar.caption(
            "📦 Свои объекты не заданы. Чтобы добавить офис/ФОК/поликлинику — "
            "перейдите на вкладку «Объекты» сверху."
        )

    # Преобразуем session_state в список Pydantic-объектов
    custom_objects_list = [
        CustomObject(**obj) for obj in st.session_state.custom_objects
    ]

    # ------------------------------------------------------------------
    # 9. Режим расчёта
    # ------------------------------------------------------------------
    st.sidebar.markdown("---")
    mode_label = st.sidebar.radio(
        "🔧 Режим расчёта",
        [
            "Максимальный КИТ",
            "КИТ с резервом территории",
            "Проверка конкретного КИТ",
        ],
        index=0,
        key="calc_mode",
    )
    target_surplus_m2 = 0.0
    verify_kit_value = 1.5
    if mode_label == "Максимальный КИТ":
        mode = "max_kit"
    elif mode_label == "КИТ с резервом территории":
        mode = "with_reserve"
        target_surplus_m2 = float(
            st.sidebar.number_input(
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
            st.sidebar.number_input(
                "КИТ для проверки",
                min_value=0.1,
                max_value=3.0,
                value=1.5,
                step=0.05,
                key="verify_kit",
            )
        )

    # ------------------------------------------------------------------
    # Сборка CalculationOptions
    # ------------------------------------------------------------------
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
