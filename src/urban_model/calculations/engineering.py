"""Расчёт инженерной инфраструктуры квартала (v0.12) — чистые функции.

Источник показателей: dict/Показатели инженерных объектов.docx → секция
`engineering` в spb.yaml. Все объекты считаются от результатов расчёта
(площадь квартир, население, число соцобъектов) в один проход внутри
compute_tep_for_kit — отдельный проход не нужен.

Зависимости количеств:
    ТП       = ceil(S_квартир / норма_на_ТП) + (ДОО+СОШ+мед.объекты)
    РТП      = ceil(ТП / 8)
    Котельная: Q_МВт = S_квартир × q_тепл/1000; делится при >split_max_mw
    ГРП      = число котельных
    ОСПС:     Q_м³ = N_жит × q_вода/1000; делится при >split_max_m3day
    Насосная = число ОСПС
"""

from __future__ import annotations

import math

from urban_model.models.engineering import (
    ENGINEERING_LABELS,
    EngineeringObject,
    EngineeringResult,
    EngineeringSpec,
)


def _split_count(total: float, per_object_max: float) -> int:
    """Число объектов, чтобы нагрузка каждого не превышала порог дробления."""
    if total <= 0 or per_object_max <= 0:
        return 0
    return max(1, math.ceil(total / per_object_max))


def compute_engineering(
    apartments_area: float,
    population: float,
    n_social_objects: int,
    norms,
    spec: EngineeringSpec | None = None,
) -> EngineeringResult:
    """Полный расчёт инженерных объектов квартала.

    Args:
        apartments_area: площадь квартир, м².
        population: население квартала, чел.
        n_social_objects: число физически размещаемых на квартале объектов
            ДОО + СОШ + поликлиник (медицинских CustomObject) — каждый требует
            отдельную ТП. Объекты «только потребность» сюда НЕ входят.
        norms: загруженные нормативы.
        spec: настройки учёта (demand_only / cooking / override нагрузок).

    Returns:
        EngineeringResult: список объектов + площади (в балансе / всего).
    """
    spec = spec or EngineeringSpec()
    objs: list[EngineeringObject] = []

    def _add(key: str, count: int, plot_each: float,
             capacity: float | None = None, capacity_unit: str | None = None,
             note: str | None = None) -> None:
        in_balance = not spec.is_demand_only(key)
        plot_total = count * plot_each
        objs.append(EngineeringObject(
            key=key,
            label=ENGINEERING_LABELS[key],
            count=count,
            capacity=round(capacity, 2) if capacity is not None else None,
            capacity_unit=capacity_unit,
            plot_each=plot_each,
            plot_total=plot_total,
            in_balance=in_balance,
            note=note,
        ))

    # --- Электроснабжение: ТП ---
    per_tp = float(norms.resolve(
        "engineering.tp.norm_per_apartments_by_cooking", cooking=spec.cooking))
    n_tp_load = math.ceil(apartments_area / per_tp) if apartments_area > 0 else 0
    n_tp = n_tp_load + max(0, int(n_social_objects))
    tp_plot = float(norms.resolve("engineering.tp.plot_area"))
    _add("tp", n_tp, tp_plot,
         note=f"{n_tp_load} по нагрузке жилья + {n_social_objects} на соцобъекты")

    # --- РТП ---
    per_rtp = float(norms.resolve("engineering.rtp.per_tp"))
    n_rtp = math.ceil(n_tp / per_rtp) if n_tp > 0 else 0
    rtp_plot = float(norms.resolve("engineering.rtp.plot_area"))
    _add("rtp", n_rtp, rtp_plot)

    # --- Теплоснабжение: котельная ---
    q_heat = spec.q_heat_override
    if q_heat is None:
        q_heat = float(norms.resolve("engineering.boiler.q_heat"))
    q_mw = apartments_area * q_heat / 1000.0  # кВт→МВт
    split_mw = float(norms.resolve("engineering.boiler.split_max_mw"))
    n_boiler = _split_count(q_mw, split_mw)
    mw_each = q_mw / n_boiler if n_boiler else 0.0
    boiler_plot = (
        float(norms.resolve("engineering.boiler.plot_area_by_mw", mw=mw_each))
        if n_boiler else 0.0
    )
    _add("boiler", n_boiler, boiler_plot, capacity=mw_each, capacity_unit="МВт",
         note=f"суммарно {q_mw:.1f} МВт")

    # --- Газоснабжение: ГРП (по числу котельных) ---
    per_grp = float(norms.resolve("engineering.grp.per_boiler"))
    n_grp = int(n_boiler * per_grp)
    grp_plot = float(norms.resolve("engineering.grp.plot_area"))
    _add("grp", n_grp, grp_plot)

    # --- Водоотведение: ОСПС ---
    q_water = spec.q_water_override
    if q_water is None:
        q_water = float(norms.resolve("engineering.osps.q_water"))
    q_m3 = population * q_water / 1000.0  # л→м³
    split_m3 = float(norms.resolve("engineering.osps.split_max_m3day"))
    n_osps = _split_count(q_m3, split_m3)
    m3_each = q_m3 / n_osps if n_osps else 0.0
    osps_plot = (
        float(norms.resolve("engineering.osps.plot_area_by_m3day", m3day=m3_each))
        if n_osps else 0.0
    )
    _add("osps", n_osps, osps_plot, capacity=m3_each, capacity_unit="м³/сут",
         note=f"суммарно {q_m3:.0f} м³/сут")

    # --- Насосная станция (по числу ОСПС) ---
    per_pump = float(norms.resolve("engineering.pump.per_osps"))
    n_pump = int(n_osps * per_pump)
    pump_plot = float(norms.resolve("engineering.pump.plot_area"))
    _add("pump", n_pump, pump_plot)

    plot_in_balance = sum(o.plot_total for o in objs if o.in_balance)
    plot_total_all = sum(o.plot_total for o in objs)

    return EngineeringResult(
        objects=objs,
        plot_in_balance=plot_in_balance,
        plot_total_all=plot_total_all,
        cooking=spec.cooking,
    )
