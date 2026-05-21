"""Тесты на стабильность warning-кодов (AUDIT P0-3).

Защищают от тихого регресса: если кто-то поправит текст warning в
forward.py, забыв оставить тег `[CODE]`, эти тесты упадут — а вместе
с ними и Optuna-feasibility, которая по этим кодам фильтрует.
"""

from __future__ import annotations

import pytest

from urban_model import verify_kit
from urban_model.calculations.warning_codes import WC, any_with_code, has_code, prefix
from urban_model.models import CalculationOptions, Site
from urban_model.models.social import KindergartenSpec, SchoolSpec
from urban_model.normatives import load_normatives


@pytest.fixture(scope="module")
def spb():
    return load_normatives("spb")


class TestWarningCodeHelpers:
    def test_prefix_wraps_message(self):
        s = prefix(WC.SOC_CAP_MIN_BELOW, "Hello")
        assert s.startswith("[SOC_CAP_MIN_BELOW] ")
        assert s.endswith("Hello")

    def test_has_code_matches(self):
        s = prefix(WC.KIT_ABOVE_LIMIT, "x")
        assert has_code(s, WC.KIT_ABOVE_LIMIT)
        assert not has_code(s, WC.DENSITY_ABOVE_LIMIT)

    def test_any_with_code_filters(self):
        ws = [prefix(WC.KIT_ABOVE_LIMIT, "a"), "untagged warning"]
        assert any_with_code(ws, WC.KIT_ABOVE_LIMIT)
        assert not any_with_code(ws, WC.SOC_CAP_MIN_BELOW)


class TestForwardEmitsCodes:
    """forward.py должен ставить теги при типовых ошибках."""

    def test_kit_above_limit_tagged(self, spb):
        """При КИТ > kit_max → warning с кодом KIT_ABOVE_LIMIT."""
        # planning_doc=False → kit_max=1.4; задаём kit=2.0 чтобы получить ERROR
        res = verify_kit(
            2.0,
            Site(area_m2=50_000),
            CalculationOptions(floors=12, planning_doc=False),
            spb,
        )
        assert any_with_code(res.warnings, WC.KIT_ABOVE_LIMIT)

    def test_density_above_limit_tagged(self, spb):
        """При очень высоком КИТ плотность пробивает 450 чел/га → DENSITY_ABOVE_LIMIT."""
        res = verify_kit(
            2.5,
            Site(area_m2=10_000),  # маленький квартал → высокая плотность
            CalculationOptions(floors=25, planning_doc=True),
            spb,
        )
        # Может пройти оба warning — kit и density. Достаточно одного.
        # Главное — что warning есть и помечен.
        if res.density_chel_per_ga.status.value == "error":
            assert any_with_code(res.warnings, WC.DENSITY_ABOVE_LIMIT)

    def test_soc_capacity_min_tagged(self, spb):
        """Малый квартал → ДОО получит вместимость ниже минимума → код."""
        # 5000 м² × kit=1.0 → ~3000 м² квартир → ~110 чел → ~7 мест ДОО
        # Это меньше минимума 120 → должен быть SOC_CAP_MIN_BELOW.
        res = verify_kit(
            1.0,
            Site(area_m2=5_000),
            CalculationOptions(
                floors=5,
                kindergarten=KindergartenSpec(num_objects=1),
            ),
            spb,
        )
        # Если требуется ≥ 1 ДОО и расчётно < 120 мест — будет код.
        if (res.kindergarten_places_accepted.value or 0) < 120:
            assert any_with_code(res.warnings, WC.SOC_CAP_MIN_BELOW)
