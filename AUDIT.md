# Аудит проекта my_urban_model — 2026-05-20

**Версия в аудите:** v0.8.4 (`__init__.py`, `pyproject.toml`).
**Состояние тестов:** ✅ 284/284 passed (9.31 s).
**Профиль аудита:** полный — тесты + ядро/экономика + Optuna/UI + YAML/архитектура.

---

## 📌 Статус после v0.8.5

Все **P0** закрыты в коммите v0.8.5. Тесты: 290/290.

| ID | Статус | Решение |
|---|---|---|
| P0-1 | ✅ | `economy/cost.py` читает `parking.open_space_per_place` (20.75 м²). |
| P0-2 | ✅ | Кэш preview-solve по `_vpp_preview_key()` в `runner.py`. |
| P0-3 | ✅ | Модуль `calculations/warning_codes.py` (WC-enum + теги `[CODE]`). |
| P0-4 | ✅ | `TYPE_CHECKING`-импорт + `model_rebuild()` в конце `result.py`. |
| P0-5 | ✅ | Зомби `fixed = land_cost+…` удалён, всегда 0 до v0.8.6 cash-flow. |
| P0-6 | ✅ | `CostBreakdown.{social_parking,sport,custom_objects}` + `RevenueBreakdown.custom_commercial`. |
| P0-7 | ✅ | Норматив `parking.underground_capacity_per_level=400` + WARNING `PARKING_UG_OVERPACKED`. |

**P1** оставлены на следующий спринт (рефакторинг forward.py, deprecate `vpp_share`/`built_in` single, расширение тестов). **P1-3** (`multilevel_explicit_places` мёртвое) — оказалось используется в `calculations/parking.py:88`, не мёртвый.

---

Каждое замечание содержит **файл:строка**, **симптом**, **причину** и **предлагаемое действие**. Приоритеты:

- **P0** — корректность расчёта или потенциальное падение; править в ближайший спринт.
- **P1** — нагрузка, согласованность данных, мёртвый код; править планово.
- **P2** — косметика и документация.

---

## P0 — корректность расчёта

### P0-1. Дублирующие нормативы площади открытого м/м (YAML рассогласован)

- `configs/spb.yaml:269` — `parking.open_space_per_place = 20.75` м² (источник: «принято условно»).
- `configs/spb.yaml:560` — `economy.parking_areas.surface_m2_per_space = 25` м² (источник: «СП 113.13330»).

Один и тот же физический параметр — площадь одного открытого м/м — задан **дважды разными значениями** в одной YAML. `forward.py` использует 20.75 для расчёта `parking_open_area` и баланса; `economy/cost.py` использует 25 для себестоимости. Это даёт +20% к себестоимости открытых парковок относительно их физической площади, попавшей в баланс.

**Действие:** свести к одному источнику. Удалить `economy.parking_areas.surface_m2_per_space`, в `economy/cost.py` использовать `parking.open_space_per_place`. Параллельно проверить multilevel/underground — они существуют только в economy-секции (это OK, потому что в forward `parking_multilevel_area` идёт по другому нормативу `parking.multilevel_area_per_place(levels)`, а подземные не имеют поверхностной площади).

---

### P0-2. Optuna делает **двойной `solve_max_kit`** на каждый trial с vpp_modes

- `src/urban_model/optimize/runner.py:182-207` — внутри `_build_options_for_trial` для каждого режима ВПП (full_floor/half_floor/min_only/min_plus/custom_only, т.е. **всех 5 режимов**) выполняется предварительный `solve_max_kit(site, opts_step1, norms)` ради `footprint` и `population`.

Затем в `_make_objective` снова вызывается `solve_max_kit(site, opts, norms)`. Итог: **2 inverse-solve на каждый Optuna-trial**. При дефолтном `_DEFAULT_TRIALS = 2000` и решении в среднем ≈30–50 мс, ВПП-перебор удваивает время с ~30 с до ~1 мин.

К тому же `try`/`except Exception` в строках 191-193 / 205-207 **молча гасит ошибки** preview-solve, давая `footprint=0, pop=0`. Для full_floor это означает «полпустых ВПП», что Optuna не отличит от валидных режимов.

**Действие:**
1. Кэшировать preview-solve по «ключу опций без ВПП» — в рамках одного study типовых базовых конфигураций — единицы, не 2000.
2. `except Exception` заменить на конкретные `(ValueError, KeyError)` + warning через `exception_log`.

---

### P0-3. `strict_social_validation` — keyword-фильтр по русским строкам

- `src/urban_model/optimize/runner.py:267-274` — feasibility ловит «плохие» вместимости ДОО/СОШ по подстрокам:
  ```python
  keywords = ["меньше минимальной", "меньше минимума отдельно",
              "< нормативного минимума", "превышает принятый максимум"]
  ```

Если кто-то поправит формулировку warning в `forward.py:163, 171, 181, 286` — Optuna **молча перестанет фильтровать** невалидные сценарии. Тестов на эту связь нет.

**Действие:** ввести **типизированные warnings**: `WarningCode = Literal["soc_capacity_min", "soc_capacity_max", ...]` или класс `Warning(code, message)`. Feasibility-фильтр читает коды, не текст.

---

### P0-4. `EconomicMetrics` обязательный импорт в `models/result.py`

- `src/urban_model/models/result.py:15` — `from urban_model.economy.result import EconomicMetrics`.

Если economy-модуль сломан (импорт-error, синтаксическая ошибка, отсутствие YAML-ключа на старте импорта) — **весь** `TEPResult` не импортируется, что валит абсолютно всё API. Сейчас риск низкий (модуль чистый), но связность ядра с экономикой сильнее, чем заявлено в CLAUDE.md («Экономика — отдельный слой поверх ТЭП»).

**Действие:** объявить поле как `economy: "EconomicMetrics | None" = None` + `from typing import TYPE_CHECKING`-импорт. Или ввести protocol-интерфейс в models/, а реализацию — в economy/.

---

### P0-5. `fixed` cost читает несуществующие поля через `getattr`

- `src/urban_model/economy/cost.py:121-129`:
  ```python
  fixed = (getattr(options, "land_cost", 0.0) or 0.0) + \
          (getattr(options, "connection_costs", 0.0) or 0.0) + \
          (getattr(options, "demolition_costs", 0.0) or 0.0)
  ```
- В `CalculationOptions` ни одного из этих полей **нет** (см. `models/options.py` целиком). Результат всегда 0. Это «зомби-код» — выглядит как функциональность, реально null.

**Действие:** либо завести поля в `CalculationOptions` (`land_cost: float = 0.0`, …) с явными типами, либо удалить блок `fixed` до v0.8.1 cash-flow.

---

### P0-6. Экономика игнорирует кастомные объекты, спорт, парковки соцобъектов

- `src/urban_model/economy/cost.py` НЕ учитывает:
  - `tep.sport_facilities_area` (м² спортсооружений, ВРИ 5.1.3) — потенциальная стоимость, пусть и небольшая.
  - `options.custom_objects` — офис/ФОК/поликлиника на территории дают своё CAPEX.
  - `tep.social_parking_area` — парковки ДОО/СОШ имеют ту же удельную стоимость, что открытые жилищные.
- Аналогично revenue игнорирует custom_objects (часть из них коммерческая).

**Действие:** добавить статьи в `CostBreakdown` (`sport`, `custom_objects`, `social_parking`) и `RevenueBreakdown.custom_commercial`. Сейчас экономика **системно занижает** себестоимость для смешанных сценариев — что искажает Optuna при `objective="profit"`.

---

### P0-7. `underground_levels` влияет только на стоимость, не на feasibility/реалистичность

- `src/urban_model/models/parking.py:57-62` — поле есть.
- `src/urban_model/calculations/parking.py` — НЕ читает поле (площадь поверхности у подземных = 0 по определению).
- `src/urban_model/economy/cost.py:108` — единственный потребитель.

Это OK для текущего MVP, **но**: на одном уровне подземки физически вмещается ≤350–400 м/м на 10 000 м² пятна, при бо́льшем числе мест на 1 уровне рассчитывать невозможно. Optuna может предложить 2000 подземных м/м × 1 уровень — и это пройдёт.

**Действие:** добавить мягкий warning в forward.py, если `underground_places / underground_levels > capacity_max_per_level` (норматив можно завести в YAML, например 400 / уровень).

---

## P1 — нагрузка, согласованность, мёртвый код

### P1-1. `forward.py` — 969 строк линейной логики

Один файл содержит всю forward-логику: жильё, ВПП, ДОО, СОШ, спорт, соцпарковки, ЗНОП, баланс, экономика. Рост был органичным, сейчас:
- 8+ `if options.include_*` развилок переплетены друг с другом.
- Тройной чек `_kg_only_demand / _sch_only_demand / _sport_only_demand`.
- Корректировка `residential_gfa` за встроенным ДОО (строки 226-233) — не симметрична с другими типами зданий.

**Действие:** не делать big-bang рефакторинг сразу, но при следующем feature пилить отдельный шаг `compute_*` под каждый блок (ДОО, СОШ, спорт уже на пути). Карта: `_compute_kindergarten(...) → KindergartenSubResult`.

---

### P1-2. `vpp_share` (legacy) и `built_in` (single) — два устаревших пути для ВПП

- `models/options.py:26-37` — три (!) способа задать ВПП:
  1. `vpp_share: float` (плоская доля, legacy v0.2)
  2. `built_in: BuiltInArea | None` (single, legacy v0.2)
  3. `built_in_list: list[BuiltInArea]` (актуальный, v0.7.1)

`forward.py:76-94` объединяет все три через `all_built_ins`. UI с v0.7.1 строит **только** `built_in_list`. Тесты `test_vpp_list.py` покрывают только новый путь.

**Действие:** пометить `vpp_share` и `built_in` как `Deprecated`, выводить runtime-warning при ненулевом значении. Удалить в v0.9 или v1.0.

---

### P1-3. `ParkingConfig.multilevel_explicit_places` — мёртвое поле?

- `models/parking.py:66-75` — поле объявлено.
- Поиск использования: проверить в `calculations/parking.py`. Если не используется — удалить.

**Действие:** проверить grep по `multilevel_explicit_places`; если нет потребителей вне модели — убрать.

---

### P1-4. CustomObject парковки — упрощение через `parking.vpp.m2_per_place`

- `forward.py:399-402` — для **любого** ВРИ кастомного объекта берётся усреднённый коэф 64 м²/м.м (1.56 м/м на 100 м²), такой же, как для ВПП.
- Это в CLAUDE.md помечено как «упрощение по запросу» (v0.7.1.1).
- Поликлиника ВРИ 3.4.1 → ≈ 0.21 м/м на 100 м² (на посещение в смену), офис ВРИ 4.1 → ≈ 1 м/м на 60 м². Отклонение от среднего может быть в 2-3 раза.

**Действие:** оставить как осознанное упрощение, но **документировать в UI** caption под полем кастомных объектов: «парковки считаются по среднему коэф ВПП — для штучных вариантов задайте парковки вручную». Когда появится UI для парковок CustomObject — добавить ВРИ-conditional.

---

### P1-5. Тестов экономики слишком мало (5 классов × ≈12 тестов)

`tests/test_economy.py` покрывает:
- ✅ Нормативы YAML загружены.
- ✅ residential = residential_gfa × C_base.
- ✅ revenue от класса жилья.
- ✅ underground progression.
- ✅ Linearity (scale × 10).
- ✅ Optuna objective='profit' монотонна.

**Дыры:**
- Кейс встроенно-пристроенного ДОО (residential_gfa уменьшается дважды — на ВПП и на kg_bld).
- Кейс `kg_bld_total > residential_gfa` (теоретически невозможно, но `max(0, ...)` обнуляет).
- Кейс с custom_objects и спортом (нулевой costs — см. P0-6).
- Snapshot-тест на полный `CostBreakdown` для эталонного сценария.

**Действие:** добавить 4-5 тестов на edge cases.

---

### P1-6. UI: `optimizer_objective` не управляет `is_empty()`-проверкой

- `ui/optimizer.py:262-278` — даже если пользователь выбрал «Максимум прибыли», но ни одна галочка не отмечена, `is_empty()` всё равно вернёт True и оптимизация не запустится.

Это семантически правильно, но UI не подсказывает: пользователь, ожидающий, что переключение цели — уже команда, увидит «Отметьте хотя бы один параметр» и может растеряться.

**Действие:** добавить контекстуальный caption под чекбоксами «Цель работает в паре с варьируемыми параметрами — отметьте, что перебирать».

---

### P1-7. ZNOP-cap в Optuna: при `znop_per_person_choices=[6]` + `kit_max=2.5` — нет фильтрации

- `forward.py:538-545` — если задан `znop_per_person_override`, КИТ_max снижается через `znop.kit_cap_for_znop`.
- В Optuna `_build_options_for_trial:228-233` устанавливает `znop_per_person_override`, но **не корректирует** диапазон бисекции в `kit_search_max`.

Возможный сценарий: пользователь выбрал ЗНОП=3 (cap = 1.79), бисекция работает в [0.1, 2.5], даёт kit = 2.5 → kit_status=ERROR → `feasible=False` → trial выкидывается. Это не баг, но **расточительно**: при ЗНОП=3 хорошо бы сразу искать KIT в [0.1, 1.79].

**Действие:** в `solve_max_kit` (inverse.py) передавать `kit_search_max = min(kit_search_max, effective_kit_max)`. Ускорит сходимость.

---

## P2 — косметика

### P2-1. Версия в CLAUDE.md и __init__ согласованы (✅ OK)

Дорожная карта в `CLAUDE.md:113` помечает v0.8.0 как ✅, текущая версия в `__init__.py` = 0.8.4. После v0.8.0 промежуточные правки (v0.8.1–v0.8.4 — фиксы UI/Optuna) **в дорожной карте не отражены**.

**Действие:** добавить строки «v0.8.1 — UX фикс X», … «v0.8.4 — фикс ДОО/СОШ count + warning», чтобы roadmap соответствовал commit-истории.

---

### P2-2. Дублирующиеся комментарии в economy/cost.py

- `cost.py:73` — «открытая стоянка с покрытием» дублирует description в Pydantic-модели и note в YAML.

Косметика, не баг.

---

### P2-3. Магические числа в коде минимальны

Прогон grep по типовым числовым нормативам нашёл всего:
- `models/parking.py:31,43` — дефолты `open_share=0.125`, `underground_share=0.875` (соответствуют норме ПЗЗ, но это **дефолты Pydantic**, не нормативные значения в коде — формально OK).
- `forward.py:694` — литерал «24» в formula-строке для встроенного ДОО (информационная подпись).
- `ui/inputs.py:94,285,516` — дефолты слайдеров (UI hints, не расчёт).
- `ui/output.py:144` — «28 м²/чел» в `help`-tooltip (текст, не расчёт).

В коде расчёта magic numbers фактически нет. Принцип «цифры только в YAML» соблюдён. ✅

---

### P2-4. Версии в `pyproject.toml` и `__init__.py`

Обе синхронизированы на 0.8.4. ✅

---

## Сводка по приоритетам

| Приоритет | Кол-во | Что трогать |
|---|---|---|
| **P0** | 7 | YAML, runner.py, models/result.py, economy/cost.py |
| **P1** | 7 | forward.py (refactor), models/options.py, tests, UI captions |
| **P2** | 4 | CLAUDE.md, мелочи |

**Рекомендуемый порядок:**

1. P0-1 (двойная m²/м.м.) — 5 минут, влияет на цифры экономики у всех пользователей.
2. P0-5 (зомби fixed cost) — 5 минут, минимум кода.
3. P0-6 (экономика не видит custom/sport/soc_parking) — полдня; меняет ранжирование в Optuna «profit».
4. P0-3 (keyword-фильтр warnings) — 1-2 часа; защита от тихого регресса.
5. P0-2 (двойной solve в Optuna) — 1-2 часа; ускоряет UI ×2.
6. P0-4 (импорт EconomicMetrics) — 15 минут; снижает связность.
7. P0-7 (warning по плотности подземки) — 30 минут.
8. P1-* — планово в следующий спринт; параллельно с v0.8.5 (cash flow).

---

## Что в проекте действительно хорошо

- ✅ Принцип «цифры в YAML» соблюдён почти безупречно.
- ✅ Все нормативы имеют поле `source`.
- ✅ Двухуровневое наследование russia ← spb работает.
- ✅ TEPField/TEPResult — отличная audit-trail структура.
- ✅ Тесты зелёные (284/284), economy/optuna покрыты smoke-тестами.
- ✅ Forward/inverse чисто разделены.
- ✅ Архитектура 5 слоёв из CLAUDE.md соответствует реальности.
- ✅ Дорожная карта v0.5→v0.8 в CLAUDE.md детально документирована.

Проект в хорошем состоянии. Накопленный долг — типичный для серии быстрых релизов, **не блокирует** дальнейшую разработку v0.8.5 (cash flow).
