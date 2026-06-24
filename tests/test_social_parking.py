"""Тесты парковок ДОО и СОШ (v0.7.0).

Формула ПЗЗ СПб:
    parking_per_object = max(minimum, ceil(workers / 5) + ceil(students / 100))
    minimum = 2

Данные «workers по capacity» — из Excel КС (piecewise в spb.yaml).
"""

from __future__ import annotations

import math

import pytest

from urban_model import verify_kit
from urban_model.calculations import social_parking
from urban_model.models import CalculationOptions, Site
from urban_model.models.social import KindergartenSpec, SchoolSpec
from urban_model.normatives import load_normatives


@pytest.fixture(scope="module")
def spb():
    return load_normatives("spb")


@pytest.fixture
def site():
    return Site(area_m2=100_000)


# ---------------------------------------------------------------------------
# Чистая функция расчёта одного объекта
# ---------------------------------------------------------------------------

class TestParkingPerObject:
    """Проверка формулы max(2, ceil(work/5) + ceil(stud/100))."""

    def test_kg_160_places(self, spb):
        """ДОО 160 мест: workers=50 → 50/5 + 160/100 = 10 + 2 = 12."""
        # workers для 160 = 50 (из piecewise КС)
        workers = spb.resolve(
            "social_objects.kindergarten.workers_per_capacity", capacity=160
        )
        assert workers == 50
        places = social_parking.parking_for_object(50, 160, spb)
        assert places == 12  # ceil(50/5) + ceil(160/100) = 10 + 2

    def test_kg_220_places(self, spb):
        """ДОО 220 мест: workers=65 → 65/5 + 220/100 = 13 + 3 = 16."""
        workers = spb.resolve(
            "social_objects.kindergarten.workers_per_capacity", capacity=220
        )
        assert workers == 65
        places = social_parking.parking_for_object(65, 220, spb)
        assert places == 16

    def test_school_550(self, spb):
        """СОШ II параллель (550): workers=90 → 90/5 + 550/100 = 18 + 6 = 24."""
        workers = spb.resolve(
            "social_objects.school.workers_per_capacity", capacity=550
        )
        assert workers == 90
        places = social_parking.parking_for_object(90, 550, spb)
        assert places == 24  # ceil(90/5)=18 + ceil(550/100)=6 = 24

    def test_school_1100(self, spb):
        """СОШ IV (1100): workers=178 → ceil(178/5)=36 + ceil(1100/100)=11 = 47."""
        workers = spb.resolve(
            "social_objects.school.workers_per_capacity", capacity=1100
        )
        assert workers == 178
        places = social_parking.parking_for_object(178, 1100, spb)
        # ceil(178/5) = 36; ceil(1100/100) = 11; sum = 47
        assert places == 47

    def test_minimum_2(self, spb):
        """Очень маленький объект (workers=1, students=10): сумма=1+1=2 → min=2."""
        places = social_parking.parking_for_object(1, 10, spb)
        assert places == 2

    def test_minimum_kicks_in(self, spb):
        """Workers=0, students=0 → max(2, 0) = 2."""
        places = social_parking.parking_for_object(0, 0, spb)
        assert places == 2


# ---------------------------------------------------------------------------
# Множественные объекты + интеграция
# ---------------------------------------------------------------------------

class TestSocialParkingCompute:
    def test_kg_two_objects(self, spb):
        """Два ДОО по 160 мест: 12 + 12 = 24 м/м."""
        br = social_parking.compute([160, 160], [], spb)
        assert br.kindergarten_places == 24
        assert br.school_places == 0
        assert br.total_places == 24
        assert len(br.kindergarten_details) == 2

    def test_kg_plus_school(self, spb):
        """ДОО 220 + СОШ 825: суммируется."""
        br = social_parking.compute([220], [825], spb)
        # ДОО 220: 65 раб → 13 + 3 = 16
        # СОШ 825: 135 раб → ceil(135/5)=27 + ceil(825/100)=9 = 36
        assert br.kindergarten_places == 16
        assert br.school_places == 36
        assert br.total_places == 52

    def test_kg_excluded(self, spb):
        """kg_include=False → ДОО парковки = 0."""
        br = social_parking.compute([160], [550], spb, kg_include=False)
        assert br.kindergarten_places == 0
        assert br.school_places == 24
        assert br.total_places == 24

    def test_empty(self, spb):
        """Нет объектов — нет парковок."""
        br = social_parking.compute([], [], spb)
        assert br.total_places == 0


# ---------------------------------------------------------------------------
# Интеграция в forward.py / TEPResult
# ---------------------------------------------------------------------------

class TestSocialParkingInResult:
    def test_fields_populated(self, spb, site):
        """В результате есть social_parking_* поля."""
        res = verify_kit(1.5, site, CalculationOptions(floors=12), spb)
        assert res.social_parking_total.value > 0
        assert res.social_parking_kindergarten.value > 0
        assert res.social_parking_school.value > 0
        assert (
            res.social_parking_total.value
            == res.social_parking_kindergarten.value
            + res.social_parking_school.value
        )

    def test_separate_from_housing_pool(self, spb, site):
        """Парковки соцобъектов НЕ вливаются в общий пул жилищных
        (parking_required_places не включает соц-парковки)."""
        res_no = verify_kit(
            1.5, site,
            CalculationOptions(
                floors=12,
                include_kindergarten=False,
                include_school=False,
            ),
            spb,
        )
        res_yes = verify_kit(1.5, site, CalculationOptions(floors=12), spb)
        # Жилищные парковки одинаковы (зависят только от apartments_area)
        # Соц-парковки идут отдельно: смотрим social_parking_total
        assert res_yes.parking_required_places.value == res_no.parking_required_places.value
        assert res_yes.social_parking_total.value > 0
        assert res_no.social_parking_total.value == 0

    def test_social_parking_area_in_balance(self, spb, site):
        """social_parking_plot = отдельный компонент в balance."""
        # include_add_education=False: парковка доп. обр. иначе вливается в
        # social_parking_area, и равенство с social_parking_total (только ДОО+СОШ)
        # перестаёт держаться (v0.12.15).
        res = verify_kit(
            1.5, site, CalculationOptions(floors=12, include_add_education=False), spb
        )
        assert "social_parking_plot" in res.balance.components
        assert res.balance.components["social_parking_plot"] > 0
        # Площадь = м/м × 20.75 (СПб норматив открытой парковки)
        expected = res.social_parking_total.value * 20.75
        assert res.balance.components["social_parking_plot"] == pytest.approx(expected)
        # Совпадает с social_parking_area
        assert res.social_parking_area.value == pytest.approx(expected)

    def test_only_demand_kg_zeroes_kg_parking(self, spb, site):
        """only_demand для ДОО → парковки ДОО не считаются."""
        res = verify_kit(
            1.5, site,
            CalculationOptions(
                floors=12,
                kindergarten=KindergartenSpec(only_demand=True),
            ),
            spb,
        )
        assert res.social_parking_kindergarten.value == 0
        # СОШ-парковки при этом остаются
        assert res.social_parking_school.value > 0

    def test_only_demand_school_zeroes_school_parking(self, spb, site):
        """only_demand для СОШ → парковки СОШ не считаются."""
        res = verify_kit(
            1.5, site,
            CalculationOptions(
                floors=12,
                school=SchoolSpec(only_demand=True),
            ),
            spb,
        )
        assert res.social_parking_school.value == 0
        assert res.social_parking_kindergarten.value > 0

    def test_include_parking_false_zeros_everything(self, spb, site):
        """include_parking=False → парковки соцобъектов тоже = 0."""
        res = verify_kit(
            1.5, site,
            CalculationOptions(floors=12, include_parking=False),
            spb,
        )
        assert res.social_parking_total.value == 0


# ---------------------------------------------------------------------------
# Нормативы
# ---------------------------------------------------------------------------

class TestSocialParkingNormatives:
    def test_per_worker_is_5(self, spb):
        assert spb.resolve("parking.social_objects.per_worker") == 5

    def test_per_student_is_100(self, spb):
        assert spb.resolve("parking.social_objects.per_student") == 100

    def test_minimum_is_2(self, spb):
        assert spb.resolve("parking.social_objects.minimum") == 2

    def test_sources_present(self, spb):
        assert spb.source_of("parking.social_objects.per_worker")
        assert spb.source_of("parking.social_objects.per_student")
        assert spb.source_of("parking.social_objects.minimum")

    def test_kg_workers_lookup_exact_values(self, spb):
        """Точные значения работников КС: 160→50, 220→65, 250→75, 350→108."""
        for cap, expected in [(160, 50), (220, 65), (250, 75), (350, 108)]:
            assert spb.resolve(
                "social_objects.kindergarten.workers_per_capacity", capacity=cap
            ) == expected

    def test_school_workers_lookup_exact_values(self, spb):
        """Точные значения работников КС: 550→90, 825→135, 1375→220, 2475→390."""
        for cap, expected in [(550, 90), (825, 135), (1375, 220), (2475, 390)]:
            assert spb.resolve(
                "social_objects.school.workers_per_capacity", capacity=cap
            ) == expected
