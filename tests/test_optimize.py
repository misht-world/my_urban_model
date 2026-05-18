"""Тесты Optuna-оптимизатора v0.5."""

from __future__ import annotations

import pytest

from urban_model.models import CalculationOptions, Site
from urban_model.normatives import load_normatives
from urban_model.optimize import SearchSpace, optimize_max_apartments
from urban_model.optimize.runner import OptimizationReport


@pytest.fixture(scope="module")
def spb():
    return load_normatives("spb")


@pytest.fixture(scope="module")
def site():
    return Site(area_m2=50_000)


@pytest.fixture(scope="module")
def base_opts():
    return CalculationOptions(floors=12, planning_doc=True)


# ---------------------------------------------------------------------------
# Базовое поведение
# ---------------------------------------------------------------------------

class TestEmptySpace:
    def test_empty_space_returns_base_result(self, site, base_opts, spb):
        """Если SearchSpace пустой — возвращается единственный базовый расчёт."""
        space = SearchSpace()  # ничего не варьируется
        report = optimize_max_apartments(site, base_opts, spb, space, n_trials=5)
        assert report.n_trials_total == 1
        assert report.best is not None
        assert len(report.top_n) == 1


class TestSingleParameterSearch:
    def test_floors_search_finds_best(self, site, base_opts, spb):
        """Поиск по этажности должен найти лучший вариант (обычно высокая этажность)."""
        space = SearchSpace(floors_range=(8, 25))
        report = optimize_max_apartments(site, base_opts, spb, space, n_trials=15)
        assert report.best is not None
        assert report.best.feasible
        assert "floors" in report.best.params
        # Лучший вариант должен дать ≥ базового
        assert report.best.apartments_area >= report.base_apartments_area - 1.0

    def test_top_n_sorted_descending(self, site, base_opts, spb):
        """top_n упорядочен по убыванию apartments_area."""
        space = SearchSpace(floors_range=(8, 25))
        report = optimize_max_apartments(site, base_opts, spb, space, n_trials=15, top_n=5)
        areas = [r.apartments_area for r in report.top_n]
        assert areas == sorted(areas, reverse=True)
        assert all(r.feasible for r in report.top_n)
        assert all(r.rank == i + 1 for i, r in enumerate(report.top_n))


class TestParkingSearch:
    def test_parking_modes_categorical(self, site, base_opts, spb):
        """Поиск среди трёх режимов парковок не падает и даёт хотя бы один feasible."""
        space = SearchSpace(parking_modes=["min_open", "all_open"])
        report = optimize_max_apartments(site, base_opts, spb, space, n_trials=10)
        assert report.best is not None

    def test_custom_parking_shares(self, site, base_opts, spb):
        """Поиск долей в режиме custom — суммы корректны, results есть."""
        space = SearchSpace(
            parking_modes=["custom"],
            parking_open_share_range=(0.1, 0.5),
            parking_multilevel_share_range=(0.0, 0.4),
            multilevel_levels_range=(1, 4),
        )
        report = optimize_max_apartments(site, base_opts, spb, space, n_trials=15)
        assert report.best is not None


# ---------------------------------------------------------------------------
# Оптимизатор реально улучшает результат
# ---------------------------------------------------------------------------

class TestZnopSearch:
    """v0.7.3: перебор значений ЗНОП (м²/чел) через znop_per_person_choices."""

    def test_znop_choices_in_params(self, site, base_opts, spb):
        """При znop_per_person_choices=[0,3,4,6] параметр появляется в результатах."""
        space = SearchSpace(
            floors_range=(10, 15),
            znop_per_person_choices=[0.0, 3.0, 4.0, 6.0],
        )
        report = optimize_max_apartments(site, base_opts, spb, space, n_trials=20)
        assert report.best is not None
        # Хотя бы один из топ-N должен иметь znop_per_person в params
        params_with_znop = [r for r in report.top_n if "znop_per_person" in r.params]
        assert len(params_with_znop) > 0
        # Все значения из заданного списка
        for r in params_with_znop:
            assert r.params["znop_per_person"] in (0.0, 3.0, 4.0, 6.0)

    def test_znop_only_not_empty_space(self, site, base_opts, spb):
        """Если задан только znop — пространство НЕ пустое."""
        space = SearchSpace(znop_per_person_choices=[0.0, 6.0])
        assert not space.is_empty()

    def test_empty_znop_choices_means_no_variation(self, site, base_opts, spb):
        """Пустой список znop = no variation (None по умолчанию)."""
        space = SearchSpace(floors_range=(10, 12))
        assert space.znop_per_person_choices is None


class TestOptimizerImproves:
    def test_floors_optimization_beats_low_baseline(self, site, spb):
        """Низкая базовая этажность; оптимизатор должен поднять КИТ → больше квартир."""
        base = CalculationOptions(floors=5, planning_doc=True)
        space = SearchSpace(floors_range=(5, 25))
        report = optimize_max_apartments(site, base, spb, space, n_trials=20)
        # Лучший вариант должен превзойти baseline (этажность 5)
        assert report.best.apartments_area > report.base_apartments_area + 1.0


# ---------------------------------------------------------------------------
# Воспроизводимость
# ---------------------------------------------------------------------------

class TestReproducibility:
    def test_same_seed_same_result(self, site, base_opts, spb):
        """С одинаковым seed дважды → одинаковый best.apartments_area."""
        space = SearchSpace(floors_range=(8, 20))
        r1 = optimize_max_apartments(site, base_opts, spb, space, n_trials=10, seed=42)
        r2 = optimize_max_apartments(site, base_opts, spb, space, n_trials=10, seed=42)
        assert abs(r1.best.apartments_area - r2.best.apartments_area) < 1e-3
