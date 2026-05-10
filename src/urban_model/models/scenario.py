"""Сценарий расчёта — именованная пара (Site, CalculationOptions) + режим.

Используется в `compare_scenarios`: каждый сценарий — одна строка входных
данных, один из четырёх поддерживаемых режимов расчёта.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from urban_model.models.options import CalculationOptions
from urban_model.models.site import Site


ScenarioMode = Literal["inverse", "verify", "with_reserve", "with_znop"]


class Scenario(BaseModel):
    """Описание одного расчётного сценария.

    Поддерживаемые режимы:
      * `inverse`       — найти максимально допустимый КИТ (по умолчанию).
      * `verify`        — проверить заданный КИТ; требуется `kit`.
      * `with_reserve`  — макс. КИТ с целевым балансовым резервом ≥ X м²;
                          требуется `target_surplus_m2`.
      * `with_znop`     — макс. КИТ при принудительно заданном ЗНОП;
                          требуется `target_znop_per_person`.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Человекочитаемое название сценария")
    site: Site
    options: CalculationOptions = Field(default_factory=CalculationOptions)
    mode: ScenarioMode = "inverse"

    # Параметры для режимов
    kit: float | None = Field(
        default=None,
        ge=0.0,
        description="КИТ для режима verify",
    )
    target_surplus_m2: float | None = Field(
        default=None,
        ge=0.0,
        description="Целевой балансовый резерв для режима with_reserve, м²",
    )
    target_znop_per_person: float | None = Field(
        default=None,
        ge=0.0,
        description="Принудительный ЗНОП для режима with_znop, м²/чел",
    )

    @model_validator(mode="after")
    def _check_required_params(self) -> "Scenario":
        if self.mode == "verify" and self.kit is None:
            raise ValueError("kit обязателен при mode='verify'")
        if self.mode == "with_reserve" and self.target_surplus_m2 is None:
            raise ValueError("target_surplus_m2 обязателен при mode='with_reserve'")
        if self.mode == "with_znop" and self.target_znop_per_person is None:
            raise ValueError("target_znop_per_person обязателен при mode='with_znop'")
        return self
