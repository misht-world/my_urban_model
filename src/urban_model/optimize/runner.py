"""Optuna-оптимизатор для подбора параметров застройки.

Ядро — функция `optimize_max_apartments(site, base_options, norms, space, n_trials)`.
Возвращает список лучших испытаний с TEPResult, отсортированный по убыванию
площади квартир.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Callable

import optuna

from urban_model.core.inverse import solve_max_kit
from urban_model.models.built_in import BuiltInArea
from urban_model.models.options import CalculationOptions
from urban_model.models.parking import ParkingConfig
from urban_model.models.result import Status, TEPResult
from urban_model.models.site import Site
from urban_model.normatives import Normatives
from urban_model.optimize.space import SearchSpace

# Тушим болтливость Optuna в продакшене
optuna.logging.set_verbosity(optuna.logging.WARNING)


# ---------------------------------------------------------------------------
# Контейнер результата
# ---------------------------------------------------------------------------

@dataclass
class OptimizationResult:
    """Результат одного испытания Optuna."""
    rank: int
    apartments_area: float
    kit: float
    params: dict
    tep: TEPResult
    feasible: bool


@dataclass
class OptimizationReport:
    """Сводка по всему запуску."""
    best: OptimizationResult | None
    top_n: list[OptimizationResult]
    n_trials_total: int
    n_trials_feasible: int
    base_apartments_area: float | None = None  # для сравнения «было vs стало»


# ---------------------------------------------------------------------------
# Сэмплинг: применить trial к копии base_options
# ---------------------------------------------------------------------------

def _build_options_for_trial(
    trial: optuna.Trial,
    base_options: CalculationOptions,
    space: SearchSpace,
) -> tuple[CalculationOptions, dict]:
    """Возвращает (модифицированный options, dict сэмплированных параметров)."""
    opts = base_options.model_copy(deep=True)
    sampled: dict = {}

    # --- Этажность ---
    if space.floors_range is not None:
        lo, hi = space.floors_range
        opts.floors = trial.suggest_int("floors", lo, hi)
        sampled["floors"] = opts.floors

    # --- Парковки: режим ---
    parking_mode = opts.parking.mode
    if space.parking_modes:
        parking_mode = trial.suggest_categorical("parking_mode", space.parking_modes)
        sampled["parking_mode"] = parking_mode

    # --- Парковки: доли (только при custom) ---
    if parking_mode == "custom":
        if space.parking_open_share_range:
            lo, hi = space.parking_open_share_range
            open_share = trial.suggest_float("parking_open_share", lo, hi)
        else:
            open_share = opts.parking.open_share

        if space.parking_multilevel_share_range:
            lo, hi = space.parking_multilevel_share_range
            # Многоуровневая доля не должна превысить 1 - open_share
            ml_lo = lo
            ml_hi = min(hi, 1.0 - open_share)
            if ml_hi <= ml_lo:
                ml_share = ml_lo
            else:
                ml_share = trial.suggest_float("parking_ml_share", ml_lo, ml_hi)
        else:
            ml_share = opts.parking.multilevel_share

        ug_share = max(0.0, 1.0 - open_share - ml_share)

        if space.multilevel_levels_range:
            lo, hi = space.multilevel_levels_range
            ml_levels = trial.suggest_int("multilevel_levels", lo, hi)
        else:
            ml_levels = opts.parking.multilevel_levels

        opts.parking = ParkingConfig(
            mode="custom",
            open_share=round(open_share, 6),
            multilevel_share=round(ml_share, 6),
            underground_share=round(ug_share, 6),
            multilevel_levels=int(ml_levels),
        )
        sampled["parking_open_share"] = round(open_share, 3)
        sampled["parking_ml_share"] = round(ml_share, 3)
        sampled["parking_ug_share"] = round(ug_share, 3)
        sampled["multilevel_levels"] = int(ml_levels)
    else:
        opts.parking = ParkingConfig(mode=parking_mode)

    # --- ДОО: число объектов ---
    if space.kg_num_objects_range and opts.include_kindergarten:
        lo, hi = space.kg_num_objects_range
        n = trial.suggest_int("kg_num_objects", lo, hi)
        opts.kindergarten = opts.kindergarten.model_copy(update={"num_objects": n})
        sampled["kg_num_objects"] = n

    # --- СОШ: число объектов ---
    if space.school_num_objects_range and opts.include_school:
        lo, hi = space.school_num_objects_range
        n = trial.suggest_int("school_num_objects", lo, hi)
        opts.school = opts.school.model_copy(update={"num_objects": n})
        sampled["school_num_objects"] = n

    # --- ВПП ---
    if space.try_built_in:
        use_vpp = trial.suggest_categorical("use_vpp", [False, True])
        sampled["use_vpp"] = use_vpp
        if use_vpp:
            vri = trial.suggest_categorical("vpp_vri", space.built_in_vri_codes)
            sampled["vpp_vri"] = vri
            # Базовая площадь ВПП — оставляем из base_options или ставим нолевую заглушку
            if base_options.built_in is not None:
                opts.built_in = base_options.built_in.model_copy(update={"vri_code": vri})
            else:
                # Без явной площади — пользователь должен задать её в base_options
                opts.built_in = None
        else:
            opts.built_in = None

    return opts, sampled


# ---------------------------------------------------------------------------
# Objective
# ---------------------------------------------------------------------------

def _make_objective(
    site: Site,
    base_options: CalculationOptions,
    norms: Normatives,
    space: SearchSpace,
    storage: list[OptimizationResult],
) -> Callable[[optuna.Trial], float]:
    def objective(trial: optuna.Trial) -> float:
        try:
            opts, sampled = _build_options_for_trial(trial, base_options, space)
            tep = solve_max_kit(site, opts, norms)
        except Exception as e:
            logging.debug("Trial %d failed: %s", trial.number, e)
            return -1e9  # инфисимально плохая оценка

        # Жёсткие ограничения:
        feasible = (
            tep.balance.is_feasible
            and tep.density_chel_per_ga.status != Status.ERROR
        )
        apt_area = tep.apartments_area.value if tep.apartments_area.value else 0.0

        storage.append(OptimizationResult(
            rank=-1,  # заполнится после сортировки
            apartments_area=apt_area if feasible else 0.0,
            kit=tep.kit.value if tep.kit.value else 0.0,
            params=sampled,
            tep=tep,
            feasible=feasible,
        ))

        return apt_area if feasible else -1e6
    return objective


# ---------------------------------------------------------------------------
# Главный entry-point
# ---------------------------------------------------------------------------

def optimize_max_apartments(
    site: Site,
    base_options: CalculationOptions,
    norms: Normatives,
    space: SearchSpace,
    n_trials: int = 50,
    top_n: int = 10,
    seed: int | None = 42,
    progress_callback: Callable[[int, int, float], None] | None = None,
) -> OptimizationReport:
    """Запустить Optuna-оптимизацию и вернуть лучшие сценарии.

    Args:
        site:           Квартал.
        base_options:   Шаблон опций; все поля, не помеченные в space, фиксируются.
        norms:          Загруженные нормативы.
        space:          Описание варьируемых параметров.
        n_trials:       Сколько испытаний провести.
        top_n:          Сколько лучших испытаний вернуть.
        seed:           Зерно генератора (для воспроизводимости).
        progress_callback(current, total, best_so_far): для отрисовки прогресса.

    Returns:
        OptimizationReport с топ-N результатов.
    """
    if space.is_empty():
        # Нечего оптимизировать — просто возвращаем базовый результат
        base_tep = solve_max_kit(site, base_options, norms)
        base_res = OptimizationResult(
            rank=1,
            apartments_area=base_tep.apartments_area.value or 0.0,
            kit=base_tep.kit.value or 0.0,
            params={},
            tep=base_tep,
            feasible=base_tep.balance.is_feasible,
        )
        return OptimizationReport(
            best=base_res,
            top_n=[base_res],
            n_trials_total=1,
            n_trials_feasible=1 if base_res.feasible else 0,
            base_apartments_area=base_res.apartments_area,
        )

    # Базовый расчёт — для сравнения «было vs стало»
    try:
        base_tep = solve_max_kit(site, base_options, norms)
        base_apt = base_tep.apartments_area.value or 0.0
    except Exception:
        base_apt = None

    storage: list[OptimizationResult] = []
    sampler = optuna.samplers.TPESampler(seed=seed)
    study = optuna.create_study(direction="maximize", sampler=sampler)

    obj = _make_objective(site, base_options, norms, space, storage)

    if progress_callback is not None:
        def _cb(study: optuna.Study, trial: optuna.FrozenTrial) -> None:
            best_so_far = study.best_value if study.best_trial else 0.0
            progress_callback(trial.number + 1, n_trials, best_so_far)
        study.optimize(obj, n_trials=n_trials, callbacks=[_cb], show_progress_bar=False)
    else:
        study.optimize(obj, n_trials=n_trials, show_progress_bar=False)

    # Сортируем сохранённые результаты по убыванию apt_area, оставляем feasible впереди
    feasible = [r for r in storage if r.feasible]
    feasible.sort(key=lambda r: r.apartments_area, reverse=True)
    for i, r in enumerate(feasible, 1):
        r.rank = i

    top = feasible[:top_n]
    best = top[0] if top else None

    return OptimizationReport(
        best=best,
        top_n=top,
        n_trials_total=len(storage),
        n_trials_feasible=len(feasible),
        base_apartments_area=base_apt,
    )
