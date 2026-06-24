"""TEPField и TEPResult — структурированный результат расчёта.

Каждое поле несёт аудит-трейл: значение, нормативное, пользовательское,
статус, источник и формулу. См. CLAUDE.md → "TEPResult — структура".
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from urban_model.models.engineering import EngineeringResult

# P0-4: ленивый импорт EconomicMetrics. economy/result.py не импортирует
# ничего из models, поэтому прямой импорт работает, но создаёт жёсткую
# связность: поломка в economy уронит весь TEPResult. TYPE_CHECKING-импорт
# + строковая аннотация поля + model_rebuild() в конце развязывает это.
if TYPE_CHECKING:
    from urban_model.economy.result import EconomicMetrics


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

    # Контроль норматива озеленения квартала: фактическое vs требуемое
    # (норматив 25% от площади квартала без участков ДОО/СОШ — СП 42.13330).
    # is_feasible учитывает оба ограничения: территориальный баланс И озеленение.
    greening_actual: float = 0.0      # фактическое озеленение, м² (ЗНОП + жильё + ВПП)
    greening_required: float = 0.0    # минимально требуемое, м²

    @property
    def greening_deficit(self) -> float:
        """Сколько м² озеленения не хватает; 0 если норматив выполнен."""
        return max(0.0, self.greening_required - self.greening_actual)


class TEPResult(BaseModel):
    """Полный результат расчёта ТЭП по кварталу."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    profile: str

    # Решения
    # `kit` — это КИТ по ПЗЗ СПб = площадь квартир / площадь ЗУ жилой застройки.
    # Этот же КИТ применяется к нормативам (kit_max, ЗНОП piecewise).
    kit: TEPField
    # `block_density` — внутренняя «плотность квартала» = GFA / S_квартала.
    # Используется как переменная бисекции; не путать с КИТ ПЗЗ.
    block_density: TEPField
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

    # Организации доп. образования (ВРИ 3.5.1, v0.12.15). Defaults — для старых
    # конструкторов/тестов ядра. built_in отмечает размещение в ВПП.
    add_education_places_required: TEPField = Field(
        default_factory=lambda: TEPField(value=0, unit="мест")
    )
    add_education_places_accepted: TEPField = Field(
        default_factory=lambda: TEPField(value=0, unit="мест")
    )
    add_education_plot_area: TEPField = Field(
        default_factory=lambda: TEPField(value=0.0, unit="m2")
    )
    add_education_building_area: TEPField = Field(
        default_factory=lambda: TEPField(value=0.0, unit="m2")
    )
    add_education_parking_places: TEPField = Field(
        default_factory=lambda: TEPField(value=0, unit="м/м")
    )
    add_education_built_in: bool = False

    # Амбулаторно-поликлинические учреждения (ВРИ 3.4.1, v0.12.28).
    polyclinic_visits_required: TEPField = Field(
        default_factory=lambda: TEPField(value=0, unit="посещ.")
    )
    polyclinic_visits_accepted: TEPField = Field(
        default_factory=lambda: TEPField(value=0, unit="посещ.")
    )
    polyclinic_plot_area: TEPField = Field(
        default_factory=lambda: TEPField(value=0.0, unit="m2")
    )
    polyclinic_building_area: TEPField = Field(
        default_factory=lambda: TEPField(value=0.0, unit="m2")
    )
    polyclinic_parking_places: TEPField = Field(
        default_factory=lambda: TEPField(value=0, unit="м/м")
    )
    polyclinic_built_in: bool = False

    # Плоскостные спортивные сооружения (v0.6.8)
    sport_facilities_area: TEPField           # площадь самих спортплощадок, м²
    sport_facilities_plot_area: TEPField      # полный ЗУ (sport + extra greening), м²
    sport_facilities_greening_required: TEPField  # требуемое озеленение по ПЗЗ
    sport_facilities_greening_extra: TEPField     # доп. озеленение после substitution

    # Парковки соцобъектов (v0.7.0) — ПЗЗ СПб
    social_parking_total: TEPField        # всего м/м для ДОО + СОШ
    social_parking_kindergarten: TEPField # м/м для всех ДОО
    social_parking_school: TEPField       # м/м для всех СОШ
    social_parking_area: TEPField         # площадь парковок соцобъектов на квартале, м²

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
    # v0.12.2: стилобат — поднятый паркинг, не занимает ЗУ квартала; 25% деки
    # под домами (−1 этаж жилья), 75% — двор (озеленение ≤70% по ПЗЗ).
    parking_stylobate_places: TEPField = Field(
        default_factory=lambda: TEPField(value=0, unit="м/м")
    )
    parking_stylobate_area: TEPField = Field(
        default_factory=lambda: TEPField(value=0.0, unit="m2")
    )

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

    # Кластеры этажности (v0.9.28). None / [] — единая этажность (1 кластер).
    # effective_floors — средневзвешенная этажность для баланса/озеленения.
    # floor_clusters_detail — покластерная разбивка (площадь, этажи, КИТ_i,
    # GFA_i, площадь квартир_i, пятно_i, себестоимость жилья_i).
    effective_floors: float | None = None
    floor_clusters_detail: list[dict[str, Any]] = Field(default_factory=list)

    # Инженерная инфраструктура (v0.12). None — не считалась (старые расчёты /
    # unit-тесты ядра). При include_engineering=False объекты считаются, но
    # plot_in_balance=0.
    engineering: "EngineeringResult | None" = None

    # Ограничивающий фактор (для обратного расчёта)
    limiting_factor: str | None = None

    warnings: list[str] = Field(default_factory=list)

    # Экономика (v0.8.0) — None допустимо для unit-тестов чистых расчётов.
    economy: "EconomicMetrics | None" = None

    def summary(self) -> str:
        lines = [
            f"Профиль: {self.profile}",
            f"КИТ (ПЗЗ):               {self.kit.value:.3f} (норм. макс {self.kit_normative_max.value})",
            f"Плотность квартала:      {self.block_density.value:.3f} (внутр., GFA/S_кв)",
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
        # v0.8.6: префиксы [CODE] прячем; они для машинной фильтрации.
        from urban_model.calculations.warning_codes import strip_code
        for w in self.warnings:
            lines.append(f"  ⚠ {strip_code(w)}")
        return "\n".join(lines)


# P0-4: разрешаем forward-ref `EconomicMetrics`. Импорт выполняется здесь
# (после объявления TEPResult), чтобы избежать круговых зависимостей и
# сохранить «ленивость» — если economy сломан, TEPResult всё равно
# импортируется (рухнет только rebuild при первом использовании).
def _rebuild_with_economy() -> None:
    from urban_model.economy.result import EconomicMetrics  # noqa: F401
    TEPResult.model_rebuild()


try:
    _rebuild_with_economy()
except Exception:  # pragma: no cover — деградация без экономики
    pass
