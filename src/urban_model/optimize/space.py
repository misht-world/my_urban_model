"""Описание пространства поиска для оптимизатора.

`SearchSpace` определяет, какие параметры варьировать и в каких пределах.
Передаётся в `optimize_max_apartments`. Поля = None означают «не варьировать»
(берётся из base_options).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SearchSpace(BaseModel):
    """Пространство поиска для Optuna.

    None для любого поля = «не варьировать» (фиксируем из base_options).
    Tuple (lo, hi) задаёт диапазон. Список — категориальный выбор.
    """
    model_config = ConfigDict(extra="forbid")

    # Этажность
    floors_range: tuple[int, int] | None = Field(
        default=None,
        description="(min, max) этажность; None = фиксированная из base_options",
    )

    # Парковки: режим
    parking_modes: list[str] | None = Field(
        default=None,
        description="Подмножество ['min_open', 'all_open', 'custom']; None = из base",
    )
    # Парковки: доли (применяется только если режим custom попал в выборку)
    parking_open_share_range: tuple[float, float] | None = None
    parking_multilevel_share_range: tuple[float, float] | None = None
    multilevel_levels_range: tuple[int, int] | None = None

    # ДОО: число объектов
    kg_num_objects_range: tuple[int, int] | None = None
    # СОШ: число объектов
    school_num_objects_range: tuple[int, int] | None = None

    # ВПП: пробовать с ВПП и без
    try_built_in: bool = Field(
        default=False,
        description="Если True — Optuna выбирает варианты с ВПП и без",
    )
    built_in_vri_codes: list[str] = Field(
        default_factory=lambda: ["4.4"],
        description="Какие ВРИ-коды пробовать; используется при try_built_in=True",
    )

    def is_empty(self) -> bool:
        """True, если ни одна декорация не задана — оптимизировать нечего."""
        return all(
            getattr(self, f) in (None, False, [])
            for f in (
                "floors_range",
                "parking_modes",
                "parking_open_share_range",
                "parking_multilevel_share_range",
                "multilevel_levels_range",
                "kg_num_objects_range",
                "school_num_objects_range",
            )
        ) and not self.try_built_in
