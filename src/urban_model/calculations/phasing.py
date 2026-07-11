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
from urban_model.models.phasing import PhasingResult, PhasingSpec, StageProvision


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


def _auto_shares(result) -> list[float] | None:
    """Авто-доли очередей из дискретности соцобъектов (v0.15.2).

    Границы очередей — по ёмкости корпусов ДОО (сортировка по убыванию:
    крупные раньше): доля очереди k = вместимость корпуса k / Σ вместимостей.
    Тогда на конец каждой очереди введённые корпуса покрывают накопительную
    потребность с общим профицитом проекта (~95–105%) — каждая очередь
    самодостаточна. Нет ДОО (или 1 корпус) → по корпусам СОШ. Больше 4
    корпусов → мелкие сливаются в последнюю очередь.

    v0.15.4: если ни ДОО, ни СОШ не дают ≥2 корпусов — деление на очереди
    не имеет опоры (единственный соцобъект нужен с первой очереди, любые
    доли произвольны) → None: «не делить».
    """
    buckets = _buckets(result.kindergarten_places_accepted.formula)
    if len(buckets) < 2:
        buckets = _buckets(result.school_places_accepted.formula)
    if len(buckets) < 2:
        return None
    caps = sorted(buckets, reverse=True)
    if len(caps) > 4:
        caps = caps[:3] + [sum(caps[3:])]   # мелкие корпуса → последняя очередь
    total = sum(caps)
    return [c / total for c in caps]


_NO_SPLIT_NOTE = (
    "Деление на очереди не выполнено: у проекта единственный корпус "
    "соцобъектов (ДОО/СОШ) — он необходим с первой очереди, и границы "
    "очередей по обеспеченности провести не по чему. Задайте доли вручную, "
    "если очерёдность нужна по другим соображениям."
)


def compute_phasing(result, spec: PhasingSpec) -> PhasingResult:
    """Раскладка готового TEPResult по очередям. Не мутирует result."""
    if spec.mode == "auto":
        shares = _auto_shares(result)
        if shares is None:
            return PhasingResult(mode="auto", stages=[], note=_NO_SPLIT_NOTE)
    else:
        shares = spec.shares
    n = len(shares)
    cum_shares = [sum(shares[: k + 1]) for k in range(n)]

    site_area = float(result.balance.site_area or 0.0)
    apt_total = float(result.apartments_area.value or 0.0)
    pop_total = float(result.population.value or 0.0)
    park_total = int(result.parking_required_places.value or 0)

    # Нормативные ставки «мест на жителя» — из самого результата (устойчиво к
    # override'ам): required / population.
    kg_req_total = float(result.kindergarten_places_required.value or 0.0)
    sch_req_total = float(result.school_places_required.value or 0.0)
    kg_rate = kg_req_total / pop_total if pop_total > 0 else 0.0
    sch_rate = sch_req_total / pop_total if pop_total > 0 else 0.0

    kg_buckets = _buckets(result.kindergarten_places_accepted.formula)
    sch_buckets = _buckets(result.school_places_accepted.formula)

    kg_req_cum = [pop_total * cs * kg_rate for cs in cum_shares]
    sch_req_cum = [pop_total * cs * sch_rate for cs in cum_shares]
    kg_per_stage = _assign_buckets(kg_buckets, kg_req_cum)
    sch_per_stage = _assign_buckets(sch_buckets, sch_req_cum)

    # Инженерка: объекты по накопительному спросу.
    eng_per_stage: list[dict[str, int]] = [{} for _ in range(n)]
    if getattr(result, "engineering", None) is not None:
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
    # ЛОТ (v0.15.5): группа очередей, полностью обеспеченная соцобъектами.
    # Границы — по вводу корпусов СОШ: первый корпус обслуживает лот 1
    # (включая очереди до него), каждый СЛЕДУЮЩИЙ корпус открывает новый лот.
    lot_idx = 1
    schools_seen = False
    for k in range(n):
        kg_prov += sum(kg_per_stage[k])
        sch_prov += sum(sch_per_stage[k])
        if sch_per_stage[k] and schools_seen:
            lot_idx += 1
        if sch_per_stage[k]:
            schools_seen = True
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
    return PhasingResult(mode=spec.mode, stages=stages, warnings=warnings)
