"""Тесты ВПП как самостоятельной сущности (BuiltInArea, v0.2)."""

from __future__ import annotations

import math

import pytest

from urban_model import solve_max_kit, verify_kit
from urban_model.models import BuiltInArea, CalculationOptions, Site
from urban_model.normatives import load_normatives


@pytest.fixture(scope="module")
def spb():
    return load_normatives("spb")


@pytest.fixture
def site_5ga():
    return Site(area_m2=50_000, name="5 га")


# ---------------------------------------------------------------------------
# Модель BuiltInArea
# ---------------------------------------------------------------------------

class TestBuiltInAreaModel:
    def test_default_vri_is_4_4(self):
        bi = BuiltInArea(area_m2=1000)
        assert bi.vri_code == "4.4"

    def test_explicit_vri(self):
        bi = BuiltInArea(area_m2=500, vri_code="3.6", label="ДК")
        assert bi.vri_code == "3.6"
        assert bi.label == "ДК"

    def test_zero_area_rejected(self):
        with pytest.raises(ValueError):
            BuiltInArea(area_m2=0)

    def test_negative_area_rejected(self):
        with pytest.raises(ValueError):
            BuiltInArea(area_m2=-100)


# ---------------------------------------------------------------------------
# Влияние на расчёт квартир
# ---------------------------------------------------------------------------

class TestBuiltInImpactOnApartments:
    def test_built_in_subtracts_from_gfa(self, spb, site_5ga):
        # КИТ=1.5, GFA = 75 000. Без ВПП: квартиры = 75000 × 0.75 = 56 250
        # С ВПП 5 000: квартиры = (75000 − 5000) × 0.75 = 52 500
        res_no = verify_kit(1.5, site_5ga, CalculationOptions(floors=12), spb)
        res_yes = verify_kit(
            1.5, site_5ga,
            CalculationOptions(floors=12, built_in=BuiltInArea(area_m2=5_000)),
            spb,
        )
        assert res_no.apartments_area.value == pytest.approx(56_250)
        assert res_yes.apartments_area.value == pytest.approx(52_500)
        assert res_yes.built_in_area.value == 5_000

    def test_legacy_vpp_share_still_works(self, spb, site_5ga):
        # built_in=None → использует vpp_share как раньше
        res = verify_kit(
            1.5, site_5ga,
            CalculationOptions(floors=12, vpp_share=0.1),
            spb,
        )
        # квартиры = 75000 × 0.9 × 0.75 = 50 625
        assert res.apartments_area.value == pytest.approx(50_625)
        assert res.built_in_area.value == 0

    def test_built_in_overrides_vpp_share(self, spb, site_5ga):
        """Если задано BuiltInArea, vpp_share игнорируется."""
        res = verify_kit(
            1.5, site_5ga,
            CalculationOptions(
                floors=12,
                built_in=BuiltInArea(area_m2=5_000),
                vpp_share=0.3,  # должен быть проигнорирован
            ),
            spb,
        )
        # Должно быть как при built_in, не учитывая vpp_share
        assert res.apartments_area.value == pytest.approx(52_500)


# ---------------------------------------------------------------------------
# Парковки ВПП
# ---------------------------------------------------------------------------

class TestBuiltInParking:
    def test_parking_for_4_4_shop(self, spb, site_5ga):
        # ВРИ 4.4: 50 м²/м.м.; ВПП 5000 м² → 100 м/м
        res = verify_kit(
            1.5, site_5ga,
            CalculationOptions(
                floors=12, built_in=BuiltInArea(area_m2=5_000, vri_code="4.4"),
            ),
            spb,
        )
        assert res.built_in_parking_places.value == 100

    def test_parking_for_4_6_cafe(self, spb, site_5ga):
        # ВРИ 4.6: 25 м²/м.м.; ВПП 1000 м² → 40 м/м
        res = verify_kit(
            1.5, site_5ga,
            CalculationOptions(
                floors=12, built_in=BuiltInArea(area_m2=1_000, vri_code="4.6"),
            ),
            spb,
        )
        assert res.built_in_parking_places.value == 40

    def test_built_in_parking_added_to_total(self, spb, site_5ga):
        """Парковка ВПП суммируется с парковкой жилья в общий total."""
        res_no = verify_kit(1.5, site_5ga, CalculationOptions(floors=12), spb)
        res_yes = verify_kit(
            1.5, site_5ga,
            CalculationOptions(
                floors=12, built_in=BuiltInArea(area_m2=5_000, vri_code="4.4"),
            ),
            spb,
        )
        # Жильё уменьшилось → парковка жилья снизилась, но добавилось +100 ВПП
        # housing_no = 56250/80 = 703, total_no = 703
        # housing_yes = 52500/80 = 656.25 → 657, vpp_yes = 100 → total_yes = 757
        assert res_yes.parking_required_places.value > res_no.parking_required_places.value

    def test_parking_source_present(self, spb, site_5ga):
        """Источник нормы ВРИ-4.4 проставляется в поле."""
        res = verify_kit(
            1.5, site_5ga,
            CalculationOptions(built_in=BuiltInArea(area_m2=2_000, vri_code="4.4")),
            spb,
        )
        assert res.built_in_parking_places.source is not None
        assert "ПЗЗ" in res.built_in_parking_places.source


# ---------------------------------------------------------------------------
# Озеленение ВПП
# ---------------------------------------------------------------------------

class TestBuiltInGreening:
    def test_greening_15_percent(self, spb, site_5ga):
        # 15 м² на 100 м² ВПП → коэффициент 0.15
        res = verify_kit(
            1.5, site_5ga,
            CalculationOptions(built_in=BuiltInArea(area_m2=2_000)),
            spb,
        )
        assert res.built_in_greening_area.value == pytest.approx(2_000 * 0.15)

    def test_no_greening_without_built_in(self, spb, site_5ga):
        res = verify_kit(1.5, site_5ga, CalculationOptions(), spb)
        assert res.built_in_greening_area.value == 0


# ---------------------------------------------------------------------------
# Влияние на solve_max_kit (ВПП ужесточает баланс → КИТ может снизиться)
# ---------------------------------------------------------------------------

class TestBuiltInImpactOnInverse:
    def test_solve_max_kit_with_vpp_runs(self, spb):
        """С ВПП обратный расчёт сходится и даёт валидный КИТ ≤ нормативного потолка."""
        site = Site(area_m2=100_000)
        res = solve_max_kit(
            site,
            CalculationOptions(
                floors=15, planning_doc=True,
                built_in=BuiltInArea(area_m2=10_000, vri_code="4.4"),
            ),
            spb,
        )
        assert 0 < res.kit.value <= 2.5
        # ВПП-поля заполнены
        assert res.built_in_area.value == 10_000
        assert res.built_in_vri_code == "4.4"
        assert res.built_in_parking_places.value > 0

    def test_vpp_can_relax_balance_when_social_is_bottleneck(self, spb):
        """Когда ограничитель — соцблок (ДОО/СОШ), ВПП «разбавляет» население
        и позволяет повысить КИТ. Это корректное поведение."""
        site = Site(area_m2=100_000)
        kit_no = solve_max_kit(
            site, CalculationOptions(floors=15, planning_doc=True), spb
        ).kit.value
        kit_yes = solve_max_kit(
            site,
            CalculationOptions(
                floors=15, planning_doc=True,
                built_in=BuiltInArea(area_m2=10_000, vri_code="4.4"),
            ),
            spb,
        ).kit.value
        # При этом параметре сценария соцблок — ограничитель,
        # поэтому КИТ с ВПП ≥ КИТ без ВПП.
        assert kit_yes >= kit_no - 1e-6
