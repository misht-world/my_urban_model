"""Форматирование чисел и цветовая палитра статусов для UI."""

from __future__ import annotations

from urban_model.models.result import Status, TEPField

# Цвета для status-бейджей (соответствуют палитре xlsx-экспорта)
STATUS_COLOR = {
    Status.OK:      "#107C10",   # зелёный
    Status.WARNING: "#B07B00",   # янтарный
    Status.ERROR:   "#A4262C",   # красный
    Status.MANUAL:  "#0078D4",   # синий
    Status.NO_DATA: "#605E5C",   # серый
}

STATUS_BG = {
    Status.OK:      "#DFF6DD",
    Status.WARNING: "#FFF4CE",
    Status.ERROR:   "#FDE7E9",
    Status.MANUAL:  "#DEECF9",
    Status.NO_DATA: "#F3F2F1",
}

STATUS_LABEL_RU = {
    Status.OK:      "ok",
    Status.WARNING: "внимание",
    Status.ERROR:   "ошибка",
    Status.MANUAL:  "вручную",
    Status.NO_DATA: "нет данных",
}


def fmt_int(x: float | int | None) -> str:
    if x is None:
        return "—"
    return f"{x:,.0f}".replace(",", " ")


def fmt_float(x: float | int | None, digits: int = 2) -> str:
    if x is None:
        return "—"
    return f"{x:,.{digits}f}".replace(",", " ")


def fmt_m2(x: float | int | None) -> str:
    if x is None:
        return "—"
    return f"{fmt_int(x)} м²"


def fmt_ga(area_m2: float | int | None) -> str:
    if area_m2 is None:
        return "—"
    return f"{area_m2 / 10_000:.2f} га"


def status_badge_html(status: Status) -> str:
    """HTML-бейдж со статусом."""
    color = STATUS_COLOR.get(status, "#605E5C")
    bg = STATUS_BG.get(status, "#F3F2F1")
    label = STATUS_LABEL_RU.get(status, status.value)
    return (
        f'<span style="background:{bg};color:{color};'
        f'padding:2px 8px;border-radius:10px;'
        f'font-size:0.85em;font-weight:500;">{label}</span>'
    )


def field_with_status(field: TEPField, fmt_fn=fmt_int, suffix: str = "") -> str:
    """Значение TEPField + цветной бейдж статуса (HTML)."""
    val = fmt_fn(field.value) if field.value is not None else "—"
    badge = status_badge_html(field.status) if field.status != Status.OK else ""
    return f"{val}{suffix}&nbsp;&nbsp;{badge}"
