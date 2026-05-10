"""Равномерное распределение мест по объектам с соблюдением кратности.

Используется для разбивки суммарного количества мест в ДОО/СОШ по
отдельным зданиям. Логика:

1. Каждый объект имеет вместимость кратную `multiple` (5 для ДОО, 10 для СОШ).
2. Распределение должно быть максимально равномерным: между крайними
   объектами разница либо 0, либо ровно `multiple`.
3. Сумма вместимостей объектов = `total_places` (если кратно `multiple`).
"""

from __future__ import annotations


def distribute_places_evenly(
    total_places: int,
    n_objects: int,
    multiple: int,
) -> list[int]:
    """Распределить `total_places` мест по `n_objects` объектам максимально
    равномерно, сохранив кратность `multiple` для каждого объекта.

    Args:
        total_places: общее число мест (предполагается кратным `multiple`).
        n_objects:    количество объектов (≥ 1).
        multiple:     требуемая кратность вместимости объекта.

    Returns:
        Список длиной `n_objects` с вместимостями (отсортирован по убыванию).
        Сумма списка == total_places (если total_places кратно multiple).

    Examples:
        >>> distribute_places_evenly(800, 3, multiple=5)
        [270, 265, 265]
        >>> distribute_places_evenly(1500, 3, multiple=10)
        [500, 500, 500]
        >>> distribute_places_evenly(820, 3, multiple=10)
        [280, 270, 270]
    """
    if n_objects <= 0:
        return []
    if total_places <= 0:
        return [0] * n_objects
    if n_objects == 1:
        return [total_places]

    # Считаем «единицы» — кратные multiple.
    # total_places должно быть кратно multiple (округлено выше по стеку).
    units = total_places // multiple
    base_units = units // n_objects
    remainder = units - base_units * n_objects

    # `remainder` объектов получают (base+1) * multiple, остальные base * multiple
    bigger = (base_units + 1) * multiple
    smaller = base_units * multiple
    result = [bigger] * remainder + [smaller] * (n_objects - remainder)
    return result


def choose_n_objects(
    total_places: int,
    capacity_min: int | None,
    capacity_max: int,
) -> int:
    """Выбрать минимальное число объектов так, чтобы:
    - вместимость каждого объекта ≤ `capacity_max`
    - вместимость каждого объекта ≥ `capacity_min` (если задан)

    Если `capacity_min` мешает (total_places < capacity_min), возвращает 1.
    """
    if total_places <= 0:
        return 1
    # Минимальное число объектов, чтобы влезть в capacity_max
    n_min = (total_places + capacity_max - 1) // capacity_max  # ceil division

    # Если capacity_min задан — проверяем, что при n_min среднее ≥ capacity_min
    if capacity_min and n_min > 1:
        # Уменьшаем n до тех пор, пока base ≥ capacity_min или n=1
        while n_min > 1 and total_places // n_min < capacity_min:
            n_min -= 1
    return max(n_min, 1)
