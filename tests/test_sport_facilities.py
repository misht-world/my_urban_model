"""Тесты плоскостных спортивных сооружений (v0.6.8).

Нормативы:
- 1000 м²/1000 чел (НГП СПб)
- + 40% озеленения по ПЗЗ (ВРИ 5.1.3)
- До 49% озеленения замещается самой спортплощадкой (п.1.9.4 ПЗЗ)

Пример из ТЗ (1000 чел):
- sport_area = 1000 м²
- greening_required = 400 м²
- greening_substituted = 196 м²
- greening_extra = 204 м²
- plot_area = 1204 м²
"""

from __future__ import annotations

import pytest

from urban_model import verify_kit
from urban_model.calculations import sport
from urban_model.models import CalculationOptions, Site
from urban_model.models.social import SportFacilitiesSpec
from urban_model.normatives import load_normatives


@pytest.fixture(scope="module")
def spb():
    return load_normatives("spb")


# ---------------------------------------------------------------------------
# Чистая функция расчёта (calculations/sport.py)
# ---------------------------------------------------------------------------

class TestSportComputation:
    def test_example_from_tz_1000_people(self, spb):
        """Точно проверяем формулу на примере из ТЗ: 1000 чел → 1204 м² ЗУ."""
        br = sport.compute(1000.0, spb)
        assert br.sport_area == 1000.0
        assert br.greening_required == 400.0
        assert br.greening_substituted == pytest.approx(196.0, abs=0.01)
        assert br.greening_extra == pytest.approx(204.0, abs=0.01)
        assert br.plot_area == pytest.approx(1204.0, abs=0.01)

    def test_zero_population(self, spb):
        """При нулевом населении все площади = 0."""
        br = sport.compute(0.0, spb)
        assert br.sport_area == 0.0
        assert br.plot_area == 0.0

    def test_proportional_scaling(self, spb):
        """Линейная зависимость от населения."""
        br1 = sport.compute(1000.0, spb)
        br2 = sport.compute(2500.0, spb)
        assert br2.sport_area == pytest.approx(2.5 * br1.sport_area)
        assert br2.plot_area == pytest.approx(2.5 * br1.plot_area)


# ---------------------------------------------------------------------------
# Интеграция в forward.py: TEPResult + balance
# ---------------------------------------------------------------------------

@pytest.fixture
def site():
    return Site(area_m2=100_000)


class TestSportInResult:
    def test_fields_populated(self, spb, site):
        """TEPResult содержит все 4 поля sport_*."""
        res = verify_kit(1.5, site, CalculationOptions(floors=12), spb)
        assert res.sport_facilities_area.value > 0
        assert res.sport_facilities_plot_area.value > 0
        assert res.sport_facilities_greening_required.value > 0
        assert res.sport_facilities_greening_extra.value > 0
        # plot = sport + greening_extra
        assert res.sport_facilities_plot_area.value == pytest.approx(
            res.sport_facilities_area.value + res.sport_facilities_greening_extra.value
        )

    def test_in_balance_components(self, spb, site):
        """sport_facilities входит в balance.components."""
        res = verify_kit(1.5, site, CalculationOptions(floors=12), spb)
        assert "sport_facilities" in res.balance.components
        assert res.balance.components["sport_facilities"] > 0
        # Совпадает с sport_facilities_plot_area
        assert res.balance.components["sport_facilities"] == pytest.approx(
            res.sport_facilities_plot_area.value
        )

    def test_include_false_zeros_in_balance(self, spb, site):
        """include_sport_facilities=False: компонент = 0 в balance."""
        res_off = verify_kit(
            1.5, site,
            CalculationOptions(floors=12, include_sport_facilities=False),
            spb,
        )
        assert res_off.sport_facilities_plot_area.value == 0
        assert res_off.balance.components["sport_facilities"] == 0

    def test_only_demand_zeros_in_balance_but_keeps_area(self, spb, site):
        """only_demand=True: plot_area считается, но в баланс не идёт."""
        res = verify_kit(
            1.5, site,
            CalculationOptions(
                floors=12,
                sport_facilities=SportFacilitiesSpec(only_demand=True),
            ),
            spb,
        )
        # Информационное значение есть
        assert res.sport_facilities_plot_area.value > 0
        # Но в баланс не входит
        assert res.balance.components["sport_facilities"] == 0
        # И в formula — пометка
        assert "только потребность" in res.sport_facilities_plot_area.formula

    def test_include_off_increases_surplus(self, spb, site):
        """Выключение спорт-сооружений → больший резерв баланса."""
        res_on = verify_kit(1.5, site, CalculationOptions(floors=12), spb)
        res_off = verify_kit(
            1.5, site,
            CalculationOptions(floors=12, include_sport_facilities=False),
            spb,
        )
        assert res_off.balance.surplus > res_on.balance.surplus


# ---------------------------------------------------------------------------
# Нормативы — sanity check
# ---------------------------------------------------------------------------

class TestSportNormatives:
    def test_area_per_1000_is_1000(self, spb):
        assert spb.resolve("sport_facilities.area_per_1000") == 1000

    def test_greening_ratio_is_04(self, spb):
        assert spb.resolve("sport_facilities.greening_ratio") == 0.4

    def test_substitution_max_is_049(self, spb):
        assert spb.resolve("sport_facilities.greening_substitution_max") == 0.49

    def test_sources_present(self, spb):
        assert spb.source_of("sport_facilities.area_per_1000")
        assert spb.source_of("sport_facilities.greening_ratio")
        assert "1.9.4" in (
            spb.source_of("sport_facilities.greening_substitution_max") or ""
        )

    def test_defaults_in_options(self):
        """Дефолты CalculationOptions: include_sport_facilities=True."""
        opts = CalculationOptions(floors=12)
        assert opts.include_sport_facilities is True
        assert opts.sport_facilities.only_demand is False
