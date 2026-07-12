"""Тесты для optimize/target.py — подбор под целевую площадь квартир
и ЗНОП-режим настроек подбора (v0.16.0)."""

from __future__ import annotations

import pytest

from urban_model import solve_max_kit
from urban_model.models import CalculationOptions, Site
from urban_model.normatives import load_normatives
from urban_model.optimize.pareto import (
    ParetoConstraints,
    apply_znop_constraints,
)
from urban_model.optimize.target import (
    TargetBundle,
    _FAMILY_LABELS,
    _family_allowed,
    _shares_from_params,
    generate_target_recommendations,
)


@pytest.fixture(scope="module")
def norms():
    return load_normatives("spb")


@pytest.fixture(scope="module")
def site_large():
    return Site(area_m2=50_000)


@pytest.fixture(scope="module")
def base_options():
    return CalculationOptions(floors=12)


@pytest.fixture(scope="module")
def base_tep(site_large, base_options, norms):
    return solve_max_kit(site_large, base_options, norms)


# ---------------------------------------------------------------------------
# ЗНОП-режим настроек подбора
# ---------------------------------------------------------------------------

class TestApplyZnopConstraints:
    def test_base_mode_returns_same_object(self, base_options):
        c = ParetoConstraints(znop_mode="base")
        assert apply_znop_constraints(base_options, c) is base_options
        assert apply_znop_constraints(base_options, None) is base_options

    def test_normative_clears_overrides(self):
        opts = CalculationOptions(floors=12, znop_per_person_override=6.0)
        c = ParetoConstraints(znop_mode="normative")
        out = apply_znop_constraints(opts, c)
        assert out.znop_per_person_override is None
        assert out.znop_total_area_override is None
        # Исходный объект не мутирован
        assert opts.znop_per_person_override == 6.0

    def test_manual_sets_override(self, base_options):
        c = ParetoConstraints(znop_mode="manual", znop_value=4.0)
        out = apply_znop_constraints(base_options, c)
        assert out.znop_per_person_override == 4.0
        assert out.znop_total_area_override is None

    def test_manual_without_value_is_noop(self, base_options):
        c = ParetoConstraints(znop_mode="manual", znop_value=None)
        assert apply_znop_constraints(base_options, c) is base_options


# ---------------------------------------------------------------------------
# Хелперы целевого режима
# ---------------------------------------------------------------------------

class TestTargetHelpers:
    def test_shares_from_modes(self):
        assert _shares_from_params({"parking_mode": "all_open"}) == (1.0, 0.0, 0.0, 0.0)
        assert _shares_from_params({"parking_mode": "min_open"}) == (0.125, 0.0, 0.875, 0.0)
        sh = _shares_from_params({
            "parking_mode": "custom",
            "parking_open_share": 0.5, "parking_ml_share": 0.5,
            "parking_ug_share": 0.0, "parking_stylobate_share": 0.0,
        })
        assert sh == (0.5, 0.5, 0.0, 0.0)

    def test_shares_invalid_sum_returns_none(self):
        assert _shares_from_params({
            "parking_mode": "custom",
            "parking_open_share": 0.2, "parking_ml_share": 0.2,
        }) is None

    def test_family_allowed_respects_constraints(self):
        c = ParetoConstraints(allow_underground=False, allow_stylobate=False)
        assert _family_allowed("surface", c)
        assert _family_allowed("multilevel", c)
        assert not _family_allowed("underground", c)
        assert not _family_allowed("stylobate", c)


# ---------------------------------------------------------------------------
# Полный прогон (малый n_trials — как в test_pareto)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def bundle_reachable(site_large, base_options, norms, base_tep):
    """Цель заметно ниже базовой площади — достижима наверняка."""
    target = float(base_tep.apartments_area.value) * 0.7
    return generate_target_recommendations(
        site_large, base_options, norms, base_tep,
        target_m2=target, n_trials=80, seed=42,
    )


class TestTargetReachable:
    def test_returns_bundle(self, bundle_reachable):
        assert isinstance(bundle_reachable, TargetBundle)
        assert bundle_reachable.n_trials_feasible >= 1

    def test_achievable(self, bundle_reachable):
        assert bundle_reachable.achievable
        assert bundle_reachable.max_apartments >= bundle_reachable.target_m2

    def test_has_recommendations_with_family_labels(self, bundle_reachable):
        assert len(bundle_reachable.recommendations) >= 1
        labels = set(_FAMILY_LABELS.values())
        for r in bundle_reachable.recommendations:
            assert r.label in labels

    def test_reaching_recs_meet_target(self, bundle_reachable):
        """Каждая карточка либо достигает цели, либо честно говорит «НЕ»."""
        t = bundle_reachable.target_m2
        for r in bundle_reachable.recommendations:
            apt = float(r.tep.apartments_area.value or 0.0)
            if "НЕ достигается" in r.rationale:
                assert apt < t
            else:
                assert apt >= t

    def test_all_recs_feasible(self, bundle_reachable):
        for r in bundle_reachable.recommendations:
            assert r.tep.balance.is_feasible


class TestTargetUnreachable:
    def test_absurd_target_reported(self, site_large, base_options, norms, base_tep):
        target = float(base_tep.apartments_area.value) * 50.0
        b = generate_target_recommendations(
            site_large, base_options, norms, base_tep,
            target_m2=target, n_trials=40, seed=42,
        )
        assert not b.achievable
        assert b.note is not None and "недостижима" in b.note
        # Карточки всё равно показываются — максимум каждого семейства.
        for r in b.recommendations:
            assert "НЕ достигается" in r.rationale


class TestZnopModeReachesTrials:
    def test_manual_znop_in_pareto_recs(self, site_large, base_options, norms, base_tep):
        """znop_mode='manual' → у всех рекомендаций ЗНОП = заданному значению."""
        from urban_model.optimize.pareto import generate_pareto_recommendations
        c = ParetoConstraints(znop_mode="manual", znop_value=6.0)
        b = generate_pareto_recommendations(
            site_large, base_options, norms, base_tep,
            n_trials=40, seed=42, constraints=c,
        )
        assert b.recommendations, "ожидались рекомендации"
        for r in b.recommendations:
            assert float(r.tep.znop_per_person.value or 0.0) == pytest.approx(6.0)


class TestTargetRespectsConstraints:
    def test_forbidden_families_absent(self, site_large, base_options, norms, base_tep):
        """Запрет подземных и стилобата → таких семейств нет в карточках."""
        c = ParetoConstraints(allow_underground=False, allow_stylobate=False)
        target = float(base_tep.apartments_area.value) * 0.7
        b = generate_target_recommendations(
            site_large, base_options, norms, base_tep,
            target_m2=target, n_trials=60, seed=42, constraints=c,
        )
        forbidden = {_FAMILY_LABELS["underground"], _FAMILY_LABELS["stylobate"]}
        for r in b.recommendations:
            assert r.label not in forbidden
