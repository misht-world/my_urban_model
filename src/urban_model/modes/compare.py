"""Режим сравнения: запуск нескольких сценариев и сводная таблица результатов.

    >>> from urban_model.modes.compare import compare_scenarios, run_scenarios
    >>> from urban_model.models import Scenario, Site, CalculationOptions
    >>> from urban_model.normatives import load_normatives
    >>> norms = load_normatives("spb")
    >>> scenarios = [
    ...     Scenario(name="Малый 1 га", site=Site(area_m2=10_000)),
    ...     Scenario(name="Средний 3 га с ППТ",
    ...              site=Site(area_m2=30_000),
    ...              options=CalculationOptions(floors=12, planning_doc=True)),
    ...     Scenario(name="Проверка КИТ=1.8",
    ...              site=Site(area_m2=30_000),
    ...              mode="verify", kit=1.8),
    ... ]
    >>> df = compare_scenarios(scenarios, norms)
    >>> df
"""

from __future__ import annotations

import pandas as pd

from urban_model.core.forward import compute_tep_for_kit
from urban_model.core.inverse import solve_max_kit
from urban_model.export.table import results_to_dataframe
from urban_model.models.result import TEPResult
from urban_model.models.scenario import Scenario
from urban_model.normatives import Normatives


def run_scenarios(
    scenarios: list[Scenario],
    norms: Normatives,
) -> list[tuple[str, TEPResult]]:
    """Запустить расчёт по каждому сценарию и вернуть список (name, TEPResult).

    - mode='inverse' → solve_max_kit (подбор максимального КИТ)
    - mode='verify'  → compute_tep_for_kit (проверка заданного КИТ)
    """
    results: list[tuple[str, TEPResult]] = []
    for sc in scenarios:
        if sc.mode == "inverse":
            res = solve_max_kit(sc.site, sc.options, norms)
        else:  # verify
            assert sc.kit is not None  # validator гарантирует
            res = compute_tep_for_kit(sc.kit, sc.site, sc.options, norms)
        results.append((sc.name, res))
    return results


def compare_scenarios(
    scenarios: list[Scenario],
    norms: Normatives,
) -> pd.DataFrame:
    """Запустить сценарии и вернуть сводный DataFrame (КПЭ × сценарии).

    Строки — показатели (КИТ, население, ДОО, СОШ, ЗНОП, баланс …).
    Столбцы — сценарии (по имени).

    Args:
        scenarios: список Scenario; порядок → порядок столбцов.
        norms:     нормативная база.

    Returns:
        DataFrame с индексом «показатель» и колонками — именами сценариев.
    """
    pairs = run_scenarios(scenarios, norms)
    return results_to_dataframe(pairs)
