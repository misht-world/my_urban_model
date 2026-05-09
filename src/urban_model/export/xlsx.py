"""Экспорт результатов в Excel (.xlsx).

Структура книги:
  Лист «Сравнение»   — сводная таблица КПЭ по всем сценариям; ячейки окрашены
                        по статусу (зелёный / жёлтый / красный).
  Лист «Аудит»       — длинная таблица: все поля × все сценарии со source/formula.
  Лист «Сценарий N»  — (опционально) краткая сводка каждого сценария.

Использование:
    from urban_model.export import to_xlsx
    to_xlsx(pairs, "output/tep_report.xlsx")
"""

from __future__ import annotations

import pathlib
from typing import Union

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from urban_model.export.table import results_to_audit_dataframe, results_to_dataframe
from urban_model.models.result import TEPResult

# ---------------------------------------------------------------------------
# Палитра статусов (Excel ColorIndex-совместимые HEX без #)
# ---------------------------------------------------------------------------
_FILL = {
    "ok":       PatternFill("solid", fgColor="C6EFCE"),   # светло-зелёный
    "warning":  PatternFill("solid", fgColor="FFEB9C"),   # светло-жёлтый
    "error":    PatternFill("solid", fgColor="FFC7CE"),   # светло-красный
    "manual":   PatternFill("solid", fgColor="BDD7EE"),   # светло-синий
    "no_data":  PatternFill("solid", fgColor="E2EFDA"),   # очень светлый
    "ДЕФИЦИТ":  PatternFill("solid", fgColor="FFC7CE"),
    "OK":       PatternFill("solid", fgColor="C6EFCE"),
}
_FILL_HEADER = PatternFill("solid", fgColor="2F5496")   # тёмно-синий
_FILL_LABEL  = PatternFill("solid", fgColor="D6DCE4")   # серый

_FONT_HEADER = Font(bold=True, color="FFFFFF", size=11)
_FONT_LABEL  = Font(bold=True, size=10)
_FONT_BODY   = Font(size=10)

_THIN = Side(style="thin", color="999999")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)

_STATUS_ROWS = {"Плотность статус", "Баланс статус"}


def _auto_width(ws, min_col: int = 1, min_width: int = 12, max_width: int = 60) -> None:
    """Подогнать ширину столбцов по содержимому."""
    for col_cells in ws.iter_cols():
        col_idx = col_cells[0].column
        if col_idx < min_col:
            continue
        max_len = max(
            (len(str(c.value)) if c.value is not None else 0) for c in col_cells
        )
        ws.column_dimensions[get_column_letter(col_idx)].width = max(
            min_width, min(max_len + 2, max_width)
        )


def _write_comparison_sheet(wb: Workbook, pairs: list[tuple[str, TEPResult]]) -> None:
    ws = wb.active
    ws.title = "Сравнение"

    df = results_to_dataframe(pairs)
    scenario_names = [name for name, _ in pairs]

    # --- Заголовок ---
    ws.append(["Показатель"] + scenario_names)
    header_row = ws[1]
    for cell in header_row:
        cell.fill = _FILL_HEADER
        cell.font = _FONT_HEADER
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = _BORDER
    ws.row_dimensions[1].height = 28

    # --- Данные ---
    # Соберём статусы по сценарию для каждой строки индекса
    status_map: dict[str, list[str]] = {
        "Плотность статус": [
            res.density_chel_per_ga.status.value for _, res in pairs
        ],
        "Баланс статус": [
            "OK" if res.balance.is_feasible else "ДЕФИЦИТ" for _, res in pairs
        ],
    }

    for row_idx, (label, row_data) in enumerate(df.iterrows(), start=2):
        ws.cell(row=row_idx, column=1, value=label).fill = _FILL_LABEL
        ws.cell(row=row_idx, column=1).font = _FONT_LABEL
        ws.cell(row=row_idx, column=1).border = _BORDER

        statuses = status_map.get(str(label), [None] * len(scenario_names))

        for col_idx, (val, status_str) in enumerate(zip(row_data, statuses), start=2):
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.font = _FONT_BODY
            cell.border = _BORDER
            cell.alignment = Alignment(horizontal="center")
            # Окрашиваем строки-статусов
            if str(label) in _STATUS_ROWS and status_str in _FILL:
                cell.fill = _FILL[status_str]
            # Для остальных строк — только значение

    _auto_width(ws)
    ws.freeze_panes = "B2"


def _write_audit_sheet(wb: Workbook, pairs: list[tuple[str, TEPResult]]) -> None:
    ws = wb.create_sheet("Аудит")
    df = results_to_audit_dataframe(pairs)
    if df.empty:
        return

    # Заголовок
    cols = list(df.columns)
    ws.append(cols)
    for cell in ws[1]:
        cell.fill = _FILL_HEADER
        cell.font = _FONT_HEADER
        cell.border = _BORDER
        cell.alignment = Alignment(horizontal="center")

    # Данные
    for _, row in df.iterrows():
        ws.append(list(row))
        data_row = ws[ws.max_row]
        status_val = str(row.get("статус", "")).lower()
        fill = _FILL.get(status_val)
        for cell in data_row:
            cell.font = _FONT_BODY
            cell.border = _BORDER
            if fill and cell.column_letter != "A":
                cell.fill = fill

    _auto_width(ws)
    ws.freeze_panes = "A2"


def to_xlsx(
    pairs: list[tuple[str, TEPResult]],
    path: Union[str, pathlib.Path],
) -> pathlib.Path:
    """Записать результаты расчётов в Excel-файл.

    Args:
        pairs: список (название_сценария, TEPResult). Порядок определяет порядок
               столбцов в листе «Сравнение» и строк в листе «Аудит».
        path:  Путь к выходному файлу (.xlsx). Директория должна существовать.

    Returns:
        pathlib.Path к записанному файлу.

    Example:
        >>> from urban_model.export import to_xlsx
        >>> to_xlsx([("Малый", res1), ("Большой", res2)], "report.xlsx")
    """
    path = pathlib.Path(path)
    wb = Workbook()

    _write_comparison_sheet(wb, pairs)
    _write_audit_sheet(wb, pairs)

    wb.save(path)
    return path
