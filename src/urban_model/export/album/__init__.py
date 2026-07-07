"""Альбом-презентация (PPTX) по варианту застройки — Фаза 1.

Стиль «Минимал · Спецификация»: белый фон, графит + амбер, тонкие линии,
моноширинные цифры. Соответствует интерфейсу программы и лендингу.

Точка входа:
    from urban_model.export.album import build_variant_album
    build_variant_album(name, tep, options, path)  # -> path к .pptx

См. ALBUM_PLAN.md.
"""
from __future__ import annotations

from urban_model.export.album.builder import build_variant_album
from urban_model.export.album.concept import build_concept_album

__all__ = ["build_variant_album", "build_concept_album"]
