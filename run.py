"""
Интерактивный расчёт КИТ/ТЭП — Санкт-Петербург.
Запуск: uv run python run.py   или   python run.py (в .venv)
"""

from __future__ import annotations

import sys
import os

# Форсируем UTF-8 для stdout/stderr на Windows
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Убедимся, что src/ виден (для запуска вне установленного окружения)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from urban_model import solve_max_kit, solve_max_kit_with_reserve, solve_max_kit_with_znop, verify_kit
from urban_model.models import Site, CalculationOptions, SchoolSpec
from urban_model.models.built_in import BuiltInArea
from urban_model.normatives import load_normatives


# ---------------------------------------------------------------------------
# Утилиты вывода
# ---------------------------------------------------------------------------

SEP = "─" * 60

def hr(): print(SEP)

def ask_float(prompt: str, default: float | None = None) -> float:
    suffix = f" [{default}]" if default is not None else ""
    while True:
        raw = input(f"  {prompt}{suffix}: ").strip()
        if raw == "" and default is not None:
            return default
        try:
            return float(raw.replace(",", "."))
        except ValueError:
            print("  ⚠  Введите число.")


def ask_int(prompt: str, default: int | None = None) -> int:
    suffix = f" [{default}]" if default is not None else ""
    while True:
        raw = input(f"  {prompt}{suffix}: ").strip()
        if raw == "" and default is not None:
            return default
        try:
            return int(raw)
        except ValueError:
            print("  ⚠  Введите целое число.")


def ask_bool(prompt: str, default: bool = False) -> bool:
    hint = "[Д/н]" if default else "[д/Н]"
    raw = input(f"  {prompt} {hint}: ").strip().lower()
    if raw == "":
        return default
    return raw in ("д", "y", "yes", "да", "1", "true")


def print_result(r, title: str = "Результат") -> None:
    hr()
    print(f"  {title}")
    hr()
    # Используем встроенный summary() и добавляем отступ
    for line in r.summary().splitlines():
        print(f"  {line}")
    hr()


# ---------------------------------------------------------------------------
# Сборка параметров квартала
# ---------------------------------------------------------------------------

def input_site() -> Site:
    print("\n  — Квартал —")
    area = ask_float("Площадь квартала, м²", 50_000)
    return Site(area_m2=area)


def input_options() -> CalculationOptions:
    print("\n  — Параметры расчёта —")
    floors        = ask_int("Этажность", 12)
    planning_doc  = ask_bool("Есть документ планировки (ПД)?", False)
    has_pool      = ask_bool("СОШ с бассейном?", False)
    has_sport     = ask_bool("СОШ со спортивным ядром?", False)

    use_vpp = ask_bool("Учесть ВПП (встроенно-пристроенные помещения)?", False)
    built_in = None
    if use_vpp:
        vpp_area = ask_float("Площадь ВПП, м²", 2_000)
        print("  ВРИ-коды: 2.6 жильё | 4.4 магазины | 4.6 общепит | 3.3 бытовые услуги")
        vri = input("  ВРИ-код ВПП [4.4]: ").strip() or "4.4"
        built_in = BuiltInArea(area_m2=vpp_area, vri_code=vri)

    return CalculationOptions(
        floors=floors,
        planning_doc=planning_doc,
        school=SchoolSpec(has_pool=has_pool, has_sport_core=has_sport),
        built_in=built_in,
    )


# ---------------------------------------------------------------------------
# Меню режимов
# ---------------------------------------------------------------------------

MENU = """
  Выберите режим расчёта:
    1  Максимальный КИТ  (стандартный)
    2  КИТ с резервом территории
    3  КИТ при заданном ЗНОП
    4  Проверка конкретного КИТ
    0  Выход
"""

def mode_max_kit(norms):
    site = input_site()
    opts = input_options()
    print("\n  Считаем...")
    r = solve_max_kit(site, opts, norms)
    print_result(r, "Максимальный КИТ")

def mode_with_reserve(norms):
    site = input_site()
    opts = input_options()
    target = ask_float("Целевой резерв территории, м²", 5_000)
    print("\n  Считаем...")
    r = solve_max_kit_with_reserve(site, target, opts, norms)
    print_result(r, f"КИТ с резервом ≥ {target:,.0f} м²")

def mode_with_znop(norms):
    site = input_site()
    opts = input_options()
    znop = ask_float("Требуемый ЗНОП, м²/чел", 6.0)
    print("\n  Считаем...")
    r = solve_max_kit_with_znop(site, znop, opts, norms)
    print_result(r, f"КИТ при ЗНОП = {znop} м²/чел")

def mode_verify(norms):
    site = input_site()
    opts = input_options()
    kit = ask_float("КИТ для проверки", 1.5)
    print("\n  Считаем...")
    r = verify_kit(kit, site, opts, norms)
    print_result(r, f"Проверка КИТ = {kit}")

def mode_export(norms):
    """Быстрый расчёт + сохранение xlsx."""
    from urban_model.export import to_xlsx

    site = input_site()
    opts = input_options()
    print("\n  Считаем все режимы...")
    r_max   = solve_max_kit(site, opts, norms)
    r_res   = solve_max_kit_with_reserve(site, 5_000, opts, norms)
    r_znop  = solve_max_kit_with_znop(site, 6, opts, norms)

    os.makedirs("output", exist_ok=True)
    path = "output/results.xlsx"
    pairs = [
        ("Макс. КИТ", r_max),
        ("КИТ + резерв 5000 м²", r_res),
        ("КИТ + ЗНОП=6", r_znop),
    ]
    to_xlsx(pairs, path)
    print(f"\n  Сохранено: {os.path.abspath(path)}")
    print_result(r_max, "Максимальный КИТ (справка)")


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

def main():
    print()
    hr()
    print("  Модель застройки территории  v0.2  —  Санкт-Петербург")
    hr()
    print("  Загружаем нормативы СПб...", end=" ", flush=True)
    norms = load_normatives("spb")
    print("готово.")

    while True:
        print(MENU)
        choice = input("  > ").strip()
        try:
            if choice == "0":
                print("  До свидания.")
                break
            elif choice == "1":
                mode_max_kit(norms)
            elif choice == "2":
                mode_with_reserve(norms)
            elif choice == "3":
                mode_with_znop(norms)
            elif choice == "4":
                mode_verify(norms)
            elif choice == "5":
                mode_export(norms)
            else:
                print("  Неверный выбор. Введите 0–4.")
        except KeyboardInterrupt:
            print("\n  (прервано)")
        except Exception as e:
            print(f"\n  ОШИБКА: {e}")
            import traceback; traceback.print_exc()

        input("\n  Нажмите Enter для продолжения...")


if __name__ == "__main__":
    main()
