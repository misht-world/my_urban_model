"""Параметры расчёта (то, что пользователь может крутить, не меняя нормативы)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from urban_model.models.built_in import BuiltInArea
from urban_model.models.custom_object import CustomObject
from urban_model.models.parking import ParkingConfig
from urban_model.models.social import KindergartenSpec, SchoolSpec, SportFacilitiesSpec


class CalculationOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Параметры жилья
    floors: int = Field(default=10, ge=1, description="этажность жилья")
    planning_doc: bool = True  # ППТ → КИТ_max = 2.5; иначе 1.4

    # Встроенно-пристроенные помещения (single, legacy от v0.2).
    # С v0.7.1 рекомендуется `built_in_list` для нескольких ВПП с разными ВРИ.
    # Если задано — площадь вычитается из GFA, считаются парковки/озеленение.
    # Если None — используется legacy-параметр vpp_share.
    built_in: BuiltInArea | None = None

    # Список ВПП (v0.7.1): несколько помещений с разными ВРИ в одном жилом доме.
    # При расчёте forward.py объединяет single `built_in` и `built_in_list`
    # в один итерируемый список. Использовать любой из вариантов (или оба).
    built_in_list: list[BuiltInArea] = Field(
        default_factory=list,
        description="Список ВПП в составе жилого дома (несколько помещений с разными ВРИ)",
    )

    # Legacy: плоская доля ВПП в GFA. Игнорируется при заданном built_in/_list.
    vpp_share: float = Field(default=0.0, ge=0.0, le=0.5)

    # Соцобъекты — учитывать или нет
    include_kindergarten: bool = True
    include_school: bool = True
    # Прочие компоненты баланса — учитывать или нет (v0.6.7).
    # По умолчанию все включены (стандартный сценарий). Выключают, когда
    # компонент размещается за пределами квартала: парковки — на стоянке-
    # спутнике; ЗНОП — на смежной зелёной зоне; внутриквартальные проезды —
    # отсутствуют (плотная городская застройка с уличными проездами).
    include_parking: bool = Field(default=True, description="Учитывать парковки в балансе")
    include_znop: bool = Field(default=True, description="Учитывать ЗНОП в балансе и озеленении")
    include_intra_driveways: bool = Field(
        default=True, description="Учитывать внутриквартальные проезды в балансе"
    )

    # v0.8.7: «мягкие» нормативы — пользователь может отключить проверку.
    # На малых кварталах (< 0.5 га) норматив 25% озеленения и норматив
    # плотности 450 чел/га физически противоречивы, бисекция падает в
    # kit_search_min. Отключение даёт пользователю режим «физический
    # потолок» — модель максимизирует КИТ только в рамках территории.
    enforce_quarter_greening_norm: bool = Field(
        default=True,
        description=(
            "Соблюдать норматив 25% озеленения квартала "
            "(СП 42.13330). Отключите для малых кварталов или при компенсации "
            "озеленения вне границ территории."
        ),
    )
    enforce_density_norm: bool = Field(
        default=True,
        description=(
            "Соблюдать норматив плотности 450 чел/га (по 20 м²/чел). "
            "Отключите для малых кварталов или экспериментальных сценариев."
        ),
    )

    kindergarten: KindergartenSpec = Field(default_factory=KindergartenSpec)
    school: SchoolSpec = Field(default_factory=SchoolSpec)

    # Плоскостные спортивные сооружения (ВРИ 5.1.3, v0.6.8) — по умолчанию включены.
    include_sport_facilities: bool = Field(
        default=True, description="Учитывать плоскостные спорт. сооружения в балансе"
    )
    sport_facilities: SportFacilitiesSpec = Field(default_factory=SportFacilitiesSpec)

    # Парковочный сценарий
    parking: ParkingConfig = Field(
        default_factory=ParkingConfig,
        description=(
            "Конфигурация парковок: mode='min_open' (по умолчанию) / "
            "'all_open' / 'custom' с долями open/multilevel/underground"
        ),
    )

    # Экономика (v0.8.0): класс жилья влияет на цену продажи м² квартир.
    # Конструктив и отделка — пока дефолты из YAML (monolith / standard).
    residential_class: Literal["economy", "comfort", "business"] = Field(
        default="comfort",
        description="Класс жилья — влияет на цену продажи м² квартир.",
    )

    # Бисекция КИТ
    kit_search_min: float = 0.1
    kit_search_max: float | None = None  # если None — берём из норматива
    kit_tolerance: float = 0.001

    # Override для ЗНОП (м²/чел). Если задано — заменяет нормативную piecewise(КИТ).
    # Используется в режиме solve_max_kit_with_znop.
    znop_per_person_override: float | None = Field(
        default=None,
        ge=0.0,
        description="Принудительный ЗНОП в м²/чел; None = по нормативу (piecewise по КИТ)",
    )

    # v0.6: альтернатива — фиксированная общая площадь ЗНОП в м² (не зависит
    # от населения). Приоритет: znop_total_area_override > znop_per_person_override
    # > норматив. Удобно, когда проектировщик задаёт «у меня выделено N га ЗНОП».
    znop_total_area_override: float | None = Field(
        default=None,
        ge=0.0,
        description="Принудительная общая площадь ЗНОП в м²; None = по другому правилу",
    )

    # Кастомные объекты на территории (офис, ФОК, поликлиника и т.п.).
    # Каждый занимает свою площадь (вычитается из доступной территории),
    # требует парковок и даёт озеленение по ВРИ-коду.
    custom_objects: list[CustomObject] = Field(
        default_factory=list,
        description="Список произвольных объектов на территории квартала",
    )

    # Override долей проездов. None = по нормативу (значения из YAML).
    # Используется когда пользователь хочет проверить «что если бы доля
    # проездов была другая» на конкретном проекте.
    driveways_intra_share_override: float | None = Field(
        default=None,
        ge=0.0,
        le=0.5,
        description="Доля внутриквартальных проездов от S_квартала. None = норматив (0.10 СПб)",
    )
    driveways_lot_share_override: float | None = Field(
        default=None,
        ge=0.0,
        le=3.0,
        description="Доля проездов на ЗУ жилой застройки от S_застройки. None = норматив (1.20 СПб)",
    )
