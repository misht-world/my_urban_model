"""Правила округления из ТЗ §11."""

from __future__ import annotations

import math


def round_up_to_multiple(value: float, multiple: int) -> int:
    """Округлить вверх до ближайшего кратного `multiple`."""
    if multiple <= 0:
        raise ValueError(f"multiple должен быть > 0, дано {multiple}")
    return int(math.ceil(value / multiple) * multiple)


def ceil_int(value: float) -> int:
    return int(math.ceil(value))
