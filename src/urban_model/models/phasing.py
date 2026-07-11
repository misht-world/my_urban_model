"""Очерёдность застройки (v0.15.0) — территориальные этапы.

Квартал делится на 2–4 очереди долями площади. Расчёт квартала не меняется:
очерёдность — надстройка ПОВЕРХ готового TEPResult (как экономика).
Население/квартиры распределяются пропорционально долям; ДИСКРЕТНЫЕ объекты
(корпуса ДОО/СОШ, объекты инженерки) раскладываются по очередям по
накопительной потребности. Модель проверяет обеспеченность на конец каждого
этапа и выдаёт предупреждения о дефицитах.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PhasingSpec(BaseModel):
    """Задание очередей.

    mode="auto" (по умолчанию) — доли выводятся из дискретности соцобъектов:
    границы очередей проходят по ёмкости корпусов ДОО (нет ДОО → СОШ; нет
    ничего → 2 равные), так что на конец каждой очереди накопительная
    потребность покрыта введёнными корпусами (~95–105%). В UI это называется
    нейтрально «по обеспеченности соцобъектами».

    mode="manual" — пользователь задаёт доли площади (Σ = 1, 2–4 очереди).
    """
    model_config = ConfigDict(extra="forbid")

    mode: Literal["auto", "manual"] = "auto"
    shares: list[float] = Field(
        default_factory=lambda: [0.5, 0.5],
        description="Доли площади по очередям (только для mode='manual')",
    )

    @field_validator("shares")
    @classmethod
    def _validate_shares(cls, v: list[float]) -> list[float]:
        if not 2 <= len(v) <= 4:
            raise ValueError(f"очередей должно быть 2–4, задано {len(v)}")
        if any(s <= 0 for s in v):
            raise ValueError("доля очереди должна быть > 0")
        total = sum(v)
        if total <= 0:
            raise ValueError("сумма долей должна быть > 0")
        return [s / total for s in v]


class StageProvision(BaseModel):
    """Обеспеченность на КОНЕЦ очереди k (накопительно)."""
    model_config = ConfigDict(extra="forbid")

    index: int                       # 1-based номер очереди
    share: float                     # доля площади этой очереди
    area_m2: float                   # площадь очереди
    apartments_m2: float             # квартиры этой очереди
    population_stage: float          # население этой очереди
    population_cum: float            # население накопительно
    # Соцобъекты: накопительно (требуется от population_cum / есть по корпусам)
    kg_required_cum: float
    kg_provided_cum: int
    kg_buckets: list[int] = Field(default_factory=list)   # корпуса ЭТОЙ очереди
    school_required_cum: float
    school_provided_cum: int
    school_buckets: list[int] = Field(default_factory=list)
    # Парковки (пропорционально — строятся вместе с жильём)
    parking_places_stage: int = 0
    # Инженерка: объекты этой очереди («ТП ×3, Котельная ×1»)
    engineering_stage: dict[str, int] = Field(default_factory=dict)
    # Статус этапа
    deficits: list[str] = Field(default_factory=list)     # человекочитаемые

    @property
    def is_ok(self) -> bool:
        return not self.deficits


class PhasingResult(BaseModel):
    """Итог раскладки по очередям.

    stages может быть ПУСТЫМ (v0.15.4): авто-режим решил, что делить на
    очереди нет смысла (единственный корпус ДОО/СОШ) — причина в `note`.
    """
    model_config = ConfigDict(extra="forbid")

    mode: str = "manual"             # "auto" | "manual" — как получены доли
    stages: list[StageProvision] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    note: str | None = None          # пояснение, если stages пуст
