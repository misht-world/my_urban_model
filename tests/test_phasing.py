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
            PhasingSpec(shares=[0.05] * 21)

    def test_rejects_nonpositive(self):
        with pytest.raises(ValueError):
            PhasingSpec(shares=[0.5, 0.0])

    def test_rejects_nan_inf(self):
        """v0.15.8: NaN/inf не проходят (nan<=0 ложно — нужен isfinite)."""
        with pytest.raises(ValueError):
            PhasingSpec(shares=[float("nan"), 0.5])
        with pytest.raises(ValueError):
            PhasingSpec(shares=[float("inf"), 0.5])
        with pytest.raises(ValueError):
            PhasingSpec(shares=["abc", 0.5])
        with pytest.raises(ValueError):
            PhasingSpec(shares=[-1.0, 2.0])


class TestPhasingResult:
    @pytest.fixture(scope="class")
    def result(self, norms):
        o = CalculationOptions(
            floors=12, planning_doc=True,
            phasing=PhasingSpec(mode="manual", shares=[0.3, 0.3, 0.4]))
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


class TestAutoMode:
    def test_auto_stages_follow_kg_buckets(self, norms):
        """Авто: число очередей = число корпусов ДОО; покрытие ≥95% на этапах."""
        o = CalculationOptions(floors=12, planning_doc=True,
                               phasing=PhasingSpec(mode="auto"))
        r = solve_max_kit(Site(area_m2=200_000), o, norms)
        ph = r.phasing
        assert ph.mode == "auto"
        import re
        m = re.search(r"\[([\d,\s]+)\]", r.kindergarten_places_accepted.formula)
        n_kg = len(m.group(1).split(","))
        assert len(ph.stages) == min(20, max(2, n_kg))
        for s in ph.stages:
            if s.kg_required_cum > 0:
                assert s.kg_provided_cum / s.kg_required_cum >= 0.95
        assert all(s.is_ok for s in ph.stages)

    def test_auto_shares_sum_to_one(self, norms):
        o = CalculationOptions(floors=12, planning_doc=True,
                               phasing=PhasingSpec(mode="auto"))
        r = solve_max_kit(Site(area_m2=200_000), o, norms)
        assert sum(s.share for s in r.phasing.stages) == pytest.approx(1.0)

    def test_auto_no_split_without_soc(self, norms):
        """v0.15.4: нет корпусов ДОО/СОШ (≥2) → авто НЕ делит (stages пусты,
        note объясняет), вместо прежних произвольных 50/50."""
        o = CalculationOptions(floors=9, planning_doc=True,
                               include_kindergarten=False, include_school=False,
                               phasing=PhasingSpec(mode="auto"))
        r = solve_max_kit(Site(area_m2=50_000), o, norms)
        ph = r.phasing
        assert ph is not None and ph.stages == []
        assert ph.note and "Деление на очереди не выполнено" in ph.note

    def test_auto_no_split_single_kg(self, norms):
        """Единственный корпус ДОО и единственная СОШ → не делим."""
        from urban_model.models.social import KindergartenSpec, SchoolSpec
        o = CalculationOptions(
            floors=9, planning_doc=True,
            kindergarten=KindergartenSpec(num_objects=1),
            school=SchoolSpec(num_objects=1),
            phasing=PhasingSpec(mode="auto"))
        r = solve_max_kit(Site(area_m2=100_000), o, norms)
        ph = r.phasing
        assert ph.stages == [] and ph.note

    def test_auto_single_kg_but_two_schools(self, norms):
        """1 ДОО, но ≥2 корпусов СОШ → деление по школам остаётся."""
        from urban_model.models.social import KindergartenSpec, SchoolSpec
        o = CalculationOptions(
            floors=12, planning_doc=True,
            kindergarten=KindergartenSpec(num_objects=1),
            school=SchoolSpec(num_objects=2),
            phasing=PhasingSpec(mode="auto"))
        r = solve_max_kit(Site(area_m2=200_000), o, norms)
        assert len(r.phasing.stages) == 2


class TestLots:
    def test_single_school_single_lot(self, norms):
        """Одна СОШ вбирает всю потребность → все очереди в лоте 1."""
        o = CalculationOptions(floors=12, planning_doc=True,
                               phasing=PhasingSpec(mode="auto"))
        r = solve_max_kit(Site(area_m2=200_000), o, norms)
        assert all(s.lot == 1 for s in r.phasing.stages)

    def test_second_school_opens_new_lot(self, norms):
        """Второй корпус СОШ открывает новый лот."""
        from urban_model.models.social import SchoolSpec
        o = CalculationOptions(
            floors=12, planning_doc=True,
            school=SchoolSpec(num_objects=2),
            kindergarten=KindergartenSpec(num_objects=4),
            phasing=PhasingSpec(mode="auto"))
        r = solve_max_kit(Site(area_m2=400_000), o, norms)
        lots = [s.lot for s in r.phasing.stages]
        assert max(lots) == 2
        assert lots == sorted(lots)          # лоты монотонны по очередям
        # новый лот начинается ровно в очереди со 2-м корпусом СОШ
        second_school_stage = next(
            s.index for s in r.phasing.stages[1:] if s.school_buckets)
        assert next(s.lot for s in r.phasing.stages
                    if s.index == second_school_stage) == 2

    def test_lots_block_and_no_lot_column(self, norms):
        """v0.15.9: колонки «Лот» в очередях нет; лоты — отдельным блоком."""
        from urban_model.export.variant_tables import build_variant_table_blocks
        from urban_model.models.social import SchoolSpec
        o = CalculationOptions(floors=12, planning_doc=True,
                               school=SchoolSpec(num_objects=2),
                               kindergarten=KindergartenSpec(num_objects=4),
                               phasing=PhasingSpec(mode="auto"))
        r = solve_max_kit(Site(area_m2=400_000), o, norms)
        blocks = build_variant_table_blocks(r)
        ph = next(b for b in blocks if b.key == "phasing")
        assert "Лот" not in ph.columns
        lots = next(b for b in blocks if b.key == "lots")
        assert "Лот" in lots.columns and "ДОО, мест" in lots.columns
        assert len(lots.rows) == 2


class TestEngineeringByLots:
    @pytest.fixture(scope="class")
    def result(self, norms):
        from urban_model.models.social import SchoolSpec
        o = CalculationOptions(
            floors=12, planning_doc=True,
            school=SchoolSpec(num_objects=2),
            kindergarten=KindergartenSpec(num_objects=4),
            phasing=PhasingSpec(mode="auto", engineering_by_lots=True))
        return solve_max_kit(Site(area_m2=400_000), o, norms)

    def test_lots_have_full_kits(self, result):
        """Каждый лот автономен: котельная и ОСПС в каждом."""
        ph = result.phasing
        assert len(ph.lots) == max(s.lot for s in ph.stages)
        for lp in ph.lots:
            assert any("Котельная" in lbl for lbl in lp.engineering)
            assert any("ОСПС" in lbl for lbl in lp.engineering)
            assert lp.eng_plot_total > 0

    def test_delta_note_present(self, result):
        assert result.phasing.eng_delta_note
        assert "по-лотовой" in result.phasing.eng_delta_note

    def test_lots_embedded_in_balance(self, result, norms):
        """v0.15.7: по-лотовая инженерка ВСТРОЕНА в баланс/экономику."""
        # встроенный агрегат == Σ комплектов лотов из таблицы
        lot_sum = sum(lp.eng_plot_total for lp in result.phasing.lots)
        assert result.engineering.plot_total_all == pytest.approx(lot_sum, abs=1.0)
        # объекты помечены лотами
        assert any("— лот" in o.label for o in result.engineering.objects)
        # квартир меньше, чем при единой квартальной схеме (цена автономности)
        from urban_model.models.social import SchoolSpec
        o_off = CalculationOptions(
            floors=12, planning_doc=True,
            school=SchoolSpec(num_objects=2),
            kindergarten=KindergartenSpec(num_objects=4),
            phasing=PhasingSpec(mode="auto", engineering_by_lots=False))
        r_off = solve_max_kit(Site(area_m2=400_000), o_off, norms)
        assert (result.apartments_area.value
                < r_off.apartments_area.value - 100)
        assert (result.balance.components["engineering_plot"]
                > r_off.balance.components["engineering_plot"])

    def test_lot_population_sums(self, result):
        ph = result.phasing
        assert sum(lp.population for lp in ph.lots) == pytest.approx(
            result.population.value, rel=1e-6)

    def test_off_by_default(self, norms):
        """Без галочки лоты-агрегаты есть, но БЕЗ инженерных комплектов."""
        o = CalculationOptions(floors=12, planning_doc=True,
                               phasing=PhasingSpec(mode="auto"))
        r = solve_max_kit(Site(area_m2=200_000), o, norms)
        assert r.phasing.lots  # агрегаты строятся всегда
        assert all(not lp.engineering for lp in r.phasing.lots)
        assert not any("— лот" in o2.label for o2 in r.engineering.objects)

    def test_block_in_variant_tables(self, result):
        from urban_model.export.variant_tables import build_variant_table_blocks
        pe = next((b for b in build_variant_table_blocks(result)
                   if b.key == "lots"), None)
        assert pe is not None
        assert "Лот" in pe.columns and "ЗУ инж., м²" in pe.columns
        assert len(pe.rows) == len(result.phasing.lots)

    def test_eng_distributed_within_lot(self, norms):
        """v0.15.9 (баг Михаила): комплект лота НЕ валится в 1-ю очередь —
        ТП распределяются по очередям лота по накопительному спросу."""
        o = CalculationOptions(
            floors=12, planning_doc=True,
            phasing=PhasingSpec(mode="manual", shares=[0.34, 0.33, 0.33],
                                engineering_by_lots=True))
        r = solve_max_kit(Site(area_m2=400_000), o, norms)
        ph = r.phasing
        # один лот (1 школа) на 3 очереди: ТП должны быть и в оч.2/3
        stages_with_eng = [s.index for s in ph.stages if s.engineering_stage]
        assert len(stages_with_eng) >= 2, stages_with_eng
        # метки без «— лот»
        for s in ph.stages:
            assert all("— лот" not in lbl for lbl in s.engineering_stage)
        # Σ по очередям = Σ комплектов лотов
        tot_stage = sum(c for s in ph.stages
                        for c in s.engineering_stage.values())
        tot_lots = sum(sum(lp.engineering.values()) for lp in ph.lots)
        assert tot_stage == tot_lots


def test_deficit_when_social_undersized(norms):
    """Недостаточная вместимость ДОО (ручной override) → дефицит на этапе."""
    o = CalculationOptions(
        floors=12, planning_doc=True,
        kindergarten=KindergartenSpec(num_objects=1, capacity_per_object=100),
        phasing=PhasingSpec(mode="manual", shares=[0.5, 0.5]),
    )
    r = solve_max_kit(Site(area_m2=200_000), o, norms)
    ph = r.phasing
    assert any(not s.is_ok for s in ph.stages)
    assert any("PHASE_SOC_DEFICIT" in w for w in r.warnings)


def test_kg_over_250_note(norms):
    """v0.15.3: корпус ДОО > 250 мест → примечание про выкуп КС."""
    from urban_model.export.variant_tables import build_variant_table_blocks
    o = CalculationOptions(
        floors=12, planning_doc=True,
        kindergarten=KindergartenSpec(num_objects=1),  # 1 крупный корпус
    )
    r = solve_max_kit(Site(area_m2=200_000), o, norms)
    kg_block = next(b for b in build_variant_table_blocks(r)
                    if b.key == "kindergarten")
    import re
    caps = [int(x) for x in re.search(
        r"\[([\d,\s]+)\]", r.kindergarten_places_accepted.formula).group(1).split(",")]
    has_note = any("более 250 мест" in n for n in kg_block.notes)
    assert has_note == (max(caps) > 250)


def test_phasing_block_in_variant_tables(norms):
    from urban_model.export.variant_tables import build_variant_table_blocks
    o = CalculationOptions(floors=12, planning_doc=True,
                           phasing=PhasingSpec(mode="manual", shares=[0.5, 0.5]))
    r = solve_max_kit(Site(area_m2=100_000), o, norms)
    blocks = build_variant_table_blocks(r)
    ph = next((b for b in blocks if b.key == "phasing"), None)
    assert ph is not None and len(ph.rows) == 2
    assert ph.summary
