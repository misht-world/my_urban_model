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
    # «Только потребность»: ЗУ и здание ДОО не учитываются в балансе квартала
    # и не вычитаются из жилой GFA (для built_in). Используется когда ДОО
    # размещается за пределами квартала или уже существует. Места требуемые/
    # принятые при этом считаются и показываются как обычно.
    only_demand: bool = Field(
        default=False,
        description="ДОО учитывается только для расчёта потребности; ЗУ/здание не входят в баланс",
    )


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
    # «Только потребность»: ЗУ СОШ не учитывается в балансе квартала.
    # Используется когда СОШ размещается за пределами квартала.
    only_demand: bool = Field(
        default=False,
        description="СОШ учитывается только для расчёта потребности; ЗУ не входит в баланс",
    )
