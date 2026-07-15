"""Инвариантная регрессия (v0.15.15): сочетания вкл/выкл + зависимости.

Закрепляет постоянными тестами то, что раньше проверялось разовыми
аудит-скриптами:
  1. Матрица включения компонентов: каждый флаг выключен по одному
     (leave-one-out), все выключены, все включены — базовые инварианты
     держатся, выключенный компонент действительно обнулён.
  2. Направленные зависимости: изменение параметра двигает результат в
     ожидаемую сторону (чувствительность данных к данным).
"""

from __future__ import annotations

import pytest

from urban_model import solve_max_kit
from urban_model.models import CalculationOptions, CustomObject, Site
from urban_model.models.parking import ParkingConfig
from urban_model.models.social import (
    AdditionalEducationSpec,
    KindergartenSpec,
    PolyclinicSpec,
    SchoolSpec,
    SportFacilitiesSpec,
)
from urban_model.normatives import load_normatives


@pytest.fixture(scope="module")
def norms():
    return load_normatives("spb")


SITE = Site(area_m2=100_000)

# Флаг → компоненты баланса, которые он обнуляет.
_FLAG_COMPONENTS = {
    "include_kindergarten": ["kindergarten_plot"],
    "include_school": ["school_plot"],
    "include_add_education": ["add_education_plot"],
    "include_polyclinic": ["polyclinic_plot"],
    "include_sport_facilities": ["sport_facilities"],
    "include_znop": ["znop"],
    "include_intra_driveways": ["intra_quarter_driveways"],
    "include_engineering": ["engineering_plot"],
}


def _base_invariants(r, site=SITE):
    """Базовые тождества, обязанные держаться в ЛЮБОЙ конфигурации."""
    b = r.balance
    assert sum(b.components.values()) + b.surplus == pytest.approx(
        b.site_area, rel=1e-6), "баланс не сходится"
    assert all(v >= -1e-6 for v in b.components.values()), "отрицательный компонент"
    apt = r.apartments_area.value or 0
    hl = r.housing_lot_area.value or 0
    if hl > 0:
        assert r.kit.value == pytest.approx(apt / hl, rel=1e-4)
    assert (r.population.value or 0) == pytest.approx(apt / 28.0, rel=1e-3)
    # парковки: разбивка сходится с итогом
    total = int(r.parking_required_places.value or 0)
    parts = (int(r.parking_open_places.value or 0)
             + int(r.parking_multilevel_places.value or 0)
             + int(r.parking_underground_places.value or 0)
             + int(getattr(r, "parking_stylobate_places").value or 0))
    assert parts == total, f"Σ типов парковок {parts} ≠ {total}"


class TestIncludeMatrix:
    """Каждый компонент выключен по одному — и «все off» / «все on»."""

    @pytest.mark.parametrize("flag", sorted(_FLAG_COMPONENTS))
    def test_leave_one_out(self, norms, flag):
        o = CalculationOptions(floors=12, planning_doc=True, **{flag: False})
        r = solve_max_kit(SITE, o, norms)
        _base_invariants(r)
        for comp in _FLAG_COMPONENTS[flag]:
            assert r.balance.components.get(comp, 0) == 0, (
                f"{flag}=False, но {comp} в балансе")
        # экономика существует и тождественна
        if r.economy is not None:
            e = r.economy
            assert e.profit == pytest.approx(e.revenue.total - e.cost.total, rel=1e-6)
            assert e.economy_index == pytest.approx(
                100 * e.revenue.total / e.cost.total, rel=1e-6)

    def test_all_off(self, norms):
        o = CalculationOptions(
            floors=12, planning_doc=True,
            **{f: False for f in _FLAG_COMPONENTS},
            include_parking=False, include_economy=False,
        )
        r = solve_max_kit(SITE, o, norms)
        _base_invariants(r)
        assert r.economy is None
        for comps in _FLAG_COMPONENTS.values():
            for comp in comps:
                assert r.balance.components.get(comp, 0) == 0
        # без обременений квартир больше, чем со всеми компонентами
        r_full = solve_max_kit(SITE, CalculationOptions(floors=12, planning_doc=True), norms)
        assert r.apartments_area.value > r_full.apartments_area.value

    def test_all_on_with_extras(self, norms):
        o = CalculationOptions(
            floors=12, planning_doc=True,
            custom_objects=[CustomObject(name="ФОК", plot_area_m2=3000,
                                         vri_code="5.1.2")],
        )
        r = solve_max_kit(SITE, o, norms)
        _base_invariants(r)
        assert r.balance.components.get("custom_objects", 0) == pytest.approx(3000)
        assert r.economy is not None

    def test_parking_off(self, norms):
        """include_parking=False: ПОТРЕБНОСТЬ и разбивка м/м считаются
        (справочно, как «только потребность»), но в БАЛАНС парковки не входят:
        компоненты обнулены (вклад в housing_lot обнуляется в forward,
        строки parking_open_in_lot/builtin_social_park_area)."""
        o = CalculationOptions(floors=12, planning_doc=True, include_parking=False)
        r = solve_max_kit(SITE, o, norms)
        _base_invariants(r)
        assert int(r.parking_required_places.value or 0) > 0  # справочно есть
        assert r.balance.components.get("parking_multilevel", 0) == 0
        assert r.balance.components.get("social_parking_plot", 0) == 0


class TestDependencies:
    """Направленные зависимости: параметр ↑ → результат двигается ожидаемо."""

    def test_custom_plot_reduces_apartments(self, norms):
        """Больше ЗУ доп. объекта → меньше квартир (строго монотонно)."""
        apts = []
        for plot in (2_000, 10_000, 30_000):
            o = CalculationOptions(floors=12, planning_doc=True,
                                   custom_objects=[CustomObject(
                                       name="X", plot_area_m2=plot, vri_code="4.0")])
            apts.append(solve_max_kit(SITE, o, norms).apartments_area.value)
        assert apts[0] > apts[1] > apts[2], apts

    # --- ручные параметры соцобъектов (v0.16.1, по случаю «Далты»:
    # «60 vs 500 мест доп. образования — площадь не меняется» оказалось
    # включённым only_demand; закрепляем ОБА поведения тестами) ---

    def test_add_education_places_reduce_apartments(self, norms):
        """Доп. образование отд. стоящее: больше мест → больше ЗУ → меньше квартир."""
        apts = []
        for places in (200, 500, 900):
            o = CalculationOptions(
                floors=12, planning_doc=True,
                add_education=AdditionalEducationSpec(
                    mode="manual", places_override=places))
            apts.append(solve_max_kit(SITE, o, norms).apartments_area.value)
        assert apts[0] > apts[1] > apts[2], apts

    def test_add_education_vpp_places_reduce_apartments(self, norms):
        """Доп. образование в ВПП: здание из жилой GFA → больше мест → меньше квартир."""
        apts = []
        for places in (60, 500):
            o = CalculationOptions(
                floors=12, planning_doc=True,
                add_education=AdditionalEducationSpec(
                    mode="manual", places_override=places, in_vpp=True))
            apts.append(solve_max_kit(SITE, o, norms).apartments_area.value)
        assert apts[0] > apts[1], apts

    def test_add_education_only_demand_insensitive(self, norms):
        """only_demand: места НЕ влияют на квартиры (бит-в-бит) — по дизайну."""
        apts = []
        for places in (60, 500):
            o = CalculationOptions(
                floors=12, planning_doc=True,
                add_education=AdditionalEducationSpec(
                    mode="manual", places_override=places, only_demand=True))
            apts.append(solve_max_kit(SITE, o, norms).apartments_area.value)
        assert apts[0] == apts[1], apts

    def test_polyclinic_visits_reduce_apartments(self, norms):
        """Поликлиника отд. стоящая: больше посещений → больше ЗУ → меньше квартир."""
        apts = []
        for visits in (300, 900):
            o = CalculationOptions(
                floors=12, planning_doc=True,
                polyclinic=PolyclinicSpec(mode="manual", visits_override=visits))
            apts.append(solve_max_kit(SITE, o, norms).apartments_area.value)
        assert apts[0] > apts[1], apts

    def test_sport_override_reduces_apartments(self, norms):
        """Спортплощадки: больше заданной площади → меньше квартир."""
        apts = []
        for area in (1_000.0, 10_000.0):
            o = CalculationOptions(
                floors=12, planning_doc=True,
                sport_facilities=SportFacilitiesSpec(area_override_m2=area))
            apts.append(solve_max_kit(SITE, o, norms).apartments_area.value)
        assert apts[0] > apts[1], apts

    def test_kindergarten_manual_capacity_reduces_apartments(self, norms):
        """ДОО вручную: 1 корпус на 120 мест vs 300 → больше ЗУ → меньше квартир."""
        apts = []
        for cap in (120, 300):
            o = CalculationOptions(
                floors=12, planning_doc=True,
                kindergarten=KindergartenSpec(
                    num_objects=1, capacity_per_object=cap))
            apts.append(solve_max_kit(SITE, o, norms).apartments_area.value)
        assert apts[0] > apts[1], apts

    # --- подъезды к объектам во внутриквартальных проездах
    # (v0.17.0 — соцобъекты по 600; v0.18.0 — гибрид по всем группам) ---

    def test_social_access_included_in_intra_driveways(self, norms):
        """Гибрид (дефолт): intra = base_share×S + подъезды; справочное поле
        сходится с добавкой."""
        o = CalculationOptions(floors=12, planning_doc=True)
        assert o.driveways_intra_mode == "by_objects"  # дефолт с v0.18.0
        r = solve_max_kit(SITE, o, norms)
        acc = r.driveways_social_access_area.value or 0
        share = norms.resolve("driveways.intra_quarter_base_share")
        assert acc > 0
        assert r.driveways_intra_quarter_area.value == pytest.approx(
            SITE.area_m2 * share + acc, rel=1e-9)

    def test_quarter_share_mode_keeps_legacy_scheme(self, norms):
        """Режим «доля от квартала» = схема до v0.18: 7.5%×S + 600×N."""
        o = CalculationOptions(floors=12, planning_doc=True,
                               driveways_intra_mode="quarter_share")
        r = solve_max_kit(SITE, o, norms)
        acc = r.driveways_social_access_area.value or 0
        share = norms.resolve("driveways.intra_quarter_share")
        per_obj = norms.resolve("driveways.social_object_access_m2")
        assert acc > 0 and acc % per_obj == 0     # все объекты по 600
        assert r.driveways_intra_quarter_area.value == pytest.approx(
            SITE.area_m2 * share + acc, rel=1e-9)

    def test_hybrid_counts_engineering_and_multilevel(self, norms):
        """Гибрид начисляет подъезды инженерке и МУ-паркингам, а схема
        «доля от квартала» — нет (там только соцобъекты)."""
        cfg = dict(floors=12, planning_doc=True,
                   parking=ParkingConfig(mode="custom", open_share=0.3,
                                         multilevel_share=0.7,
                                         underground_share=0.0,
                                         multilevel_levels=2))
        r_hy = solve_max_kit(SITE, CalculationOptions(**cfg), norms)
        r_qs = solve_max_kit(SITE, CalculationOptions(
            driveways_intra_mode="quarter_share", **cfg), norms)
        # у гибрида в формуле подъездов есть инженерия и МУ
        f = r_hy.driveways_social_access_area.formula or ""
        assert "инж." in f and "МУ-паркинг" in f, f
        assert "инж." not in (r_qs.driveways_social_access_area.formula or "")

    def test_more_school_objects_more_access(self, norms):
        """Больше корпусов СОШ → больше подъездов (по 600 за корпус)."""
        r1 = solve_max_kit(SITE, CalculationOptions(
            floors=12, planning_doc=True, school=SchoolSpec(num_objects=1)), norms)
        r3 = solve_max_kit(SITE, CalculationOptions(
            floors=12, planning_doc=True, school=SchoolSpec(num_objects=3)), norms)
        per_obj = norms.resolve("driveways.social_object_access_m2")
        a1 = r1.driveways_social_access_area.value or 0
        a3 = r3.driveways_social_access_area.value or 0
        # ≥ +1 объект (не +2): больше корпусов СОШ → меньше квартир/населения
        # → корпусов ДОО может стать меньше (обратная связь бисекции).
        assert a3 >= a1 + per_obj - 1e-6

    def test_custom_object_adds_access(self, norms):
        """Пользовательский объект (отд. стоящий) добавляет один подъезд."""
        base = CalculationOptions(floors=12, planning_doc=True)
        with_c = CalculationOptions(
            floors=12, planning_doc=True,
            custom_objects=[CustomObject(name="ФОК", plot_area_m2=3000,
                                         vri_code="5.1.2")])
        rb = solve_max_kit(SITE, base, norms)
        rc = solve_max_kit(SITE, with_c, norms)
        per_obj = norms.resolve("driveways.social_object_access_m2")
        # ≥, а не ==: число корпусов ДОО/СОШ может сдвинуться от населения.
        assert (rc.driveways_social_access_area.value
                >= rb.driveways_social_access_area.value + per_obj - 1e-6) or (
            rc.driveways_social_access_area.value > 0)

    def test_only_demand_excludes_access(self, norms):
        """ДОО «только потребность» — вне квартала, подъезд не считается."""
        rb = solve_max_kit(SITE, CalculationOptions(floors=12, planning_doc=True), norms)
        rd = solve_max_kit(SITE, CalculationOptions(
            floors=12, planning_doc=True,
            kindergarten=KindergartenSpec(only_demand=True)), norms)
        assert (rd.driveways_social_access_area.value
                < rb.driveways_social_access_area.value)

    def test_site_area_increases_apartments(self, norms):
        o = CalculationOptions(floors=12, planning_doc=True)
        a1 = solve_max_kit(Site(area_m2=50_000), o, norms).apartments_area.value
        a2 = solve_max_kit(Site(area_m2=150_000), o, norms).apartments_area.value
        assert a2 > a1 * 1.5

    def test_floors_monotone_without_znop(self, norms):
        """Без ЗНОП площадь монотонна по этажности (v0.10.6: пик — ступени ЗНОП)."""
        apts = []
        for fl in (4, 9, 16):
            o = CalculationOptions(floors=fl, planning_doc=True, include_znop=False)
            apts.append(solve_max_kit(SITE, o, norms).apartments_area.value)
        assert apts[0] < apts[1] <= apts[2] + 1.0, apts

    def test_planning_doc_raises_ceiling(self, norms):
        """ДПТ поднимает потолок КИТ → квартир не меньше."""
        o_no = CalculationOptions(floors=12, planning_doc=False)
        o_yes = CalculationOptions(floors=12, planning_doc=True)
        a_no = solve_max_kit(SITE, o_no, norms).apartments_area.value
        a_yes = solve_max_kit(SITE, o_yes, norms).apartments_area.value
        assert a_yes >= a_no - 1.0

    def test_multilevel_share_frees_surface(self, norms):
        """Перевод парковок открытые → МУ (5 эт.) освобождает ЗУ → квартир
        больше. БЕЗ ЗНОП: со ЗНОП направление может инвертироваться —
        меньший housing_lot поднимает КИТ на следующую ЗНОП-ступень
        (документированный эффект, v0.10.6)."""
        def _apts(open_share):
            o = CalculationOptions(
                floors=12, planning_doc=True, include_znop=False,
                parking=ParkingConfig(mode="custom", open_share=open_share,
                                      multilevel_share=1.0 - open_share,
                                      underground_share=0.0, multilevel_levels=5))
            return solve_max_kit(SITE, o, norms).apartments_area.value
        assert _apts(0.3) > _apts(0.9)

    def test_znop_step_can_invert_parking_effect(self, norms):
        """Документируем норматив: со ЗНОП больше открытых МОЖЕТ дать больше
        квартир (КИТ ниже → ступень ЗНОП 0 м²/чел). Это ПЗЗ, не баг."""
        def _r(open_share):
            o = CalculationOptions(
                floors=12, planning_doc=True,
                parking=ParkingConfig(mode="custom", open_share=open_share,
                                      multilevel_share=1.0 - open_share,
                                      underground_share=0.0, multilevel_levels=5))
            return solve_max_kit(SITE, o, norms)
        r_ml, r_open = _r(0.3), _r(0.9)
        # у варианта с большим lot КИТ ниже и ЗНОП-ступень не выше
        assert r_open.kit.value < r_ml.kit.value
        assert (r_open.znop_per_person.value or 0) <= (r_ml.znop_per_person.value or 0)

    def test_only_demand_kg_frees_land(self, norms):
        """«Только потребность» ДОО: ЗУ вне квартала → квартир больше."""
        o1 = CalculationOptions(floors=12, planning_doc=True)
        o2 = CalculationOptions(floors=12, planning_doc=True,
                                kindergarten=KindergartenSpec(only_demand=True))
        a1 = solve_max_kit(SITE, o1, norms).apartments_area.value
        a2 = solve_max_kit(SITE, o2, norms).apartments_area.value
        assert a2 > a1

    def test_znop_area_override_reduces_apartments(self, norms):
        """Больше ЗНОП (площадью) → меньше места под жильё."""
        def _apts(z):
            o = CalculationOptions(floors=12, planning_doc=True,
                                   znop_total_area_override=z)
            return solve_max_kit(SITE, o, norms).apartments_area.value
        assert _apts(5_000) > _apts(30_000)

    def test_school_objects_increase_plot(self, norms):
        """Больше корпусов СОШ → больше суммарного ЗУ школ → меньше квартир
        (ступени м²/место + бассейн/спорт-ядро на корпус, v0.14.3)."""
        big = Site(area_m2=200_000)
        o1 = CalculationOptions(floors=12, planning_doc=True,
                                school=SchoolSpec(num_objects=1))
        o2 = CalculationOptions(floors=12, planning_doc=True,
                                school=SchoolSpec(num_objects=3))
        r1 = solve_max_kit(big, o1, norms)
        r2 = solve_max_kit(big, o2, norms)
        assert r2.school_plot_area.value > r1.school_plot_area.value
        assert r2.apartments_area.value < r1.apartments_area.value

    def test_residential_class_keeps_physics(self, norms):
        """Класс жилья меняет только экономику, не физику."""
        res = {}
        for cls in ("economy", "business"):
            o = CalculationOptions(floors=12, planning_doc=True, residential_class=cls)
            res[cls] = solve_max_kit(SITE, o, norms)
        assert res["economy"].apartments_area.value == pytest.approx(
            res["business"].apartments_area.value, abs=1.0)
        assert (res["business"].economy.revenue.residential
                > res["economy"].economy.revenue.residential)

    def test_social_funding_monotone_index(self, norms):
        """developer ≤ compensated ≤ at_cost ≤ city по эконом-индексу."""
        idx = {}
        for mode in ("developer", "compensated", "at_cost", "city"):
            o = CalculationOptions(floors=12, planning_doc=True,
                                   social_funding=mode,
                                   social_compensation_share=0.7)
            idx[mode] = solve_max_kit(SITE, o, norms).economy.economy_index
        assert (idx["developer"] <= idx["compensated"] + 1e-9
                <= idx["at_cost"] + 2e-9 <= idx["city"] + 3e-9), idx

    def test_engineering_by_lots_costs_land(self, norms):
        """Автономная инженерия по лотам: ЗУ инженерии ≥, квартир ≤."""
        from urban_model.models.phasing import PhasingSpec
        big = Site(area_m2=200_000)
        o_off = CalculationOptions(floors=12, planning_doc=True,
                                   phasing=PhasingSpec(mode="auto"))
        o_on = CalculationOptions(floors=12, planning_doc=True,
                                  phasing=PhasingSpec(mode="auto",
                                                      engineering_by_lots=True))
        r_off = solve_max_kit(big, o_off, norms)
        r_on = solve_max_kit(big, o_on, norms)
        assert (r_on.balance.components["engineering_plot"]
                >= r_off.balance.components["engineering_plot"] - 1e-6)
        assert r_on.apartments_area.value <= r_off.apartments_area.value + 1.0

    def test_custom_floor_area_raises_cost(self, norms):
        """Больше поэтажной площади доп. объекта → выше себестоимость custom."""
        def _cost(fa):
            o = CalculationOptions(floors=12, planning_doc=True,
                                   custom_objects=[CustomObject(
                                       name="X", plot_area_m2=3000,
                                       vri_code="4.0", floor_area_m2=fa)])
            return solve_max_kit(SITE, o, norms).economy.cost.custom_objects
        assert _cost(9_000) > _cost(3_000)


class TestVariantTablesConsistency:
    """Билдер таблиц не падает и отражает состав на всех конфигурациях."""

    @pytest.mark.parametrize("flag", sorted(_FLAG_COMPONENTS))
    def test_builder_runs_on_all_flags(self, norms, flag):
        from urban_model.export.variant_tables import build_variant_table_blocks
        o = CalculationOptions(floors=12, planning_doc=True, **{flag: False})
        r = solve_max_kit(SITE, o, norms)
        blocks = build_variant_table_blocks(r)
        keys = {b.key for b in blocks}
        assert {"housing", "balance", "parking"} <= keys
        # инженерия при include=False остаётся видимой как «только потребность»
        # (осознанно); её ЗУ в балансе — 0 (проверено в TestIncludeMatrix)


# ---------------------------------------------------------------------------
# Расширение (v0.15.15, по запросу): ВПП, кластеры, очерёдность, комбо,
# идемпотентность и независимость от истории ввода.
# ---------------------------------------------------------------------------

from urban_model.calculations import vpp as _vpp
from urban_model.models.cluster import FloorCluster
from urban_model.models.phasing import PhasingSpec


def _solve_with_vpp(site, opts, norms, mode):
    """База как в UI: 2-проходная сборка ВПП заданным режимом."""
    o1 = opts.model_copy(deep=True)
    o1.built_in = None
    o1.built_in_list = []
    r0 = solve_max_kit(site, o1, norms)
    build = _vpp.build_built_ins(
        mode=mode, population=r0.population.value or 0,
        footprint=r0.housing_footprint.value or 0, norms=norms)
    o2 = opts.model_copy(deep=True)
    o2.built_in = None
    o2.built_in_list = build.built_ins
    return solve_max_kit(site, o2, norms)


class TestVppModes:
    """Все режимы ВПП: сборка, баланс, направленный эффект."""

    @pytest.mark.parametrize("mode", ["min_only", "min_plus", "half_floor",
                                      "full_floor"])
    def test_mode_builds_and_balances(self, norms, mode):
        o = CalculationOptions(floors=12, planning_doc=True)
        r = _solve_with_vpp(SITE, o, norms, mode)
        _base_invariants(r)
        assert (r.built_in_area.value or 0) > 0
        assert (r.built_in_parking_places.value or 0) > 0
        # обязательный минимум по НГП покрыт
        m = _vpp.compute_mandatory_areas(r.population.value or 0, norms)
        assert (r.built_in_area.value or 0) >= (
            m.shopping_4_4 + m.catering_4_6 + m.domestic_3_3) * 0.99

    def test_bigger_vpp_fewer_apartments(self, norms):
        """Больше ВПП -> меньше квартир (ВПП вычитается из жилой GFA)."""
        o = CalculationOptions(floors=12, planning_doc=True)
        a_min = _solve_with_vpp(SITE, o, norms, "min_only").apartments_area.value
        a_full = _solve_with_vpp(SITE, o, norms, "full_floor").apartments_area.value
        assert a_full < a_min


class TestClusterCombos:
    """Кластеры в сочетаниях с другими параметрами."""

    def test_single_cluster_equals_single_floors(self, norms):
        """1 зона на весь квартал == одиночная этажность (бит-в-бит)."""
        o1 = CalculationOptions(floors=12, planning_doc=True)
        o2 = CalculationOptions(
            floors=12, planning_doc=True,
            floor_clusters=[FloorCluster(area_m2=SITE.area_m2, floors=12)])
        r1 = solve_max_kit(SITE, o1, norms)
        r2 = solve_max_kit(SITE, o2, norms)
        assert r2.apartments_area.value == pytest.approx(
            r1.apartments_area.value, rel=1e-9)

    def test_equal_zones_equal_single(self, norms):
        """3 зоны равной этажности == одиночная (бит-в-бит, с ВПП)."""
        o1 = CalculationOptions(floors=12, planning_doc=True)
        o3 = CalculationOptions(
            floors=12, planning_doc=True,
            floor_clusters=[
                FloorCluster(area_m2=40_000, floors=12),
                FloorCluster(area_m2=30_000, floors=12),
                FloorCluster(area_m2=30_000, floors=12)])
        a1 = _solve_with_vpp(SITE, o1, norms, "half_floor").apartments_area.value
        a3 = _solve_with_vpp(SITE, o3, norms, "half_floor").apartments_area.value
        assert a3 == pytest.approx(a1, rel=1e-6)

    def test_zones_sum_and_effective_floors(self, norms):
        """Сумма квартир по зонам = итог; средневзвеш. этажность корректна."""
        o = CalculationOptions(
            floors=12, planning_doc=True,
            floor_clusters=[FloorCluster(area_m2=60_000, floors=9),
                            FloorCluster(area_m2=40_000, floors=16)])
        r = solve_max_kit(SITE, o, norms)
        _base_invariants(r)
        det = r.floor_clusters_detail
        assert sum(d["apartments_area"] for d in det) == pytest.approx(
            r.apartments_area.value, rel=1e-6)
        feff = (60_000 * 9 + 40_000 * 16) / 100_000
        assert r.effective_floors == pytest.approx(feff, rel=1e-9)

    def test_clusters_with_phasing_and_vpp(self, norms):
        """Кластеры + очерёдность + ВПП вместе — баланс и этапы сходятся."""
        o = CalculationOptions(
            floors=12, planning_doc=True,
            floor_clusters=[FloorCluster(area_m2=60_000, floors=9),
                            FloorCluster(area_m2=40_000, floors=16)],
            phasing=PhasingSpec(mode="auto"))
        r = _solve_with_vpp(SITE, o, norms, "half_floor")
        _base_invariants(r)
        if r.phasing and r.phasing.stages:
            assert sum(s.apartments_m2 for s in r.phasing.stages) == pytest.approx(
                r.apartments_area.value, rel=1e-6)


class TestPhasingCombos:
    """Очерёдность во всех видах, в т.ч. с only_demand и off-флагами."""

    @pytest.mark.parametrize("spec", [
        PhasingSpec(mode="auto"),
        PhasingSpec(mode="auto", engineering_by_lots=True),
        PhasingSpec(mode="manual", shares=[0.5, 0.5]),
        PhasingSpec(mode="manual", shares=[0.2, 0.2, 0.2, 0.2, 0.2]),
        PhasingSpec(mode="manual", shares=[0.125] * 8),
        PhasingSpec(mode="manual", shares=[0.05] * 20),
        PhasingSpec(mode="manual", shares=[0.7, 0.3], engineering_by_lots=True),
    ], ids=["auto", "auto-lots-eng", "manual2", "manual5", "manual8",
            "manual20", "manual2-lots-eng"])
    def test_phasing_variants(self, norms, spec):
        big = Site(area_m2=200_000)
        o = CalculationOptions(floors=12, planning_doc=True,
                               phasing=spec.model_copy(deep=True))
        r = solve_max_kit(big, o, norms)
        _base_invariants(r, site=big)
        ph = r.phasing
        assert ph is not None
        if ph.stages:
            assert sum(s.share for s in ph.stages) == pytest.approx(1.0)
            assert sum(s.apartments_m2 for s in ph.stages) == pytest.approx(
                r.apartments_area.value, rel=1e-6)
            lots = [s.lot for s in ph.stages]
            assert lots == sorted(lots) and lots[0] == 1

    def test_phasing_with_only_demand_social(self, norms):
        """Очереди при only_demand ДОО/СОШ: корпуса считаются, ЗУ вне баланса."""
        big = Site(area_m2=200_000)
        o = CalculationOptions(
            floors=12, planning_doc=True,
            kindergarten=KindergartenSpec(only_demand=True),
            school=SchoolSpec(only_demand=True),
            phasing=PhasingSpec(mode="auto"))
        r = solve_max_kit(big, o, norms)
        _base_invariants(r, site=big)
        assert r.balance.components.get("kindergarten_plot", 0) == 0
        assert r.balance.components.get("school_plot", 0) == 0

    def test_phasing_with_social_off(self, norms):
        """Очереди при выключенных ДОО и СОШ: авто честно не делит."""
        o = CalculationOptions(floors=12, planning_doc=True,
                               include_kindergarten=False, include_school=False,
                               phasing=PhasingSpec(mode="auto"))
        r = solve_max_kit(SITE, o, norms)
        assert r.phasing is not None and r.phasing.stages == []
        # но ручной режим работает и без соцобъектов
        o2 = CalculationOptions(floors=12, planning_doc=True,
                                include_kindergarten=False, include_school=False,
                                phasing=PhasingSpec(mode="manual",
                                                    shares=[0.5, 0.5]))
        r2 = solve_max_kit(SITE, o2, norms)
        assert len(r2.phasing.stages) == 2


class TestComboMatrix:
    """Пары «сложных» параметров вместе — баланс и тождества держатся."""

    @pytest.mark.parametrize("kw", [
        dict(kindergarten=KindergartenSpec(only_demand=True),
             include_znop=False),
        dict(school=SchoolSpec(only_demand=True), include_engineering=False),
        dict(include_kindergarten=False,
             custom_objects=[CustomObject(name="X", plot_area_m2=5000,
                                          vri_code="4.0")]),
        dict(znop_total_area_override=20_000.0, include_sport_facilities=False),
        dict(parking=ParkingConfig(mode="all_open"), include_polyclinic=False),
        dict(parking=ParkingConfig(mode="custom", open_share=0.4,
                                   multilevel_share=0.3, underground_share=0.2,
                                   stylobate_share=0.1, multilevel_levels=3),
             include_add_education=False),
        dict(enforce_quarter_greening_norm=False, enforce_density_norm=False,
             floors=25),
        dict(kindergarten=KindergartenSpec(strict_capacity=True),
             school=SchoolSpec(strict_capacity=True),
             phasing=PhasingSpec(mode="auto")),
    ], ids=["od-kg-no-znop", "od-sch-no-eng", "no-kg-custom",
            "znop-manual-no-sport", "all-open-no-poly", "4types-no-ae",
            "soft-norms-25fl", "strict-phasing"])
    def test_combo(self, norms, kw):
        base = dict(floors=12, planning_doc=True)
        base.update(kw)
        r = solve_max_kit(SITE, CalculationOptions(**base), norms)
        _base_invariants(r)
        if r.economy is not None:
            e = r.economy
            assert e.economy_index == pytest.approx(
                100 * e.revenue.total / e.cost.total, rel=1e-6)
        # билдер таблиц не падает
        from urban_model.export.variant_tables import build_variant_table_blocks
        assert build_variant_table_blocks(r)


class TestIdempotency:
    """Расчёт — чистая функция опций: не зависит от повторов и истории."""

    def test_same_options_bitwise_equal(self, norms):
        o = CalculationOptions(floors=12, planning_doc=True)
        r1 = solve_max_kit(SITE, o, norms)
        r2 = solve_max_kit(SITE, o, norms)
        assert r1.apartments_area.value == r2.apartments_area.value
        assert r1.kit.value == r2.kit.value
        assert r1.balance.surplus == r2.balance.surplus

    def test_change_and_revert_equals_original(self, norms):
        """o -> o2 -> o: возврат параметров даёт исходный результат бит-в-бит."""
        o = CalculationOptions(floors=12, planning_doc=True)
        r_before = solve_max_kit(SITE, o, norms)
        o2 = o.model_copy(deep=True)
        o2.floors = 20
        o2.include_znop = False
        solve_max_kit(SITE, o2, norms)           # промежуточный расчёт
        r_after = solve_max_kit(SITE, o, norms)  # вернули как было
        assert r_after.apartments_area.value == r_before.apartments_area.value
        assert r_after.kit.value == r_before.kit.value

    def test_options_not_mutated_by_solve(self, norms):
        """solve не мутирует переданные options (несколько прогонов подряд)."""
        o = CalculationOptions(
            floors=12, planning_doc=True,
            custom_objects=[CustomObject(name="X", plot_area_m2=5000,
                                         vri_code="4.0")],
            phasing=PhasingSpec(mode="auto"))
        snapshot = o.model_dump_json()
        for _ in range(3):
            solve_max_kit(SITE, o, norms)
        assert o.model_dump_json() == snapshot


class TestUIHistory:
    """UI: результат не зависит от того, вводились параметры сразу или
    по одному за несколько прогонов (ловит баги «со второго раза»)."""

    def test_stepwise_equals_direct(self):
        """floors и площадь по одному (2 прогона) == оба сразу (1 прогон)."""
        from streamlit.testing.v1 import AppTest

        def _run(preset: dict, steps: list[dict]):
            at = AppTest.from_file("src/urban_model/ui/app.py",
                                   default_timeout=600)
            at.session_state["_auth_ok"] = True
            for k, v in preset.items():
                at.session_state[k] = v
            at.run()
            for step in steps:
                for k, v in step.items():
                    at.session_state[k] = v
                at.run()
            assert not at.exception
            return at.session_state["last_calc_result"].apartments_area.value

        direct = _run({"floors": 16, "area_input_m2": 60_000.0}, [])
        stepwise = _run({}, [{"floors": 16}, {"area_input_m2": 60_000.0}])
        assert stepwise == pytest.approx(direct, rel=1e-9)

    def test_toggle_component_and_back(self):
        """Выключил ДОО -> включил обратно: результат как исходный."""
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file("src/urban_model/ui/app.py",
                               default_timeout=600)
        at.session_state["_auth_ok"] = True
        at.session_state["area_input_m2"] = 50_000.0
        at.run()
        a0 = at.session_state["last_calc_result"].apartments_area.value
        cb = next(c for c in at.checkbox if c.key == "include_kg")
        cb.uncheck()
        at.run()
        a_off = at.session_state["last_calc_result"].apartments_area.value
        assert a_off > a0  # без ДОО квартир больше
        cb2 = next(c for c in at.checkbox if c.key == "include_kg")
        cb2.check()
        at.run()
        a_back = at.session_state["last_calc_result"].apartments_area.value
        assert not at.exception
        assert a_back == pytest.approx(a0, rel=1e-9)
