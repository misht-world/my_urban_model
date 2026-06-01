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
    # v0.9.11 (AUDIT S-1/P1-7): если рекомендаций нет — почему?
    # Используется в UI для понятного сообщения вместо пустых карточек.
    no_feasible_reason: str | None = None


@dataclass(frozen=True)
class ParetoConstraints:
    """Ограничения подбора (v0.9.3+).

    Позволяет пользователю исключить из перебора Optuna нежелательные
    варианты: задать диапазон этажности, запретить отдельные типы парковок
    и т.п.

    v0.9.9: `restrict_parking_combos` — типологический фильтр. На рынке
    обычно строят: (а) только открытые, (б) открытые + многоуровневые,
    (в) открытые + подземные. Сочетание МУ + подземные = редкое
    (двойной перекрыт, дорого без выгоды). По умолчанию такие гибриды
    исключаются из рекомендаций.
    """
    floors_range: tuple[int, int] = (5, 25)
    allow_open: bool = True
    allow_multilevel: bool = True
    allow_underground: bool = True
    restrict_parking_combos: bool = True
    # v0.10.4: диапазон этажности ПО КАЖДОЙ ЗОНЕ для подбора (список (lo,hi) в
    # порядке зон). None → этажность зон НЕ варьируется (берётся из базы).
    # Длина должна совпадать с числом зон; иначе варьирование игнорируется.
    cluster_floors_ranges: tuple[tuple[int, int], ...] | None = None


def _is_hybrid_parking(params: dict, threshold: float = 0.10) -> bool:
    """True если в сценарии одновременно ≥ threshold многоуровневых И ≥ threshold
    подземных (типологически редкое сочетание МУ+UG, обычно не строится).

    v0.9.11 (AUDIT P1-3): включающая граница `>=` вместо `>` — устраняет
    скачок поведения на ровно 10%. Теперь варианты с ml=10%, ug=10%
    однозначно считаются гибридами и отфильтровываются.
    """
    ml = float(params.get("parking_ml_share", 0.0) or 0.0)
    ug = float(params.get("parking_ug_share", 0.0) or 0.0)
    return ml >= threshold and ug >= threshold


# v0.9.10-v0.9.12: минимальные пороги «осмысленной» секции парковки.
# Меньше — символическое количество, на практике не строится.
# Многоуровневый паркинг = 1 объект ≈ 100 мест (max 300 по нормативу СПб),
# минимально стартует от ~50 мест. Подземная секция стартует от ~30 мест
# (один въезд + ~10 мест на ряд × 3 ряда).
#
# AUDIT P1-2: пороги hardcoded плохо работали на крайних масштабах.
# На квартале 200k м² (2000 м/м) 50 МУ = 2.5% — фильтр почти не работает.
# На 5k м² (70 м/м) 50 МУ = 71% — фильтр запрещает любые МУ.
# Решение: max(абсолютный порог, доля_от_total). 5% — типовая «доля
# одной секции» от общего числа мест для среднего квартала.
_MIN_ML_PLACES_ABS = 50
_MIN_UG_PLACES_ABS = 30
_MIN_FRACTION_OF_TOTAL = 0.05


def _has_token_parking(tep: TEPResult) -> bool:
    """True если в варианте есть «символическое» количество МУ или подземных
    мест: 0 < N < max(абсолютный_порог, 5% от общего числа м/м).
    На больших кварталах порог растёт пропорционально (50 → ~100+),
    на малых — снижается до абсолютного минимума (30/50).
    """
    ml = int(tep.parking_multilevel_places.value or 0)
    ug = int(tep.parking_underground_places.value or 0)
    total = int(tep.parking_required_places.value or 0)
    # Адаптивный порог: max(абсолютный, доля от total)
    th_ml = max(_MIN_ML_PLACES_ABS, int(total * _MIN_FRACTION_OF_TOTAL))
    th_ug = max(_MIN_UG_PLACES_ABS, int(total * _MIN_FRACTION_OF_TOTAL))
    if 0 < ml < th_ml:
        return True
    if 0 < ug < th_ug:
        return True
    return False


# ---------------------------------------------------------------------------
# Текстовая дельта параметров (для подписи «что изменилось»)
# ---------------------------------------------------------------------------

def _format_key_changes(base_options: CalculationOptions, scenario_params: dict) -> list[str]:
    """Возвращает человекочитаемые строки про отличия сценария от базы."""
    changes: list[str] = []
    # Этажность зон (кластеры, v0.9.29)
    if "cluster_floors" in scenario_params and base_options.floor_clusters:
        new_fl = [int(x) for x in scenario_params["cluster_floors"]]
        old_fl = [int(c.floors) for c in base_options.floor_clusters]
        if new_fl != old_fl:
            changes.append(
                f"Этажность зон: {old_fl} → {new_fl}".replace("[", "").replace("]", "")
            )
    # Этажность (одиночная)
    elif "floors" in scenario_params:
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

def _parking_archetype(r: OptimizationResult) -> str:
    """Грубая категория парковки для стратификации рекомендаций.

    Используется чтобы 3 карточки показывали ТИПОЛОГИЧЕСКИ разные сценарии,
    даже когда Optuna сходится в одну точку по основной метрике.
    """
    p = r.params
    mode = p.get("parking_mode", "")
    if mode != "custom":
        return mode  # "min_open" / "all_open"
    ug = float(p.get("parking_ug_share", 0.0))
    ml = float(p.get("parking_ml_share", 0.0))
    if ug >= 0.6:
        return "custom_deep_underground"  # ≥60% подземки
    if ml >= 0.4:
        return "custom_multilevel"        # ≥40% многоуровневых
    return "custom_surface"               # преимущественно открытые


def _params_fingerprint(r: OptimizationResult) -> tuple:
    """Семантический ключ варианта — для точного дедупа по параметрам."""
    p = r.params
    return (
        int(p.get("floors", 0)),
        tuple(int(x) for x in p.get("cluster_floors", [])),
        _parking_archetype(r),
        str(p.get("vpp_mode", "")),
        round(float(p.get("znop_per_person", -1.0)), 1),
        int(p.get("kg_num_objects", 0)),
        int(p.get("school_num_objects", 0)),
    )


def _select_three(
    top_n: list[OptimizationResult],
    base_tep: TEPResult,
    base_options: CalculationOptions,
    constraints: "ParetoConstraints | None" = None,
) -> list[Recommendation]:
    """Из топа выбирает 3 лучших по разным критериям + DeltaSummary.

    v0.9.9: при `constraints.restrict_parking_combos=True` (дефолт)
    отбрасываются типологически редкие гибриды МУ+подземные.
    """
    feasible = [r for r in top_n if r.feasible and r.apartments_area > 0]
    if not feasible:
        return []

    # Типологические фильтры (v0.9.9-0.9.10):
    #   1) Не сочетать МУ + подземные одновременно (>10% каждого)
    #   2) Не предлагать «символическое» количество мест в МУ/подземке
    #      (< 50 МУ или < 30 подземных — это меньше одной секции, на
    #       практике не строится)
    # Оба фильтра привязаны к одной опции `restrict_parking_combos`.
    if constraints is None or constraints.restrict_parking_combos:
        filtered = [
            r for r in feasible
            if not _is_hybrid_parking(r.params)
            and not _has_token_parking(r.tep)
        ]
        # Страховка от пустого пула — fallback на исходный список.
        if filtered:
            feasible = filtered

    with_econ = [r for r in feasible if r.tep.economy is not None]

    # Заготовим отсортированные пулы для каждого критерия
    apt_sorted = sorted(feasible, key=lambda r: r.apartments_area, reverse=True)
    profit_sorted = (
        sorted(with_econ, key=lambda r: r.tep.economy.profit, reverse=True)
        if with_econ else apt_sorted
    )

    # Balanced: нормировка min-max → argmax 0.5*apt + 0.5*profit
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

        balanced_sorted = sorted(with_econ, key=score, reverse=True)
    else:
        balanced_sorted = apt_sorted

    rationales = {
        "Максимум площади": "Наибольшая площадь квартир — максимальный выход ТЭП.",
        "Максимум прибыли": "Лучший баланс себестоимости и выручки в условных единицах.",
        "Сбалансированный": "Компромисс между площадью квартир и прибылью.",
    }

    def _pick(
        pool: list[OptimizationResult],
        seen_fps: set[tuple],
        seen_archetypes: set[str],
        require_new_archetype: bool,
    ) -> OptimizationResult | None:
        """Выбрать первый из pool, удовлетворяющий условиям дедупликации."""
        # Сначала пробуем с new archetype (для типологического разнообразия)
        if require_new_archetype:
            for r in pool:
                arch = _parking_archetype(r)
                fp = _params_fingerprint(r)
                if fp not in seen_fps and arch not in seen_archetypes:
                    seen_fps.add(fp); seen_archetypes.add(arch)
                    return r
        # Fallback: любой уникальный fp (даже если архетип повторяется)
        for r in pool:
            fp = _params_fingerprint(r)
            if fp not in seen_fps:
                seen_fps.add(fp); seen_archetypes.add(_parking_archetype(r))
                return r
        return None

    recs: list[Recommendation] = []
    seen_fps: set[tuple] = set()
    seen_arch: set[str] = set()
    for i, (label, pool) in enumerate([
        ("Максимум площади", apt_sorted),
        ("Максимум прибыли", profit_sorted),
        ("Сбалансированный", balanced_sorted),
    ]):
        # Первая рекомендация — без ограничения архетипа.
        # Вторая и третья — стараемся выбрать другой архетип парковки,
        # чтобы предложить типологически различающиеся сценарии.
        picked = _pick(pool, seen_fps, seen_arch, require_new_archetype=(i > 0))
        if picked is None:
            continue
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

def _build_search_space(
    constraints: ParetoConstraints,
    has_clusters: bool = False,
    vary_zones: bool = False,
) -> SearchSpace:
    """SearchSpace с учётом пользовательских ограничений.

    v0.9.29/v0.10.3: при кластерах глобальная этажность не варьируется
    (`has_clusters` → floors_range=None). Этажность зон варьируется только
    если `vary_zones=True` (пользователь задал диапазон в настройках подбора).
    """
    # Режимы парковок: если запрещены все «нестандартные» — оставляем что есть.
    parking_modes: list[str] = []
    if constraints.allow_open:
        parking_modes.append("all_open")  # 100% открытые
    # min_open включаем, только если разрешены подземные (он = 12.5% open + 87.5% ug)
    if constraints.allow_underground:
        parking_modes.append("min_open")
    # custom — нужен, если разрешён хоть один из multilevel/underground
    if constraints.allow_multilevel or constraints.allow_underground:
        parking_modes.append("custom")
    if not parking_modes:
        # Запретили всё — оставляем хотя бы all_open, иначе ничего не построится
        parking_modes = ["all_open"]

    # Диапазоны долей: если тип запрещён — фиксируем 0..0
    ug_range = (0.0, 1.0) if constraints.allow_underground else (0.0, 0.0)
    ml_range = (0.0, 0.5) if constraints.allow_multilevel else (0.0, 0.0)
    # Open: минимум 12.5% (норматив). Если разрешено всё подземное — открытые
    # могут быть на минимуме; если же открытые запрещены — не получится
    # (норматив требует ≥12.5%), здесь open_share_range игнорируется.
    open_range = (0.125, 0.5) if constraints.allow_open else (0.125, 0.125)

    return SearchSpace(
        # При кластерах глобальная этажность не варьируется — её определяют зоны.
        floors_range=None if has_clusters else constraints.floors_range,
        vary_cluster_floors=vary_zones,
        parking_modes=parking_modes,
        parking_open_share_range=open_range,
        parking_multilevel_share_range=ml_range,
        parking_underground_share_range=ug_range,
        multilevel_levels_range=(1, 5) if constraints.allow_multilevel else None,
        underground_levels_range=(1, 3) if constraints.allow_underground else None,
        # v0.9.4: ЗНОП НЕ варьируется в Парето — он считается по нормативу
        # piecewise(КИТ ПЗЗ). Принудительный ЗНОП имеет смысл ТОЛЬКО когда
        # бисекция упирается в потолок КИТ; если КИТ ниже нормативной ступени,
        # принудительный ЗНОП лишь «проедает» квартал зеленью без пользы.
        # Анализ влияния ЗНОП остаётся в карточке «🌳 ЗНОП» Пофакторного.
        znop_per_person_choices=None,
        objective="apartments_area",
        strict_social_validation=False,
        diversify_sampler=True,
    )


def generate_pareto_recommendations(
    site: Site,
    base_options: CalculationOptions,
    norms: Normatives,
    base_tep: TEPResult,
    n_trials: int = 400,
    seed: int | None = 42,
    constraints: ParetoConstraints | None = None,
    progress_callback=None,
) -> ParetoBundle:
    """Запускает один Optuna-прогон в широком SearchSpace и возвращает
    3 рекомендации, привязанные к разным критериям, с дельтами vs `base_tep`.

    v0.9.3: добавлен параметр `constraints` (ParetoConstraints) — пользователь
    может ограничить диапазон этажности и запретить отдельные типы парковок.
    v0.9.15: `progress_callback(current, total, best)` пробрасывается в Optuna
    для отображения реального прогресса в UI.
    """
    if constraints is None:
        constraints = ParetoConstraints()

    # v0.10.4: этажность зон варьируется ТОЛЬКО если заданы диапазоны ПО КАЖДОЙ
    # зоне (список совпадает по длине с числом зон). Иначе зоны фиксированы.
    _ranges = constraints.cluster_floors_ranges
    _n_zones = len(base_options.floor_clusters)
    vary_zones = (
        _n_zones > 0 and _ranges is not None and len(_ranges) == _n_zones
    )
    if vary_zones:
        base_options = base_options.model_copy(update={
            "floor_clusters": [
                c.model_copy(update={
                    "floors_min": int(min(rng)), "floors_max": int(max(rng)),
                })
                for c, rng in zip(base_options.floor_clusters, _ranges)
            ]
        })

    space = _build_search_space(
        constraints,
        has_clusters=bool(base_options.floor_clusters),
        vary_zones=vary_zones,
    )
    report: OptimizationReport = optimize_max_apartments(
        site=site,
        base_options=base_options,
        norms=norms,
        space=space,
        n_trials=n_trials,
        # top_n=300 — берём широкий пул, чтобы при стратификации по архетипу
        # парковки точно нашлись варианты разных типов.
        top_n=300,
        seed=seed,
        progress_callback=progress_callback,
    )
    recs = _select_three(report.top_n, base_tep, base_options, constraints)

    # v0.9.11 (AUDIT S-1/P1-7): диагностика «почему пусто», чтобы UI
    # мог показать прицельное сообщение пользователю.
    reason: str | None = None
    if not recs:
        if report.n_trials_feasible == 0:
            reason = (
                "Ни одно из 400 испытаний Optuna не дало feasible-результата. "
                "Вероятные причины:\n"
                "• Малый квартал (нормативы 25% озеленения + 450 чел/га физически "
                "противоречивы на участках <0.5 га);\n"
                "• Слишком узкие ограничения подбора (диапазон этажности / "
                "запрет всех типов парковок);\n"
                "• Соцобъекты не помещаются нормативно при выбранной площади.\n\n"
                "Попробуйте: отключить нормативы 25%/450 в Параметрах; "
                "разрешить все типы парковок; снять ограничение по этажности; "
                "включить «только потребность» для ДОО/СОШ."
            )
        else:
            reason = (
                f"Optuna нашёл {report.n_trials_feasible} feasible-сценариев, "
                f"но фильтры «реалистичных сочетаний парковок» отсеяли все. "
                "Отключите чекбокс «Реалистичные сочетания» в настройках подбора, "
                "чтобы увидеть все варианты, либо разрешите больше типов парковок."
            )

    return ParetoBundle(
        recommendations=recs,
        base_tep=base_tep,
        n_trials_total=report.n_trials_total,
        n_trials_feasible=report.n_trials_feasible,
        no_feasible_reason=reason,
    )
