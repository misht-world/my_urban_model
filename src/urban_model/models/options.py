"""Параметры расчёта (то, что пользователь может крутить, не меняя нормативы)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from urban_model.models.built_in import BuiltInArea
from urban_model.models.custom_object import CustomObject
from urban_model.models.parking import ParkingConfig
from urban_model.models.social import KindergartenSpec, SchoolSpec


class CalculationOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Параметры жилья
    floors: int = Field(default=10, ge=1, description="этажность жилья")
    planning_doc: bool = True  # ППТ → КИТ_max = 2.5; иначе 1.4

    # Встроенно-пристроенные помещения. Если задано — площадь вычитается из GFA,
    # для неё считаются собственные парковки (по ВРИ) и озеленение.
    # Если None — используется legacy-параметр vpp_share.
    built_in: BuiltInArea | None = None

    # Legacy: плоская доля ВПП в GFA. Игнорируется при заданном built_in.
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
