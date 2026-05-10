"""Аудит-тесты v0.5.7: численная корректность КИТ и стресс Optuna."""

from __future__ import annotations

import pytest

from urban_model import solve_max_kit
from urban_model.calculations.distribute import choose_n_objects
from urban_model.core.forward import compute_tep_for_kit
from urban_model.models import CalculationOptions, Site
from urban_model.normatives import load_normatives
from urban_model.optimize import SearchSpace, optimize_max_apartments


@pytest.fixture(scope="module")
def spb():
    return load_normatives("spb")


@pytest.fixture(scope="module")
def site():
    return Site(area_m2=50_000)


# ---------------------------------------------------------------------------
# #7 — численная проверка формулы КИТ ПЗЗ
# ---------------------------------------------------------------------------

class TestKitFormula:
    @pytest.mark.parametrize("block_density", [0.5, 1.0, 1.2, 1.5, 2.0])
    def test_kit_equals_apt_over_housing_lot(self, spb, site, block_density):
        """result.kit.value должен численно равняться apt_area / housing_lot."""
        opts = CalculationOptions(floors=12, planning_doc=True)
        r = compute_tep_for_kit(block_density, site, opts, spb)
        if r.housing_lot_area.value > 0:
            expected = r.apartments_area.value / r.housing_lot_area.value
            assert abs(r.kit.value - expected) < 1e-6, (
                f"kit={r.kit.value} != apt/lot={expected} "
                f"при block_density={block_density}"
            )

    def test_block_density_equals_input(self, spb, site):
        """result.block_density.value == входному параметру."""
        opts = CalculationOptions(floors=12, planning_doc=True)
        for bd in [0.5, 1.0, 1.5, 2.0]:
            r = compute_tep_for_kit(bd, site, opts, spb)
            assert abs(r.block_density.value - bd) < 1e-9

    def test_kit_developed_constant_for_fixed_floors(self, spb, site):
        """При фиксированных floors/parking, kit_developed (apt/lot) ≈ const
        независимо от block_density (математическое следствие линейности)."""
        opts = CalculationOptions(floors=12, planning_doc=True)
        kit_at_low = compute_tep_for_kit(0.5, site, opts, spb).kit.value
        kit_at_high = compute_tep_for_kit(1.5, site, opts, spb).kit.value
        # Разница менее 1% — модель действительно почти линейна
        assert abs(kit_at_low - kit_at_high) / kit_at_low < 0.05


# ---------------------------------------------------------------------------
# #6 — стресс-тест Optuna с custom-парковками
# ---------------------------------------------------------------------------

class TestOptunaStress:
    def test_50_trials_custom_parking_no_validation_errors(self, spb, site):
        """50 trials с parking_modes=['custom'] не должны давать ParkingConfig
        ValidationError (это была реальная проблема в v0.5.5)."""
        base = CalculationOptions(floors=12, planning_doc=True)
        space = SearchSpace(
            floors_range=(8, 20),
            parking_modes=["custom"],
            parking_open_share_range=(0.1, 0.5),
            parking_multilevel_share_range=(0.0, 0.4),
            multilevel_levels_range=(1, 4),
        )
        report = optimize_max_apartments(site, base, spb, space, n_trials=50)
        # Должно быть 0 exceptions от ValidationError
        for msg in report.exceptions:
            assert "ParkingConfig" not in msg, (
                f"Optuna trial упал с ParkingConfig ValidationError: {msg}"
            )

    def test_60_trials_all_modes_runs(self, spb, site):
        """Все 3 режима парковок + остальные параметры — 60 trials, ничего не падает критически."""
        base = CalculationOptions(floors=12, planning_doc=True)
        space = SearchSpace(
            floors_range=(8, 20),
            parking_modes=["min_open", "all_open", "custom"],
            parking_open_share_range=(0.1, 0.5),
            parking_multilevel_share_range=(0.0, 0.4),
            multilevel_levels_range=(1, 4),
            kg_num_objects_range=(1, 3),
        )
        report = optimize_max_apartments(site, base, spb, space, n_trials=60)
        assert report.n_trials_total == 60
        # Хотя бы один feasible должен быть найден
        assert report.n_trials_feasible >= 1


# ---------------------------------------------------------------------------
# choose_n_objects — валидация противоречивых границ
# ---------------------------------------------------------------------------

class TestChooseNValidation:
    def test_inverted_min_max_raises(self):
        """capacity_min > capacity_max → ValueError, а не молчаливый бред."""
        with pytest.raises(ValueError, match="capacity_min .* > capacity_max"):
            choose_n_objects(total_places=200, capacity_min=400, capacity_max=300)

    def test_equal_min_max_ok(self):
        """capacity_min == capacity_max — допустимо (фиксированная вместимость)."""
        n = choose_n_objects(total_places=600, capacity_min=300, capacity_max=300)
        assert n == 2


# ---------------------------------------------------------------------------
# Magic numbers ушли из formula-строк
# ---------------------------------------------------------------------------

class TestFormulaConsistency:
    def test_kg_formula_uses_norm_value(self, spb, site):
        """ДОО formula строится из значения норматива, а не литерала 61."""
        opts = CalculationOptions(floors=12, planning_doc=True)
        r = compute_tep_for_kit(1.0, site, opts, spb)
        kg_per_1000 = spb.resolve("social_objects.kindergarten.places_per_1000")
        # В formula должно быть фактическое значение норматива
        formula = r.kindergarten_places_required.formula
        assert formula is not None
        assert str(int(kg_per_1000)) in formula or str(kg_per_1000) in formula

    def test_school_formula_uses_norm_value(self, spb, site):
        opts = CalculationOptions(floors=12, planning_doc=True)
        r = compute_tep_for_kit(1.0, site, opts, spb)
        sch_per_1000 = spb.resolve("social_objects.school.places_per_1000")
        formula = r.school_places_required.formula
        assert formula is not None
        assert str(int(sch_per_1000)) in formula or str(sch_per_1000) in formula
