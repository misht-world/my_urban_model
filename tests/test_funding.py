"""Режимы финансирования по объектам (v0.19.0)."""

from __future__ import annotations

import pytest

from urban_model import solve_max_kit
from urban_model.models import CalculationOptions, CustomObject, Site
from urban_model.models.funding import (
    FUNDING_KEYS,
    FUNDING_LABELS,
    ObjectFunding,
    resolve_funding,
)
from urban_model.models.social import AdditionalEducationSpec, PolyclinicSpec
from urban_model.normatives import load_normatives


@pytest.fixture(scope="module")
def norms():
    return load_normatives("spb")


SITE = Site(area_m2=200_000)


def _econ(norms, **kw):
    return solve_max_kit(
        SITE, CalculationOptions(floors=12, planning_doc=True, **kw), norms).economy


class TestResolve:
    def test_labels_cover_keys(self):
        assert set(FUNDING_LABELS) == set(FUNDING_KEYS)

    def test_default_follows_global_for_ngp(self, norms):
        """Соцобъекты НГП без override → глобальный режим."""
        o = CalculationOptions(social_funding="developer")
        assert resolve_funding(o, "kindergarten", norms) == ("developer", 0.0)
        o2 = CalculationOptions(social_funding="city")
        assert resolve_funding(o2, "school", norms)[0] == "not_developer"
        o3 = CalculationOptions(social_funding="at_cost")
        assert resolve_funding(o3, "polyclinic", norms) == ("compensated", 1.0)

    def test_default_is_developer_for_others(self, norms):
        """Спорт/соц-парковки/инженерия НЕ следуют глобальному режиму."""
        o = CalculationOptions(social_funding="city")
        for key in ("sport", "social_parking", "engineering"):
            assert resolve_funding(o, key, norms) == ("developer", 0.0), key

    def test_object_override_wins(self, norms):
        o = CalculationOptions(
            social_funding="developer",
            object_funding={"kindergarten": ObjectFunding(
                mode="compensated", compensation_share=0.5)})
        assert resolve_funding(o, "kindergarten", norms) == ("compensated", 0.5)
        # прочие остались глобальными
        assert resolve_funding(o, "school", norms) == ("developer", 0.0)

    def test_compensated_share_fallbacks(self, norms):
        """Доля не задана на объекте → общая настройка, иначе норматив."""
        o = CalculationOptions(social_compensation_share=0.3,
                               object_funding={"school": ObjectFunding(mode="compensated")})
        assert resolve_funding(o, "school", norms) == ("compensated", 0.3)
        o2 = CalculationOptions(object_funding={"school": ObjectFunding(mode="compensated")})
        mode, share = resolve_funding(o2, "school", norms)
        assert mode == "compensated" and share == pytest.approx(
            norms.resolve("economy.social_compensation.share"))


class TestBackwardCompat:
    """Глобальные режимы работают как до v0.19."""

    def test_global_modes_ordered(self, norms):
        idx = {g: _econ(norms, social_funding=g).economy_index
               for g in ("developer", "compensated", "at_cost", "city")}
        assert idx["developer"] < idx["compensated"] < idx["at_cost"] < idx["city"]

    def test_city_zeroes_social_cost(self, norms):
        e = _econ(norms, social_funding="city")
        assert e.cost.kindergarten == 0 and e.cost.school == 0
        assert e.revenue.social_compensation == 0

    def test_developer_no_compensation(self, norms):
        e = _econ(norms, social_funding="developer")
        assert e.cost.kindergarten > 0
        assert e.revenue.social_compensation == 0


class TestOnlyDemandSemantics:
    """v0.19.0: «только потребность» = вне баланса территории, НЕ вне экономики."""

    def test_only_demand_keeps_cost(self, norms):
        e = _econ(norms, social_funding="developer",
                  add_education=AdditionalEducationSpec(only_demand=True))
        assert e.cost.add_education > 0

    def test_not_developer_zeroes_cost(self, norms):
        e = _econ(norms, social_funding="developer",
                  add_education=AdditionalEducationSpec(only_demand=True),
                  object_funding={"add_education": ObjectFunding(mode="not_developer")})
        assert e.cost.add_education == 0

    def test_only_demand_still_out_of_balance(self, norms):
        """Территориальная семантика не изменилась."""
        r = solve_max_kit(SITE, CalculationOptions(
            floors=12, planning_doc=True,
            polyclinic=PolyclinicSpec(only_demand=True)), norms)
        assert r.balance.components.get("polyclinic_plot", 0) == 0
        assert (r.polyclinic_visits_accepted.value or 0) > 0


class TestPerObjectModes:
    def test_mixed_modes(self, norms):
        """Запрос заказчика: ДОУ/СОШ — застройщик, поликлиника — компенсация,
        доп. образование — не за счёт застройщика."""
        e = _econ(norms, social_funding="compensated", object_funding={
            "kindergarten": ObjectFunding(mode="developer"),
            "school": ObjectFunding(mode="developer"),
            "polyclinic": ObjectFunding(mode="compensated", compensation_share=0.5),
            "add_education": ObjectFunding(mode="not_developer"),
        })
        assert e.cost.kindergarten > 0 and e.cost.school > 0
        assert e.cost.add_education == 0
        assert e.cost.polyclinic > 0
        # компенсация только за поликлинику, ровно 50% её себестоимости
        assert e.revenue.social_compensation == pytest.approx(
            e.cost.polyclinic * 0.5, rel=1e-6)

    @pytest.mark.parametrize("key,field", [
        ("sport", "sport"), ("social_parking", "social_parking"),
        ("engineering", "engineering"),
    ])
    def test_not_developer_zeroes_each(self, norms, key, field):
        base = getattr(_econ(norms).cost, field)
        assert base > 0, f"{key}: базовая себестоимость должна быть > 0"
        off = getattr(_econ(norms, object_funding={
            key: ObjectFunding(mode="not_developer")}).cost, field)
        assert off == 0


class TestCustomObjectFunding:
    def test_default_is_developer(self, norms):
        e = _econ(norms, custom_objects=[
            CustomObject(name="Магазин", plot_area_m2=5000, vri_code="4.4")])
        assert e.cost.custom_objects > 0 and e.revenue.custom_commercial > 0

    def test_not_developer_excludes_both(self, norms):
        e = _econ(norms, custom_objects=[
            CustomObject(name="ФОК города", plot_area_m2=5000, vri_code="4.4",
                         funding=ObjectFunding(mode="not_developer"))])
        assert e.cost.custom_objects == 0
        assert e.revenue.custom_commercial == 0

    def test_economy_off_keeps_territory(self, norms):
        """«Не за счёт застройщика» не убирает объект с территории."""
        r = solve_max_kit(SITE, CalculationOptions(
            floors=12, planning_doc=True,
            custom_objects=[CustomObject(
                name="ФОК", plot_area_m2=5000, vri_code="4.4",
                funding=ObjectFunding(mode="not_developer"))]), norms)
        assert r.balance.components.get("custom_objects", 0) == pytest.approx(5000)
        assert r.economy.cost.custom_objects == 0

    def test_per_object_independent(self, norms):
        e = _econ(norms, custom_objects=[
            CustomObject(name="Город", plot_area_m2=4000, vri_code="4.4",
                         funding=ObjectFunding(mode="not_developer")),
            CustomObject(name="Наш", plot_area_m2=4000, vri_code="4.4"),
        ])
        # в экономике только второй
        e_one = _econ(norms, custom_objects=[
            CustomObject(name="Наш", plot_area_m2=4000, vri_code="4.4")])
        assert e.cost.custom_objects == pytest.approx(e_one.cost.custom_objects)
        assert e.revenue.custom_commercial == pytest.approx(
            e_one.revenue.custom_commercial)
