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
        floors, planning_doc, built_in, vpp_auto = _render_housing_and_vpp()
        znop_pp_override, znop_total_override = _render_znop()
        intra_override, lot_override = _render_driveways()

    # ─── ПРАВАЯ КОЛОНКА ─────────────────────────────────────────────
    with col_right:
        kg_spec, include_kg = _render_kg()
        school_spec, include_school = _render_school()
        parking = _render_parking()

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


def _render_housing_and_vpp() -> tuple[int, bool, BuiltInArea | None, bool]:
    """Жилая застройка: этажность, ДПТ и (опционально) ВПП."""
    with st.container(border=True):
        st.markdown("##### Жилая застройка")

        # ДПТ — сверху, выровнен по левому краю (если выключен, ниже —
        # пояснение о потолке КИТ=1.4 и возможном дефиците)
        planning_doc = st.checkbox(
            "ДПТ (документация по планировке территории)",
            value=True, key="planning_doc",
            help="Без ДПТ нормативный потолок КИТ = 1.4; с ДПТ = 2.5.",
        )
        if not planning_doc:
            st.caption(
                "Без ДПТ КИТ ≤ 1.4 — на типичной этажности (≥7) этого добиться "
                "практически невозможно. Включите ДПТ или уменьшите этажность."
            )

        floors = st.number_input(
            "Этажность",
            min_value=1, max_value=40, value=12, step=1,
            key="floors",
        )

        # --- ВПП ---
        st.markdown("**Встроенно-пристроенные помещения (ВПП)**")
        include_vpp = st.checkbox(
            "Учитывать ВПП в составе жилого дома",
            value=False, key="include_vpp",
            help=(
                "ВПП занимает часть GFA дома, требует своих парковок и "
                "озеленения по ВРИ-коду."
            ),
        )
        if not include_vpp:
            return int(floors), planning_doc, None, False

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
            return (
                int(floors), planning_doc,
                BuiltInArea(area_m2=1.0, vri_code=vri_code, label="1 этаж"),
                True,
            )
        vpp_area = st.number_input(
            "Площадь ВПП, м²",
            min_value=10.0, max_value=100_000.0,
            value=2_000.0, step=100.0, key="vpp_area",
        )
        return (
            int(floors), planning_doc,
            BuiltInArea(area_m2=float(vpp_area), vri_code=vri_code),
            False,
        )


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
                "По нормативу",
                "Задать вручную: м²/чел",
                "Задать вручную: общая площадь",
            ],
            key="znop_mode",
            help="По нормативу СПб: ЗНОП зависит от КИТ ступенями 0 / 3 / 4 / 6 м²/чел.",
        )
        if znop_mode == "По нормативу":
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
        kg_only_demand = st.checkbox(
            "Только рассчитать потребность",
            value=False, disabled=not include_kg, key="kg_only_demand",
            help=(
                "Показать число мест и площади объектов, но НЕ учитывать ЗУ "
                "и здание ДОО в балансе квартала. Полезно, если ДОО размещается "
                "за пределами квартала или уже существует."
            ),
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
            "Задать число ДОО вручную",
            value=False, disabled=not include_kg, key="kg_override",
        )
        kg_num_objects, kg_capacity = None, None
        if include_kg and kg_override:
            c1, c2 = st.columns(2)
            kg_num_objects = c1.number_input(
                "Кол-во ДОО", min_value=1, max_value=20, value=2, step=1,
                key="kg_num_objects",
            )
            # Диапазон: минимум 90 (наименьшее значение в allowed_capacities КС),
            # максимум 350 (capacity_max). Step=5 (кратность норматива rounding).
            kg_capacity = c2.number_input(
                "Мест в каждом",
                min_value=90, max_value=350, value=160, step=5,
                help=(
                    "Типовые вместимости КС: 90, 100, 110, 120, 140, 150, 160, "
                    "165, 170, 180, 190, 200, 215, 220, 230, 240, 250, 260, "
                    "280, 310, 320, 340, 350. Иное → предупреждение."
                ),
                key="kg_capacity",
            )
    spec = KindergartenSpec(
        building_type=kg_btype,
        num_objects=int(kg_num_objects) if kg_num_objects else None,
        capacity_per_object=int(kg_capacity) if kg_capacity else None,
        only_demand=bool(kg_only_demand),
    )
    return spec, include_kg


def _render_school() -> tuple[SchoolSpec, bool]:
    with st.container(border=True):
        st.markdown("##### Средние общеобразовательные школы (СОШ)")
        include_school = st.checkbox(
            "Учитывать СОШ", value=True, key="include_school",
        )
        sch_only_demand = st.checkbox(
            "Только рассчитать потребность",
            value=False, disabled=not include_school, key="sch_only_demand",
            help=(
                "Показать число мест и площадь СОШ, но НЕ учитывать ЗУ "
                "в балансе квартала. Полезно, если СОШ размещается "
                "за пределами квартала или уже существует."
            ),
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
            # Диапазон: 550 (II параллель) — 2475 (IX параллель). Step=25 (rounding).
            sch_capacity = c2.number_input(
                "Мест в каждой",
                min_value=550, max_value=2475, value=550, step=25,
                help=(
                    "Типовые параллели КС: 550, 825, 1100, 1375, 1650, 1925, "
                    "2200, 2475. Иное → предупреждение."
                ),
                key="sch_capacity",
            )
    spec = SchoolSpec(
        has_pool=school_pool, has_sport_core=school_sport,
        num_objects=int(sch_num_objects) if sch_num_objects else None,
        capacity_per_object=int(sch_capacity) if sch_capacity else None,
        only_demand=bool(sch_only_demand),
    )
    return spec, include_school


def _render_parking() -> ParkingConfig:
    """Парковки — пресеты + расширенный custom с типами и count×capacity."""
    with st.container(border=True):
        st.markdown("##### Парковки")
        PARK_MODE_LABELS = {
            "Минимум открытых, остальное подземные (по умолчанию)": "min_open",
            "Все парковки открытые наземные": "all_open",
            "50/50: открытые + многоуровневые": "preset_50_50",
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
        if park_mode == "preset_50_50":
            ml_levels = st.number_input(
                "Этажность многоуровневого паркинга",
                min_value=1, max_value=10, value=3, step=1,
                key="park_preset_ml_levels",
                help=(
                    "Многоуровневый паркинг компактнее открытого: его пятно "
                    "обратно пропорционально этажности."
                ),
            )
            st.caption(
                "50% машино-мест — открытые на земле, 50% — в многоуровневых "
                "паркингах с указанной этажностью. Подземных нет."
            )
            return ParkingConfig(
                mode="custom",
                open_share=0.5,
                multilevel_share=0.5,
                underground_share=0.0,
                multilevel_levels=int(ml_levels),
            )
        return _render_parking_custom()


NORM_MIN_OPEN = 12.5  # % — норматив СПб (parking.open_share_min)

# ---------------------------------------------------------------------------
# Зависимые слайдеры парковок с поддержкой блокировки (v0.6.2)
# ---------------------------------------------------------------------------
# Каждый тип имеет:
#   - park_use_X    — включён ли тип (чекбокс)
#   - park_X_pct    — текущая доля, %
#   - park_X_locked — заблокирован ли (галочка «Зафиксировать»)
# Слайдер показывается интерактивным только если включён И не заблокирован И
# есть хотя бы один другой свободный слайдер для перераспределения.

_ALL_TYPES = ["open", "ml", "ug"]
_PCT_KEY = {t: f"park_{t}_pct" for t in _ALL_TYPES}
_USE_KEY = {t: f"park_use_{t}" for t in _ALL_TYPES}
_LOCK_KEY = {t: f"park_{t}_locked" for t in _ALL_TYPES}


def _is_type_active(t: str) -> bool:
    """Тип «активен» если включён чекбоксом и (для ml) не в explicit-режиме."""
    if not st.session_state.get(_USE_KEY[t], False):
        return False
    if t == "ml":
        ml_mode = st.session_state.get(
            "park_ml_mode", "Доля от общей потребности, %"
        )
        if ml_mode.startswith("Количество"):
            return False
    return True


def _is_type_locked(t: str) -> bool:
    return bool(st.session_state.get(_LOCK_KEY[t], False))


def _on_share_change(moved_type: str) -> None:
    """Коллбэк слайдера: перераспределить (100 − залочено − moved) между
    остальными активными незалочеными типами с сохранением их соотношения.
    """
    active = [t for t in _ALL_TYPES if _is_type_active(t)]
    if moved_type not in active:
        return
    locked_sum = sum(
        st.session_state[_PCT_KEY[t]]
        for t in active if _is_type_locked(t)
    )
    moved_value = float(st.session_state[_PCT_KEY[moved_type]])
    target = max(0.0, 100.0 - locked_sum - moved_value)
    others = [
        t for t in active
        if t != moved_type and not _is_type_locked(t)
    ]
    if not others:
        return  # больше некому распределять
    olds = [float(st.session_state[_PCT_KEY[t]]) for t in others]
    s = sum(olds)
    if s > 0:
        new_vals = [target * v / s for v in olds]
    else:
        new_vals = [target / len(others)] * len(others)
    new_vals = [round(v * 2) / 2 for v in new_vals]
    diff = target - sum(new_vals)
    if new_vals:
        new_vals[0] = max(0.0, min(100.0, round((new_vals[0] + diff) * 2) / 2))
    for t, v in zip(others, new_vals):
        st.session_state[_PCT_KEY[t]] = max(0.0, min(100.0, v))


def _init_parking_state() -> None:
    """Дефолты session_state при первом рендере."""
    defaults = {
        "park_open_pct": 12.5,
        "park_ml_pct": 0.0,
        "park_ug_pct": 87.5,
        "park_open_locked": False,
        "park_ml_locked": False,
        "park_ug_locked": False,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def _rebalance_active_to_100() -> None:
    """Привести сумму активных типов к 100%, не трогая залоченные.
    Неактивные обнуляются. Вызывается перед рендером слайдеров.
    """
    active = [t for t in _ALL_TYPES if _is_type_active(t)]
    # Обнуляем неактивные
    for t in _ALL_TYPES:
        if t not in active:
            st.session_state[_PCT_KEY[t]] = 0.0

    if not active:
        return

    locked = [t for t in active if _is_type_locked(t)]
    unlocked = [t for t in active if not _is_type_locked(t)]
    locked_sum = sum(st.session_state[_PCT_KEY[t]] for t in locked)

    if not unlocked:
        # Нечего двигать; если сумма не 100%, отметим визуально
        return

    target = max(0.0, 100.0 - locked_sum)
    cur_unlocked_sum = sum(st.session_state[_PCT_KEY[t]] for t in unlocked)
    if abs(cur_unlocked_sum - target) < 0.05:
        return  # уже сбалансировано

    if cur_unlocked_sum > 0:
        scale = target / cur_unlocked_sum
        for t in unlocked:
            st.session_state[_PCT_KEY[t]] = round(
                st.session_state[_PCT_KEY[t]] * scale * 2
            ) / 2
    else:
        share = target / len(unlocked)
        for t in unlocked:
            st.session_state[_PCT_KEY[t]] = round(share * 2) / 2

    # Поправка от округления — в первый разлоченный
    diff = target - sum(st.session_state[_PCT_KEY[t]] for t in unlocked)
    st.session_state[_PCT_KEY[unlocked[0]]] = max(
        0.0,
        min(
            100.0,
            round((st.session_state[_PCT_KEY[unlocked[0]]] + diff) * 2) / 2,
        ),
    )


def _render_share_slider(
    type_key: str,
    label: str,
    interactive: bool,
    show_norm_warning: bool = False,
    is_remainder_mode: bool = False,
) -> None:
    """Рендер слайдера/метрики для одного типа парковок.

    Args:
        type_key:           один из "open"/"ml"/"ug"
        label:              заголовок секции
        interactive:        если False — слайдер показывается как disabled
                            (залочен), или как metric если не может двигаться.
        show_norm_warning:  для открытых — красная подсветка при <12.5%
        is_remainder_mode:  для open/ug в режиме «count × cap» для multilevel:
                            доли распределяются на остаток после явных ml-мест.
    """
    pct_key = _PCT_KEY[type_key]
    lock_key = _LOCK_KEY[type_key]
    is_locked = st.session_state.get(lock_key, False)

    # В режиме count×cap для multilevel — open и ug делят остаток.
    # Слайдер показывает долю В ОСТАТКЕ (не от общего числа м/м).
    slider_label = (
        "Доля в остатке, %"
        if is_remainder_mode
        else "Доля, %"
    )

    # Замок-чекбокс слева, слайдер справа.
    # CSS: компенсируем вертикальный отступ slider-лейбла, чтобы
    # чекбокс визуально стоял на уровне слайдера, а не над ним.
    c_lock, c_slider = st.columns([1, 12], vertical_alignment="bottom")
    with c_lock:
        # Текст-подсказка чекбокса = его эмодзи (без help-«?», который
        # налезал на слайдер). Поведение объясняется в caption выше.
        st.checkbox("🔒", key=lock_key, label_visibility="visible")

    with c_slider:
        if not interactive:
            st.metric(slider_label, f"{st.session_state[pct_key]:.1f}%")
        else:
            st.slider(
                slider_label,
                min_value=0.0, max_value=100.0, step=0.5,
                key=pct_key,
                disabled=is_locked,
                on_change=(_on_share_change if not is_locked else None),
                args=((type_key,) if not is_locked else None),
            )

    if show_norm_warning and st.session_state[pct_key] < NORM_MIN_OPEN:
        st.markdown(
            f"<div style='color:#A4262C;font-size:0.85em;margin-top:-0.5rem;'>"
            f"⚠ Ниже норматива СПб ({NORM_MIN_OPEN}%) — расчёт "
            f"принудительно поднимет долю до {NORM_MIN_OPEN}%."
            f"</div>",
            unsafe_allow_html=True,
        )


def _render_parking_custom() -> ParkingConfig:
    """Custom-режим парковок (v0.6.2):
       - чекбоксы для включения каждого типа;
       - «🔒 Зафиксировать» для каждого активного типа;
       - зависимые слайдеры: незалоченные перераспределяются;
       - открытые подсвечиваются красным при <12.5%;
       - многоуровневые: альтернативный режим «кол-во × вместимость» —
         открытые и подземные тогда делят ОСТАТОК после явных м/м.
    """
    _init_parking_state()

    st.caption(
        "Выберите типы парковок и доли. Замок 🔒 рядом со слайдером "
        "фиксирует долю — двигаться будут только остальные. "
        "Сумма всех активных = 100%."
    )

    c1, c2, c3 = st.columns(3)
    with c1:
        use_open = st.checkbox("Открытые наземные", value=True, key=_USE_KEY["open"])
    with c2:
        use_ml = st.checkbox("Многоуровневые наземные", value=False, key=_USE_KEY["ml"])
    with c3:
        use_ug = st.checkbox("Подземные", value=True, key=_USE_KEY["ug"])

    if not (use_open or use_ml or use_ug):
        st.error("Выберите хотя бы один тип парковок.")
        return ParkingConfig(mode="min_open")

    # Определяем режим multilevel (Доля / Количество × вместимость)
    ml_use_explicit = False
    if use_ml:
        ml_mode_label = st.session_state.get(
            "park_ml_mode", "Доля от общей потребности, %"
        )
        ml_use_explicit = ml_mode_label.startswith("Количество")

    # Перебалансируем активные доли к 100% (с учётом залоченных)
    _rebalance_active_to_100()

    # Считаем сколько активных-незалоченных — для решения slider vs metric
    active_types = [t for t in _ALL_TYPES if _is_type_active(t)]
    unlocked_types = [t for t in active_types if not _is_type_locked(t)]
    can_redistribute = len(unlocked_types) >= 2

    ml_explicit_places: int | None = None
    ml_levels = 3

    # === Открытые ===
    if use_open:
        with st.container(border=True):
            st.markdown("**Открытые наземные**")
            _render_share_slider(
                "open", "Открытые наземные",
                interactive=can_redistribute or _is_type_locked("open"),
                show_norm_warning=True,
                is_remainder_mode=ml_use_explicit,
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
                _render_share_slider(
                    "ml", "Многоуровневые",
                    interactive=can_redistribute or _is_type_locked("ml"),
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
                # Если есть последний расчёт — покажем оценку остатка
                last_total = st.session_state.get("_last_total_required")
                if last_total and last_total > 0:
                    est_remainder = max(0, last_total - ml_explicit_places)
                    st.info(
                        f"Многоуровневые: **{ml_explicit_places} м/м** "
                        f"({ml_n} × {ml_cap}). По последнему расчёту общая "
                        f"потребность ≈ **{last_total} м/м**; на открытые и "
                        f"подземные остаётся ≈ **{est_remainder} м/м** — их "
                        f"и распределяют доли ниже."
                    )
                else:
                    st.info(
                        f"Многоуровневые: **{ml_explicit_places} м/м** "
                        f"({ml_n} × {ml_cap}). Это абсолютное число — оно "
                        f"«забирается» из общей потребности **в первую очередь**. "
                        f"Открытые и подземные затем делят остаток в долях ниже."
                    )
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
            _render_share_slider(
                "ug", "Подземные",
                interactive=can_redistribute or _is_type_locked("ug"),
                is_remainder_mode=ml_use_explicit,
            )

    # === Сборка ParkingConfig ===
    open_pct = st.session_state.park_open_pct if use_open else 0.0
    ml_pct = (
        st.session_state.park_ml_pct
        if (use_ml and not ml_use_explicit) else 0.0
    )
    ug_pct = st.session_state.park_ug_pct if use_ug else 0.0

    if ml_use_explicit:
        # multilevel — абсолютным числом. open и ug делят остаток.
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


def _render_custom_objects() -> list[CustomObject]:
    """Табличный редактор для дополнительных объектов на территории."""
    with st.container(border=True):
        st.markdown("##### Дополнительные объекты на территории квартала")
        st.caption(
            "Объекты вне базовых классов (офис, ФОК, поликлиника, "
            "торговля). Каждый занимает свой ЗУ и считается по ВРИ-коду. "
            "Чтобы добавить — нажмите «+» в таблице, заполните строку и "
            "нажмите «Применить»."
        )

        if "custom_objects" not in st.session_state:
            st.session_state.custom_objects = []

        # DataFrame только из реально применённых объектов; пусто = пусто
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
        df = pd.DataFrame(
            rows,
            columns=["Название", "Площадь ЗУ, м²", "ВРИ-код", "Общая площадь, м²"],
        )

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

        col1, col2, _ = st.columns([1, 1, 3])
        with col1:
            if st.button("Применить", type="primary", use_container_width=True):
                new_list = []
                for _, row in edited_df.iterrows():
                    try:
                        name = str(row["Название"]).strip()
                        plot = float(row["Площадь ЗУ, м²"])
                        vri = str(row["ВРИ-код"])
                        if not name or plot <= 0:
                            continue
                        new_list.append({
                            "name": name,
                            "plot_area_m2": plot,
                            "vri_code": vri,
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
            if st.button("Очистить", use_container_width=True,
                         disabled=not st.session_state.custom_objects):
                st.session_state.custom_objects = []
                st.rerun()

    return [CustomObject(**obj) for obj in st.session_state.custom_objects]


# ---------------------------------------------------------------------------
# Совместимость со старым кодом
# ---------------------------------------------------------------------------

def render_sidebar() -> UserInputs:
    """Алиас для обратной совместимости."""
    return render_params_tab()
