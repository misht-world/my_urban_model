"""Анализ чувствительности (v0.9.15) — «какой фактор сильнее влияет».

Переиспользует детерминированные сканы (`optimize/scans.py`): для каждого
фактора берёт разброс площади квартир / прибыли среди feasible-точек в
окрестности базы. Результат — ранжированный список (tornado), показывающий,
изменение какого параметра даёт наибольший эффект.

Это локальный анализ при прочих равных — дополняет Парето-рекомендации
(которые меняют параметры в комбинации).
"""

from __future__ import annotations

from dataclasses import dataclass

from urban_model.core.inverse import solve_max_kit
from urban_model.models.options import CalculationOptions
from urban_model.models.site import Site
from urban_model.normatives import Normatives
from urban_model.optimize.scans import (
    ScanResult,
    scan_floors,
    scan_parking_multilevel_share,
    scan_parking_underground_share,
    scan_znop_steps,
)


@dataclass(frozen=True)
class FactorImpact:
    """Влияние одного фактора (по площади квартир и прибыли)."""

    factor: str
    label: str
    base_apt: float
    low_apt: float          # минимум apt среди feasible
    high_apt: float         # максимум apt среди feasible
    low_label: str          # значение фактора в точке минимума
    high_label: str         # значение фактора в точке максимума
    base_profit: float | None
    low_profit: float | None
    high_profit: float | None

    @property
    def apt_swing(self) -> float:
        """Размах площади квартир (high − low) — мера влияния фактора."""
        return self.high_apt - self.low_apt

    @property
    def apt_swing_pct(self) -> float:
        """Размах в % от базовой площади."""
        return (self.apt_swing / self.base_apt * 100.0) if self.base_apt > 1e-9 else 0.0

    @property
    def profit_swing(self) -> float | None:
        if self.low_profit is None or self.high_profit is None:
            return None
        return self.high_profit - self.low_profit


def _impact_from_scan(
    scan: ScanResult, base_apt: float, base_profit: float | None,
) -> FactorImpact | None:
    """Сводит ScanResult в FactorImpact по feasible-точкам.

    `base_apt`/`base_profit` — ЕДИНАЯ базовая точка (общий расчёт base_options),
    чтобы размах в % был сопоставим между факторами.
    """
    feasible = [p for p in scan.points if p.feasible]
    if not feasible:
        return None

    lo = min(feasible, key=lambda p: p.apartments_area)
    hi = max(feasible, key=lambda p: p.apartments_area)
    profits = [p.profit for p in feasible if p.profit is not None]

    return FactorImpact(
        factor=scan.factor,
        label=scan.title,
        base_apt=base_apt,
        low_apt=lo.apartments_area,
        high_apt=hi.apartments_area,
        low_label=lo.x_label,
        high_label=hi.x_label,
        base_profit=base_profit,
        low_profit=(min(profits) if profits else None),
        high_profit=(max(profits) if profits else None),
    )


def compute_sensitivity(
    site: Site,
    base_options: CalculationOptions,
    norms: Normatives,
) -> list[FactorImpact]:
    """Считает влияние ключевых факторов, отсортированное по размаху площади.

    Факторы: этажность, доля подземных парковок, доля многоуровневых, ЗНОП.
    (ДОО/СОШ не включены — число объектов не влияет на площадь квартир.)
    """
    # Единая базовая точка — для сопоставимых процентов размаха.
    try:
        base_tep = solve_max_kit(site, base_options, norms)
        base_apt = float(base_tep.apartments_area.value or 0.0)
        base_profit = (
            float(base_tep.economy.profit) if base_tep.economy is not None else None
        )
    except (ValueError, KeyError, RuntimeError):
        base_apt, base_profit = 0.0, None

    scans = [
        scan_floors(site, base_options, norms),
        scan_parking_underground_share(site, base_options, norms),
        scan_parking_multilevel_share(site, base_options, norms),
        scan_znop_steps(site, base_options, norms),
    ]
    impacts: list[FactorImpact] = []
    for sc in scans:
        imp = _impact_from_scan(sc, base_apt, base_profit)
        if imp is not None and imp.apt_swing > 1e-6:
            impacts.append(imp)
    impacts.sort(key=lambda im: im.apt_swing, reverse=True)
    return impacts
