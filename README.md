# urban_model

Математическая модель застройки территории. Основной режим — **обратный расчёт**: по площади квартала и ограничениям подбирает максимально допустимый КИТ и соответствующие ТЭП (площадь квартир, население, ДОО, СОШ, ЗНОП, парковки, баланс территории).

## Запуск (под Windows)

Дважды кликнуть **`start.bat`**. Скрипт сам:

1. установит `uv`, если его нет;
2. синхронизирует зависимости (`uv sync --extra dev`);
3. предложит выбрать режим:
   - **1** — Веб-приложение Streamlit (откроется в браузере) — *по умолчанию*;
   - **2** — демо-ноутбук Jupyter;
   - **3** — интерактивный расчёт в консоли (`run.py`);
   - **4** — прогон pytest-тестов.

## Streamlit-UI (v0.3)

В sidebar — параметры (квартал, жильё, ЗНОП, соцобъекты, парковки, ВПП, режим расчёта). Главная область — KPI-карточки + раскрывающиеся секции с детализацией. Результаты можно накапливать на вкладке «Сравнение» и скачивать xlsx-отчёт.

## Установка вручную

```bash
uv sync --extra dev
uv run streamlit run src/urban_model/ui/app.py
```

## Программный API

```python
from urban_model import solve_max_kit
from urban_model.models import Site, CalculationOptions
from urban_model.normatives import load_normatives

norms = load_normatives("spb")
site = Site(area_m2=50_000)
result = solve_max_kit(site, CalculationOptions(floors=12, planning_doc=True), norms)
print(result.summary())
```

См. `notebooks/demo_v0_4.ipynb`.

Подробности архитектуры: `CLAUDE.md`. ТЗ: `info/ТЗ_обратный_расчет_ТЭП.docx`.
