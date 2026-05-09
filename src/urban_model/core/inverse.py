"""Обратный расчёт: подбор максимально допустимого КИТ.

Стратегия — бисекция. Целевая функция balance(kit) монотонно убывает
с ростом КИТ (больше жильё → больше население → больше нагрузки).
Особый случай — пороги ЗНОП (1.6/1.8/2.0): функция кусочно непрерывна,
бисекция корректно обрабатывает разрывы.

Дополнительно: после бисекции определяется ограничивающий фактор —
самая «тяжёлая» компонента баланса в доле от площади квартала.
"""

from __future__ import annotations

from typing import Callable

from urban_model.core.forward import compute_tep_for_kit
from urban_model.models.options import CalculationOptions
from urban_model.models.result import Status, TEPResult
from urban_model.models.site import Site
from urban_model.normatives import Normatives


def _is_feasible(kit: float, site: Site, options: CalculationOptions, norms: Normatives) -> bool:
    res = compute_tep_for_kit(kit, site, options, norms)
    return res.balance.is_feasible and res.density_chel_per_ga.status != Status.ERROR


def _identify_limiting_factor(result: TEPResult) -> str:
    """Какая компонента съела больше всего территории относительно квартала."""
    site_area = result.balance.site_area
    biggest = max(result.balance.components.items(), key=lambda kv: kv[1])
    name, val = biggest
    pct = val / site_area * 100
    pretty = {
        "housing_lot": "ЗУ жилой застройки",
        "kindergarten_plot": "участки ДОО",
        "school_plot": "участки СОШ",
        "znop": "ЗНОП",
        "intra_quarter_driveways": "внутриквартальные проезды",
    }.get(name, name)
    return f"{pretty} ({val:,.0f} м², {pct:.1f}% квартала)"


def solve_max_kit(
    site: Site,
    options: CalculationOptions | None = None,
    norms: Normatives | None = None,
) -> TEPResult:
    """Найти максимально допустимый КИТ через бисекцию и вернуть полный TEPResult.

    Если даже минимальный КИТ не проходит — возвращается результат на КИТ_min с
    `balance.is_feasible = False`.
    """
    if norms is None:
        from urban_model.normatives import load_normatives
        norms = load_normatives("spb")
    if options is None:
        options = CalculationOptions()

    kit_max = options.kit_search_max
    if kit_max is None:
        kit_max = norms.resolve(
            "kit_limits", planning_doc="yes" if options.planning_doc else "no"
        )

    lo, hi = options.kit_search_min, float(kit_max)
    tol = options.kit_tolerance

    feas: Callable[[float], bool] = lambda k: _is_feasible(k, site, options, norms)

    # Если даже КИТ_max проходит — возвращаем его (нормативный потолок).
    if feas(hi):
        result = compute_tep_for_kit(hi, site, options, norms)
        result.limiting_factor = (
            f"нормативный потолок КИТ = {hi}; "
            + _identify_limiting_factor(result)
        )
        return result

    # Если даже КИТ_min не проходит — отдаём его.
    if not feas(lo):
        result = compute_tep_for_kit(lo, site, options, norms)
        result.limiting_factor = (
            f"даже минимальный КИТ {lo} не проходит — "
            + _identify_limiting_factor(result)
        )
        return result

    # Стандартная бисекция: [lo проходит, hi не проходит].
    while hi - lo > tol:
        mid = (lo + hi) / 2
        if feas(mid):
            lo = mid
        else:
            hi = mid

    result = compute_tep_for_kit(lo, site, options, norms)
    result.limiting_factor = _identify_limiting_factor(result)
    return result
