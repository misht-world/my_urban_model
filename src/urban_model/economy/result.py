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
    add_education: float = Field(0.0, description="Здание доп. образования (ВРИ 3.5.1)")
    polyclinic: float = Field(0.0, description="Здание поликлиники (ВРИ 3.4.1)")
    parking_open: float = Field(0.0, description="Открытые парковки (пятно)")
    parking_multilevel: float = Field(0.0, description="Многоуровневые наземные паркинги")
    parking_underground: float = Field(0.0, description="Подземные паркинги (с прогрессией уровней)")
    parking_stylobate: float = Field(0.0, description="Стилобатные паркинги (дека над землёй)")

    # AUDIT P0-6: ранее экономика игнорировала эти статьи.
    social_parking: float = Field(0.0, description="Парковки ДОО/СОШ (открытые на ЗУ соцобъекта)")
    sport: float = Field(0.0, description="Плоскостные спорт. сооружения (ВРИ 5.1.3)")
    custom_objects: float = Field(0.0, description="Кастомные объекты (офис/ФОК/поликлиника)")
    engineering: float = Field(0.0, description="Инженерная инфраструктура (ТП/котельная/ОСПС…)")

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
    parking_stylobate: float = Field(0.0, description="Стилобатные м/м × цена × доля реализации")
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
        0.0, description="profit / site.area_m2 — «Запас проекта / м²», основная метрика ранжирования"
    )
    # v0.12.1: стабильный экономический индекс = 100 × выручка / себестоимость.
    # 100 = окупаемость; >100 эффективнее; <100 ниже. Не зависит от пула Optuna
    # (в отличие от min-max-нормировки) → стабилен между запусками. Headline-
    # метрика для UI вместо сырой прибыли в у.е. (которая может быть «в минус»).
    economy_index: float = Field(
        0.0, description="100 × выручка / себестоимость (100 = окупаемость)"
    )

    # v0.9.14: разделение социальной нагрузки.
    # net_social_burden = (cost ДОО+СОШ+соц.парк) − компенсация города.
    #   >0 — соцобъекты в минус (типично); <0 — компенсация перекрыла затраты.
    # profit_before_social = profit + net_social_burden — прибыль проекта
    #   без учёта социальных обязательств (показывает «чистый» девелопмент).
    net_social_burden: float = Field(
        0.0, description="Себестоимость соцобъектов − компенсация города"
    )
    profit_before_social: float = Field(
        0.0, description="Прибыль без социальной нагрузки = profit + net_social_burden"
    )
    # profit_before_land = revenue − (все затраты КРОМЕ земли). Пока fixed=0,
    # совпадает с profit; поле зарезервировано под ввод стоимости земли (v0.9.16).
    profit_before_land: float = Field(
        0.0, description="Прибыль до вычета стоимости земли = profit + cost.fixed"
    )
    # Доля продаваемого жилья = площадь квартир / общая GFA (выход жилья).
    sellable_ratio: float = Field(
        0.0, description="Площадь квартир / общая GFA (выход жилья)"
    )

    # Источник единиц — для аудита
    units_label: str = Field(
        default="баллы выгодности (1.0 ≈ м² жилья 9эт. монолит standard)",
        description="Подпись единиц для UI/отчётов",
    )
