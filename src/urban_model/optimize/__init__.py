"""Optuna-оптимизация поверх обратного расчёта (Galapagos-аналог).

Цель: при заданных ограничениях найти комбинацию параметров (этажность,
доли парковок, число ДОО/СОШ и т.п.), при которой `solve_max_kit` даёт
максимальную площадь квартир.

Принцип:
    Каждое испытание Optuna:
      1. Сэмплирует значения decision-переменных из `SearchSpace`.
      2. Конструирует `CalculationOptions` поверх базового шаблона.
      3. Вызывает `solve_max_kit(site, options, norms)`.
      4. Возвращает apartments_area (или -inf при infeasible).

Optuna учится на TPE-семплере и сходится к лучшему сочетанию.

Публичный API:
    from urban_model.optimize import SearchSpace, optimize_max_apartments
"""

from urban_model.optimize.space import SearchSpace
from urban_model.optimize.runner import optimize_max_apartments, OptimizationResult

__all__ = ["SearchSpace", "optimize_max_apartments", "OptimizationResult"]
