"""Тесты v0.12.15 — организации доп. образования (ВРИ 3.5.1).

Норматив РМД 15-26-2017 + НГП СПб:
- потребность 65 мест/1000 чел; работники = места/6;
- здание 17 м²/место (и ВПП, и отд. стоящее);
- < 150 мест → встроенное (ВПП); ≥ 150 → отд. стоящее (ЗУ 15 м²/место + 30%);
- парковка — формула соцобъектов max(2, ⌈раб/5⌉+⌈места/100⌉).
"""

from __future__ import annotations

import math

import pytest

from urban_model import verify_kit, solve_max_kit
from urban_model.calculations import add_education
from urban_model.models import CalculationOptions, Site
from urban_model.models.social import AdditionalEducationSpec
from urban_model.normatives import load_normatives


@pytest.fixture(scope="module")
def spb():
    return load_normatives("spb")


@pytest.fixture
def site():
    return Site(area_m2=100_000)


# ---------------------------------------------------------------------------
# Чистый расчёт add_education.compute
# ---------------------------------------------------------------------------

class TestCompute:
    def test_places_from_norm(self, spb):
        """65 мест/1000 чел, вверх кратно 5."""
        r = add_education.compute(2000.0, spb)
        # 2000 × 65/1000 = 130 → кратно 5 = 130
        assert r.places == 130
        assert r.built_in is True   # < 150 → ВПП

    def test_standalone_above_threshold(self, spb):
        """≥ 150 мест → отдельно стоящее с ЗУ 15 м²/место + 30% озеленения."""
        r = add_education.compute(4000.0, spb)  # 260 мест
        assert r.places == 260
        assert r.built_in is False
        assert r.plot_area == pytest.approx(260 * 15)
        assert r.building_area == pytest.approx(260 * 17)
        assert r.greening_required == pytest.approx(0.30 * 260 * 15)

    def test_built_in_has_no_plot(self, spb):
        r = add_education.compute(1500.0, spb)  # 97.5 → 100 мест
        assert r.built_in is True
        assert r.plot_area == 0.0
        assert r.building_area == pytest.approx(r.places * 17)

    def test_workers_and_parking(self, spb):
        """Работники = места/6, парковка по формуле соцобъектов."""
        r = add_education.compute(4000.0, spb)  # 260 мест
        assert r.workers == round(260 / 6)
        expected = max(2, math.ceil(r.workers / 5) + math.ceil(260 / 100))
        assert r.parking_places == expected

    def test_manual_force_vpp_splits(self, spb):
        """Ручной режим + force_vpp: ≥150 мест → дробление на N встроенных."""
        r = add_education.compute(
            999999.0, spb, mode="manual", places_override=300, force_vpp=True
        )
        assert r.places == 300
        assert r.built_in is True          # принудительно в ВПП, несмотря на ≥150
        assert r.plot_area == 0.0
        assert r.n_objects == 2            # ceil(300/150) = 2 встроенных объекта

    def test_manual_standalone_above_threshold(self, spb):
        """Ручной режим ≥150 без force_vpp → отдельно стоящее."""
        r = add_education.compute(
            0.0, spb, mode="manual", places_override=200, force_vpp=False
        )
        assert r.places == 200
        assert r.built_in is False
        assert r.n_objects == 1
        assert r.plot_area == pytest.approx(200 * 15)

    def test_manual_below_threshold_is_built_in(self, spb):
        """<150 мест → всегда встроенное (ВПП), даже вручную без force_vpp."""
        r = add_education.compute(
            0.0, spb, mode="manual", places_override=80, force_vpp=False
        )
        assert r.places == 80
        assert r.built_in is True
        assert r.plot_area == 0.0

    def test_zero_population(self, spb):
        r = add_education.compute(0.0, spb)
        assert r.places == 0
        assert r.parking_places == 0
        assert r.n_objects == 0


# ---------------------------------------------------------------------------
# Интеграция в forward / TEPResult
# ---------------------------------------------------------------------------

class TestIntegration:
    def test_standalone_plot_in_balance(self, spb):
        """Отд. стоящее доп. обр. → компонент add_education_plot в балансе."""
        site = Site(area_m2=200_000)
        r = solve_max_kit(site, CalculationOptions(floors=18, planning_doc=True), spb)
        assert r.add_education_places_accepted.value >= 150
        assert r.add_education_built_in is False
        assert "add_education_plot" in r.balance.components
        assert r.balance.components["add_education_plot"] == pytest.approx(
            r.add_education_plot_area.value
        )
        # ЗУ = 15 м²/место
        assert r.add_education_plot_area.value == pytest.approx(
            r.add_education_places_accepted.value * 15
        )

    def test_built_in_no_balance_component(self, spb):
        """Встроенное доп. обр. → нет компонента ЗУ в балансе."""
        site = Site(area_m2=60_000)
        r = solve_max_kit(site, CalculationOptions(floors=10, planning_doc=True), spb)
        if r.add_education_built_in:
            assert "add_education_plot" not in r.balance.components
            assert r.add_education_plot_area.value == 0.0

    def test_disabled_no_places(self, spb, site):
        """include_add_education=False → объект не считается."""
        r = verify_kit(
            1.5, site, CalculationOptions(floors=12, include_add_education=False), spb
        )
        assert (r.add_education_places_accepted.value or 0) == 0
        assert "add_education_plot" not in r.balance.components

    def test_only_demand_excludes_from_balance(self, spb):
        """only_demand: места считаются, ЗУ/здание вне баланса."""
        site = Site(area_m2=200_000)
        r = solve_max_kit(
            site,
            CalculationOptions(
                floors=18, planning_doc=True,
                add_education=AdditionalEducationSpec(only_demand=True),
            ),
            spb,
        )
        assert r.add_education_places_accepted.value > 0
        assert "add_education_plot" not in r.balance.components

    def test_economy_cost_present(self, spb):
        """Себестоимость здания доп. обр. попадает в cost.add_education."""
        site = Site(area_m2=200_000)
        r = solve_max_kit(site, CalculationOptions(floors=18, planning_doc=True), spb)
        assert r.economy is not None
        assert r.economy.cost.add_education > 0

    def test_economy_social_symmetry(self, spb):
        """Доп. обр. симметричен ДОО/СОШ: входит и в соцнагрузку, и в
        компенсацию города (v0.12.19)."""
        site = Site(area_m2=200_000)
        e = solve_max_kit(
            site, CalculationOptions(floors=18, planning_doc=True), spb
        ).economy
        cb, rb = e.cost, e.revenue
        # net_social_burden включает cost.add_education
        expected = (
            cb.kindergarten + cb.school + cb.add_education + cb.social_parking
        ) - rb.social_compensation
        assert e.net_social_burden == pytest.approx(expected)
        # компенсация включает долю от здания доп. обр.
        comp_share = spb.resolve("economy.social_compensation.share")
        c_ae = spb.resolve("economy.construction.add_education")
        ae_comp = cb.add_education * comp_share  # cost.add_education = bld × c_ae
        assert rb.social_compensation >= ae_comp - 1e-6
