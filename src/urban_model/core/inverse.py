"""Обратные расчёты: подбор КИТ через бисекцию.

Три публичных режима:
  solve_max_kit                — макс. КИТ при выполнении баланса (v0.1).
  solve_max_kit_with_reserve   — макс. КИТ так, чтобы балансовый резерв ≥ target (v0.2).
  solve_max_kit_with_znop      — макс. КИТ при заданном ЗНОП в м²/чел (v0.2).

Целевая функция balance(kit) монотонно убывает с ростом КИТ
(больше жильё → больше население → больше нагрузки), поэтому работает бисекция.
Особый случай — пороги ЗНОП (1.6/1.8/2.0): функция кусочно непрерывна,
бисекция корректно обрабатывает разрывы.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Callable

from urban_model.core.forward import compute_tep_for_kit
from urban_model.models.options import CalculationOptions
from urban_model.models.result import Status, TEPResult
from urban_model.models.site import Site
from urban_model.normatives import Normatives


# ---------------------------------------------------------------------------
# Универсальная бисекция: предикат feasible(kit) монотонен на [lo, hi].
# ---------------------------------------------------------------------------

def _bisect_max_feasible(
    feasible: Callable[[float], bool],
    lo: float,
    hi: float,
    tol: float,
) -> tuple[float, str]:
    """Найти максимальный kit в [lo, hi], при котором feasible(kit) = True.

    Возвращает (best_kit, reason), где reason ∈ {"ceiling", "lo_infeasible", "ok"}.
    """
    if feasible(hi):
        return hi, "ceiling"
    if not feasible(lo):
        return lo, "lo_infeasible"
    while hi - lo > tol:
        mid = (lo + hi) / 2
        if feasible(mid):
            lo = mid
        else:
            hi = mid
    return lo, "ok"


def _identify_limiting_factor(result: TEPResult) -> str:
    """Какая компонента съела больше всего территории относительно квартала."""
    site_area = result.balance.site_area
    if not result.balance.components:
        return "—"
    biggest = max(result.balance.components.items(), key=lambda kv: kv[1])
    name, val = biggest
    pct = val / site_area * 100
    pretty = {
        "housing_lot": "ЗУ жилой застройки",
        "kindergarten_plot": "участки ДОО",
        "school_plot": "участки СОШ",
        "znop": "ЗНОП",
        "intra_quarter_driveways": "внутриквартальные проезды",
        "parking_multilevel": "многоуровневые паркинги",
    }.get(name, name)
    return f"{pretty} ({val:,.0f} м², {pct:.1f}% квартала)"


def _resolve_search_range(
    options: CalculationOptions,
    norms: Normatives,
) -> tuple[float, float]:
    """Диапазон бисекции [lo, hi]."""
    kit_max = options.kit_search_max
    if kit_max is None:
        kit_max = norms.resolve(
            "kit_limits", planning_doc="yes" if options.planning_doc else "no"
        )
    return options.kit_search_min, float(kit_max)


# ---------------------------------------------------------------------------
# Режим 1: solve_max_kit — максимально допустимый КИТ (v0.1)
# ---------------------------------------------------------------------------

def solve_max_kit(
    site: Site,
    options: CalculationOptions | None = None,
    norms: Normatives | None = None,
) -> TEPResult:
    """Найти максимально допустимый КИТ через бисекцию и вернуть полный TEPResult.

    Если даже минимальный КИТ не проходит — возвращается результат на КИТ_min
    с `balance.is_feasible = False`.
    """
    if norms is None:
        from urban_model.normatives import load_normatives
        norms = load_normatives("spb")
    if options is None:
        options = CalculationOptions()

    lo, hi = _resolve_search_range(options, norms)

    def feasible(k: float) -> bool:
        r = compute_tep_for_kit(k, site, options, norms)
        return r.balance.is_feasible and r.density_chel_per_ga.status != Status.ERROR

    best, reason = _bisect_max_feasible(feasible, lo, hi, options.kit_tolerance)
    result = compute_tep_for_kit(best, site, options, norms)
    if reason == "ceiling":
        result.limiting_factor = (
            f"нормативный потолок КИТ = {hi}; "
            + _identify_limiting_factor(result)
        )
    elif reason == "lo_infeasible":
        result.limiting_factor = (
            f"даже минимальный КИТ {lo} не проходит — "
            + _identify_limiting_factor(result)
        )
    else:
        result.limiting_factor = _identify_limiting_factor(result)
    return result


# ---------------------------------------------------------------------------
# Режим 2: solve_max_kit_with_reserve — КИТ с заданным балансовым резервом (v0.2)
# ---------------------------------------------------------------------------

def solve_max_kit_with_reserve(
    site: Site,
    target_surplus_m2: float,
    options: CalculationOptions | None = None,
    norms: Normatives | None = None,
) -> TEPResult:
    """Найти максимальный КИТ так, чтобы балансовый резерв был ≥ target_surplus_m2.

    Используется для подбора варианта с запасом площади «на манёвр»:
    например, целевой резерв 5 000 м² на дополнительные элементы благоустройства.

    Args:
        site:               Описание квартала.
        target_surplus_m2:  Минимальный требуемый избыток баланса, м².
        options, norms:     То же, что в solve_max_kit.

    Returns:
        TEPResult; если даже минимальный КИТ не даёт нужного резерва —
        вернётся КИТ_min с `limiting_factor` об этом.
    """
    if norms is None:
        from urban_model.normatives import load_normatives
        norms = load_normatives("spb")
    if options is None:
        options = CalculationOptions()

    lo, hi = _resolve_search_range(options, norms)

    def feasible(k: float) -> bool:
        r = compute_tep_for_kit(k, site, options, norms)
        return (
            r.balance.surplus >= target_surplus_m2
            and r.density_chel_per_ga.status != Status.ERROR
        )

    best, reason = _bisect_max_feasible(feasible, lo, hi, options.kit_tolerance)
    result = compute_tep_for_kit(best, site, options, norms)
    base_lf = _identify_limiting_factor(result)
    if reason == "ceiling":
        result.limiting_factor = (
            f"нормативный потолок КИТ = {hi} (резерв ≥ {target_surplus_m2:,.0f} достижим); "
            + base_lf
        )
    elif reason == "lo_infeasible":
        result.limiting_factor = (
            f"даже КИТ {lo} не даёт резерв ≥ {target_surplus_m2:,.0f} м²; "
            + base_lf
        )
    else:
        result.limiting_factor = (
            f"подбор по резерву ≥ {target_surplus_m2:,.0f} м²; "
            + base_lf
        )
    return result


# ---------------------------------------------------------------------------
# Режим 3: solve_max_kit_with_znop — КИТ при заданном ЗНОП (v0.2)
# ---------------------------------------------------------------------------

def solve_max_kit_with_znop(
    site: Site,
    target_znop_per_person: float,
    options: CalculationOptions | None = None,
    norms: Normatives | None = None,
) -> TEPResult:
    """Найти максимальный КИТ при принудительно заданном ЗНОП (м²/чел).

    Замысел: пользователь хочет повысить ЗНОП выше нормативного (например, 6 м²/чел
    на квартале, где по piecewise(КИТ) попадает в ступень 3 м²/чел) и узнать,
    какой КИТ при этом ещё возможен.

    Технически передаёт `znop_per_person_override` в options и запускает
    стандартный solve_max_kit поверх. Поле `znop_per_person.status = MANUAL`.

    Args:
        site:                    Описание квартала.
        target_znop_per_person:  Требуемый ЗНОП, м²/чел.
        options, norms:          То же, что в solve_max_kit.
    """
    if norms is None:
        from urban_model.normatives import load_normatives
        norms = load_normatives("spb")
    if options is None:
        options = CalculationOptions()

    # Глубокая копия, чтобы не мутировать переданный пользователем options
    opts = options.model_copy(deep=True)
    opts.znop_per_person_override = target_znop_per_person

    result = solve_max_kit(site, opts, norms)
    # Дополним ограничивающий фактор пометкой про override
    if result.limiting_factor:
        result.limiting_factor = (
            f"ЗНОП зафиксирован вручную = {target_znop_per_person} м²/чел; "
            + result.limiting_factor
        )
    return result
