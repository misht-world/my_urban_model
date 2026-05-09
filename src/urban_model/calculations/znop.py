"""ЗНОП — кусочно-постоянная функция от КИТ."""

from __future__ import annotations

from urban_model.normatives import Normatives


def znop_per_person(kit: float, norms: Normatives) -> float:
    return norms.resolve("znop_per_person", kit=kit)


def znop_total_area(population: float, kit: float, norms: Normatives) -> float:
    per_person = znop_per_person(kit, norms)
    return population * per_person
