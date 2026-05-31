"""Сборка PPTX-альбома по одному варианту (Фаза 1).

build_variant_album(name, tep, options, path) -> path. Каждый слайд
обёрнут в try/except: при нехватке данных блок пропускается (§12 ТЗ).
"""
from __future__ import annotations

import datetime as _dt

from urban_model.export.album import slides
from urban_model.export.album import theme as T
from urban_model.export.docx_report import _fmt
from urban_model.models.result import TEPResult


def build_variant_album(name: str, tep: TEPResult, options, path: str) -> str:
    """Собирает альбом-презентацию (PPTX 16:9) по варианту. Возвращает путь."""
    deck = T.Deck()

    site = tep.balance.site_area
    meta = (f"Площадь квартала: {_fmt(site)} м² ({site / 10000:.2f} га)   ·   "
            f"{_dt.date.today().strftime('%d.%m.%Y')}   ·   профиль СПб")
    T.title_slide(deck, "Альбом по", "варианту",
                  subtitle=f"«{name}»", meta_line=meta)

    steps = [
        lambda: slides.slide_summary(deck, tep, name),
        lambda: slides.slide_inputs(deck, tep, options, name),
        lambda: slides.slide_tep(deck, tep, name),
        lambda: slides.slide_balance(deck, tep, name),
        lambda: slides.slide_housing(deck, tep, name),
        lambda: slides.slide_social(deck, tep, name),
        lambda: slides.slide_parking(deck, tep, name),
        lambda: slides.slide_open_spaces(deck, tep, name),
        lambda: slides.slide_economy(deck, tep, name),
        lambda: slides.slide_risks(deck, tep, name),
        lambda: slides.slide_verdict(deck, tep, name),
    ]
    for step in steps:
        try:
            step()
        except Exception:  # noqa: BLE001 — §12: альбом не должен падать
            pass

    return deck.save(path)
