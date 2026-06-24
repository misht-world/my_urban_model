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


def split_to_allowed_capacities(
    total_places: int,
    allowed: list[int],
    capacity_min: int | None = None,
) -> list[int]:
    """Разбить спрос на объекты СТАНДАРТНЫХ вместимостей (СП / письмо КОБр).

    Используется в режиме «только нормативная наполняемость»: вместимость
    каждого объекта берётся ТОЛЬКО из списка `allowed` (типовые размеры),
    а не произвольным кратным. Цель — минимум объектов с минимальным
    профицитом мест, при вместимости ≥ capacity_min.

    Логика:
      n = ceil(target / max_allowed), где target = max(спрос, capacity_min);
      на каждый объект — наименьшая разрешённая вместимость ≥ target/n
      (и ≥ capacity_min). Объекты одинаковой типовой вместимости.
    """
    if total_places <= 0 or not allowed:
        return []
    allowed_sorted = sorted(set(int(a) for a in allowed))
    cap_max = allowed_sorted[-1]
    floor = capacity_min or 0
    target = max(int(total_places), floor)
    n = max(1, -(-target // cap_max))  # ceil
    per = -(-target // n)              # ceil(target / n)
    need = max(per, floor)
    # наименьшая разрешённая вместимость ≥ need (иначе максимальная)
    cap = next((a for a in allowed_sorted if a >= need), cap_max)
    return [cap] * n


def split_to_allowed_capacities_n(
    total_places: int,
    allowed: list[int],
    n_objects: int,
    capacity_min: int | None = None,
) -> list[int]:
    """Разбить спрос на РОВНО `n_objects` объектов СТАНДАРТНЫХ вместимостей.

    Режим «только нормативная наполняемость» + заданное пользователем число
    объектов: каждый корпус — из списка `allowed` (типовые СП/КОБр), а суммарная
    вместимость ≥ спроса при МИНИМАЛЬНОМ профиците (и максимальной равномерности
    при равном профиците). В отличие от `split_to_allowed_capacities` число
    объектов здесь фиксировано пользователем, а не минимизируется.

    Пример: спрос 2250, n=2, типовые [550..2475] → [1375, 1100] (Σ=2475).
    """
    from itertools import combinations_with_replacement

    if n_objects <= 0 or total_places <= 0 or not allowed:
        return []
    allowed_sorted = sorted(set(int(a) for a in allowed))
    floor = capacity_min or 0
    cand = [a for a in allowed_sorted if a >= floor] or [allowed_sorted[-1]]

    best_key = None
    best_combo: tuple[int, ...] | None = None
    for combo in combinations_with_replacement(cand, n_objects):
        s = sum(combo)
        if s < total_places:
            continue
        # минимум суммы (профицита), затем минимум разброса (равномернее)
        key = (s, max(combo) - min(combo))
        if best_key is None or key < best_key:
            best_key, best_combo = key, combo

    if best_combo is None:
        # спрос превышает n × max_допустимой → все объекты максимальные
        return [cand[-1]] * n_objects
    return sorted(best_combo, reverse=True)


def choose_n_objects(
    total_places: int,
    capacity_min: int | None,
    capacity_max: int,
) -> int:
    """Выбрать минимальное число объектов так, чтобы:
    - вместимость каждого объекта ≤ `capacity_max`
    - вместимость каждого объекта ≥ `capacity_min` (если задан)

    Если `capacity_min` мешает (total_places < capacity_min), возвращает 1.

    Raises:
        ValueError: если `capacity_min > capacity_max` — нормативы
            противоречат друг другу.
    """
    if capacity_min is not None and capacity_max is not None and capacity_min > capacity_max:
        raise ValueError(
            f"capacity_min ({capacity_min}) > capacity_max ({capacity_max}) — "
            "нормативы противоречат друг другу. Проверьте YAML."
        )
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
