"""Амбулаторно-поликлинические учреждения (ВРИ 3.4.1) — v0.12.28.

Норматив НГП СПб + СП 158.13330.2014:
  - потребность: 26.33 посещения в смену на 1000 жителей;
  - работники = посещения / 6 (условно);
  - размещение по порогу 150 посещений (условно):
      < 150 → ВПП («офис врача общей практики»): здание 8 м²/посещ. из жилой
              GFA; 1 объект ≤ 100 посещений → дробление на N объектов;
      ≥ 150 → отдельно стоящая поликлиника: ЗУ 10 м²/посещ. (мин. 2000 м²) +
              15% озеленения; здание 23 м²/посещ. (условно).
  - парковка: 1 м/м на 5 работников + 1 м/м на 40 посетителей, не менее 2;
  - высота ≤ 28 м.

Чистые функции; размещение/баланс — на стороне forward.py (как у ДОО/СОШ).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from urban_model.calculations.rounding import round_up_to_multiple
from urban_model.normatives import Normatives


@dataclass(frozen=True)
class PolyclinicResult:
    """Сводка по поликлинике / офису врача общей практики."""
    visits: int                 # принятые посещения в смену
    workers: int                # работники = посещения / 6
    built_in: bool              # True → ВПП (офис врача в составе жилого дома)
    plot_area: float            # ЗУ, м² (только отд. стоящее; ВПП → 0)
    building_area: float        # площадь здания, м²
    greening_required: float    # 15% ЗУ (только отд. стоящее), м²
    parking_places: int         # м/м
    n_objects: int              # число объектов (ВПП >100 → дробление)
    threshold: int              # порог 150 посещений (для предупреждений)


def required_visits(population: float, visits_per_1000: float) -> float:
    return population * visits_per_1000 / 1000


def compute(
    population: float,
    norms: Normatives,
    *,
    mode: str = "norm",
    visits_override: int | None = None,
    force_vpp: bool = False,
) -> PolyclinicResult:
    """Рассчитать поликлинику от населения (или ручного числа посещений).

    Args:
        population: население квартала.
        norms: нормативы.
        mode: "norm" — посещения по нормативу; "manual" — задано вручную.
        visits_override: число посещений в ручном режиме.
        force_vpp: разместить в ВПП (офис врача) принудительно. В ВПП 1 объект
                   ≤ 100 посещений → при большем числе дробится на N объектов.
    """
    base = "social_objects.polyclinic"
    per_1000 = norms.resolve(f"{base}.visits_per_1000")
    rounding = norms.resolve(f"{base}.rounding")
    workers_ratio = norms.resolve(f"{base}.workers_ratio")
    threshold = int(norms.resolve(f"{base}.built_in_threshold"))
    obj_max = int(norms.resolve(f"{base}.vpp_object_max"))
    bld_vpp = norms.resolve(f"{base}.building_area_per_visit_vpp")
    bld_det = norms.resolve(f"{base}.building_area_per_visit_detached")
    plot_per = norms.resolve(f"{base}.plot_per_visit")
    plot_min = norms.resolve(f"{base}.plot_min")
    greening_share = norms.resolve(f"{base}.greening_share")
    per_worker = norms.resolve(f"{base}.parking_per_worker")
    per_visitor = norms.resolve(f"{base}.parking_per_visitor")
    park_min = int(norms.resolve(f"{base}.parking_min"))

    if mode == "manual" and visits_override is not None:
        visits = int(round_up_to_multiple(visits_override, int(rounding)))
    else:
        visits = int(round_up_to_multiple(
            required_visits(population, per_1000), int(rounding)
        ))

    if visits <= 0:
        return PolyclinicResult(0, 0, False, 0.0, 0.0, 0.0, 0, 0, threshold)

    workers = max(1, round(visits / workers_ratio))

    # Размещение: ВПП если форсировано ИЛИ посещений < порога; иначе отд. стоящее.
    built_in = bool(force_vpp) or (visits < threshold)

    if built_in:
        # ВПП: 1 объект (офис врача) ≤ obj_max посещений → дробление.
        n_objects = max(1, math.ceil(visits / obj_max))
        building_area = bld_vpp * visits
        plot_area = 0.0
        greening_required = 0.0
    else:
        n_objects = 1
        building_area = bld_det * visits
        plot_area = max(plot_min, plot_per * visits)
        greening_required = greening_share * plot_area

    # Парковка: 1 м/м на 5 работников + 1 м/м на 40 посетителей, не менее 2.
    parking_places = max(
        park_min,
        math.ceil(workers / per_worker) + math.ceil(visits / per_visitor),
    )

    return PolyclinicResult(
        visits=visits,
        workers=workers,
        built_in=built_in,
        plot_area=plot_area,
        building_area=building_area,
        greening_required=greening_required,
        parking_places=parking_places,
        n_objects=n_objects,
        threshold=threshold,
    )
