"""Кластер этажности (v0.9.28) — подучасток квартала со своей высотностью.

Кластеры моделируют градостроительные ограничения по высоте (зоны ПЗЗ):
квартал делится на N подучастков, у каждого своя площадь и этажность.

Ключевая математика (см. CLAUDE.md → дорожная карта v0.9.28):

    floors_eff = Σ(A_i · F_i) / Σ A_i     — средневзвешенная этажность.

Для баланса территории и норматива озеленения квартал схлопывается в одну
величину `floors_eff` (т.к. вычитаемые территории распределяются
пропорционально площади кластеров → доступная под застройку доля `f`
одинакова для всех кластеров). Нелинейные по этажам величины (себестоимость,
проезды) считаются покластерно. КИТ — покластерно:

    КИТ_i = КИТ_global · F_i / floors_eff
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FloorCluster(BaseModel):
    """Подучасток квартала с собственной этажностью.

    `area_m2` — физическая площадь подучастка (границы зоны ПЗЗ).
    `floors` — принятая этажность.
    `floors_min` / `floors_max` — диапазон для оптимизатора (Optuna, Фаза C);
    на обычный расчёт не влияют.
    """

    model_config = ConfigDict(extra="forbid")

    area_m2: float = Field(gt=0, description="Площадь подучастка, м²")
    floors: int = Field(ge=1, le=60, description="Этажность кластера")
    floors_min: int = Field(default=3, ge=1, le=60, description="Мин. этажность для подбора")
    floors_max: int = Field(default=25, ge=1, le=60, description="Макс. этажность для подбора")
    label: str | None = Field(default=None, description="Подпись кластера (для UI/отчётов)")

    @model_validator(mode="after")
    def _check_range(self) -> "FloorCluster":
        if self.floors_max < self.floors_min:
            raise ValueError(
                f"floors_max ({self.floors_max}) < floors_min ({self.floors_min})"
            )
        return self
