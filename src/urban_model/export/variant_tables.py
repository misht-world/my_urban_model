"""Единый источник ДАННЫХ таблиц варианта (v0.13.0).

Одни и те же строки таблиц потребляют И вкладка «Расчёт» (Streamlit-рендер в
`ui/output.py`), И альбом концепции (PPTX в `export/album/concept.py`). За счёт
общего билдера таблицы Базы и вариантов ГАРАНТИРОВАННО идентичны — не «похожи»,
а бит-в-бит одни и те же значения.

Все данные берутся из `TEPResult` (options не нужны: лейблы размещения читают
флаги `*_built_in` прямо из result). Экономика и полный аудит сюда НЕ входят.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from urban_model.models.result import TEPField, TEPResult
from urban_model.ui.formatting import (
    STATUS_LABEL_RU,
    fmt_float,
    fmt_ga,
    fmt_int,
    fmt_m2,
)


@dataclass
class TableBlock:
    """Одна секция-таблица варианта (= один expander на «Расчёте» / слайд в альбоме)."""

    key: str
    title: str
    icon: str                       # имя Material Symbols (для «Расчёта»)
    rows: list[dict]
    columns: list[str] | None = None  # None → key-value (Показатель/Значение); иначе grid
    notes: list[str] = field(default_factory=list)
    # Итог раздела одной строкой. Используется в альбоме концепции (после
    # таблицы); на «Расчёте» не выводится.
    summary: str | None = None
    # Подмножество колонок для АЛЬБОМА (PPTX-слайд уже 10-колоночную таблицу
    # не вмещает — текст переносится и налезает на подписи). None → columns.
    # На «Расчёте» (st.dataframe) всегда полный набор columns.
    album_columns: list[str] | None = None


# ---------------------------------------------------------------------------
# Хелперы строк (перенесены из ui/output.py — единый источник)
# ---------------------------------------------------------------------------

def _row(label: str, field_: TEPField, fmt_fn=fmt_int, suffix: str = "") -> dict:
    if field_.value is None:
        val_str = "—"
    elif callable(fmt_fn):
        val_str = fmt_fn(field_.value)
    else:
        val_str = str(field_.value)
    return {
        "Показатель": label,
        "Значение": f"{val_str}{suffix}",
        "Статус": STATUS_LABEL_RU.get(field_.status, field_.status.value),
        "Источник": field_.source or "",
        "Формула": field_.formula or "",
    }


def _text_row(label: str, text: str) -> dict:
    return {"Показатель": label, "Значение": text,
            "Статус": "", "Источник": "", "Формула": ""}


def parse_object_buckets(formula: str | None) -> tuple[int, list[int]]:
    """Из formula соцобъекта → (число объектов, вместимости). «… [160, 160]»."""
    if not formula:
        return 0, []
    m = re.search(r"\[([\d,\s]+)\]", formula)
    if not m:
        return 0, []
    caps = [int(x) for x in m.group(1).split(",") if x.strip()]
    return len(caps), caps


def format_buckets(n: int, caps: list[int]) -> str:
    """«2 объекта (по 160 мест)» / «2 объекта (160 + 240 мест)»."""
    if n <= 0:
        return "—"
    word = "объект" if n == 1 else ("объекта" if 2 <= n <= 4 else "объектов")
    if len(set(caps)) == 1:
        return f"{n} {word} (по {caps[0]} мест)" if n > 1 else f"{n} {word} ({caps[0]} мест)"
    return f"{n} {word} ({' + '.join(str(c) for c in caps)} мест)"


def ae_obj_count(formula: str | None) -> int:
    """Число объектов доп.обр/поликлиники из formula-строки (ВПП-дробление)."""
    if not formula:
        return 1
    m = re.search(r"(\d+)\s*(?:об\.|объект|офис)", formula)
    return int(m.group(1)) if m else 1


# ---------------------------------------------------------------------------
# Билдер блоков — ЗЕРКАЛО render_details (ui/output.py)
# ---------------------------------------------------------------------------

def build_variant_table_blocks(result: TEPResult) -> list[TableBlock]:
    """Список секций-таблиц варианта (Жильё…Баланс). Без Экономики/Аудита."""
    blocks: list[TableBlock] = []

    # 🏠 Жильё
    rows = [
        _row("КИТ ПЗЗ (площадь квартир / ЗУ жилой застройки)", result.kit, fmt_float),
        _row("Плотность (по СП 42.13330, для 20 м²/чел)",
             result.density_chel_per_ga, lambda x: f"{x:.1f}", " чел./га"),
        _row("Население", result.population, fmt_int, " чел."),
        _row("Общая площадь жилых зданий (GFA)", result.gfa, fmt_m2),
        _row("Площадь квартир", result.apartments_area, fmt_m2),
    ]
    if result.built_in_area.value and result.built_in_area.value > 0:
        rows.append(_row(
            f"Встроенно-пристроенные помещения (ВПП, ВРИ {result.built_in_vri_code})",
            result.built_in_area, fmt_m2))
        rows.append(_row("Парковки ВПП", result.built_in_parking_places, fmt_int, " м/м"))
        rows.append(_row("Озеленение ВПП", result.built_in_greening_area, fmt_m2))
    rows += [
        _row("Площадь застройки", result.housing_footprint, fmt_m2),
        _row("ЗУ жилой застройки", result.housing_lot_area, fmt_m2),
        _row("Плотность квартала (внутренняя, GFA / площадь квартала)",
             result.block_density, fmt_float),
    ]
    _sum_housing = (
        f"КИТ {result.kit.value:.3f} (потолок {result.kit_normative_max.value or 0}); "
        f"население {fmt_int(result.population.value)} чел.; площадь квартир "
        f"{fmt_m2(result.apartments_area.value)}; плотность "
        f"{result.density_chel_per_ga.value:.0f} чел./га."
    )
    blocks.append(TableBlock("housing", "Жильё", "home", rows, summary=_sum_housing))

    # 🎒 ДОО
    rows = [
        _row("Мест требуется", result.kindergarten_places_required, fmt_float),
        _row("Мест принято (округлено)", result.kindergarten_places_accepted, fmt_int),
    ]
    _kg_n, _kg_caps = parse_object_buckets(result.kindergarten_places_accepted.formula)
    if _kg_n:
        rows.append(_text_row("Принято объектов", format_buckets(_kg_n, _kg_caps)))
    rows += [
        _row("Площадь участков", result.kindergarten_plot_area, fmt_m2),
        _row("Площадь зданий", result.kindergarten_building_area, fmt_m2),
    ]
    _sum_kg = (
        f"{int(result.kindergarten_places_accepted.value or 0)} мест"
        + (f" в {format_buckets(_kg_n, _kg_caps)}" if _kg_n else "")
        + f"; участки {fmt_m2(result.kindergarten_plot_area.value)}."
    )
    # v0.15.3 (по данным КС): корпуса крупнее 250 мест затрудняют выкуп городом.
    _kg_notes = []
    if _kg_caps and max(_kg_caps) > 250:
        _kg_notes.append(
            "Наполняемость ДОУ более 250 мест не рекомендована КС "
            "в случае выкупа."
        )
    blocks.append(TableBlock("kindergarten", "ДОО (детские сады)", "child_care",
                             rows, notes=_kg_notes, summary=_sum_kg))

    # 🏫 СОШ
    rows = [
        _row("Мест требуется", result.school_places_required, fmt_float),
        _row("Мест принято (округлено)", result.school_places_accepted, fmt_int),
    ]
    _sch_n, _sch_caps = parse_object_buckets(result.school_places_accepted.formula)
    if _sch_n:
        rows.append(_text_row("Принято объектов", format_buckets(_sch_n, _sch_caps)))
    rows += [
        _row("Площадь участков", result.school_plot_area, fmt_m2),
        _row("Площадь зданий", result.school_building_area, fmt_m2),
    ]
    _sum_sch = (
        f"{int(result.school_places_accepted.value or 0)} мест"
        + (f" в {format_buckets(_sch_n, _sch_caps)}" if _sch_n else "")
        + f"; участки {fmt_m2(result.school_plot_area.value)}."
    )
    blocks.append(TableBlock("school", "СОШ (школы)", "school", rows, summary=_sum_sch))

    # 🎨 Доп. образование
    if (result.add_education_places_accepted.value or 0) > 0:
        _ae_built_in = bool(getattr(result, "add_education_built_in", False))
        _ae_label = "встроенное (ВПП)" if _ae_built_in else "отдельно стоящее"
        _ae_n = ae_obj_count(result.add_education_places_accepted.formula)
        _ae_place_str = _ae_label + (f", {_ae_n} объект(ов)" if _ae_n > 1 else "")
        rows = [
            _text_row("Размещение", _ae_place_str),
            _row("Мест требуется", result.add_education_places_required, fmt_float),
            _row("Мест принято", result.add_education_places_accepted, fmt_int),
            _row("Площадь здания (17 м²/место)", result.add_education_building_area, fmt_m2),
        ]
        if not _ae_built_in:
            rows.append(_row("Площадь ЗУ (15 м²/место)", result.add_education_plot_area, fmt_m2))
        _ae_park_lbl = ("Парковка — открытая на ЗУ жилья" if _ae_built_in
                        else "Парковка — на своём ЗУ (как ДОУ/СОШ)")
        rows.append(_row(_ae_park_lbl, result.add_education_parking_places, fmt_int))
        _sum_ae = (
            f"{int(result.add_education_places_accepted.value or 0)} мест, "
            f"{_ae_place_str}."
        )
        blocks.append(TableBlock(
            "add_education", f"Организации доп. образования — {_ae_label}", "palette",
            rows, summary=_sum_ae))

    # 🏥 Поликлиника
    if (result.polyclinic_visits_accepted.value or 0) > 0:
        _poly_bi = bool(getattr(result, "polyclinic_built_in", False))
        _poly_label = "ВПП (офис врача)" if _poly_bi else "отдельно стоящая"
        _poly_n = ae_obj_count(result.polyclinic_visits_accepted.formula)
        _poly_place_str = _poly_label + (f", {_poly_n} объект(ов)" if _poly_n > 1 else "")
        rows = [
            _text_row("Размещение", _poly_place_str),
            _row("Посещений требуется", result.polyclinic_visits_required, fmt_float),
            _row("Посещений принято", result.polyclinic_visits_accepted, fmt_int),
            _row(f"Площадь здания ({12 if _poly_bi else 23} м²/посещ.)",
                 result.polyclinic_building_area, fmt_m2),
        ]
        if not _poly_bi:
            rows.append(_row("Площадь ЗУ (10 м²/посещ., мин. 2000)",
                             result.polyclinic_plot_area, fmt_m2))
        _poly_park_lbl = ("Парковка — открытая на ЗУ жилья" if _poly_bi
                          else "Парковка — на ЗУ поликлиники")
        rows.append(_row(_poly_park_lbl, result.polyclinic_parking_places, fmt_int))
        _sum_poly = (
            f"{int(result.polyclinic_visits_accepted.value or 0)} посещений/смену, "
            f"{_poly_place_str}."
        )
        blocks.append(TableBlock(
            "polyclinic", f"Поликлиника — {_poly_label}", "local_hospital",
            rows, summary=_sum_poly))

    # Спорт
    if (result.sport_facilities_plot_area.value or 0) > 0:
        rows = [
            _row("Площадь сооружений", result.sport_facilities_area, fmt_m2),
            _row("Озеленение требуется (40%)", result.sport_facilities_greening_required, fmt_m2),
            _row("Доп. озеленение на ЗУ (после substitution)",
                 result.sport_facilities_greening_extra, fmt_m2),
            _row("Полный ЗУ (sport + доп. озеленение)", result.sport_facilities_plot_area, fmt_m2),
        ]
        _sum_sport = (
            f"Спортплощадки {fmt_m2(result.sport_facilities_area.value)}; "
            f"полный ЗУ {fmt_m2(result.sport_facilities_plot_area.value)}."
        )
        blocks.append(TableBlock(
            "sport", "Плоскостные спортивные сооружения", "directions_run",
            rows, summary=_sum_sport))

    # 🌳 ЗНОП и озеленение
    rows = [
        _row("ЗНОП на человека", result.znop_per_person, fmt_float, " м²/чел"),
        _row("Площадь ЗНОП", result.znop_area, fmt_m2),
        _row("Озеленение жилья", result.greening_housing_area, fmt_m2),
        _row("Минимум озеленения квартала (норматив)", result.greening_quarter_required, fmt_m2),
    ]
    _sum_znop = (
        f"ЗНОП {result.znop_per_person.value or 0:.1f} м²/чел, всего "
        f"{fmt_m2(result.znop_area.value)}; норматив озеленения квартала "
        f"{fmt_m2(result.greening_quarter_required.value)}."
    )
    blocks.append(TableBlock("znop", "ЗНОП и озеленение", "park", rows, summary=_sum_znop))

    # 🅿️ Парковки
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
    if (getattr(result, "parking_stylobate_places", None)
            and (result.parking_stylobate_places.value or 0) > 0):
        rows += [
            _row("Стилобатные м/м", result.parking_stylobate_places, fmt_int),
            _row("Площадь деки стилобата", result.parking_stylobate_area, fmt_m2),
        ]
    if (result.social_parking_total.value or 0) > 0:
        rows += [
            _row("СОЦ: всего м/м (отдельные открытые на ЗУ)", result.social_parking_total, fmt_int),
            _row("В т.ч. ДОО", result.social_parking_kindergarten, fmt_int),
            _row("В т.ч. СОШ", result.social_parking_school, fmt_int),
        ]
        if (result.add_education_parking_places.value or 0) > 0:
            rows.append(_row("В т.ч. доп. образование",
                             result.add_education_parking_places, fmt_int))
        if (result.polyclinic_parking_places.value or 0) > 0:
            rows.append(_row("В т.ч. поликлиника",
                             result.polyclinic_parking_places, fmt_int))
        rows.append(_row("Площадь парковок соцобъектов на квартале",
                         result.social_parking_area, fmt_m2))
    _pp = []
    if (result.parking_open_places.value or 0) > 0:
        _pp.append(f"открытых {int(result.parking_open_places.value)}")
    if (result.parking_multilevel_places.value or 0) > 0:
        _pp.append(f"многоур. {int(result.parking_multilevel_places.value)}")
    if (getattr(result, 'parking_stylobate_places', None)
            and (result.parking_stylobate_places.value or 0) > 0):
        _pp.append(f"стилоб. {int(result.parking_stylobate_places.value)}")
    if (result.parking_underground_places.value or 0) > 0:
        _pp.append(f"подземных {int(result.parking_underground_places.value)}")
    _sum_park = (
        f"Всего {int(result.parking_required_places.value or 0)} м/м"
        + (" (" + ", ".join(_pp) + ")" if _pp else "") + "."
    )
    blocks.append(TableBlock("parking", "Парковки", "local_parking", rows, summary=_sum_park))

    # Проезды (формулы/источники скрыты)
    rows = [
        _row("Внутриквартальные", result.driveways_intra_quarter_area, fmt_m2),
        _row("На ЗУ жилой застройки", result.driveways_housing_lot_area, fmt_m2),
    ]
    for r in rows:
        r["Формула"] = ""
        r["Источник"] = ""
    _sum_dw = (
        f"Внутриквартальные {fmt_m2(result.driveways_intra_quarter_area.value)}, "
        f"на ЗУ жилья {fmt_m2(result.driveways_housing_lot_area.value)}."
    )
    blocks.append(TableBlock("driveways", "Проезды", "route", rows, summary=_sum_dw))

    # 🔌 Инженерная инфраструктура (grid)
    if result.engineering is not None and result.engineering.objects:
        eng = result.engineering
        # v0.15.9: метки «— лот N» на общем листе не показываем (лоты видны на
        # своём листе «Лоты застройки»); одинаковые объекты разных лотов
        # (метка+мощность+ЗУ/шт) агрегируются в одну строку.
        _agg: dict[tuple, dict] = {}
        for o in eng.objects:
            if o.count <= 0:
                continue
            lbl = o.label.split(" — лот")[0]
            cap = (f"{o.capacity:g} {o.capacity_unit}"
                   if o.capacity and o.capacity_unit else "—")
            key = (lbl, cap, round(o.plot_each, 1), o.in_balance)
            a = _agg.setdefault(key, {"count": 0, "plot_total": 0.0})
            a["count"] += o.count
            a["plot_total"] += o.plot_total
        eng_rows = []
        for (lbl, cap, plot_each, in_bal), a in _agg.items():
            eng_rows.append({
                "Объект": lbl,
                "Кол-во": a["count"],
                "Мощность (1 шт.)": cap,
                "ЗУ (1 шт.), м²": f"{plot_each:,.0f}".replace(",", " "),
                "ЗУ всего, м²": f"{a['plot_total']:,.0f}".replace(",", " "),
                "В балансе": "да" if in_bal else "только потребность",
            })
        cooking_lbl = "электроплиты" if eng.cooking == "electric" else "газовые плиты"
        note = (
            f"Приготовление пищи: {cooking_lbl}. В баланс входит "
            f"{fmt_m2(eng.plot_in_balance)} (всего по объектам {fmt_m2(eng.plot_total_all)}). "
            f"«Только потребность» — объект считается, но ЗУ вне баланса."
        )
        _n_eng = sum(o.count for o in eng.objects if o.count > 0)
        _sum_eng = (
            f"{_n_eng} объектов инж. инфраструктуры; в баланс "
            f"{fmt_m2(eng.plot_in_balance)} (всего {fmt_m2(eng.plot_total_all)})."
        )
        blocks.append(TableBlock(
            "engineering", "Инженерная инфраструктура", "bolt", eng_rows,
            columns=["Объект", "Кол-во", "Мощность (1 шт.)", "ЗУ (1 шт.), м²",
                     "ЗУ всего, м²", "В балансе"],
            notes=[note], summary=_sum_eng))

    # ⚖️ Баланс территории (grid, с зонами)
    blocks.append(_balance_block(result))

    # 🏗 Кластеры этажности (grid)
    if result.floor_clusters_detail:
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
        notes = [
            f"Норматив проверяется по общему КИТ {result.kit.value:.3f} "
            f"(средневзвешенная этажность {result.effective_floors:.1f}). "
            f"КИТ по зонам ниже — справочно: локальная плотность каждой зоны "
            f"(Σ площадей квартир сходится с общей)."
        ]
        kit_norm = result.kit.normative
        max_kit = max((d["kit"] for d in result.floor_clusters_detail), default=0.0)
        if kit_norm and max_kit > kit_norm + 1e-6:
            notes.append(
                f"ℹ Локальный КИТ верхней зоны {max_kit:.3f} выше норматива "
                f"{kit_norm} — на отдельном ЗУ такая зона потребовала бы более "
                f"высокого предела. В общем балансе квартала это компенсируется "
                f"низкими зонами (общий КИТ {result.kit.value:.3f} в норме)."
            )
        _sum_cl = (
            f"{len(result.floor_clusters_detail)} зон(ы); средневзвешенная "
            f"этажность {result.effective_floors:.1f}, общий КИТ {result.kit.value:.3f}."
        )
        blocks.append(TableBlock(
            "clusters", "Кластеры этажности (по зонам)", "apartment", cl_rows,
            columns=["Зона", "Площадь, м²", "Этажей", "КИТ зоны (справ.)",
                     "Площадь квартир, м²", "Пятно застройки, м²"],
            notes=notes, summary=_sum_cl))

    # 🪜 Очерёдность застройки (v0.15.0)
    ph = getattr(result, "phasing", None)
    if ph is not None and ph.stages:
        ph_rows = []
        for s in ph.stages:
            eng = ", ".join(
                f"{lbl.split(' (')[0]}×{cnt}" for lbl, cnt in s.engineering_stage.items()
            ) or "—"
            def _bk(bk):
                return " + ".join(str(b) for b in bk) if bk else "—"
            ph_rows.append({
                "Очередь": s.index,
                "Доля": f"{s.share:.0%}",
                "Площадь, м²": f"{s.area_m2:,.0f}".replace(",", " "),
                "Квартиры, м²": f"{s.apartments_m2:,.0f}".replace(",", " "),
                "Население (накоп.)": f"{s.population_cum:,.0f}".replace(",", " "),
                "ДОО введено/треб.": f"{s.kg_provided_cum}/{s.kg_required_cum:.0f}"
                                     + (f" (+{_bk(s.kg_buckets)})" if s.kg_buckets else ""),
                "СОШ введено/треб.": f"{s.school_provided_cum}/{s.school_required_cum:.0f}"
                                     + (f" (+{_bk(s.school_buckets)})" if s.school_buckets else ""),
                "Парковки, м/м": s.parking_places_stage,
                "Инженерия": eng,
                "Статус": "✓" if s.is_ok else "⚠ дефицит",
            })
        n_def = sum(1 for s in ph.stages if not s.is_ok)
        n_lots = max((s.lot for s in ph.stages), default=1)
        _auto_ph = (" (доли подобраны автоматически по обеспеченности "
                    "соцобъектами)" if getattr(ph, "mode", "") == "auto" else "")
        _lots_ph = f" в {n_lots} лоте(ах)" if n_lots >= 1 else ""
        _sum_ph = (
            f"{len(ph.stages)} очереди(ей){_lots_ph}{_auto_ph}; "
            + ("обеспеченность соцобъектами выдержана на всех этапах."
               if n_def == 0 else f"дефицит соцобъектов на {n_def} этапе(ах).")
        )
        # Инженерия по очередям — строкой в примечания (в альбомной таблице
        # колонка «Инженерия» не помещается).
        _eng_note = "Инженерия по очередям: " + "; ".join(
            f"оч. {s.index} — " + (", ".join(
                f"{lbl.split(' (')[0]}×{cnt}"
                for lbl, cnt in s.engineering_stage.items()) or "—")
            for s in ph.stages
        ) + "."
        blocks.append(TableBlock(
            "phasing", "Очерёдность застройки", "stairs", ph_rows,
            columns=["Очередь", "Доля", "Площадь, м²", "Квартиры, м²",
                     "Население (накоп.)", "ДОО введено/треб.",
                     "СОШ введено/треб.", "Парковки, м/м", "Инженерия", "Статус"],
            album_columns=["Очередь", "Доля", "Квартиры, м²",
                           "Население (накоп.)", "ДОО введено/треб.",
                           "СОШ введено/треб.", "Статус"],
            notes=["Корпуса ДОО/СОШ и объекты инженерии разложены по очередям "
                   "автоматически — по накопительной потребности. «(+N)» — "
                   "вместимости корпусов, вводимых в этой очереди; "
                   "«введено/требуется» — накопительно на конец очереди.",
                   _eng_note],
            summary=_sum_ph))

        # 🧩 Лоты застройки (v0.15.9): лоты + их очереди, социалка, инженерия.
        # Показывается, когда лоты содержательны: их больше одного ИЛИ включена
        # автономная инженерия (у лота есть свой комплект).
        _lots = getattr(ph, "lots", None) or []
        _has_lot_eng = any(lp.engineering for lp in _lots)
        if _lots and (len(_lots) > 1 or _has_lot_eng):
            def _bkl(bk):
                return " + ".join(str(b) for b in bk) if bk else "—"
            lot_rows = []
            for lp in _lots:
                row = {
                    "Лот": lp.index,
                    "Очереди": ", ".join(str(i) for i in lp.stages),
                    "Доля": f"{lp.share:.0%}",
                    "Площадь, м²": f"{lp.area_m2:,.0f}".replace(",", " "),
                    "Квартиры, м²": f"{lp.apartments_m2:,.0f}".replace(",", " "),
                    "Население": f"{lp.population:,.0f}".replace(",", " "),
                    "ДОО, мест": _bkl(lp.kg_buckets),
                    "СОШ, мест": _bkl(lp.school_buckets),
                    "Парковки, м/м": lp.parking_places,
                }
                if _has_lot_eng:
                    row["Инженерия"] = ", ".join(
                        f"{lbl.split(' (')[0]}×{cnt}"
                        for lbl, cnt in lp.engineering.items()) or "—"
                    row["ЗУ инж., м²"] = (
                        f"{lp.eng_plot_total:,.0f}".replace(",", " "))
                lot_rows.append(row)
            lot_cols = ["Лот", "Очереди", "Доля", "Площадь, м²", "Квартиры, м²",
                        "Население", "ДОО, мест", "СОШ, мест", "Парковки, м/м"]
            lot_alb = ["Лот", "Очереди", "Доля", "Квартиры, м²", "Население",
                       "ДОО, мест", "СОШ, мест"]
            if _has_lot_eng:
                lot_cols += ["Инженерия", "ЗУ инж., м²"]
                lot_alb += ["ЗУ инж., м²"]
            _lot_notes = ["Лот — группа очередей, полностью обеспеченная "
                          "соцобъектами: границы лотов проходят по вводу "
                          "корпусов СОШ (школа обслуживает несколько очередей)."]
            if _has_lot_eng:
                _lot_notes.append(
                    "Каждый лот обеспечен собственным комплектом инженерных "
                    "объектов по своему спросу; комплекты заложены в баланс "
                    "территории и экономику.")
            if ph.eng_delta_note:
                _lot_notes.append(ph.eng_delta_note)
            blocks.append(TableBlock(
                "lots", "Лоты застройки", "view_module", lot_rows,
                columns=lot_cols, album_columns=lot_alb, notes=_lot_notes,
                summary=ph.eng_delta_note))

    return blocks


def _balance_block(result: TEPResult) -> TableBlock:
    """Таблица баланса территории (с колонками по зонам при кластерах)."""
    b = result.balance
    site_area = b.site_area
    _cl = result.floor_clusters_detail
    _cl_total = sum(d["area_m2"] for d in _cl) if _cl else 0.0
    _cl_shares = ([(d["label"], d["area_m2"] / _cl_total) for d in _cl]
                  if _cl and _cl_total > 0 else [])

    def _with_zones(row: dict, val: float) -> dict:
        for label, sh in _cl_shares:
            row[f"{label}, м²"] = f"{val * sh:,.0f}".replace(",", " ")
        return row

    required_map = {
        "housing_lot": result.housing_lot_area.value,
        "kindergarten_plot": result.kindergarten_plot_area.value,
        "school_plot": result.school_plot_area.value,
        "sport_facilities": result.sport_facilities_plot_area.value,
        "social_parking_plot": result.social_parking_area.value,
        "add_education_plot": result.add_education_plot_area.value,
        "polyclinic_plot": result.polyclinic_plot_area.value,
        "znop": result.znop_area.value,
        "intra_quarter_driveways": result.driveways_intra_quarter_area.value,
        "parking_multilevel": result.parking_multilevel_area.value,
        "engineering_plot": (result.engineering.plot_total_all if result.engineering else 0.0),
    }
    pretty_map = {
        "housing_lot": "ЗУ жилой застройки",
        "kindergarten_plot": "Участки ДОО",
        "school_plot": "Участки СОШ",
        "sport_facilities": "Спортивные сооружения",
        "social_parking_plot": "Парковки соцобъектов (ДОО/СОШ)",
        "add_education_plot": "Доп. образование (ЗУ)",
        "polyclinic_plot": "Поликлиника (ЗУ)",
        "znop": "ЗНОП",
        "intra_quarter_driveways": "Внутриквартальные проезды",
        "parking_multilevel": "Многоуровневые паркинги",
        "built_in_greening": "Озеленение ВПП",
        "custom_objects": "Пользовательские объекты",
        "engineering_plot": "Инженерная инфраструктура",
    }
    comp_rows = []
    for name, val in sorted(b.components.items(), key=lambda kv: -kv[1]):
        pct = val / site_area * 100 if site_area > 0 else 0
        req = required_map.get(name, val)
        if req is None:
            req = val
        comp_rows.append(_with_zones({
            "Компонент": pretty_map.get(name, name),
            "Требуется, м²": f"{req:,.0f}".replace(",", " "),
            "В балансе, м²": f"{val:,.0f}".replace(",", " "),
            "Доля квартала, %": f"{pct:.1f}%",
        }, val))
    comp_rows.append(_with_zones({
        "Компонент": "— Итого занято", "Требуется, м²": "",
        "В балансе, м²": f"{b.required_total:,.0f}".replace(",", " "),
        "Доля квартала, %": f"{b.required_total / site_area * 100:.1f}%",
    }, b.required_total))
    comp_rows.append(_with_zones({
        "Компонент": "— Резерв (surplus)", "Требуется, м²": "",
        "В балансе, м²": f"{b.surplus:,.0f}".replace(",", " "),
        "Доля квартала, %": f"{b.surplus / site_area * 100:.1f}%",
    }, b.surplus))

    columns = ["Компонент", "Требуется, м²", "В балансе, м²", "Доля квартала, %"]
    columns += [f"{label}, м²" for label, _ in _cl_shares]
    note = (
        f"Площадь квартала: {fmt_m2(site_area)}  ·  {fmt_ga(site_area)}  ·  "
        f"«Требуется» — фактическая площадь; «В балансе» = 0 у компонентов в "
        f"режиме «только потребность» (размещаются вне квартала)."
    )
    if _cl_shares:
        note += "  ·  колонки по зонам — пропорционально площади зон"
    _occ_pct = b.required_total / site_area * 100 if site_area > 0 else 0
    _feas = "баланс сходится" if b.is_feasible else "ДЕФИЦИТ территории"
    summary = (
        f"Занято {fmt_m2(b.required_total)} ({_occ_pct:.0f}% квартала), "
        f"резерв {fmt_m2(b.surplus)}; {_feas}."
    )
    return TableBlock("balance", "Баланс территории", "balance", comp_rows,
                      columns=columns, notes=[note], summary=summary)
