"""Тесты strict-наполняемости соцобъектов и соц-эффективности (v0.12.4)."""

from __future__ import annotations

import pytest

from urban_model import solve_max_kit
from urban_model.calculations.distribute import split_to_allowed_capacities
from urban_model.models import CalculationOptions, KindergartenSpec, SchoolSpec, Site
from urban_model.normatives import load_normatives


@pytest.fixture(scope="module")
def spb():
    return load_normatives("spb")


# --- split_to_allowed_capacities ---

def test_allowed_snaps_up_to_standard():
    allowed = [90, 120, 160, 250, 350]
    # спрос 130 → наименьшая разрешённая ≥130 = 160
    assert split_to_allowed_capacities(130, allowed) == [160]


def test_allowed_respects_capacity_min():
    allowed = [90, 120, 160, 250, 350]
    # спрос 100, но capacity_min=160 → 160
    assert split_to_allowed_capacities(100, allowed, capacity_min=160) == [160]


def test_allowed_splits_when_exceeds_max():
    allowed = [550, 1100, 2475]
    # спрос 3000 > 2475 → 2 объекта; каждый снапнут к типовому ≥ 1500 = 2475
    res = split_to_allowed_capacities(3000, allowed)
    assert len(res) == 2
    assert all(c in allowed for c in res)
    assert sum(res) >= 3000


def test_allowed_single_when_fits():
    allowed = [550, 825, 1100, 1375]
    # спрос 1080 → 1 школа 1100
    assert split_to_allowed_capacities(1080, allowed) == [1100]


# --- интеграция: strict_capacity ---

def test_strict_capacity_uses_standard_sizes(spb):
    site = Site(area_m2=80_000)
    r = solve_max_kit(
        site,
        CalculationOptions(
            floors=16,
            kindergarten=KindergartenSpec(strict_capacity=True),
            school=SchoolSpec(strict_capacity=True),
        ),
        spb,
    )
    kg_allowed = spb.resolve("social_objects.kindergarten.allowed_capacities")
    sch_allowed = spb.resolve("social_objects.school.allowed_capacities")
    # accepted = фактическая вместимость корпусов; должна попадать в типовой ряд
    # (для одного корпуса) или быть суммой типовых.
    assert r.kindergarten_places_accepted.value in kg_allowed or (
        r.kindergarten_places_accepted.value > max(kg_allowed)
    )
    assert r.school_places_accepted.value in sch_allowed or (
        r.school_places_accepted.value > max(sch_allowed)
    )


def test_strict_accepted_ge_required(spb):
    site = Site(area_m2=80_000)
    r = solve_max_kit(
        site,
        CalculationOptions(floors=16, school=SchoolSpec(strict_capacity=True)),
        spb,
    )
    # профицит: принятая вместимость ≥ требуемой
    assert r.school_places_accepted.value >= r.school_places_required.value


def test_strict_off_by_default(spb):
    assert KindergartenSpec().strict_capacity is False
    assert SchoolSpec().strict_capacity is False


# --- соц-эффективность в developer_score ---

def test_social_plot_per_place_helper(spb):
    from urban_model.optimize.pareto import _social_plot_per_place
    site = Site(area_m2=80_000)
    r = solve_max_kit(site, CalculationOptions(floors=16), spb)
    spp = _social_plot_per_place(r)
    # ЗУ/место положителен и в разумных пределах (десятки м²/место)
    assert spp > 0
    plot = r.kindergarten_plot_area.value + r.school_plot_area.value
    places = r.kindergarten_places_accepted.value + r.school_places_accepted.value
    assert spp == pytest.approx(plot / places, rel=1e-6)
