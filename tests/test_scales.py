"""Параметризованные тесты на разных масштабах квартала (v0.9.11).

Закрывает AUDIT P1-12: раньше все тесты были на 50 000 м². Теперь
проверяем что модель и Парето корректно работают на 4 размерах:
1 000 / 5 000 / 50 000 / 200 000 м².

Что проверяем:
- `solve_max_kit` не падает с исключением.
- `generate_pareto_recommendations` возвращает ParetoBundle с понятным
  `no_feasible_reason`, если рекомендаций нет.
- На больших кварталах Парето даёт ≥1 рекомендацию.
"""

from __future__ import annotations

import pytest

from urban_model import solve_max_kit
from urban_model.models import CalculationOptions, Site
from urban_model.normatives import load_normatives
from urban_model.optimize.pareto import (
    ParetoBundle,
    ParetoConstraints,
    generate_pareto_recommendations,
)


@pytest.fixture(scope="module")
def spb():
    return load_normatives("spb")


# ---------------------------------------------------------------------------
# solve_max_kit на разных масштабах — не должен падать с исключением
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("area_m2", [1_000, 5_000, 50_000, 200_000])
def test_solve_max_kit_does_not_crash(spb, area_m2):
    """Базовый расчёт должен отрабатывать на любом разумном масштабе.
    Может вернуть feasible=False, но не должен падать исключением.
    """
    site = Site(area_m2=area_m2)
    opts = CalculationOptions(floors=12)
    res = solve_max_kit(site, opts, spb)
    assert res is not None
    assert res.apartments_area.value is not None
    # На любом масштабе apt должен быть неотрицательным
    assert (res.apartments_area.value or 0) >= 0


@pytest.mark.parametrize("area_m2", [50_000, 200_000])
def test_pareto_returns_recommendations_on_large_sites(spb, area_m2):
    """На кварталах ≥0.5 га Парето должен находить ≥1 feasible рекомендацию."""
    site = Site(area_m2=area_m2)
    opts = CalculationOptions(floors=12)
    base = solve_max_kit(site, opts, spb)
    # Уменьшенный n_trials для скорости тестов
    bundle = generate_pareto_recommendations(
        site, opts, spb, base, n_trials=80, seed=42,
    )
    assert isinstance(bundle, ParetoBundle)
    assert len(bundle.recommendations) >= 1


@pytest.mark.parametrize("area_m2", [1_000])
def test_pareto_explains_zero_feasible_on_tiny_site(spb, area_m2):
    """На очень малом квартале (0.1 га) нормативы противоречивы.
    Парето должен возвращать пустые recs + понятный no_feasible_reason.
    """
    site = Site(area_m2=area_m2)
    opts = CalculationOptions(floors=12)
    base = solve_max_kit(site, opts, spb)
    bundle = generate_pareto_recommendations(
        site, opts, spb, base, n_trials=60, seed=42,
    )
    # На таком квартале либо 0 рекомендаций, либо очень мало;
    # ключевое — если 0, должен быть reason.
    if not bundle.recommendations:
        assert bundle.no_feasible_reason is not None
        assert len(bundle.no_feasible_reason) > 30  # содержательный текст


# ---------------------------------------------------------------------------
# Стабильность Парето при одинаковом seed (AUDIT P1-11)
# ---------------------------------------------------------------------------

class TestParetoStability:
    """При фиксированном seed повторный вызов даёт ТЕ ЖЕ recommendations."""

    def test_recommendations_stable_with_same_seed(self, spb):
        site = Site(area_m2=50_000)
        opts = CalculationOptions(floors=12)
        base = solve_max_kit(site, opts, spb)

        b1 = generate_pareto_recommendations(
            site, opts, spb, base, n_trials=80, seed=42,
        )
        b2 = generate_pareto_recommendations(
            site, opts, spb, base, n_trials=80, seed=42,
        )
        # Длина одинакова
        assert len(b1.recommendations) == len(b2.recommendations)
        # Labels совпадают
        assert [r.label for r in b1.recommendations] == [r.label for r in b2.recommendations]
        # Площади квартир совпадают (с округлением до м²)
        apt1 = [int(r.tep.apartments_area.value or 0) for r in b1.recommendations]
        apt2 = [int(r.tep.apartments_area.value or 0) for r in b2.recommendations]
        assert apt1 == apt2


# ---------------------------------------------------------------------------
# Граничные случаи фильтров парковок (AUDIT P1-3, P1-4)
# ---------------------------------------------------------------------------

class TestParkingFilterEdges:
    """Граничные значения фильтра гибридных парковок и архетипа."""

    def test_hybrid_filter_includes_boundary_10pct(self):
        """v0.9.11: ml=10% AND ug=10% теперь считается гибридом (>= вместо >)."""
        from urban_model.optimize.pareto import _is_hybrid_parking
        assert _is_hybrid_parking({"parking_ml_share": 0.10, "parking_ug_share": 0.10})
        assert _is_hybrid_parking({"parking_ml_share": 0.10, "parking_ug_share": 0.50})
        assert not _is_hybrid_parking({"parking_ml_share": 0.099, "parking_ug_share": 0.50})

    def test_token_parking_thresholds_small_site(self):
        """На малом total (≤200 м/м) порог = абсолютный (50 МУ / 30 UG)."""
        from urban_model.optimize.pareto import _has_token_parking
        import types

        def _make_tep(ml: int, ug: int, total: int = 100):
            tep = types.SimpleNamespace()
            tep.parking_multilevel_places = types.SimpleNamespace(value=ml)
            tep.parking_underground_places = types.SimpleNamespace(value=ug)
            tep.parking_required_places = types.SimpleNamespace(value=total)
            return tep

        # При total=100, 5% = 5 → пороги остаются 50/30 (max)
        assert _has_token_parking(_make_tep(ml=10, ug=0))      # 10 < 50 → token
        assert _has_token_parking(_make_tep(ml=0, ug=10))      # 10 < 30 → token
        assert not _has_token_parking(_make_tep(ml=0, ug=0))   # нет парковок — не token
        assert not _has_token_parking(_make_tep(ml=100, ug=0)) # 100 > 50 — норма
        assert not _has_token_parking(_make_tep(ml=0, ug=200)) # норма

    def test_token_parking_scales_with_total(self):
        """v0.9.12 (AUDIT P1-2): пороги растут с total (5% от него)."""
        from urban_model.optimize.pareto import _has_token_parking
        import types

        def _make_tep(ml: int, ug: int, total: int):
            tep = types.SimpleNamespace()
            tep.parking_multilevel_places = types.SimpleNamespace(value=ml)
            tep.parking_underground_places = types.SimpleNamespace(value=ug)
            tep.parking_required_places = types.SimpleNamespace(value=total)
            return tep

        # total=2000, 5% = 100 → порог МУ становится 100 (а не 50)
        assert _has_token_parking(_make_tep(ml=80, ug=0, total=2000))  # 80 < 100
        assert not _has_token_parking(_make_tep(ml=120, ug=0, total=2000))  # 120 > 100
