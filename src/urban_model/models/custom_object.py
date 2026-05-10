"""Произвольный объект на территории квартала.

Используется для размещения сущностей, не покрытых базовыми классами
(ДОО / СОШ / ВПП / парковки / ЗНОП): например, офисное здание, ФОК,
поликлиника, культурный центр. Каждый такой объект:

* занимает площадь ЗУ — она ВЫЧИТАЕТСЯ из доступной территории квартала
  (попадает в `balance.components` как отдельная позиция);
* по своему ВРИ-коду требует парковок (через `parking.vpp.m2_per_place`)
  и даёт озеленение (через `greening.vpp_per_floor_area`).

Минималистичная модель: для парковок и озеленения нужна полная площадь
объекта (floor_area_m2). По умолчанию = plot_area_m2 (одноэтажный объект).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CustomObject(BaseModel):
    """Произвольный объект на территории квартала."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="Объект", description="Название (для отчёта)")
    plot_area_m2: float = Field(
        gt=0.0,
        description="Площадь ЗУ объекта, м² (вычитается из доступной территории)",
    )
    vri_code: str = Field(
        default="3.4",
        description="ВРИ-код по dict_vri.yaml — для парковок и озеленения",
    )
    floor_area_m2: float | None = Field(
        default=None,
        description=(
            "Общая площадь объекта (этажные перекрытия), м². "
            "Используется для расчёта парковок и озеленения по ВРИ. "
            "Если не задано — приравнивается к plot_area_m2 (одноэтажный объект)."
        ),
    )

    @model_validator(mode="after")
    def _default_floor_area(self) -> "CustomObject":
        if self.floor_area_m2 is None:
            self.floor_area_m2 = self.plot_area_m2
        return self
