"""Тесты инженерной инфраструктуры (v0.12)."""

from __future__ import annotations

import math

import pytest

from urban_model.calculations import engineering
from urban_model.models.engineering import EngineeringSpec
from urban_model.normatives import load_normatives


@pytest.fixture(scope="module")
def spb():
    return load_normatives("spb")


def _by_key(res):
    return {o.key: o for o in res.objects}


def test_tp_count_load_plus_social(spb):
    # 100 000 м² квартир → ceil(100000/28000)=4 ТП по нагрузке, +3 соцобъекта
    res = engineering.compute_engineering(
        apartments_area=100_000, population=3_500, n_social_objects=3, norms=spb
    )
    o = _by_key(res)
    assert o["tp"].count == math.ceil(100_000 / 28_000) + 3  # 4 + 3 = 7
    assert o["tp"].plot_each == 145
    assert o["tp"].plot_total == o["tp"].count * 145


def test_rtp_grp_pump_dependencies(spb):
    res = engineering.compute_engineering(
        apartments_area=100_000, population=3_500, n_social_objects=3, norms=spb
    )
    o = _by_key(res)
    # РТП = ceil(ТП / 8)
    assert o["rtp"].count == math.ceil(o["tp"].count / 8)
    # ГРП = число котельных
    assert o["grp"].count == o["boiler"].count
    # Насосная = число ОСПС
    assert o["pump"].count == o["osps"].count


def test_boiler_power_and_plot(spb):
    # 200 000 м² × 0.10 кВт/м² = 20 МВт → 1 котельная, ЗУ piecewise(20)=3500
    res = engineering.compute_engineering(
        apartments_area=200_000, population=7_000, n_social_objects=0, norms=spb
    )
    o = _by_key(res)
    assert o["boiler"].count == 1
    assert o["boiler"].capacity == pytest.approx(20.0, abs=0.01)
    assert o["boiler"].plot_each == 3500  # ступень 10–20 МВт


def test_boiler_splits_above_threshold(spb):
    # 1 200 000 м² × 0.10 = 120 МВт > 50 → ceil(120/50)=3 котельных по 40 МВт
    res = engineering.compute_engineering(
        apartments_area=1_200_000, population=40_000, n_social_objects=0, norms=spb
    )
    o = _by_key(res)
    assert o["boiler"].count == 3
    assert o["boiler"].capacity == pytest.approx(40.0, abs=0.01)
    # ЗУ каждой 40 МВт → ступень 35–50 = 6500
    assert o["boiler"].plot_each == 6500
    assert o["grp"].count == 3  # ГРП следует за котельными


def test_osps_power_and_split(spb):
    # 11 278 чел × 230 / 1000 = 2594 м³/сут → 1 ОСПС, ЗУ piecewise(2594)=6000
    res = engineering.compute_engineering(
        apartments_area=0, population=11_278, n_social_objects=0, norms=spb
    )
    o = _by_key(res)
    assert o["osps"].count == 1
    assert o["osps"].capacity == pytest.approx(2594.0, abs=1.0)
    assert o["osps"].plot_each == 6000  # ступень 2000–3000 м³/сут


def test_demand_only_excludes_from_balance_keeps_count(spb):
    spec = EngineeringSpec(demand_only=["osps", "pump"])
    res = engineering.compute_engineering(
        apartments_area=100_000, population=3_500, n_social_objects=2,
        norms=spb, spec=spec,
    )
    o = _by_key(res)
    # Объекты по-прежнему посчитаны
    assert o["osps"].count >= 1 and o["pump"].count >= 1
    # Но не входят в баланс
    assert o["osps"].in_balance is False
    assert o["pump"].in_balance is False
    # plot_in_balance не включает их площадь
    excluded = o["osps"].plot_total + o["pump"].plot_total
    assert res.plot_in_balance == pytest.approx(res.plot_total_all - excluded)


def test_cooking_gas_is_demo_equal_electric(spb):
    """Демо-хук: газовый режим пока считается как электрический (YAML равны)."""
    base = engineering.compute_engineering(100_000, 3_500, 0, spb,
                                           EngineeringSpec(cooking="electric"))
    gas = engineering.compute_engineering(100_000, 3_500, 0, spb,
                                          EngineeringSpec(cooking="gas"))
    assert _by_key(base)["tp"].count == _by_key(gas)["tp"].count


def test_driveways_intra_reduced_to_7_5_percent(spb):
    assert spb.resolve("driveways.intra_quarter_share") == pytest.approx(0.075)


# --- Интеграция с ядром ---

def test_engineering_in_balance_and_reduces_apartments(spb):
    from urban_model import solve_max_kit
    from urban_model.models import CalculationOptions, Site
    site = Site(area_m2=50_000)
    on = solve_max_kit(site, CalculationOptions(floors=12), spb)
    off = solve_max_kit(site, CalculationOptions(floors=12, include_engineering=False), spb)
    # Инженерка входит в баланс при include_engineering=True
    assert on.balance.components.get("engineering_plot", 0) > 0
    assert off.balance.components.get("engineering_plot", 0) == 0
    # При выключении объекты всё равно считаются
    assert off.engineering is not None and len(off.engineering.objects) == 6
    # Инженерка изымает территорию → меньше площади квартир
    assert on.apartments_area.value < off.apartments_area.value


def test_engineering_cost_in_economy(spb):
    from urban_model import solve_max_kit
    from urban_model.models import CalculationOptions, Site
    r = solve_max_kit(Site(area_m2=50_000), CalculationOptions(floors=12), spb)
    assert r.economy is not None
    assert r.economy.cost.engineering > 0
    # входит в shell_total
    assert r.economy.cost.engineering <= r.economy.cost.shell_total


def test_medical_custom_object_adds_tp(spb):
    """CustomObject с ВРИ 3.4* (поликлиника) добавляет ТП."""
    from urban_model import solve_max_kit
    from urban_model.models import CalculationOptions, CustomObject, Site
    base = solve_max_kit(
        Site(area_m2=50_000),
        CalculationOptions(floors=12, include_kindergarten=False, include_school=False),
        spb,
    )
    withclinic = solve_max_kit(
        Site(area_m2=50_000),
        CalculationOptions(
            floors=12, include_kindergarten=False, include_school=False,
            custom_objects=[CustomObject(
                name="Поликлиника", vri_code="3.4.1",
                plot_area_m2=3000, floor_area_m2=5000,
            )],
        ),
        spb,
    )
    tp_base = next(o for o in base.engineering.objects if o.key == "tp").count
    tp_clinic = next(o for o in withclinic.engineering.objects if o.key == "tp").count
    assert tp_clinic == tp_base + 1
