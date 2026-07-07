"""Альбом КОНЦЕПЦИИ (PPTX) — мульти-вариантный (v0.13.0).

Структура (по ТЗ Михаила):
  1. Титул
  2. (Общая информация о территории — позже)
  3. Базовый вариант: карточка (KPI как на сайте) + слайды-таблицы по вкладкам
  4. Варианты оптимизации: то же — карточка + те же таблицы
  5. Сводная сравнительная таблица (как в xlsx, в стиле альбома)

Таблицы берутся из ЕДИНОГО билдера `export/variant_tables.py` — те же строки,
что на «Расчёте», поэтому Базовый и оптимизационные варианты идентичны.
Экономика и полный аудит в альбом не входят (по запросу).
"""

from __future__ import annotations

import datetime as _dt

from urban_model.export.album import theme as T
from urban_model.export.table import results_to_dataframe
from urban_model.export.variant_tables import build_variant_table_blocks
from urban_model.models.result import TEPResult
from urban_model.ui.formatting import fmt_int, fmt_m2

_EMU = T.EMU
_MARGIN = 0.6
_CONTENT_W = 12.13


def _floors_txt(tep: TEPResult) -> str:
    ef = getattr(tep, "effective_floors", None) or 0
    if getattr(tep, "floor_clusters_detail", None):
        return f"{ef:.1f} ср."
    return f"{ef:.0f} эт." if ef else "—"


def _kpi_pairs(tep: TEPResult) -> list[tuple[str, str, str | None]]:
    """(значение, подпись, доп.строка) — зеркало render_main_kpi_grid."""
    pairs: list[tuple[str, str, str | None]] = []
    pairs.append((f"{tep.kit.value:.3f}", "КИТ (ПЗЗ)", None))
    pairs.append((f"{fmt_int(tep.population.value)}", "Население, чел.", None))
    pairs.append((fmt_m2(tep.apartments_area.value), "Площадь квартир", None))
    pairs.append((_floors_txt(tep), "Этажность", None))
    znop_pp = tep.znop_per_person.value or 0
    znop_area = int(tep.znop_area.value or 0)
    pairs.append((f"{znop_area:,}".replace(",", " ") + " м²" if znop_area else "—",
                  "ЗНОП", f"{znop_pp:.1f} м²/чел" if znop_pp > 0 else None))
    # ряд 2
    kg = int(tep.kindergarten_places_accepted.value or 0)
    pairs.append((f"{kg} мест" if kg else "—", "ДОО", None))
    sch = int(tep.school_places_accepted.value or 0)
    pairs.append((f"{sch} мест" if sch else "—", "СОШ", None))
    ae = int(tep.add_education_places_accepted.value or 0)
    ae_place = ("встроен. (ВПП)" if getattr(tep, "add_education_built_in", False)
                else "отд. стоящее")
    pairs.append((f"{ae} мест" if ae else "—", "Доп. образование",
                  ae_place if ae else None))
    poly_f = getattr(tep, "polyclinic_visits_accepted", None)
    poly = int(poly_f.value or 0) if poly_f is not None else 0
    poly_place = ("ВПП (офис врача)" if getattr(tep, "polyclinic_built_in", False)
                  else "отд. стоящая")
    pairs.append((f"{poly} посещ." if poly else "—", "Поликлиника",
                  poly_place if poly else None))
    op = int(tep.parking_open_places.value or 0)
    ml = int(tep.parking_multilevel_places.value or 0)
    ug = int(tep.parking_underground_places.value or 0)
    st_f = getattr(tep, "parking_stylobate_places", None)
    styl = int(st_f.value or 0) if st_f is not None else 0
    total_pl = int(tep.parking_required_places.value or 0)
    parts = []
    if op:   parts.append(f"откр. {op}")
    if ml:   parts.append(f"мн. {ml}")
    if styl: parts.append(f"ст. {styl}")
    if ug:   parts.append(f"пд. {ug}")
    pairs.append((f"{total_pl} м/м" if total_pl else "—", "Парковки",
                  " · ".join(parts) or None))
    return pairs


def _econ_pairs(tep: TEPResult) -> list[tuple[str, str, str | None]]:
    e = getattr(tep, "economy", None)
    if e is None:
        return []
    out: list[tuple[str, str, str | None]] = [
        (f"{e.economy_index:.0f} / 100", "Эконом-индекс", None),
        (f"{e.sellable_ratio * 100:.0f}%" if e.sellable_ratio else "—", "Выход жилья", None),
    ]
    social_cost = (e.cost.kindergarten + e.cost.school + e.cost.social_parking
                   + getattr(e.cost, "add_education", 0.0))
    if social_cost > 0.5 or e.revenue.social_compensation > 0.5:
        out.append((f"{-e.net_social_burden:+,.0f}".replace(",", " "),
                    "Соц. нагрузка", "баллы"))
    else:
        out.append((f"{e.roi * 100:.1f}%" if e.cost.total > 0 else "—", "ROI", None))
    return out


def _card_slide(deck, name: str, tep: TEPResult, kind: str) -> None:
    """Слайд-карточка варианта: KPI-сетка как на сайте (2 ряда × 5 + экономика)."""
    s = deck.slide()
    T.title_band(s, kind, name)

    def _draw_row(pairs, top):
        n = len(pairs)
        if n == 0:
            return
        gap = 0.12
        cw = (_CONTENT_W - gap * (n - 1)) / n
        for i, (val, lbl, extra) in enumerate(pairs):
            left = _MARGIN + i * (cw + gap)
            T.kpi_card(s, left, top, cw, 1.2, val, lbl, delta=extra)

    kp = _kpi_pairs(tep)
    _draw_row(kp[:5], 1.5)
    _draw_row(kp[5:10], 2.85)
    econ = _econ_pairs(tep)
    if econ:
        T.section_label(s, _MARGIN, 4.25, _CONTENT_W, "Экономика")
        _draw_row(econ, 4.65)
    T.footer(s, name)


def _table_slide(deck, name: str, block, idx: int | None = None) -> None:
    """Слайд-таблица одной вкладки варианта (из общего билдера)."""
    s = deck.slide()
    T.title_band(s, block.title, "", idx=idx)
    if block.columns is None:
        headers = ["Показатель", "Значение"]
        rows = [(r["Показатель"], r["Значение"]) for r in block.rows]
        ratios = [3, 2]
    else:
        headers = block.columns
        rows = [[r.get(c, "") for c in block.columns] for r in block.rows]
        ratios = None
    # theme.table сама считает высоту (0.4 + 0.32×строк). Ограничим 15 строк
    # на слайд, чтобы не выехать за нижнюю кромку.
    max_rows = 15
    top = 1.55
    if len(rows) <= max_rows:
        T.table(s, _MARGIN, top, _CONTENT_W, headers, rows, col_ratios=ratios)
    else:
        # длинная таблица (напр. баланс с зонами) — только первые строки + пометка
        T.table(s, _MARGIN, top, _CONTENT_W, headers, rows[:max_rows], col_ratios=ratios)
    note_top = top + 0.4 + 0.32 * min(len(rows), max_rows) + 0.15
    for note in block.notes:
        T.text(s, _MARGIN, note_top, _CONTENT_W, 0.6, note, size=9, color=T.MUTED)
        note_top += 0.5
    T.footer(s, name)


def _comparison_slides(deck, scenarios: list[tuple[str, TEPResult]]) -> None:
    """Сводная сравнительная таблица (как в xlsx). Разбита по слайдам."""
    df = results_to_dataframe(scenarios)
    names = list(df.columns)
    labels = list(df.index)
    chunk = 13
    for start in range(0, len(labels), chunk):
        s = deck.slide()
        part = "" if len(labels) <= chunk else f" ({start // chunk + 1})"
        T.title_band(s, "Сравнение", "вариантов" + part)
        sub = labels[start:start + chunk]
        headers = ["Показатель"] + names
        rows = []
        for lab in sub:
            row = [lab]
            for nm in names:
                v = df.loc[lab, nm]
                row.append("" if v is None else str(v))
            rows.append(row)
        ratios = [2.4] + [1.0] * len(names)
        T.table(s, _MARGIN, 1.55, _CONTENT_W, headers, rows, col_ratios=ratios, fsize=10)
        T.footer(s, "сравнение")


def build_concept_album(
    scenarios: list[tuple[str, TEPResult]], path: str, *,
    title_line: str = "Альбом",
    title_bold: str = "концепции",
) -> str:
    """Собрать мульти-вариантный альбом концепции (PPTX 16:9). Возвращает путь.

    scenarios: список (имя, TEPResult) — Базовый вариант первым, далее варианты.
    Каждый слайд обёрнут в try/except — альбом не падает на нехватке данных.
    """
    deck = T.Deck()
    n_var = len(scenarios)
    meta = (f"Вариантов: {n_var}   ·   {_dt.date.today().strftime('%d.%m.%Y')}"
            f"   ·   профиль СПб")
    T.title_slide(deck, title_line, title_bold,
                  subtitle="Базовый вариант и варианты оптимизации", meta_line=meta)

    for v_idx, (name, tep) in enumerate(scenarios):
        kind = "Базовый вариант" if v_idx == 0 else f"Вариант {v_idx}"
        try:
            _card_slide(deck, name, tep, kind)
        except Exception:  # noqa: BLE001 — §12: альбом не должен падать
            pass
        try:
            blocks = build_variant_table_blocks(tep)
        except Exception:  # noqa: BLE001
            blocks = []
        for blk in blocks:
            try:
                _table_slide(deck, name, blk)
            except Exception:  # noqa: BLE001
                pass

    try:
        _comparison_slides(deck, scenarios)
    except Exception:  # noqa: BLE001
        pass

    return deck.save(path)
