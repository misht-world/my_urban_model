# urban_model

Математическая модель застройки территории. Основной режим v0.1 — **обратный расчёт**: по площади квартала и ограничениям подбирает максимально допустимый КИТ и соответствующие ТЭП (площадь квартир, население, ДОО, СОШ, ЗНОП, парковки, баланс территории).

## Установка

```bash
uv sync --extra dev
```

## Быстрый старт

```python
from urban_model.normatives import load_normatives
from urban_model.modes.inverse import solve_max_kit
from urban_model.models.site import Site

norms = load_normatives(profile="spb")
site = Site(area_m2=20_000)
result = solve_max_kit(site, norms=norms)
print(result.summary())
```

См. `notebooks/demo_v0_1.ipynb`.

Подробности: `CLAUDE.md`, ТЗ — `info/ТЗ_обратный_расчет_ТЭП.docx`.
