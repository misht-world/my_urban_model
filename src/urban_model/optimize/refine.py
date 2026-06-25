"""Детерминированное доуточнение экстремумов «Максимум площади» / «Максимум
эконом-индекса» (v0.12.31).

Случайный Optuna-перебор покрывает пространство РАЗРЕЖЕННО — для гладких,
малоразмерных функций (площадь/индекс зависят в основном от этажности и долей
парковки) он почти никогда не попадает в точный оптимум, а «промежутки»
(50.0→50.5→51.0…) не проходятся вовсе.

Здесь — координатный спуск: от стартовых точек (лучшие кандидаты Optuna +
конфиг Базы) мелким шагом проходим этажность и доли парковки, карабкаясь к
локальному максимуму. Это находит ИСТИННЫЙ максимум площади/индекса (с
промежутками) и делает «добавление Базы кандидатом» (костыль v0.12.30)
ненужным — поиск естественно ≥ Базы.

Оценка конфига — тем же 2-проходным механизмом ВПП, что и trial'ы Optuna
(footprint/pop без ВПП → build_built_ins → основной solve), поэтому результаты
сравнимы с результатами Optuna напрямую.
"""

from __future__ import annotations

from dataclasses import dataclass

from urban_model.calculations import vpp as _vpp
from urban_model.models.options import CalculationOptions
from urban_model.models.parking import ParkingConfig
from urban_model.models.result import TEPResult
from urban_model.models.site import Site
from urban_model.normatives import Normatives
from urban_model.optimize.runner import OptimizationResult

_IDX = {"open": 0, "ml": 1, "ug": 2, "styl": 3}
_STEP = 0.02            # шаг линейного поиска по доле (2%)
_PASSES = 3            # циклов координатного спуска
_OPEN_MIN = 0.125      # норматив СПб (≥12.5% открытых)


def _active_types(constraints) -> list[str]:
    """Разрешённые типы парковки из ограничений подбора."""
    act = ["open"] if (constraints is None or constraints.allow_open) else []
    if constraints is None or constraints.allow_multilevel:
        act.append("ml")
    if constraints is None or constraints.allow_underground:
        act.append("ug")
    if constraints is None or getattr(constraints, "allow_stylobate", True):
        act.append("styl")
    return act or ["open"]


def _set_share(shares: tuple, t: str, v: float, active: list[str]) -> tuple:
    """Установить долю типа `t` = v; остаток распределить по другим активным
    типам пропорционально текущим (или поровну). Неактивные → 0."""
    new = [0.0, 0.0, 0.0, 0.0]
    for a in active:
        new[_IDX[a]] = shares[_IDX[a]]
    new[_IDX[t]] = v
    others = [a for a in active if a != t]
    rem = max(0.0, 1.0 - v)
    cur = sum(shares[_IDX[a]] for a in others)
    if others:
        if cur > 1e-9:
            for a in others:
                new[_IDX[a]] = shares[_IDX[a]] / cur * rem
        else:
            for a in others:
                new[_IDX[a]] = rem / len(others)
    return (round(new[0], 4), round(new[1], 4), round(new[2], 4), round(new[3], 4))


@dataclass
class _Evaluator:
    site: Site
    base_options: CalculationOptions
    norms: Normatives
    vpp_mode: str | None
    vpp_c44: float | None
    vpp_c46: float | None

    def __post_init__(self):
        self._memo: dict = {}
        self._has_clusters = bool(self.base_options.floor_clusters)

    def __call__(self, floors, shares: tuple, ml_levels: int, ug_levels: int) -> TEPResult | None:
        key = (floors, shares, int(ml_levels), int(ug_levels))
        if key in self._memo:
            return self._memo[key]
        from urban_model import solve_max_kit
        opts = self.base_options.model_copy(deep=True)
        if not self._has_clusters and floors is not None:
            opts.floors = int(floors)
        o, m, u, s = shares
        opts.parking = ParkingConfig(
            mode="custom", open_share=o, multilevel_share=m,
            underground_share=u, stylobate_share=s,
            multilevel_levels=int(ml_levels),
            underground_levels=int(ug_levels),
        )
        opts.built_in = None
        opts.built_in_list = []
        # ВПП — 2-проход (как trial Optuna): footprint/pop без ВПП → построение.
        if self.vpp_mode:
            try:
                r0 = solve_max_kit(self.site, opts, self.norms)
                build = _vpp.build_built_ins(
                    mode=self.vpp_mode,
                    population=r0.population.value or 0.0,
                    footprint=r0.housing_footprint.value or 0.0,
                    norms=self.norms,
                    custom_4_4_m2=self.vpp_c44, custom_4_6_m2=self.vpp_c46,
                )
                opts.built_in_list = build.built_ins
            except (ValueError, KeyError):
                pass
        try:
            tep = solve_max_kit(self.site, opts, self.norms)
        except (ValueError, KeyError):
            tep = None
        self._memo[key] = tep
        return tep


def _objective(tep: TEPResult | None, key: str) -> float:
    """Значение целевой функции для feasible-результата; -inf иначе."""
    if tep is None or not tep.balance.is_feasible:
        return float("-inf")
    if key == "index":
        return tep.economy.economy_index if tep.economy is not None else float("-inf")
    return float(tep.apartments_area.value or 0.0)


def _params_of(floors, shares: tuple, ml_levels: int, ug_levels: int,
               base_options: CalculationOptions) -> dict:
    """Params в формате trial'ов (для _rec_options_from_params / дельт)."""
    p: dict = {
        "parking_mode": "custom",
        "parking_open_share": round(shares[0], 4),
        "parking_ml_share": round(shares[1], 4),
        "parking_ug_share": round(shares[2], 4),
        "parking_stylobate_share": round(shares[3], 4),
        "multilevel_levels": int(ml_levels),
        "underground_levels": int(ug_levels),
    }
    if base_options.floor_clusters:
        p["cluster_floors"] = [int(c.floors) for c in base_options.floor_clusters]
    elif floors is not None:
        p["floors"] = int(floors)
    return p


def _coord_descent(ev: _Evaluator, floors_list, active, seed: tuple,
                   obj_key: str) -> tuple:
    """Координатный спуск по этажности, долям парковки И этажности МУ/подземки.
    Возвращает (floors, shares, ml_levels, ug_levels, value)."""
    bf, bsh, bml, bug = seed
    bv = _objective(ev(bf, bsh, bml, bug), obj_key)
    for _ in range(_PASSES):
        improved = False
        # --- этажность жилья ---
        for f in floors_list:
            v = _objective(ev(f, bsh, bml, bug), obj_key)
            if v > bv + 1e-6:
                bv, bf = v, f
                improved = True
        # --- доли парковки (по каждому активному типу) ---
        for t in active:
            lo = _OPEN_MIN if t == "open" else 0.0
            x = lo
            while x <= 0.985 + 1e-9:
                sh = _set_share(bsh, t, x, active)
                v = _objective(ev(bf, sh, bml, bug), obj_key)
                if v > bv + 1e-6:
                    bv, bsh = v, sh
                    improved = True
                x += _STEP
        # --- этажность многоуровневого паркинга (пятно ∝ 1/этажность) ---
        if "ml" in active:
            for lv in range(1, 6):
                v = _objective(ev(bf, bsh, lv, bug), obj_key)
                if v > bv + 1e-6:
                    bv, bml = v, lv
                    improved = True
        # --- уровни подземки (прогрессия себестоимости) ---
        if "ug" in active:
            for lv in range(1, 4):
                v = _objective(ev(bf, bsh, bml, lv), obj_key)
                if v > bv + 1e-6:
                    bv, bug = v, lv
                    improved = True
        if not improved:
            break
    return bf, bsh, bml, bug, bv


def refine_extrema(
    site: Site,
    base_options: CalculationOptions,
    norms: Normatives,
    constraints,
    base_tep: TEPResult,
    seed_results: list[OptimizationResult],
    vpp_request=None,
) -> list[OptimizationResult]:
    """Доуточнить «Максимум площади» и «Максимум эконом-индекса» координатным
    спуском от стартовых точек (лучшие кандидаты Optuna + конфиг Базы).

    Возвращает список OptimizationResult с доуточнёнными экстремумами — их
    добавляют в пул `_select_three` наравне с trial'ами Optuna.
    """
    if base_options.floor_clusters:
        floors_list: list = []          # этажность зон не трогаем здесь
    else:
        lo, hi = (constraints.floors_range if constraints is not None
                  else (base_options.floors, base_options.floors))
        floors_list = list(range(int(lo), int(hi) + 1))

    active = _active_types(constraints)
    vpp_mode = getattr(vpp_request, "mode", None) if vpp_request is not None else None
    base_ml = int(getattr(base_options.parking, "multilevel_levels", 1) or 1)
    base_ug = int(getattr(base_options.parking, "underground_levels", 1) or 1)
    ev = _Evaluator(
        site=site, base_options=base_options, norms=norms, vpp_mode=vpp_mode,
        vpp_c44=getattr(vpp_request, "custom_4_4_m2", None) if vpp_request else None,
        vpp_c46=getattr(vpp_request, "custom_4_6_m2", None) if vpp_request else None,
    )

    # --- стартовые точки: конфиг Базы + лучшие кандидаты Optuna по обоим крит. ---
    def _shares_of(opts_parking) -> tuple:
        p = opts_parking
        return (round(float(p.open_share), 4), round(float(p.multilevel_share), 4),
                round(float(p.underground_share), 4),
                round(float(getattr(p, "stylobate_share", 0.0) or 0.0), 4))

    base_floors = None if base_options.floor_clusters else int(base_options.floors)
    # seed = (floors, shares, ml_levels, ug_levels)
    seeds: list[tuple] = [
        (base_floors, _shares_of(base_options.parking), base_ml, base_ug)
    ]
    for r in seed_results:
        pr = r.params
        f = None if base_options.floor_clusters else int(pr.get("floors", base_options.floors))
        sh = (round(float(pr.get("parking_open_share", 0.0)), 4),
              round(float(pr.get("parking_ml_share", 0.0)), 4),
              round(float(pr.get("parking_ug_share", 0.0)), 4),
              round(float(pr.get("parking_stylobate_share", 0.0)), 4))
        if abs(sum(sh) - 1.0) < 0.05:        # валидный набор долей
            seeds.append((
                f, sh,
                int(pr.get("multilevel_levels", base_ml) or base_ml),
                int(pr.get("underground_levels", base_ug) or base_ug),
            ))

    out: list[OptimizationResult] = []
    for obj_key in ("apartments", "index"):
        best = None
        for seed in seeds:
            f, sh, mll, ugl, val = _coord_descent(ev, floors_list, active, seed, obj_key)
            if val == float("-inf"):
                continue
            if best is None or val > best[-1] + 1e-6:
                best = (f, sh, mll, ugl, val)
        if best is None:
            continue
        f, sh, mll, ugl, _ = best
        tep = ev(f, sh, mll, ugl)
        if tep is None:
            continue
        out.append(OptimizationResult(
            rank=0,
            apartments_area=float(tep.apartments_area.value or 0.0),
            kit=float(tep.kit.value or 0.0),
            params=_params_of(f, sh, mll, ugl, base_options),
            tep=tep,
            feasible=bool(tep.balance.is_feasible),
        ))
    return out
