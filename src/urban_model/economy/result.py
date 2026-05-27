"""Dataclasses для экономических метрик (v0.8.0)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CostBreakdown(BaseModel):
    """Детализированная себестоимость проекта в условных единицах."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    # Себестоимость зданий по типам
    residential: float = Field(0.0, description="Жилые здания, GFA × C_base × коэф")
    vpp: float = Field(0.0, description="ВПП (встроенно-пристроенные)")
    kindergarten: float = Field(0.0, description="Здания ДОО")
    school: float = Field(0.0, description="Здания СОШ")
    parking_open: float = Field(0.0, description="Открытые парковки (пятно)")
    parking_multilevel: float = Field(0.0, description="Многоуровневые наземные паркинги")
    parking_underground: float = Field(0.0, description="Подземные паркинги (с прогрессией уровней)")

    # AUDIT P0-6: ранее экономика игнорировала эти статьи.
    social_parking: float = Field(0.0, description="Парковки ДОО/СОШ (открытые на ЗУ соцобъекта)")
    sport: float = Field(0.0, description="Плоскостные спорт. сооружения (ВРИ 5.1.3)")
    custom_objects: float = Field(0.0, description="Кастомные объекты (офис/ФОК/поликлиника)")

    # Подытоги
    shell_total: float = Field(0.0, description="Σ зданий и сооружений")
    networks: float = Field(0.0, description="Сети (% от shell)")
    landscaping: float = Field(0.0, description="Благоустройство (% от shell)")
    design: float = Field(0.0, description="Проектирование/изыскания (% от shell)")
    contingency: float = Field(0.0, description="Непредвиденные (% от shell+networks+landscape+design)")
    fixed: float = Field(0.0, description="Земля, ТУ, снос (зашиты в опции)")

    total: float = Field(0.0, description="Итого = shell + overhead + fixed")


class RevenueBreakdown(BaseModel):
    """Детализированная выручка от продажи в условных единицах."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    residential: float = Field(0.0, description="Площадь квартир × цена/м² по классу")
    parking_open: float = Field(0.0, description="Открытые м/м × цена/м.м.")
    parking_multilevel: float = Field(0.0, description="Многоуровневые м/м × цена/м.м.")
    parking_underground: float = Field(0.0, description="Подземные м/м × цена/м.м.")
    vpp_commercial: float = Field(0.0, description="Площадь ВПП × цена/м² коммерции")
    # AUDIT P0-6: коммерческие кастомные объекты дают выручку. Соцобъекты
    # (ДОО/СОШ/спорт) — соцнагрузка, выручка = 0.
    custom_commercial: float = Field(0.0, description="Кастомные коммерческие объекты")
    # v0.9.14: компенсация ДОО/СОШ городом (выкуп / КОТ / бюджетные субсидии).
    # Не «продажа», но фактический денежный поток к застройщику.
    social_compensation: float = Field(
        0.0,
        description="Компенсация ДОО/СОШ городом (доля их себестоимости)",
    )

    total: float = Field(0.0, description="Σ всех источников выручки")


class EconomicMetrics(BaseModel):
    """Итоговые экономические метрики проекта."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    cost: CostBreakdown
    revenue: RevenueBreakdown
    profit: float = Field(0.0, description="revenue.total − cost.total")
    margin: float = Field(0.0, description="profit / revenue (0 если revenue=0)")
    roi: float = Field(0.0, description="profit / cost (0 если cost=0)")
    profit_per_site_m2: float = Field(
        0.0, description="profit / site.area_m2 — основная метрика ранжирования"
    )

    # Источник единиц — для аудита
    units_label: str = Field(
        default="у.е. (1.0 = м² жилья 9эт. монолит standard)",
        description="Подпись единиц для UI/отчётов",
    )
