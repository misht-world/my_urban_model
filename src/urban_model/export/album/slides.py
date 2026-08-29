"""Рендер 12 слайдов альбома по одному варианту (Фаза 1)."""
from __future__ import annotations

from urban_model.export.album import charts, narrative
from urban_model.export.album import theme as T
from urban_model.export.album.risks import detect_risks, risk_level_color
from urban_model.export.docx_report import _fmt
from urban_model.models.result import Status, TEPResult

EMU = T.EMU


def _pic(slide, buf, left, top, width):
    if buf is not None:
        slide.shapes.add_picture(buf, int(left * EMU), int(top * EMU),
                                 width=int(width * EMU))


def _status_of(field):
    m = {
        Status.OK: ("в норме", T.OK),
        Status.WARNING: ("внимание", T.WARN),
        Status.ERROR: ("нарушение", T.BAD),
        Status.MANUAL: ("вручную", T.MUTED),
        Status.NO_DATA: ("нет данных", T.SOFT),
    }
    return m.get(getattr(field, "status", None), ("", T.MUTED))


def slide_summary(deck, tep: TEPResult, name: str):
    s = deck.slide()
    T.title_band(s, "Резюме", "варианта", idx=2)
    T.footer(s, name)
    e = tep.economy
    cards = [
        (f"{tep.kit.value:.2f}", "КИТ (ПЗЗ)"),
        (f"{_fmt(tep.apartments_area.value)}", "Площадь квартир, м²"),
        (f"{_fmt(tep.population.value)}", "Население, чел"),
        (f"{int(tep.parking_required_places.value or 0)}", "Машино-мест"),
        ((f"{e.profit:+,.0f}".replace(",", " ") if e else "—"), "Эконом. запас"),
    ]
    cw, ch, gx = 2.34, 1.25, 0.12
    for i, (v, lbl) in enumerate(cards):
        T.kpi_card(s, 0.6 + i * (cw + gx), 1.5, cw, ch, v, lbl)
    T.section_label(s, 0.6, 3.15, 12.1, "Вывод")
    lines = [narrative.economy_verdict(tep)]
    if tep.limiting_factor:
        lines.append(f"Ограничивающий фактор: {tep.limiting_factor}")
    risks = detect_risks(tep)
    if risks:
        lines.append(f"Главный риск: {risks[0].title} ({risks[0].level}).")
    T.text(s, 0.6, 3.65, 12.1, 2.8,
           [("• " + ln, T.INK, False) for ln in lines], size=14, spacing=1.3)
    return s


def slide_inputs(deck, tep: TEPResult, options, name: str):
    s = deck.slide()
    T.title_band(s, "Исходные", "условия", idx=3)
    T.footer(s, name)
    site = tep.balance.site_area
    o = options
    cls = {"economy": "эконом", "comfort": "комфорт", "business": "бизнес"}.get(
        getattr(o, "residential_class", ""), getattr(o, "residential_class", "—"))
    floors = (f"{tep.effective_floors:.1f} (средневзв.)"
              if getattr(o, "floor_clusters", None) else str(getattr(o, "floors", "—")))
    rows = [
        ["Площадь квартала", f"{_fmt(site)} м² ({site / 10000:.2f} га)"],
        ["Регион / профиль", "Санкт-Петербург"],
        ["ДПТ", "есть" if getattr(o, "planning_doc", False) else "нет"],
        ["Этажность", floors],
        ["Класс жилья", cls],
        ["Парковки", _parking_mode_ru(o)],
        ["ДОО / СОШ", _social_mode_ru(o)],
    ]
    T.table(s, 0.6, 1.5, 9.0, ["Параметр", "Значение"], rows,
            col_ratios=[2, 3], fsize=12)
    return s


def _parking_mode_ru(o) -> str:
    p = getattr(o, "parking", None)
    if p is None:
        return "—"
    return {
        "min_open": "минимум открытых + подземные",
        "all_open": "все открытые наземные",
        "custom": "заданное соотношение типов",
    }.get(getattr(p, "mode", ""), getattr(p, "mode", "—"))


def _social_mode_ru(o) -> str:
    parts = []
    if getattr(o, "include_kindergarten", True):
        kg = getattr(o, "kindergarten", None)
        bt = getattr(kg, "building_type", "") if kg else ""
        parts.append("ДОО " + ("встроенный" if bt == "built_in" else "отдельный"))
    parts.append("СОШ учитывается" if getattr(o, "include_school", True)
                 else "СОШ не учитывается")
    return "; ".join(parts) if parts else "—"


def slide_tep(deck, tep: TEPResult, name: str):
    s = deck.slide()
    T.title_band(s, "Главные", "ТЭП", idx=4)
    T.footer(s, name)
    e = tep.economy
    cards = [
        (f"{_fmt(tep.gfa.value)}", "GFA, м²"),
        (f"{_fmt(tep.apartments_area.value)}", "Квартиры, м²"),
        ((f"{e.sellable_ratio * 100:.0f}%" if e else "—"), "Выход жилья"),
        (f"{tep.kit.value:.2f}", "КИТ"),
        (f"{_fmt(tep.population.value)}", "Население, чел"),
        (f"{_fmt(tep.density_chel_per_ga.value, 0)}", "Плотность, чел/га"),
        (f"{_fmt(tep.housing_footprint.value)}", "Застройка, м²"),
        (f"{_fmt(tep.balance.surplus)}", "Резерв, м²"),
    ]
    cw, ch, gx, gy = 2.95, 1.3, 0.13, 0.2
    for i, (v, lbl) in enumerate(cards):
        r, c = divmod(i, 4)
        T.kpi_card(s, 0.6 + c * (cw + gx), 1.5 + r * (ch + gy), cw, ch, v, lbl)
    T.text(s, 0.6, 4.7, 12.1, 1.0,
           [("• " + narrative.economy_verdict(tep), T.INK, False)],
           size=13, spacing=1.3)
    return s


def slide_balance(deck, tep: TEPResult, name: str):
    s = deck.slide()
    T.title_band(s, "Баланс", "территории", idx=5)
    T.footer(s, name)
    _pic(s, charts.chart_balance(tep), 0.6, 1.5, 12.1)
    b = tep.balance
    pretty = {
        "housing_lot": "Жильё", "kindergarten_plot": "ДОО", "school_plot": "СОШ",
        "sport_facilities": "Спорт", "social_parking_plot": "Парковки соц.",
        "znop": "Озеленение", "intra_quarter_driveways": "Проезды",
        "parking_multilevel": "Паркинг МУ", "custom_objects": "Доп. объекты",
        "engineering_plot": "Инж. инфраструктура",
    }
    rows = []
    for nm, val in sorted(b.components.items(), key=lambda kv: -kv[1]):
        if val > 0:
            rows.append([pretty.get(nm, nm), f"{_fmt(val)}",
                         f"{val / b.site_area * 100:.1f}%"])
    surplus = max(0, b.surplus)
    rows.append(["Резерв", f"{_fmt(surplus)}", f"{surplus / b.site_area * 100:.1f}%"])
    T.table(s, 0.6, 3.5, 7.5, ["Компонент", "Площадь, м²", "Доля"], rows,
            col_ratios=[2, 1.3, 1], fsize=10, bold_last=True)
    return s


def slide_housing(deck, tep: TEPResult, name: str):
    s = deck.slide()
    T.title_band(s, "Жильё и", "население", idx=6)
    T.footer(s, name)
    e = tep.economy
    has_clusters = bool(tep.floor_clusters_detail)
    cards = [
        (f"{_fmt(tep.apartments_area.value)}", "Квартиры, м²"),
        (f"{_fmt(tep.gfa.value)}", "GFA, м²"),
        ((f"{e.sellable_ratio * 100:.0f}%" if e else "—"), "Выход жилья"),
        (f"{_fmt(tep.population.value)}", "Население, чел"),
        (f"{_fmt(tep.density_chel_per_ga.value, 0)}", "Плотность, чел/га"),
        ((f"{tep.effective_floors:.1f}" if has_clusters
          else f"{_fmt(tep.housing_footprint.value)}"),
         "Средневзв. этаж." if has_clusters else "Застройка, м²"),
    ]
    cw, ch, gx, gy = 2.95, 1.3, 0.13, 0.2
    for i, (v, lbl) in enumerate(cards):
        r, c = divmod(i, 3)
        T.kpi_card(s, 0.6 + c * (cw + gx), 1.5 + r * (ch + gy), cw, ch, v, lbl)
    if has_clusters:
        T.section_label(s, 0.6, 4.6, 12.1, "Этажность по зонам")
        _pic(s, charts.chart_clusters(tep), 0.6, 5.0, 5.0)
    return s


def slide_social(deck, tep: TEPResult, name: str):
    s = deck.slide()
    T.title_band(s, "Социальная", "инфраструктура", idx=7)
    T.footer(s, name)
    rows = []
    for label, req, acc, plot, bld in (
        ("ДОО", tep.kindergarten_places_required, tep.kindergarten_places_accepted,
         tep.kindergarten_plot_area, tep.kindergarten_building_area),
        ("СОШ", tep.school_places_required, tep.school_places_accepted,
         tep.school_plot_area, tep.school_building_area),
    ):
        st_txt, _ = _status_of(acc)
        rows.append([label, f"{_fmt(req.value)}", f"{_fmt(acc.value)}",
                     f"{_fmt(plot.value)}", f"{_fmt(bld.value)}", st_txt or "—"])
    T.table(s, 0.6, 1.5, 12.1,
            ["Объект", "Треб., мест", "Принято", "ЗУ, м²", "Здание, м²", "Статус"],
            rows, col_ratios=[1.2, 1.2, 1.1, 1.1, 1.1, 1.2], fsize=12)
    soc_warn = [w for w in tep.warnings if "ДОО" in w or "СОШ" in w]
    if soc_warn:
        from urban_model.calculations.warning_codes import strip_code
        T.section_label(s, 0.6, 3.4, 12.1, "Предупреждения")
        T.text(s, 0.6, 3.85, 12.1, 2.8,
               [("• " + strip_code(w), T.WARN, False) for w in soc_warn[:3]],
               size=11, spacing=1.25)
    T.text(s, 0.6, 6.3, 12.1, 0.6, narrative.social_verdict(tep),
           size=11, color=T.MUTED)
    return s


def slide_parking(deck, tep: TEPResult, name: str):
    s = deck.slide()
    T.title_band(s, "Парковки", "", idx=8)
    T.footer(s, name)
    _pic(s, charts.chart_parking(tep), 0.6, 1.6, 6.0)
    rows = [
        ["Всего требуется", f"{int(tep.parking_required_places.value or 0)}", "—"],
        ["Открытые", f"{int(tep.parking_open_places.value or 0)}",
         f"{_fmt(tep.parking_open_area.value)}"],
        ["Многоуровневые", f"{int(tep.parking_multilevel_places.value or 0)}",
         f"{_fmt(tep.parking_multilevel_area.value)}"],
        ["Подземные", f"{int(tep.parking_underground_places.value or 0)}", "—"],
        ["Соцобъектов", f"{int(tep.social_parking_total.value or 0)}",
         f"{_fmt(tep.social_parking_area.value)}"],
    ]
    T.table(s, 6.9, 1.6, 5.8, ["Тип", "м/м", "Площадь, м²"], rows,
            col_ratios=[2, 1, 1.4], fsize=11)
    T.text(s, 0.6, 6.2, 12.1, 0.7, narrative.parking_verdict(tep),
           size=12, color=T.MUTED)
    return s


def slide_open_spaces(deck, tep: TEPResult, name: str):
    s = deck.slide()
    T.title_band(s, "Озеленение,", "спорт, проезды", idx=9)
    T.footer(s, name)
    cards = [
        (f"{_fmt(tep.znop_area.value)}", "ЗНОП, м²"),
        (f"{_fmt(tep.znop_per_person.value, 1)}", "ЗНОП, м²/чел"),
        (f"{_fmt(tep.sport_facilities_area.value)}", "Спорт, м²"),
        (f"{_fmt(tep.greening_housing_area.value)}", "Озелен. жилья, м²"),
    ]
    cw, ch, gx = 2.95, 1.3, 0.13
    for i, (v, lbl) in enumerate(cards):
        T.kpi_card(s, 0.6 + i * (cw + gx), 1.6, cw, ch, v, lbl)
    b = tep.balance
    gr_ok = b.greening_actual + 1e-6 >= b.greening_required
    T.section_label(s, 0.6, 3.3, 12.1, "Озеленение ТОП (≥ 6 м²/чел)")
    T.text(s, 0.6, 3.75, 12.1, 0.6,
           [(f"Требуется ≥ {_fmt(b.greening_required)} м², "
             f"факт {_fmt(b.greening_actual)} м² — "
             + ("выполнен" if gr_ok else "НЕ выполнен"),
             T.OK if gr_ok else T.BAD, True)], size=13)
    return s


def slide_economy(deck, tep: TEPResult, name: str):
    s = deck.slide()
    T.title_band(s, "Экономическая", "оценка", idx=10)
    T.footer(s, name)
    e = tep.economy
    if e is None:
        T.text(s, 0.6, 3.0, 12.1, 1.0, "Экономика не рассчитывалась.", size=16)
        return s
    _pic(s, charts.chart_economy_waterfall(tep), 0.6, 1.5, 7.4)
    cards = [
        (f"{e.profit:+,.0f}".replace(",", " "), "Эконом. запас"),
        (f"{e.margin * 100:.1f}%", "Маржа"),
        (f"{e.roi * 100:.1f}%", "ROI"),
        (f"{e.net_social_burden:,.0f}".replace(",", " "), "Соц. нагрузка"),
    ]
    for i, (v, lbl) in enumerate(cards):
        T.kpi_card(s, 8.3, 1.5 + i * 1.18, 4.4, 1.0, v, lbl)
    T.text(s, 0.6, 6.55, 12.1, 0.7, narrative.DISCLAIMER, size=9, color=T.MUTED)
    return s


def slide_risks(deck, tep: TEPResult, name: str):
    s = deck.slide()
    T.title_band(s, "Риски и", "ограничения", idx=11)
    T.footer(s, name)
    risks = detect_risks(tep)
    if not risks:
        T.text(s, 0.6, 3.0, 12.1, 1.0,
               "Существенных рисков не выявлено.", size=16, color=T.OK)
        return s
    y = 1.55
    for r in risks[:7]:
        col = risk_level_color(r.level)
        T.status_pill(s, 0.6, y, 1.7, r.level, col)
        T.text(s, 2.45, y - 0.04, 10.2, 0.7,
               [(r.title, T.INK, True), (r.note, T.MUTED, False)],
               size=11, spacing=1.05)
        y += 0.78
    return s


def slide_verdict(deck, tep: TEPResult, name: str):
    s = deck.slide()
    T.title_band(s, "Что выбрать /", "что дальше", idx=12)
    T.footer(s, name)
    v = narrative.overall_verdict(tep)
    T.status_pill(s, 0.6, 1.5, 3.2, v["status"], v["status_color"])
    T.text(s, 0.6, 2.05, 12.1, 1.0, v["headline"], size=14, spacing=1.3)
    T.section_label(s, 0.6, 3.1, 5.9, "Главный плюс")
    T.text(s, 0.6, 3.55, 5.9, 1.5,
           [("• " + p, T.INK, False) for p in v["pros"]], size=11, spacing=1.2)
    T.section_label(s, 6.8, 3.1, 5.9, "Главный риск")
    T.text(s, 6.8, 3.55, 5.9, 1.5,
           [("• " + c, T.INK, False) for c in v["cons"]], size=11, spacing=1.2)
    T.section_label(s, 0.6, 4.9, 12.1, "Что проверить дальше")
    T.text(s, 0.6, 5.35, 12.1, 1.3,
           [("• " + c, T.INK, False) for c in v["checks"]], size=11, spacing=1.2)
    T.text(s, 0.6, 6.55, 12.1, 0.7, narrative.DISCLAIMER, size=9, color=T.MUTED)
    return s
