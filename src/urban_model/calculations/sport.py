"""Плоскостные спортивные сооружения (ВРИ 5.1.3, v0.6.8).

Норматив СПб: 1000 м²/1000 чел. На ЗУ требуется озеленение (по ПЗЗ — 40% от
ЗУ для ВРИ 5.1.3). По п. 1.9.4 ПЗЗ СПб открытые спортплощадки могут
оборудоваться в составе озеленения участка — до 50% площади озеленения
(в коде используется коэффициент 0.49 для запаса).

Формула ЗУ:
    sport_area = население × area_per_1000 / 1000
    greening_required = sport_area × greening_ratio (например, ×0.4)
    greening_substituted = greening_required × substitution_max (например, ×0.49)
    greening_extra = greening_required − greening_substituted
    sport_plot = sport_area + greening_extra

Пример (1000 чел, ratio=0.4, substitution=0.49):
    sport_area = 1000 м²
    greening_required = 400 м²
    greening_substituted = 196 м²
    greening_extra = 204 м²
    sport_plot = 1204 м²
"""

from __future__ import annotations

from dataclasses import dataclass

from urban_model.normatives import Normatives


@dataclass(frozen=True)
class SportBreakdown:
    """Разложение площадей для аудит-трейла."""
    sport_area: float           # площадь самих спортплощадок, м²
    greening_required: float    # требуемое озеленение на ЗУ спорта, м²
    greening_substituted: float # часть, замещённая объектами (по п.1.9.4)
    greening_extra: float       # дополнительное озеленение на ЗУ
    plot_area: float            # полный ЗУ = sport_area + greening_extra


def compute(population: float, norms: Normatives) -> SportBreakdown:
    """Расчёт площадей плоскостных спорт. сооружений по жителям."""
    if population <= 0:
        return SportBreakdown(0.0, 0.0, 0.0, 0.0, 0.0)

    area_per_1000 = norms.resolve("sport_facilities.area_per_1000")
    greening_ratio = norms.resolve("sport_facilities.greening_ratio")
    substitution_max = norms.resolve("sport_facilities.greening_substitution_max")

    sport_area = population * area_per_1000 / 1000
    greening_required = sport_area * greening_ratio
    greening_substituted = greening_required * substitution_max
    greening_extra = greening_required - greening_substituted
    plot_area = sport_area + greening_extra
    return SportBreakdown(
        sport_area=sport_area,
        greening_required=greening_required,
        greening_substituted=greening_substituted,
        greening_extra=greening_extra,
        plot_area=plot_area,
    )
