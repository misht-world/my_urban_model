"""Параметры расчёта (то, что пользователь может крутить, не меняя нормативы)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from urban_model.models.parking import ParkingConfig
from urban_model.models.social import KindergartenSpec, SchoolSpec


class CalculationOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Параметры жилья
    floors: int = Field(default=10, ge=1, description="этажность жилья")
    planning_doc: bool = True  # ППТ → КИТ_max = 2.5; иначе 1.4

    # Доля ВПП в общей площади (грубо, до v0.3+)
    vpp_share: float = Field(default=0.0, ge=0.0, le=0.5)

    # Соцобъекты — учитывать или нет
    include_kindergarten: bool = True
    include_school: bool = True

    kindergarten: KindergartenSpec = Field(default_factory=KindergartenSpec)
    school: SchoolSpec = Field(default_factory=SchoolSpec)

    # Парковочный сценарий
    parking: ParkingConfig = Field(
        default_factory=ParkingConfig,
        description=(
            "Конфигурация парковок: mode='min_open' (по умолчанию) / "
            "'all_open' / 'custom' с долями open/multilevel/underground"
        ),
    )

    # Бисекция КИТ
    kit_search_min: float = 0.1
    kit_search_max: float | None = None  # если None — берём из норматива
    kit_tolerance: float = 0.001
