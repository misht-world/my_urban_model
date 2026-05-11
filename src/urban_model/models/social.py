"""Спецификации соцобъектов для расчёта v0.1."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

KGType = Literal["detached", "built_in"]


class KindergartenSpec(BaseModel):
    """Структура ДОО на участке. По умолчанию — все мест распределяем по
    отдельностоящим ДОО с автоматическим количеством объектов."""
    model_config = ConfigDict(extra="forbid")

    building_type: KGType = "detached"
    # если задано — фиксированное число объектов и вместимость каждого
    num_objects: int | None = None
    capacity_per_object: int | None = None


class SchoolSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    building_type: Literal["detached"] = "detached"
    # По умолчанию — стандартная СОШ СПб с бассейном (+0.2 га к участку) и
    # спортивным ядром (+0.7 га). Это типовое благоустройство в современных
    # проектах СПб. Если у проекта другая комплектация — отключите явно.
    has_pool: bool = True
    has_sport_core: bool = True
    num_objects: int | None = None
    capacity_per_object: int | None = None
