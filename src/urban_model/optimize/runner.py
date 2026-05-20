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
    n_trials_exception: int = 0          # сколько trials упало с исключением
    exceptions: list[str] = field(default_factory=list)  # сводка ошибок (top-5 уникальных)
    warnings: list[str] = field(default_factory=list)    # предупреждения о пространстве поиска


# ---------------------------------------------------------------------------
# Сэмплинг: применить trial к копии base_options
# ---------------------------------------------------------------------------

def _build_options_for_trial(
    trial: optuna.Trial,
    base_options: CalculationOptions,
    space: SearchSpace,
    site: Site | None = None,
    norms: Normatives | None = None,
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

        if space.multilevel_levels_range:
            lo, hi = space.multilevel_levels_range
            ml_levels = trial.suggest_int("multilevel_levels", lo, hi)
        else:
            ml_levels = opts.parking.multilevel_levels

        # Подземные: либо как остаток до 100%, либо в заданном диапазоне.
        # Если диапазон задан и сумма не вписывается — пробуем подогнать.
        if space.parking_underground_share_range:
            ug_lo, ug_hi = space.parking_underground_share_range
            ug_target = trial.suggest_float("parking_ug_share", ug_lo, ug_hi)
            # Нормализуем доли: open + ml + ug = 1.0
            s = max(open_share + ml_share + ug_target, 1e-9)
            open_share /= s
            ml_share /= s
            ug_target /= s
            ug_share_final = ug_target
        else:
            open_share = open_share
            ug_share_final = max(0.0, 1.0 - open_share - ml_share)

        open_r = round(open_share, 4)
        ml_r = round(ml_share, 4)
        ug_r = max(0.0, 1.0 - open_r - ml_r)

        # Подземные уровни (v0.8.0)
        if space.underground_levels_range:
            lo_ug, hi_ug = space.underground_levels_range
            ug_levels = trial.suggest_int("underground_levels", lo_ug, hi_ug)
            sampled["underground_levels"] = int(ug_levels)
        else:
            ug_levels = getattr(opts.parking, "underground_levels", 1) or 1

        opts.parking = ParkingConfig(
            mode="custom",
            open_share=open_r,
            multilevel_share=ml_r,
            underground_share=ug_r,
            multilevel_levels=int(ml_levels),
            underground_levels=int(ug_levels),
        )
        sampled["parking_open_share"] = round(open_r, 3)
        sampled["parking_ml_share"] = round(ml_r, 3)
        sampled["parking_ug_share"] = round(ug_r, 3)
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
    # v0.8.0: новый механизм — Optuna выбирает один из РЕЖИМОВ ВПП из
    # space.vpp_modes (как на вкладке «Параметры»). Если vpp_modes задан,
    # try_built_in игнорируется. Список ВПП собирается через vpp.build_built_ins.
    if space.vpp_modes and site is not None and norms is not None:
        from urban_model.calculations import vpp as _vpp
        vpp_choices = ["off"] + list(space.vpp_modes)
        chosen = trial.suggest_categorical("vpp_mode", vpp_choices)
        sampled["vpp_mode"] = chosen
        if chosen == "off":
            opts.built_in = None
            opts.built_in_list = []
        else:
            # Для full_floor/half_floor нужен footprint — пробный прогон без ВПП.
            # Это та же логика, что в state.run_calculation, но локально.
            if chosen in ("full_floor", "half_floor"):
                from urban_model import solve_max_kit as _solve
                opts_step1 = opts.model_copy(deep=True)
                opts_step1.built_in = None
                opts_step1.built_in_list = []
                try:
                    r0 = _solve(site, opts_step1, norms)
                    footprint = r0.housing_footprint.value or 0.0
                    pop = r0.population.value or 0.0
                except Exception:
                    footprint = 0.0
                    pop = 0.0
            else:
                # Для min_only/min_plus/custom_only footprint не нужен,
                # но нужен pop — берём от текущей options оценочно.
                from urban_model import solve_max_kit as _solve
                opts_step1 = opts.model_copy(deep=True)
                opts_step1.built_in = None
                opts_step1.built_in_list = []
                try:
                    r0 = _solve(site, opts_step1, norms)
                    pop = r0.population.value or 0.0
                    footprint = r0.housing_footprint.value or 0.0
                except Exception:
                    pop = 0.0
                    footprint = 0.0
            build = _vpp.build_built_ins(
                mode=chosen, population=pop, footprint=footprint, norms=norms,
            )
            opts.built_in = None
            opts.built_in_list = build.built_ins
    elif space.try_built_in:
        # Legacy: бинарный выбор on/off с одним ВРИ-кодом из base_options
        use_vpp = trial.suggest_categorical("use_vpp", [False, True])
        sampled["use_vpp"] = use_vpp
        if use_vpp:
            vri = trial.suggest_categorical("vpp_vri", space.built_in_vri_codes)
            sampled["vpp_vri"] = vri
            if base_options.built_in is not None:
                opts.built_in = base_options.built_in.model_copy(update={"vri_code": vri})
            else:
                opts.built_in = None
        else:
            opts.built_in = None

    # --- ЗНОП: override м²/чел (v0.7.3) ---
    if space.znop_per_person_choices:
        znop_pp = trial.suggest_categorical(
            "znop_per_person", space.znop_per_person_choices
        )
        opts.znop_per_person_override = float(znop_pp)
        sampled["znop_per_person"] = znop_pp

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
    exception_log: list[str],
) -> Callable[[optuna.Trial], float]:
    def objective(trial: optuna.Trial) -> float:
        try:
            opts, sampled = _build_options_for_trial(
                trial, base_options, space, site=site, norms=norms,
            )
            tep = solve_max_kit(site, opts, norms)
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            logging.warning("Optuna trial %d failed: %s", trial.number, msg)
            exception_log.append(msg)
            return -1e9  # инфисимально плохая оценка

        # Жёсткие ограничения:
        # v0.8.0: для оптимизатора дополнительно фильтруем нереалистичные
        # сценарии — когда расчётная вместимость соцобъекта МЕНЬШЕ норматива
        # (типа «9 ДОО по 9 мест каждое» при 80 мест общей потребности).
        # WARNING про «вне списка типовых» не блокируем — это допустимо.
        def _has_capacity_violation(warnings_list: list[str]) -> bool:
            keywords = [
                "меньше минимальной",       # ДОО < 120
                "меньше минимума отдельно",  # ДОО detached < 160
                "< нормативного минимума",   # СОШ < 550
                "превышает принятый максимум",  # ДОО > 350
            ]
            return any(any(k in w for k in keywords) for w in warnings_list)

        strict_social = getattr(space, "strict_social_validation", False)
        feasible = (
            tep.balance.is_feasible
            and tep.density_chel_per_ga.status != Status.ERROR
            and (not strict_social or not _has_capacity_violation(tep.warnings))
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

        # Целевая функция (v0.8.0):
        # - "apartments_area" — максимизируем площадь квартир (как было)
        # - "profit" — максимизируем прибыль из tep.economy
        if not feasible:
            return -1e6
        objective_key = getattr(space, "objective", "apartments_area")
        if objective_key == "profit":
            if tep.economy is not None:
                return float(tep.economy.profit)
            # Если экономика не задана — деградация к apartments_area
            return apt_area
        return apt_area
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
    # ---- Предполётные проверки пространства поиска ----
    space_warnings: list[str] = []
    # Warning только если используется legacy try_built_in (с одним BuiltInArea
    # из base_options). При новом vpp_modes (v0.8.0) список ВПП собирается
    # автоматически через vpp.build_built_ins — base_options.built_in не нужен.
    if space.try_built_in and base_options.built_in is None and not space.vpp_modes:
        # Без базового built_in оптимизатор не знает площадь ВПП — варианты
        # `use_vpp=True` молча выродятся в `built_in=None` и не дадут
        # отличий. Это ловушка для пользователя.
        space_warnings.append(
            "try_built_in=True, но base_options.built_in is None — "
            "варианты с ВПП будут проигнорированы. Задайте built_in в "
            "base_options как шаблон (хотя бы примерную площадь)."
        )

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
            warnings=space_warnings,
        )

    # Базовый расчёт — для сравнения «было vs стало»
    try:
        base_tep = solve_max_kit(site, base_options, norms)
        base_apt = base_tep.apartments_area.value or 0.0
    except Exception:
        base_apt = None

    storage: list[OptimizationResult] = []
    exception_log: list[str] = []
    sampler = optuna.samplers.TPESampler(seed=seed)
    study = optuna.create_study(direction="maximize", sampler=sampler)

    obj = _make_objective(site, base_options, norms, space, storage, exception_log)

    if progress_callback is not None:
        def _cb(study: optuna.Study, trial: optuna.FrozenTrial) -> None:
            best_so_far = study.best_value if study.best_trial else 0.0
            progress_callback(trial.number + 1, n_trials, best_so_far)
        study.optimize(obj, n_trials=n_trials, callbacks=[_cb], show_progress_bar=False)
    else:
        study.optimize(obj, n_trials=n_trials, show_progress_bar=False)

    # Сортируем сохранённые результаты — сортировочный ключ зависит от целевой
    # функции (v0.8.0). По умолчанию — apartments_area.
    feasible = [r for r in storage if r.feasible]
    sort_by_profit = getattr(space, "objective", "apartments_area") == "profit"
    if sort_by_profit:
        feasible.sort(
            key=lambda r: (
                r.tep.economy.profit if r.tep.economy is not None else r.apartments_area
            ),
            reverse=True,
        )
    else:
        feasible.sort(key=lambda r: r.apartments_area, reverse=True)
    for i, r in enumerate(feasible, 1):
        r.rank = i

    top = feasible[:top_n]
    best = top[0] if top else None

    # Сводка по уникальным исключениям (top-5)
    unique_excs: dict[str, int] = {}
    for msg in exception_log:
        unique_excs[msg] = unique_excs.get(msg, 0) + 1
    summary = [
        f"{count}× {msg}"
        for msg, count in sorted(unique_excs.items(), key=lambda kv: -kv[1])[:5]
    ]

    return OptimizationReport(
        best=best,
        top_n=top,
        n_trials_total=len(storage) + len(exception_log),
        n_trials_feasible=len(feasible),
        n_trials_exception=len(exception_log),
        exceptions=summary,
        base_apartments_area=base_apt,
        warnings=space_warnings,
    )
