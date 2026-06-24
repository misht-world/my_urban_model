"""Тесты v0.12.27 — режимы финансирования соцобъектов (за чей счёт ДОО/СОШ/доп.обр)."""

from __future__ import annotations

import pytest

from urban_model import solve_max_kit
from urban_model.models import CalculationOptions, Site
from urban_model.normatives import load_normatives


@pytest.fixture(scope="module")
def spb():
    return load_normatives("spb")


def _econ(spb, **kw):
    o = CalculationOptions(floors=16, planning_doc=True, **kw)
    return solve_max_kit(Site(area_m2=200_000), o, spb).economy


def test_compensated_default_share(spb):
    """compensated без share → норматив YAML (0.7) от себестоимости соц-зданий."""
    e = _econ(spb, social_funding="compensated")
    soc = e.cost.kindergarten + e.cost.school + e.cost.add_education
    share = spb.resolve("economy.social_compensation.share")
    assert e.revenue.social_compensation == pytest.approx(soc * share, rel=1e-3)


def test_compensated_custom_share(spb):
    e = _econ(spb, social_funding="compensated", social_compensation_share=0.5)
    soc = e.cost.kindergarten + e.cost.school + e.cost.add_education
    assert e.revenue.social_compensation == pytest.approx(soc * 0.5, rel=1e-3)


def test_developer_no_compensation(spb):
    """developer → компенсация 0, вся соц-себестоимость в нагрузке."""
    e = _econ(spb, social_funding="developer")
    assert e.revenue.social_compensation == 0.0
    soc = e.cost.kindergarten + e.cost.school + e.cost.add_education
    assert soc > 0
    assert e.net_social_burden >= soc - 1e-6  # + соц-парковки


def test_city_zero_social_cost(spb):
    """city → соц-здания строит город: себест. соц = 0, компенсация 0."""
    e = _econ(spb, social_funding="city")
    assert e.cost.kindergarten == 0.0
    assert e.cost.school == 0.0
    assert e.cost.add_education == 0.0
    assert e.revenue.social_compensation == 0.0


def test_at_cost_neutral(spb):
    """at_cost → компенсация = 100% себестоимости → соц-нагрузка ≈ соц-парковки."""
    e = _econ(spb, social_funding="at_cost")
    soc = e.cost.kindergarten + e.cost.school + e.cost.add_education
    assert e.revenue.social_compensation == pytest.approx(soc, rel=1e-3)
    # нагрузка близка к нулю (остаются только соц-парковки)
    assert abs(e.net_social_burden) <= e.cost.social_parking + 1e-3


def test_index_monotonic_by_funding(spb):
    """Индекс: developer < compensated < at_cost ≤ city (меньше нагрузки → выше)."""
    i_dev = _econ(spb, social_funding="developer").economy_index
    i_cmp = _econ(spb, social_funding="compensated").economy_index
    i_atc = _econ(spb, social_funding="at_cost").economy_index
    i_city = _econ(spb, social_funding="city").economy_index
    assert i_dev < i_cmp < i_atc <= i_city + 1e-6
