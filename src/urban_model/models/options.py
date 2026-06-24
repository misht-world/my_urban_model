"""Параметры расчёта (то, что пользователь может крутить, не меняя нормативы)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from urban_model.models.built_in import BuiltInArea
from urban_model.models.cluster import FloorCluster
from urban_model.models.custom_object import CustomObject
from urban_model.models.engineering import EngineeringSpec
from urban_model.models.parking import ParkingConfig
from urban_model.models.social import (
    AdditionalEducationSpec,
    KindergartenSpec,
    PolyclinicSpec,
    SchoolSpec,
    SportFacilitiesSpec,
)


class CalculationOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Параметры жилья
    floors: int = Field(default=10, ge=1, description="этажность жилья")
    planning_doc: bool = True  # ППТ → КИТ_max = 2.5; иначе 1.4

    # Кластеры этажности (v0.9.28): квартал делится на подучастки со своей
    # высотностью (зоны ПЗЗ). Пусто → единая этажность `floors` (1 кластер).
    # Когда задано — баланс/озеленение считаются по floors_eff = Σ(A·F)/ΣA,
    # экономика и проезды — покластерно, КИТ — покластерно с поджатием потолка.
    floor_clusters: list[FloorCluster] = Field(
        default_factory=list,
        description="Подучастки квартала с разной этажностью (до 3 шт.)",
    )

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
    # v0.8.8: ЗНОП «только потребность» — площадь рассчитывается, отображается
    # в результате, но НЕ входит в баланс и НЕ засчитывается в озеленение
    # квартала. Симметрично KindergartenSpec.only_demand / SchoolSpec.only_demand.
    znop_only_demand: bool = Field(
        default=False,
        description=(
            "ЗНОП только для расчёта потребности: площадь считается и "
            "показывается, но не входит в баланс и не зачитывается в "
            "норматив озеленения квартала."
        ),
    )
    include_intra_driveways: bool = Field(
        default=True, description="Учитывать внутриквартальные проезды в балансе"
    )

    # Инженерная инфраструктура (v0.12): ТП/РТП/котельная/ГРП/ОСПС/насосная.
    # По умолчанию включено — объекты считаются и изымают площадь из баланса.
    # Тонкая настройка (какие объекты «только потребность», тип плит, override
    # нагрузок) — в `engineering`.
    include_engineering: bool = Field(
        default=True, description="Учитывать инженерную инфраструктуру в балансе"
    )
    engineering: EngineeringSpec = Field(default_factory=EngineeringSpec)

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

    # Организации доп. образования (ВРИ 3.5.1, v0.12.15) — вынесены из ВПП в
    # отдельный объект. По умолчанию включены (как ДОО/СОШ).
    include_add_education: bool = Field(
        default=True, description="Учитывать организации доп. образования (ВРИ 3.5.1)"
    )
    add_education: AdditionalEducationSpec = Field(
        default_factory=AdditionalEducationSpec
    )

    # Амбулаторно-поликлинические учреждения (ВРИ 3.4.1, v0.12.28) — вынесены
    # из обязательных ВПП в отдельный объект.
    include_polyclinic: bool = Field(
        default=True, description="Учитывать поликлинику (ВРИ 3.4.1)"
    )
    polyclinic: PolyclinicSpec = Field(default_factory=PolyclinicSpec)

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
    # v0.9.29: include_economy=False — экономика не считается (result.economy=None),
    # и UI скрывает все экономические показатели на всех вкладках.
    include_economy: bool = Field(
        default=True, description="Считать экономику (оценку выгодности)"
    )
    # Конструктив и отделка — пока дефолты из YAML (monolith / standard).
    residential_class: Literal["economy", "comfort", "business"] = Field(
        default="comfort",
        description="Класс жилья — влияет на цену продажи м² квартир.",
    )

    # v0.12.27: за чей счёт соцобъекты (ДОО/СОШ/доп.обр здания + соц-парковки).
    #   compensated — город компенсирует долю себестоимости (share, дефолт 0.7);
    #   developer   — полностью застройщик (компенсация 0);
    #   city        — полностью город (себестоимость соц-зданий = 0 у застройщика);
    #   at_cost     — передача по себестоимости (компенсация = 100%, нейтрально).
    social_funding: Literal["compensated", "developer", "city", "at_cost"] = Field(
        default="compensated",
        description="За чей счёт соцобъекты: город % / застройщик / город / по себестоимости",
    )
    # Доля компенсации города для режима 'compensated'. None → норматив из YAML (0.7).
    social_compensation_share: float | None = Field(
        default=None, ge=0.0, le=1.0,
        description="Доля компенсации города (режим compensated); None = норматив YAML",
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

    # --- Кластеры: удобные производные (без зависимости от calculations) ---
    # NB: дублирует `calculations.clusters.effective_floors` намеренно —
    # models не может импортировать calculations (нижний слой). Формула одна.
    def effective_floors(self) -> float:
        """Средневзвешенная этажность Σ(A·F)/ΣA; `floors` если кластеров нет."""
        if not self.floor_clusters:
            return float(self.floors)
        total_a = sum(c.area_m2 for c in self.floor_clusters)
        if total_a <= 0:
            return float(self.floors)
        return sum(c.area_m2 * c.floors for c in self.floor_clusters) / total_a
