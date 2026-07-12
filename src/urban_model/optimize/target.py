"""Подбор под ЦЕЛЕВУЮ площадь квартир (v0.16.0).

Обратная постановка к «Максимуму площади»: у пользователя есть требуемая
площадь квартир (и площадь квартала с прочими условиями) — нужно понять,
ДОСТИЖИМА ли она по нормативам, и если да — какими параметрами, в нескольких
типологически разных вариантах ПАРКОВОК (только открытые в уровне земли /
с многоуровневыми / с подземными / со стилобатом).

Механика — поверх той же машинерии, что и Парето-рекомендации:
  1) Optuna-прогон в широком SearchSpace → пул feasible-сценариев;
  2) пул группируется по СЕМЕЙСТВУ парковок (по итоговым долям TEP);
  3) в каждом семействе выбирается «самый дешёвый» способ выйти на цель —
     минимальная этажность среди достигающих (тай-брейк: выше эконом-индекс);
  4) выбранный вариант ДЕТЕРМИНИРОВАННО доводится по этажности вниз
     (тем же 2-проходным ВПП-механизмом, что trial'ы) — минимальная
     этажность, при которой цель ещё достигается;
  5) если семейство цели не достигает — показывается его максимум и разрыв.

Все варианты остаются нормативно-предельными (solve_max_kit): «выйти ровно
на цель» достигается снижением этажности, а не недозастройкой при высоком КИТ.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from urban_model.models.options import CalculationOptions
from urban_model.models.result import TEPResult
from urban_model.models.site import Site
from urban_model.normatives import Normatives
from urban_model.optimize.pareto import (
    ParetoConstraints,
    Recommendation,
    _build_search_space,
    _delta,
    _has_token_parking,
    _is_hybrid_parking,
    _parking_shares,
    apply_znop_constraints,
)
from urban_model.optimize.refine import _Evaluator, _params_of
from urban_model.optimize.runner import (
    OptimizationReport,
    OptimizationResult,
    optimize_max_apartments,
)


@dataclass(frozen=True)
class TargetBundle:
    """Итог подбора под целевую площадь квартир."""
    target_m2: float
    achievable: bool                 # достижима ли цель хоть одним сценарием
    max_apartments: float            # максимум площади по всему пулу
    recommendations: list[Recommendation] = field(default_factory=list)
    n_trials_total: int = 0
    n_trials_feasible: int = 0
    note: str | None = None          # пояснение (напр., ограничивающий фактор)


# Семейства парковок — фиксированный порядок вывода. Классификация по
# ИТОГОВЫМ долям TEP (а не по sampled-параметрам): min_open/all_open и
# custom-миксы попадают в правильную группу единообразно.
_FAMILY_ORDER = ("surface", "multilevel", "underground", "stylobate")
_FAMILY_LABELS = {
    "surface": "Только открытые (в уровне земли)",
    "multilevel": "С многоуровневыми паркингами",
    "underground": "С подземными",
    "stylobate": "Со стилобатом",
}
_FAMILY_SHARE_MIN = 0.05  # существенная доля типа → вариант в этом семействе


def _family_of(tep: TEPResult) -> str:
    """Семейство парковки по итоговым долям. Приоритет styl > ug > ml:
    с фильтром «реалистичных сочетаний» гибриды и так отсеяны, а редкие
    остатки классифицируются по самому «тяжёлому» типу."""
    sh = _parking_shares(tep)
    if sh["stylobate"] >= _FAMILY_SHARE_MIN:
        return "stylobate"
    if sh["underground"] >= _FAMILY_SHARE_MIN:
        return "underground"
    if sh["multilevel"] >= _FAMILY_SHARE_MIN:
        return "multilevel"
    return "surface"


def _family_allowed(fam: str, c: ParetoConstraints) -> bool:
    if fam == "surface":
        return c.allow_open
    if fam == "multilevel":
        return c.allow_multilevel
    if fam == "underground":
        return c.allow_underground
    return c.allow_stylobate


def _floors_key(r: OptimizationResult, base_options: CalculationOptions) -> float:
    """Этажность варианта для ранжирования «дешевле = ниже».
    При кластерах — средневзвешенная (effective_floors)."""
    eff = getattr(r.tep, "effective_floors", None)
    if eff:
        return float(eff)
    if "floors" in r.params:
        return float(r.params["floors"])
    return float(base_options.floors)


def _shares_from_params(params: dict) -> tuple | None:
    """Доли (open, ml, ug, styl) из sampled-параметров; режимы min_open /
    all_open переводятся в эквивалентный custom-набор."""
    mode = str(params.get("parking_mode", "custom"))
    if mode == "all_open":
        return (1.0, 0.0, 0.0, 0.0)
    if mode == "min_open":
        # min_open = ровно норматив открытых (12.5%), остальное в подземные.
        return (0.125, 0.0, 0.875, 0.0)
    sh = (
        round(float(params.get("parking_open_share", 0.0)), 4),
        round(float(params.get("parking_ml_share", 0.0)), 4),
        round(float(params.get("parking_ug_share", 0.0)), 4),
        round(float(params.get("parking_stylobate_share", 0.0)), 4),
    )
    return sh if abs(sum(sh) - 1.0) < 0.05 else None


def _refine_floors_to_target(
    ev: _Evaluator,
    picked: OptimizationResult,
    target: float,
    floors_lo: int,
    base_options: CalculationOptions,
) -> OptimizationResult:
    """Минимальная этажность, при которой цель ещё достигается (без кластеров).

    Сканирует этажность СНИЗУ ВВЕРХ от floors_lo до этажности picked
    (зависимость площади от этажности немонотонна из-за ступеней ЗНОП, поэтому
    берётся ПЕРВАЯ подходящая, а не бисекция). Доли парковки/этажность МУ и
    подземки — как у picked. Если ничего ниже не находится — возвращает picked.
    """
    if base_options.floor_clusters or "floors" not in picked.params:
        return picked
    shares = _shares_from_params(picked.params)
    if shares is None:
        return picked
    f0 = int(picked.params["floors"])
    ml = int(picked.params.get("multilevel_levels", 1) or 1)
    ug = int(picked.params.get("underground_levels", 1) or 1)
    for f in range(int(floors_lo), f0):
        tep = ev(f, shares, ml, ug)
        if tep is None or not tep.balance.is_feasible:
            continue
        apt = float(tep.apartments_area.value or 0.0)
        if apt >= target:
            return OptimizationResult(
                rank=0,
                apartments_area=apt,
                kit=float(tep.kit.value or 0.0),
                params=_params_of(f, shares, ml, ug, base_options),
                tep=tep,
                feasible=True,
            )
    return picked


def generate_target_recommendations(
    site: Site,
    base_options: CalculationOptions,
    norms: Normatives,
    base_tep: TEPResult,
    target_m2: float,
    n_trials: int = 700,
    seed: int | None = 42,
    constraints: ParetoConstraints | None = None,
    progress_callback=None,
    vpp_request=None,
) -> TargetBundle:
    """Найти до 4 вариантов (по семействам парковок), выходящих на целевую
    площадь квартир, либо честно показать недостижимость и разрыв."""
    if constraints is None:
        constraints = ParetoConstraints()
    target = float(target_m2)

    # ЗНОП-режим настроек подбора — как в Парето (v0.16.0).
    base_options = apply_znop_constraints(base_options, constraints)

    # Этажность зон (кластеры) — та же логика, что в Парето (v0.10.4).
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

    _vpp_mode = getattr(vpp_request, "mode", None) if vpp_request is not None else None
    space = _build_search_space(
        constraints,
        has_clusters=bool(base_options.floor_clusters),
        vary_zones=vary_zones,
        caps=None,
        vpp_fixed_mode=_vpp_mode,
        vpp_custom_4_4_m2=getattr(vpp_request, "custom_4_4_m2", None) if vpp_request else None,
        vpp_custom_4_6_m2=getattr(vpp_request, "custom_4_6_m2", None) if vpp_request else None,
    )
    report: OptimizationReport = optimize_max_apartments(
        site=site,
        base_options=base_options,
        norms=norms,
        space=space,
        n_trials=n_trials,
        # Широкий пул: для целевого режима важны и НЕ-максимальные варианты
        # (низкая этажность около цели), поэтому берём весь feasible-пул.
        top_n=n_trials,
        seed=seed,
        progress_callback=progress_callback,
    )

    pool = [r for r in report.top_n if r.feasible and r.apartments_area > 0]
    if constraints.restrict_parking_combos:
        filtered = [
            r for r in pool
            if not _is_hybrid_parking(r.params) and not _has_token_parking(r.tep)
        ]
        if filtered:
            pool = filtered
    _ok_znop = [
        r for r in pool
        if not any("ZNOP_BELOW_MIN" in w for w in (r.tep.warnings or []))
    ]
    if _ok_znop:
        pool = _ok_znop

    if not pool:
        return TargetBundle(
            target_m2=target, achievable=False, max_apartments=0.0,
            n_trials_total=report.n_trials_total,
            n_trials_feasible=report.n_trials_feasible,
            note=(
                "Подбор не нашёл ни одного допустимого сценария — цель "
                "проверить не на чем. Ослабьте ограничения подбора "
                "(диапазон этажности, типы парковок) или нормативы."
            ),
        )

    max_apartments = max(r.apartments_area for r in pool)
    achievable = max_apartments >= target

    # Оценщик для детерминированной доводки этажности (общий memo на все семейства).
    floors_lo = int(min(constraints.floors_range))
    ev = _Evaluator(
        site=site, base_options=base_options, norms=norms, vpp_mode=_vpp_mode,
        vpp_c44=getattr(vpp_request, "custom_4_4_m2", None) if vpp_request else None,
        vpp_c46=getattr(vpp_request, "custom_4_6_m2", None) if vpp_request else None,
    )

    recs: list[Recommendation] = []
    for fam in _FAMILY_ORDER:
        if not _family_allowed(fam, constraints):
            continue
        fam_pool = [r for r in pool if _family_of(r.tep) == fam]
        if not fam_pool:
            continue
        reaching = [r for r in fam_pool if r.apartments_area >= target]
        if reaching:
            # «Дешевле всего»: минимальная этажность; тай-брейк — эконом-индекс.
            picked = min(reaching, key=lambda r: (
                _floors_key(r, base_options),
                -(r.tep.economy.economy_index if r.tep.economy is not None else 0.0),
            ))
            picked = _refine_floors_to_target(ev, picked, target, floors_lo, base_options)
            surplus = picked.apartments_area - target
            fl = _floors_key(picked, base_options)
            rationale = (
                f"Цель достигается при {fl:.0f} эт.: "
                f"{picked.apartments_area:,.0f} м² "
                f"(запас +{surplus:,.0f} м², {surplus / target * 100:+.1f}%)."
            ).replace(",", " ")
        else:
            picked = max(fam_pool, key=lambda r: r.apartments_area)
            gap = target - picked.apartments_area
            rationale = (
                f"Цель НЕ достигается в этом составе парковок: максимум "
                f"{picked.apartments_area:,.0f} м² "
                f"(до цели −{gap:,.0f} м², −{gap / target * 100:.1f}%)."
            ).replace(",", " ")
        recs.append(Recommendation(
            label=_FAMILY_LABELS[fam],
            rationale=rationale,
            params=dict(picked.params),
            tep=picked.tep,
            delta_vs_base=_delta(base_tep, picked.tep, base_options, picked.params),
        ))

    note: str | None = None
    if not achievable:
        best = max(pool, key=lambda r: r.apartments_area)
        lf = getattr(best.tep, "limiting_factor", None)
        note = (
            f"Цель {target:,.0f} м² недостижима в заданных рамках: максимум "
            f"{max_apartments:,.0f} м² "
            f"(−{(target - max_apartments) / target * 100:.1f}%)."
        ).replace(",", " ")
        if lf:
            note += f" Ограничивающий фактор лучшего варианта: {lf}"
        note += (
            " Попробуйте расширить диапазон этажности или разрешить "
            "дополнительные типы парковок в «Настройках подбора»."
        )

    return TargetBundle(
        target_m2=target,
        achievable=achievable,
        max_apartments=max_apartments,
        recommendations=recs,
        n_trials_total=report.n_trials_total,
        n_trials_feasible=report.n_trials_feasible,
        note=note,
    )
