@echo off
title Urban Model

echo.
echo  ================================================================
echo     Urban Model v0.3  /  TEP inverse calculator  /  SPb norms
echo  ================================================================
echo.

REM --- Check uv -------------------------------------------------------
where uv >nul 2>&1
if %errorlevel% neq 0 (
    echo  [1/3] uv not found. Installing...
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
        "irm https://astral.sh/uv/install.ps1 | iex"
    if %errorlevel% neq 0 (
        echo.
        echo  ERROR: failed to install uv.
        echo  Install manually: https://docs.astral.sh/uv/getting-started/installation/
        pause
        exit /b 1
    )
    echo  [1/3] uv installed.
) else (
    echo  [1/3] uv found.
)

REM --- Sync dependencies ----------------------------------------------
echo  [2/3] Installing dependencies (first time only)...
cd /d "%~dp0"
uv sync --extra dev --quiet
if %errorlevel% neq 0 (
    echo.
    echo  ERROR: dependency installation failed.
    pause
    exit /b 1
)
echo  [2/3] Dependencies ready.

REM --- Force UTF-8 for Python output ----------------------------------
set PYTHONIOENCODING=utf-8

REM --- Launch ---------------------------------------------------------
echo  [3/3] Choose mode:
echo.
echo    1  Web UI - Streamlit  (opens in browser)    [DEFAULT]
echo    2  Demo notebook       (Jupyter)
echo    3  Interactive CLI     (console)
echo    4  Run tests
echo.
set "MODE=1"
set /p MODE="  Enter 1, 2, 3 or 4 [1]: "

if "%MODE%"=="1" (
    echo.
    echo  Starting Streamlit... open http://localhost:8501 in browser.
    echo  To stop: press Ctrl+C in this window.
    echo.
    uv run streamlit run src\urban_model\ui\app.py
) else if "%MODE%"=="2" (
    echo.
    echo  Starting Jupyter...
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
    echo  Invalid choice.
    pause
)
