"""Детерминированные one-factor-at-a-time сканы для вкладки «Оптимизация» (v0.9.0).

Идея: вместо случайного перебора Optuna — взять один фактор (доля подземки,
ЗНОП-ступень, этажность), перебрать его на сетке при остальных параметрах
зафиксированных из base_options, и показать график «как меняется apt/profit
от значения этого фактора». Это объяснимо и быстро (десятки solve вместо
тысяч trials).

См. план: `D:/Github/my_urban_model/AUDIT.md` (если есть) и одобренный
план v0.9.0 в `~/.claude/plans/`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

from urban_model.core.inverse import solve_max_kit, solve_max_kit_with_znop
from urban_model.models.options import CalculationOptions
from urban_model.models.parking import ParkingConfig
from urban_model.models.result import TEPResult
from urban_model.models.site import Site
from urban_model.normatives import Normatives

Metric = Literal["apartments_area", "profit"]


# ---------------------------------------------------------------------------
# Результат-типы
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ScanPoint:
    """Одна точка скана: значение фактора → ТЭП-результат."""
    x_value: float
    x_label: str
    kit: float
    apartments_area: float
    profit: float | None
    feasible: bool
    tep: TEPResult
    is_base: bool = False
    is_recommended: bool = False


@dataclass(frozen=True)
class ScanResult:
    """Результат скана одного фактора."""
    factor: str            # "parking_underground" | "znop" | "floors"
    title: str             # "Парковки: доля подземных"
    x_axis_label: str      # "Доля подземных, %"
    points: list[ScanPoint] = field(default_factory=list)
    base_point: ScanPoint | None = None
    recommended_point: ScanPoint | None = None
    metric: Metric = "apartments_area"


# ---------------------------------------------------------------------------
# Внутренние утилиты
# ---------------------------------------------------------------------------

def _metric_value(p: ScanPoint, metric: Metric) -> float:
    if metric == "profit":
        return p.profit if p.profit is not None else -math.inf
    return p.apartments_area


def _point_from_tep(
    x_value: float, x_label: str, tep: TEPResult,
) -> ScanPoint:
    profit = tep.economy.profit if tep.economy is not None else None
    return ScanPoint(
        x_value=x_value,
        x_label=x_label,
        kit=float(tep.kit.value or 0.0),
        apartments_area=float(tep.apartments_area.value or 0.0),
        profit=profit,
        feasible=bool(tep.balance.is_feasible),
        tep=tep,
    )


def _mark_base_and_recommended(
    points: list[ScanPoint],
    base_x: float,
    metric: Metric,
) -> tuple[ScanPoint | None, ScanPoint | None]:
    """Помечает точку, ближайшую к base_x, как `is_base`, и argmax feasible — как
    `is_recommended`. Возвращает (base, recommended) — могут быть None если
    список пуст или нет feasible.

    Возвращает НОВЫЕ ScanPoint (т.к. frozen), а вызывающий код обязан
    заменить исходные точки на эти.
    """
    if not points:
        return None, None
    # base — ближайший по x
    base_idx = min(range(len(points)), key=lambda i: abs(points[i].x_value - base_x))
    base = points[base_idx]
    # recommended — argmax метрики среди feasible
    feasible_idx = [i for i, p in enumerate(points) if p.feasible]
    rec_idx = (
        max(feasible_idx, key=lambda i: _metric_value(points[i], metric))
        if feasible_idx else None
    )
    # Создаём отредактированные копии и подменяем в списке
    for i in range(len(points)):
        new_base = (i == base_idx)
        new_rec = (rec_idx is not None and i == rec_idx)
        if new_base or new_rec:
            p = points[i]
            points[i] = ScanPoint(
                x_value=p.x_value, x_label=p.x_label, kit=p.kit,
                apartments_area=p.apartments_area, profit=p.profit,
                feasible=p.feasible, tep=p.tep,
                is_base=new_base, is_recommended=new_rec,
            )
    base = points[base_idx]
    recommended = points[rec_idx] if rec_idx is not None else None
    return base, recommended


# ---------------------------------------------------------------------------
# Парковки: scan по доле подземных (open_share_norm=12.5%, ml = остаток)
# ---------------------------------------------------------------------------

# Нормативный минимум открытых м/м (ПЗЗ СПб). Делать ниже нельзя.
_PARK_OPEN_NORM = 0.125


def scan_parking_underground_share(
    site: Site,
    base_options: CalculationOptions,
    norms: Normatives,
    steps: int = 11,
    metric: Metric = "apartments_area",
) -> ScanResult:
    """Скан по доле подземных парковок: 0%..100% шагом 100%/(steps-1).

    Остальные доли распределяются так:
      • open = max(0.125, 1 - underground)  — норматив СПб не ниже 12.5%
      • multilevel = 1 - open - underground (≥ 0)
    Это даёт три «логичных» режима: 100% open (ug=0), смесь, 87.5%
    подземных (ug=0.875 — типовое `min_open`).

    Один из шагов попадает в `base_options.parking.underground_share`
    (закругление до ближайшего шага).
    """
    base_ug = float(base_options.parking.underground_share)

    points: list[ScanPoint] = []
    for i in range(steps):
        ug = i / (steps - 1)
        open_share = max(_PARK_OPEN_NORM, 1.0 - ug)
        ml = max(0.0, 1.0 - open_share - ug)
        # Нормализуем (sum может быть != 1.0 из-за clamp open_share)
        s = open_share + ml + ug
        if s > 0:
            open_share, ml, ug_n = open_share / s, ml / s, ug / s
        else:
            open_share, ml, ug_n = 1.0, 0.0, 0.0

        opts = base_options.model_copy(deep=True)
        opts.parking = ParkingConfig(
            mode="custom",
            open_share=open_share,
            multilevel_share=ml,
            underground_share=ug_n,
            multilevel_levels=base_options.parking.multilevel_levels,
            underground_levels=base_options.parking.underground_levels,
        )
        try:
            tep = solve_max_kit(site, opts, norms)
        except (ValueError, KeyError, RuntimeError) as e:
            # v0.9.11 (AUDIT P1-1): логируем что точка упала и почему.
            # Раньше молчаливый skip приводил к «дырам» в графике без
            # объяснения. Теперь хотя бы видно в stderr.
            import logging
            logging.warning(
                "scan: solve_max_kit failed (skipped): %s — %s",
                type(e).__name__, e,
            )
            continue
        points.append(_point_from_tep(
            x_value=ug, x_label=f"{ug*100:.0f}%", tep=tep,
        ))

    base, recommended = _mark_base_and_recommended(points, base_ug, metric)
    return ScanResult(
        factor="parking_underground",
        title="Парковки: доля подземных",
        x_axis_label="Доля подземных, %",
        points=points,
        base_point=base,
        recommended_point=recommended,
        metric=metric,
    )


def scan_parking_multilevel_share(
    site: Site,
    base_options: CalculationOptions,
    norms: Normatives,
    steps: int = 11,
    metric: Metric = "apartments_area",
) -> ScanResult:
    """Скан по доле МНОГОУРОВНЕВЫХ парковок: 0..100% шагом 100%/(steps-1).

    Остальное — пропорционально:
      • open = 12.5% (норматив СПб)
      • underground = max(0, 1 - 0.125 - multilevel)
    При multilevel > 87.5% open пропорционально снижается через нормализацию.
    """
    base_ml = float(base_options.parking.multilevel_share)

    points: list[ScanPoint] = []
    for i in range(steps):
        ml = i / (steps - 1)
        open_share = _PARK_OPEN_NORM
        ug = max(0.0, 1.0 - open_share - ml)
        # Если ml > 1 - open_share, нормализуем
        s = open_share + ml + ug
        if s > 0:
            open_n, ml_n, ug_n = open_share / s, ml / s, ug / s
        else:
            open_n, ml_n, ug_n = 1.0, 0.0, 0.0

        opts = base_options.model_copy(deep=True)
        opts.parking = ParkingConfig(
            mode="custom",
            open_share=open_n,
            multilevel_share=ml_n,
            underground_share=ug_n,
            multilevel_levels=base_options.parking.multilevel_levels,
            underground_levels=base_options.parking.underground_levels,
        )
        try:
            tep = solve_max_kit(site, opts, norms)
        except (ValueError, KeyError, RuntimeError) as e:
            # v0.9.11 (AUDIT P1-1): логируем что точка упала и почему.
            # Раньше молчаливый skip приводил к «дырам» в графике без
            # объяснения. Теперь хотя бы видно в stderr.
            import logging
            logging.warning(
                "scan: solve_max_kit failed (skipped): %s — %s",
                type(e).__name__, e,
            )
            continue
        points.append(_point_from_tep(
            x_value=ml, x_label=f"{ml*100:.0f}%", tep=tep,
        ))

    base, recommended = _mark_base_and_recommended(points, base_ml, metric)
    return ScanResult(
        factor="parking_multilevel",
        title="Парковки: доля многоуровневых",
        x_axis_label="Доля многоуровневых, %",
        points=points,
        base_point=base,
        recommended_point=recommended,
        metric=metric,
    )


# ---------------------------------------------------------------------------
# ЗНОП: 4 нормативные ступени по ПЗЗ СПб
# ---------------------------------------------------------------------------

# v0.9.12 (AUDIT P2-1): ZNOP-ступени читаются ДИНАМИЧЕСКИ из YAML, а не
# hardcoded. Если в norms норматив `znop_per_person` изменится (например,
# для другого региона добавится ступень 8 м²/чел), скан подстроится сам.
_ZNOP_STEPS_FALLBACK = [0.0, 3.0, 4.0, 6.0]


def _znop_steps_from_norms(norms: Normatives) -> list[float]:
    """Извлечь нормативные ступени ЗНОП из piecewise(КИТ).

    Возвращает уникальные значения (м²/чел) в порядке возрастания.
    Fallback на [0, 3, 4, 6] если резолв упал.
    """
    try:
        node = norms.get("znop_per_person")
        # node — это Piecewise (см. normatives/schema.py), у него есть .breakpoints
        breakpoints = getattr(node, "breakpoints", None)
        if not breakpoints:
            return list(_ZNOP_STEPS_FALLBACK)
        values: list[float] = []
        for bp in breakpoints:
            # breakpoint — pydantic-модель с полем .value
            v = getattr(bp, "value", None)
            if v is not None:
                values.append(float(v))
        return sorted(set(values)) if values else list(_ZNOP_STEPS_FALLBACK)
    except (AttributeError, KeyError, TypeError, ValueError):
        return list(_ZNOP_STEPS_FALLBACK)


def scan_znop_steps(
    site: Site,
    base_options: CalculationOptions,
    norms: Normatives,
    metric: Metric = "apartments_area",
) -> ScanResult:
    """Скан по нормативным ступеням ЗНОП (берутся из piecewise в YAML).

    Использует готовую функцию `solve_max_kit_with_znop` — она через
    `kit_cap_for_znop()` автоматически снижает потолок КИТ под ступень.
    """
    base_znop = (
        float(base_options.znop_per_person_override)
        if base_options.znop_per_person_override is not None
        else 0.0  # без override считаем «база = ступень 0»
    )

    steps = _znop_steps_from_norms(norms)

    points: list[ScanPoint] = []
    for v in steps:
        try:
            tep = solve_max_kit_with_znop(site, v, base_options, norms)
        except (ValueError, KeyError, RuntimeError) as e:
            import logging
            logging.warning(
                "scan_znop[%s]: solve failed (skipped): %s — %s",
                v, type(e).__name__, e,
            )
            continue
        points.append(_point_from_tep(
            x_value=v, x_label=f"{v:.0f} м²/чел", tep=tep,
        ))

    base, recommended = _mark_base_and_recommended(points, base_znop, metric)
    return ScanResult(
        factor="znop",
        title="ЗНОП: норматив м²/чел",
        x_axis_label="ЗНОП, м²/чел",
        points=points,
        base_point=base,
        recommended_point=recommended,
        metric=metric,
    )


# ---------------------------------------------------------------------------
# Этажность: линейный скан
# ---------------------------------------------------------------------------

def scan_floors(
    site: Site,
    base_options: CalculationOptions,
    norms: Normatives,
    lo: int = 5,
    hi: int = 25,
    metric: Metric = "apartments_area",
) -> ScanResult:
    """Скан по этажности от `lo` до `hi` включительно (шаг 1).

    v0.9.29: при кластерах базовая точка — средневзвешенная этажность, а сам
    скан выполняется по ЕДИНОЙ этажности (зоны временно схлопываются), т.к.
    цель карточки — показать чувствительность к высоте при прочих равных.
    """
    base_floors = (
        float(base_options.effective_floors())
        if base_options.floor_clusters
        else float(base_options.floors)
    )

    points: list[ScanPoint] = []
    for f in range(lo, hi + 1):
        opts = base_options.model_copy(deep=True)
        opts.floor_clusters = []  # скан по единой этажности
        opts.floors = f
        try:
            tep = solve_max_kit(site, opts, norms)
        except (ValueError, KeyError, RuntimeError) as e:
            # v0.9.11 (AUDIT P1-1): логируем что точка упала и почему.
            # Раньше молчаливый skip приводил к «дырам» в графике без
            # объяснения. Теперь хотя бы видно в stderr.
            import logging
            logging.warning(
                "scan: solve_max_kit failed (skipped): %s — %s",
                type(e).__name__, e,
            )
            continue
        points.append(_point_from_tep(
            x_value=float(f), x_label=f"{f} эт.", tep=tep,
        ))

    base, recommended = _mark_base_and_recommended(points, base_floors, metric)
    return ScanResult(
        factor="floors",
        title="Этажность",
        x_axis_label="Этажность",
        points=points,
        base_point=base,
        recommended_point=recommended,
        metric=metric,
    )


# ---------------------------------------------------------------------------
# ДОО / СОШ: скан по числу объектов (v0.9.30)
# ---------------------------------------------------------------------------

import re as _re


def _count_objects_from_formula(formula: str | None) -> int:
    """Число объектов из formula-строки вида '... [120, 120, 180]'."""
    if not formula:
        return 0
    m = _re.search(r"\[([^\]]+)\]", formula)
    if not m:
        return 0
    return len([x for x in m.group(1).split(",") if x.strip()])


def _scan_social_objects(
    site: Site,
    base_options: CalculationOptions,
    norms: Normatives,
    *,
    kind: Literal["kindergarten", "school"],
    lo: int,
    hi: int,
    metric: Metric,
    title: str,
    x_label: str,
    factor: str,
) -> ScanResult:
    """Общий скан по числу объектов соцобъекта (ДОО или СОШ).

    На каждом шаге фиксируем `num_objects = n` (площадь квартир падает,
    когда объектов больше — суммарный ЗУ соцобъектов растёт). Базовая точка —
    фактическое число объектов в базовом расчёте.
    """
    include = getattr(base_options, f"include_{kind}", True)
    spec = getattr(base_options, kind)
    if not include:
        return ScanResult(
            factor=factor, title=title, x_axis_label=x_label,
            points=[], base_point=None, recommended_point=None, metric=metric,
        )

    # Базовое число объектов: из spec.num_objects, иначе из авто-разбивки базы.
    try:
        base_tep0 = solve_max_kit(site, base_options, norms)
        accepted = getattr(base_tep0, f"{kind}_places_accepted")
        base_n = spec.num_objects or _count_objects_from_formula(accepted.formula) or lo
    except (ValueError, KeyError, RuntimeError):
        base_n = spec.num_objects or lo

    points: list[ScanPoint] = []
    for n in range(lo, hi + 1):
        opts = base_options.model_copy(deep=True)
        new_spec = getattr(opts, kind).model_copy(update={"num_objects": n})
        setattr(opts, kind, new_spec)
        try:
            tep = solve_max_kit(site, opts, norms)
        except (ValueError, KeyError, RuntimeError) as e:
            import logging
            logging.warning("scan %s: solve failed (n=%d): %s", factor, n, e)
            continue
        points.append(_point_from_tep(
            x_value=float(n), x_label=f"{n} шт.", tep=tep,
        ))

    base, recommended = _mark_base_and_recommended(points, float(base_n), metric)
    return ScanResult(
        factor=factor, title=title, x_axis_label=x_label,
        points=points, base_point=base, recommended_point=recommended,
        metric=metric,
    )


def scan_kindergarten_objects(
    site: Site,
    base_options: CalculationOptions,
    norms: Normatives,
    lo: int = 1,
    hi: int = 5,
    metric: Metric = "apartments_area",
) -> ScanResult:
    """Скан по числу объектов ДОО (1..5)."""
    return _scan_social_objects(
        site, base_options, norms, kind="kindergarten",
        lo=lo, hi=hi, metric=metric,
        title="Число ДОО", x_label="Число объектов ДОО",
        factor="kindergarten_objects",
    )


def scan_school_objects(
    site: Site,
    base_options: CalculationOptions,
    norms: Normatives,
    lo: int = 1,
    hi: int = 3,
    metric: Metric = "apartments_area",
) -> ScanResult:
    """Скан по числу корпусов СОШ (1..3)."""
    return _scan_social_objects(
        site, base_options, norms, kind="school",
        lo=lo, hi=hi, metric=metric,
        title="Число СОШ", x_label="Число корпусов СОШ",
        factor="school_objects",
    )
