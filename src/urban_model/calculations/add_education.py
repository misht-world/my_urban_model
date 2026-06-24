"""Организации дополнительного образования детей (ВРИ 3.5.1) — v0.12.15.

Норматив РМД 15-26-2017 + НГП СПб:
  - потребность: 65 мест на 1000 жителей;
  - работники = места / 6 (условно);
  - площадь здания: 17 м²/место (и для ВПП, и для отдельно стоящего);
  - размещение по порогу 150 мест:
      < 150 → встроенно-пристроенное (ВПП): здание вычитается из жилой GFA;
      ≥ 150 → отдельно стоящее: ЗУ 15 м²/место + 30% озеленения ЗУ.
  - парковка по общей формуле parking.social_objects (как ДОУ/СОШ);
  - до 4 этажей.

Чистые функции: на вход население/нормативы, на выход — AddEducationResult.
Размещение/баланс — на стороне forward.py (как у ДОО/СОШ).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from urban_model.calculations.rounding import round_up_to_multiple
from urban_model.calculations.social_parking import parking_for_object
from urban_model.normatives import Normatives


@dataclass(frozen=True)
class AddEducationResult:
    """Сводка по объекту доп. образования."""
    places: int                 # принятые места (вверх кратно rounding)
    workers: int                # работники = места / 6
    built_in: bool              # True → ВПП (здание в составе жилого дома)
    plot_area: float            # ЗУ, м² (только отд. стоящее; ВПП → 0)
    building_area: float        # площадь здания, м² (17 × места)
    greening_required: float    # 30% ЗУ (только отд. стоящее), м²
    parking_places: int         # м/м по формуле соцобъектов
    n_objects: int              # число объектов (ВПП >порога → дробится на N)
    threshold: int              # порог 150 мест (для предупреждений)


def required_places(population: float, places_per_1000: float) -> float:
    return population * places_per_1000 / 1000


def compute(
    population: float,
    norms: Normatives,
    *,
    mode: str = "norm",
    places_override: int | None = None,
    force_vpp: bool = False,
) -> AddEducationResult:
    """Рассчитать объект доп. образования от населения (или ручного числа мест).

    Args:
        population: население квартала.
        norms: нормативы.
        mode: "norm" — места по нормативу (65/1000); "manual" — число задано.
        places_override: число мест в ручном режиме.
        force_vpp: разместить в ВПП (встроенно-пристроенное) принудительно.
                   Работает в любом режиме. При местах ≥ порога объект делится
                   на несколько встроенных объектов (порог 150 перестаёт быть
                   ограничением).

    Размещение:
        built_in = force_vpp ИЛИ места < порога.
        Если built_in и мест ≥ порога → дробление на ceil(мест/порог) объектов.
        Иначе (мест ≥ порога, без force_vpp) → отдельно стоящее.
    """
    per_1000 = norms.resolve("social_objects.add_education.places_per_1000")
    rounding = norms.resolve("social_objects.add_education.rounding")
    workers_ratio = norms.resolve("social_objects.add_education.workers_ratio")
    threshold = int(norms.resolve("social_objects.add_education.built_in_threshold"))
    bld_per_place = norms.resolve("social_objects.add_education.building_area_per_place")
    plot_per_place = norms.resolve("social_objects.add_education.plot_area_per_place")
    greening_share = norms.resolve("social_objects.add_education.greening_share")

    if mode == "manual" and places_override is not None:
        places = int(round_up_to_multiple(places_override, int(rounding)))
    else:
        places = int(round_up_to_multiple(
            required_places(population, per_1000), int(rounding)
        ))

    if places <= 0:
        return AddEducationResult(0, 0, False, 0.0, 0.0, 0.0, 0, 0, threshold)

    workers = max(1, round(places / workers_ratio))

    # Размещение: ВПП если форсировано ИЛИ мест < порога; иначе отд. стоящее.
    built_in = bool(force_vpp) or (places < threshold)

    # Дробление встроенного объекта на несколько ВПП, когда мест ≥ порога.
    if built_in and places >= threshold:
        n_objects = max(2, math.ceil(places / threshold))
    else:
        n_objects = 1

    building_area = bld_per_place * places
    if built_in:
        plot_area = 0.0
        greening_required = 0.0
    else:
        plot_area = plot_per_place * places
        greening_required = greening_share * plot_area

    parking_places = parking_for_object(workers, places, norms)

    return AddEducationResult(
        places=places,
        workers=workers,
        built_in=built_in,
        plot_area=plot_area,
        building_area=building_area,
        greening_required=greening_required,
        parking_places=parking_places,
        n_objects=n_objects,
        threshold=threshold,
    )
