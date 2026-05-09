"""Баланс территории квартала."""

from __future__ import annotations

from urban_model.models.result import BalanceCheck


def compute_balance(site_area: float, components: dict[str, float]) -> BalanceCheck:
    required = sum(components.values())
    surplus = site_area - required
    return BalanceCheck(
        site_area=site_area,
        required_total=required,
        surplus=surplus,
        components=components,
        is_feasible=surplus >= 0,
    )
