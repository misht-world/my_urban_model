"""Тесты расширенной Scenario.mode (v0.5.2): поддержка всех 4 режимов в run_scenarios."""

from __future__ import annotations

import pytest

from urban_model.models import CalculationOptions, Scenario, Site
from urban_model.modes.compare import compare_scenarios, run_scenarios
from urban_model.normatives import load_normatives


@pytest.fixture(scope="module")
def spb():
    return load_normatives("spb")


@pytest.fixture(scope="module")
def site():
    return Site(area_m2=50_000)


# ---------------------------------------------------------------------------
# Pydantic-валидация: обязательные поля для каждого режима
# ---------------------------------------------------------------------------

class TestScenarioValidation:
    def test_verify_requires_kit(self, site):
        with pytest.raises(ValueError, match="kit"):
            Scenario(name="bad", site=site, mode="verify")

    def test_with_reserve_requires_target_surplus(self, site):
        with pytest.raises(ValueError, match="target_surplus_m2"):
            Scenario(name="bad", site=site, mode="with_reserve")

    def test_with_znop_requires_target_znop(self, site):
        with pytest.raises(ValueError, match="target_znop_per_person"):
            Scenario(name="bad", site=site, mode="with_znop")

    def test_inverse_no_extras_required(self, site):
        # mode по умолчанию = inverse, никаких extra-полей не нужно
        sc = Scenario(name="ok", site=site)
        assert sc.mode == "inverse"


# ---------------------------------------------------------------------------
# run_scenarios: каждый режим даёт TEPResult с правильным КИТ-поведением
# ---------------------------------------------------------------------------

class TestRunScenariosAllModes:
    def test_run_inverse_and_verify(self, spb, site):
        """В режиме verify входной `kit` интерпретируется как block_density."""
        scs = [
            Scenario(name="макс", site=site, mode="inverse"),
            Scenario(name="фикс 1.0", site=site, mode="verify", kit=1.0),
        ]
        pairs = run_scenarios(scs, spb)
        assert len(pairs) == 2
        # В verify-режиме block_density фиксируется на входном значении.
        assert pairs[1][1].block_density.value == 1.0

    def test_run_with_reserve(self, spb, site):
        sc = Scenario(
            name="резерв 5к",
            site=site,
            mode="with_reserve",
            target_surplus_m2=5_000,
        )
        pairs = run_scenarios([sc], spb)
        res = pairs[0][1]
        # Если решение feasible — резерв ≥ target (с допуском)
        if res.balance.is_feasible:
            assert res.balance.surplus >= 5_000 - 1.0

    def test_run_with_znop(self, spb, site):
        sc = Scenario(
            name="ЗНОП=6",
            site=site,
            mode="with_znop",
            target_znop_per_person=6.0,
        )
        pairs = run_scenarios([sc], spb)
        res = pairs[0][1]
        # ЗНОП должен быть зафиксирован вручную = 6
        assert res.znop_per_person.value == 6.0
        assert res.znop_per_person.status.value == "manual"

    def test_compare_all_four_modes(self, spb, site):
        """compare_scenarios строит DataFrame со всеми 4 режимами."""
        scs = [
            Scenario(name="A_inverse", site=site),
            Scenario(name="B_verify", site=site, mode="verify", kit=1.0),
            Scenario(
                name="C_reserve", site=site, mode="with_reserve",
                target_surplus_m2=3_000,
            ),
            Scenario(
                name="D_znop", site=site, mode="with_znop",
                target_znop_per_person=5.0,
            ),
        ]
        df = compare_scenarios(scs, spb)
        assert list(df.columns) == ["A_inverse", "B_verify", "C_reserve", "D_znop"]
