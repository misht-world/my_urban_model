"""Советующий слой (v0.14.1): текстовые рекомендации из детерминированных сканов.

Первая версия «пофакторного анализа++»: по каждому фактору (этажность, доля
подземных, доля многоуровневых, ЗНОП) берём готовый скан и формулируем совет,
если найденная точка заметно лучше Базы — по площади квартир И/ИЛИ по условной
выгодности. Это ЛОКАЛЬНЫЙ анализ (один фактор, прочие как в базе) — совет
показывает направление, а точную комбинацию ищет подбор (4 карточки).
"""

from __future__ import annotations

from dataclasses import dataclass

from urban_model.models.options import CalculationOptions
from urban_model.models.site import Site
from urban_model.normatives import Normatives
from urban_model.optimize.scans import (
    ScanPoint,
    ScanResult,
    scan_floors,
    scan_parking_multilevel_share,
    scan_parking_underground_share,
)

# ЗНОП в советы НЕ входит (решение v0.9.4): норматив piecewise(КИТ) даёт
# корректные ступени сам, а ручной override — отдельное осознанное решение
# пользователя, а не «улучшение».

# Порог значимости: советуем только если улучшение ≥ 0.5% площади (или ≥1%
# выгодности) — мелочь не показываем, чтобы не создавать шум.
_MIN_APT_GAIN_PCT = 0.5
_MIN_PROFIT_GAIN_PCT = 1.0


@dataclass(frozen=True)
class Advice:
    """Один совет: что изменить и какой эффект (локально, при прочих равных)."""
    factor: str            # "floors" | "parking_ug" | "parking_ml" | "znop"
    text: str              # готовая строка для UI (markdown)
    gain_pct: float        # улучшение ключевой метрики, % (для сортировки)


def _fmt_m2(v: float) -> str:
    return f"{v:,.0f} м²".replace(",", " ")


def _best_by(points: list[ScanPoint], key) -> ScanPoint | None:
    feas = [p for p in points if p.feasible and key(p) is not None]
    return max(feas, key=key) if feas else None


def _advices_from_scan(scan: ScanResult, factor: str, what: str) -> list[Advice]:
    """Советы из одного скана: лучший по площади и лучший по выгодности."""
    base = scan.base_point
    if base is None or not base.feasible:
        return []
    out: list[Advice] = []

    # 1) Максимум площади квартир
    best_apt = _best_by(scan.points, lambda p: p.apartments_area)
    if best_apt is not None and best_apt.x_value != base.x_value:
        gain = best_apt.apartments_area - base.apartments_area
        gain_pct = gain / base.apartments_area * 100 if base.apartments_area else 0
        if gain_pct >= _MIN_APT_GAIN_PCT:
            profit_note = ""
            if best_apt.profit is not None and base.profit is not None:
                dp = best_apt.profit - base.profit
                profit_note = (f"; выгодность {dp:+,.0f} баллов".replace(",", " "))
            out.append(Advice(
                factor=factor,
                text=(f"**{what}: {best_apt.x_label} вместо {base.x_label}** → "
                      f"площадь квартир +{_fmt_m2(gain)} "
                      f"(+{gain_pct:.1f}%){profit_note}."),
                gain_pct=gain_pct,
            ))

    # 2) Максимум условной выгодности (если отличается от точки максимума площади).
    # Проценты от прибыли не показываем: base.profit бывает мал по модулю, и
    # «+100%» вводит в заблуждение — говорим в баллах.
    best_pr = _best_by(scan.points, lambda p: p.profit)
    if (best_pr is not None and base.profit
            and best_pr.x_value != base.x_value
            and (best_apt is None or best_pr.x_value != best_apt.x_value)):
        dp = best_pr.profit - base.profit
        dp_pct = dp / abs(base.profit) * 100 if base.profit else 0
        if dp_pct >= _MIN_PROFIT_GAIN_PCT:
            da = best_pr.apartments_area - base.apartments_area
            out.append(Advice(
                factor=factor,
                text=(f"**{what}: {best_pr.x_label} вместо {base.x_label}** → "
                      f"выгодность {dp:+,.0f} баллов; "
                      f"площадь {da:+,.0f} м².".replace(",", " ")),
                # для сортировки проценты прибыли обрезаем (шумная база)
                gain_pct=min(dp_pct, 50.0),
            ))
    return out


def build_advice(
    site: Site,
    base_options: CalculationOptions,
    norms: Normatives,
    base_apartments: float | None = None,
) -> list[Advice]:
    """Собрать советы по всем факторам. Отсортированы по эффекту (убыв.), ≤5.

    base_apartments: площадь квартир НАСТОЯЩЕЙ Базы (с вкладки «Расчёт») —
    guard от рассинхрона: если внутренняя база скана расходится с ней > 5%,
    скан пропускается (его проценты вводили бы в заблуждение). Допуск 5%,
    а не строже: парковочные сканы используют собственную параметризацию
    (open 12.5% + остаток) и слегка отличаются от базового конфига.
    """
    out: list[Advice] = []
    scans: list[tuple[str, str, ScanResult]] = []
    try:
        scans.append(("floors", "Этажность", scan_floors(site, base_options, norms)))
    except Exception:  # noqa: BLE001 — совет не должен ронять вкладку
        pass
    try:
        scans.append(("parking_ug", "Доля подземных парковок",
                      scan_parking_underground_share(site, base_options, norms)))
    except Exception:  # noqa: BLE001
        pass
    try:
        scans.append(("parking_ml", "Доля многоуровневых парковок",
                      scan_parking_multilevel_share(site, base_options, norms)))
    except Exception:  # noqa: BLE001
        pass

    for factor, what, scan in scans:
        # Guard: база скана должна совпадать с настоящей Базой (±2%), иначе
        # проценты совета считались бы от «не той» точки.
        if (base_apartments and scan.base_point is not None
                and scan.base_point.apartments_area > 0
                and abs(scan.base_point.apartments_area - base_apartments)
                > 0.05 * base_apartments):
            continue
        out.extend(_advices_from_scan(scan, factor, what))

    out.sort(key=lambda a: a.gain_pct, reverse=True)
    return out[:5]
