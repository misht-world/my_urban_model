"""Альбом КОНЦЕПЦИИ (PPTX) — мульти-вариантный (v0.13.0).

Структура (по ТЗ Михаила):
  1. Титул
  2. (Общая информация о территории — позже)
  3. Сводная сравнительная таблица (сначала — обзор всех вариантов)
  4. Базовый вариант: карточка (KPI как на сайте) + слайды-таблицы по вкладкам
  5. Варианты оптимизации: то же — карточка + те же таблицы

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
from pptx.enum.text import PP_ALIGN

from urban_model.ui.formatting import fmt_int, fmt_m2

_MARGIN = T.LEFT
_CONTENT_W = T.CONTENT_W

# Значки KPI-карточек. Не эмодзи (те дают «тофу»-квадрат или цветной глиф), а
# ТЕКСТОВЫЕ символы Unicode из старых блоков (Геометрия/Дингбаты/Letterlike) —
# они гарантированно рендерятся МОНОХРОМНО и красятся цветом подписи (серым),
# как значки в модели.
_EMO = {
    "КИТ (ПЗЗ)": "⌂",          # ⌂ дом
    "Население, чел.": "☰",     # ☰ ряды
    "Площадь квартир": "▦",     # ▦ сетка
    "Этажность": "▤",           # ▤ слои
    "ЗНОП": "✿",               # ✿ флёрон
    "ДОО": "❍",                # ❍ круг
    "СОШ": "✎",                # ✎ карандаш
    "Доп. образование": "❖",    # ❖ ромб
    "Поликлиника": "✚",         # ✚ крест
    "Парковки": "◈",           # ◈ ромб-в-ромбе
    "Эконом-индекс": "₽",       # ₽ рубль
    "Выход жилья": "▲",         # ▲ вверх
    "Соц. нагрузка": "§",       # § параграф
    "ROI": "↗",                # ↗ рост
}


def _clean_name(name: str) -> str:
    """Убрать служебный префикс «opt:» из имени сценария."""
    return str(name).removeprefix("opt:").strip()


def _floors_txt(tep: TEPResult) -> str:
    ef = getattr(tep, "effective_floors", None) or 0
    if getattr(tep, "floor_clusters_detail", None):
        return f"{ef:.1f} ср."
    return f"{ef:.0f} эт." if ef else "—"


def _kpi_pairs(tep: TEPResult) -> list[tuple[str, str, str | None]]:
    """(значение, подпись с эмодзи, доп.строка) — зеркало render_main_kpi_grid."""
    def lbl(t):
        e = _EMO.get(t)
        return f"{e}  {t}" if e else t

    pairs: list[tuple[str, str, str | None]] = []
    pairs.append((f"{tep.kit.value:.3f}", lbl("КИТ (ПЗЗ)"), None))
    pairs.append((f"{fmt_int(tep.population.value)}", lbl("Население, чел."), None))
    pairs.append((fmt_m2(tep.apartments_area.value), lbl("Площадь квартир"), None))
    pairs.append((_floors_txt(tep), lbl("Этажность"), None))
    znop_pp = tep.znop_per_person.value or 0
    znop_area = int(tep.znop_area.value or 0)
    pairs.append((f"{znop_area:,}".replace(",", " ") + " м²" if znop_area else "—",
                  lbl("ЗНОП"), f"{znop_pp:.1f} м²/чел" if znop_pp > 0 else None))
    kg = int(tep.kindergarten_places_accepted.value or 0)
    pairs.append((f"{kg} мест" if kg else "—", lbl("ДОО"), None))
    sch = int(tep.school_places_accepted.value or 0)
    pairs.append((f"{sch} мест" if sch else "—", lbl("СОШ"), None))
    ae = int(tep.add_education_places_accepted.value or 0)
    ae_place = ("встроен. (ВПП)" if getattr(tep, "add_education_built_in", False)
                else "отд. стоящее")
    pairs.append((f"{ae} мест" if ae else "—", lbl("Доп. образование"),
                  ae_place if ae else None))
    poly_f = getattr(tep, "polyclinic_visits_accepted", None)
    poly = int(poly_f.value or 0) if poly_f is not None else 0
    poly_place = ("ВПП (офис врача)" if getattr(tep, "polyclinic_built_in", False)
                  else "отд. стоящая")
    pairs.append((f"{poly} посещ." if poly else "—", lbl("Поликлиника"),
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
    pairs.append((f"{total_pl} м/м" if total_pl else "—", lbl("Парковки"),
                  " · ".join(parts) or None))
    return pairs


def _econ_pairs(tep: TEPResult) -> list[tuple[str, str, str | None]]:
    e = getattr(tep, "economy", None)
    if e is None:
        return []

    def lbl(t):
        e = _EMO.get(t)
        return f"{e}  {t}" if e else t

    out: list[tuple[str, str, str | None]] = [
        (f"{e.economy_index:.0f} / 100", lbl("Эконом-индекс"), None),
        (f"{e.sellable_ratio * 100:.0f}%" if e.sellable_ratio else "—",
         lbl("Выход жилья"), None),
    ]
    social_cost = (e.cost.kindergarten + e.cost.school + e.cost.social_parking
                   + getattr(e.cost, "add_education", 0.0))
    if social_cost > 0.5 or e.revenue.social_compensation > 0.5:
        out.append((f"{-e.net_social_burden:+,.0f}".replace(",", " "),
                    lbl("Соц. нагрузка"), "баллы"))
    else:
        out.append((f"{e.roi * 100:.1f}%" if e.cost.total > 0 else "—", lbl("ROI"), None))
    return out


def _var_label(v_index: int) -> str:
    return "База" if v_index == 0 else f"Вариант {v_index}"


def _split_title(title: str) -> tuple[str, str]:
    """Разбить заголовок на (обычная часть, жирное последнее слово)."""
    parts = title.rsplit(" ", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return "", title


def _chrome(s, rail_labels: list[str], current: int | None) -> None:
    """Общие элементы слайда: бренд справа-сверху + рейка вариантов справа."""
    T.top_right_brand(s)
    T.variant_rail(s, rail_labels, current)


def _card_slide(deck, name: str, tep: TEPResult, kind: str, v_index: int,
                rail_labels: list[str]) -> None:
    """Слайд-карточка варианта: KPI-сетка как на сайте (2 ряда × 5 + экономика)."""
    s = deck.slide()
    # Заголовок с частичным жирным (как «Сравнение вариантов»): последнее слово
    # — Black. «Базовый вариант» → Базовый + **вариант**; «Вариант 1» → Вариант + **1**.
    _plain, _bold = _split_title(kind)
    T.title_band(s, _plain, _bold)
    _chrome(s, rail_labels, v_index)
    # Длинное имя — отдельной строкой под чертой (перенос), по правому краю.
    # Единый стиль имени варианта на всех карточках (как на слайдах-таблицах).
    T.text(s, _MARGIN, 1.3, _CONTENT_W, 0.6, _clean_name(name),
           size=11, color=T.MUTED, spacing=1.05, align=PP_ALIGN.RIGHT)

    def _draw_row(pairs, top):
        n = len(pairs)
        if n == 0:
            return
        gap = 0.12
        cw = (_CONTENT_W - gap * (n - 1)) / n
        for i, (val, lbl, extra) in enumerate(pairs):
            left = _MARGIN + i * (cw + gap)
            T.kpi_card(s, left, top, cw, 1.15, val, lbl, delta=extra)

    kp = _kpi_pairs(tep)
    _draw_row(kp[:5], 1.95)
    _draw_row(kp[5:10], 3.25)
    econ = _econ_pairs(tep)
    if econ:
        T.section_label(s, _MARGIN, 4.6, _CONTENT_W, "Экономика")
        _draw_row(econ, 5.0)
    T.footer(s)


def _table_slide(deck, name: str, block, v_index: int,
                 rail_labels: list[str]) -> None:
    """Слайд-таблица одной вкладки варианта (из общего билдера) + итог раздела."""
    s = deck.slide()
    T.title_band(s, block.title, "")
    _chrome(s, rail_labels, v_index)
    T.text(s, _MARGIN, 1.28, _CONTENT_W, 0.35, _clean_name(name),
           size=11, color=T.MUTED, align=PP_ALIGN.RIGHT)
    if block.columns is None:
        headers = ["Показатель", "Значение"]
        rows = [(r["Показатель"], r["Значение"]) for r in block.rows]
        ratios = [3, 2]
    else:
        headers = block.columns
        rows = [[r.get(c, "") for c in block.columns] for r in block.rows]
        ratios = None
    max_rows = 15
    top = 1.75
    T.table(s, _MARGIN, top, _CONTENT_W, headers, rows[:max_rows], col_ratios=ratios)
    note_top = top + 0.4 + 0.32 * min(len(rows), max_rows) + 0.12
    # Итог раздела — мелким серым, как справочные notes (без «ИТОГ», не жирный).
    if block.summary:
        T.text(s, _MARGIN, note_top, _CONTENT_W, 0.6, block.summary,
               size=9, color=T.MUTED)
        note_top += 0.42
    for note in block.notes:
        T.text(s, _MARGIN, note_top, _CONTENT_W, 0.6, note, size=9, color=T.MUTED)
        note_top += 0.42
    T.footer(s)


# Показатели, где БОЛЬШЕ = лучше (подсвечиваем максимум в строке).
_HL_MORE_IS_BETTER = ("Площадь квартир", "Эконом-индекс")


def _best_col_for(df, lab: str, names: list[str]) -> int | None:
    """Индекс варианта (0-based) с максимальным числовым значением в строке."""
    best_j, best_v = None, None
    for j, nm in enumerate(names):
        try:
            v = float(str(df.loc[lab, nm]).replace(" ", "").replace(" ", ""))
        except (ValueError, TypeError):
            continue
        if best_v is None or v > best_v:
            best_v, best_j = v, j
    return best_j


def _comparison_slides(deck, scenarios: list[tuple[str, TEPResult]],
                       rail_labels: list[str]) -> None:
    """Сводная сравнительная таблица (как в xlsx). Разбита по слайдам."""
    clean = [(_clean_name(n), t) for n, t in scenarios]
    df = results_to_dataframe(clean)
    names = list(df.columns)
    labels = list(df.index)
    # Лучший вариант (столбец) по каждому «больше=лучше» показателю.
    best_by_label = {
        lab: _best_col_for(df, lab, names)
        for lab in labels if any(k in lab for k in _HL_MORE_IS_BETTER)
    }
    ratios = [2.4] + [1.0] * len(names)
    top = 1.7
    chunk = 13
    for start in range(0, len(labels), chunk):
        s = deck.slide()
        part = "" if len(labels) <= chunk else f" ({start // chunk + 1})"
        T.title_band(s, "Сравнение", "вариантов" + part)
        _chrome(s, rail_labels, None)   # рейка со всеми вариантами (обзор)
        sub = labels[start:start + chunk]
        headers = ["Показатель"] + names
        rows = []
        hl_cells = set()
        for i, lab in enumerate(sub, 1):     # i — 1-based строка данных в таблице
            row = [lab]
            for nm in names:
                v = df.loc[lab, nm]
                row.append("" if v is None else str(v))
            rows.append(row)
            bj = best_by_label.get(lab)
            if bj is not None:
                hl_cells.add((i, bj + 1))    # +1: столбец 0 — «Показатель»
        # (item 3) Серые подписи «База»/«Вариант N» над колонками — для сопоставления
        # с боковой рейкой. Позиции колонок из ratios.
        _tot = sum(ratios)
        for j in range(len(names)):
            x0 = _MARGIN + _CONTENT_W * (2.4 + j) / _tot
            cw = _CONTENT_W * 1.0 / _tot
            T.text(s, x0, top - 0.28, cw, 0.24, _var_label(j),
                   size=8, color=T.SOFT, align=PP_ALIGN.CENTER)
        T.table(s, _MARGIN, top, _CONTENT_W, headers, rows, col_ratios=ratios,
                fsize=10, hl_cells=hl_cells)
        T.footer(s)


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

    # Фиксированные подписи ушек-рейки: сверху вниз — База, Вариант 1, 2…
    rail_labels = [_var_label(i) for i in range(n_var)]

    # (9) Сначала — сводное сравнение (обзор всех вариантов).
    try:
        _comparison_slides(deck, scenarios, rail_labels)
    except Exception:  # noqa: BLE001 — §12: альбом не должен падать
        pass

    # Затем — детально по каждому варианту.
    for v_idx, (name, tep) in enumerate(scenarios):
        kind = "Базовый вариант" if v_idx == 0 else f"Вариант {v_idx}"
        try:
            _card_slide(deck, name, tep, kind, v_idx, rail_labels)
        except Exception:  # noqa: BLE001
            pass
        try:
            blocks = build_variant_table_blocks(tep)
        except Exception:  # noqa: BLE001
            blocks = []
        for blk in blocks:
            try:
                _table_slide(deck, name, blk, v_idx, rail_labels)
            except Exception:  # noqa: BLE001
                pass

    return deck.save(path)
