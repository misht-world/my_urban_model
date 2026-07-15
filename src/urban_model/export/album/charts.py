"""Диаграммы PPTX-альбома (matplotlib, Agg) в палитре «Спецификация».

Каждая функция возвращает BytesIO PNG или None (нет данных).
"""
from __future__ import annotations

from io import BytesIO

from urban_model.export.album.theme import CHART_COLORS as _CC
from urban_model.models.result import TEPResult


def _mpl():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


def _save(fig, plt) -> BytesIO:
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return buf


def _style(ax):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(_CC["hair"])
    ax.tick_params(colors=_CC["ink"], labelsize=9, length=0)


def chart_balance(tep: TEPResult) -> BytesIO | None:
    b = tep.balance
    site = b.site_area
    if not site or site <= 0:
        return None
    pretty = {
        "housing_lot": ("Жильё", _CC["housing"]),
        "kindergarten_plot": ("ДОО", _CC["kindergarten"]),
        "school_plot": ("СОШ", _CC["school"]),
        "sport_facilities": ("Спорт", _CC["sport"]),
        "social_parking_plot": ("Парк. соц.", _CC["parking"]),
        "znop": ("Озеленение", _CC["znop"]),
        "intra_quarter_driveways": ("Проезды", _CC["driveways"]),
        "parking_multilevel": ("Паркинг МУ", _CC["parking"]),
        "custom_objects": ("Доп. объекты", _CC["engineering"]),
        "engineering_plot": ("Инж. инфр.", _CC["engineering"]),
    }
    pretty = {
        **pretty,
        "add_education_plot": ("Доп. образование", _CC["kindergarten"]),
        "polyclinic_plot": ("Поликлиника", _CC["school"]),
        "built_in_greening": ("Озеленение ВПП", _CC["sport"]),
    }
    items = []
    for name, val in sorted(b.components.items(), key=lambda kv: -kv[1]):
        if val > 0:
            lbl, col = pretty.get(name, (name, _CC["engineering"]))
            items.append((lbl, val, col))
    surplus = max(0.0, b.surplus)
    if surplus > 0:
        items.append(("Резерв", surplus, _CC["reserve"]))
    if not items:
        return None
    # v0.17.4 (утверждено Михаилом): горизонтальные бары по компонентам вместо
    # стек-полосы — подписи мелких компонентов не слипаются.
    plt = _mpl()
    fig, ax = plt.subplots(figsize=(7.5, 0.55 + 0.40 * len(items)))
    labels = [it[0] for it in items][::-1]
    vals = [it[1] for it in items][::-1]
    cols = [it[2] for it in items][::-1]
    bars = ax.barh(labels, vals, color=cols, height=0.62)
    vmax = max(vals)
    for bar, v in zip(bars, vals):
        pct = v / site * 100
        ax.text(bar.get_width() + vmax * 0.015,
                bar.get_y() + bar.get_height() / 2,
                f"{v:,.0f} м² · {pct:.0f}%".replace(",", " "),
                va="center", fontsize=8.5, color=_CC["ink"])
    ax.set_xlim(0, vmax * 1.30)
    ax.tick_params(axis="y", labelsize=8.5)
    ax.set_xticks([])
    _style(ax)
    ax.spines["bottom"].set_visible(False)
    return _save(fig, plt)


def chart_per_capita(tep: TEPResult) -> BytesIO | None:
    """Удельные показатели на жителя, м²/чел (v0.17.2, п.10 Михаила):
    озеленение жилого ЗУ / ЗНОП / спортплощадки / ВПП. Горизонтальные бары."""
    pop = float(tep.population.value or 0.0)
    if pop <= 0:
        return None

    def _pc(field) -> float:
        return float((field.value or 0.0)) / pop if field is not None else 0.0

    items = [
        ("ЗНОП", float(tep.znop_per_person.value or 0.0), _CC["znop"]),
        ("Озеленение жилого ЗУ", _pc(tep.greening_housing_area), _CC["housing"]),
        ("Спортплощадки", _pc(tep.sport_facilities_area), _CC["sport"]),
        ("ВПП (коммерция/сервисы)", _pc(tep.built_in_area), _CC["amber"]),
    ]
    items = [it for it in items if it[1] > 0.005]
    if not items:
        return None
    plt = _mpl()
    fig, ax = plt.subplots(figsize=(5.6, 0.6 + 0.55 * len(items)))
    labels = [it[0] for it in items][::-1]
    vals = [it[1] for it in items][::-1]
    cols = [it[2] for it in items][::-1]
    bars = ax.barh(labels, vals, color=cols, height=0.55)
    for b, v in zip(bars, vals):
        ax.text(b.get_width() + max(vals) * 0.02,
                b.get_y() + b.get_height() / 2,
                f"{v:.1f}", va="center", fontsize=9, color=_CC["ink"])
    ax.set_xlim(0, max(vals) * 1.18)
    ax.set_xlabel("м² на жителя", fontsize=9, color=_CC["ink"])
    _style(ax)
    return _save(fig, plt)


def chart_social_provision(tep: TEPResult) -> BytesIO | None:
    """Обеспеченность соцобъектами, % (v0.17.4, утверждено Михаилом):
    принято/требуется × 100 для ДОО / СОШ / доп. образования / поликлиники,
    пунктирная линия на 100%. Подпись у бара — «принято/требуется»."""
    def _pair(req_f, acc_f):
        req = float(req_f.value or 0.0) if req_f is not None else 0.0
        acc = float(acc_f.value or 0.0) if acc_f is not None else 0.0
        return req, acc

    raw = [
        ("ДОО", *_pair(tep.kindergarten_places_required,
                       tep.kindergarten_places_accepted), "мест"),
        ("СОШ", *_pair(tep.school_places_required,
                       tep.school_places_accepted), "мест"),
        ("Доп. образование", *_pair(tep.add_education_places_required,
                                    tep.add_education_places_accepted), "мест"),
        ("Поликлиника", *_pair(getattr(tep, "polyclinic_visits_required", None),
                               getattr(tep, "polyclinic_visits_accepted", None)),
         "посещ."),
    ]
    items = [(lbl, req, acc, unit) for lbl, req, acc, unit in raw if req > 0]
    if not items:
        return None
    plt = _mpl()
    fig, ax = plt.subplots(figsize=(5.6, 0.5 + 0.5 * len(items)))
    labels = [it[0] for it in items][::-1]
    pcts = [it[2] / it[1] * 100 for it in items][::-1]
    cols = [(_CC["ok"] if p >= 100 - 1e-6 else _CC["bad"]) for p in pcts]
    bars = ax.barh(labels, pcts, color=cols, height=0.55)
    vmax = max(pcts + [100])
    for bar, (lbl, req, acc, unit) in zip(bars, items[::-1]):
        ax.text(bar.get_width() + vmax * 0.02,
                bar.get_y() + bar.get_height() / 2,
                f"{acc:.0f}/{req:.0f} {unit}",
                va="center", fontsize=8.5, color=_CC["ink"])
    ax.axvline(100, color=_CC["ink"], linestyle="--", linewidth=1)
    ax.set_xlim(0, vmax * 1.35)
    ax.set_xlabel("обеспеченность, % (пунктир = 100%)",
                  fontsize=8.5, color=_CC["ink"])
    ax.tick_params(axis="y", labelsize=8.5)
    _style(ax)
    return _save(fig, plt)


def chart_economy_structure(tep: TEPResult) -> BytesIO | None:
    """Структура экономики варианта (v0.19.1; v0.19.3 — горизонтальные бары).

    Два блока в общем масштабе: себестоимость по статьям и выручка по
    источникам. Стек-колонки (v0.19.1) не читались — мелкие статьи налезали
    друг на друга; тот же приём, что спас диаграмму баланса.
    """
    e = getattr(tep, "economy", None)
    if e is None or e.cost.total <= 0:
        return None
    c, rv = e.cost, e.revenue
    cost_items = [
        ("Жильё", c.residential, _CC["housing"]),
        ("ВПП", c.vpp, _CC["amber"]),
        ("Соцобъекты", c.kindergarten + c.school + c.add_education
         + c.polyclinic + c.social_parking, _CC["school"]),
        ("Парковки", c.parking_open + c.parking_multilevel
         + c.parking_underground + c.parking_stylobate, _CC["parking"]),
        ("Спорт", c.sport, _CC["sport"]),
        ("Доп. объекты", c.custom_objects, _CC["znop"]),
        ("Инженерия", c.engineering, _CC["engineering"]),
        ("Накладные", c.networks + c.landscaping + c.design + c.contingency
         + c.fixed, _CC["driveways"]),
    ]
    rev_items = [
        ("Квартиры", rv.residential, _CC["housing"]),
        ("ВПП/коммерция", rv.vpp_commercial, _CC["amber"]),
        ("Доп. объекты", rv.custom_commercial, _CC["znop"]),
        ("Парковки", rv.parking_open + rv.parking_multilevel
         + rv.parking_underground + rv.parking_stylobate, _CC["parking"]),
        ("Компенсация соц.", rv.social_compensation, _CC["ok"]),
    ]
    cost_items = [i for i in cost_items if i[1] > 0]
    rev_items = [i for i in rev_items if i[1] > 0]
    if not cost_items:
        return None
    plt = _mpl()
    n = len(cost_items) + len(rev_items)
    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(7.0, 1.1 + 0.34 * n), sharex=True,
        gridspec_kw={"height_ratios": [len(cost_items), max(1, len(rev_items))],
                     "hspace": 0.45},
    )
    _top = max(c.total, rv.total)

    def _block(ax, items, total, title):
        labels = [i[0] for i in items][::-1]
        vals = [i[1] for i in items][::-1]
        cols = [i[2] for i in items][::-1]
        bars = ax.barh(labels, vals, color=cols, height=0.62)
        for bar, v in zip(bars, vals):
            ax.text(bar.get_width() + _top * 0.012,
                    bar.get_y() + bar.get_height() / 2,
                    f"{v:,.0f} · {v / total * 100:.0f}%".replace(",", " "),
                    va="center", fontsize=8, color=_CC["ink"])
        ax.set_title(f"{title}  {total:,.0f}".replace(",", " "),
                     loc="left", fontsize=9.5, color=_CC["ink"], pad=4)
        ax.set_xlim(0, _top * 1.30)
        ax.set_xticks([])
        ax.tick_params(axis="y", labelsize=8)
        _style(ax)
        ax.spines["bottom"].set_visible(False)

    _block(ax1, cost_items, c.total, "Себестоимость")
    _block(ax2, rev_items, rv.total, "Выручка")
    return _save(fig, plt)


def chart_parking(tep: TEPResult) -> BytesIO | None:
    parts = [
        ("Открытые", int(tep.parking_open_places.value or 0), _CC["amber"]),
        ("Многоуровн.", int(tep.parking_multilevel_places.value or 0), _CC["housing"]),
        ("Подземные", int(tep.parking_underground_places.value or 0), _CC["ink"]),
        ("Соцобъектов", int(tep.social_parking_total.value or 0), _CC["parking"]),
    ]
    parts = [p for p in parts if p[1] > 0]
    total = sum(p[1] for p in parts)
    if total == 0:
        return None
    plt = _mpl()
    fig, ax = plt.subplots(figsize=(4.4, 3.4))
    wedges, _ = ax.pie(
        [p[1] for p in parts], colors=[p[2] for p in parts],
        startangle=90, counterclock=False,
        wedgeprops=dict(width=0.42, edgecolor="white", linewidth=2),
    )
    ax.text(0, 0, f"{total}\nм/м", ha="center", va="center",
            fontsize=15, color=_CC["ink"])
    ax.legend(wedges, [f"{p[0]} — {p[1]}" for p in parts],
              loc="center left", bbox_to_anchor=(1.0, 0.5),
              frameon=False, fontsize=9)
    ax.set(aspect="equal")
    return _save(fig, plt)


def chart_economy_waterfall(tep: TEPResult) -> BytesIO | None:
    e = tep.economy
    if e is None:
        return None
    rev, cost = e.revenue, e.cost
    steps = [
        ("Кварт.", rev.residential, True),
        ("Паркинг", rev.parking_open + rev.parking_multilevel + rev.parking_underground, True),
        ("ВПП", rev.vpp_commercial + rev.custom_commercial, True),
        ("Комп. соц.", rev.social_compensation, True),
        ("Жильё", -cost.residential, False),
        ("Паркинг", -(cost.parking_open + cost.parking_multilevel + cost.parking_underground), False),
        ("Соц.", -(cost.kindergarten + cost.school + cost.social_parking), False),
        ("Накладные", -(cost.networks + cost.landscaping + cost.design + cost.contingency), False),
    ]
    steps = [s for s in steps if abs(s[1]) > 1e-6]
    if not steps:
        return None
    plt = _mpl()
    fig, ax = plt.subplots(figsize=(7.4, 3.6))
    cum = 0.0
    xs = []
    for i, (lbl, val, is_rev) in enumerate(steps):
        col = _CC["ok"] if is_rev else _CC["bad"]
        ax.bar(i, val, bottom=cum, color=col, edgecolor="white", width=0.7)
        cum += val
        xs.append(lbl)
    ax.bar(len(steps), cum, color=_CC["amber"], edgecolor="white", width=0.7)
    xs.append("Запас")
    ax.axhline(0, color=_CC["ink"], linewidth=0.8)
    ax.set_xticks(range(len(xs)))
    ax.set_xticklabels(xs, fontsize=8, rotation=20, ha="right")
    _style(ax)
    ax.set_yticks([])
    ax.text(len(steps), cum, f"{cum:+,.0f}".replace(",", " "),
            ha="center", va="bottom" if cum >= 0 else "top",
            fontsize=10, color=_CC["ink"])
    return _save(fig, plt)


def chart_clusters(tep: TEPResult) -> BytesIO | None:
    det = tep.floor_clusters_detail
    if not det:
        return None
    plt = _mpl()
    fig, ax = plt.subplots(figsize=(5.2, 2.6))
    labels = [d["label"] for d in det]
    floors = [d["floors"] for d in det]
    ax.bar(labels, floors, color=_CC["housing"], edgecolor="white", width=0.55)
    for i, f in enumerate(floors):
        ax.text(i, f, str(f), ha="center", va="bottom", fontsize=10, color=_CC["ink"])
    _style(ax)
    ax.set_ylabel("этажей", fontsize=9, color="#8A8A8A")
    return _save(fig, plt)
