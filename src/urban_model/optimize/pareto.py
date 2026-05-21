"""Парето-рекомендации (v0.9.0).

Один прогон Optuna в широком SearchSpace → 3 готовые рекомендации:
  • Максимум площади квартир
  • Максимум прибыли (если экономика включена)
  • Сбалансированный (нормированная сумма apt и profit)

Это даёт пользователю не «крути 20 ручек», а 3 конкретных сценария
с явным сравнением vs «база» (текущий результат вкладки Расчёт).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from urban_model.models.options import CalculationOptions
from urban_model.models.result import TEPResult
from urban_model.models.site import Site
from urban_model.normatives import Normatives
from urban_model.optimize.runner import (
    OptimizationReport,
    OptimizationResult,
    optimize_max_apartments,
)
from urban_model.optimize.space import SearchSpace


# ---------------------------------------------------------------------------
# Результат-типы
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DeltaSummary:
    """Дельты сценария относительно базы."""
    d_apt_abs: float
    d_apt_pct: float
    d_profit_abs: float | None
    d_profit_pct: float | None
    d_kit_abs: float
    key_changes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Recommendation:
    """Одна рекомендация — лучший сценарий по конкретному критерию."""
    label: str
    rationale: str
    params: dict
    tep: TEPResult
    delta_vs_base: DeltaSummary


@dataclass(frozen=True)
class ParetoBundle:
    """Полный набор рекомендаций + база + сырой отчёт Optuna."""
    recommendations: list[Recommendation]
    base_tep: TEPResult
    n_trials_total: int
    n_trials_feasible: int


# ---------------------------------------------------------------------------
# Текстовая дельта параметров (для подписи «что изменилось»)
# ---------------------------------------------------------------------------

def _format_key_changes(base_options: CalculationOptions, scenario_params: dict) -> list[str]:
    """Возвращает человекочитаемые строки про отличия сценария от базы."""
    changes: list[str] = []
    # Этажность
    if "floors" in scenario_params:
        new_f = int(scenario_params["floors"])
        if new_f != int(base_options.floors):
            diff = new_f - int(base_options.floors)
            sign = "+" if diff > 0 else ""
            changes.append(f"Этажность: {int(base_options.floors)} → {new_f} ({sign}{diff})")
    # Режим парковок
    if "parking_mode" in scenario_params:
        new_mode = scenario_params["parking_mode"]
        old_mode = base_options.parking.mode
        if new_mode != old_mode:
            ru = {"min_open": "минимум открытых", "all_open": "все открытые", "custom": "вручную"}
            changes.append(f"Парковки: {ru.get(old_mode, old_mode)} → {ru.get(new_mode, new_mode)}")
    # Доля подземных
    if "parking_ug_share" in scenario_params:
        new_ug = float(scenario_params["parking_ug_share"])
        old_ug = float(base_options.parking.underground_share)
        if abs(new_ug - old_ug) > 0.05:
            changes.append(f"Подземн. парковка: {old_ug*100:.0f}% → {new_ug*100:.0f}%")
    # ВПП-режим
    if "vpp_mode" in scenario_params:
        vm = scenario_params["vpp_mode"]
        if vm != "off":
            ru = {
                "min_only": "минимум",
                "min_plus": "минимум + допы",
                "custom_only": "вручную",
                "full_floor": "весь 1 этаж",
                "half_floor": "50% 1 этажа",
            }
            changes.append(f"ВПП: {ru.get(vm, vm)}")
    # ЗНОП
    if "znop_per_person" in scenario_params:
        new_z = float(scenario_params["znop_per_person"])
        old_z = (
            float(base_options.znop_per_person_override)
            if base_options.znop_per_person_override is not None
            else 0.0
        )
        if abs(new_z - old_z) > 0.1:
            changes.append(f"ЗНОП: {old_z:.0f} → {new_z:.0f} м²/чел")
    return changes


def _delta(
    base_tep: TEPResult,
    scenario_tep: TEPResult,
    base_options: CalculationOptions,
    scenario_params: dict,
) -> DeltaSummary:
    """Считает все дельты сценария vs база."""
    base_apt = float(base_tep.apartments_area.value or 0.0)
    sc_apt = float(scenario_tep.apartments_area.value or 0.0)
    d_apt_abs = sc_apt - base_apt
    d_apt_pct = (d_apt_abs / base_apt * 100.0) if base_apt > 1e-9 else 0.0

    d_profit_abs: float | None = None
    d_profit_pct: float | None = None
    if base_tep.economy is not None and scenario_tep.economy is not None:
        base_p = float(base_tep.economy.profit)
        sc_p = float(scenario_tep.economy.profit)
        d_profit_abs = sc_p - base_p
        d_profit_pct = (d_profit_abs / abs(base_p) * 100.0) if abs(base_p) > 1e-9 else 0.0

    d_kit_abs = float(scenario_tep.kit.value or 0.0) - float(base_tep.kit.value or 0.0)

    return DeltaSummary(
        d_apt_abs=d_apt_abs,
        d_apt_pct=d_apt_pct,
        d_profit_abs=d_profit_abs,
        d_profit_pct=d_profit_pct,
        d_kit_abs=d_kit_abs,
        key_changes=_format_key_changes(base_options, scenario_params),
    )


# ---------------------------------------------------------------------------
# Выборка трёх рекомендаций из top_n
# ---------------------------------------------------------------------------

def _select_three(
    top_n: list[OptimizationResult],
    base_tep: TEPResult,
    base_options: CalculationOptions,
) -> list[Recommendation]:
    """Из топа выбирает 3 лучших по разным критериям + DeltaSummary."""
    feasible = [r for r in top_n if r.feasible and r.apartments_area > 0]
    if not feasible:
        return []

    # 1. Максимум площади
    apt_best = max(feasible, key=lambda r: r.apartments_area)

    # 2. Максимум прибыли (только среди тех, у кого экономика посчиталась)
    with_econ = [r for r in feasible if r.tep.economy is not None]
    profit_best = (
        max(with_econ, key=lambda r: r.tep.economy.profit) if with_econ else apt_best
    )

    # 3. Сбалансированный — нормировка min-max по top_n, выбор argmax(0.5*apt + 0.5*profit)
    if with_econ and len(with_econ) >= 2:
        apts = [r.apartments_area for r in with_econ]
        profits = [r.tep.economy.profit for r in with_econ]
        apt_min, apt_max = min(apts), max(apts)
        p_min, p_max = min(profits), max(profits)
        apt_range = apt_max - apt_min if apt_max > apt_min else 1.0
        p_range = p_max - p_min if p_max > p_min else 1.0

        def score(r: OptimizationResult) -> float:
            apt_n = (r.apartments_area - apt_min) / apt_range
            p_n = (r.tep.economy.profit - p_min) / p_range
            return 0.5 * apt_n + 0.5 * p_n

        balanced = max(with_econ, key=score)
    else:
        balanced = apt_best

    rationales = {
        "Максимум площади": "Наибольшая площадь квартир — максимальный выход ТЭП.",
        "Максимум прибыли": "Лучший баланс себестоимости и выручки в условных единицах.",
        "Сбалансированный": "Компромисс между площадью квартир и прибылью.",
    }

    recs: list[Recommendation] = []
    # Используем dict-keys для дедупликации: если apt_best == profit_best,
    # «Максимум прибыли» сольётся с «Максимум площади».
    seen: set[int] = set()
    for label, picked in [
        ("Максимум площади", apt_best),
        ("Максимум прибыли", profit_best),
        ("Сбалансированный", balanced),
    ]:
        # id-based дедупликация: если тот же объект Optuna-trial — пропускаем,
        # т.к. рекомендация будет идентична уже добавленной.
        if id(picked) in seen and len(recs) >= 1:
            # Подбираем следующий лучший по этому критерию (если есть).
            # Простая стратегия: берём ВТОРОЙ по метрике с тем же критерием.
            alternatives = [r for r in feasible if id(r) not in seen]
            if not alternatives:
                continue
            if label == "Максимум прибыли" and with_econ:
                alts_econ = [r for r in alternatives if r.tep.economy is not None]
                if alts_econ:
                    picked = max(alts_econ, key=lambda r: r.tep.economy.profit)
                else:
                    continue
            elif label == "Сбалансированный":
                if not alternatives:
                    continue
                picked = alternatives[0]  # уже отсортирован Optuna по основной метрике
            else:
                picked = max(alternatives, key=lambda r: r.apartments_area)
        seen.add(id(picked))
        recs.append(Recommendation(
            label=label,
            rationale=rationales[label],
            params=dict(picked.params),
            tep=picked.tep,
            delta_vs_base=_delta(base_tep, picked.tep, base_options, picked.params),
        ))
    return recs


# ---------------------------------------------------------------------------
# Главная точка входа
# ---------------------------------------------------------------------------

def generate_pareto_recommendations(
    site: Site,
    base_options: CalculationOptions,
    norms: Normatives,
    base_tep: TEPResult,
    n_trials: int = 400,
    seed: int | None = 42,
) -> ParetoBundle:
    """Запускает один Optuna-прогон в широком SearchSpace и возвращает
    3 рекомендации, привязанные к разным критериям, с дельтами vs `base_tep`.

    Это первая итерация v0.9.0 MVP — простая стратегия: одна Optuna,
    три выборки из top_n. Парето-фронт (DEAP/NSGA-II) — на v0.10.
    """
    space = SearchSpace(
        floors_range=(5, 25),
        parking_modes=["min_open", "all_open", "custom"],
        parking_open_share_range=(0.125, 0.5),
        parking_multilevel_share_range=(0.0, 0.5),
        parking_underground_share_range=(0.0, 1.0),
        multilevel_levels_range=(1, 5),
        underground_levels_range=(1, 3),
        znop_per_person_choices=[0.0, 3.0, 4.0, 6.0],
        objective="apartments_area",  # сортируем по apt, потом выберем по разным критериям
        strict_social_validation=False,
    )
    report: OptimizationReport = optimize_max_apartments(
        site=site,
        base_options=base_options,
        norms=norms,
        space=space,
        n_trials=n_trials,
        top_n=50,
        seed=seed,
    )
    recs = _select_three(report.top_n, base_tep, base_options)
    return ParetoBundle(
        recommendations=recs,
        base_tep=base_tep,
        n_trials_total=report.n_trials_total,
        n_trials_feasible=report.n_trials_feasible,
    )
