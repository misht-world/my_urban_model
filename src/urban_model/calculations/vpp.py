"""Расчёт обязательных ВПП по НГП СПб и распределение площадей по 5 вариантам.

Норматив задаёт «объём потребности» (мест/посещ./м²) на 1000 жителей.
Для не-площадных ВРИ нужен коэф пересчёта в м² (m2_per_seat, m2_per_workplace
и т.д.) — задаётся в `spb.yaml` секция `mandatory_vpp`.

5 вариантов размещения:
  1. full_floor   — весь 1 этаж = ВПП: min для 3.3, остаток
                    между 4.4 и 4.6 (с обязательным минимумом + extra/2)
  2. half_floor   — то же, но footprint × 0.5
  3. min_only     — только обязательный минимум по всем 5 ВРИ
  4. min_plus     — min всех 5 + опциональные 4.4 и/или 4.6 пользователя
  5. custom_only  — только пользовательские 4.4 и/или 4.6 (без min)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

from urban_model.models.built_in import BuiltInArea
from urban_model.normatives import Normatives


VppMode = Literal["full_floor", "half_floor", "min_only", "min_plus", "custom_only"]


@dataclass
class MandatoryVppAreas:
    """Обязательные площади ВПП на 1000 жителей × текущая population.

    v0.12.15: ВРИ 3.5.1 (доп. образование) вынесен в отдельный соцобъект.
    v0.12.28: ВРИ 3.4.1 (поликлиника) вынесен в `social_objects.polyclinic`.
    В обязательных ВПП осталось 3 ВРИ: 4.4 / 4.6 / 3.3.
    """
    shopping_4_4: float = 0.0       # ВРИ 4.4 — торговля
    catering_4_6: float = 0.0       # ВРИ 4.6 — общепит
    domestic_3_3: float = 0.0       # ВРИ 3.3 — бытовое обслуживание

    @property
    def total(self) -> float:
        return self.shopping_4_4 + self.catering_4_6 + self.domestic_3_3

    @property
    def non_44_46_total(self) -> float:
        """Сумма минимумов для ВРИ, которые не растягиваются (3.3)."""
        return self.domestic_3_3


def compute_mandatory_areas(population: float, norms: Normatives) -> MandatoryVppAreas:
    """Минимальные площади обязательных ВПП от населения."""
    if population <= 0:
        return MandatoryVppAreas()

    # ВРИ 4.4 — норматив сразу в м²
    s_44 = population * norms.resolve("mandatory_vpp.shopping_4_4.area_per_1000") / 1000
    # ВРИ 4.6 — посад. мест × м²/место
    seats_46 = population * norms.resolve("mandatory_vpp.catering_4_6.seats_per_1000") / 1000
    s_46 = seats_46 * norms.resolve("mandatory_vpp.catering_4_6.m2_per_seat")
    # ВРИ 3.3 — раб. мест × м²/раб.место
    wp_33 = population * norms.resolve("mandatory_vpp.domestic_3_3.workplaces_per_1000") / 1000
    s_33 = wp_33 * norms.resolve("mandatory_vpp.domestic_3_3.m2_per_workplace")
    # v0.12.15/28: ВРИ 3.5.1 и 3.4.1 больше не в ВПП — см. add_education.py / polyclinic.py

    return MandatoryVppAreas(
        shopping_4_4=s_44,
        catering_4_6=s_46,
        domestic_3_3=s_33,
    )


@dataclass
class VppBuildResult:
    """Итог сборки списка ВПП по выбранному режиму."""
    built_ins: list[BuiltInArea] = field(default_factory=list)
    # Тех. инфо для аудита
    overflow: bool = False     # True если обязательный min не помещается в footprint
    warnings: list[str] = field(default_factory=list)


def build_built_ins(
    mode: VppMode,
    population: float,
    footprint: float,
    norms: Normatives,
    custom_4_4_m2: float | None = None,
    custom_4_6_m2: float | None = None,
) -> VppBuildResult:
    """Собрать список ВПП для одного из 5 вариантов.

    Args:
        mode: один из 5 вариантов.
        population: население квартала (для расчёта min от НГП).
        footprint: площадь застройки 1 этажа (нужна для full_floor / half_floor).
        norms: нормативы.
        custom_4_4_m2: пользовательская площадь 4.4 (для min_plus / custom_only).
        custom_4_6_m2: пользовательская площадь 4.6 (для min_plus / custom_only).
    """
    res = VppBuildResult()
    mandatory = compute_mandatory_areas(population, norms)

    def _add(vri: str, area: float, label: str = "") -> None:
        if area > 0:
            res.built_ins.append(BuiltInArea(area_m2=area, vri_code=vri, label=label))

    if mode == "min_only":
        # Только обязательный минимум по всем 5 ВРИ
        _add("3.3", mandatory.domestic_3_3, "min быт.обсл.")
        _add("4.4", mandatory.shopping_4_4, "min торговля")
        _add("4.6", mandatory.catering_4_6, "min общепит")
        return res

    if mode == "min_plus":
        # Min всех 5 + опционально дополнительные 4.4 / 4.6
        _add("3.3", mandatory.domestic_3_3, "min быт.обсл.")
        # Если custom задан — добавляем СВЕРХ min; иначе только min
        s_44 = mandatory.shopping_4_4 + (custom_4_4_m2 or 0.0)
        s_46 = mandatory.catering_4_6 + (custom_4_6_m2 or 0.0)
        _add("4.4", s_44, "торговля")
        _add("4.6", s_46, "общепит")
        return res

    if mode == "custom_only":
        # Только пользовательские 4.4 и/или 4.6, без min
        _add("4.4", custom_4_4_m2 or 0.0, "торговля (custom)")
        _add("4.6", custom_4_6_m2 or 0.0, "общепит (custom)")
        return res

    # Режимы full_floor / half_floor
    target_floor = footprint if mode == "full_floor" else footprint * 0.5

    # Сначала размещаем min для 3.3
    _add("3.3", mandatory.domestic_3_3, "min быт.обсл.")

    # Минимум для 4.4 и 4.6
    s_44_min = mandatory.shopping_4_4
    s_46_min = mandatory.catering_4_6

    # Проверяем доступность остатка
    occupied_by_non_44_46 = mandatory.non_44_46_total
    available_for_44_46 = target_floor - occupied_by_non_44_46

    if available_for_44_46 < s_44_min + s_46_min:
        # Минимум не помещается в выбранную долю этажа
        res.overflow = True
        res.warnings.append(
            f"Обязательная программа ВПП (min) превышает доступную площадь "
            f"1 этажа: требуется ≥ {occupied_by_non_44_46 + s_44_min + s_46_min:.0f} м², "
            f"доступно {target_floor:.0f} м². Принято: min 4.4 и 4.6 без extra."
        )
        _add("4.4", s_44_min, "min торговля (overflow)")
        _add("4.6", s_46_min, "min общепит (overflow)")
        return res

    # Распределяем остаток 50/50 между 4.4 и 4.6
    extra_total = available_for_44_46 - s_44_min - s_46_min
    s_44 = s_44_min + extra_total / 2
    s_46 = s_46_min + extra_total / 2
    _add("4.4", s_44, "торговля (min + extra/2)")
    _add("4.6", s_46, "общепит (min + extra/2)")
    return res


# v0.7.1.1: убрана функция advanced_parking_for_vri.
# Теперь все ВПП считаются по единому коэффициенту parking.vpp.m2_per_place = 64
# (1.56 м/м на 100 м²) — упрощение по сравнению с детальной формулой
# «5 работников + N посетителей» для 3.4.1 / 3.5.1.
