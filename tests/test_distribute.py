"""Тесты равномерного распределения мест по объектам ДОО/СОШ (v0.5.6)."""

from __future__ import annotations

import pytest

from urban_model.calculations.distribute import (
    choose_n_objects,
    distribute_places_evenly,
)


# ---------------------------------------------------------------------------
# distribute_places_evenly
# ---------------------------------------------------------------------------

class TestDistributeEvenly:
    def test_exact_division(self):
        """1500 / 3 = 500 ровно — все объекты равные."""
        assert distribute_places_evenly(1500, 3, multiple=10) == [500, 500, 500]

    def test_with_remainder_one_unit(self):
        """800 / 3 с шагом 5 → один объект больше остальных на multiple."""
        result = distribute_places_evenly(800, 3, multiple=5)
        assert result == [270, 265, 265]
        assert sum(result) == 800

    def test_with_remainder(self):
        """820 / 3 с шагом 10: units=82, base=27, rem=1 → [280, 270, 270]."""
        result = distribute_places_evenly(820, 3, multiple=10)
        assert result == [280, 270, 270]
        assert sum(result) == 820

    def test_two_unit_remainder(self):
        """1100 / 3 с шагом 10: units=110, base=36, rem=2 → [370, 370, 360]."""
        result = distribute_places_evenly(1100, 3, multiple=10)
        assert result == [370, 370, 360]
        assert sum(result) == 1100

    def test_difference_at_most_multiple(self):
        """Разница между крайними объектами не может превышать multiple."""
        for total in [100, 250, 500, 1000, 1530, 2000]:
            for n in [1, 2, 3, 4, 5]:
                result = distribute_places_evenly(total, n, multiple=10)
                if result and total % 10 == 0:
                    assert max(result) - min(result) <= 10
                    assert sum(result) == total

    def test_single_object(self):
        assert distribute_places_evenly(800, 1, multiple=5) == [800]

    def test_zero_total(self):
        assert distribute_places_evenly(0, 3, multiple=5) == [0, 0, 0]

    def test_zero_n(self):
        assert distribute_places_evenly(800, 0, multiple=5) == []


# ---------------------------------------------------------------------------
# choose_n_objects
# ---------------------------------------------------------------------------

class TestChooseN:
    def test_fits_in_one(self):
        """200 мест, max=350 → 1 объект."""
        assert choose_n_objects(200, capacity_min=160, capacity_max=350) == 1

    def test_needs_multiple(self):
        """800 мест, max=350 → 3 объекта (round up)."""
        assert choose_n_objects(800, capacity_min=160, capacity_max=350) == 3

    def test_min_constraint_reduces_n(self):
        """500 мест, min=300, max=350 → 1 объект (500/2=250 < 300)."""
        # 500/2=250, 500/1=500. Min 300 → выбираем 1.
        assert choose_n_objects(500, capacity_min=300, capacity_max=350) == 1


# ---------------------------------------------------------------------------
# Интеграция: split_into_objects в kindergarten/school
# ---------------------------------------------------------------------------

class TestSplitIntoObjects:
    def test_kindergarten_split_evenly(self):
        from urban_model.calculations import kindergarten
        # 800 мест, max=350, min=160 → 3 объекта
        result = kindergarten.split_into_objects(
            total_places=800,
            spec_capacity=None, spec_count=None,
            capacity_min=160, capacity_max=350,
            multiple=5,
        )
        assert result == [270, 265, 265]
        assert sum(result) == 800

    def test_school_split_evenly(self):
        from urban_model.calculations import school
        # 2200 мест, max=1500, min=550 → 2 объекта
        result = school.split_into_objects(
            total_places=2200,
            spec_capacity=None, spec_count=None,
            capacity_min=550, capacity_max=1500,
            multiple=10,
        )
        assert result == [1100, 1100]

    def test_school_explicit_spec(self):
        from urban_model.calculations import school
        # Если задано вручную — используем как есть
        result = school.split_into_objects(
            total_places=1000,
            spec_capacity=500, spec_count=2,
            capacity_min=550, capacity_max=1500,
            multiple=10,
        )
        assert result == [500, 500]


# ---------------------------------------------------------------------------
# Driveway overrides
# ---------------------------------------------------------------------------

class TestDrivewayOverrides:
    @pytest.fixture(scope="class")
    def spb(self):
        from urban_model.normatives import load_normatives
        return load_normatives("spb")

    def test_intra_quarter_override_applies(self, spb):
        from urban_model.core.forward import compute_tep_for_kit
        from urban_model.models import CalculationOptions, Site
        site = Site(area_m2=50_000)
        # По умолчанию 10% → 5000 м²; override 20% → 10000 м²
        opts_default = CalculationOptions(floors=12, planning_doc=True)
        opts_override = CalculationOptions(
            floors=12, planning_doc=True,
            driveways_intra_share_override=0.20,
        )
        r_default = compute_tep_for_kit(1.0, site, opts_default, spb)
        r_override = compute_tep_for_kit(1.0, site, opts_override, spb)
        assert abs(r_default.driveways_intra_quarter_area.value - 5000) < 1
        assert abs(r_override.driveways_intra_quarter_area.value - 10000) < 1

    def test_lot_override_applies(self, spb):
        from urban_model.core.forward import compute_tep_for_kit
        from urban_model.models import CalculationOptions, Site
        site = Site(area_m2=50_000)
        opts_default = CalculationOptions(floors=12, planning_doc=True)
        opts_override = CalculationOptions(
            floors=12, planning_doc=True,
            driveways_lot_share_override=0.50,  # вместо 1.20 норматив
        )
        r_default = compute_tep_for_kit(1.0, site, opts_default, spb)
        r_override = compute_tep_for_kit(1.0, site, opts_override, spb)
        # При меньшей доле проездов на ЗУ — проездов меньше
        assert r_override.driveways_housing_lot_area.value < r_default.driveways_housing_lot_area.value


# ---------------------------------------------------------------------------
# ParkingConfig validation tolerance fix
# ---------------------------------------------------------------------------

class TestParkingValidationTolerance:
    def test_floating_point_sum_within_tolerance(self):
        """Сумма с микроскопической fp-ошибкой не должна падать с ValueError."""
        from urban_model.models.parking import ParkingConfig
        # Имитируем состояние, в которое попадает Optuna: суммы ≠ 1 на ~1e-7
        cfg = ParkingConfig(
            mode="custom",
            open_share=0.234567,
            multilevel_share=0.345678,
            underground_share=1.0 - 0.234567 - 0.345678 + 1e-9,
            multilevel_levels=3,
        )
        # Должно нормализоваться, не упасть
        assert abs(cfg.open_share + cfg.multilevel_share + cfg.underground_share - 1.0) < 1e-9

    def test_clearly_wrong_sum_still_rejects(self):
        from urban_model.models.parking import ParkingConfig
        with pytest.raises(ValueError, match="ожидается 1.0"):
            ParkingConfig(
                mode="custom",
                open_share=0.5,
                multilevel_share=0.5,
                underground_share=0.5,  # сумма 1.5 — недопустимо
            )
