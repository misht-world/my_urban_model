"""Конвертация TEPResult в tabular-форматы (dict, DataFrame).

Два уровня детализации:
- result_to_dict(name, result)     → OrderedDict с ключевыми КПЭ (для сравнения)
- result_to_audit(result)          → список строк аудит-трейла (все поля)
- results_to_dataframe(pairs)      → сводный DataFrame (КПЭ × сценарии)
- results_to_audit_dataframe(pairs)→ длинный DataFrame аудита по всем сценариям
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

import pandas as pd

from urban_model.models.result import TEPField, TEPResult


# ---------------------------------------------------------------------------
# Ключевые КПЭ — строки сравнительной таблицы
# ---------------------------------------------------------------------------

_KPI_ROWS: list[tuple[str, str]] = [
    # (label, attr.subattr)
    ("КИТ", "kit"),
    ("КИТ норм. макс.", "kit_normative_max"),
    ("GFA, м²", "gfa"),
    ("Площадь квартир, м²", "apartments_area"),
    ("Население, чел", "population"),
    ("Плотность, чел/га", "density_chel_per_ga"),
    ("Плотность статус", "_density_status"),        # специальный случай
    ("ДОО мест (требуется)", "kindergarten_places_required"),
    ("ДОО мест (принято)", "kindergarten_places_accepted"),
    ("ДОО участок, м²", "kindergarten_plot_area"),
    ("СОШ мест (требуется)", "school_places_required"),
    ("СОШ мест (принято)", "school_places_accepted"),
    ("СОШ участок, м²", "school_plot_area"),
    ("ЗНОП, м²/чел", "znop_per_person"),
    ("ЗНОП итого, м²", "znop_area"),
    ("Парковки, м/м", "parking_required_places"),
    ("Откр. парковки, м/м", "parking_open_places"),
    ("Откр. парковки, м²", "parking_open_area"),
    ("Баланс территории, м²", "_balance_surplus"),  # специальный случай
    ("Баланс статус", "_balance_status"),            # специальный случай
    ("Ограничивающий фактор", "_limiting_factor"),  # специальный случай
]


def _get_value(result: TEPResult, attr: str) -> Any:
    """Получить значение по имени атрибута (поддерживает _-алиасы)."""
    if attr == "_density_status":
        return result.density_chel_per_ga.status.value
    if attr == "_balance_surplus":
        return round(result.balance.surplus, 1)
    if attr == "_balance_status":
        return "OK" if result.balance.is_feasible else "ДЕФИЦИТ"
    if attr == "_limiting_factor":
        return result.limiting_factor or ""
    field = getattr(result, attr)
    if isinstance(field, TEPField):
        v = field.value
        return round(v, 2) if isinstance(v, float) else v
    return field


def result_to_dict(name: str, result: TEPResult) -> OrderedDict:
    """КПЭ одного сценария — упорядоченный словарь {label: value}."""
    d: OrderedDict = OrderedDict()
    d["сценарий"] = name
    for label, attr in _KPI_ROWS:
        d[label] = _get_value(result, attr)
    return d


def results_to_dataframe(
    pairs: list[tuple[str, TEPResult]],
) -> pd.DataFrame:
    """Сводная таблица: строки — КПЭ, столбцы — сценарии.

    Args:
        pairs: список (название_сценария, TEPResult).

    Returns:
        DataFrame с мультиколоночным заголовком по сценариям.
    """
    rows = [result_to_dict(name, res) for name, res in pairs]
    df = pd.DataFrame(rows).set_index("сценарий").T
    df.index.name = "показатель"
    return df


# ---------------------------------------------------------------------------
# Аудит-трейл — все поля со source/formula
# ---------------------------------------------------------------------------

def result_to_audit(result: TEPResult) -> list[dict]:
    """Развернуть все TEPField в список строк аудит-трейла."""
    rows = []
    for field_name in type(result).model_fields:
        val = getattr(result, field_name)
        if not isinstance(val, TEPField):
            continue
        rows.append({
            "поле": field_name,
            "значение": val.value,
            "ед.": val.unit,
            "статус": val.status.value if val.status else None,
            "источник": val.source,
            "формула": val.formula,
        })
    return rows


def results_to_audit_dataframe(
    pairs: list[tuple[str, TEPResult]],
) -> pd.DataFrame:
    """Длинный DataFrame аудита: все поля всех сценариев в одной таблице."""
    frames = []
    for name, res in pairs:
        df = pd.DataFrame(result_to_audit(res))
        df.insert(0, "сценарий", name)
        frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
