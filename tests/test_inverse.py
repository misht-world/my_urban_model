"""Интеграционные тесты обратного расчёта."""

from __future__ import annotations

import pytest

from urban_model.core.forward import compute_tep_for_kit
from urban_model.models import CalculationOptions, Site
from urban_model.modes.inverse import solve_max_kit
from urban_model.normatives import load_normatives


@pytest.fixture(scope="module")
def spb():
    return load_normatives("spb")


class TestForward:
    def test_density_check_kicks_in_for_huge_kit(self, spb):
        # Берём КИТ на верхней границе (для маленького квартала плотность зашкалит)
        site = Site(area_m2=10_000)
        opts = CalculationOptions(floors=20, planning_doc=True)
        res = compute_tep_for_kit(2.4, site, opts, spb)
        # ratio 0.75 → S_квартир = 2.4*10000*0.75=18000, нас. по 20 = 900 → 900 чел/га
        assert res.density_chel_per_ga.status.value == "error"
        assert any("Плотность" in w for w in res.warnings)

    def test_balance_negative_for_huge_kit(self, spb):
        site = Site(area_m2=10_000)
        opts = CalculationOptions(floors=10)
        res = compute_tep_for_kit(2.5, site, opts, spb)
        assert not res.balance.is_feasible
        assert res.balance.surplus < 0

    def test_zero_population_when_kit_zero(self, spb):
        site = Site(area_m2=10_000)
        opts = CalculationOptions(floors=10)
        res = compute_tep_for_kit(0.0, site, opts, spb)
        assert res.population.value == 0
        assert res.kindergarten_places_required.value == 0


class TestInverse:
    def test_returns_kit_in_range(self, spb):
        site = Site(area_m2=20_000)
        opts = CalculationOptions(floors=10, planning_doc=True)
        res = solve_max_kit(site, opts, spb)
        assert 0 < res.kit.value <= 2.5

    def test_higher_quartal_density_gives_more_apartments(self, spb):
        site_small = Site(area_m2=20_000)
        site_big = Site(area_m2=100_000)
        opts = CalculationOptions(floors=10, planning_doc=True)
        r_small = solve_max_kit(site_small, opts, spb)
        r_big = solve_max_kit(site_big, opts, spb)
        # На большем квартале можно достичь большей плотности (block_density)
        # и, соответственно, большей суммарной площади квартир.
        assert r_big.apartments_area.value >= r_small.apartments_area.value

    def test_no_ppt_constrains_kit_pzz(self, spb):
        """Без ДПТ КИТ_max=1.4 (по ПЗЗ). Норматив применяется к КИТ ПЗЗ
        (apt/lot). При типичных параметрах модели КИТ ПЗЗ при минимальной
        плотности уже > 1.4 → solve_max_kit вернёт infeasible-результат.
        """
        site = Site(area_m2=200_000)
        r_yes = solve_max_kit(site, CalculationOptions(planning_doc=True), spb)
        r_no = solve_max_kit(site, CalculationOptions(planning_doc=False), spb)
        assert r_no.kit_normative_max.value == 1.4
        assert r_yes.kit_normative_max.value == 2.5
        # Без ДПТ результат либо полностью OK (feasible баланс + KIT ≤ 1.4),
        # либо помечен неуспешным (infeasible баланс ИЛИ kit.status=ERROR).
        # v0.9.16: balance.is_feasible теперь может быть True даже при
        # KIT > 1.4 (surplus засчитывается в зелень). Полная проверка
        # должна учитывать ОБА условия — баланс И статус КИТ.
        from urban_model.models.result import Status
        fully_ok = (
            r_no.balance.is_feasible
            and r_no.kit.status != Status.ERROR
        )
        if fully_ok:
            assert r_no.kit.value <= 1.4 + 1e-3

    def test_limiting_factor_set(self, spb):
        site = Site(area_m2=20_000)
        res = solve_max_kit(site, CalculationOptions(), spb)
        assert res.limiting_factor is not None
        assert len(res.limiting_factor) > 0

    def test_disabling_school_relaxes_constraints(self, spb):
        """Без СОШ освобождается территория → можно достичь большей
        квартальной плотности (block_density), даже если КИТ ПЗЗ
        (apt/lot) при этом сохраняется на том же значении (он зависит
        от floors/parking, не от плотности квартала)."""
        site = Site(area_m2=30_000)
        with_school = solve_max_kit(
            site, CalculationOptions(include_school=True), spb
        )
        without_school = solve_max_kit(
            site, CalculationOptions(include_school=False), spb
        )
        # Сравниваем по block_density — это и есть «насколько плотно квартал застроен»
        assert without_school.block_density.value >= with_school.block_density.value

    def test_balance_feasible_at_solution(self, spb):
        site = Site(area_m2=50_000)
        res = solve_max_kit(site, CalculationOptions(), spb)
        # либо подобран КИТ с положительным балансом,
        # либо помечено, что даже минимальный не проходит
        assert res.balance.is_feasible or "минимальный" in (res.limiting_factor or "")

    def test_audit_trail_in_fields(self, spb):
        """Каждое ключевое поле несёт source/formula — это требование ТЗ."""
        site = Site(area_m2=30_000)
        res = solve_max_kit(site, CalculationOptions(), spb)
        assert res.population.source is not None
        assert res.population.formula is not None
        assert res.znop_per_person.formula is not None


# v0.10.7: на крупном участке КИТ ограничивает норматив плотности 450 чел/га,
# а не территория — limiting_factor должен это явно сообщать (большой «резерв»
# = высвобожденная плотностью земля, уходит в озеленение).

def test_density_norm_identified_as_limiting_factor():
    from urban_model import solve_max_kit
    from urban_model.models import CalculationOptions, Site
    from urban_model.models.parking import ParkingConfig
    from urban_model.normatives import load_normatives
    n = load_normatives("spb")
    r = solve_max_kit(
        Site(area_m2=500_000),
        CalculationOptions(floors=12, planning_doc=True, parking=ParkingConfig(mode="all_open")),
        n,
    )
    # плотность у норматива
    assert r.density_chel_per_ga.value >= r.density_chel_per_ga.normative - 1.0
    # резерв заметный
    assert r.balance.surplus > 500_000 * 0.005
    # limiting_factor сообщает про плотность
    assert "плотност" in (r.limiting_factor or "").lower()


def test_small_site_not_density_limited():
    """На малом участке плотность НЕ у норматива → density-сообщение не выводится."""
    from urban_model import solve_max_kit
    from urban_model.models import CalculationOptions, Site
    from urban_model.normatives import load_normatives
    n = load_normatives("spb")
    r = solve_max_kit(Site(area_m2=50_000), CalculationOptions(floors=12, planning_doc=True), n)
    assert r.density_chel_per_ga.value < r.density_chel_per_ga.normative
    assert "плотност" not in (r.limiting_factor or "").lower()


def test_no_stale_density_warning_with_builtin_doo():
    """v0.10.11: при встроенном ДОО (вычитает GFA) итоговая плотность ≤ 450,
    и устаревшего предупреждения «плотность > норматива» быть не должно."""
    from urban_model import solve_max_kit
    from urban_model.models import CalculationOptions, KindergartenSpec, SchoolSpec, Site
    from urban_model.normatives import load_normatives
    n = load_normatives("spb")
    r = solve_max_kit(
        Site(area_m2=50_000),
        CalculationOptions(
            floors=12, planning_doc=True,
            kindergarten=KindergartenSpec(building_type="built_in"),
            school=SchoolSpec(only_demand=True),
        ),
        n,
    )
    assert r.density_chel_per_ga.value <= r.density_chel_per_ga.normative + 0.5
    assert r.density_chel_per_ga.status.value == "ok"
    assert not any("DENSITY_ABOVE" in w for w in r.warnings)
