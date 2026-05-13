"""Тесты загрузчика и резолвера нормативов."""

from __future__ import annotations

import math

import pytest

from urban_model.normatives import load_normatives


@pytest.fixture(scope="module")
def spb():
    return load_normatives("spb")


@pytest.fixture(scope="module")
def ru():
    return load_normatives("russia")


class TestScalar:
    def test_simple_value(self, spb):
        assert spb.resolve("housing_provision") == 28

    def test_source_present(self, spb):
        assert spb.source_of("housing_provision") == "НГП СПб"


class TestPiecewise:
    def test_kindergarten_plot_below_100(self, spb):
        assert spb.resolve("social_objects.kindergarten.plot_area_per_place", capacity=99) == 45

    def test_kindergarten_plot_at_100_boundary(self, spb):
        # граница "до 100" включительно — по семантике up_to
        assert spb.resolve("social_objects.kindergarten.plot_area_per_place", capacity=100) == 45

    def test_kindergarten_plot_above_100(self, spb):
        assert spb.resolve("social_objects.kindergarten.plot_area_per_place", capacity=180) == 40

    def test_kindergarten_plot_inf_branch(self, spb):
        assert spb.resolve("social_objects.kindergarten.plot_area_per_place", capacity=10_000) == 40

    def test_school_piecewise(self, spb):
        # v0.6.5: данные от КС — II/III параллели (≤825) → 30, IV+ → 25
        assert spb.resolve("social_objects.school.building_area_per_place", capacity=550) == 30
        assert spb.resolve("social_objects.school.building_area_per_place", capacity=825) == 30
        assert spb.resolve("social_objects.school.building_area_per_place", capacity=1100) == 25
        assert spb.resolve("social_objects.school.building_area_per_place", capacity=2475) == 25

    def test_school_plot_per_place(self, spb):
        # spb-переопределение для СОШ: II→35, III→28, IV/V→24, VI–IX→22
        assert spb.resolve("social_objects.school.plot_area_per_place", capacity=550) == 35
        assert spb.resolve("social_objects.school.plot_area_per_place", capacity=825) == 28
        assert spb.resolve("social_objects.school.plot_area_per_place", capacity=1100) == 24
        assert spb.resolve("social_objects.school.plot_area_per_place", capacity=1375) == 24
        assert spb.resolve("social_objects.school.plot_area_per_place", capacity=1650) == 22
        assert spb.resolve("social_objects.school.plot_area_per_place", capacity=2475) == 22

    def test_school_allowed_capacities(self, spb):
        caps = spb.resolve("social_objects.school.allowed_capacities")
        assert caps == [550, 825, 1100, 1375, 1650, 1925, 2200, 2475]

    def test_school_rounding_is_25(self, spb):
        # v0.6.5: кратность 25 мест (СП Градостроительство)
        assert spb.resolve("social_objects.school.rounding") == 25

    def test_znop_thresholds(self, spb):
        assert spb.resolve("znop_per_person", kit=1.4) == 0
        assert spb.resolve("znop_per_person", kit=1.7) == 3
        assert spb.resolve("znop_per_person", kit=1.9) == 4
        assert spb.resolve("znop_per_person", kit=2.3) == 6

    def test_missing_arg_raises(self, spb):
        with pytest.raises(KeyError):
            spb.resolve("znop_per_person")


class TestConditional:
    def test_capacity_max_detached(self, spb):
        assert spb.resolve(
            "social_objects.kindergarten.capacity_max", building_type="detached"
        ) == 350

    def test_capacity_max_built_in(self, spb):
        # capacity_max для встроенного ДОО принято как для отдельно стоящего (350)
        assert spb.resolve(
            "social_objects.kindergarten.capacity_max", building_type="built_in"
        ) == 350

    def test_capacity_min_built_in(self, spb):
        # capacity_min встроенного ДОО = 120 (Письмо К.Обр)
        assert spb.resolve(
            "social_objects.kindergarten.capacity_min", building_type="built_in"
        ) == 120

    def test_per_branch_source(self, spb):
        assert spb.source_of(
            "social_objects.kindergarten.capacity_max", building_type="detached"
        ) == "РМД 15-26-2017"
        # built_in capacity_max — принято условно
        assert "принято" in spb.source_of(
            "social_objects.kindergarten.capacity_max", building_type="built_in"
        ).lower()

    def test_kit_limits(self, spb):
        assert spb.resolve("kit_limits", planning_doc="yes") == 2.5
        assert spb.resolve("kit_limits", planning_doc="no") == 1.4


class TestInheritance:
    def test_quarter_share_from_russia(self, spb):
        # этого ключа нет в spb.yaml — должен наследоваться от russia
        assert spb.resolve("greening.quarter_min_share") == 0.25

    def test_spb_overrides_kindergarten_plot(self, spb, ru):
        # russia: до 100 = 44; spb: до 100 = 45
        assert ru.resolve("social_objects.kindergarten.plot_area_per_place", capacity=80) == 44
        assert spb.resolve("social_objects.kindergarten.plot_area_per_place", capacity=80) == 45

    def test_multilevel_overridden(self, spb, ru):
        # russia: 500; spb: 300
        assert ru.resolve("parking.multilevel_capacity_max_default") == 500
        assert spb.resolve("parking.multilevel_capacity_max") == 300


class TestAliases:
    def test_alias_resolve(self, spb):
        # housing → vri.2.6 — но раздела vri.2.6 у нас пока нет, проверяем,
        # что для отсутствующего пути выбрасывается KeyError, а не некорректный ответ
        with pytest.raises(KeyError):
            spb.resolve("housing.something")
