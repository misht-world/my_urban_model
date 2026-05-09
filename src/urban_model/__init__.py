"""Математическая модель застройки территории — обратный расчёт ТЭП.

Публичный API v0.2:

    from urban_model import solve_max_kit, verify_kit, compare_scenarios, run_scenarios
    from urban_model.models import Site, CalculationOptions, Scenario
    from urban_model.normatives import load_normatives
    from urban_model.export import to_xlsx, results_to_dataframe

Пример:
    norms = load_normatives("spb")
    res = solve_max_kit(Site(area_m2=30_000), CalculationOptions(floors=12), norms)
    print(res.summary())
"""

__version__ = "0.2.0"

from urban_model.core.inverse import solve_max_kit
from urban_model.modes.verify import verify_kit
from urban_model.modes.compare import compare_scenarios, run_scenarios

__all__ = [
    "solve_max_kit",
    "verify_kit",
    "compare_scenarios",
    "run_scenarios",
]
