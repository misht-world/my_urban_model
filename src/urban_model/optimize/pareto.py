"""Парето-рекомендации (v0.9.0; 4 стратегии — v0.12.1).

Один прогон Optuna в широком SearchSpace → до 4 готовых рекомендаций:
  • Максимум площади квартир
  • Максимум эконом-индекса (стабильный 100 × выручка / себестоимость)
  • Пороговый (эконом-индекс ≈ 100 — предельная застройка на грани окупаемости)
  • Девелоперский (практичный: уклон в экономику, без перекосов/хрупкости)

Optuna здесь — ГЕНЕРАТОР допустимых сценариев, а не «выбор лучшего проекта».
Пользователь видит не один «лучший», а несколько осмысленных стратегий
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
    # v0.12.1: дельта экономического индекса (в пунктах индекса, не %).
    d_index_abs: float | None = None
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
    allow_stylobate: bool = True  # v0.12.2
    restrict_parking_combos: bool = True
    # v0.10.4: диапазон этажности ПО КАЖДОЙ ЗОНЕ для подбора (список (lo,hi) в
    # порядке зон). None → этажность зон НЕ варьируется (берётся из базы).
    # Длина должна совпадать с числом зон; иначе варьирование игнорируется.
    cluster_floors_ranges: tuple[tuple[int, int], ...] | None = None
    # v0.16.0: режим ЗНОП для подбора. По умолчанию — как в Базе (наследуется
    # из options «Параметров»). "normative" — сбросить ручные оверрайды, ЗНОП
    # считается piecewise от КИТ каждого варианта. "manual" — принудительное
    # значение м²/чел (znop_value); связка ЗНОП→потолок КИТ (v0.7.4) применится
    # автоматически, занизить норматив этим нельзя.
    znop_mode: str = "base"          # "base" | "normative" | "manual"
    znop_value: float | None = None  # м²/чел (только для znop_mode="manual")


def apply_znop_constraints(
    base_options: CalculationOptions, constraints: "ParetoConstraints | None",
) -> CalculationOptions:
    """Применить ЗНОП-режим настроек подбора к шаблону options (v0.16.0).

    Возвращает НОВЫЙ options (копию) при отличии от режима «как в Базе»;
    иначе исходный объект без копирования.
    """
    if constraints is None or constraints.znop_mode == "base":
        return base_options
    if constraints.znop_mode == "normative":
        return base_options.model_copy(update={
            "znop_per_person_override": None,
            "znop_total_area_override": None,
        })
    if constraints.znop_mode == "manual" and constraints.znop_value is not None:
        return base_options.model_copy(update={
            "znop_per_person_override": float(constraints.znop_value),
            "znop_total_area_override": None,
        })
    return base_options


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
# v0.12.6: стилобат — целое монолитное сооружение (дека + двор сверху),
# строить его ради горстки мест нерационально. Порог выше, чем у МУ/подземки.
_MIN_STYL_PLACES_ABS = 60
_MIN_FRACTION_OF_TOTAL = 0.05


def _has_token_parking(tep: TEPResult) -> bool:
    """True если в варианте есть «символическое» количество МУ, подземных
    ИЛИ стилобатных мест: 0 < N < max(абсолютный_порог, 5% от общего числа м/м).
    На больших кварталах порог растёт пропорционально, на малых — снижается
    до абсолютного минимума. v0.12.6: стилобат включён в фильтр.
    """
    ml = int(tep.parking_multilevel_places.value or 0)
    ug = int(tep.parking_underground_places.value or 0)
    styl = int(getattr(tep, "parking_stylobate_places", None).value or 0) \
        if getattr(tep, "parking_stylobate_places", None) is not None else 0
    total = int(tep.parking_required_places.value or 0)
    # Адаптивный порог: max(абсолютный, доля от total)
    th_ml = max(_MIN_ML_PLACES_ABS, int(total * _MIN_FRACTION_OF_TOTAL))
    th_ug = max(_MIN_UG_PLACES_ABS, int(total * _MIN_FRACTION_OF_TOTAL))
    th_styl = max(_MIN_STYL_PLACES_ABS, int(total * _MIN_FRACTION_OF_TOTAL))
    if 0 < ml < th_ml:
        return True
    if 0 < ug < th_ug:
        return True
    if 0 < styl < th_styl:
        return True
    return False


def _is_kit_at_ceiling(tep: TEPResult, tol: float = 0.01) -> bool:
    """True если КИТ упёрся в нормативный потолок (хрупкое решение —
    нет запаса до ограничения). Слабый сигнал для девелоперского отбора."""
    try:
        kit = float(tep.kit.value or 0.0)
        kmax = float(tep.kit_normative_max.value or 0.0)
    except (TypeError, ValueError):
        return False
    return kmax > 0 and kit >= kmax - tol


def _parking_rationality(tep: TEPResult, residential_class: str, norms) -> float:
    """Рыночная уместность парковочного микса для класса жилья (0…1, v0.12.2).

    = Σ(доля_типа × пригодность[класс][тип]) по фактическим м/м варианта.
    Матрица пригодности — economy.parking_suitability в YAML.
    """
    o = float(tep.parking_open_places.value or 0)
    m = float(tep.parking_multilevel_places.value or 0)
    u = float(tep.parking_underground_places.value or 0)
    s = float(getattr(tep, "parking_stylobate_places", None).value or 0) \
        if getattr(tep, "parking_stylobate_places", None) is not None else 0.0
    total = o + m + u + s
    if total <= 0:
        return 0.5  # нет парковок — нейтрально
    try:
        suit = norms.resolve(
            "economy.parking_suitability", residential_class=residential_class)
    except (KeyError, TypeError, ValueError):
        return 0.5
    return (
        o / total * float(suit.get("open", 0.5))
        + m / total * float(suit.get("multilevel", 0.5))
        + u / total * float(suit.get("underground", 0.5))
        + s / total * float(suit.get("stylobate", 0.5))
    )


def _parking_shares(tep: TEPResult) -> dict[str, float]:
    """Итоговые доли типов парковки от ОБЩЕГО числа м/м (v0.12.7)."""
    o = float(tep.parking_open_places.value or 0)
    m = float(tep.parking_multilevel_places.value or 0)
    u = float(tep.parking_underground_places.value or 0)
    s = float(getattr(tep, "parking_stylobate_places", None).value or 0) \
        if getattr(tep, "parking_stylobate_places", None) is not None else 0.0
    total = o + m + u + s
    if total <= 0:
        return {"open": 0.0, "multilevel": 0.0, "underground": 0.0, "stylobate": 0.0}
    return {
        "open": o / total, "multilevel": m / total,
        "underground": u / total, "stylobate": s / total,
    }


def _violates_parking_caps(tep: TEPResult, caps: dict | None, tol: float = 0.02) -> bool:
    """True если итоговая доля какого-либо типа превышает потолок класса (v0.12.7)."""
    if not caps:
        return False
    sh = _parking_shares(tep)
    for key, frac in sh.items():
        cap = float(caps.get(key, 1.0))
        if frac > cap + tol:
            return True
    return False


def _robustness_score(tep: TEPResult) -> float:
    """«Крепость» решения 0…1 (v0.12.2): 1 = надёжно, 0 = хрупко.

    Штрафы: warning-и, КИТ на нормативном потолке, баланс почти в ноль
    (слабый), плотность у верхней границы.
    """
    score = 1.0
    score -= 0.10 * min(len(tep.warnings or []), 5)
    if _is_kit_at_ceiling(tep):
        score -= 0.15
    # Баланс почти в ноль — слабый технический сигнал (НЕ критерий качества).
    try:
        site = float(tep.balance.site_area or 0.0)
        surplus = float(tep.balance.surplus or 0.0)
        if site > 0 and surplus < 0.005 * site:
            score -= 0.05
    except (TypeError, ValueError, AttributeError):
        pass
    # Плотность у верхней границы (≥95% норматива).
    try:
        d = float(tep.density_chel_per_ga.value or 0.0)
        dn = float(tep.density_chel_per_ga.normative or 0.0)
        if dn > 0 and d >= 0.95 * dn:
            score -= 0.10
    except (TypeError, ValueError, AttributeError):
        pass
    return max(0.0, min(1.0, score))


def _social_plot_per_place(tep: TEPResult) -> float:
    """ЗУ соцобъектов (ДОО+СОШ) на 1 место, м²/место (v0.12.4).

    Ниже = эффективнее: piecewise-норматив даёт больше м²/место для мелких
    объектов, поэтому «2 маленьких» и «перескок на 2 с недозагрузкой» дают
    более высокий показатель. Используется как тай-брейк в developer_score.
    Возвращает 0.0, если соцобъектов нет (нейтрально).
    """
    plot = (
        float(tep.kindergarten_plot_area.value or 0.0)
        + float(tep.school_plot_area.value or 0.0)
    )
    places = (
        float(tep.kindergarten_places_accepted.value or 0.0)
        + float(tep.school_places_accepted.value or 0.0)
    )
    return plot / places if places > 0 else 0.0


def _engineering_risk_penalty(tep: TEPResult) -> float:
    """Штраф за число крупных инженерных объектов (котельные + ОСПС) сверх
    базовых 1+1 (v0.12.2). Тай-брейк: при близких вариантах предпочесть тот,
    где меньше крупных объектов инфраструктуры."""
    eng = getattr(tep, "engineering", None)
    if eng is None:
        return 0.0
    big = sum(
        o.count for o in eng.objects
        if o.key in ("boiler", "osps") and o.in_balance
    )
    return 0.03 * max(0, big - 2)


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
    # Доля стилобата (v0.12.2)
    if "parking_stylobate_share" in scenario_params:
        new_s = float(scenario_params["parking_stylobate_share"])
        old_s = float(getattr(base_options.parking, "stylobate_share", 0.0) or 0.0)
        if abs(new_s - old_s) > 0.05:
            changes.append(f"Стилобат: {old_s*100:.0f}% → {new_s*100:.0f}%")
    # ВПП-режим: НЕ показываем как «изменение» — режим ВПП фиксирован из
    # «Параметров» (vpp_fixed_mode), одинаков у базы и всех рекомендаций
    # (v0.12.26). Раньше ошибочно писалось «ВПП: 50% 1 этажа» в каждой карточке.
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
    d_index_abs: float | None = None
    if base_tep.economy is not None and scenario_tep.economy is not None:
        base_p = float(base_tep.economy.profit)
        sc_p = float(scenario_tep.economy.profit)
        d_profit_abs = sc_p - base_p
        d_profit_pct = (d_profit_abs / abs(base_p) * 100.0) if abs(base_p) > 1e-9 else 0.0
        d_index_abs = (
            float(scenario_tep.economy.economy_index)
            - float(base_tep.economy.economy_index)
        )

    d_kit_abs = float(scenario_tep.kit.value or 0.0) - float(base_tep.kit.value or 0.0)

    return DeltaSummary(
        d_apt_abs=d_apt_abs,
        d_apt_pct=d_apt_pct,
        d_profit_abs=d_profit_abs,
        d_profit_pct=d_profit_pct,
        d_kit_abs=d_kit_abs,
        d_index_abs=d_index_abs,
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
    # v0.12.2: существенная доля стилобата — отдельный архетип (для разнообразия).
    styl = float(p.get("parking_stylobate_share", 0.0))
    if styl >= 0.4:
        return "stylobate"
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
        round(float(p.get("parking_stylobate_share", 0.0)), 1),  # v0.12.2
    )


def _select_three(
    top_n: list[OptimizationResult],
    base_tep: TEPResult,
    base_options: CalculationOptions,
    constraints: "ParetoConstraints | None" = None,
    norms=None,
) -> list[Recommendation]:
    """Из топа выбирает до 4 рекомендаций по разным критериям + DeltaSummary.

    v0.12.1: 4 стратегии — Максимум площади / Максимум эконом-индекса /
    Пороговый (≈100) / Девелоперский (практичный). Ранжирование
    экономики — по стабильному economy_index, а не по сырой прибыли.

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

    # v0.12.11: отсев сценариев с нарушением минимума ЗНОП по ПЗЗ — ДО
    # разделения на пулы (актуально при ручном override площади ЗНОП). С откатом.
    _ok_znop = [
        r for r in feasible
        if not any("ZNOP_BELOW_MIN" in w for w in (r.tep.warnings or []))
    ]
    if _ok_znop:
        feasible = _ok_znop

    # v0.12.31: «Базу кандидатом в пул» (костыль v0.12.30) заменило
    # детерминированное доуточнение экстремумов (refine_extrema) в
    # generate_pareto_recommendations — оно стартует в т.ч. от конфига Базы,
    # поэтому «Макс. площади»/«Макс. индекса» ≥ База естественно, и при этом
    # находит ИСТИННЫЙ максимум (промежуточные доли), а не просто саму Базу.

    # v0.12.26: потолки долей парковки по классу применяем ТОЛЬКО к
    # «реалистичным» карточкам (Пороговый/Девелоперский). «Максимум площади» и
    # «Максимум эконом-индекса» ищут ПРЕДЕЛЬНЫЕ значения БЕЗ потолков — иначе
    # «Макс. площади» оказывался < Базы (если База использует мимо-потолочный
    # микс, напр. min_open 88% подземки в Экономе), а индекс не мог превысить
    # естественный максимум застройки.
    _caps = None
    if norms is not None:
        try:
            _caps = norms.resolve(
                "economy.parking_caps",
                residential_class=getattr(base_options, "residential_class", "comfort"),
            )
        except (KeyError, TypeError, ValueError):
            _caps = None
    pool_uncapped = feasible
    if _caps:
        _capped = [r for r in feasible if not _violates_parking_caps(r.tep, _caps)]
        pool_capped = _capped if _capped else feasible
    else:
        pool_capped = feasible

    with_econ = [r for r in pool_uncapped if r.tep.economy is not None]       # экстремумы
    with_econ_real = [r for r in pool_capped if r.tep.economy is not None]    # реалистичные

    # Экстремальные карточки — по НЕограниченному пулу (площадь / индекс).
    apt_sorted = sorted(pool_uncapped, key=lambda r: r.apartments_area, reverse=True)
    # v0.12.1: ранжируем по СТАБИЛЬНОМУ эконом-индексу (100 × выручка / себест.).
    index_sorted = (
        sorted(with_econ, key=lambda r: r.tep.economy.economy_index, reverse=True)
        if with_econ else apt_sorted
    )

    # v0.19.2: экономика может быть ОТКЛЮЧЕНА (include_economy=False) — тогда
    # индекса нет вовсе. Карточки «Максимум эконом-индекса» и «Пороговый» без
    # него бессмысленны (раньше молча вырождались в копии «Максимума площади»),
    # поэтому не строятся. «Девелоперский» остаётся — его не-экономические
    # составляющие (рациональность парковок, устойчивость, инженерка, соц,
    # ЗНОП) считаются и без экономики; вес индекса перераспределяется.
    has_econ = bool(with_econ)
    # Пул для Девелоперского: реалистичный (с потолками класса). Без экономики
    # берём pool_capped напрямую — иначе фильтр потолков молча отключался
    # вместе с экономикой и карточка предлагала нереальные миксы парковок.
    dev_pool = with_econ_real if has_econ else pool_capped

    # Нормировки apt и economy_index (min-max по пулу) — для blended-скоров.
    # v0.12.26: Пороговый/Девелоперский считаются по РЕАЛИСТИЧНОМУ пулу
    # (с потолками класса), поэтому нормировки — по нему же.
    if dev_pool and len(dev_pool) >= 2:
        apts = [r.apartments_area for r in dev_pool]
        apt_min, apt_max = min(apts), max(apts)
        apt_range = apt_max - apt_min if apt_max > apt_min else 1.0

        def _apt_n(r: OptimizationResult) -> float:
            return (r.apartments_area - apt_min) / apt_range

        if has_econ:
            idxs = [r.tep.economy.economy_index for r in dev_pool]
            i_min, i_max = min(idxs), max(idxs)
            i_range = i_max - i_min if i_max > i_min else 1.0

            def _idx_n(r: OptimizationResult) -> float:
                return (r.tep.economy.economy_index - i_min) / i_range
        else:
            def _idx_n(r: OptimizationResult) -> float:
                return 0.0

        # v0.12.4: соц-эффективность (ЗУ/место) — нормировка по пулу для
        # тай-брейка в developer_score (ниже ЗУ/место → лучше → меньше штраф).
        _spps = [_social_plot_per_place(r.tep) for r in dev_pool]
        _spp_min, _spp_max = min(_spps), max(_spps)
        _spp_range = _spp_max - _spp_min if _spp_max > _spp_min else 1.0

        def _spp_n(r: OptimizationResult) -> float:
            return (_social_plot_per_place(r.tep) - _spp_min) / _spp_range

        # v0.12.11: ЗНОП/чел — нормировка по пулу. Больше ЗНОП = качественнее
        # среда = легче продавать (нематериальный плюс, НЕ учтённый в economy).
        # Малый положительный вес как тай-брейк в developer_score.
        def _znop_pp(r: OptimizationResult) -> float:
            return float(r.tep.znop_per_person.value or 0.0)
        _znops = [_znop_pp(r) for r in dev_pool]
        _znop_min, _znop_max = min(_znops), max(_znops)
        _znop_range = _znop_max - _znop_min if _znop_max > _znop_min else 1.0

        def _znop_n(r: OptimizationResult) -> float:
            return (_znop_pp(r) - _znop_min) / _znop_range

        # Пороговый (v0.12.3): вариант на пороге окупаемости — эконом-индекс
        # ближе всего к 100 (выручка ≈ себестоимость). Это «потолок застройки
        # при нулевом запасе экономики». Без экономики карточка не строится.
        threshold_sorted = (
            sorted(with_econ_real,
                   key=lambda r: abs(r.tep.economy.economy_index - 100.0))
            if has_econ else []
        )
        # Девелоперский (v0.12.2) — полноценный developer_score (аудит-формула):
        #   0.50·эконом-индекс + 0.25·площадь + 0.15·парк.рациональность
        #   + 0.10·robustness − риск(крупная инженерка)
        # Парк.рациональность учитывает класс жилья (матрица пригодности).
        _res_class = getattr(base_options, "residential_class", "comfort")

        # v0.19.2: без экономики вес индекса (0.50) перераспределяется на
        # остальные слагаемые пропорционально — карточка остаётся осмысленной
        # («максимум площади, но без перекосов парковок и хрупких решений»)
        # и отличается от чистого «Максимума площади».
        _w = (0.25, 0.15, 0.10) if has_econ else (0.50, 0.30, 0.20)

        def _dev_score(r: OptimizationResult) -> float:
            rationality = (
                _parking_rationality(r.tep, _res_class, norms)
                if norms is not None else 0.5
            )
            robustness = _robustness_score(r.tep)
            risk = _engineering_risk_penalty(r.tep)
            # v0.12.4: соц-нагрузка как тай-брейк — штраф за неэффективную
            # соцземлю (мелкие/недозагруженные ДОО/СОШ → выше ЗУ/место).
            social_penalty = 0.05 * _spp_n(r)
            # v0.12.11: малый бонус за ЗНОП/чел (качество среды, продаваемость).
            znop_bonus = 0.04 * _znop_n(r)
            return (
                (0.50 * _idx_n(r) if has_econ else 0.0)
                + _w[0] * _apt_n(r)
                + _w[1] * rationality
                + _w[2] * robustness
                - risk
                - social_penalty
                + znop_bonus
            )

        developer_sorted = sorted(dev_pool, key=_dev_score, reverse=True)
    else:
        threshold_sorted = apt_sorted if has_econ else []
        developer_sorted = dev_pool or apt_sorted

    rationales = {
        "Максимум площади": "Наибольшая площадь квартир — максимальный выход ТЭП.",
        "Максимум эконом-индекса": "Наивысший экономический индекс (выручка ÷ себестоимость).",
        "Пороговый": "На пороге окупаемости (эконом-индекс ≈ 100): предельная застройка при нулевом запасе экономики.",
        "Девелоперский": (
            "Практичный вариант для дальнейшей проработки: уклон в экономику, "
            "без перекосов парковок и спорных решений."
            if has_econ else
            "Практичный вариант для дальнейшей проработки: близко к максимуму "
            "площади, но без перекосов парковок и хрупких решений. "
            "Экономика отключена — карточки по эконом-индексу не строятся."
        ),
    }

    def _pick(
        pool: list[OptimizationResult],
        seen_fps: set[tuple],
        seen_ids: set[int],
    ) -> OptimizationResult | None:
        """Выбрать лучший из pool (он уже отсортирован по метрике карточки).

        v0.12.12: дедуп по fingerprint (чтобы карточки не совпадали 1-в-1).
        v0.12.13: если уникального fingerprint нет (узкий поиск — напр. только
        open+МУ → мало различных типов), берём ЛЮБОЙ ещё не показанный сценарий.
        Так карточки «Пороговый»/«Девелоперский» не пропадают при ограничении
        типов парковок — они просто могут оказаться близки к другим.
        """
        # 1) предпочитаем сценарий с НОВЫМ fingerprint (типологически отличный)
        for r in pool:
            if id(r) in seen_ids:
                continue
            fp = _params_fingerprint(r)
            if fp not in seen_fps:
                seen_fps.add(fp); seen_ids.add(id(r))
                return r
        # 2) fallback: любой ещё не показанный результат (fingerprint может
        #    повторяться, но сам сценарий — другой по этажности/долям/метрике)
        for r in pool:
            if id(r) not in seen_ids:
                seen_ids.add(id(r))
                return r
        return None

    recs: list[Recommendation] = []
    seen_fps: set[tuple] = set()
    seen_ids: set[int] = set()
    _index_top_id = id(index_sorted[0]) if (has_econ and index_sorted) else None
    # v0.19.2: без экономики строим только «Максимум площади» + «Девелоперский».
    _cards: list[tuple[str, list[OptimizationResult]]] = [
        ("Максимум площади", apt_sorted)]
    if has_econ:
        _cards += [("Максимум эконом-индекса", index_sorted),
                   ("Пороговый", threshold_sorted)]
    _cards.append(("Девелоперский", developer_sorted))
    for i, (label, pool) in enumerate(_cards):
        if label == "Пороговый" and pool:
            # v0.12.26: «Пороговый» — ЧЕСТНО ближайший к 100, БЕЗ дедупа.
            # Может совпасть с «Макс. индекса», если проект сабокупаемый (макс.
            # индекс < 100 → ближайший к 100 = максимум). Это корректно: раньше
            # дедуп толкал Пороговый на худший вариант (напр. 92 вместо 97).
            picked = pool[0]
            seen_ids.add(id(picked))
            seen_fps.add(_params_fingerprint(picked))
        else:
            picked = _pick(pool, seen_fps, seen_ids)
        if picked is None:
            continue
        rationale = rationales[label]
        if (label == "Пороговый" and _index_top_id is not None
                and id(picked) == _index_top_id):
            rationale += (
                " Проект сабокупаемый (макс. индекс < 100), поэтому ближайший "
                "к 100 совпал с «Максимумом эконом-индекса»."
            )
        elif label == "Пороговый":
            # v0.14.2 (зеркальный случай к сабокупаемости): в заданных рамках
            # подбора ВСЕ сценарии прибыльные (минимальный индекс пула заметно
            # выше 100) — ближайший к 100 = минимальный, и он «сильно выше
            # 100». Поясняем и подсказываем расширить рамки (обычно диапазон
            # этажности: выше этажность → дороже строить → индекс ниже).
            _pool_idx = [r.tep.economy.economy_index for r in pool
                         if r.tep.economy is not None]
            if _pool_idx and min(_pool_idx) > 102.0:
                rationale += (
                    f" В заданных рамках подбора все варианты прибыльные "
                    f"(минимальный индекс {min(_pool_idx):.0f} > 100) — ближе "
                    f"к порогу окупаемости подойти нельзя. Чтобы найти "
                    f"предельную застройку (индекс ≈ 100), расширьте диапазон "
                    f"этажности в «Настройках подбора»."
                )
        recs.append(Recommendation(
            label=label,
            rationale=rationale,
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
    caps: dict | None = None,
    vpp_fixed_mode: str | None = None,
    vpp_custom_4_4_m2: float | None = None,
    vpp_custom_4_6_m2: float | None = None,
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

    # v0.12.7: верхние границы диапазонов перебора — из потолков класса
    # (caps). Это мягкая направляющая для Optuna (жёсткую гарантию даёт фильтр
    # рекомендаций); сужает зону поиска под реалистичные для класса миксы.
    def _cap(key: str, default: float) -> float:
        return float(caps.get(key, default)) if caps else default

    # Диапазоны долей: если тип запрещён — фиксируем 0..0
    ug_range = (0.0, _cap("underground", 1.0)) if constraints.allow_underground else (0.0, 0.0)
    ml_range = (0.0, _cap("multilevel", 0.5)) if constraints.allow_multilevel else (0.0, 0.0)
    # Open: минимум 12.5% (норматив). Верх — по потолку класса.
    open_range = (0.125, max(0.125, _cap("open", 0.5))) if constraints.allow_open else (0.125, 0.125)
    styl_cap = _cap("stylobate", 0.7)

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
        # v0.12.2/v0.12.7: стилобат — диапазон до потолка класса.
        parking_stylobate_share_range=(0.0, styl_cap) if constraints.allow_stylobate else None,
        # v0.9.4: ЗНОП НЕ варьируется в Парето — он считается по нормативу
        # piecewise(КИТ ПЗЗ). Принудительный ЗНОП имеет смысл ТОЛЬКО когда
        # бисекция упирается в потолок КИТ; если КИТ ниже нормативной ступени,
        # принудительный ЗНОП лишь «проедает» квартал зеленью без пользы.
        # Анализ влияния ЗНОП остаётся в карточке «🌳 ЗНОП» Пофакторного.
        znop_per_person_choices=None,
        # v0.12.14: ВПП — фиксированный режим из «Параметров» (пересчёт площади
        # каждый trial); подбор не отменяет и не меняет принцип расчёта ВПП.
        vpp_fixed_mode=vpp_fixed_mode,
        vpp_custom_4_4_m2=vpp_custom_4_4_m2,
        vpp_custom_4_6_m2=vpp_custom_4_6_m2,
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
    vpp_request=None,
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

    # v0.16.0: ЗНОП-режим настроек подбора («по нормативу» / «вручную»)
    # применяется к шаблону options ДО всех trial'ов и доуточнения. Дельты
    # считаются по-прежнему против base_tep (настоящей Базы с «Расчёта»).
    base_options = apply_znop_constraints(base_options, constraints)

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

    # v0.12.29.x: search space БЕЗ потолков класса (caps=None) — Optuna
    # исследует ВЕСЬ диапазон долей парковки (вкл. мимо-потолочные миксы вроде
    # min_open 88% подземки, как у Базы). Потолки класса остаются ТОЛЬКО в
    # фильтре пула (_violates_parking_caps) для «Пороговый»/«Девелоперский».
    # Так «Максимум площади»/«Максимум индекса» честно ищут предельные значения
    # (раньше слой диапазонов Optuna втихую ограничивал и их). Раньше: caps=_pcaps.
    # v0.12.14: ВПП — фиксированный режим из «Параметров» (vpp_request), чтобы
    # подбор пересчитывал площадь ВПП каждый trial, но НЕ менял принцип расчёта
    # и не отключал ВПП.
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
        # top_n=300 — берём широкий пул, чтобы при стратификации по архетипу
        # парковки точно нашлись варианты разных типов.
        top_n=300,
        seed=seed,
        progress_callback=progress_callback,
    )

    # v0.12.31: детерминированное доуточнение экстремумов «Максимум площади» /
    # «Максимум индекса» координатным спуском (площадь/индекс — гладкие функции
    # малой размерности; случайный Optuna их разреженно покрывает и промахивается
    # мимо истинного оптимума). Старты — лучшие кандидаты Optuna + конфиг Базы.
    # Результаты добавляются в пул наравне с trial'ами (заменяет костыль v0.12.30).
    # NB: при кластерах этажности (зонах) доуточнение пропускаем — этажность зон
    # подбирает Optuna (vary_cluster_floors); спуск по долям парковки с
    # фиксированными зонами дал бы кандидата с базовыми зонами вне диапазонов
    # перебора (нарушил бы cluster_floors_ranges).
    pool = report.top_n
    if not base_options.floor_clusters:
        try:
            from urban_model.optimize.refine import refine_extrema
            _feas = [r for r in report.top_n if r.feasible and r.apartments_area > 0]
            _seeds = _feas[:1]  # лучший по площади (top_n уже отсортирован)
            if _feas:
                _seeds = _feas[:1] + sorted(
                    (r for r in _feas if r.tep.economy is not None),
                    key=lambda r: r.tep.economy.economy_index, reverse=True,
                )[:1]
            refined = refine_extrema(
                site, base_options, norms, constraints, base_tep, _seeds, vpp_request,
            )
            pool = list(refined) + list(report.top_n)
        except Exception:  # noqa: BLE001 — доуточнение не должно ронять подбор
            pool = report.top_n

    recs = _select_three(pool, base_tep, base_options, constraints, norms)

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
