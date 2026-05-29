@echo off
title Urban Model - Public Link (cloudflared)
cd /d "%~dp0"

echo.
echo  ================================================================
echo    Urban Model  /  Public link via Cloudflare Tunnel
echo  ================================================================
echo.
echo  Пока это окно открыто - сайт доступен по публичной ссылке.
echo.

REM --- 1. cloudflared: найти или скачать --------------------------------
set "CF=cloudflared"
where cloudflared >nul 2>&1
if %errorlevel% neq 0 (
    if not exist "cloudflared.exe" (
        echo  [1/3] cloudflared не найден - скачиваю...
        powershell -NoProfile -ExecutionPolicy Bypass -Command ^
            "Invoke-WebRequest -Uri 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe' -OutFile 'cloudflared.exe'"
        if %errorlevel% neq 0 (
            echo  ERROR: не удалось скачать cloudflared.
            echo  Скачайте вручную: https://github.com/cloudflare/cloudflared/releases/latest
            pause
            exit /b 1
        )
    )
    set "CF=%~dp0cloudflared.exe"
)
echo  [1/3] cloudflared готов.

REM --- 2. Запуск Streamlit в отдельном окне -----------------------------
echo  [2/3] Запускаю Streamlit (http://localhost:8501)...
start "Urban Model server" cmd /c ^
    "uv run streamlit run src\urban_model\ui\app.py --server.headless true --server.port 8501"

echo  Жду запуск сервера (10 сек)...
timeout /t 10 /nobreak >nul

REM --- 3. Туннель: публичная ссылка -------------------------------------
echo.
echo  [3/3] Создаю публичную ссылку. Адрес вида
echo        https://XXXX.trycloudflare.com  появится НИЖЕ.
echo        Скопируйте его и отправьте коллегам.
echo.
echo  Остановить раздачу: закройте это окно (или Ctrl+C).
echo  ================================================================
echo.
"%CF%" tunnel --url http://localhost:8501

pause
