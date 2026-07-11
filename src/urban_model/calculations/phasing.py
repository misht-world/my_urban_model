"""Раскладка по очередям застройки (v0.15.0) — чистая функция поверх TEPResult.

Ядро расчёта не знает про очереди: compute_phasing вызывается в конце forward
(как экономика) и распределяет ГОТОВЫЕ агрегаты:
  • квартиры / население / парковки — пропорционально долям площади (модель
    безгеометрийная, типология однородна по кварталу);
  • корпуса ДОО/СОШ — дискретно: очередной корпус вводится в той очереди, на
    конец которой накопительная потребность впервые превышает уже введённую
    вместимость (крупные корпуса — раньше);
  • инженерка — по накопительному спросу (объекты со count=1 — в 1-ю очередь).

На конец каждого этапа проверяется обеспеченность; дефицит → предупреждение
[PHASE_SOC_DEFICIT] (информационное: показывает, что соцобъект нужен раньше).
"""

from __future__ import annotations

import math
import re

from urban_model.calculations.warning_codes import WC, prefix
from urban_model.models.phasing import (
    LotProvision,
    PhasingResult,
    PhasingSpec,
    StageProvision,
)


def _buckets(formula: str | None) -> list[int]:
    """Вместимости корпусов из formula-строки («… [280, 280, 280]»)."""
    if not formula:
        return []
    m = re.search(r"\[([\d,\s]+)\]", formula)
    if not m:
        return []
    return [int(x) for x in m.group(1).split(",") if x.strip()]


def _assign_buckets(
    buckets: list[int], required_cum: list[float],
) -> list[list[int]]:
    """Разложить корпуса по очередям: корпус вводится в очереди, на конец
    которой накопительная потребность впервые превышает уже введённую
    вместимость. Крупные корпуса — раньше (меньше строек на старте).

    Возвращает список корпусов ПО очередям (len == len(required_cum)).
    Гарантия: все корпуса размещены (остаток — в последнюю очередь).
    """
    n_stages = len(required_cum)
    per_stage: list[list[int]] = [[] for _ in range(n_stages)]
    provided = 0.0
    for cap in sorted(buckets, reverse=True):
        placed = False
        for k in range(n_stages):
            if required_cum[k] > provided + 1e-9:
                per_stage[k].append(cap)
                provided += cap
                placed = True
                break
        if not placed:
            # потребность уже покрыта — корпус в последнюю очередь
            per_stage[-1].append(cap)
            provided += cap
    return per_stage


def _distribute_count(total: int, cum_shares: list[float]) -> list[int]:
    """Распределить `total` дискретных объектов по очередям пропорционально
    накопительной доле (ceil, накопительно-монотонно). count=1 → очередь 1."""
    if total <= 0:
        return [0] * len(cum_shares)
    out: list[int] = []
    assigned = 0
    for cs in cum_shares:
        want = min(total, math.ceil(total * cs - 1e-9))
        out.append(max(0, want - assigned))
        assigned = max(assigned, want)
    # остаток (числовые огрехи) — в последнюю
    if assigned < total:
        out[-1] += total - assigned
    return out


def _auto_shares_from_buckets(
    kg_buckets: list[int], sch_buckets: list[int],
) -> list[float] | None:
    """Авто-доли очередей из дискретности соцобъектов (v0.15.2).

    Границы очередей — по ёмкости корпусов ДОО (сортировка по убыванию:
    крупные раньше): доля очереди k = вместимость корпуса k / Σ вместимостей.
    Тогда на конец каждой очереди введённые корпуса покрывают накопительную
    потребность с общим профицитом проекта (~95–105%) — каждая очередь
    самодостаточна. Нет ДОО (или 1 корпус) → по корпусам СОШ. Больше 8
    корпусов → мелкие сливаются в последнюю очередь.

    v0.15.4: если ни ДОО, ни СОШ не дают ≥2 корпусов — деление на очереди
    не имеет опоры (единственный соцобъект нужен с первой очереди, любые
    доли произвольны) → None: «не делить».
    """
    buckets = kg_buckets if len(kg_buckets) >= 2 else sch_buckets
    if len(buckets) < 2:
        return None
    caps = sorted(buckets, reverse=True)
    if len(caps) > 8:
        caps = caps[:7] + [sum(caps[7:])]   # мелкие корпуса → последняя очередь
    total = sum(caps)
    return [c / total for c in caps]


def _auto_shares(result) -> list[float] | None:
    return _auto_shares_from_buckets(
        _buckets(result.kindergarten_places_accepted.formula),
        _buckets(result.school_places_accepted.formula),
    )


def _stage_partition(
    population: float,
    kg_buckets: list[int],
    sch_buckets: list[int],
    kg_required: float,
    sch_required: float,
    spec: PhasingSpec,
):
    """Общий партишн очередей/лотов (v0.15.7) — ЕДИНАЯ логика для
    compute_phasing (слой поверх результата) и lot_engineering_totals
    (внутри forward, по-лотовая инженерка в балансе).

    Возвращает (shares, cum_shares, kg_per_stage, sch_per_stage, lot_of_stage)
    либо None, если авто-режим решил не делить.
    """
    if spec.mode == "auto":
        shares = _auto_shares_from_buckets(kg_buckets, sch_buckets)
        if shares is None:
            return None
    else:
        shares = spec.shares
    n = len(shares)
    cum_shares = [sum(shares[: k + 1]) for k in range(n)]
    kg_rate = kg_required / population if population > 0 else 0.0
    sch_rate = sch_required / population if population > 0 else 0.0
    kg_req_cum = [population * cs * kg_rate for cs in cum_shares]
    sch_req_cum = [population * cs * sch_rate for cs in cum_shares]
    kg_per_stage = _assign_buckets(kg_buckets, kg_req_cum)
    sch_per_stage = _assign_buckets(sch_buckets, sch_req_cum)
    # ЛОТы: первый корпус СОШ обслуживает лот 1, каждый следующий открывает новый.
    lot_of_stage: list[int] = []
    lot_idx = 1
    schools_seen = False
    for k in range(n):
        if sch_per_stage[k] and schools_seen:
            lot_idx += 1
        if sch_per_stage[k]:
            schools_seen = True
        lot_of_stage.append(lot_idx)
    return shares, cum_shares, kg_per_stage, sch_per_stage, lot_of_stage


def lot_engineering_totals(
    apartments_area: float,
    population: float,
    kg_buckets: list[int],
    sch_buckets: list[int],
    kg_required: float,
    sch_required: float,
    spec: PhasingSpec,
    norms,
    eng_spec,
    count_kg: bool = True,
    count_sch: bool = True,
    n_extra_social: int = 0,
):
    """По-лотовая инженерка ДЛЯ БАЛАНСА/ЭКОНОМИКИ (v0.15.7).

    Вызывается из forward на каждой итерации бисекции вместо квартальной
    `compute_engineering`, когда включён `spec.engineering_by_lots`: каждый
    лот получает автономный комплект по своему спросу, объекты помечаются
    «— лот N» и агрегируются в общий EngineeringResult (Σ площадей → баланс;
    объекты с мощностями → экономика).

    count_kg/count_sch — учитывать ли корпуса в ТП (False при only_demand);
    n_extra_social — ТП отдельно стоящих доп.обр/поликлиники/мед-объектов
    (не лотуются, относятся к лоту 1).

    Возвращает EngineeringResult либо None (очереди не строятся — квартальная
    схема остаётся).
    """
    from urban_model.calculations.engineering import compute_engineering
    from urban_model.models.engineering import EngineeringResult

    part = _stage_partition(population, kg_buckets, sch_buckets,
                            kg_required, sch_required, spec)
    if part is None:
        return None
    shares, _cum, kg_per_stage, sch_per_stage, lot_of_stage = part

    # Спрос по лотам
    lots: dict[int, dict] = {}
    for k, lot in enumerate(lot_of_stage):
        d = lots.setdefault(lot, {"apt": 0.0, "pop": 0.0, "soc": 0})
        d["apt"] += apartments_area * shares[k]
        d["pop"] += population * shares[k]
        d["soc"] += (len(kg_per_stage[k]) if count_kg else 0) \
            + (len(sch_per_stage[k]) if count_sch else 0)
    first_lot = min(lots)
    lots[first_lot]["soc"] += n_extra_social

    objects = []
    plot_in_balance = 0.0
    plot_total_all = 0.0
    cooking = "electric"
    for lot_idx in sorted(lots):
        d = lots[lot_idx]
        eng = compute_engineering(d["apt"], d["pop"], d["soc"], norms, eng_spec)
        cooking = eng.cooking
        for o in eng.objects:
            if o.count <= 0:
                continue
            objects.append(o.model_copy(update={
                "label": f"{o.label} — лот {lot_idx}",
            }))
        plot_in_balance += eng.plot_in_balance
        plot_total_all += eng.plot_total_all
    return EngineeringResult(
        objects=objects,
        plot_in_balance=plot_in_balance,
        plot_total_all=plot_total_all,
        cooking=cooking,
    )


_NO_SPLIT_NOTE = (
    "Деление на очереди не выполнено: у проекта единственный корпус "
    "соцобъектов (ДОО/СОШ) — он необходим с первой очереди, и границы "
    "очередей по обеспеченности провести не по чему. Задайте доли вручную, "
    "если очерёдность нужна по другим соображениям."
)


def _build_lots(
    stages: list[StageProvision], norms, eng_spec,
    with_engineering: bool,
    count_kg: bool = True, count_sch: bool = True, n_extra_social: int = 0,
) -> tuple[list[LotProvision], str | None]:
    """Сводки лотов (v0.15.9 — строятся ВСЕГДА при очередях).

    Агрегаты (доля/площадь/население/квартиры/корпуса/парковки) — из очередей
    лота. Инженерия — только при `with_engineering` (автономные комплекты, тем
    же способом, что `lot_engineering_totals` в forward → таблица лотов
    совпадает с тем, что заложено в баланс и экономику). Дельта-примечание —
    что дала бы единая квартальная схема.
    """
    lots: list[LotProvision] = []
    by_lot: dict[int, list[StageProvision]] = {}
    for s in stages:
        by_lot.setdefault(s.lot, []).append(s)
    first_lot = min(by_lot) if by_lot else 1
    for lot_idx in sorted(by_lot):
        ls = by_lot[lot_idx]
        n_soc = sum(
            (len(s.kg_buckets) if count_kg else 0)
            + (len(s.school_buckets) if count_sch else 0)
            for s in ls
        )
        if lot_idx == first_lot:
            n_soc += n_extra_social
        lots.append(LotProvision(
            index=lot_idx, stages=[s.index for s in ls],
            share=sum(s.share for s in ls),
            area_m2=sum(s.area_m2 for s in ls),
            population=sum(s.population_stage for s in ls),
            apartments_m2=sum(s.apartments_m2 for s in ls),
            kg_buckets=[c for s in ls for c in s.kg_buckets],
            school_buckets=[c for s in ls for c in s.school_buckets],
            parking_places=sum(s.parking_places_stage for s in ls),
            n_social=n_soc,
        ))

    delta_note = None
    if with_engineering and norms is not None:
        from urban_model.calculations.engineering import compute_engineering
        for i, lp in enumerate(lots):
            eng = compute_engineering(lp.apartments_m2, lp.population,
                                      lp.n_social, norms, eng_spec)
            lots[i] = lp.model_copy(update={
                "engineering": {o.label: o.count
                                for o in eng.objects if o.count > 0},
                "eng_plot_total": eng.plot_total_all,
            })
        # Что дала бы ЕДИНАЯ квартальная схема (эффект масштаба).
        try:
            q_eng = compute_engineering(
                sum(lp.apartments_m2 for lp in lots),
                sum(lp.population for lp in lots),
                sum(lp.n_social for lp in lots), norms, eng_spec)
            q_objs = sum(o.count for o in q_eng.objects if o.count > 0)
            l_objs = sum(sum(lp.engineering.values()) for lp in lots)
            l_plot = sum(lp.eng_plot_total for lp in lots)

            def _n(v: float, sign: bool = False) -> str:
                s = f"{v:+,.0f}" if sign else f"{v:,.0f}"
                return s.replace(",", " ")

            delta_note = (
                f"Баланс и экономика рассчитаны по автономной по-лотовой "
                f"схеме: {l_objs} объектов инженерии, ЗУ {_n(l_plot)} м². "
                f"Единая квартальная схема дала бы {q_objs} объектов и "
                f"{_n(q_eng.plot_total_all)} м² "
                f"({_n(l_plot - q_eng.plot_total_all, sign=True)} м² — цена "
                f"автономности лотов)."
            )
        except Exception:  # noqa: BLE001 — примечание не критично
            pass
    return lots, delta_note


def compute_phasing(result, spec: PhasingSpec, norms=None, eng_spec=None,
                    count_kg: bool = True, count_sch: bool = True,
                    n_extra_social: int = 0) -> PhasingResult:
    """Раскладка готового TEPResult по очередям. Не мутирует result.

    norms/eng_spec/count_*/n_extra_social нужны только для
    `spec.engineering_by_lots` (автономные комплекты инженерии по лотам —
    те же параметры, что использовал forward при встраивании в баланс).
    """
    site_area = float(result.balance.site_area or 0.0)
    apt_total = float(result.apartments_area.value or 0.0)
    pop_total = float(result.population.value or 0.0)
    park_total = int(result.parking_required_places.value or 0)
    kg_req_total = float(result.kindergarten_places_required.value or 0.0)
    sch_req_total = float(result.school_places_required.value or 0.0)
    kg_buckets = _buckets(result.kindergarten_places_accepted.formula)
    sch_buckets = _buckets(result.school_places_accepted.formula)

    part = _stage_partition(pop_total, kg_buckets, sch_buckets,
                            kg_req_total, sch_req_total, spec)
    if part is None:
        return PhasingResult(mode="auto", stages=[], note=_NO_SPLIT_NOTE)
    shares, cum_shares, kg_per_stage, sch_per_stage, lot_of_stage = part
    n = len(shares)

    kg_rate = kg_req_total / pop_total if pop_total > 0 else 0.0
    sch_rate = sch_req_total / pop_total if pop_total > 0 else 0.0
    kg_req_cum = [pop_total * cs * kg_rate for cs in cum_shares]
    sch_req_cum = [pop_total * cs * sch_rate for cs in cum_shares]

    # Инженерка по очередям. Обычный режим: объекты квартальной схемы по
    # накопительному спросу. По-лотовый режим (v0.15.9, фикс по замечанию
    # Михаила): объекты КАЖДОГО лота распределяются по очередям ЭТОГО лота
    # по накопительному спросу внутри лота (18 ТП лота — не все в 1-ю
    # очередь, а по мере ввода жилья); единичные (котельная) — в первую
    # очередь лота. Метки — без «— лот N» (лоты видны на своём листе).
    eng_per_stage: list[dict[str, int]] = [{} for _ in range(n)]
    if getattr(result, "engineering", None) is not None:
        if spec.engineering_by_lots:
            _stages_of_lot: dict[int, list[int]] = {}
            for k, lot in enumerate(lot_of_stage):
                _stages_of_lot.setdefault(lot, []).append(k)
            for obj in result.engineering.objects:
                if obj.count <= 0:
                    continue
                _m = re.search(r"— лот (\d+)$", obj.label)
                _lot = int(_m.group(1)) if _m else min(_stages_of_lot)
                ks = _stages_of_lot.get(_lot) or [0]
                _tot = sum(shares[k] for k in ks)
                _local_cum = []
                _acc = 0.0
                for k in ks:
                    _acc += shares[k] / _tot if _tot > 0 else 1.0 / len(ks)
                    _local_cum.append(_acc)
                _short = obj.label.split(" — лот")[0]
                dist = _distribute_count(int(obj.count), _local_cum)
                for j, c in enumerate(dist):
                    if c > 0:
                        k = ks[j]
                        eng_per_stage[k][_short] = (
                            eng_per_stage[k].get(_short, 0) + c)
        else:
            for obj in result.engineering.objects:
                if obj.count <= 0:
                    continue
                dist = _distribute_count(int(obj.count), cum_shares)
                for k, c in enumerate(dist):
                    if c > 0:
                        eng_per_stage[k][obj.label] = c

    stages: list[StageProvision] = []
    warnings: list[str] = []
    kg_prov = 0
    sch_prov = 0
    for k in range(n):
        kg_prov += sum(kg_per_stage[k])
        sch_prov += sum(sch_per_stage[k])
        lot_idx = lot_of_stage[k]
        deficits: list[str] = []
        # −0.5 места — допуск на округление требуемых мест
        if kg_prov < kg_req_cum[k] - 0.5:
            deficits.append(
                f"ДОО: требуется {kg_req_cum[k]:.0f} мест, введено {kg_prov}"
            )
        if sch_prov < sch_req_cum[k] - 0.5:
            deficits.append(
                f"СОШ: требуется {sch_req_cum[k]:.0f} мест, введено {sch_prov}"
            )
        for d in deficits:
            warnings.append(prefix(
                WC.PHASE_SOC_DEFICIT,
                f"Очередь {k + 1}: {d} — объект нужен в более ранней очереди "
                f"или очереди стоит укрупнить.",
            ))
        stages.append(StageProvision(
            index=k + 1,
            lot=lot_idx,
            share=shares[k],
            area_m2=site_area * shares[k],
            apartments_m2=apt_total * shares[k],
            population_stage=pop_total * shares[k],
            population_cum=pop_total * cum_shares[k],
            kg_required_cum=kg_req_cum[k],
            kg_provided_cum=kg_prov,
            kg_buckets=kg_per_stage[k],
            school_required_cum=sch_req_cum[k],
            school_provided_cum=sch_prov,
            school_buckets=sch_per_stage[k],
            parking_places_stage=round(park_total * shares[k]),
            engineering_stage=eng_per_stage[k],
            deficits=deficits,
        ))
    lots: list[LotProvision] = []
    eng_delta_note = None
    if stages:
        try:
            lots, eng_delta_note = _build_lots(
                stages, norms, eng_spec,
                with_engineering=spec.engineering_by_lots,
                count_kg=count_kg, count_sch=count_sch,
                n_extra_social=n_extra_social)
        except Exception:  # noqa: BLE001 — сводка лотов не роняет расчёт
            lots, eng_delta_note = [], None

    return PhasingResult(mode=spec.mode, stages=stages, warnings=warnings,
                         lots=lots, eng_delta_note=eng_delta_note)
