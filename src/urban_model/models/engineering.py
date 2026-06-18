"""Инженерная инфраструктура (v0.12): ТП/РТП/котельная/ГРП/ОСПС/насосная.

Ось — ВРИ 3.1 «Коммунальное обслуживание». Объекты обслуживают квартал и
занимают собственные ЗУ; ранее их площадь была неявно «зашита» в 10% проездов
(теперь проезды = 7.5%, инженерка — отдельный компонент баланса).

`EngineeringSpec` — пользовательское управление: какие объекты учитывать как
изымаемую площадь, тип приготовления пищи (влияет на нагрузку), override
удельных нагрузок. `EngineeringObject` / `EngineeringResult` — результат расчёта.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Ключи объектов в порядке расчёта (зависимости: РТП←ТП, ГРП←котельная,
# насосная←ОСПС). Используются в UI, балансе и экономике как единый словарь.
ENGINEERING_KEYS: tuple[str, ...] = ("tp", "rtp", "boiler", "grp", "osps", "pump")

ENGINEERING_LABELS: dict[str, str] = {
    "tp": "ТП (трансформаторная)",
    "rtp": "РТП (распределительная)",
    "boiler": "Котельная",
    "grp": "ГРП (газорегуляторный)",
    "osps": "ОСПС (очистные пов. стока)",
    "pump": "Насосная станция",
}

Cooking = Literal["electric", "gas"]


class EngineeringSpec(BaseModel):
    """Управление учётом инженерной инфраструктуры.

    По умолчанию все 6 объектов учитываются как изымаемая площадь (входят в
    баланс). Любой объект можно перевести в режим «только потребность»
    (`demand_only`): он по-прежнему считается (кол-во/мощность отображаются),
    но его ЗУ не изымается из баланса и не входит в себестоимость — например,
    ОСПС размещается за пределами квартала или подключение к городским сетям.
    """

    model_config = ConfigDict(extra="forbid")

    # Объекты, чьё ЗУ НЕ изымается из баланса (только расчёт потребности).
    # Ключи из ENGINEERING_KEYS. По умолчанию пусто = все занимают площадь.
    demand_only: list[str] = Field(
        default_factory=list,
        description="Ключи объектов в режиме «только потребность» (ЗУ не в балансе)",
    )

    # Приготовление пищи: электроплиты (текущая логика) / газовые плиты.
    # Газ меняет нагрузку на ТП (ниже электрическая) и на газоснабжение.
    # ДЕМО: газовый коэффициент в YAML пока равен электрическому (хук на будущее).
    cooking: Cooking = Field(
        default="electric",
        description="Тип плит: electric (текущий режим) / gas (демо-хук)",
    )

    # Override удельных нагрузок (None → норматив YAML).
    q_heat_override: float | None = Field(
        default=None, ge=0.0, description="Удельная тепловая нагрузка, кВт/м² (None=норматив)"
    )
    q_water_override: float | None = Field(
        default=None, ge=0.0, description="Водоотведение на жителя, л/сут (None=норматив)"
    )

    def is_demand_only(self, key: str) -> bool:
        return key in self.demand_only


class EngineeringObject(BaseModel):
    """Один тип инженерного объекта в результате расчёта."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    label: str
    count: int = 0
    capacity: float | None = None      # мощность ОДНОГО объекта (МВт / м³·сут)
    capacity_unit: str | None = None   # "МВт" / "м³/сут" / None
    plot_each: float = 0.0             # ЗУ одного объекта, м²
    plot_total: float = 0.0            # ЗУ всех объектов этого типа, м²
    in_balance: bool = True            # изымает ли площадь (False = только потребность)
    note: str | None = None


class EngineeringResult(BaseModel):
    """Сводный результат по инженерной инфраструктуре квартала."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    objects: list[EngineeringObject] = Field(default_factory=list)
    # Площадь, входящая в баланс (только объекты с in_balance=True).
    plot_in_balance: float = 0.0
    # Полная площадь всех объектов (вкл. «только потребность») — справочно.
    plot_total_all: float = 0.0
    cooking: Cooking = "electric"
