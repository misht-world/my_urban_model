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
    plt = _mpl()
    fig, ax = plt.subplots(figsize=(7.2, 2.2))
    left = 0.0
    for lbl, val, col in items:
        pct = val / site * 100
        ax.barh(0, val, left=left, color=col, edgecolor="white", height=0.6)
        if pct >= 4:
            ax.text(left + val / 2, 0, f"{lbl}\n{pct:.0f}%", ha="center",
                    va="center", fontsize=8,
                    color="white" if col != _CC["reserve"] else _CC["ink"])
        left += val
    ax.set_xlim(0, max(site, left))
    ax.set_ylim(-0.5, 0.5)
    ax.axis("off")
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
