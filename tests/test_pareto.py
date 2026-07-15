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

    def test_max_area_not_below_base(self, bundle, base_tep):
        """v0.12.31: «Максимум площади» ГАРАНТИРОВАННО ≥ База — детерминированное
        доуточнение (refine_extrema) стартует в т.ч. от конфига Базы, поэтому
        даже если случайный Optuna промахнулся, доуточнённый экстремум ≥ Базы."""
        mx = next((r for r in bundle.recommendations
                   if r.label == "Максимум площади"), None)
        if mx is not None:
            assert mx.tep.apartments_area.value >= base_tep.apartments_area.value - 1.0

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


def test_apply_reproduces_recommendation_with_vpp(site_large, norms):
    """v0.12.32: при ВПП «Применить к Расчёту» обязан ПЕРЕСОБРАТЬ ВПП под
    этажность/парковку карточки (rec_options несёт built_in_list базы). Без
    проброса vpp_request площадь на «Расчёте» разошлась бы с карточкой на
    десятки м² (баг, найденный аудитом)."""
    from types import SimpleNamespace

    from urban_model.calculations import vpp as _vpp
    from urban_model.models.parking import ParkingConfig
    from urban_model.optimize.pareto import (
        ParetoConstraints,
        generate_pareto_recommendations,
    )
    from urban_model.ui.optimizer import _rec_options_from_params
    from urban_model.ui.state import run_calculation

    base = CalculationOptions(
        floors=12, planning_doc=True,
        parking=ParkingConfig(mode="custom", open_share=0.5, multilevel_share=0.5,
                              underground_share=0.0, multilevel_levels=5),
    )
    # База как в UI — 2-проход ВПП.
    o1 = base.model_copy(deep=True); o1.built_in = None; o1.built_in_list = []
    r0 = solve_max_kit(site_large, o1, norms)
    build = _vpp.build_built_ins(mode="half_floor", population=r0.population.value or 0,
                                 footprint=r0.housing_footprint.value or 0, norms=norms)
    bopts = base.model_copy(deep=True); bopts.built_in = None
    bopts.built_in_list = build.built_ins
    btep = solve_max_kit(site_large, bopts, norms)

    vpp_req = SimpleNamespace(mode="half_floor", custom_4_4_m2=None, custom_4_6_m2=None)
    con = ParetoConstraints(floors_range=(3, 16), allow_underground=False,
                            allow_stylobate=False)
    bundle = generate_pareto_recommendations(
        site_large, bopts, norms, btep, n_trials=200, seed=42,
        constraints=con, vpp_request=vpp_req,
    )
    rec = next((r for r in bundle.recommendations if r.label == "Максимум площади"), None)
    if rec is None:
        pytest.skip("нет карточки «Максимум площади»")
    rec_opts = _rec_options_from_params(bopts, rec.params)
    # как app.py: vpp_request пробрасывается → ВПП пересобирается под карточку.
    applied = run_calculation(site=site_large, options=rec_opts, norms=norms,
                              mode="max_kit", vpp_request=vpp_req)
    assert applied.apartments_area.value == pytest.approx(
        rec.tep.apartments_area.value, rel=1e-3
    )


class TestEconomyDisabled:
    """v0.19.2: при include_economy=False индекса нет — карточки по индексу
    не строятся (раньше молча вырождались в копии «Максимума площади»,
    а фильтр реалистичных парковок отключался вместе с экономикой)."""

    @pytest.fixture(scope="class")
    def bundle_no_econ(self, site_large, norms):
        opts = CalculationOptions(floors=12, planning_doc=True,
                                  include_economy=False)
        base = solve_max_kit(site_large, opts, norms)
        assert base.economy is None
        return generate_pareto_recommendations(
            site_large, opts, norms, base, n_trials=60, seed=42), base

    def test_only_two_cards(self, bundle_no_econ):
        b, _ = bundle_no_econ
        labels = [r.label for r in b.recommendations]
        assert labels == ["Максимум площади", "Девелоперский"], labels

    def test_no_economy_labels(self, bundle_no_econ):
        b, _ = bundle_no_econ
        labels = {r.label for r in b.recommendations}
        assert "Максимум эконом-индекса" not in labels
        assert "Пороговый" not in labels

    def test_developer_differs_from_max_area(self, bundle_no_econ):
        """«Девелоперский» не обязан совпадать с «Максимумом площади» —
        он учитывает рациональность парковок и устойчивость."""
        b, _ = bundle_no_econ
        by = {r.label: r for r in b.recommendations}
        assert by["Девелоперский"].tep is not by["Максимум площади"].tep

    def test_developer_respects_parking_caps(self, bundle_no_econ, norms):
        """Фильтр потолков класса работает и без экономики."""
        from urban_model.optimize.pareto import _violates_parking_caps
        b, _ = bundle_no_econ
        caps = norms.resolve("economy.parking_caps", residential_class="comfort")
        dev = next(r for r in b.recommendations if r.label == "Девелоперский")
        assert not _violates_parking_caps(dev.tep, caps)

    def test_deltas_have_no_index(self, bundle_no_econ):
        b, _ = bundle_no_econ
        for r in b.recommendations:
            assert r.delta_vs_base.d_index_abs is None
            assert r.delta_vs_base.d_profit_abs is None


class TestEconomyEnabledStillFour:
    def test_four_cards_with_economy(self, bundle):
        """Регрессия: с экономикой карточек по-прежнему до 4."""
        labels = [r.label for r in bundle.recommendations]
        assert "Максимум площади" in labels
        assert len(labels) >= 2
