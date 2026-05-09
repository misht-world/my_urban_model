"""TEPField и TEPResult — структурированный результат расчёта.

Каждое поле несёт аудит-трейл: значение, нормативное, пользовательское,
статус, источник и формулу. См. CLAUDE.md → "TEPResult — структура".
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Status(str, Enum):
    OK = "ok"
    WARNING = "warning"
    ERROR = "error"
    MANUAL = "manual"
    NO_DATA = "no_data"


class TEPField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: float | int | None
    unit: str | None = None
    normative: float | int | None = None
    user_value: float | int | None = None
    status: Status = Status.OK
    source: str | None = None
    formula: str | None = None

    def __str__(self) -> str:
        u = f" {self.unit}" if self.unit else ""
        return f"{self.value}{u} [{self.status.value}]"


class BalanceCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    site_area: float
    required_total: float
    surplus: float  # site_area - required_total; <0 = дефицит
    components: dict[str, float] = Field(default_factory=dict)
    is_feasible: bool


class TEPResult(BaseModel):
    """Полный результат расчёта ТЭП по кварталу."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    profile: str

    # Решения
    kit: TEPField
    kit_normative_max: TEPField

    # Жильё
    gfa: TEPField                  # общая площадь жилых зданий
    apartments_area: TEPField      # площадь квартир
    built_in_area: TEPField        # площадь ВПП (0, если ВПП не задано)
    built_in_parking_places: TEPField   # м/м для ВПП по нормативу ВРИ
    built_in_greening_area: TEPField    # озеленение, привязанное к ВПП, м²
    population: TEPField
    population_check_20: TEPField  # население по 20 м²/чел (для проверки плотности)
    density_chel_per_ga: TEPField

    # Соцобъекты
    kindergarten_places_required: TEPField
    kindergarten_places_accepted: TEPField
    kindergarten_plot_area: TEPField
    kindergarten_building_area: TEPField

    school_places_required: TEPField
    school_places_accepted: TEPField
    school_plot_area: TEPField
    school_building_area: TEPField

    # ЗНОП
    znop_per_person: TEPField
    znop_area: TEPField

    # Озеленение
    greening_housing_area: TEPField
    greening_quarter_required: TEPField

    # Парковки — итого и разбивка по типам
    parking_required_places: TEPField   # всего м/м по нормативу
    parking_open_places: TEPField       # открытые в уровне земли
    parking_open_area: TEPField         # площадь открытых, м²
    parking_multilevel_places: TEPField # многоуровневые
    parking_multilevel_objects: TEPField# число объектов МП
    parking_multilevel_area: TEPField   # пятно МП, м²
    parking_underground_places: TEPField# подземные (не занимают surface-площадь)

    # Проезды
    driveways_intra_quarter_area: TEPField
    driveways_housing_lot_area: TEPField

    # Площади территорий
    housing_lot_area: TEPField     # площадь ЗУ жилой застройки
    housing_footprint: TEPField    # площадь застройки

    # Баланс
    balance: BalanceCheck

    # ВРИ-код ВПП (если задано) — строка, не TEPField, поскольку нечисловое значение
    built_in_vri_code: str | None = None

    # Ограничивающий фактор (для обратного расчёта)
    limiting_factor: str | None = None

    warnings: list[str] = Field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"Профиль: {self.profile}",
            f"КИТ:                     {self.kit.value:.3f} (норм. макс {self.kit_normative_max.value})",
            f"Площадь квартир:         {self.apartments_area.value:,.0f} м²",
        ]
        if self.built_in_area.value and self.built_in_area.value > 0:
            lines.append(
                f"ВПП:                     {self.built_in_area.value:,.0f} м² "
                f"(ВРИ {self.built_in_vri_code}), парковки +{self.built_in_parking_places.value} м/м"
            )
        lines += [
            f"Население:               {self.population.value:,.0f} чел",
            f"Плотность:               {self.density_chel_per_ga.value:.1f} чел/га [{self.density_chel_per_ga.status.value}]",
            f"ДОО (мест):              требуется {self.kindergarten_places_required.value} → принято {self.kindergarten_places_accepted.value}",
            f"СОШ (мест):              требуется {self.school_places_required.value} → принято {self.school_places_accepted.value}",
            f"ЗНОП (м²/чел):           {self.znop_per_person.value} → итого {self.znop_area.value:,.0f} м²",
            "Парковки (м/м):",
            f"  всего требуется        {self.parking_required_places.value}",
            f"  открытые               {self.parking_open_places.value} м/м, {self.parking_open_area.value:,.0f} м²",
        ]
        if self.parking_multilevel_places.value and self.parking_multilevel_places.value > 0:
            lines.append(
                f"  многоуровневые         {self.parking_multilevel_places.value} м/м "
                f"в {self.parking_multilevel_objects.value} объект(ах), "
                f"пятно {self.parking_multilevel_area.value:,.0f} м²"
            )
        if self.parking_underground_places.value and self.parking_underground_places.value > 0:
            lines.append(
                f"  подземные              {self.parking_underground_places.value} м/м (без поверхностной площади)"
            )
        lines += [
            f"Баланс:                  {'OK' if self.balance.is_feasible else 'ДЕФИЦИТ'} ({self.balance.surplus:+,.0f} м²)",
        ]
        if self.limiting_factor:
            lines.append(f"Ограничивающий фактор:   {self.limiting_factor}")
        for w in self.warnings:
            lines.append(f"  ⚠ {w}")
        return "\n".join(lines)
