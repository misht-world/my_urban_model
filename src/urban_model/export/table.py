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

# Маркер строки-заголовка секции (v0.16.2, п.4 Михаила): визуально группирует
# показатели одной категории. Значений у такой строки нет (пустые ячейки) —
# это переживают ВСЕ рендеры df (st.dataframe / xlsx / PPTX); альбом
# дополнительно подкрашивает такие строки (KPI_SECTION_LABELS).
_SECTION = "__section__"

_KPI_ROWS: list[tuple[str, str]] = [
    # (label, attr.subattr)
    ("ОСНОВНЫЕ ПОКАЗАТЕЛИ", _SECTION),
    ("КИТ", "kit"),
    ("КИТ норм. макс.", "kit_normative_max"),
    # v0.16.2 (п.3): эконом-индекс — перед GFA (ключевая метрика выше).
    ("Эконом-индекс (100=окуп.)", "_economy_index"),  # специальный случай
    ("GFA, м²", "gfa"),
    ("Площадь квартир, м²", "apartments_area"),
    ("Население, чел", "population"),
    ("Плотность, чел/га", "density_chel_per_ga"),
    ("Плотность статус", "_density_status"),        # специальный случай
    ("ДЕТСКИЕ САДЫ (ДОО)", _SECTION),
    ("ДОО мест (требуется)", "kindergarten_places_required"),
    ("ДОО мест (принято)", "kindergarten_places_accepted"),
    ("ДОО участок, м²", "kindergarten_plot_area"),
    ("ШКОЛЫ (СОШ)", _SECTION),
    ("СОШ мест (требуется)", "school_places_required"),
    ("СОШ мест (принято)", "school_places_accepted"),
    ("СОШ участок, м²", "school_plot_area"),
    ("ЗНОП И ОЗЕЛЕНЕНИЕ", _SECTION),
    ("ЗНОП, м²/чел", "znop_per_person"),
    ("ЗНОП итого, м²", "znop_area"),
    ("ПАРКОВКИ", _SECTION),
    ("Парковки всего, м/м", "parking_required_places"),
    ("Откр. парковки, м/м", "parking_open_places"),
    ("Откр. парковки, м²", "parking_open_area"),
    ("Многоуровн. парковки, м/м", "parking_multilevel_places"),
    ("Многоуровн. паркинги, шт", "parking_multilevel_objects"),
    ("Многоуровн. паркинги, м²", "parking_multilevel_area"),
    ("Подземные парковки, м/м", "parking_underground_places"),
    ("БАЛАНС ТЕРРИТОРИИ", _SECTION),
    # v0.16.2 (п.5): строка «Баланс территории, м²» (surplus) убрана — модель
    # обратным ходом всегда жмёт резерв к нулю, число не информативно;
    # достаточно статуса. (п.6) статус переименован.
    ("Статус баланса территории", "_balance_status"),  # специальный случай
    ("Ограничивающий фактор", "_limiting_factor"),  # специальный случай
]

# Публичный набор строк-заголовков секций — для стилизации в рендерах
# (альбом PPTX подкрашивает их серым и делает жирными).
KPI_SECTION_LABELS: frozenset[str] = frozenset(
    lab for lab, attr in _KPI_ROWS if attr == _SECTION
)


def _get_value(result: TEPResult, attr: str) -> Any:
    """Получить значение по имени атрибута (поддерживает _-алиасы)."""
    if attr == _SECTION:
        return ""            # строка-заголовок секции — без значений
    if attr == "_density_status":
        return result.density_chel_per_ga.status.value
    if attr == "_balance_status":
        return "OK" if result.balance.is_feasible else "ДЕФИЦИТ"
    if attr == "_limiting_factor":
        return result.limiting_factor or ""
    if attr == "_economy_index":
        # Экономика может быть отключена (include_economy=False) → None.
        econ = getattr(result, "economy", None)
        return round(econ.economy_index, 1) if econ is not None else None
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
