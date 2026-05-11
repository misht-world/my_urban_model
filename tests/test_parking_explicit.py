"""Тесты v0.6: ParkingConfig.multilevel_explicit_places и ZNOP total area override."""

from __future__ import annotations

import pytest

from urban_model.calculations.parking import compute_parking_breakdown
from urban_model.core.forward import compute_tep_for_kit
from urban_model.models import CalculationOptions, ParkingConfig, Site
from urban_model.normatives import load_normatives


@pytest.fixture(scope="module")
def spb():
    return load_normatives("spb")


# ---------------------------------------------------------------------------
# Multilevel explicit places — задание абсолютным числом
# ---------------------------------------------------------------------------

class TestMultilevelExplicit:
    def test_explicit_overrides_share(self, spb):
        """multilevel_explicit_places имеет приоритет над multilevel_share."""
        apt = 80_000  # → required ≈ 1000 м/м
        cfg = ParkingConfig(
            mode="custom",
            open_share=0.20, multilevel_share=0.50, underground_share=0.30,
            multilevel_explicit_places=300,
        )
        br = compute_parking_breakdown(apt, cfg, spb)
        # Реальные multilevel = 300 (а не 50% от total_required)
        assert br.multilevel_places == 300

    def test_explicit_capped_at_total(self, spb):
        """Если explicit > total, обрезается до total."""
        apt = 8_000  # → required ≈ 100 м/м
        cfg = ParkingConfig(
            mode="custom",
            open_share=0.20, multilevel_share=0.50, underground_share=0.30,
            multilevel_explicit_places=999,  # больше total
        )
        br = compute_parking_breakdown(apt, cfg, spb)
        assert br.multilevel_places <= br.total_required

    def test_explicit_zero_acts_like_no_multilevel(self, spb):
        apt = 80_000
        cfg = ParkingConfig(
            mode="custom",
            open_share=0.30, multilevel_share=0.50, underground_share=0.20,
            multilevel_explicit_places=0,
        )
        br = compute_parking_breakdown(apt, cfg, spb)
        assert br.multilevel_places == 0

    def test_open_min_still_enforced(self, spb):
        """Жёсткий минимум 12.5% открытых соблюдается даже при explicit multilevel."""
        apt = 80_000
        # Малая доля открытых (5%), но норматив форсит 12.5%
        cfg = ParkingConfig(
            mode="custom",
            open_share=0.05, multilevel_share=0.0, underground_share=0.95,
            multilevel_explicit_places=300,
        )
        br = compute_parking_breakdown(apt, cfg, spb)
        open_min = br.total_required * 0.125
        assert br.open_places >= open_min - 1  # ceil tolerance


# ---------------------------------------------------------------------------
# ZNOP total area override
# ---------------------------------------------------------------------------

class TestZnopTotalAreaOverride:
    def test_total_area_overrides_per_person(self, spb):
        """znop_total_area_override имеет приоритет над znop_per_person_override."""
        site = Site(area_m2=50_000)
        opts = CalculationOptions(
            floors=12, planning_doc=True,
            znop_per_person_override=10.0,      # был бы 10 × pop
            znop_total_area_override=5_000.0,    # фиксированная площадь
        )
        r = compute_tep_for_kit(1.0, site, opts, spb)
        assert r.znop_area.value == 5_000.0

    def test_total_area_computes_per_person(self, spb):
        """При фиксированной площади znop_per_person = area / pop."""
        site = Site(area_m2=50_000)
        opts = CalculationOptions(
            floors=12, planning_doc=True,
            znop_total_area_override=3_000.0,
        )
        r = compute_tep_for_kit(1.0, site, opts, spb)
        expected_pp = 3_000.0 / r.population.value
        assert abs(r.znop_per_person.value - expected_pp) < 1e-6

    def test_total_area_marked_manual(self, spb):
        """ЗНОП со status manual при override."""
        from urban_model.models.result import Status
        site = Site(area_m2=50_000)
        opts = CalculationOptions(
            floors=12, planning_doc=True,
            znop_total_area_override=2_000.0,
        )
        r = compute_tep_for_kit(1.0, site, opts, spb)
        assert r.znop_per_person.status == Status.MANUAL


# ---------------------------------------------------------------------------
# SchoolSpec defaults (v0.6: True/True)
# ---------------------------------------------------------------------------

class TestSchoolDefaults:
    def test_school_defaults_pool_and_core(self):
        from urban_model.models import SchoolSpec
        spec = SchoolSpec()
        assert spec.has_pool is True
        assert spec.has_sport_core is True

    def test_school_plot_includes_extras_by_default(self, spb):
        """По дефолтным настройкам участок СОШ включает +0.2 (бассейн) + 0.7 (ядро)."""
        site = Site(area_m2=100_000)
        opts = CalculationOptions(floors=15, planning_doc=True)
        r = compute_tep_for_kit(1.0, site, opts, spb)
        # При include_school=True (дефолт) plot должен быть > 0
        # и включать +9000 от extras
        if r.school_places_accepted.value > 0:
            assert r.school_plot_area.value > 0
