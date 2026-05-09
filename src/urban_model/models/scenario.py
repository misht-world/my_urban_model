"""Сценарий расчёта — именованная пара (Site, CalculationOptions) + режим.

Используется в compare_scenarios: каждый сценарий — одна строка входных данных.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from urban_model.models.options import CalculationOptions
from urban_model.models.site import Site


class Scenario(BaseModel):
    """Описание одного расчётного сценария.

    mode="inverse"  — найти максимально допустимый КИТ (по умолчанию).
    mode="verify"   — проверить заданный КИТ; обязательно указывать `kit`.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Человекочитаемое название сценария")
    site: Site
    options: CalculationOptions = Field(default_factory=CalculationOptions)
    mode: Literal["inverse", "verify"] = "inverse"
    kit: float | None = Field(
        default=None,
        ge=0.0,
        description="КИТ для режима verify; игнорируется при mode='inverse'",
    )

    @model_validator(mode="after")
    def _check_kit_required_for_verify(self) -> "Scenario":
        if self.mode == "verify" and self.kit is None:
            raise ValueError("kit обязателен при mode='verify'")
        return self
