"""Вкладка «Параметры»: полноэкранная форма ввода → UserInputs.

v0.6.0 — все параметры в одной вкладке (включая объекты, парковки,
ЗНОП с двумя способами задания).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
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


# Допустимые ВРИ для произвольных объектов
_VRI_OPTIONS = ["3.4", "3.5", "3.6", "3.7", "4.0", "4.1", "4.4", "4.5", "4.6", "5.1"]


def render_params_tab() -> UserInputs:
    """Полноэкранная форма параметров на отдельной вкладке."""
    st.markdown("### Параметры расчёта")
    st.caption(
        "Настройте квартал и нормативные ограничения. Все изменения "
        "применяются автоматически — перейдите на вкладку «Расчёт», "
        "чтобы увидеть результат."
    )
    st.markdown("---")

    # ==================================================================
    # Верхняя строка: режим расчёта
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
            target_surplus_m2 = float(st.number_input(
                "Целевой резерв, м²",
                min_value=0.0, max_value=500_000.0,
                value=5_000.0, step=500.0,
                key="target_surplus_m2",
            ))
        else:
            mode = "verify"
            verify_kit_value = float(st.number_input(
                "Внутренняя плотность (block_density) для проверки",
                min_value=0.1, max_value=5.0,
                value=1.5, step=0.05,
                key="verify_kit",
                help=(
                    "Это плотность GFA/S_квартала. Фактический КИТ "
                    "(площадь квартир / ЗУ жилой) рассчитается."
                ),
            ))

    st.markdown("---")

    col_left, col_right = st.columns(2)

    # ─── ЛЕВАЯ КОЛОНКА ──────────────────────────────────────────────
    with col_left:
        site = _render_quarter()
        floors, planning_doc = _render_housing()
        znop_pp_override, znop_total_override = _render_znop()
        intra_override, lot_override = _render_driveways()

    # ─── ПРАВАЯ КОЛОНКА ─────────────────────────────────────────────
    with col_right:
        kg_spec, include_kg = _render_kg()
        school_spec, include_school = _render_school()
        parking = _render_parking()
        built_in, vpp_auto = _render_vpp()

    # ==================================================================
    # Произвольные объекты — full-width внизу
    # ==================================================================
    st.markdown("---")
    custom_objects_list = _render_custom_objects()

    # ==================================================================
    # Сборка опций
    # ==================================================================
    options = CalculationOptions(
        floors=int(floors),
        planning_doc=planning_doc,
        include_kindergarten=include_kg,
        include_school=include_school,
        kindergarten=kg_spec,
        school=school_spec,
        parking=parking,
        built_in=built_in,
        znop_per_person_override=znop_pp_override,
        znop_total_area_override=znop_total_override,
        custom_objects=custom_objects_list,
        driveways_intra_share_override=intra_override,
        driveways_lot_share_override=lot_override,
    )
    return UserInputs(
        site=site, options=options, mode=mode,
        target_surplus_m2=target_surplus_m2,
        verify_kit_value=verify_kit_value,
        vpp_auto_one_floor=vpp_auto,
    )


# ===========================================================================
# Секции — выделены в отдельные функции для читаемости
# ===========================================================================

def _render_quarter() -> Site:
    with st.container(border=True):
        st.markdown("##### Квартал")
        unit = st.radio(
            "Единицы площади", ["м²", "га"],
            horizontal=True, key="area_unit",
            label_visibility="collapsed",
        )
        if unit == "га":
            area_ga = st.number_input(
                "Площадь квартала, га",
                min_value=0.1, max_value=200.0,
                value=5.0, step=0.1,
                key="area_input_ga",
            )
            area_m2 = area_ga * 10_000
        else:
            area_m2 = st.number_input(
                "Площадь квартала, м²",
                min_value=1_000.0, max_value=2_000_000.0,
                value=50_000.0, step=1_000.0,
                key="area_input_m2",
            )
    return Site(area_m2=area_m2)


def _render_housing() -> tuple[int, bool]:
    with st.container(border=True):
        st.markdown("##### Жильё")
        c1, c2 = st.columns(2)
        floors = c1.number_input(
            "Этажность",
            min_value=1, max_value=40, value=12, step=1,
            key="floors",
        )
        with c2:
            st.write("")
            planning_doc = st.checkbox(
                "ДПТ (документация по планировке)",
                value=True, key="planning_doc",
                help="Без ДПТ нормативный потолок КИТ = 1.4; с ДПТ = 2.5.",
            )
    return int(floors), planning_doc


def _render_znop() -> tuple[float | None, float | None]:
    """Возвращает (znop_per_person_override, znop_total_area_override).

    Только одно из значений может быть задано одновременно (или None для
    нормативного режима).
    """
    with st.container(border=True):
        st.markdown("##### ЗНОП — зелёные насаждения общего пользования")
        znop_include = st.checkbox(
            "Учитывать ЗНОП", value=True, key="znop_include",
        )
        if not znop_include:
            st.caption("ЗНОП принудительно = 0 (не для нормативного режима).")
            return 0.0, None

        znop_mode = st.radio(
            "Источник значения",
            [
                "По нормативу (зависит от КИТ ступенями: 0/3/4/6 м²/чел)",
                "Задать вручную: м²/чел",
                "Задать вручную: общая площадь",
            ],
            key="znop_mode",
        )
        if znop_mode.startswith("По нормативу"):
            return None, None
        if znop_mode.startswith("Задать вручную: м²/чел"):
            znop_pp = st.number_input(
                "ЗНОП, м²/чел",
                min_value=0.0, max_value=20.0,
                value=6.0, step=0.5,
                key="znop_value_pp",
            )
            return float(znop_pp), None
        # «Задать вручную: общая площадь»
        c1, c2 = st.columns(2)
        znop_unit = c1.radio(
            "Единица", ["м²", "га"],
            horizontal=True, key="znop_total_unit",
            label_visibility="collapsed",
        )
        if znop_unit == "га":
            znop_ga = c2.number_input(
                "Общая площадь ЗНОП, га",
                min_value=0.0, max_value=50.0,
                value=0.5, step=0.05,
                key="znop_total_ga",
            )
            znop_total = znop_ga * 10_000
        else:
            znop_total = c2.number_input(
                "Общая площадь ЗНОП, м²",
                min_value=0.0, max_value=500_000.0,
                value=5_000.0, step=100.0,
                key="znop_total_m2",
            )
        return None, float(znop_total)


def _render_driveways() -> tuple[float | None, float | None]:
    with st.container(border=True):
        st.markdown("##### Проезды")
        st.caption(
            "Значения по умолчанию приняты условно (точных нормативов "
            "СПб для этих долей нет)."
        )
        c1, c2 = st.columns(2)
        intra_override = None
        with c1:
            intra_use_override = st.checkbox(
                "Внутриквартальные — задать",
                value=False, key="drive_intra_override",
            )
            if intra_use_override:
                intra_pct = st.slider(
                    "Доля от S_квартала, %",
                    min_value=0, max_value=30, value=10, step=1,
                    key="drive_intra_pct",
                )
                intra_override = intra_pct / 100
            else:
                st.caption("По умолчанию: 10%")
        lot_override = None
        with c2:
            lot_use_override = st.checkbox(
                "На ЗУ жилой — задать",
                value=False, key="drive_lot_override",
            )
            if lot_use_override:
                lot_pct = st.slider(
                    "Доля от S_застройки, %",
                    min_value=0, max_value=300, value=120, step=5,
                    key="drive_lot_pct",
                )
                lot_override = lot_pct / 100
            else:
                st.caption("По умолчанию: 120%")
    return intra_override, lot_override


def _render_kg() -> tuple[KindergartenSpec, bool]:
    with st.container(border=True):
        st.markdown("##### Дошкольные образовательные организации (ДОО)")
        include_kg = st.checkbox(
            "Учитывать ДОО", value=True, key="include_kg",
        )
        kg_btype_label = st.selectbox(
            "Тип здания ДОО",
            ["Отдельно стоящее", "Встроенно-пристроенное"],
            index=0,
            disabled=not include_kg,
            help=(
                "Отдельно стоящее: 160–350 мест (РМД). "
                "Встроенно-пристроенное: до 120 мест на 1-м этаже жилого дома."
            ),
            key="kg_btype_label",
        )
        kg_btype = "detached" if kg_btype_label == "Отдельно стоящее" else "built_in"
        kg_override = st.checkbox(
            "Задать число объектов вручную",
            value=False, disabled=not include_kg, key="kg_override",
        )
        kg_num_objects, kg_capacity = None, None
        if include_kg and kg_override:
            c1, c2 = st.columns(2)
            kg_num_objects = c1.number_input(
                "Кол-во ДОО", min_value=1, max_value=20, value=2, step=1,
                key="kg_num_objects",
            )
            kg_capacity = c2.number_input(
                "Мест в каждом",
                min_value=40, max_value=400, value=160, step=10,
                key="kg_capacity",
            )
    spec = KindergartenSpec(
        building_type=kg_btype,
        num_objects=int(kg_num_objects) if kg_num_objects else None,
        capacity_per_object=int(kg_capacity) if kg_capacity else None,
    )
    return spec, include_kg


def _render_school() -> tuple[SchoolSpec, bool]:
    with st.container(border=True):
        st.markdown("##### Средние общеобразовательные школы (СОШ)")
        include_school = st.checkbox(
            "Учитывать СОШ", value=True, key="include_school",
        )
        c1, c2 = st.columns(2)
        with c1:
            school_pool = st.checkbox(
                "С бассейном (+0.2 га)",
                value=True,  # v0.6.0: по умолчанию вкл (типовая СОШ СПб)
                disabled=not include_school, key="school_pool",
            )
        with c2:
            school_sport = st.checkbox(
                "Со спортивным ядром (+0.7 га)",
                value=True,  # v0.6.0: по умолчанию вкл
                disabled=not include_school, key="school_sport",
            )
        sch_override = st.checkbox(
            "Задать число СОШ вручную",
            value=False, disabled=not include_school, key="sch_override",
        )
        sch_num_objects, sch_capacity = None, None
        if include_school and sch_override:
            c1, c2 = st.columns(2)
            sch_num_objects = c1.number_input(
                "Кол-во СОШ", min_value=1, max_value=10, value=1, step=1,
                key="sch_num_objects",
            )
            sch_capacity = c2.number_input(
                "Мест в каждой",
                min_value=200, max_value=2000, value=550, step=10,
                key="sch_capacity",
            )
    spec = SchoolSpec(
        has_pool=school_pool, has_sport_core=school_sport,
        num_objects=int(sch_num_objects) if sch_num_objects else None,
        capacity_per_object=int(sch_capacity) if sch_capacity else None,
    )
    return spec, include_school


def _render_parking() -> ParkingConfig:
    """Парковки — три режима + расширенный custom с типами и count×capacity."""
    with st.container(border=True):
        st.markdown("##### Парковки")
        PARK_MODE_LABELS = {
            "Минимум открытых, остальное подземные (по умолчанию)": "min_open",
            "Все парковки открытые наземные": "all_open",
            "Задать вручную": "custom",
        }
        park_label = st.radio(
            "Размещение машино-мест",
            list(PARK_MODE_LABELS.keys()),
            index=0,
            key="park_mode_label",
        )
        park_mode = PARK_MODE_LABELS[park_label]
        if park_mode == "min_open":
            st.caption("12.5% открыто (норматив СПб), 87.5% — подземные.")
            return ParkingConfig(mode="min_open")
        if park_mode == "all_open":
            st.caption("100% м/м на поверхности — максимальная нагрузка на квартал.")
            return ParkingConfig(mode="all_open")
        return _render_parking_custom()


NORM_MIN_OPEN = 12.5  # % — норматив СПб (parking.open_share_min)


# ---------------------------------------------------------------------------
# Callbacks для зависимых слайдеров парковок
# ---------------------------------------------------------------------------
# Streamlit's on_change-механизм: коллбэк может менять session_state ДРУГИХ
# виджетов (не своего собственного key). Используем это для перераспределения
# остатка между не-двинутыми долями, сохраняя их относительное соотношение.

def _redistribute(moved_key: str, other_keys: list[str]) -> None:
    """Сохранить сумму = 100%. moved_key только что изменился; распределить
    оставшиеся 100 − moved_value между other_keys пропорционально их текущим
    значениям. Если все остальные = 0, делим поровну.
    """
    moved_value = float(st.session_state.get(moved_key, 0.0))
    target = max(0.0, 100.0 - moved_value)
    olds = [float(st.session_state.get(k, 0.0)) for k in other_keys]
    s = sum(olds)
    if s > 0:
        new_vals = [target * v / s for v in olds]
    else:
        n = max(len(other_keys), 1)
        new_vals = [target / n] * len(other_keys)
    # Округление до 0.5% — соответствует step ползунков
    new_vals = [round(v * 2) / 2 for v in new_vals]
    # Поправка остатка из-за округления — добавляем разницу в первый
    diff = target - sum(new_vals)
    if new_vals:
        new_vals[0] = round((new_vals[0] + diff) * 2) / 2
        new_vals[0] = max(0.0, min(100.0, new_vals[0]))
    for k, v in zip(other_keys, new_vals):
        st.session_state[k] = max(0.0, min(100.0, v))


def _on_open_change_3():
    _redistribute("park_open_pct", ["park_ml_pct", "park_ug_pct"])


def _on_ml_change_3():
    _redistribute("park_ml_pct", ["park_open_pct", "park_ug_pct"])


def _on_ug_change_3():
    _redistribute("park_ug_pct", ["park_open_pct", "park_ml_pct"])


def _on_open_change_open_ug():
    _redistribute("park_open_pct", ["park_ug_pct"])


def _on_ug_change_open_ug():
    _redistribute("park_ug_pct", ["park_open_pct"])


def _on_open_change_open_ml():
    _redistribute("park_open_pct", ["park_ml_pct"])


def _on_ml_change_open_ml():
    _redistribute("park_ml_pct", ["park_open_pct"])


def _on_ml_change_ml_ug():
    _redistribute("park_ml_pct", ["park_ug_pct"])


def _on_ug_change_ml_ug():
    _redistribute("park_ug_pct", ["park_ml_pct"])


def _init_parking_state() -> None:
    """Инициализация дефолтных значений долей при первом рендере."""
    defaults = {
        "park_open_pct": 12.5,
        "park_ml_pct": 0.0,
        "park_ug_pct": 87.5,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def _rebalance_on_toggle(use_open: bool, use_ml: bool, use_ug: bool) -> None:
    """Когда пользователь включает/выключает тип, сумма может сбиться.
    Перенормируем доли так, чтобы сумма по активным = 100%, а выключенные = 0.
    """
    active = []
    if use_open: active.append("park_open_pct")
    if use_ml:   active.append("park_ml_pct")
    if use_ug:   active.append("park_ug_pct")

    # Обнуляем неактивные
    all_keys = ["park_open_pct", "park_ml_pct", "park_ug_pct"]
    for k in all_keys:
        if k not in active:
            st.session_state[k] = 0.0

    if not active:
        return
    cur_sum = sum(st.session_state[k] for k in active)
    if abs(cur_sum - 100.0) < 0.1:
        return  # уже нормализовано

    if cur_sum == 0:
        # Распределяем поровну, но первый — не меньше 12.5%
        share = 100.0 / len(active)
        for k in active:
            st.session_state[k] = round(share * 2) / 2
    else:
        # Нормируем пропорционально
        for k in active:
            st.session_state[k] = round(st.session_state[k] * 100.0 / cur_sum * 2) / 2
        # Поправка разницы из-за округления
        s = sum(st.session_state[k] for k in active)
        st.session_state[active[0]] = round((st.session_state[active[0]] + (100.0 - s)) * 2) / 2


def _render_parking_custom() -> ParkingConfig:
    """Custom-режим парковок (v0.6.1):
       - чекбоксы на каждый тип;
       - **зависимые слайдеры**: меняешь один — остальные авто-подстраиваются
         с сохранением их относительного соотношения; сумма всегда 100%;
       - открытые подсвечиваются красным при <12.5%;
       - многоуровневые: альтернативный режим «кол-во × вместимость».
    """
    _init_parking_state()

    st.caption(
        "Выберите типы парковок. Слайдеры зависимы: меняешь один — остальные "
        "автоматически перераспределятся. Сумма всегда 100%."
    )

    c1, c2, c3 = st.columns(3)
    with c1:
        use_open = st.checkbox("Открытые наземные", value=True, key="park_use_open")
    with c2:
        use_ml = st.checkbox("Многоуровневые наземные", value=False, key="park_use_ml")
    with c3:
        use_ug = st.checkbox("Подземные", value=True, key="park_use_ug")

    if not (use_open or use_ml or use_ug):
        st.error("Выберите хотя бы один тип парковок.")
        return ParkingConfig(mode="min_open")

    # Выбираем колбэки в зависимости от активных типов
    if use_open and use_ml and use_ug:
        cb_open, cb_ml, cb_ug = _on_open_change_3, _on_ml_change_3, _on_ug_change_3
    elif use_open and use_ug and not use_ml:
        cb_open, cb_ml, cb_ug = _on_open_change_open_ug, None, _on_ug_change_open_ug
    elif use_open and use_ml and not use_ug:
        cb_open, cb_ml, cb_ug = _on_open_change_open_ml, _on_ml_change_open_ml, None
    elif use_ml and use_ug and not use_open:
        cb_open, cb_ml, cb_ug = None, _on_ml_change_ml_ug, _on_ug_change_ml_ug
    else:
        cb_open = cb_ml = cb_ug = None

    # Если включён режим «кол-во × вместимость» для многоуровневых — ml-слайдера нет
    # (multilevel задаётся абсолютным числом, share=0). Перенормируем при необходимости.
    ml_use_explicit = False
    if use_ml:
        ml_mode_label = st.session_state.get("park_ml_mode", "Доля от общей потребности, %")
        ml_use_explicit = ml_mode_label.startswith("Количество")

    # Перенормируем при изменении флагов или при переключении ml в explicit-режим
    # (если ml уйдёт из общих долей, нужно перераспределить его % на open и ug)
    effective_use_ml_share = use_ml and not ml_use_explicit
    _rebalance_on_toggle(use_open, effective_use_ml_share, use_ug)

    ml_explicit_places: int | None = None
    ml_levels = 3

    # === Открытые ===
    if use_open:
        with st.container(border=True):
            st.markdown("**Открытые наземные**")
            st.slider(
                "Доля, %",
                min_value=0.0, max_value=100.0,
                step=0.5,
                key="park_open_pct",
                on_change=cb_open,
            )
            if st.session_state.park_open_pct < NORM_MIN_OPEN:
                st.markdown(
                    f"<div style='color:#A4262C;font-size:0.85em;'>"
                    f"⚠ Ниже норматива СПб ({NORM_MIN_OPEN}%) — расчёт "
                    f"принудительно поднимет долю до {NORM_MIN_OPEN}%."
                    f"</div>",
                    unsafe_allow_html=True,
                )

    # === Многоуровневые ===
    if use_ml:
        with st.container(border=True):
            st.markdown("**Многоуровневые наземные**")
            ml_mode = st.radio(
                "Способ задания",
                ["Доля от общей потребности, %", "Количество паркингов × вместимость"],
                key="park_ml_mode",
            )
            if ml_mode.startswith("Доля"):
                st.slider(
                    "Доля, %",
                    min_value=0.0, max_value=100.0,
                    step=0.5,
                    key="park_ml_pct",
                    on_change=cb_ml,
                )
            else:
                cc1, cc2 = st.columns(2)
                ml_n = cc1.number_input(
                    "Кол-во паркингов",
                    min_value=1, max_value=10, value=1, step=1,
                    key="park_ml_n",
                )
                ml_cap = cc2.number_input(
                    "Вместимость каждого, м/м",
                    min_value=50, max_value=600, value=300, step=10,
                    key="park_ml_cap",
                    help="По нормативу СПб — макс. 300 м/м в одном паркинге.",
                )
                ml_explicit_places = int(ml_n) * int(ml_cap)
                st.caption(f"Итого многоуровневых: **{ml_explicit_places} м/м**.")
            ml_levels = st.number_input(
                "Этажность многоуровневого паркинга",
                min_value=1, max_value=10, value=3, step=1,
                key="park_ml_levels",
                help="Чем выше — тем компактнее пятно, но дороже строительство.",
            )

    # === Подземные ===
    if use_ug:
        with st.container(border=True):
            st.markdown("**Подземные**")
            st.slider(
                "Доля, %",
                min_value=0.0, max_value=100.0,
                step=0.5,
                key="park_ug_pct",
                on_change=cb_ug,
            )

    # === Текущие значения из state ===
    open_pct = st.session_state.park_open_pct if use_open else 0.0
    ml_pct = st.session_state.park_ml_pct if (use_ml and not ml_use_explicit) else 0.0
    ug_pct = st.session_state.park_ug_pct if use_ug else 0.0

    # === Сборка ParkingConfig ===
    if ml_use_explicit:
        # multilevel — абсолютным числом. Open и Ug делят остаток.
        sum_ou = max(open_pct + ug_pct, 0.01)
        open_share = open_pct / sum_ou
        ug_share = ug_pct / sum_ou
        ml_share = 0.0
    else:
        total = max(open_pct + ml_pct + ug_pct, 0.01)
        open_share = open_pct / total
        ml_share = ml_pct / total
        ug_share = ug_pct / total

    try:
        return ParkingConfig(
            mode="custom",
            open_share=open_share,
            multilevel_share=ml_share,
            underground_share=ug_share,
            multilevel_levels=int(ml_levels),
            multilevel_explicit_places=ml_explicit_places,
        )
    except Exception as e:
        st.error(f"Некорректная конфигурация парковок: {e}")
        return ParkingConfig(mode="min_open")


def _render_vpp() -> tuple[BuiltInArea | None, bool]:
    with st.container(border=True):
        st.markdown("##### ВПП — встроенно-пристроенные помещения")
        include_vpp = st.checkbox(
            "Учитывать ВПП", value=False, key="include_vpp",
            help="ВПП занимает часть GFA, требует своих парковок и озеленения по ВРИ.",
        )
        if not include_vpp:
            return None, False
        vpp_vri = st.selectbox(
            "ВРИ-код ВПП",
            [
                "4.4 — магазины",
                "4.6 — общепит",
                "3.3 — бытовые услуги",
                "3.6 — культура",
                "3.7 — религия",
            ],
            index=0, key="vpp_vri",
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
            return BuiltInArea(area_m2=1.0, vri_code=vri_code, label="1 этаж"), True
        vpp_area = st.number_input(
            "Площадь ВПП, м²",
            min_value=10.0, max_value=100_000.0,
            value=2_000.0, step=100.0, key="vpp_area",
        )
        return BuiltInArea(area_m2=float(vpp_area), vri_code=vri_code), False


def _render_custom_objects() -> list[CustomObject]:
    """Табличный редактор для произвольных объектов (бывшая вкладка)."""
    with st.container(border=True):
        st.markdown("##### Произвольные объекты на территории квартала")
        st.caption(
            "Объекты вне базовых классов (офис, ФОК, поликлиника, "
            "торговля). Каждый занимает свой ЗУ и считается по ВРИ-коду."
        )

        if "custom_objects" not in st.session_state:
            st.session_state.custom_objects = []

        # Готовим DataFrame для редактора
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
        if not rows:
            rows = [{
                "Название": "Офис",
                "Площадь ЗУ, м²": 2_000.0,
                "ВРИ-код": "4.1",
                "Общая площадь, м²": 2_000.0,
            }]
            # Дефолтная строка — это шаблон, не сохраняем сразу
            default_template = True
        else:
            default_template = False

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
                    min_value=10.0, max_value=200_000.0,
                    step=100.0, format="%.0f", required=True,
                ),
                "ВРИ-код": st.column_config.SelectboxColumn(
                    "ВРИ-код",
                    options=_VRI_OPTIONS,
                    required=True,
                    help="Определяет нормативы парковок и озеленения объекта",
                ),
                "Общая площадь, м²": st.column_config.NumberColumn(
                    "Общая площадь, м²",
                    min_value=10.0, max_value=500_000.0,
                    step=100.0, format="%.0f",
                    help="Сумма площадей этажных перекрытий объекта",
                ),
            },
            key="objects_editor_inline",
        )

        col1, col2, col3 = st.columns([1, 1, 3])
        with col1:
            if st.button("Применить", type="primary", use_container_width=True):
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
                        continue
                st.session_state.custom_objects = new_list
                st.toast(f"Применено: {len(new_list)} объект(а/ов)", icon="📦")
                st.rerun()
        with col2:
            if st.button("Очистить", use_container_width=True):
                st.session_state.custom_objects = []
                st.rerun()
        with col3:
            if default_template and st.session_state.custom_objects == []:
                st.caption(
                    "👆 Это шаблон-пример. Отредактируйте строку и нажмите "
                    "«Применить», или добавьте новые строки."
                )

    return [CustomObject(**obj) for obj in st.session_state.custom_objects]


# ---------------------------------------------------------------------------
# Совместимость со старым кодом
# ---------------------------------------------------------------------------

def render_sidebar() -> UserInputs:
    """Алиас для обратной совместимости."""
    return render_params_tab()
