@echo off
chcp 65001 >nul
title Модель застройки территории

echo.
echo  ================================================================
echo     Модель застройки территории  v0.3
echo     Обратный расчёт КИТ / ТЭП  -  Санкт-Петербург
echo  ================================================================
echo.

REM --- Проверяем наличие uv -----------------------------------------
where uv >nul 2>&1
if %errorlevel% neq 0 (
    echo  [1/3] uv не найден. Устанавливаем...
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
        "irm https://astral.sh/uv/install.ps1 | iex"
    if %errorlevel% neq 0 (
        echo.
        echo  ОШИБКА: не удалось установить uv.
        echo  Установите вручную: https://docs.astral.sh/uv/getting-started/installation/
        pause
        exit /b 1
    )
    echo  [1/3] uv установлен.
) else (
    echo  [1/3] uv найден.
)

REM --- Синхронизируем зависимости ------------------------------------
echo  [2/3] Устанавливаем зависимости (только первый раз)...
cd /d "%~dp0"
uv sync --extra dev --quiet
if %errorlevel% neq 0 (
    echo.
    echo  ОШИБКА при установке зависимостей.
    pause
    exit /b 1
)
echo  [2/3] Зависимости готовы.

REM --- Форсируем UTF-8 -----------------------------------------------
set PYTHONIOENCODING=utf-8

REM --- Запускаем -----------------------------------------------------
echo  [3/3] Запуск...
echo.
echo  Выберите режим:
echo    1  Веб-приложение (Streamlit, откроется в браузере)   [по умолчанию]
echo    2  Демо-ноутбук (Jupyter)
echo    3  Интерактивный расчёт (консоль)
echo    4  Тесты
echo.
set "MODE=1"
set /p MODE="  Введите 1, 2, 3 или 4 [1]: "

if "%MODE%"=="1" (
    echo.
    echo  Открываем Streamlit-приложение в браузере...
    echo  Для остановки: нажмите Ctrl+C в этом окне.
    echo.
    uv run streamlit run src\urban_model\ui\app.py
) else if "%MODE%"=="2" (
    echo.
    echo  Открываем Jupyter...
    echo  Для остановки: нажмите Ctrl+C в этом окне.
    echo.
    uv run jupyter notebook notebooks\demo_v0_4.ipynb
) else if "%MODE%"=="3" (
    echo.
    uv run python run.py
) else if "%MODE%"=="4" (
    echo.
    uv run pytest tests\ -v
    pause
) else (
    echo  Неверный выбор.
    pause
)
