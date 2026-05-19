"""Конфигурация и результат расчёта парковок.

Три типа машино-мест по занимаемой площади:
  open         — открытые в уровне земли; занимают surface-площадь квартала.
  multilevel   — многоуровневые наземные паркинги; занимают surface-площадь.
  underground  — подземные; не занимают поверхностную площадь.

Режимы ParkingConfig.mode:
  "min_open"   — только минимально требуемые открытые (≥12.5%),
                 остаток — подземные (текущее поведение v0.1/v0.2).
  "all_open"   — всё открытое; максимальная нагрузка на баланс.
  "custom"     — пользователь задаёт доли каждого типа
                 (open_share + multilevel_share + underground_share = 1.0).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ParkingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str = Field(
        default="min_open",
        description="min_open | all_open | custom",
    )

    # Параметры для mode="custom"
    open_share: float = Field(
        default=0.125,
        ge=0.0,
        le=1.0,
        description="Доля открытых м/м от общего числа требуемых",
    )
    multilevel_share: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Доля многоуровневых м/м",
    )
    underground_share: float = Field(
        default=0.875,
        ge=0.0,
        le=1.0,
        description="Доля подземных м/м",
    )
    multilevel_levels: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Число уровней многоуровневого паркинга",
    )
    # v0.8.0: уровни подземного паркинга. Влияет на себестоимость через
    # прогрессию (каждый следующий уровень дороже предыдущего).
    # Площадь поверхности не занимает — учитывается только в economy.cost.
    underground_levels: int = Field(
        default=1,
        ge=1,
        le=5,
        description="Число уровней подземного паркинга",
    )

    # v0.6: альтернативный способ задать многоуровневые парковки —
    # абсолютным числом мест (через количество объектов × вместимость).
    # Если задано, переопределяет multilevel_share при расчёте.
    multilevel_explicit_places: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Абсолютное число м/м в многоуровневых паркингах "
            "(обычно = количество_паркингов × вместимость_каждого). "
            "Если None — используется multilevel_share."
        ),
    )

    @model_validator(mode="after")
    def _validate(self) -> "ParkingConfig":
        valid_modes = {"min_open", "all_open", "custom"}
        if self.mode not in valid_modes:
            raise ValueError(f"mode должен быть одним из {valid_modes}")
        if self.mode == "custom":
            total = self.open_share + self.multilevel_share + self.underground_share
            # Tolerance 1e-3 (0.1% доли) — практичная точность; жёсткое 1e-6
            # ловило артефакты fp-арифметики в Optuna-сэмплере.
            if abs(total - 1.0) > 1e-3:
                raise ValueError(
                    f"open_share + multilevel_share + underground_share = {total:.4f},"
                    " ожидается 1.0 (допуск 0.1%)"
                )
            # Нормализуем, чтобы сумма точно = 1.0 (для дальнейших расчётов)
            scale = 1.0 / total
            self.open_share *= scale
            self.multilevel_share *= scale
            self.underground_share *= scale
        return self


class ParkingBreakdown(BaseModel):
    """Итог расчёта парковок — разбивка по типам."""

    model_config = ConfigDict(extra="forbid")

    total_required: int = Field(description="Всего м/м по нормативу")

    open_places: int = Field(description="Открытые в уровне земли")
    multilevel_places: int = Field(description="Многоуровневые наземные")
    underground_places: int = Field(description="Подземные")

    multilevel_objects: int = Field(description="Число объектов многоуровнего паркинга")
    multilevel_levels: int = Field(description="Этажность многоуровнего паркинга")

    open_area: float = Field(description="Площадь открытых парковок, м²")
    multilevel_footprint: float = Field(description="Пятно многоуровнего паркинга, м²")
    # underground_footprint = 0 по определению

    @property
    def total_surface_area(self) -> float:
        """Суммарная наземная площадь парковок (открытые + многоуровневые)."""
        return self.open_area + self.multilevel_footprint
