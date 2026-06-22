"""Тесты для optimize/pareto.py (v0.9.0)."""

from __future__ import annotations

import pytest

from urban_model import solve_max_kit
from urban_model.models import CalculationOptions, Site
from urban_model.normatives import load_normatives
from urban_model.optimize.pareto import (
    DeltaSummary,
    ParetoBundle,
    Recommendation,
    _format_key_changes,
    generate_pareto_recommendations,
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


@pytest.fixture(scope="module")
def bundle(site_large, base_options, norms, base_tep):
    # n_trials=80 — компромисс между скоростью и стабильностью теста.
    # На детерминированном seed=42 даёт ~50 feasible — достаточно для всех 3 выборок.
    return generate_pareto_recommendations(
        site_large, base_options, norms, base_tep, n_trials=80, seed=42,
    )


class TestParetoBundle:
    def test_returns_bundle(self, bundle):
        assert isinstance(bundle, ParetoBundle)
        assert bundle.n_trials_total >= 1
        assert bundle.n_trials_feasible >= 1

    def test_returns_recommendations(self, bundle):
        # v0.12.1: до 4 стратегий (площадь / эконом-индекс / сбаланс. /
        # девелоперский). Может быть меньше при дедупе на узком пуле.
        assert 2 <= len(bundle.recommendations) <= 4

    def test_recommendations_have_distinct_labels(self, bundle):
        labels = [r.label for r in bundle.recommendations]
        assert len(labels) == len(set(labels))

    def test_each_rec_has_delta(self, bundle):
        for r in bundle.recommendations:
            assert isinstance(r, Recommendation)
            assert isinstance(r.delta_vs_base, DeltaSummary)


class TestDeltaSummary:
    def test_apt_sign_consistency(self, bundle, base_tep):
        """Знак d_apt_pct должен совпадать со знаком (scenario.apt - base.apt)."""
        for r in bundle.recommendations:
            scen_apt = float(r.tep.apartments_area.value or 0.0)
            base_apt = float(base_tep.apartments_area.value or 0.0)
            actual_diff = scen_apt - base_apt
            if abs(actual_diff) > 1e-3:
                assert (
                    (r.delta_vs_base.d_apt_pct > 0) == (actual_diff > 0)
                ), f"Знак d_apt_pct не совпадает: {r.delta_vs_base.d_apt_pct} vs {actual_diff}"


class TestKeyChanges:
    def test_floors_diff_in_text(self):
        """При разной этажности строка должна содержать 'Этажность:'."""
        base = CalculationOptions(floors=12)
        changes = _format_key_changes(base, {"floors": 20})
        assert any("Этажность" in c for c in changes)
        assert any("20" in c for c in changes)

    def test_no_changes_when_identical(self):
        base = CalculationOptions(floors=12)
        # Параметры, совпадающие с базой → не должно быть отличий
        changes = _format_key_changes(base, {"floors": 12})
        assert changes == []

    def test_znop_change_in_text(self):
        base = CalculationOptions(floors=12)  # znop_per_person_override=None → база=0
        changes = _format_key_changes(base, {"znop_per_person": 6.0})
        assert any("ЗНОП" in c for c in changes)


# v0.10.1: «Применить к Расчёту» — расчёт по rec_options должен воспроизводить
# результат карточки (валидирует _rec_options_from_params + override-путь).

def test_apply_reproduces_recommendation(site_large, base_options, norms, bundle):
    from urban_model.ui.optimizer import _rec_options_from_params
    if not bundle.recommendations:
        pytest.skip("нет рекомендаций")
    rec = bundle.recommendations[0]
    rec_opts = _rec_options_from_params(base_options, rec.params)
    reproduced = solve_max_kit(site_large, rec_opts, norms)
    # Площадь квартир и КИТ совпадают с tep карточки (в пределах допуска).
    assert reproduced.apartments_area.value == pytest.approx(
        rec.tep.apartments_area.value, rel=1e-3
    )
    assert reproduced.kit.value == pytest.approx(rec.tep.kit.value, rel=1e-3)
