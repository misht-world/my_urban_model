"""Тесты v0.9.28 — кластеры этажности (ядро)."""

from __future__ import annotations

import pytest

from urban_model import solve_max_kit, verify_kit
from urban_model.calculations import clusters as cl
from urban_model.models import CalculationOptions, FloorCluster, Site
from urban_model.normatives import load_normatives


@pytest.fixture(scope="module")
def spb():
    return load_normatives("spb")


# ---------------------------------------------------------------------------
# Чистая математика кластеров
# ---------------------------------------------------------------------------

class TestClusterMath:
    def test_effective_floors_weighted(self):
        clusters = [
            FloorCluster(area_m2=50_000, floors=9),
            FloorCluster(area_m2=80_000, floors=12),
        ]
        # (50000*9 + 80000*12)/130000 = 1410000/130000
        assert cl.effective_floors(clusters, 10) == pytest.approx(1410000 / 130000)

    def test_effective_floors_fallback_when_empty(self):
        assert cl.effective_floors([], 14) == 14.0

    def test_max_floors(self):
        clusters = [FloorCluster(area_m2=1, floors=5), FloorCluster(area_m2=1, floors=20)]
        assert cl.max_floors(clusters, 10) == 20.0

    def test_area_weights_sum_to_one(self):
        clusters = [
            FloorCluster(area_m2=30_000, floors=9),
            FloorCluster(area_m2=70_000, floors=12),
        ]
        w = cl.area_weights(clusters)
        assert sum(w) == pytest.approx(1.0)
        assert w[0] == pytest.approx(0.3)

    def test_gfa_weights_sum_to_one(self):
        clusters = [
            FloorCluster(area_m2=50_000, floors=9),
            FloorCluster(area_m2=50_000, floors=18),
        ]
        w = cl.gfa_weights(clusters)
        assert sum(w) == pytest.approx(1.0)
        # Высокий кластер даёт вдвое больше GFA при равной площади
        assert w[1] == pytest.approx(2 * w[0])

    def test_gfa_weights_match_apartments_split(self):
        # Доли GFA — основа разбивки площади квартир по кластерам.
        clusters = [
            FloorCluster(area_m2=40_000, floors=10),
            FloorCluster(area_m2=60_000, floors=10),
        ]
        w = cl.gfa_weights(clusters)
        # При равной этажности доли GFA = долям площади
        assert w[0] == pytest.approx(0.4)
        assert w[1] == pytest.approx(0.6)


# ---------------------------------------------------------------------------
# Обратная совместимость: один кластер == старое поведение
# ---------------------------------------------------------------------------

class TestBackwardCompat:
    def test_single_cluster_equals_plain_floors(self, spb):
        """Кластер с площадью = всему участку и той же этажностью даёт
        идентичный КИТ/площадь, что и обычный floors."""
        site = Site(area_m2=50_000)
        plain = solve_max_kit(site, CalculationOptions(floors=12), spb)
        clustered = solve_max_kit(
            site,
            CalculationOptions(
                floors=12,
                floor_clusters=[FloorCluster(area_m2=50_000, floors=12)],
            ),
            spb,
        )
        assert clustered.kit.value == pytest.approx(plain.kit.value, rel=1e-6)
        assert clustered.apartments_area.value == pytest.approx(
            plain.apartments_area.value, rel=1e-6
        )
        if plain.economy and clustered.economy:
            assert clustered.economy.profit == pytest.approx(
                plain.economy.profit, rel=1e-6
            )

    def test_no_clusters_detail_empty(self, spb):
        res = solve_max_kit(Site(area_m2=50_000), CalculationOptions(floors=12), spb)
        assert res.floor_clusters_detail == []
        assert res.effective_floors == pytest.approx(12.0)


# ---------------------------------------------------------------------------
# Поведение при двух кластерах
# ---------------------------------------------------------------------------

class TestTwoClusters:
    @pytest.fixture
    def two_cluster_opts(self):
        return CalculationOptions(
            floors=11,
            planning_doc=True,
            floor_clusters=[
                FloorCluster(area_m2=50_000, floors=9, label="A"),
                FloorCluster(area_m2=80_000, floors=12, label="B"),
            ],
        )

    def test_detail_has_two_entries(self, spb, two_cluster_opts):
        res = solve_max_kit(Site(area_m2=130_000), two_cluster_opts, spb)
        assert len(res.floor_clusters_detail) == 2

    def test_apartments_split_sums_to_total(self, spb, two_cluster_opts):
        """Σ площадей квартир по кластерам = общей площади квартир."""
        res = solve_max_kit(Site(area_m2=130_000), two_cluster_opts, spb)
        total = sum(d["apartments_area"] for d in res.floor_clusters_detail)
        assert total == pytest.approx(res.apartments_area.value, rel=1e-6)

    def test_tallest_cluster_has_highest_kit(self, spb, two_cluster_opts):
        res = solve_max_kit(Site(area_m2=130_000), two_cluster_opts, spb)
        kits = [(d["floors"], d["kit"]) for d in res.floor_clusters_detail]
        kits.sort()
        # Больше этажей → больше локальный КИТ_i
        assert kits[-1][1] >= kits[0][1]

    def test_kit_ceiling_NOT_tightened_by_clusters(self, spb):
        """v0.9.28.1: КИТ — общий, потолок = норматив (НЕ поджимается высоким
        кластером). Ранее поджатие давало ложный «огромный резерв»."""
        site = Site(area_m2=100_000)
        opts = CalculationOptions(
            floors=15,
            planning_doc=True,
            floor_clusters=[
                FloorCluster(area_m2=50_000, floors=5),
                FloorCluster(area_m2=50_000, floors=25),
            ],
        )
        res = solve_max_kit(site, opts, spb)
        # Потолок = нормативный 2.5 (с ДПТ), без поджатия
        assert res.kit.normative == pytest.approx(2.5, rel=1e-6)
        # Резерв близок к нулю (территория используется), а не 70%+
        assert res.balance.surplus / site.area_m2 < 0.05

    def test_clusters_kit_matches_equivalent_single_floors(self, spb):
        """Общий КИТ кластеров ≈ КИТ одиночной застройки с floors_eff."""
        site = Site(area_m2=100_000)
        clustered = solve_max_kit(
            site,
            CalculationOptions(
                floors=15, planning_doc=True,
                floor_clusters=[
                    FloorCluster(area_m2=50_000, floors=5),
                    FloorCluster(area_m2=50_000, floors=25),
                ],
            ),
            spb,
        )
        # floors_eff = 15 → сравниваем с одиночной 15-эт.
        single = solve_max_kit(
            site, CalculationOptions(floors=15, planning_doc=True), spb
        )
        assert clustered.kit.value == pytest.approx(single.kit.value, rel=0.02)


# ---------------------------------------------------------------------------
# Экономика: разноэтажные кластеры считаются по своим ставкам
# ---------------------------------------------------------------------------

class TestClusterEconomy:
    def test_residential_cost_between_pure_low_and_high(self, spb):
        """Себестоимость смешанного 5+25 кластера лежит между чистым 5-эт.
        и чистым 25-эт. вариантом при той же geometric КИТ-задаче."""
        site = Site(area_m2=100_000)
        low = verify_kit(1.0, site, CalculationOptions(floors=5), spb)
        high = verify_kit(1.0, site, CalculationOptions(floors=25), spb)
        mixed = verify_kit(
            1.0, site,
            CalculationOptions(
                floors=15,
                floor_clusters=[
                    FloorCluster(area_m2=50_000, floors=5),
                    FloorCluster(area_m2=50_000, floors=25),
                ],
            ),
            spb,
        )
        c_low = low.economy.cost.residential
        c_high = high.economy.cost.residential
        c_mixed = mixed.economy.cost.residential
        assert min(c_low, c_high) < c_mixed < max(c_low, c_high)


# ---------------------------------------------------------------------------
# Фаза C: Optuna варьирует этажность зон
# ---------------------------------------------------------------------------

class TestClusterOptimization:
    def test_search_space_vary_clusters_not_empty(self):
        from urban_model.optimize import SearchSpace
        space = SearchSpace(vary_cluster_floors=True)
        assert not space.is_empty()

    def test_runner_varies_each_cluster_within_bounds(self, spb):
        from urban_model.optimize import SearchSpace, optimize_max_apartments
        site = Site(area_m2=100_000)
        base = CalculationOptions(
            floors=12, planning_doc=True,
            floor_clusters=[
                FloorCluster(area_m2=50_000, floors=9, floors_min=5, floors_max=20),
                FloorCluster(area_m2=50_000, floors=12, floors_min=5, floors_max=20),
            ],
        )
        space = SearchSpace(
            vary_cluster_floors=True,
            parking_modes=["min_open", "all_open"],
        )
        report = optimize_max_apartments(site, base, spb, space, n_trials=20, seed=1)
        assert report.top_n  # есть feasible-результаты
        for r in report.top_n:
            cf = r.params.get("cluster_floors")
            assert cf is not None and len(cf) == 2
            for f in cf:
                assert 5 <= f <= 20

    def test_pareto_recommendations_vary_cluster_floors(self, spb):
        from urban_model import solve_max_kit
        from urban_model.optimize.pareto import (
            ParetoConstraints,
            generate_pareto_recommendations,
        )
        site = Site(area_m2=100_000)
        base = CalculationOptions(
            floors=12, planning_doc=True,
            floor_clusters=[
                FloorCluster(area_m2=50_000, floors=9, floors_min=3, floors_max=25),
                FloorCluster(area_m2=50_000, floors=12, floors_min=3, floors_max=25),
            ],
        )
        base_tep = solve_max_kit(site, base, spb)
        bundle = generate_pareto_recommendations(
            site, base, spb, base_tep, n_trials=60,
            constraints=ParetoConstraints(floors_range=(3, 25)),
        )
        assert bundle.recommendations
        for rec in bundle.recommendations:
            cf = rec.params.get("cluster_floors")
            assert cf is not None and len(cf) == 2
