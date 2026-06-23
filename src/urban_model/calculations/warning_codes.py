"""Коды предупреждений в TEPResult.warnings (AUDIT P0-3).

Каждое сообщение начинается с тега `[<CODE>] ...` — это позволяет
фильтровать warnings машиночитаемо (Optuna feasibility, отчёты), не
завися от русского текста, который может меняться.

Использование:
    from urban_model.calculations.warning_codes import WC, prefix

    warnings.append(prefix(WC.SOC_CAP_MIN_BELOW, "ДОО: …"))

    # в runner.py:
    if any_with_code(warnings, WC.SOC_CAP_MIN_BELOW, WC.SOC_CAP_MAX_ABOVE):
        return False
"""

from __future__ import annotations

from enum import Enum


class WC(str, Enum):
    """Warning codes — стабильные идентификаторы для фильтрации."""

    # Соцобъекты: вместимость одного объекта меньше минимально допустимой
    # (для ДОО — capacity_min built_in; для СОШ — capacity_min detached).
    SOC_CAP_MIN_BELOW = "SOC_CAP_MIN_BELOW"

    # ДОО detached: вместимость между «минимум built_in» и «минимум detached»
    # — рекомендация выбрать тип «встроенный».
    SOC_CAP_MIN_DETACHED_HINT = "SOC_CAP_MIN_DETACHED_HINT"

    # Вместимость объекта превышает разрешённый максимум.
    SOC_CAP_MAX_ABOVE = "SOC_CAP_MAX_ABOVE"

    # Вместимость не из списка типовых (allowed_capacities).
    SOC_CAP_NOT_TYPICAL = "SOC_CAP_NOT_TYPICAL"

    # Плотность подземки выше норматива capacity_per_level.
    PARKING_UG_OVERPACKED = "PARKING_UG_OVERPACKED"

    # Открытых м/м меньше норматива (≥12.5%) — обычно из-за слишком большой
    # доли стилобата/закрытых типов, не оставивших места под открытые.
    PARKING_OPEN_BELOW_MIN = "PARKING_OPEN_BELOW_MIN"

    # ЗНОП (площадь/м²-чел) ниже минимума ПЗЗ для достигнутого КИТ —
    # обычно при ручном override площади ЗНОП.
    ZNOP_BELOW_MIN = "ZNOP_BELOW_MIN"

    # Плотность населения превышает норматив (450 чел/га для многоэтажки).
    DENSITY_ABOVE_LIMIT = "DENSITY_ABOVE_LIMIT"

    # КИТ выше нормативного потолка.
    KIT_ABOVE_LIMIT = "KIT_ABOVE_LIMIT"

    # Σ площадей кластеров этажности существенно расходится с S_квартала.
    CLUSTER_AREA_MISMATCH = "CLUSTER_AREA_MISMATCH"


def prefix(code: WC, message: str) -> str:
    """Префиксирует сообщение тегом `[CODE] `."""
    return f"[{code.value}] {message}"


def has_code(warning: str, *codes: WC) -> bool:
    """True, если строка warning начинается с тега любого из переданных кодов."""
    return any(warning.startswith(f"[{c.value}]") for c in codes)


def any_with_code(warnings_list: list[str], *codes: WC) -> bool:
    """True, если в списке warnings есть хотя бы одна строка с одним из кодов."""
    return any(has_code(w, *codes) for w in warnings_list)


def strip_code(warning: str) -> str:
    """Убрать ведущий тег `[CODE] ` для отображения пользователю."""
    if warning.startswith("[") and "] " in warning:
        return warning.split("] ", 1)[1]
    return warning
