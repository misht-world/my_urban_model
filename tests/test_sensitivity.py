"""Тесты v0.9.15 — анализ чувствительности (tornado)."""

from __future__ import annotations

import pytest

from urban_model.models import CalculationOptions, Site
from urban_model.normatives import load_normatives
from urban_model.optimize.sensitivity import FactorImpact, compute_sensitivity


@pytest.fixture(scope="module")
def spb():
    return load_normatives("spb")


@pytest.fixture
def site():
    return Site(area_m2=50_000)


def test_returns_factor_impacts(spb, site):
    imps = compute_sensitivity(site, CalculationOptions(floors=12), spb)
    assert imps
    assert all(isinstance(im, FactorImpact) for im in imps)


def test_sorted_by_apt_swing_desc(spb, site):
    imps = compute_sensitivity(site, CalculationOptions(floors=12), spb)
    swings = [im.apt_swing for im in imps]
    assert swings == sorted(swings, reverse=True)


def test_unified_base_apt_across_factors(spb, site):
    """Все факторы используют ЕДИНУЮ базовую площадь — % сопоставимы."""
    imps = compute_sensitivity(site, CalculationOptions(floors=12), spb)
    bases = {round(im.base_apt) for im in imps}
    assert len(bases) == 1  # одна общая база


def test_floors_is_a_strong_factor(spb, site):
    """Этажность обычно в числе самых влиятельных факторов."""
    imps = compute_sensitivity(site, CalculationOptions(floors=12), spb)
    labels = [im.label for im in imps[:2]]
    assert any("тажност" in lbl for lbl in labels)


def test_swing_is_nonnegative(spb, site):
    imps = compute_sensitivity(site, CalculationOptions(floors=12), spb)
    assert all(im.apt_swing >= 0 for im in imps)
    assert all(im.high_apt >= im.low_apt for im in imps)
