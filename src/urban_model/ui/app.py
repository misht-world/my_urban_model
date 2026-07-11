"""Streamlit-приложение: обратный расчёт ТЭП.

Запуск:  uv run streamlit run src/urban_model/ui/app.py

С v0.5.8 — деловой светлый дизайн: параметры на отдельной вкладке,
sidebar свёрнут по умолчанию.
"""

from __future__ import annotations

import streamlit as st

from urban_model.ui.inputs import render_params_tab
from urban_model.ui.optimizer import render_optimizer_tab
from urban_model.ui.output import (
    render_actions,
    render_comparison_tab,
    render_details,
    render_header,
    render_kpi,
)
from urban_model.ui.state import (
    auto_scenario_name,
    get_norms,
    init_session,
    run_calculation,
)

# ---------------------------------------------------------------------------
# Конфигурация страницы
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Модель застройки территории",
    page_icon="🏙",
    layout="wide",
    initial_sidebar_state="collapsed",
)


# ---------------------------------------------------------------------------
# Парольный вход (v0.11.0)
# ---------------------------------------------------------------------------
# Пароль берётся из Streamlit Secrets (st.secrets["app_password"]) — его
# НЕЛЬЗЯ хранить в публичном репозитории. Если secrets не заданы — дефолт
# "123231" (для быстрого старта; смените через Secrets в Streamlit Cloud:
#   Manage app → Settings → Secrets →  app_password = "ваш_пароль"
# ).
def _check_password() -> bool:
    try:
        expected = str(st.secrets.get("app_password", "123231"))
    except Exception:  # noqa: BLE001 — secrets-файла нет (локальный запуск)
        expected = "123231"

    if st.session_state.get("_auth_ok"):
        return True

    # Скрываем GitHub-бейдж/тулбар и на странице входа (основной CSS-блок
    # идёт ниже st.stop() и сюда не доходит).
    st.markdown(
        '<style>.stAppToolbar,[data-testid="stToolbar"],'
        '[data-testid="stToolbarActions"],.viewerBadge_container__1QSob,'
        '.stAppDeployButton,a[href*="github.com"][class*="viewerBadge"]'
        '{display:none!important;visibility:hidden!important;}</style>',
        unsafe_allow_html=True,
    )

    # Центрированная форма входа
    _, mid, _ = st.columns([1, 1.4, 1])
    with mid:
        st.markdown("### 🏙 Модель застройки территории")
        st.caption("Доступ по паролю.")
        pwd = st.text_input("Пароль", type="password", key="_auth_pwd")
        if st.button("Войти", type="primary", use_container_width=True):
            if pwd == expected:
                st.session_state["_auth_ok"] = True
                st.rerun()
            else:
                st.error("Неверный пароль.")
    return False


if not _check_password():
    st.stop()

# Глобальный CSS: крупнее вкладки + плотнее вертикальные интервалы.
# Версионный маркер /* v0.8.2 */ помогает отследить обновление CSS при
# отладке. Если ты видишь старый стиль — это значит браузер кэшировал
# страницу. Hard refresh (Ctrl+F5) сбрасывает кэш.
st.markdown("""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Material+Symbols+Sharp:opsz,wght,FILL,GRAD@20..48,300..700,0..1,-50..200');
  /* v0.11.0: скрываем GitHub-бейдж (fork) и тулбар Streamlit в правом
     верхнем углу — для публичного приложения он не нужен. */
  .stAppToolbar, [data-testid="stToolbar"],
  [data-testid="stToolbarActions"],
  .viewerBadge_container__1QSob, .stAppDeployButton,
  a[href*="github.com"][class*="viewerBadge"] {
      display: none !important;
      visibility: hidden !important;
  }
  /* v0.11.2 stylesheet marker. bump кэша. */
  /* Шрифт грузится <link> выше (надёжнее @import внутри st.markdown).
     Заставляем все material-иконки использовать Sharp-вариант. */
  span.material-symbols-rounded,
  span.material-symbols-outlined,
  span.material-symbols-sharp,
  [class*="material-symbols"],
  [class*="material-icons"] {
      font-family: 'Material Symbols Sharp' !important;
      font-variation-settings: 'FILL' 0, 'wght' 400, 'GRAD' 0, 'opsz' 24 !important;
      font-feature-settings: 'liga' 1 !important;
      -webkit-font-feature-settings: 'liga' 1 !important;
      text-transform: none !important;
      white-space: nowrap !important;
      word-wrap: normal !important;
      letter-spacing: normal !important;
      vertical-align: middle !important;
  }
  /* v0.10.19: расширил max-width до 1340px (раньше 1180px было тесно для
     2-колоночных Параметров). Карточки получают больше горизонтального
     места, проще читать вложенный контент. */
  div.block-container {
      padding-top: 1.5rem;
      padding-bottom: 1rem;
      max-width: 1340px !important;
      margin: 0 auto !important;
  }
  /* v0.10.18: единое шрифт-семейство как в макете. Только body — иначе
     перекроем Material Icons (expand_more, arrow_drop_down и т.п.) и
     получим литеральный текст вместо иконок. */
  html, body, [data-testid="stAppViewContainer"] {
      font-family: -apple-system, "Segoe UI", Roboto, Arial, sans-serif !important;
      font-variant-numeric: tabular-nums;
      -webkit-font-smoothing: antialiased;
      text-rendering: optimizeLegibility;
  }
  /* Текстовые элементы получают системный шрифт явно (на случай если
     Streamlit-тема перебивает inheritance). НЕ трогаем .material-icons. */
  [data-testid="stAppViewContainer"] h1,
  [data-testid="stAppViewContainer"] h2,
  [data-testid="stAppViewContainer"] h3,
  [data-testid="stAppViewContainer"] h4,
  [data-testid="stAppViewContainer"] h5,
  [data-testid="stAppViewContainer"] h6,
  [data-testid="stAppViewContainer"] p,
  [data-testid="stAppViewContainer"] label,
  [data-testid="stAppViewContainer"] input,
  [data-testid="stAppViewContainer"] textarea,
  [data-testid="stAppViewContainer"] button,
  [data-testid="stAppViewContainer"] td,
  [data-testid="stAppViewContainer"] th {
      font-family: -apple-system, "Segoe UI", Roboto, Arial, sans-serif !important;
  }
  /* Защищаем шрифт иконок Streamlit (expand_more, arrow_drop_down) от
     перекрытия системным шрифтом. ВАЖНО: НЕ трогаем material-symbols-* —
     ими управляет правило выше (Material Symbols Sharp). Иначе лигатуры
     наших алертов (check_circle/info) рендерились бы как текст. */
  .material-icons, .material-icons-outlined,
  [class*="material-icons"],
  span[class*="MuiIcon"], i[class*="material-icons"] {
      font-family: "Material Symbols Rounded", "Material Icons",
        "Material Icons Outlined" !important;
  }
  /* Главный заголовок страницы (st.title → h1) — тонкий и крупный,
     с выраженным отрицательным трекингом. Изящнее жирного дефолта. */
  h1#модель-застройки-территории, .stApp h1 {
      font-weight: 300 !important;
      letter-spacing: -1px !important;
      color: #111111 !important;
  }
  /* Подзаголовки секций уровня h2/h3/h4 — спокойный вес */
  .stApp h2, .stApp h3, .stApp h4 {
      font-weight: 600;
      letter-spacing: -0.3px;
      color: #1A1A1A;
  }
  /* v0.11.0: кнопки — прямоугольные, чёрный контур (стиль спецификации).
     По наведению — заливка графитом. Download-кнопки выглядят так же. */
  div[data-testid="stButton"] > button,
  div[data-testid="stDownloadButton"] > button {
      border-radius: 2px;
      padding: 0.4rem 1.15rem;
      font-weight: 600;
      border: 1px solid #1A1A1A;
      background: #FFFFFF;
      color: #1A1A1A;
      white-space: nowrap;
      transition: background .12s, color .12s;
      /* v0.10.18: убираем BaseWeb-овский min-width ≈ 64px, из-за которого
         мелкие кнопки (например «✕» в шапках плиток) скрывались, если
         родительская колонка была уже 64px. */
      min-width: 0 !important;
  }
  div[data-testid="stButton"] > button:hover,
  div[data-testid="stDownloadButton"] > button:hover {
      background: #1A1A1A;
      color: #FFFFFF;
      border-color: #1A1A1A;
  }
  /* primary-кнопки — МАКСИМАЛЬНО агрессивно. Несколько вариантов селекторов
     под разные версии Streamlit (kind, data-testid, css-class). */
  button[kind="primary"],
  button[data-testid="stBaseButton-primary"],
  button[data-testid="baseButton-primary"],
  div[data-testid="stButton"] button[kind="primary"],
  div[data-testid="stButton"] button[data-testid="stBaseButton-primary"],
  div[data-testid="stButton"] > button[kind="primary"] {
      background: #1A1A1A !important;
      background-color: #1A1A1A !important;
      color: #FFFFFF !important;
      border: 1px solid #1A1A1A !important;
      visibility: visible !important;
      opacity: 1 !important;
      display: inline-flex !important;
      min-width: auto !important;
      padding: 0.4rem 1.15rem !important;
      font-weight: 600 !important;
      border-radius: 2px !important;
  }
  button[kind="primary"]:hover,
  button[data-testid="stBaseButton-primary"]:hover {
      background: #333333 !important;
      background-color: #333333 !important;
      color: #FFFFFF !important;
  }
  /* Текст внутри primary-кнопки (Streamlit оборачивает в p) — белый */
  button[kind="primary"] p,
  button[data-testid="stBaseButton-primary"] p,
  button[kind="primary"] [data-testid="stMarkdownContainer"] p {
      color: #FFFFFF !important;
  }
  /* Плотнее интервалы между виджетами */
  div[data-testid="stVerticalBlock"] {gap: 0.5rem;}
  /* Вкладки — более выразительные */
  div[data-baseweb="tab-list"] {
      gap: 0.25rem;
      border-bottom: 2px solid #CBD5E1;
  }
  button[data-baseweb="tab"] {
      font-size: 1.1rem !important;
      padding: 0.65rem 1.6rem !important;
      font-weight: 500;
      border-radius: 6px 6px 0 0 !important;
      border: 1px solid transparent !important;
      border-bottom: none !important;
      background: transparent;
      color: #475569;
  }
  button[data-baseweb="tab"]:hover {
      background: #F1F5F9 !important;
      color: #1E40AF !important;
  }
  button[data-baseweb="tab"][aria-selected="true"] {
      font-weight: 700 !important;
      font-size: 1.13rem !important;
      background: transparent !important;
      color: #111111 !important;
      border: 1px solid transparent !important;
      border-bottom: none !important;
      /* v0.11.0: амбер-подчёркивание активной вкладки (минимал) */
      box-shadow: inset 0 -3px 0 0 #F5A623;
  }
  /* Цветовая дифференциация левой/правой колонок на «Параметрах»:
     • левая (вводные данные) — голубой акцент слева + светло-голубой фон
     • правая (настройки компонентов) — зелёный акцент слева + светло-зелёный фон
     Усилено !important + border-left для надёжной видимости. */
  /* v0.9.15: цветовая дифференциация ВСЕЙ колонки «Параметры»
     через `:has()`-селектор по невидимым CSS-маркерам, которые
     `inputs.py` вставляет в каждую колонку. Работает в современных
     браузерах (Chrome 105+, Firefox 121+, Safari 15.4+).
     Деловые приглушённые цвета, не яркие.

     Также сохранены fallback-селекторы по testid на случай отсутствия
     `:has` (старые браузеры).
  */
  /* v0.10.18: колонки «Параметры» в стиле спецификации — белый фон,
     волосяная рамка, почти прямые углы; различие ввод/настройки — тонкий
     2px-акцент слева (графит = данные, амбер = настройки), без серой заливки. */
  /* v0.10.19: КОЛОНКА получает ТОЛЬКО border и background (визуальная
     рамка карточки). padding убираем — он сужал саму карточку.
     Padding идёт на внутренний stVerticalBlock (см. правило ниже),
     чтобы content отступал от border, а border не сдвигался. */
  /* v0.10.19: КОЛОНКА больше НЕ карточка — фон/рамка убраны. Каждый
     логический блок внутри (Ввод данных, Учитывать в расчёте, плитки)
     обёрнут в st.container(border=True) и сам является карточкой с
     padding. Так нет «карточки в карточке» и текст всегда отступает. */
  [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:has(.params-col-input),
  [data-testid="stHorizontalBlock"] > [data-testid="column"]:has(.params-col-input),
  [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:has(.params-col-settings),
  [data-testid="stHorizontalBlock"] > [data-testid="column"]:has(.params-col-settings) {
      background-color: transparent !important;
      border: none !important;
      padding: 0 !important;
  }
  /* Слайдеры внутри border-блоков — ограничение ширины до ~65%.
     Перечисляем разные селекторы для надёжности. */
  [data-testid="stVerticalBlockBorderWrapper"] [data-testid="stSlider"],
  [data-testid="stVerticalBlockBorderWrapper"] div[data-baseweb="slider"] {
      max-width: 65% !important;
  }
  /* Чуть меньше пустоты у containers с border */
  div[data-testid="stVerticalBlockBorderWrapper"] {padding: 0.6rem 0.9rem;}

  /* v0.12.17 (#8): контент раскрывающихся блоков (st.expander) — внутренние
     отступы от рамки. Без этого текст и ползунки в «Настройки подбора»
     прилипали к границе. */
  [data-testid="stExpander"] [data-testid="stExpanderDetails"] {
      padding: 0.4rem 1.1rem 0.8rem !important;
  }

  /* v0.12.8: карточки ЛЕВОЙ колонки «Параметры» — серый фон + акцентная линия
     слева + лёгкая тень, чтобы отделить «управление» (что считать) от плиток
     «настройки» справа. Прежний :has()-селектор не срабатывал в проде, поэтому
     таргетим по классу .st-key-<key> (Streamlit вешает его на st.container(key=…)).
     Класс ставится на сам border-wrapper. */
  .st-key-param_left_card_essentials,
  .st-key-param_left_card_include {
      background-color: #E9EEF4 !important;
      border: 1px solid #CBD5E1 !important;
      border-left: 3px solid #7C8BA1 !important;
      box-shadow: 0 1px 4px rgba(15,23,42,0.10) !important;
  }
  /* Вложенные блоки не перекрывают серый фон белым */
  .st-key-param_left_card_essentials [data-testid="stVerticalBlock"],
  .st-key-param_left_card_include [data-testid="stVerticalBlock"] {
      background-color: transparent !important;
  }

  /* v0.12.3: карточки рекомендаций в сетке 2×2 — равная высота в ряду.
     Колонка-флекс растягивает дочерний border-wrapper, помеченный .rec-card,
     на всю высоту (даже если контент короче — внизу пустое место). */
  [data-testid="stColumn"]:has(.rec-card),
  [data-testid="column"]:has(.rec-card) {
      display: flex !important;
  }
  [data-testid="stColumn"]:has(.rec-card) > div[data-testid="stVerticalBlockBorderWrapper"],
  [data-testid="column"]:has(.rec-card) > div[data-testid="stVerticalBlockBorderWrapper"] {
      height: 100% !important;
      width: 100% !important;
  }

  /* v0.9.15: единый деловой стиль для ВСЕХ вкладок.
     Контейнеры st.container(border=True) получают тонкий приглушённый
     border и едва заметный фон. Subheader'ы становятся ненавязчивыми. */
  /* v0.11.0: стиль «спецификация» — белые секции с волосяной рамкой,
     без синего акцента сверху, почти прямые углы. */
  div[data-testid="stVerticalBlockBorderWrapper"] {
      border: 1px solid #EDEDED !important;
      background-color: #FFFFFF;
      border-radius: 3px;
  }
  /* H5-заголовки секций — мелкий UPPERCASE с жирной графитовой чертой снизу
     (как заголовки разделов в техническом паспорте). */
  div[data-testid="stVerticalBlockBorderWrapper"] h5 {
      color: #8a8a8a;
      font-size: 0.78rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 1.5px;
      margin-top: 0.1rem;
      margin-bottom: 0.8rem;
      padding-bottom: 0.55rem;
      border-bottom: 2px solid #1A1A1A;
  }
  /* KPI-показатели (st.metric) в духе спецификации: тонкие крупные цифры,
     моноширинные; подписи — мелкие UPPERCASE. */
  [data-testid="stMetricValue"] {
      font-weight: 300 !important;
      font-size: 2.05rem !important;
      color: #111111 !important;
      font-variant-numeric: tabular-nums;
      letter-spacing: -0.5px;
      line-height: 1.15;
  }
  [data-testid="stMetricLabel"] p {
      font-size: 0.74rem !important;
      text-transform: uppercase;
      letter-spacing: 0.6px;
      color: #999999 !important;
  }
  /* v0.10.18: дельта st.metric — плоский серый sub-текст (мокап-стиль),
     без цветных «пилюль». Стрелки делает Streamlit сам — оставляем мелкие. */
  [data-testid="stMetricDelta"] {
      background: none !important;
      color: #888 !important;
      font-size: 11px !important;
      padding: 0 !important;
      font-weight: 400 !important;
      margin-top: 4px !important;
  }
  [data-testid="stMetricDelta"] svg {
      width: 10px !important; height: 10px !important; color: #bbb !important;
  }
  /* v0.10.18: hide-rule для (?) иконок ОТКЛЮЧЁН — в текущей версии
     Streamlit без правильного svg иконка рендерится как чёрный квадрат.
     Лучше оставить «?» рабочими, чем ломать их внешний вид. */
  /* Уведомления (st.success / st.info / st.warning / st.error) — минимал:
     белый фон, тонкая рамка, цветной кант слева, без ярких заливок. */
  [data-testid="stAlert"], div[role="alert"] {
      background-color: #fcfcfc !important;
      border: 1px solid #EDEDED !important;
      border-left: 3px solid #888 !important;
      border-radius: 2px !important;
      padding: 10px 14px !important;
      color: #1A1A1A !important;
      box-shadow: none !important;
  }
  /* v0.10.19: текст ВСЕХ алертов — графит (раньше success рендерился
     зелёным текстом и выбивался из оформления). Только кант слева цветной. */
  [data-testid="stAlert"] *, div[role="alert"] * {
      color: #1A1A1A !important;
  }
  [data-testid="stAlert"][kind="success"], div[role="alert"][data-baseweb~="success"],
  div[data-testid="stAlertContentSuccess"] {
      border-left-color: #15803d !important;
  }
  [data-testid="stAlert"][kind="info"], div[data-testid="stAlertContentInfo"] {
      border-left-color: #1A1A1A !important;
  }
  [data-testid="stAlert"][kind="warning"], div[data-testid="stAlertContentWarning"] {
      border-left-color: #F5A623 !important;
  }
  [data-testid="stAlert"][kind="error"], div[data-testid="stAlertContentError"] {
      border-left-color: #c0392b !important;
  }
  /* v0.10.18: шрифт алертов — тот же системный (Streamlit задаёт свой
     внутри, отсюда визуальный «разнобой»). */
  [data-testid="stAlert"], [data-testid="stAlert"] p,
  [data-testid="stAlert"] span, [data-testid="stAlert"] div {
      font-family: -apple-system, "Segoe UI", Roboto, Arial, sans-serif !important;
  }
  /* Крестики «✕» в шапках плиток — компактные, чтобы влезали в узкую колонку.
     Targeted by aria-label, который Streamlit ставит из параметра help=. */
  button[aria-label*="Скрыть блок"], button[title*="Скрыть блок"] {
      padding: 2px 8px !important;
      min-width: 0 !important;
      min-height: 0 !important;
      font-size: 13px !important;
      line-height: 1 !important;
      border-radius: 2px !important;
  }
  /* v0.10.18: убираем ВНУТРЕННЮЮ рамку алерта (Streamlit рисует свою
     обёртку с border внутри stAlert — отсюда двойная рамка). */
  [data-testid="stAlert"] > div,
  [data-testid="stAlert"] [data-testid^="stAlertContent"],
  [data-testid="stAlert"] [data-testid$="ContentSuccess"],
  [data-testid="stAlert"] [data-testid$="ContentInfo"],
  [data-testid="stAlert"] [data-testid$="ContentWarning"],
  [data-testid="stAlert"] [data-testid$="ContentError"] {
      border: none !important;
      background: transparent !important;
      box-shadow: none !important;
      padding: 0 !important;
  }
  /* Жирный отказ от Streamlit-палитры в h1 (на случай если глобальный
     primaryColor подкрашивал заголовок). */
  h1 {
      font-weight: 300 !important;
      letter-spacing: -1px !important;
      color: #111111 !important;
  }
  /* v0.10.18: УБИРАЕМ внешнюю рамку у «карточек» (st.container border=True)
     на больших секциях (Расчёт / Сравнение / Оптимизация) — макет
     «Спецификация» структурирует чёрной чертой под заголовком, а не
     боксом. Tile-плитки в правой колонке Параметров — оставляем с рамкой. */
  [data-testid="stAppViewContainer"] [data-testid="stVerticalBlockBorderWrapper"] {
      border: none !important;
      background: transparent !important;
      padding: 0 !important;
      border-radius: 0 !important;
  }
  /* Карточки-контейнеры внутри колонок Параметров — рамка + padding.
     Левый акцент: графит для «Ввод данных», амбер для «Настроек». */
  [data-testid="stColumn"]:has(.params-col-input) [data-testid="stVerticalBlockBorderWrapper"],
  [data-testid="column"]:has(.params-col-input) [data-testid="stVerticalBlockBorderWrapper"] {
      border: 1px solid #EDEDED !important;
      border-left: 2px solid #1A1A1A !important;
      background: #FFFFFF !important;
      padding: 18px 22px !important;
      border-radius: 3px !important;
      position: relative !important;
  }
  [data-testid="stColumn"]:has(.params-col-settings) [data-testid="stVerticalBlockBorderWrapper"],
  [data-testid="column"]:has(.params-col-settings) [data-testid="stVerticalBlockBorderWrapper"] {
      border: 1px solid #EDEDED !important;
      border-left: 2px solid #F5A623 !important;
      background: #FFFFFF !important;
      padding: 18px 22px !important;
      border-radius: 3px !important;
      position: relative !important;
  }
  /* v0.10.18: type="tertiary" кнопки Streamlit (минимал-тип) — для ✕
     в плитках. Без рамки/фона по умолчанию, только серый × → темнее
     на hover. Также перекрываем aria-label/title для совместимости
     со старыми версиями, где tertiary недоступен. */
  button[kind="tertiary"],
  button[data-testid="stBaseButton-tertiary"],
  div[data-testid="stButton"] > button[kind="tertiary"],
  button[aria-label*="Скрыть блок"],
  button[title*="Скрыть блок"] {
      min-width: 0 !important;
      min-height: 0 !important;
      padding: 0 6px !important;
      width: auto !important;
      height: auto !important;
      font-size: 18px !important;
      line-height: 1 !important;
      background: transparent !important;
      background-color: transparent !important;
      color: #bbb !important;
      border: 0 !important;
      box-shadow: none !important;
      border-radius: 0 !important;
      font-weight: 400 !important;
  }
  button[kind="tertiary"]:hover,
  button[data-testid="stBaseButton-tertiary"]:hover,
  button[aria-label*="Скрыть блок"]:hover,
  button[title*="Скрыть блок"]:hover {
      color: #111 !important;
      background: transparent !important;
      background-color: transparent !important;
      border: 0 !important;
  }
  /* v0.10.18: повышаем специфичность шрифтового правила для md-заголовков —
     раньше Streamlit-тема (Source Sans Pro) могла перебивать. */
  [data-testid="stAppViewContainer"] [data-testid="stMarkdownContainer"] h1,
  [data-testid="stAppViewContainer"] [data-testid="stMarkdownContainer"] h2,
  [data-testid="stAppViewContainer"] [data-testid="stMarkdownContainer"] h3,
  [data-testid="stAppViewContainer"] [data-testid="stMarkdownContainer"] h4,
  [data-testid="stAppViewContainer"] [data-testid="stMarkdownContainer"] h5,
  [data-testid="stAppViewContainer"] [data-testid="stMarkdownContainer"] h6,
  [data-testid="stAppViewContainer"] [data-testid="stMarkdownContainer"] p {
      font-family: -apple-system, "Segoe UI", Roboto, Arial, sans-serif !important;
  }
  /* v0.10.18: «секционные» подзаголовки — все уровни md (h2..h5) выглядят
     одинаково: мелкий UPPERCASE + 2px чёрная черта снизу. В разных
     местах кода используется ### / ## / **bold** — пользователь не должен
     это замечать, все выглядят как разделы спецификации. */
  [data-testid="stMarkdownContainer"] h2,
  [data-testid="stMarkdownContainer"] h3,
  [data-testid="stMarkdownContainer"] h4,
  [data-testid="stMarkdownContainer"] h5 {
      color: #8a8a8a !important;
      font-size: 0.78rem !important;
      font-weight: 700 !important;
      text-transform: uppercase !important;
      letter-spacing: 1.5px !important;
      padding-bottom: 0.45rem !important;
      margin-bottom: 0.7rem !important;
      margin-top: 0.2rem !important;
      border-bottom: 2px solid #1A1A1A !important;
      line-height: 1.4 !important;
  }
  /* h1 остаётся «крупный тонкий заголовок страницы» — НЕ затрагиваем
     уже определённое правило выше. */
  /* v0.10.18: слайдеры в стиле спецификации — графитовая ручка/трек,
     амбер-«пузырёк» значения. */
  [data-testid="stSlider"] [data-baseweb="slider"] [role="slider"] {
      background-color: #1A1A1A !important;
      border-color: #1A1A1A !important;
      box-shadow: 0 0 0 1px #1A1A1A !important;
  }
  /* Заполненная часть трека — графит, незаполненная — светло-серая */
  [data-testid="stSlider"] [data-baseweb="slider"] > div > div > div {
      background-color: #1A1A1A !important;
  }
  [data-testid="stSlider"] [data-baseweb="slider"] > div > div {
      background-color: #EDEDED !important;
  }
  /* «Пузырёк» текущего значения над ручкой — амбер, чёрный текст */
  [data-testid="stSlider"] [data-baseweb="slider"] [role="slider"]
      > div:first-of-type {
      background-color: #F5A623 !important;
      color: #1A1A1A !important;
      border-radius: 2px !important;
      font-weight: 600 !important;
  }
  /* Подпись min/max под треком — мелкая серая */
  [data-testid="stSlider"] [data-baseweb="slider"] ~ div {
      color: #999 !important;
      font-size: 11px !important;
  }
  /* v0.11.0: декоративные засечки убраны — без подписи значений они
     бесполезны, а точные числовые деления Streamlit нативно не рисует.
     Текущее значение видно в амбер-пузырьке, min/max — по краям. */
  /* ======================================================================
     v0.10.18 АУДИТ — приведение остальных виджетов к стилю «Спецификация»
     ====================================================================== */
  /* Checkbox/Radio/Toggle: цвет берётся из темы primaryColor=#1A1A1A
     (см. .streamlit/config.toml). Кастомный CSS НЕ нужен — раньше его
     :has(input:checked) селекторы случайно ломали (?) help-иконки. */
  /* Expander (st.expander) — БЕЗ внешней рамки, шапка как секционный
     подзаголовок «Основные показатели»: мелкий UPPERCASE с 2px-чёрной
     чертой снизу. Стрелка-маркер остаётся слева (Material Icon). */
  [data-testid="stExpander"] {
      border: none !important;
      border-radius: 0 !important;
      background: transparent !important;
      box-shadow: none !important;
      margin: 0 0 6px 0 !important;
  }
  /* Streamlit рисует рамку и скругление на самом <details> (и на summary).
     Прибиваем border-radius:0 на всех уровнях, чтобы углы были прямые. */
  [data-testid="stExpander"] details,
  [data-testid="stExpander"] details summary,
  [data-testid="stExpander"] > details,
  [data-testid="stExpander"] [data-testid="stExpanderDetails"] {
      border-radius: 0 !important;
  }
  [data-testid="stExpander"] details > summary {
      font-size: 0.78rem !important;
      font-weight: 700 !important;
      text-transform: uppercase !important;
      letter-spacing: 1.5px !important;
      color: #8a8a8a !important;
      padding: 8px 0 8px 24px !important;
      border-bottom: 2px solid #1A1A1A !important;
      background: transparent !important;
      min-height: 0 !important;
  }
  [data-testid="stExpander"] details > summary p {
      margin: 0 !important;
      line-height: 1.3 !important;
      color: inherit !important;
      font-size: inherit !important;
      font-weight: inherit !important;
      text-transform: inherit !important;
      letter-spacing: inherit !important;
  }
  [data-testid="stExpander"] details > summary:hover {
      background: #F5F5F5 !important;
      color: #1A1A1A !important;
  }
  [data-testid="stExpander"] details > div {
      padding: 12px 0 18px 0 !important;
  }
  /* DataFrame: тонкие хайрлайны, шапка с UPPERCASE и черной чертой */
  [data-testid="stDataFrame"] {
      border: 1px solid #EDEDED !important;
      border-radius: 2px;
  }
  [data-testid="stDataFrame"] thead th {
      font-size: 11px !important;
      text-transform: uppercase !important;
      letter-spacing: 0.6px !important;
      font-weight: 700 !important;
      color: #999 !important;
      border-bottom: 2px solid #1A1A1A !important;
      background: #FFFFFF !important;
  }
  /* st.subheader (h3 в выводе) — НЕ должен превращаться в мелкий UPPERCASE
     по правилу секций; это «оглавление» секции, оставляем нормальным.
     Применяем только если у h3 есть data-testid="stHeader" обёртка. */
  [data-testid="stHeader"] h3,
  [data-testid="stSubheader"] h3 {
      font-size: 1.25rem !important;
      font-weight: 600 !important;
      text-transform: none !important;
      letter-spacing: -0.2px !important;
      color: #111 !important;
      border-bottom: none !important;
      padding-bottom: 0 !important;
      margin-top: 1rem !important;
  }
  /* Input/Select: лёгкая рамка + нормальный padding (раньше текст влипал
     в границы — особенно в number_input). */
  input[type="text"], input[type="number"], textarea {
      border-radius: 2px !important;
      padding: 6px 12px !important;
  }
  [data-baseweb="input"] input,
  [data-baseweb="base-input"] input {
      padding: 6px 12px !important;
  }
  input[type="text"]:focus, input[type="number"]:focus, textarea:focus {
      border-color: #1A1A1A !important;
      box-shadow: 0 0 0 1px #1A1A1A !important;
  }
  /* h5-подзаголовок ВНУТРИ плитки (Параметры → Настройки компонентов):
     поджимаем margin-top, чтобы заголовок не «висел» далеко от верха карточки. */
  [data-testid="stColumn"]:has(.params-col-settings) [data-testid="stMarkdownContainer"] h5,
  [data-testid="column"]:has(.params-col-settings) [data-testid="stMarkdownContainer"] h5,
  [data-testid="stColumn"]:has(.params-col-input) [data-testid="stMarkdownContainer"] h5,
  [data-testid="column"]:has(.params-col-input) [data-testid="stMarkdownContainer"] h5 {
      margin-top: 0 !important;
  }
  /* Прогресс-бар (st.progress): графит вместо синего — единая палитра.
     v0.15.9: старый селектор `> div > div` в Streamlit 1.57 красил и ТРЕК —
     полоса выглядела 100%-заполненной с самого старта. Теперь трек светлый,
     заливка (на уровень глубже) — графит. */
  [data-testid="stProgressBar"] [data-baseweb="progress-bar"] > div > div {
      background-color: #EDEDED !important;
      background-image: none !important;
  }
  [data-testid="stProgressBar"] [data-baseweb="progress-bar"] > div > div > div {
      background-color: #1A1A1A !important;
      background-image: none !important;
  }
  /* v0.10.19: padding на колонке убран — теперь padding даёт внутренний
     st.container(border=True) каждого блока (см. правило карточек выше). */
  /* Выравнивание карточек сканов в ряду «Пофакторный анализ»: грид с
     одинаковой высотой + flex-1 на содержимое чтобы низ был выровнен. */
  [data-testid="stHorizontalBlock"] {
      align-items: stretch !important;
  }
  [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:has(> div > [data-testid="stVerticalBlockBorderWrapper"]) {
      display: flex !important;
      flex-direction: column !important;
  }
  [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:has(> div > [data-testid="stVerticalBlockBorderWrapper"]) > div {
      flex: 1 !important;
      display: flex !important;
      flex-direction: column !important;
  }
  /* Размер и стиль шрифта expander summary = h5 секций (единообразие).
     Уточняю ВСЁ для надёжности. */
  [data-testid="stExpander"] details > summary,
  [data-testid="stExpander"] details > summary p,
  [data-testid="stExpander"] details > summary span {
      font-size: 0.78rem !important;
      font-weight: 700 !important;
      text-transform: uppercase !important;
      letter-spacing: 1.5px !important;
      color: #8a8a8a !important;
      line-height: 1.4 !important;
  }
  /* Вертикальные волосяные линии между KPI-колонками (сетка-спецификация).
     Колонки, содержащие st.metric, получают разделитель слева; у первой — нет. */
  [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:has([data-testid="stMetric"]) {
      border-left: 1px solid #EDEDED;
      padding-left: 16px;
  }
  [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:first-child:has([data-testid="stMetric"]) {
      border-left: none;
      padding-left: 0;
  }
</style>
""", unsafe_allow_html=True)

# Заголовок и краткая подпись
col_title, col_meta = st.columns([3, 1])
with col_title:
    # v0.10.18: жирное слово «территории» — стиль макета (h1 + <b>).
    st.title("Модель застройки **территории**")
    # v0.10.18: амбер-полоса под заголовком убрана — макет «Сетка-Спецификация»
    # обходится без неё; акцент остался на активной вкладке.
    st.caption(
        "Обратный расчёт КИТ по площади квартала. "
        "Профиль нормативов — Санкт-Петербург."
    )
with col_meta:
    from urban_model import __version__
    st.markdown(
        f"<div style='text-align:right;padding-top:1.5rem;color:#6B7280;'>"
        f"<small>v{__version__}</small></div>",
        unsafe_allow_html=True,
    )

init_session()
norms = get_norms("spb")


# ---------------------------------------------------------------------------
# Sidebar — минимальный (можно развернуть для статуса/сброса)
# ---------------------------------------------------------------------------

with st.sidebar:
    st.markdown("### Управление")
    st.caption(
        "Параметры — на вкладке «Параметры». Здесь — служебные действия."
    )
    if st.button("Сбросить сравнение"):
        st.session_state.scenarios = []
        st.toast("Сценарии очищены", icon="🗑")
    if st.button("Сбросить объекты"):
        st.session_state.custom_objects = []
        st.toast("Объекты очищены", icon="📦")

    st.markdown("---")
    st.caption(
        f"Сценариев в сравнении: **{len(st.session_state.scenarios)}**  \n"
        f"Объектов: **{len(st.session_state.get('custom_objects', []))}**"
    )


# ---------------------------------------------------------------------------
# Вкладки
# ---------------------------------------------------------------------------

_n_scenarios = len(st.session_state.scenarios)

tab_params, tab_calc, tab_optimize, tab_compare = st.tabs([
    "Параметры",
    "Расчёт",
    "Оптимизация",
    f"Сравнение ({_n_scenarios})" if _n_scenarios else "Сравнение",
])

# --- Параметры (новая вкладка с полной формой) ---
with tab_params:
    inputs = render_params_tab()

# --- Расчёт (только результаты) ---
with tab_calc:
    # v0.10.18: h1-заголовок вкладки в стиле макета.
    st.markdown("# Расчёт **варианта**")
    # v0.9.30: «Применить к Расчёту» из Оптимизации — расчёт по применённому
    # сценарию (override), а не по форме «Параметры». Баннер + кнопка возврата.
    _applied = st.session_state.get("applied_options")
    if _applied is not None:
        _albl = st.session_state.get("applied_label", "сценарий из Оптимизации")
        bc1, bc2 = st.columns([3, 1])
        bc1.info(
            f"▶ Расчёт по применённому сценарию из Оптимизации: **{_albl}**. "
            f"Параметры формы временно не используются."
        )
        if bc2.button("↩ Вернуть форму"):
            del st.session_state["applied_options"]
            st.session_state.pop("applied_label", None)
            st.session_state.pop("applied_vpp_request", None)
            st.rerun()
        calc_options = _applied
        calc_mode = "max_kit"
        # v0.12.32: ВПП пересобираем под этажность/парковку применённой карточки
        # (rec_options несёт built_in_list базы; vpp_request подбора сохранён при
        # «В расчёт»). Без этого площадь на «Расчёте» расходилась бы с карточкой.
        calc_vpp_request = st.session_state.get("applied_vpp_request")
        calc_vpp_auto = False
    else:
        calc_options = inputs.options
        calc_mode = inputs.mode
        calc_vpp_request = inputs.vpp_request
        calc_vpp_auto = inputs.vpp_auto_one_floor
    try:
        result = run_calculation(
            site=inputs.site,
            options=calc_options,
            norms=norms,
            mode=calc_mode,
            target_surplus_m2=inputs.target_surplus_m2,
            verify_kit_value=inputs.verify_kit_value,
            vpp_auto_one_floor=calc_vpp_auto,
            vpp_request=calc_vpp_request,
        )
    except Exception as e:
        st.error(f"Ошибка расчёта: {e}")
        with st.expander("Подробно (traceback)"):
            import traceback
            st.code(traceback.format_exc())
        st.stop()

    render_header(result)
    # v0.7.3: «Добавить в сравнение» теперь ВНУТРИ блока «Основные показатели»
    # — render_kpi с scenario_default_name делает actions inline.
    _name_prefix = (
        f"[применён] {st.session_state.get('applied_label','')} · "
        if _applied is not None else ""
    )
    render_kpi(
        result,
        scenario_default_name=_name_prefix + auto_scenario_name(
            inputs.site,
            calc_options,
            calc_mode,
            target_surplus_m2=inputs.target_surplus_m2,
            verify_kit_value=inputs.verify_kit_value,
        ),
        options=calc_options,
    )
    render_details(result)

with tab_optimize:
    render_optimizer_tab(
        site=inputs.site,
        base_options=inputs.options,
        norms=norms,
        vpp_request=inputs.vpp_request,  # v0.12.14: сохранить режим ВПП в подборе
    )

with tab_compare:
    render_comparison_tab()


# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------

st.markdown("---")
st.caption(
    "© 2026. Модель застройки территории — обратный расчёт ТЭП. "
    "Все права защищены. Использование, копирование и распространение — "
    "только с письменного согласия автора. "
    "По вопросам сотрудничества: **misht.cad@gmail.com**."
)
