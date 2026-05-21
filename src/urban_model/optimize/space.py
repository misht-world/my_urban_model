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
    parking_underground_share_range: tuple[float, float] | None = None
    multilevel_levels_range: tuple[int, int] | None = None
    underground_levels_range: tuple[int, int] | None = None

    # ВПП (v0.8.0): режим расчёта обязательных ВПП — список из
    # ["min_only", "min_plus", "custom_only", "full_floor", "half_floor"].
    # None = режим из base_options.
    vpp_modes: list[str] | None = Field(
        default=None,
        description="Подмножество режимов ВПП для перебора (v0.8.0)",
    )

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

    # ЗНОП (v0.7.3): значения м²/чел для перебора. Например [0, 3, 4, 6]
    # — это нормативные ступени по ПЗЗ. None = не варьировать (брать из base).
    znop_per_person_choices: list[float] | None = Field(
        default=None,
        description="Варианты ЗНОП в м²/чел для перебора (override); None = не варьировать",
    )

    # Целевая функция оптимизации (v0.8.0):
    # • "apartments_area" — максимум площади квартир (как было раньше)
    # • "profit" — максимум прибыли (требует TEPResult.economy != None)
    objective: str = Field(
        default="apartments_area",
        description="Целевая функция: 'apartments_area' или 'profit'",
    )

    # Жёсткая фильтрация сценариев с нарушением вместимости соцобъектов
    # (например, «9 ДОО по 9 мест при 80 мест общей потребности»).
    # По умолчанию False — для обратной совместимости со старыми тестами.
    # UI optimizer.py включает True.
    strict_social_validation: bool = Field(
        default=False,
        description="Отсеивать сценарии с capacity-violation в ДОО/СОШ",
    )

    # v0.9.1: использовать RandomSampler вместо TPE для равномерного
    # покрытия пространства параметров. Включается в Парето-рекомендациях,
    # чтобы получить ТИПОЛОГИЧЕСКИ разные варианты, а не концентрацию
    # вокруг одного максимума.
    diversify_sampler: bool = Field(
        default=False,
        description="True = RandomSampler (разнообразие), False = TPE (максимум)",
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
                "parking_underground_share_range",
                "multilevel_levels_range",
                "underground_levels_range",
                "kg_num_objects_range",
                "school_num_objects_range",
                "vpp_modes",
                "znop_per_person_choices",
            )
        ) and not self.try_built_in
