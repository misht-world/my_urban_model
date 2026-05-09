"""Базовые юнит-тесты на чистые расчётные функции."""

from __future__ import annotations

import math

import pytest

from urban_model.calculations import housing, population, znop, parking
from urban_model.calculations.rounding import round_up_to_multiple, ceil_int
from urban_model.normatives import load_normatives


@pytest.fixture(scope="module")
def spb():
    return load_normatives("spb")


class TestRounding:
    def test_kindergarten_rule(self):
        assert round_up_to_multiple(196, 5) == 200
        assert round_up_to_multiple(200, 5) == 200
        assert round_up_to_multiple(201, 5) == 205

    def test_school_rule(self):
        assert round_up_to_multiple(382, 10) == 390
        assert round_up_to_multiple(390, 10) == 390

    def test_zero_is_zero(self):
        assert round_up_to_multiple(0, 5) == 0

    def test_invalid_multiple(self):
        with pytest.raises(ValueError):
            round_up_to_multiple(10, 0)


class TestHousing:
    def test_gfa_from_kit(self):
        assert housing.gfa_from_kit(2.0, 10_000) == 20_000

    def test_apartments_area_no_vpp(self):
        assert housing.apartments_area(20_000, vpp_share=0.0, ratio=0.75) == 15_000

    def test_apartments_area_with_vpp(self):
        # GFA × (1 − ВПП) × ratio
        assert housing.apartments_area(20_000, vpp_share=0.1, ratio=0.75) == 13_500

    def test_invalid_vpp(self):
        with pytest.raises(ValueError):
            housing.apartments_area(20_000, vpp_share=1.0, ratio=0.75)

    def test_footprint(self):
        assert housing.housing_footprint(20_000, floors=10) == 2_000


class TestPopulation:
    def test_basic(self):
        assert population.population(28_000, 28) == 1_000

    def test_density(self):
        # 1000 чел на 1 га = 10000 м²
        assert population.density_chel_per_ga(1_000, 10_000) == pytest.approx(1_000)

    def test_invalid_provision(self):
        with pytest.raises(ValueError):
            population.population(1000, 0)


class TestZnop:
    def test_thresholds(self, spb):
        assert znop.znop_per_person(1.5, spb) == 0
        assert znop.znop_per_person(1.7, spb) == 3
        assert znop.znop_per_person(1.9, spb) == 4
        assert znop.znop_per_person(2.5, spb) == 6

    def test_total_area(self, spb):
        assert znop.znop_total_area(1000, 2.0, spb) == 1000 * 6


class TestParking:
    def test_required_places(self, spb):
        # 80 m² на одно м/м: при 8000 м² квартир — 100 м/м
        assert parking.required_places_for_apartments(8000, spb) == pytest.approx(100)

    def test_open_min_share(self, spb):
        # 12.5% от 100 = 12.5 → ceil → 13
        assert parking.open_places_min(100, spb) == 13
