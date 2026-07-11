"""Вкладка «Параметры»: полноэкранная форма ввода → UserInputs.

v0.6.0 — все параметры в одной вкладке (включая объекты, парковки,
ЗНОП с двумя способами задания).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import streamlit as st

from urban_model.calculations.vpp import VppMode
from urban_model.models import CalculationOptions, FloorCluster, Site
from urban_model.models.built_in import BuiltInArea
from urban_model.models.custom_object import CustomObject
from urban_model.models.engineering import (
    ENGINEERING_KEYS,
    ENGINEERING_LABELS,
    EngineeringSpec,
)
from urban_model.models.parking import ParkingConfig
from urban_model.models.social import (
    AdditionalEducationSpec,
    KindergartenSpec,
    PolyclinicSpec,
    SchoolSpec,
    SportFacilitiesSpec,
)


@dataclass
class VppRequest:
    """Запрос на расчёт ВПП по одному из 5 вариантов (v0.7.1).

    Используется state.run_calculation для двухпроходного расчёта:
    проход 1 → footprint + population → vpp.build_built_ins → проход 2.
    """
    mode: VppMode
    custom_4_4_m2: float | None = None
    custom_4_6_m2: float | None = None


@dataclass
class UserInputs:
    site: Site
    options: CalculationOptions
    mode: str  # "max_kit" | "with_reserve" | "verify"
    target_surplus_m2: float
    verify_kit_value: float
    vpp_auto_one_floor: bool
    # v0.7.1: новый механизм списка ВПП с 5 режимами. Если None — ВПП не считаем.
    vpp_request: VppRequest | None = None


# Допустимые ВРИ для произвольных объектов
_VRI_OPTIONS = ["3.4", "3.5", "3.6", "3.7", "4.0", "4.1", "4.4", "4.5", "4.6", "5.1"]


def _close_tile_cb(include_key: str) -> None:
    """on_click callback для крестика плитки.

    Streamlit запрещает менять st.session_state[key] после того, как
    соответствующий widget уже создан в этот run. Callback'и выполняются
    ДО следующего цикла render — это разрешённое место для записи.
    """
    st.session_state[include_key] = False


def _tile_header(title: str, include_key: str | None = None) -> None:
    """Заголовок плитки с опциональным крестиком «✕» справа (v0.8.8).

    Если передан `include_key` — справа отрисовывается кнопка «✕»,
    которая через on_click-callback ставит `include_key = False` (тот же
    эффект, что снять галочку слева).
    """
    if include_key is None:
        st.markdown(f"##### {title}")
        return
    # v0.10.18: × через type="tertiary" — минимал-тип кнопки в Streamlit
    # 1.34+ без рамки и фона. Идеально для иконки-крестика в углу.
    col_t, col_x = st.columns([7, 1], vertical_alignment="top")
    with col_t:
        st.markdown(f"##### {title}")
    with col_x:
        st.button(
            "✕", key=f"close_{include_key}",
            on_click=_close_tile_cb, args=(include_key,),
            help="Скрыть блок",
            type="tertiary",
        )


def _only_demand_toggle(label: str, key: str, help_text: str) -> bool:
    """v0.8.9: «Только рассчитать потребность» как st.toggle, без бейджа.

    Toggle визуально отличается от обычной галочки и подразумевает
    переключение режима. На «выскакивающее сообщение» бейдж заменён
    штатным внешним видом toggle.
    """
    return st.toggle(label, value=False, key=key, help=help_text)


def render_params_tab() -> UserInputs:
    """Двухколоночная форма параметров (v0.6.7).

    Левая колонка (1/3): общие сведения о территории + чекбоксы «учитывать в расчёте».
    Правая колонка (2/3): плитки с настройками для включённых компонентов.
    """
    # v0.10.18: h1-заголовок вкладки в стиле макета (тонкий + жирное слово).
    st.markdown("# Параметры **расчёта**")
    st.caption(
        "Выберите слева компоненты — справа появятся плитки с их настройками. "
        "Изменения применяются автоматически — результат на вкладке «Расчёт»."
    )

    # v0.14.0: сохранение/загрузка проекта (все параметры формы → JSON +
    # локальные пресеты). Рендерится ДО остальных виджетов, чтобы применение
    # проекта записало session_state до их создания.
    from urban_model.ui.project_io import render_project_bar
    render_project_bar()

    # ==================================================================
    # Верхняя строка: режим расчёта (заголовок — в стиле «Настройки компонентов»)
    # ==================================================================
    st.markdown(
        '<div style="color:#475569;font-size:0.85rem;font-weight:600;'
        'margin:8px 0 4px;letter-spacing:0.02em;text-transform:uppercase;">'
        'Режим расчёта</div>',
        unsafe_allow_html=True,
    )
    col_mode1, col_mode2 = st.columns([3, 2])
    with col_mode1:
        mode_label = st.radio(
            "Режим расчёта",
            ["Максимальный КИТ", "С целевым резервом", "Проверка КИТ"],
            index=0, horizontal=True, key="calc_mode_label",
            label_visibility="collapsed",
            help=(
                "Максимальный КИТ — найти предельный КИТ при выполнении норм. "
                "С целевым резервом — оставить заданную свободную площадь. "
                "Проверка КИТ — посчитать заданную плотность."
            ),
        )
    with col_mode2:
        target_surplus_m2 = 0.0
        verify_kit_value = 1.5
        if mode_label == "Максимальный КИТ":
            mode = "max_kit"
        elif mode_label == "С целевым резервом":
            mode = "with_reserve"
            target_surplus_m2 = float(st.number_input(
                "Целевой резерв, м²",
                min_value=0.0, max_value=500_000.0,
                value=5_000.0, step=500.0, key="target_surplus_m2",
            ))
        else:
            mode = "verify"
            verify_kit_value = float(st.number_input(
                "Плотность (block_density)",
                min_value=0.1, max_value=5.0, value=1.5, step=0.05,
                key="verify_kit",
                help="Плотность GFA/S_квартала. Фактический КИТ "
                     "(площадь квартир / ЗУ жилой) рассчитается.",
            ))

    # v0.11.0: тонкий разделитель вместо st.markdown("---") — поджимаем отступ.
    st.markdown(
        '<hr style="margin:10px 0 14px;border:none;border-top:1px solid #EDEDED;">',
        unsafe_allow_html=True,
    )

    col_left, col_right = st.columns([1, 2], gap="medium")

    # ─── ЛЕВАЯ КОЛОНКА: общие сведения + чекбоксы ──────────────────
    with col_left:
        # v0.9.15: невидимый CSS-маркер — по нему `:has(.params-col-input)`
        # в app.py стилизует бордер-контейнеры этой колонки.
        st.markdown(
            '<div class="params-col-input" style="display:none"></div>',
            unsafe_allow_html=True,
        )
        # v0.12.8: левые карточки получают key → Streamlit вешает класс
        # .st-key-param_left_card, по нему app.py красит фон/рамку БЕЗ :has()
        # (прежний :has-селектор не срабатывал в проде).
        # v0.10.19: один блок «Общие сведения о территории» (не expander) —
        # в собственном st.container(border=True) с padding/рамкой.
        with st.container(border=True, key="param_left_card_essentials"):
            (
                site, floors, planning_doc, lot_override,
                enforce_greening_norm, enforce_density_norm, floor_clusters,
            ) = _render_essentials()
            # v0.15.0: очерёдность застройки (территориальные этапы).
            phasing_spec = _render_phasing_expander()

        with st.container(border=True, key="param_left_card_include"):
            st.markdown("##### Учитывать в расчёте")
            st.caption("Включите компоненты — справа появятся их настройки.")
            # ─── Объекты по НГП: мастер-чекбокс + вложенные (со сдвигом) ───
            # Снятие мастера выключает все вложенные сразу (disabled + эффект.
            # include = вложенный AND мастер).
            include_ngp = st.checkbox(
                ":material/domain: Объекты по НГП",
                value=True, key="include_ngp",
                help=(
                    "Соцобъекты с нормативной потребностью: ДОО, СОШ, доп. "
                    "образование. Снимите — выключить все сразу."
                ),
            )
            _ng_sp, _ng_col = st.columns([0.05, 0.95])
            with _ng_col:
                _inc_kg = st.checkbox(
                    ":material/child_care: ДОО — детские сады",
                    value=True, key="include_kg", disabled=not include_ngp,
                )
                _inc_sch = st.checkbox(
                    ":material/school: СОШ — школы",
                    value=True, key="include_school", disabled=not include_ngp,
                )
                _inc_ae = st.checkbox(
                    ":material/palette: Организации доп. образования",
                    value=True, key="include_add_education", disabled=not include_ngp,
                    help="ВРИ 3.5.1 (РМД 15-26-2017): 65 мест/1000 чел.",
                )
                _inc_poly = st.checkbox(
                    ":material/local_hospital: Поликлиника",
                    value=True, key="include_polyclinic", disabled=not include_ngp,
                    help="ВРИ 3.4.1 (НГП СПб + СП 158.13330): 26.33 посещ./1000 чел.",
                )
            include_kg = bool(_inc_kg) and include_ngp
            include_school = bool(_inc_sch) and include_ngp
            include_add_education = bool(_inc_ae) and include_ngp
            include_polyclinic = bool(_inc_poly) and include_ngp
            st.markdown(
                '<hr style="margin:6px 0 6px;border:none;border-top:1px solid #EDEDED;">',
                unsafe_allow_html=True,
            )
            include_sport = st.checkbox(
                ":material/directions_run: Плоскостные спортивные сооружения",
                value=True, key="include_sport",
                help=(
                    "Норматив: 1000 м²/1000 чел + 40% озеленения по ПЗЗ (ВРИ 5.1.3). "
                    "До 49% озеленения замещается самой спортплощадкой (п. 1.9.4 ПЗЗ)."
                ),
            )
            include_parking = st.checkbox(":material/local_parking: Парковки", value=True, key="include_parking")
            include_znop = st.checkbox(
                ":material/park: ЗНОП — зелёные насаждения общего пользования",
                value=True, key="include_znop",
            )
            include_vpp = st.checkbox(
                ":material/storefront: ВПП — встроенно-пристроенные помещения",
                value=True, key="include_vpp",
            )
            include_intra = st.checkbox(
                ":material/route: Внутриквартальные проезды", value=True, key="include_intra_driveways",
            )
            include_engineering = st.checkbox(
                ":material/bolt: Инженерная инфраструктура", value=True,
                key="include_engineering",
                help=(
                    "ТП/РТП, котельная/ГРП, ОСПС/насосная (ВРИ 3.1). Каждый "
                    "объект занимает свой ЗУ. Раньше был неявно в проездах."
                ),
            )
            include_custom = st.checkbox(
                ":material/inventory_2: Дополнительные объекты", value=False, key="include_custom_objects",
            )
            include_economy = st.checkbox(
                ":material/payments: Экономика (баллы выгодности)",
                value=True, key="include_economy",
                help=(
                    "Расчёт стоимости / выручки / прибыли в условных единицах. "
                    "Безразмерные баллы для сравнения вариантов (1.0 балл ≈ "
                    "м² жилья 9 эт. монолит standard)."
                ),
            )

    # ─── ПРАВАЯ КОЛОНКА: плитки в 2 колонки (v0.8.8) ────────────────
    # Дефолты ставим заранее — на случай если тайл не активен.
    kg_spec = KindergartenSpec()
    school_spec = SchoolSpec()
    add_edu_spec = AdditionalEducationSpec()
    poly_spec = PolyclinicSpec()
    sport_spec = SportFacilitiesSpec()
    parking = ParkingConfig(mode="min_open")
    znop_pp_override = None
    znop_total_override = None
    znop_only_demand = False
    vpp_request = None
    built_in = None
    vpp_auto = False
    intra_override = None
    custom_objects_list: list = []
    engineering_spec = EngineeringSpec()
    residential_class = "economy"
    social_funding = "compensated"
    social_comp_share = None

    # Активные тайлы: (key, render-callable). Раскладываются по 2 столбцам.
    active_tiles: list[tuple[str, "callable"]] = []
    if include_kg:       active_tiles.append(("kg", _render_kg_tile))
    if include_school:   active_tiles.append(("school", _render_school_tile))
    if include_add_education: active_tiles.append(("add_edu", _render_add_education_tile))
    if include_polyclinic: active_tiles.append(("poly", _render_polyclinic_tile))
    if include_sport:    active_tiles.append(("sport", _render_sport_tile))
    if include_parking:  active_tiles.append(("parking", _render_parking_tile))
    if include_znop:     active_tiles.append(("znop", _render_znop_tile))
    if include_vpp:      active_tiles.append(("vpp", _render_vpp_tile))
    # v0.10.19: плитка настроек проездов СКРЫТА (на время тестирования —
    # детали расчёта проездов пользователю знать не нужно). Флаг include_intra
    # продолжает учитываться в расчёте, доля — по нормативу (override=None).
    if include_engineering: active_tiles.append(("engineering", _render_engineering_tile))
    if include_custom:   active_tiles.append(("custom", _render_custom_objects_tile))
    if include_economy:  active_tiles.append(("economy", _render_economy_tile))

    with col_right:
        st.markdown(
            '<div class="params-col-settings" style="display:none"></div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div style="color:#475569;font-size:0.85rem;font-weight:600;'
            'margin-bottom:18px;letter-spacing:0.02em;text-transform:uppercase;">'
            'Настройки компонентов</div>',
            unsafe_allow_html=True,
        )
        if not active_tiles:
            st.info(
                "⬅ Выберите хотя бы один компонент в левой колонке, "
                "чтобы увидеть его настройки здесь."
            )
        else:
            # v0.10.18: «широкие» плитки (parking + custom_objects) рендерятся
            # на ВСЮ ширину правой колонки — там много контролов, в 2-кол
            # сетке они сжимаются. Остальные — стандартная 2-кол сетка.
            WIDE_KEYS = {"parking", "custom", "vpp", "engineering"}
            narrow = [(k, fn) for k, fn in active_tiles if k not in WIDE_KEYS]
            wide = [(k, fn) for k, fn in active_tiles if k in WIDE_KEYS]
            results: dict[str, object] = {}
            sub_cols = st.columns(2, gap="small")
            for i, (key, fn) in enumerate(narrow):
                with sub_cols[i % 2]:
                    results[key] = fn()
            for key, fn in wide:
                results[key] = fn()

            kg_spec = results.get("kg", kg_spec)
            school_spec = results.get("school", school_spec)
            add_edu_spec = results.get("add_edu", add_edu_spec)
            poly_spec = results.get("poly", poly_spec)
            sport_spec = results.get("sport", sport_spec)
            parking = results.get("parking", parking)
            if "znop" in results:
                znop_pp_override, znop_total_override, znop_only_demand = results["znop"]
            vpp_request = results.get("vpp", vpp_request)
            intra_override = results.get("intra", intra_override)
            engineering_spec = results.get("engineering", engineering_spec)
            custom_objects_list = results.get("custom", custom_objects_list)
            if "economy" in results:
                residential_class, social_funding, social_comp_share = results["economy"]

    # ==================================================================
    # Сборка опций
    # ==================================================================
    options = CalculationOptions(
        floors=int(floors),
        floor_clusters=floor_clusters,
        planning_doc=planning_doc,
        include_kindergarten=include_kg,
        include_school=include_school,
        include_sport_facilities=include_sport,
        include_parking=include_parking,
        include_znop=include_znop,
        include_intra_driveways=include_intra,
        include_engineering=include_engineering,
        engineering=engineering_spec,
        kindergarten=kg_spec,
        school=school_spec,
        include_add_education=include_add_education,
        add_education=add_edu_spec,
        include_polyclinic=include_polyclinic,
        polyclinic=poly_spec,
        sport_facilities=sport_spec,
        parking=parking,
        built_in=built_in,
        znop_per_person_override=znop_pp_override,
        znop_total_area_override=znop_total_override,
        znop_only_demand=znop_only_demand,
        custom_objects=custom_objects_list,
        driveways_intra_share_override=intra_override,
        driveways_lot_share_override=lot_override,
        include_economy=include_economy,
        residential_class=residential_class,
        social_funding=social_funding,
        social_compensation_share=social_comp_share,
        enforce_quarter_greening_norm=enforce_greening_norm,
        enforce_density_norm=enforce_density_norm,
        phasing=phasing_spec,
    )
    return UserInputs(
        site=site, options=options, mode=mode,
        target_surplus_m2=target_surplus_m2,
        verify_kit_value=verify_kit_value,
        vpp_auto_one_floor=vpp_auto,
        vpp_request=vpp_request,
    )


# ===========================================================================
# Секции — выделены в отдельные функции для читаемости
# ===========================================================================

def _render_sport_tile() -> SportFacilitiesSpec:
    """Плитка настроек плоскостных спортивных сооружений (без чекбокса 'Учитывать')."""
    with st.container(border=True):
        _tile_header(":material/directions_run: Плоскостные спортивные сооружения", "include_sport")
        only_demand = _only_demand_toggle(
            "Только рассчитать потребность",
            key="sport_only_demand",
            help_text=(
                "Показать площади, но НЕ учитывать ЗУ спортсооружений в "
                "балансе квартала. Полезно, если они размещаются за пределами "
                "квартала или уже существуют."
            ),
        )
        size_mode = st.radio(
            "Площадь спортплощадок",
            ["По нормативу (1000 м²/1000 чел)", "Задать вручную"],
            key="sport_size_mode",
        )
        area_override: float | None = None
        if size_mode == "Задать вручную":
            c1, c2 = st.columns(2)
            unit = c1.radio(
                "Единица", ["м²", "га"],
                horizontal=True, key="sport_area_unit",
                label_visibility="collapsed",
            )
            if unit == "га":
                area_ga = c2.number_input(
                    "Площадь, га",
                    min_value=0.0, max_value=50.0,
                    value=0.1, step=0.05,
                    key="sport_area_ga",
                )
                area_override = float(area_ga) * 10_000
            else:
                area_override = float(c2.number_input(
                    "Площадь, м²",
                    min_value=0.0, max_value=500_000.0,
                    value=1_000.0, step=100.0,
                    key="sport_area_m2",
                ))
    return SportFacilitiesSpec(
        only_demand=bool(only_demand),
        area_override_m2=area_override,
    )


def _render_intra_driveways_tile() -> float | None:
    """Плитка настроек внутриквартальных проездов. Override на долю."""
    with st.container(border=True):
        _tile_header(":material/route: Внутриквартальные проезды", "include_intra_driveways")
        use_override = st.checkbox(
            "Задать долю вручную (вместо норматива)",
            value=False, key="drive_intra_override",
        )
        if not use_override:
            st.caption("По умолчанию: 10% от площади квартала (норматив).")
            return None
        intra_pct = st.slider(
            "Доля от S_квартала, %",
            min_value=0, max_value=30, value=10, step=1,
            key="drive_intra_pct",
        )
        return intra_pct / 100


def _render_engineering_tile() -> EngineeringSpec:
    """Плитка инженерной инфраструктуры (v0.12).

    Управление: тип плит (электро/газ), раскрывающийся список 6 объектов с
    переключателем «только потребность» по каждому, advanced-override нагрузок.
    Возвращает EngineeringSpec.
    """
    with st.container(border=True):
        _tile_header(":material/bolt: Инженерная инфраструктура", "include_engineering")
        st.caption(
            "ТП/РТП (электро), котельная/ГРП (тепло+газ), ОСПС/насосная "
            "(водоотведение). Количество и площади считаются от площади квартир, "
            "населения и числа соцобъектов."
        )

        # --- Приготовление пищи (газ пока недоступен — серым) ---
        st.radio(
            "Приготовление пищи",
            ["Электроплиты", "Газовые плиты (в разработке)"],
            index=0, horizontal=True, key="eng_cooking",
            disabled=True,
            help="Тип плит влияет на электрическую нагрузку (ТП) и "
                 "газоснабжение. Газовый режим будет добавлен позже.",
        )
        cooking = "electric"

        # --- Список объектов с режимом «только потребность» ---
        st.markdown("**Учитывать ЗУ в балансе** (снимите — объект «только потребность»):")
        demand_only: list[str] = []
        grid = st.columns(2, gap="small")
        for i, key in enumerate(ENGINEERING_KEYS):
            with grid[i % 2]:
                in_balance = st.checkbox(
                    ENGINEERING_LABELS[key], value=True, key=f"eng_inbal_{key}",
                    help="Снято — объект считается, но его ЗУ не изымается из "
                         "баланса (размещается вне квартала / городские сети).",
                )
                if not in_balance:
                    demand_only.append(key)

        # --- Override удельных нагрузок (без expander — иначе при клике
        # чекбокса expander сворачивается и скрывает раскрытые поля) ---
        use_q = st.toggle(
            "Задать удельные нагрузки вручную", value=False, key="eng_q_override",
            help="По умолчанию — норматив (тепло 0.10 кВт/м², вода 230 л/чел·сут).",
        )
        q_heat = q_water = None
        if use_q:
            qc1, qc2 = st.columns(2)
            q_heat = float(qc1.number_input(
                "Тепловая нагрузка, кВт/м² квартир",
                min_value=0.02, max_value=0.30, value=0.10, step=0.01,
                key="eng_q_heat",
            ))
            q_water = float(qc2.number_input(
                "Водоотведение, л/чел·сут",
                min_value=100.0, max_value=400.0, value=230.0, step=10.0,
                key="eng_q_water",
            ))

    return EngineeringSpec(
        cooking=cooking,
        demand_only=demand_only,
        q_heat_override=q_heat,
        q_water_override=q_water,
    )


def _render_essentials() -> tuple[Site, int, bool, "float | None", bool, bool, list]:
    """Общие сведения о территории + нормативы-ограничения.

    v0.8.8: блок свёртываемый (st.expander), нормативы 25%/450 чел/га
    встроены сюда же. Возвращает
    (site, floors, planning_doc, lot_override, enforce_greening, enforce_density,
     floor_clusters).
    """
    # v0.9.29: если включён режим зон — площадь и этажность определяются
    # зонами. Поля выше блокируем (disabled), чтобы не вводить в заблуждение.
    _clusters_on = bool(st.session_state.get("use_floor_clusters", False))

    # v0.10.19: не expander, а обычный блок-заголовок (по просьбе —
    # «Общие сведения о территории» как единый раздел, всегда раскрыт).
    st.markdown("##### Общие сведения о территории")
    if True:
        # Площадь квартала
        unit = st.radio(
            "Единицы площади", ["м²", "га"],
            horizontal=True, key="area_unit",
            label_visibility="collapsed",
            disabled=_clusters_on,
        )
        if unit == "га":
            area_ga = st.number_input(
                "Площадь квартала, га",
                min_value=0.1, max_value=200.0,
                value=5.0, step=0.1,
                key="area_input_ga",
                disabled=_clusters_on,
            )
            area_m2 = area_ga * 10_000
        else:
            area_m2 = st.number_input(
                "Площадь квартала, м²",
                min_value=1_000.0, max_value=2_000_000.0,
                value=50_000.0, step=1_000.0,
                key="area_input_m2",
                disabled=_clusters_on,
            )
        if _clusters_on:
            st.caption("ℹ Площадь и этажность задаются зонами ниже (Σ зон).")

        # ДПТ — caption под чекбоксом убран (v0.8.8): и так понятно
        planning_doc = st.checkbox(
            "ДПТ (документация по планировке территории)",
            value=True, key="planning_doc",
            help="Без ДПТ нормативный потолок КИТ = 1.4; с ДПТ = 2.5.",
        )

        # Этажность — слайдер (v0.11.0): удобнее, чем +/- у number_input.
        # Диапазон 3–30 эт. — как в «Оптимизации».
        floors = st.slider(
            "Этажность (средняя)",
            min_value=3, max_value=30, value=12, step=1,
            key="floors",
            help="Средняя этажность жилой застройки. Влияет на долю проездов на ЗУ (зависит от этажности).",
            disabled=_clusters_on,
        )

        # v0.9.28: кластеры этажности — подучастки с разной высотностью (зоны ПЗЗ)
        floor_clusters = _render_clusters_editor(area_m2, int(floors))
        # При активных зонах площадь квартала = сумме площадей зон.
        if floor_clusters:
            area_m2 = sum(c.area_m2 for c in floor_clusters)

        # v0.10.19: expander «Проезды на ЗУ» СКРЫТ (на время тестирования).
        # Доля проездов считается по нормативу (override=None).
        lot_override = None

        # v0.8.8: нормативы-ограничения встроены в этот же блок.
        st.markdown("**Нормативы-ограничения**")
        enforce_greening = st.checkbox(
            ":material/grass: Соблюдать норматив 25% озеленения квартала",
            value=True, key="enforce_quarter_greening_norm",
            help=(
                "СП 42.13330: минимум 25% площади квартала под озеленение. "
                "Выключите для малых кварталов или при компенсации озеленения "
                "вне границ территории."
            ),
        )
        enforce_density = st.checkbox(
            ":material/groups: Соблюдать норматив 450 чел/га",
            value=True, key="enforce_density_norm",
            help=(
                "СП 42.13330: предельная плотность населения для многоэтажной "
                "застройки. Выключите для физического максимума КИТ."
            ),
        )

    return (
        Site(area_m2=area_m2), int(floors), planning_doc, lot_override,
        enforce_greening, enforce_density, floor_clusters,
    )


def _render_phasing_expander():
    """Очерёдность застройки (v0.15.0): 2–4 очереди долями площади.

    Раскладка соцобъектов/инженерки по очередям — автоматическая (по
    накопительной потребности), считается поверх готового результата.
    Все виджеты — скаляры → сохраняются в файл проекта.
    """
    from urban_model.models.phasing import PhasingSpec

    with st.expander(":material/stairs: Очерёдность застройки", expanded=False):
        on = st.checkbox(
            "Разбить на очереди", value=False, key="phasing_on",
            help=(
                "Территориальные этапы долями площади. Модель проверит "
                "обеспеченность ДОО/СОШ и инженерией на конец каждой очереди."
            ),
        )
        if not on:
            return None
        n = int(st.number_input("Число очередей", min_value=2, max_value=4,
                                value=2, step=1, key="phasing_n"))
        shares: list[float] = []
        cols = st.columns(n)
        for i in range(n):
            with cols[i]:
                shares.append(float(st.number_input(
                    f"Оч. {i + 1}, %", min_value=1.0, max_value=97.0,
                    value=round(100.0 / n, 0), step=1.0,
                    key=f"phase_share_{i + 1}",
                )))
        tot = sum(shares)
        note = "" if abs(tot - 100.0) < 0.5 else f" (Σ={tot:.0f}% → нормализуются)"
        st.caption(
            f"Доли площади квартала по очередям{note}. Корпуса ДОО/СОШ и "
            f"объекты инженерии раскладываются по очередям автоматически — "
            f"по накопительной потребности."
        )
        return PhasingSpec(shares=[s / tot for s in shares])


def _render_lot_share_expander() -> float | None:
    """Свёрнутый ползунок: переопределение доли проездов на ЗУ жилой застройки."""
    with st.expander(":material/settings: Проезды на ЗУ", expanded=False):
        st.caption(
            "По нормативу доля зависит от этажности застройки. "
            "Слайдер позволяет переопределить вручную."
        )
        use_override = st.checkbox(
            "Задать вручную", value=False, key="drive_lot_override",
        )
        if not use_override:
            return None
        lot_pct = st.slider(
            "Доля от S_застройки, %",
            min_value=0, max_value=300, value=120, step=5,
            key="drive_lot_pct",
        )
        return lot_pct / 100


def _render_clusters_editor(area_m2: float, floors: int) -> list[FloorCluster]:
    """Редактор зон этажности (v0.10.3): до 3 подучастков с разной высотностью.

    Единый чекбокс (без вложенного expander). При включении площадь квартала =
    Σ площадей зон. Диапазон этажности для подбора задаётся НЕ здесь, а во
    вкладке «Оптимизация» → «Настройки подбора».
    """
    _MAX_CLUSTERS = 3
    use_clusters = st.checkbox(
        ":material/apartment: Разная этажность по зонам",
        value=False, key="use_floor_clusters",
        help=(
            "Делит квартал на подучастки с собственной этажностью. "
            "Площадь квартала = сумме площадей зон (поле «Площадь квартала» "
            "выше при этом не используется). Баланс и озеленение — по "
            "средневзвешенной этажности; КИТ и экономика — покластерно."
        ),
    )
    if not use_clusters:
        return []

    st.caption(
        "Площадь квартала = Σ площадей зон ниже. Диапазон этажности для "
        "подбора — во вкладке «Оптимизация» → «Настройки подбора»."
    )
    default_df = pd.DataFrame([
        {"Зона": "Зона А", "Площадь, м²": round(area_m2 * 0.5), "Этажность": min(floors, 9)},
        {"Зона": "Зона Б", "Площадь, м²": round(area_m2 * 0.5), "Этажность": max(floors, 16)},
    ])
    edited = st.data_editor(
        default_df,
        key="floor_clusters_editor",
        num_rows="dynamic",
        hide_index=True,
        use_container_width=True,
        column_config={
            "Зона": st.column_config.TextColumn("Зона", width="small"),
            "Площадь, м²": st.column_config.NumberColumn(
                "Площадь, м²", min_value=1.0, step=1000.0, format="%.0f",
            ),
            "Этажность": st.column_config.NumberColumn(
                "Этажность", min_value=1, max_value=60, step=1, format="%d",
                help="Принятая этажность зоны.",
            ),
        },
    )

    clusters: list[FloorCluster] = []
    for _, row in edited.iterrows():
        try:
            a = float(row["Площадь, м²"])
            f = int(row["Этажность"])
        except (TypeError, ValueError):
            continue
        if a <= 0 or f < 1:
            continue
        label = str(row.get("Зона") or "").strip() or None
        clusters.append(FloorCluster(area_m2=a, floors=f, label=label))

    if len(clusters) > _MAX_CLUSTERS:
        st.warning(f"Учтены только первые {_MAX_CLUSTERS} зоны (задано {len(clusters)}).")
        clusters = clusters[:_MAX_CLUSTERS]

    if not clusters:
        st.info("Добавьте хотя бы одну зону с площадью и этажностью.")
        return []

    total_a = sum(c.area_m2 for c in clusters)
    feff = sum(c.area_m2 * c.floors for c in clusters) / total_a if total_a else 0.0
    # v0.10.19: компактная строка вместо крупных st.metric (выбивались
    # из оформления формы).
    _ta = f"{total_a:,.0f}".replace(",", " ")
    st.caption(
        f"Σ площадей зон (= площадь квартала): **{_ta} м²**  ·  "
        f"средневзвеш. этажность: **{feff:.1f}**"
    )
    return clusters


def _render_vpp_tile() -> VppRequest:
    """Плитка настроек ВПП с 5 вариантами размещения (v0.7.1).

    Возвращает VppRequest. Список ВПП собирается в state.run_calculation
    через двухпроходный расчёт (нужны footprint и population).
    """
    with st.container(border=True):
        _tile_header(":material/storefront: Встроенно-пристроенные помещения (ВПП)", "include_vpp")
        st.caption(
            "Обязательные ВПП по НГП СПб: 4.4 торговля, 4.6 общепит, "
            "3.3 быт.обсл. "
            "Выберите вариант размещения:"
        )

        VPP_MODES = {
            "min_only": "Минимум по нормативу (все 5 ВРИ)",
            "min_plus": "Минимум + дополнительные 4.4/4.6",
            "custom_only": "Только 4.4 и/или 4.6 вручную",
            "full_floor": "Весь 1 этаж (min + остаток между 4.4 и 4.6)",
            "half_floor": "50% 1 этажа (min + остаток между 4.4 и 4.6)",
        }
        mode_label = st.radio(
            "Вариант размещения",
            list(VPP_MODES.values()),
            index=4,  # v0.8.0: «50% 1 этажа» по умолчанию
            key="vpp_mode_label",
        )
        # Обратный mapping label → ключ
        mode: VppMode = next(k for k, v in VPP_MODES.items() if v == mode_label)

        # Поля для custom 4.4 / 4.6 — только для min_plus и custom_only
        custom_44 = None
        custom_46 = None
        if mode in ("min_plus", "custom_only"):
            c1, c2 = st.columns(2)
            with c1:
                use_44 = st.checkbox(
                    "4.4 — торговля", value=(mode == "min_plus"),
                    key="vpp_custom_use_44",
                )
                if use_44:
                    custom_44 = float(st.number_input(
                        "Площадь 4.4, м²",
                        min_value=10.0, max_value=50_000.0,
                        value=500.0, step=100.0,
                        key="vpp_custom_44_m2",
                    ))
            with c2:
                use_46 = st.checkbox(
                    "4.6 — общепит", value=(mode == "min_plus"),
                    key="vpp_custom_use_46",
                )
                if use_46:
                    custom_46 = float(st.number_input(
                        "Площадь 4.6, м²",
                        min_value=10.0, max_value=50_000.0,
                        value=300.0, step=100.0,
                        key="vpp_custom_46_m2",
                    ))

        # Превью обязательных площадей от последней рассчитанной population
        last_pop = st.session_state.get("_last_population", None)
        if last_pop and last_pop > 0:
            with st.expander("ℹ Обязательная программа НГП (превью)", expanded=False):
                from urban_model.calculations import vpp as _vpp
                from urban_model.normatives import load_normatives
                _spb = load_normatives("spb")
                m = _vpp.compute_mandatory_areas(last_pop, _spb)
                st.markdown(
                    f"**Население:** ~{last_pop:.0f} чел  \n"
                    f"• 4.4 торговля: {m.shopping_4_4:.0f} м²  \n"
                    f"• 4.6 общепит: {m.catering_4_6:.0f} м²  \n"
                    f"• 3.3 быт.обсл.: {m.domestic_3_3:.0f} м²  \n"
                    f"**Итого min:** {m.total:.0f} м²  \n"
                    f"_(доп. образование 3.5.1 и поликлиника 3.4.1 — отдельные "
                    f"карточки в «Объектах по НГП»)_"
                )

    return VppRequest(mode=mode, custom_4_4_m2=custom_44, custom_4_6_m2=custom_46)


def _render_znop_tile() -> tuple[float | None, float | None, bool]:
    """Плитка настроек ЗНОП (без внешнего чекбокса «Учитывать»).

    Возвращает (znop_per_person_override, znop_total_area_override, only_demand).
    """
    with st.container(border=True):
        _tile_header(":material/park: ЗНОП — зелёные насаждения общего пользования", "include_znop")
        only_demand = _only_demand_toggle(
            "Только рассчитать потребность",
            key="znop_only_demand",
            help_text=(
                "Показать площадь ЗНОП, но НЕ учитывать в балансе квартала "
                "и в нормативе озеленения. Полезно, если ЗНОП размещается "
                "за пределами квартала."
            ),
        )
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
            return None, None, only_demand
        if znop_mode.startswith("Задать вручную: м²/чел"):
            znop_pp = st.number_input(
                "ЗНОП, м²/чел",
                min_value=0.0, max_value=20.0,
                value=6.0, step=0.5,
                key="znop_value_pp",
            )
            return float(znop_pp), None, only_demand
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
        return None, float(znop_total), only_demand


def _render_kg_tile() -> KindergartenSpec:
    """Плитка настроек ДОО (без внешнего чекбокса «Учитывать»).

    Управляется снаружи: вызывается только если include_kg=True.
    """
    with st.container(border=True):
        _tile_header(":material/child_care: Дошкольные образовательные организации (ДОО)", "include_kg")
        kg_only_demand = _only_demand_toggle(
            "Только рассчитать потребность",
            key="kg_only_demand",
            help_text=(
                "Показать число мест и площади объектов, но НЕ учитывать ЗУ "
                "и здание ДОО в балансе квартала. Полезно, если ДОО размещается "
                "за пределами квартала или уже существует."
            ),
        )
        kg_in_vpp = st.toggle(
            "Разместить в ВПП (встроенно-пристроенное)",
            value=False, key="kg_in_vpp",
            help=(
                "Выкл — отдельно стоящее ДОО (160–350 мест, РМД). "
                "Вкл — встроенно-пристроенное (до 120 мест на 1-м этаже жилого "
                "дома, ЗУ 24 м²/место; здание вычитается из жилой GFA)."
            ),
        )
        kg_btype = "built_in" if kg_in_vpp else "detached"
        kg_strict = st.toggle(
            "Только нормативная наполняемость",
            value=False, key="kg_strict_capacity",
            help=(
                "Вместимости корпусов ДОО выбираются ТОЛЬКО из типового списка "
                "(СП / письмо КОБр): 90, 100, …, 350. Иначе — свободная "
                "разбивка кратно 5."
            ),
        )
        # v0.11.0: «Задать число вручную» = только КОЛИЧЕСТВО объектов;
        # наполняемость система считает сама (потребность / число). Вложенная
        # галочка «Задать наполняемость» — задать вместимость вручную.
        kg_override = st.checkbox(
            "Задать число ДОО вручную",
            value=False, key="kg_override",
        )
        kg_num_objects, kg_capacity = None, None
        if kg_override:
            kg_num_objects = st.number_input(
                "Кол-во ДОО", min_value=1, max_value=20, value=2, step=1,
                key="kg_num_objects",
                help="Наполняемость каждого ДОО система рассчитает: "
                     "потребность ÷ число объектов.",
            )
            kg_set_cap = st.checkbox(
                "Задать наполняемость вручную", value=False, key="kg_set_cap",
            )
            if kg_set_cap:
                kg_capacity = st.number_input(
                    "Мест в каждом",
                    min_value=90, max_value=350, value=160, step=5,
                    help=(
                        "Типовые вместимости КС: 90, 100, 110, 120, 140, 150, 160, "
                        "165, 170, 180, 190, 200, 215, 220, 230, 240, 250, 260, "
                        "280, 310, 320, 340, 350. Иное → предупреждение."
                    ),
                    key="kg_capacity",
                )
    return KindergartenSpec(
        building_type=kg_btype,
        num_objects=int(kg_num_objects) if kg_num_objects else None,
        capacity_per_object=int(kg_capacity) if kg_capacity else None,
        only_demand=bool(kg_only_demand),
        strict_capacity=bool(kg_strict),
    )


def _render_add_education_tile() -> AdditionalEducationSpec:
    """Плитка организаций доп. образования (ВРИ 3.5.1, v0.12.15)."""
    with st.container(border=True):
        _tile_header(
            ":material/palette: Организации доп. образования",
            "include_add_education",
        )
        ae_only_demand = _only_demand_toggle(
            "Только рассчитать потребность",
            key="ae_only_demand",
            help_text=(
                "Показать число мест и площади, но НЕ учитывать ЗУ/здание "
                "в балансе квартала. Полезно, если объект размещается за "
                "пределами квартала или уже существует."
            ),
        )
        ae_in_vpp = st.toggle(
            "Разместить в ВПП (встроенно-пристроенное)",
            value=False, key="ae_in_vpp",
            help=(
                "Встроить в жилой дом (ЗУ не выделяется). При > 150 мест объект "
                "делится на несколько встроенных. Выкл — по нормативу: < 150 "
                "встроенное, ≥ 150 отдельно стоящее."
            ),
        )
        # «Задать вручную» — как у ДОУ/СОШ: квадратный чекбокс + поле ниже.
        ae_manual = st.checkbox(
            "Задать число мест вручную", value=False, key="ae_add_manual",
        )
        ae_places_override = None
        if ae_manual:
            ae_places_override = int(st.number_input(
                "Число мест", min_value=0, max_value=5000, value=150, step=5,
                key="ae_places_override",
            ))
    return AdditionalEducationSpec(
        mode="manual" if ae_manual else "norm",
        places_override=ae_places_override,
        in_vpp=bool(ae_in_vpp),
        only_demand=bool(ae_only_demand),
    )


def _render_polyclinic_tile() -> PolyclinicSpec:
    """Плитка амбулаторно-поликлинических учреждений (ВРИ 3.4.1, v0.12.28)."""
    with st.container(border=True):
        _tile_header(":material/local_hospital: Поликлиника", "include_polyclinic")
        poly_only_demand = _only_demand_toggle(
            "Только рассчитать потребность",
            key="poly_only_demand",
            help_text=(
                "Показать число посещений и площади, но НЕ учитывать ЗУ/здание "
                "в балансе квартала (объект вне квартала / уже существует)."
            ),
        )
        poly_in_vpp = st.toggle(
            "Разместить в ВПП (офис врача общей практики)",
            value=False, key="poly_in_vpp",
            help=(
                "Встроить в жилой дом (ЗУ не выделяется). 1 объект ≤ 100 "
                "посещений → при большем числе делится на несколько офисов. "
                "Выкл — по нормативу: < 150 посещений встроенное, ≥ 150 "
                "отдельно стоящая поликлиника."
            ),
        )
        poly_manual = st.checkbox(
            "Задать число посещений вручную", value=False, key="poly_manual",
        )
        poly_visits_override = None
        if poly_manual:
            poly_visits_override = int(st.number_input(
                "Посещений в смену", min_value=0, max_value=5000, value=150, step=1,
                key="poly_visits_override",
            ))
    return PolyclinicSpec(
        mode="manual" if poly_manual else "norm",
        visits_override=poly_visits_override,
        in_vpp=bool(poly_in_vpp),
        only_demand=bool(poly_only_demand),
    )


def _render_school_tile() -> SchoolSpec:
    """Плитка настроек СОШ (без внешнего чекбокса «Учитывать»)."""
    with st.container(border=True):
        _tile_header(":material/school: Средние общеобразовательные школы (СОШ)", "include_school")
        sch_only_demand = _only_demand_toggle(
            "Только рассчитать потребность",
            key="sch_only_demand",
            help_text=(
                "Показать число мест и площадь СОШ, но НЕ учитывать ЗУ "
                "в балансе квартала. Полезно, если СОШ размещается "
                "за пределами квартала или уже существует."
            ),
        )
        # Вертикально, чтобы вторая галочка не терялась в узкой правой колонке
        school_pool = st.checkbox(
            "С бассейном (+0.2 га)", value=True, key="school_pool",
        )
        school_sport = st.checkbox(
            "Со спортивным ядром (+0.7 га)", value=True, key="school_sport",
        )
        sch_strict = st.toggle(
            "Только нормативная наполняемость",
            value=False, key="sch_strict_capacity",
            help=(
                "Вместимости корпусов СОШ выбираются ТОЛЬКО из типового списка "
                "(СП / письмо КОБр): 550, 825, 1100, 1375, 1650, 1925, 2200, "
                "2475. Иначе — свободная разбивка кратно 25."
            ),
        )
        # v0.11.0: «Задать число вручную» = только КОЛИЧЕСТВО объектов;
        # наполняемость система считает сама. Вложенная галочка — вручную.
        sch_override = st.checkbox(
            "Задать число СОШ вручную", value=False, key="sch_override",
        )
        sch_num_objects, sch_capacity = None, None
        if sch_override:
            sch_num_objects = st.number_input(
                "Кол-во СОШ", min_value=1, max_value=10, value=1, step=1,
                key="sch_num_objects",
                help="Наполняемость каждой СОШ система рассчитает: "
                     "потребность ÷ число объектов.",
            )
            sch_set_cap = st.checkbox(
                "Задать наполняемость вручную", value=False, key="sch_set_cap",
            )
            if sch_set_cap:
                sch_capacity = st.number_input(
                    "Мест в каждой",
                    min_value=550, max_value=2475, value=550, step=25,
                    help=(
                        "Типовые параллели КС: 550, 825, 1100, 1375, 1650, 1925, "
                        "2200, 2475. Иное → предупреждение."
                    ),
                    key="sch_capacity",
                )
    return SchoolSpec(
        has_pool=school_pool, has_sport_core=school_sport,
        num_objects=int(sch_num_objects) if sch_num_objects else None,
        capacity_per_object=int(sch_capacity) if sch_capacity else None,
        only_demand=bool(sch_only_demand),
        strict_capacity=bool(sch_strict),
    )


def _render_parking_tile() -> ParkingConfig:
    """Парковки — пресеты + расширенный custom с типами и count×capacity."""
    with st.container(border=True):
        _tile_header(":material/local_parking: Парковки", "include_parking")
        PARK_MODE_LABELS = {
            "Минимум открытых, остальное подземные (по умолчанию)": "min_open",
            "Все парковки открытые наземные": "all_open",
            "50/50: открытые + многоуровневые": "preset_50_50",
            "Задать вручную": "custom",
        }
        park_label = st.radio(
            "Размещение машино-мест",
            list(PARK_MODE_LABELS.keys()),
            index=2,  # v0.10.19: по умолчанию «50/50: открытые + многоуровневые»
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
            ml_levels = st.slider(
                "Этажность многоуровневого паркинга",
                min_value=1, max_value=9, value=5, step=1,
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

# v0.12.9: стилобат — полноценный 4-й тип в общей сумме = 100%.
_ALL_TYPES = ["open", "ml", "ug", "styl"]
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
    # v0.10.19: дефолт custom — 50% открытые + 50% многоуровневые
    # (подземные выключены), как и пресет «50/50».
    defaults = {
        "park_open_pct": 50.0,
        "park_ml_pct": 50.0,
        "park_ug_pct": 0.0,
        "park_styl_pct": 0.0,
        "park_open_locked": False,
        "park_ml_locked": False,
        "park_ug_locked": False,
        "park_styl_locked": False,
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

    # v0.12.26: замок-чекбокс с лейблом «Доля, %» ВМЕСТО отдельного
    # «Зафиксировать долю» — смысл замка = фиксация процента, карточка
    # поджимается (одна строка вместо двух). Слайдер ниже без своего лейбла.
    lock_label = (
        ":material/lock: Доля в остатке, %"
        if is_remainder_mode else ":material/lock: Доля, %"
    )
    if not interactive:
        # disabled/metric: компактный лейбл + значение (без чекбокса).
        st.markdown(
            f"<div style='padding:0.25rem 0 0.5rem 0;line-height:1.3;'>"
            f"<span style='color:#6B7280;font-size:0.85rem;'>{slider_label}</span><br>"
            f"<span style='font-weight:500;font-size:1rem;'>"
            f"{st.session_state[pct_key]:.1f}%</span>"
            f"</div>",
            unsafe_allow_html=True,
        )
    else:
        st.checkbox(
            lock_label, key=lock_key,
            help="Зафиксировать эту долю — при изменении других типов парковок "
                 "она пересчитываться не будет.",
        )
        st.slider(
            slider_label,
            min_value=0.0, max_value=100.0, step=0.5,
            key=pct_key,
            disabled=is_locked,
            label_visibility="collapsed",
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
        "Выберите типы парковок и доли. Замок рядом со слайдером "
        "фиксирует долю — двигаться будут только остальные. "
        "Сумма открытые + многоуровневые + подземные = 100% "
        "(стилобат — отдельная доля)."
    )

    # v0.10.18: чекбоксы списком (раньше в 3 колонках текст переносился
    # и обрезался — «Открыт ые назем ные»).
    use_open = st.checkbox("Открытые наземные", value=True, key=_USE_KEY["open"])
    use_ml = st.checkbox("Многоуровневые наземные", value=True, key=_USE_KEY["ml"])
    use_ug = st.checkbox("Подземные", value=False, key=_USE_KEY["ug"])
    # v0.12.2: стилобат — ОТДЕЛЬНОЕ измерение (доля жилищной парковки), не
    # участвует в нормировке open+ml+ug=100%. Слайдер — в своей карточке ниже
    # (как у остальных типов), здесь только чекбокс.
    use_styl = st.checkbox("Стилобатные (поднятая дека)", value=False, key="park_use_styl")

    if not (use_open or use_ml or use_ug or use_styl):
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
    ml_levels = 5

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
            ml_levels = st.slider(
                "Этажность многоуровневого паркинга",
                min_value=1, max_value=9, value=5, step=1,
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

    # === Стилобатные (v0.12.9: полноценный 4-й тип, в общей сумме 100%) ===
    if use_styl:
        with st.container(border=True):
            st.markdown("**Стилобатные (поднятая дека)**")
            _render_share_slider(
                "styl", "Стилобатные",
                interactive=can_redistribute or _is_type_locked("styl"),
                is_remainder_mode=ml_use_explicit,
            )
            st.caption(
                "Поднятый паркинг (35 м²/м.м), не занимает ЗУ квартала. "
                "25% деки под домами снимает 1 этаж жилья там, остальное — "
                "поднятый двор (озеленение ≤70% по ПЗЗ)."
            )

    # === Сборка ParkingConfig ===
    open_pct = st.session_state.park_open_pct if use_open else 0.0
    ml_pct = (
        st.session_state.park_ml_pct
        if (use_ml and not ml_use_explicit) else 0.0
    )
    ug_pct = st.session_state.park_ug_pct if use_ug else 0.0
    styl_pct_v = st.session_state.park_styl_pct if use_styl else 0.0

    if ml_use_explicit:
        # multilevel — абсолютным числом. open, ug, стилобат делят остаток.
        sum_rest = max(open_pct + ug_pct + styl_pct_v, 0.01)
        open_share = open_pct / sum_rest
        ug_share = ug_pct / sum_rest
        styl_share = styl_pct_v / sum_rest
        ml_share = 0.0
    else:
        total = max(open_pct + ml_pct + ug_pct + styl_pct_v, 0.01)
        open_share = open_pct / total
        ml_share = ml_pct / total
        ug_share = ug_pct / total
        styl_share = styl_pct_v / total

    try:
        return ParkingConfig(
            mode="custom",
            open_share=open_share,
            multilevel_share=ml_share,
            underground_share=ug_share,
            multilevel_levels=int(ml_levels),
            multilevel_explicit_places=ml_explicit_places,
            stylobate_share=styl_share,
        )
    except Exception as e:
        st.error(f"Некорректная конфигурация парковок: {e}")
        return ParkingConfig(mode="min_open")


def _render_economy_tile() -> tuple[str, str, float | None]:
    """Плитка экономических параметров (v0.8.0).

    Возвращает (residential_class, social_funding, social_compensation_share).
    """
    with st.container(border=True):
        _tile_header(":material/payments: Экономика (условные единицы)", "include_economy")
        _CLASS_RU = {"economy": "Эконом", "comfort": "Комфорт", "business": "Бизнес"}
        cls_label = st.selectbox(
            "Класс жилья",
            ["economy", "comfort", "business"],
            index=0,  # Эконом по умолчанию
            format_func=lambda v: _CLASS_RU.get(v, v),
            key="residential_class",
            help=(
                "Влияет на цену продажи м² квартир и себестоимость отделки.\n\n"
                "За основу принято: монолитный каркас, типовая («стандарт») "
                "отделка, цены и себестоимость СПб. Результат — в безразмерных "
                "баллах (1 балл ≈ м² жилья 9-этажного монолита), для сравнения "
                "вариантов между собой, а не как смета в рублях."
            ),
        )
        # v0.12.27: за чей счёт соцобъекты (ДОО/СОШ/доп.обр здания).
        _FUND_RU = {
            "compensated": "Город компенсирует %",
            "developer": "Полностью застройщик",
            "city": "Полностью город",
            "at_cost": "Передача по себестоимости",
        }
        fund = st.selectbox(
            "Соцобъекты — за чей счёт",
            list(_FUND_RU.keys()), index=0,
            format_func=lambda v: _FUND_RU[v],
            key="social_funding",
            help=(
                "Как учитывать ДОО/СОШ/доп.образование в экономике: "
                "город компенсирует долю себестоимости / целиком застройщик / "
                "целиком город (себест. соц = 0) / передача по себестоимости (нейтрально)."
            ),
        )
        comp_share: float | None = None
        if fund == "compensated":
            comp_share = st.slider(
                "Доля компенсации города, %", 0, 100, 70, 5,
                key="social_comp_share_pct",
                help="Какую долю себестоимости соц-зданий компенсирует город.",
            ) / 100.0
    return cls_label, fund, comp_share


def _render_custom_objects_tile() -> list[CustomObject]:
    """Табличный редактор для дополнительных объектов на территории."""
    with st.container(border=True):
        _tile_header(":material/inventory_2: Дополнительные объекты на территории квартала", "include_custom_objects")
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
                    required=False,
                    help="Необязательно. Если не указан — берётся 4.0 "
                         "(предпринимательство, коммерческий объект).",
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
            if st.button("Применить", type="primary"):
                new_list = []
                for _, row in edited_df.iterrows():
                    try:
                        name = str(row["Название"]).strip()
                        plot = float(row["Площадь ЗУ, м²"])
                        # v0.9.29: ВРИ необязателен — по умолчанию 4.0 (коммерция).
                        vri_raw = row.get("ВРИ-код")
                        vri = str(vri_raw).strip() if pd.notna(vri_raw) and str(vri_raw).strip() else "4.0"
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
            if st.button("Очистить",
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
