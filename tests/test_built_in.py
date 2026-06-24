"""Тесты ВПП как самостоятельной сущности (BuiltInArea, v0.2)."""

from __future__ import annotations

import math

import pytest

from urban_model import solve_max_kit, verify_kit
from urban_model.models import BuiltInArea, CalculationOptions, Site
from urban_model.models.social import KindergartenSpec
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
        # include_add_education=False: изолируем формулу ВПП от доп. образования
        # (встроенное доп. обр. также вычитает здание из GFA, v0.12.15).
        res_no = verify_kit(
            1.5, site_5ga,
            CalculationOptions(floors=12, include_add_education=False), spb,
        )
        res_yes = verify_kit(
            1.5, site_5ga,
            CalculationOptions(
                floors=12, include_add_education=False,
                built_in=BuiltInArea(area_m2=5_000),
            ),
            spb,
        )
        assert res_no.apartments_area.value == pytest.approx(56_250)
        assert res_yes.apartments_area.value == pytest.approx(52_500)
        assert res_yes.built_in_area.value == 5_000

    def test_legacy_vpp_share_still_works(self, spb, site_5ga):
        # built_in=None → использует vpp_share как раньше
        res = verify_kit(
            1.5, site_5ga,
            CalculationOptions(floors=12, vpp_share=0.1, include_add_education=False),
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
                include_add_education=False,
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
    """v0.7.1.1: единый коэффициент 64 м²/м.м. для всех ВПП."""

    def test_parking_for_4_4_shop(self, spb, site_5ga):
        # ВПП 5000 м² / 64 = 79 м/м (ceil)
        res = verify_kit(
            1.5, site_5ga,
            CalculationOptions(
                floors=12, built_in=BuiltInArea(area_m2=5_000, vri_code="4.4"),
            ),
            spb,
        )
        assert res.built_in_parking_places.value == math.ceil(5000 / 64)

    def test_parking_for_4_6_cafe(self, spb, site_5ga):
        # ВПП 1000 м² / 64 = 16 м/м (ceil)
        res = verify_kit(
            1.5, site_5ga,
            CalculationOptions(
                floors=12, built_in=BuiltInArea(area_m2=1_000, vri_code="4.6"),
            ),
            spb,
        )
        assert res.built_in_parking_places.value == math.ceil(1000 / 64)

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
        и позволяет повысить квартальную плотность. Это корректное поведение."""
        site = Site(area_m2=100_000)
        density_no = solve_max_kit(
            site, CalculationOptions(floors=15, planning_doc=True), spb
        ).block_density.value
        density_yes = solve_max_kit(
            site,
            CalculationOptions(
                floors=15, planning_doc=True,
                built_in=BuiltInArea(area_m2=10_000, vri_code="4.4"),
            ),
            spb,
        ).block_density.value
        # Сравниваем по block_density — внутренней плотности квартала.
        # КИТ ПЗЗ (apt/lot) от ВПП меняется иначе и не годится для этого инварианта.
        assert density_yes >= density_no - 1e-6


# ---------------------------------------------------------------------------
# Встроенно-пристроенный ДОО (built_in kindergarten, v0.6.5)
# ---------------------------------------------------------------------------

class TestBuiltInKindergarten:
    """Проверяем, что встроенно-пристроенный ДОО корректно:
    - использует норматив ЗУ 24 м²/место (ПЗЗ СПб)
    - вычитает площадь здания ДОО из жилой GFA
    - уменьшает apartments_area и население по сравнению с detached.
    """

    def test_plot_area_per_place_is_24(self, spb, site_5ga):
        """Площадь ЗУ встроенного ДОО = 24 м²/место (не 40/45)."""
        res = verify_kit(
            1.5, site_5ga,
            CalculationOptions(
                floors=12,
                kindergarten=KindergartenSpec(building_type="built_in"),
            ),
            spb,
        )
        places = res.kindergarten_places_accepted.value or 0
        if places > 0:
            # plot_area дискретный: ровно 24 м²/место × число мест по всем объектам.
            # places (kindergarten_places_accepted) — округлённый required;
            # фактическая сумма мест может отличаться (см. buckets), поэтому
            # сверяем через сумму buckets.
            import re
            formula = res.kindergarten_places_accepted.formula or ""
            m = re.search(r'\[([^\]]+)\]', formula)
            assert m, f"Не удалось получить buckets из formula: {formula}"
            buckets = [int(x.strip()) for x in m.group(1).split(",") if x.strip()]
            expected = 24 * sum(buckets)
            assert res.kindergarten_plot_area.value == expected

    def test_plot_area_less_than_detached(self, spb, site_5ga):
        """ЗУ встроенного ДОО меньше, чем у отдельно стоящего."""
        res_det = verify_kit(
            1.5, site_5ga,
            CalculationOptions(floors=12),  # detached по умолчанию
            spb,
        )
        res_bi = verify_kit(
            1.5, site_5ga,
            CalculationOptions(
                floors=12,
                kindergarten=KindergartenSpec(building_type="built_in"),
            ),
            spb,
        )
        assert res_bi.kindergarten_plot_area.value < res_det.kindergarten_plot_area.value

    def test_apartments_area_reduced(self, spb, site_5ga):
        """apartments_area при built_in ДОО меньше, чем при detached
        (здание ДОО вычитается из жилой GFA)."""
        res_det = verify_kit(1.5, site_5ga, CalculationOptions(floors=12), spb)
        res_bi = verify_kit(
            1.5, site_5ga,
            CalculationOptions(
                floors=12,
                kindergarten=KindergartenSpec(building_type="built_in"),
            ),
            spb,
        )
        assert res_bi.apartments_area.value < res_det.apartments_area.value

    def test_apartments_area_exact_formula(self, spb, site_5ga):
        """Точная формула: apartments_area = (GFA − kg_bld_total) × apt_ratio."""
        res = verify_kit(
            1.5, site_5ga,
            CalculationOptions(
                floors=12,
                include_add_education=False,  # изолируем от встроенного доп. обр.
                kindergarten=KindergartenSpec(building_type="built_in"),
            ),
            spb,
        )
        apt_ratio = spb.resolve("building_params.apartments_to_gfa_ratio")
        gfa = res.gfa.value
        kg_bld = res.kindergarten_building_area.value
        expected = (gfa - kg_bld) * apt_ratio
        assert res.apartments_area.value == pytest.approx(expected, rel=1e-4)

    def test_builtin_kg_with_vpp_both_subtract_from_gfa(self, spb, site_5ga):
        """built_in ДОО + ВПП: оба вычитаются из GFA жилого дома."""
        res = verify_kit(
            1.5, site_5ga,
            CalculationOptions(
                floors=12,
                include_add_education=False,  # изолируем от встроенного доп. обр.
                kindergarten=KindergartenSpec(building_type="built_in"),
                built_in=BuiltInArea(area_m2=2_000, vri_code="4.4"),
            ),
            spb,
        )
        apt_ratio = spb.resolve("building_params.apartments_to_gfa_ratio")
        gfa = res.gfa.value
        vpp = res.built_in_area.value
        kg_bld = res.kindergarten_building_area.value
        # apt_area = (GFA − VPP − KG_bld) × apt_ratio
        expected = (gfa - vpp - kg_bld) * apt_ratio
        assert res.apartments_area.value == pytest.approx(expected, rel=1e-3)
        assert vpp == 2_000
        assert kg_bld > 0

    def test_inverse_converges_with_builtin_kg(self, spb, site_5ga):
        """solve_max_kit сходится при встроенно-пристроенном ДОО."""
        res = solve_max_kit(
            site_5ga,
            CalculationOptions(
                floors=12, planning_doc=True,
                kindergarten=KindergartenSpec(building_type="built_in"),
            ),
            spb,
        )
        assert res.balance.is_feasible
        assert res.kit.value > 0


# ---------------------------------------------------------------------------
# Дифференцированные предупреждения по вместимости ДОО (v0.6.5)
# ---------------------------------------------------------------------------

class TestKindergartenCapacityWarnings:
    """v0.6.5: предупреждения зависят от типа ДОО и значения вместимости."""

    def test_warn_below_120_unconditional(self, spb):
        """Вместимость < 120 — категорически невозможный ДОО (любой тип)."""
        site = Site(area_m2=5_000)  # маленький квартал → мало мест
        res = verify_kit(0.5, site, CalculationOptions(floors=8), spb)
        # Должно быть < 120
        if (res.kindergarten_places_accepted.value or 0) > 0:
            assert res.kindergarten_places_accepted.value < 120
            kg_warns = [w for w in res.warnings if "ДОО" in w]
            assert any("меньше минимальной" in w for w in kg_warns)

    def test_warn_detached_120_to_159_suggests_builtin(self, spb):
        """detached с вместимостью 120≤c<160 → рекомендовать built_in."""
        site = Site(area_m2=50_000)
        res = verify_kit(
            1.5, site,
            CalculationOptions(
                floors=12,
                kindergarten=KindergartenSpec(
                    building_type="detached",
                    num_objects=1, capacity_per_object=140,
                ),
            ),
            spb,
        )
        kg_warns = [w for w in res.warnings if "ДОО" in w]
        assert any("встроенно-пристроенный" in w for w in kg_warns)

    def test_no_warn_for_allowed_capacity(self, spb):
        """Вместимость 200 в списке allowed → нет warning «не входит»."""
        site = Site(area_m2=50_000)
        res = verify_kit(
            1.5, site,
            CalculationOptions(
                floors=12,
                kindergarten=KindergartenSpec(
                    building_type="detached",
                    num_objects=1, capacity_per_object=200,
                ),
            ),
            spb,
        )
        not_in_list = [w for w in res.warnings if "ДОО" in w and "не входит" in w]
        assert len(not_in_list) == 0

    def test_warn_not_in_allowed_with_nearest(self, spb):
        """Вместимость 175 не в списке → warning с «меньше: 170; больше: 180»."""
        site = Site(area_m2=50_000)
        res = verify_kit(
            1.5, site,
            CalculationOptions(
                floors=12,
                kindergarten=KindergartenSpec(
                    building_type="detached",
                    num_objects=1, capacity_per_object=175,
                ),
            ),
            spb,
        )
        not_in_list = [w for w in res.warnings if "ДОО" in w and "не входит" in w]
        assert len(not_in_list) > 0
        assert "170" in not_in_list[0]
        assert "180" in not_in_list[0]

    def test_allowed_capacities_list_loaded(self, spb):
        """Проверка, что список typovyh capacities ДОО загружается из YAML."""
        caps = spb.resolve("social_objects.kindergarten.allowed_capacities")
        assert 90 in caps
        assert 350 in caps
        assert 175 not in caps  # промежуточное
