"""Тесты очерёдности застройки (v0.15.0)."""

from __future__ import annotations

import pytest

from urban_model import solve_max_kit
from urban_model.models import CalculationOptions, Site
from urban_model.models.phasing import PhasingSpec
from urban_model.models.social import KindergartenSpec
from urban_model.normatives import load_normatives


@pytest.fixture(scope="module")
def norms():
    return load_normatives("spb")


class TestPhasingSpec:
    def test_shares_normalized(self):
        spec = PhasingSpec(shares=[40, 60])
        assert spec.shares == [0.4, 0.6]

    def test_rejects_wrong_count(self):
        with pytest.raises(ValueError):
            PhasingSpec(shares=[1.0])
        with pytest.raises(ValueError):
            PhasingSpec(shares=[0.2] * 5)

    def test_rejects_nonpositive(self):
        with pytest.raises(ValueError):
            PhasingSpec(shares=[0.5, 0.0])


class TestPhasingResult:
    @pytest.fixture(scope="class")
    def result(self, norms):
        o = CalculationOptions(floors=12, planning_doc=True,
                               phasing=PhasingSpec(shares=[0.3, 0.3, 0.4]))
        return solve_max_kit(Site(area_m2=200_000), o, norms)

    def test_none_without_spec(self, norms):
        r = solve_max_kit(Site(area_m2=100_000),
                          CalculationOptions(floors=12, planning_doc=True), norms)
        assert r.phasing is None

    def test_stage_sums(self, result):
        ph = result.phasing
        assert ph is not None and len(ph.stages) == 3
        # квартиры/площадь по этапам сходятся с итогом
        assert sum(s.apartments_m2 for s in ph.stages) == pytest.approx(
            result.apartments_area.value, rel=1e-6)
        assert sum(s.area_m2 for s in ph.stages) == pytest.approx(
            result.balance.site_area, rel=1e-6)
        # население накопительно монотонно, финал = итог
        cums = [s.population_cum for s in ph.stages]
        assert cums == sorted(cums)
        assert cums[-1] == pytest.approx(result.population.value, rel=1e-6)

    def test_all_buckets_assigned(self, result):
        """Все корпуса ДОО/СОШ размещены; финальная обеспеченность = итоговой."""
        ph = result.phasing
        kg_total = sum(sum(s.kg_buckets) for s in ph.stages)
        assert kg_total == int(result.kindergarten_places_accepted.value)
        sch_total = sum(sum(s.school_buckets) for s in ph.stages)
        assert sch_total == int(result.school_places_accepted.value)
        assert ph.stages[-1].kg_provided_cum == kg_total
        assert ph.stages[-1].school_provided_cum == sch_total

    def test_auto_assignment_no_deficit(self, result):
        """При авто-подборе соцобъектов раскладка по потребности без дефицитов."""
        assert all(s.is_ok for s in result.phasing.stages)

    def test_engineering_distributed(self, result):
        """Инженерные объекты разложены полностью; единичные — в 1-й очереди."""
        ph = result.phasing
        eng_total: dict[str, int] = {}
        for s in ph.stages:
            for lbl, cnt in s.engineering_stage.items():
                eng_total[lbl] = eng_total.get(lbl, 0) + cnt
        model_total = {o.label: o.count for o in result.engineering.objects if o.count > 0}
        assert eng_total == model_total
        for o in result.engineering.objects:
            if o.count == 1:
                assert ph.stages[0].engineering_stage.get(o.label) == 1


def test_deficit_when_social_undersized(norms):
    """Недостаточная вместимость ДОО (ручной override) → дефицит на этапе."""
    o = CalculationOptions(
        floors=12, planning_doc=True,
        kindergarten=KindergartenSpec(num_objects=1, capacity_per_object=100),
        phasing=PhasingSpec(shares=[0.5, 0.5]),
    )
    r = solve_max_kit(Site(area_m2=200_000), o, norms)
    ph = r.phasing
    assert any(not s.is_ok for s in ph.stages)
    assert any("PHASE_SOC_DEFICIT" in w for w in r.warnings)


def test_phasing_block_in_variant_tables(norms):
    from urban_model.export.variant_tables import build_variant_table_blocks
    o = CalculationOptions(floors=12, planning_doc=True,
                           phasing=PhasingSpec(shares=[0.5, 0.5]))
    r = solve_max_kit(Site(area_m2=100_000), o, norms)
    blocks = build_variant_table_blocks(r)
    ph = next((b for b in blocks if b.key == "phasing"), None)
    assert ph is not None and len(ph.rows) == 2
    assert ph.summary
