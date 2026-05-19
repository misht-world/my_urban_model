# SPEC.md — техническое задание на модуль `my_development_calc`

## 1. Цель

Разработать Python-модуль для подбора и оценки вариантов застройки земельного участка. Модуль должен:

1. Принимать параметры участка и нормативные ограничения
2. Автоматически генерировать варианты застройки в рамках ограничений
3. Считать для каждого варианта затраты, выручку и финансовые метрики
4. Ранжировать варианты и показывать сравнительные графики
5. Выдавать HTML-отчёт с обоснованием выбора лидера

Уровень точности — preliminary feasibility (±15–25%). Все расчёты ведутся в **условных единицах** (у.е.), где 1.0 = себестоимость м² типового 9-этажного монолитного жилья со стандартной отделкой.

## 2. Модели данных

### 2.1 BuildingType (Enum)

```
RESIDENTIAL_LOW          # до 5 этажей
RESIDENTIAL_MID          # 6–9 этажей
RESIDENTIAL_HIGH         # 10–17 этажей
RESIDENTIAL_TOWER        # 18+ этажей
PARKING_UNDERGROUND      # подземный
PARKING_MULTILEVEL       # надземный многоуровневый
PARKING_SURFACE          # открытая стоянка
KINDERGARTEN             # ДОО
SCHOOL                   # СОШ
CLINIC                   # поликлиника / ФАП
RETAIL_BUILTIN           # встроенно-пристроенная коммерция
```

### 2.2 Building

```python
class Building(BaseModel):
    type: BuildingType
    gba: float                          # общая площадь, м²
    nsa: float                          # продаваемая площадь, м² (0 для непродаваемых)
    floors: int = 1
    underground_levels: int = 0         # для подземных паркингов
    construction_type: str = "monolith" # monolith | panel | monolith_brick | brick | precast
    finishing_level: str = "standard"   # shell_core | white_box | standard | comfort_plus | business
    units_count: int = 0                # квартир / машиномест / мест в группе / учеников
    residential_class: str | None = None  # economy | comfort | business | premium (только для жилья)
```

### 2.3 Site

```python
class Site(BaseModel):
    area_m2: float
    max_far: float                      # КИТ из ПЗЗ
    max_height_m: float
    max_built_up: float                 # макс. % застройки (0..1)
    complexity_factor: float = 1.0      # K_сложность (рельеф, грунты)
    connection_costs: float = 0.0       # ТУ в у.е.
    demolition_costs: float = 0.0       # подготовка территории в у.е.
    land_cost: float = 0.0              # стоимость земли в у.е.
    region: str = "spb"                 # ключ для подбора нормативов
```

### 2.4 Scenario

```python
class Scenario(BaseModel):
    name: str
    site: Site
    buildings: list[Building]
    timeline_months: int = 36
    sales_curve: list[float] | None = None  # нормированное распределение продаж
                                            # если None — равномерно с 12 месяца
```

### 2.5 Constraints

Загружается из `config/norms.yaml`. Содержит:

- Нормативы обеспеченности (мест ДОО на 1000 жителей и т.д.)
- Параметры квартирографии (м²/квартира, жителей/квартиру)
- Удельные параметры объектов (м²/место в ДОО, м²/машиноместо)
- Зонирование (типовые ограничения по зонам ПЗЗ — справочно)

## 3. Расчётные модули

### 3.1 cost.py

```python
def calc_building_cost(building: Building, site: Site, config) -> CostBreakdown
```

Формула:
```
cost = gba × C_base(type, floors) × K_construction × K_finishing × K_complexity
```

Особенности:
- **Подземные паркинги:** при `underground_levels > 1` стоимость считается как сумма по уровням, каждый следующий уровень дороже предыдущего согласно прогрессии в `coefficients.yaml`. Не `levels × base`.
- **Надбавка за отделку выше стандарта** применяется только к жилью.

```python
def calc_scenario_cost(scenario: Scenario, config) -> ScenarioCost
```

Структура итоговой стоимости:
```
shell_total           = Σ стоимости зданий
networks              = shell_total × pct_networks
landscaping           = shell_total × pct_landscaping
design_engineering    = shell_total × pct_design
contingency           = (shell + sets + landscape + design) × pct_contingency
fixed                 = site.connection_costs + site.demolition_costs
land                  = site.land_cost

total = shell_total + networks + landscaping + design_engineering + contingency + fixed + land
```

`CostBreakdown` сохраняет все промежуточные суммы и разбивку по объектам.

### 3.2 revenue.py

```python
def calc_revenue(scenario: Scenario, config) -> RevenueBreakdown
```

Считает выручку по продаваемым объектам:
- Жильё: `nsa × price_per_m2(class, finishing)`
- Машиноместа: `units_count × price_per_space(parking_type)`
- Встроенная коммерция: `nsa × commercial_price`

Для соцобъектов и обязательного нормативного паркинга (если он зачитывается как обременение) — выручка = 0.

### 3.3 financial.py

```python
def calc_financial_metrics(scenario, cost, revenue, config) -> FinancialMetrics
```

Метрики:
- `profit = revenue - cost`
- `margin = profit / revenue`
- `roi = profit / cost`
- `npv` — на основе cash flow по месяцам с дисконтированием
- `irr` — опционально (numpy_financial.irr)
- `payback_months` — по кумулятивному cash flow

Cash flow:
- Затраты распределяются равномерно по `timeline_months` (по умолчанию). Архитектурно предусмотреть подмену на S-кривую через стратегию.
- Выручка распределяется по `sales_curve` или равномерно с 12-го месяца.

### 3.4 metrics.py

```python
def calc_comparison_metrics(scenario, cost, revenue) -> ComparisonMetrics
```

Метрики сравнения:
- `profit_per_site_m2` — основная для ранжирования
- `profit_per_residential_nsa` — прибыль на м² продаваемого жилья
- `cost_per_gba_total` — себестоимость на м² общей площади
- `cost_per_nsa_residential` — себестоимость на м² продаваемого жилья
- `parking_burden = parking_cost / residential_revenue` — доля паркинга в выручке жилья
- `social_burden = social_cost / residential_revenue` — доля соцобъектов в выручке жилья
- `residential_share = Σ NSA жилья / Σ GBA` — структура застройки
- `normative_compliance: dict` — выполнение нормативов с разбивкой по типам

## 4. Генератор сценариев

### 4.1 space_planner.py

```python
def calc_max_residential_gba(site: Site, constraints) -> float
def calc_required_facilities(residential_nsa: float, constraints) -> RequiredFacilities
def calc_required_parking_split(total_spaces: int, site: Site, axes) -> dict
```

`RequiredFacilities` содержит:
- `kindergarten_places: int`
- `school_places: int`
- `parking_spaces: int`
- `green_area_m2: float`

Расчёт от квартирографии:
```
apartments     = residential_nsa / m2_per_apartment_avg
residents      = apartments × residents_per_apartment
kindergarten_places = residents / 1000 × kindergarten_norm
school_places       = residents / 1000 × school_norm
parking_spaces      = apartments × parking_per_apartment
```

### 4.2 scenario_builder.py

```python
class GenerationAxes(BaseModel):
    floors_options: list[int] = [9, 17, 25]
    parking_strategies: list[str] = ['underground_1', 'underground_2',
                                     'multilevel', 'mixed']
    residential_share_options: list[float] = [0.7, 0.8, 0.9]
    kindergarten_config: list[str] = ['1_large', '2_small', 'embedded']
    school_config: list[str] = ['1_full', '1_block', 'shared']
    finishing_levels: list[str] = ['standard', 'comfort_plus']
    construction_types: list[str] = ['monolith']

class ScenarioBuilder:
    def generate(site, constraints, axes) -> list[Scenario]
```

Алгоритм:
1. Перебрать комбинации параметров по сетке (cartesian product осей)
2. Для каждой комбинации:
   - Рассчитать ёмкости (жилья, паркинга, соцобъектов)
   - Построить список Building'ов
   - Проверить валидность по ограничениям участка (FAR, % застройки, высоты)
   - Проверить нормативную достаточность
   - Если валидно — добавить в результат

Ограничение: не более 500 сценариев на запуск (защита от взрыва комбинаторики). При превышении — предупреждение и отсечение по приоритетным комбинациям.

### 4.3 optimizer.py

```python
def rank_scenarios(
    scenarios: list[Scenario],
    objective: str = 'profit_per_site_m2',
    filters: dict | None = None,
    top_n: int = 10
) -> list[RankedScenario]

def pareto_front(
    scenarios: list[Scenario],
    objectives: list[str]  # например ['profit_per_site_m2', 'payback_months']
) -> list[Scenario]
```

`RankedScenario` включает:
- `rank: int`
- `scenario: Scenario`
- `metrics: ComparisonMetrics`
- `delta_from_leader: float` — % отставания по основной метрике
- `warnings: list[str]` — нормативы с дефицитом, рискованные допущения

Стратегия по умолчанию:
1. Отсечь сценарии с `normative_compliance != True`
2. Отсортировать по выбранной целевой метрике
3. Вернуть top_n + рассчитать дельты

## 5. Визуализация

### 5.1 charts.py — обязательный набор

1. **`plot_profit_comparison`** — горизонтальная гистограмма прибыли по сценариям
2. **`plot_cost_breakdown_stacked`** — стек затрат для топ-N (земля / коробка / паркинг / соц / сети / проект / непредвиденные)
3. **`plot_gba_structure`** — стек GBA (жильё / паркинг / соц / коммерция)
4. **`plot_risk_return_scatter`** — scatter: общая стоимость vs прибыль, размер = NSA жилья
5. **`plot_sensitivity_tornado`** — чувствительность лидера к ±20% по: цене продажи, стоимости СМР, отделке, сложности участка, выходу NSA/GBA
6. **`plot_pareto_front`** — если включён multi-objective режим
7. **`plot_cumulative_cashflow`** — кумулятивный CF по месяцам для топ-3

Все графики:
- Сохраняются в `reports/` как PNG (matplotlib)
- Опционально — HTML через plotly при флаге `interactive=True`
- Поддерживают одинаковую цветовую схему (палитра в `config/settings.yaml`)

### 5.2 report.py

```python
def generate_report(
    ranked: list[RankedScenario],
    site: Site,
    output_path: str = 'reports/report.html'
) -> str
```

HTML-отчёт через jinja2:
- Summary блок: лидер, ключевые цифры, главные отличия от второго места
- Таблица сравнения top_n по всем метрикам
- Все графики (встроенные base64 или ссылки на PNG)
- Разбивка стоимости лидера по статьям и объектам
- Список отбракованных сценариев с причинами

Также экспорт в CSV: `reports/comparison.csv` (полная таблица всех сценариев и метрик).

## 6. Конфиги

### 6.1 unit_costs.yaml

Стартовые значения. **1.0 = м² жилья 9 эт. монолит standard.**

```yaml
base_unit_description: "1.0 = себестоимость м² жилья 9 эт. монолит standard"

construction_costs:
  residential:
    low_5fl_brick: 0.92
    low_5fl_panel: 0.80
    mid_9fl_panel: 0.85
    mid_9fl_monolith_brick: 1.08
    mid_9fl_monolith: 1.00          # референс
    high_12fl_monolith: 1.10
    high_17fl_monolith: 1.20
    tower_22fl_monolith: 1.35
    tower_25fl_monolith: 1.48

  parking:
    surface_per_m2: 0.20
    multilevel_per_m2: 0.50
    underground_per_m2_l1: 1.00
    # каждый следующий уровень дороже — см. coefficients.yaml

  social:
    kindergarten_per_m2: 1.40
    school_per_m2: 1.30
    clinic_per_m2: 1.60

  commercial:
    retail_builtin_per_m2: 0.78    # shell&core

construction_overhead:
  pct_networks: 0.10                # сети — 10% от стоимости зданий
  pct_landscaping: 0.05
  pct_design: 0.05
  pct_contingency: 0.07

sale_prices:
  residential_per_nsa_m2:           # по классам жилья
    economy: 1.55
    comfort: 1.95
    business: 2.80
    premium: 4.20
  finishing_premium:                # надбавка к цене продажи за отделку
    shell_core: 0.0
    white_box: 0.04
    standard: 0.08
    comfort_plus: 0.18
    business: 0.35
  parking_space:
    underground: 0.50
    multilevel: 0.25
    surface: 0.10
  retail_builtin_per_m2: 1.30
```

### 6.2 coefficients.yaml

```yaml
construction_type:
  panel: 0.85
  monolith: 1.00
  monolith_brick: 1.07
  brick: 1.10
  precast: 0.95

finishing_level:                    # для жилья, надбавка к себестоимости
  shell_core: 0.85
  white_box: 0.92
  standard: 1.00
  comfort_plus: 1.12
  business: 1.32

site_complexity:
  flat_simple: 1.00
  moderate: 1.10
  difficult_terrain: 1.20
  weak_soils_high_water: 1.25
  congested_urban: 1.18

underground_progression:
  # стоимость уровня N относительно уровня 1
  level_1: 1.00
  level_2: 1.30
  level_3: 1.65
  level_4: 2.05

financial:
  discount_rate_annual: 0.15
  default_timeline_months: 36
  sale_start_month: 12              # с какого месяца начинаются продажи
  nsa_to_gba_residential: 0.80      # выход продаваемой площади
```

### 6.3 norms.yaml

```yaml
regions:
  spb:
    name: "Санкт-Петербург"
    source: "РНГП СПб, СП 42.13330"

    residential:
      kindergarten_places_per_1000: 35
      school_places_per_1000: 120
      parking_per_apartment: 1.0
      green_area_per_resident_m2: 6.0
      residents_per_apartment: 2.5
      m2_per_apartment_avg: 55

    facilities:
      kindergarten:
        m2_per_place: 12
        site_area_per_place_m2: 35
        capacities:
          small: 80
          medium: 140
          large: 240
      school:
        m2_per_place: 16
        site_area_per_place_m2: 40
        capacities:
          small: 550
          standard: 825
          large: 1100
      parking_underground:
        m2_per_space: 35
      parking_multilevel:
        m2_per_space: 28
      parking_surface:
        m2_per_space: 25

    zoning_typical:
      far_residential: 2.0
      max_built_up: 0.30
      max_height_m: 75
```

### 6.4 settings.yaml

```yaml
output:
  reports_dir: "reports"
  csv_export: true
  html_interactive: false

generation:
  max_scenarios: 500
  pareto_objectives: ["profit_per_site_m2", "payback_months"]

palette:
  primary: "#2E75B6"
  secondary: "#70AD47"
  warning: "#ED7D31"
  danger: "#C00000"
  neutral: "#A6A6A6"

# для будущей v0.3
unit_to_rub_multiplier: null        # null = считать в у.е.
```

## 7. Обоснование стартовых коэффициентов

Коэффициенты в `unit_costs.yaml` основаны на типичных пропорциях, наблюдаемых в открытых данных по российским жилым проектам. Краткая логика:

- **Жильё mid 9 эт. монолит = 1.0** — референсная единица.
- **Подземный паркинг ≈ 1.0/м²** — практически равен жилью по себестоимости м² из-за сложного конструктива, гидроизоляции, вентиляции, рамп.
- **Многоуровневый надземный паркинг ≈ 0.5** — упрощённая инженерия, нет гидроизоляции, минимальная отделка.
- **Социальные объекты выше 1.0** — спецтребования, малые группы помещений, повышенные нормы по инженерии и безопасности.
- **Цена продажи жилья / себестоимость ≈ 1.6–2.2** — типичная маржа по эконому/комфорту в РФ.
- **Машиноместо в подземном паркинге ≈ 0.5 у.е.** — обычно 35 м² × коэф цены, с учётом того что не все места легко продаются.

Все эти числа — стартовые. Тюнить под конкретный регион через изменение `unit_costs.yaml` без правки кода.

## 8. Тесты (минимум для v0.1)

- `test_cost_calc.py` — расчёт себестоимости здания по ручному примеру, совпадение
- `test_underground_progression.py` — стоимость 2-уровневого подземного паркинга = level_1 + level_1 × 1.30, не 2 × level_1
- `test_normative_compliance.py` — синтетический сценарий: жильё на 1000 жителей требует ~35 мест ДОО, проверка
- `test_ranking.py` — два сценария с заведомо разной прибылью ранжируются корректно
- `test_metrics_invariance.py` — при умножении всех cost и revenue на одну константу ранжирование не меняется (проверка корректности относительной модели)

## 9. Примеры

### 9.1 examples/manual_compare.py

Сравнение двух заданных вручную сценариев:
- Сценарий А: подземный паркинг 2 уровня + больше жилья
- Сценарий Б: многоуровневый наземный паркинг + меньше жилья

Должен показать: какой выгоднее, на сколько %, что съедает прибыль в проигрывающем варианте.

### 9.2 examples/auto_selection.py

Автоматический подбор:
```python
site = Site(
    area_m2=12000,
    max_far=2.5,
    max_height_m=75,
    max_built_up=0.30,
    land_cost=180.0,           # в у.е., примерно 180 м² жилья
    connection_costs=45.0,
    demolition_costs=8.0,
    complexity_factor=1.05,
    region="spb"
)

builder = ScenarioBuilder()
scenarios = builder.generate(site, Constraints.from_region("spb"),
                              axes=GenerationAxes())

ranked = rank_scenarios(scenarios, objective='profit_per_site_m2', top_n=10)
report = generate_report(ranked, site, output_path='reports/report.html')
```

Должен сгенерировать ≥50 валидных сценариев, отранжировать, выдать HTML-отчёт со всеми графиками.

## 10. Критерии приёмки v0.1

- ✅ `pip install -e .` устанавливает пакет, импорты работают
- ✅ `pytest` проходит без ошибок
- ✅ `python examples/manual_compare.py` выводит таблицу сравнения и сохраняет графики в `reports/`
- ✅ `python examples/auto_selection.py` генерирует ≥50 сценариев и HTML-отчёт
- ✅ HTML-отчёт открывается в браузере, все графики отображаются
- ✅ CSV-выгрузка содержит все сценарии и метрики
- ✅ При изменении любого коэффициента в YAML расчёт меняется без правки кода
- ✅ Ранжирование инвариантно к масштабированию: умножение всех денежных значений на 10 не меняет порядок сценариев
