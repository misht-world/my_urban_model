"""Тесты стилобатной парковки (v0.12.2)."""

from __future__ import annotations

import pytest

from urban_model import solve_max_kit, verify_kit
from urban_model.models import CalculationOptions, Site
from urban_model.models.parking import ParkingConfig
from urban_model.normatives import load_normatives


@pytest.fixture(scope="module")
def spb():
    return load_normatives("spb")


@pytest.fixture
def site():
    return Site(area_m2=60_000)


def _cfg(styl, **kw):
    # v0.12.9: стилобат — 4-й тип в общей сумме 1.0; open/ug делят остаток.
    rest = (1.0 - styl) / 2.0
    return CalculationOptions(
        floors=14,
        parking=ParkingConfig(
            mode="custom", open_share=rest, multilevel_share=0.0,
            underground_share=rest, stylobate_share=styl,
        ),
        **kw,
    )


def test_no_stylobate_by_default(spb, site):
    r = solve_max_kit(site, CalculationOptions(floors=14), spb)
    assert (r.parking_stylobate_places.value or 0) == 0
    assert (r.parking_stylobate_area.value or 0) == 0


def test_stylobate_places_and_area(spb, site):
    r = solve_max_kit(site, _cfg(0.6), spb)
    pl = int(r.parking_stylobate_places.value)
    assert pl > 0
    # площадь = места × 35
    assert r.parking_stylobate_area.value == pytest.approx(pl * 35, abs=1.0)


def test_stylobate_taken_from_housing_pool(spb, site):
    """Стилобатные места изымаются из пула, делимого на open/ml/ug."""
    r = solve_max_kit(site, _cfg(0.6), spb)
    total = int(r.parking_required_places.value)
    o = int(r.parking_open_places.value)
    u = int(r.parking_underground_places.value)
    s = int(r.parking_stylobate_places.value)
    ml = int(r.parking_multilevel_places.value)
    assert o + u + s + ml == total  # стилобат — часть общего числа м/м


def test_stylobate_reduces_apartments(spb, site):
    """25% деки под домами → −1 этаж жилья → меньше квартир.

    include_add_education=False: изолируем эффект стилобата от доп. обр., иначе
    при разном КИТ баланс с доп. обр. (отд. стоящее) смещает сравнение (v0.12.15).
    """
    base = solve_max_kit(site, _cfg(0.0, include_add_education=False), spb)
    styl = solve_max_kit(site, _cfg(0.7, include_add_education=False), spb)
    assert styl.apartments_area.value < base.apartments_area.value


def test_stylobate_no_balance_component(spb, site):
    """Стилобат не добавляет компонент ЗУ в баланс (стоит над кварталом)."""
    r = verify_kit(1.4, site, _cfg(0.6), spb)
    assert "stylobate" not in r.balance.components
    assert "stylobate_plot" not in r.balance.components


def test_stylobate_economy(spb, site):
    r = solve_max_kit(site, _cfg(0.6), spb)
    assert r.economy is not None
    assert r.economy.cost.parking_stylobate > 0
    assert r.economy.revenue.parking_stylobate > 0
    # себестоимость деки = площадь × ставка (0.34)
    expected = r.parking_stylobate_area.value * spb.resolve(
        "economy.construction.parking_stylobate")
    assert r.economy.cost.parking_stylobate == pytest.approx(expected, rel=1e-6)


def test_stylobate_economy_index_lower_at_same_floors(spb, site):
    """Доп. себестоимость стилобата при verify (фикс. КИТ) снижает индекс."""
    base = verify_kit(1.5, site, _cfg(0.0), spb)
    styl = verify_kit(1.5, site, _cfg(0.7), spb)
    assert styl.economy.economy_index <= base.economy.economy_index + 1e-6


# --- developer_score / parking matrix ---

def test_parking_suitability_matrix_loads(spb):
    for cls in ("economy", "comfort", "business"):
        m = spb.resolve("economy.parking_suitability", residential_class=cls)
        assert set(m.keys()) == {"open", "multilevel", "underground", "stylobate"}
        assert all(0.0 <= v <= 1.0 for v in m.values())


def test_open_below_min_warning_at_full_stylobate(spb, site):
    """v0.12.10: стилобат 100% → открытых 0% → WARNING о нарушении 12.5%."""
    r = solve_max_kit(site, _cfg(1.0), spb)
    assert any("OPEN_BELOW_MIN" in w for w in r.warnings)


def test_no_open_warning_normal_mix(spb, site):
    """При нормальном миксе (open ≥ 12.5%) предупреждения нет."""
    r = solve_max_kit(site, _cfg(0.3), spb)
    assert not any("OPEN_BELOW_MIN" in w for w in r.warnings)


def test_social_objects_use_post_stylobate_population(spb):
    """v0.12.10: ДОО/СОШ считаются от населения ПОСЛЕ потери квартир под
    стилобатом (требуемые места соответствуют итоговому населению)."""
    site = Site(area_m2=120_000)
    r = solve_max_kit(site, _cfg(0.7), spb)
    kg_per_1000 = spb.resolve("social_objects.kindergarten.places_per_1000")
    expected = r.population.value * kg_per_1000 / 1000
    # требуемые места ДОО соответствуют ИТОГОВОМУ населению (а не «дострилоб.»)
    assert abs(r.kindergarten_places_required.value - expected) < 1.0


def test_parking_caps_matrix_loads(spb):
    for cls in ("economy", "comfort", "business"):
        caps = spb.resolve("economy.parking_caps", residential_class=cls)
        assert set(caps.keys()) == {"open", "multilevel", "underground", "stylobate"}
        assert all(0.0 <= v <= 1.0 for v in caps.values())
    # Эконом: подземка жёстко ограничена; бизнес — свободно
    eco = spb.resolve("economy.parking_caps", residential_class="economy")
    biz = spb.resolve("economy.parking_caps", residential_class="business")
    assert eco["underground"] <= 0.25
    assert biz["underground"] >= 0.9


def test_economy_recommendations_respect_underground_cap(spb, site):
    """Эконом-рекомендации не предлагают много подземки (потолок 20%)."""
    from urban_model.optimize.pareto import generate_pareto_recommendations, _parking_shares
    base = solve_max_kit(site, CalculationOptions(floors=14, residential_class="economy"), spb)
    bundle = generate_pareto_recommendations(
        site, CalculationOptions(floors=14, residential_class="economy"),
        spb, base, n_trials=120, seed=7,
    )
    cap = spb.resolve("economy.parking_caps", residential_class="economy")["underground"]
    for r in bundle.recommendations:
        ug_share = _parking_shares(r.tep)["underground"]
        assert ug_share <= cap + 0.03, f"{r.label}: подземка {ug_share:.2f} > потолка {cap}"


def test_developer_recommendation_present(spb, site):
    from urban_model.optimize.pareto import generate_pareto_recommendations
    base = solve_max_kit(site, CalculationOptions(floors=14, residential_class="comfort"), spb)
    bundle = generate_pareto_recommendations(
        site, CalculationOptions(floors=14, residential_class="comfort"),
        spb, base, n_trials=80, seed=3,
    )
    labels = [r.label for r in bundle.recommendations]
    # «Девелоперский» должен присутствовать (при достаточном пуле)
    assert any("Девелоперск" in lbl for lbl in labels)
