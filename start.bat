@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

:: Get absolute path to the directory containing this script, without a trailing slash.
for %%I in ("%~dp0.") do set "ROOT_DIR=%%~fI"
if defined LOCALAPPDATA (
    set "APP_DATA_ROOT=%LOCALAPPDATA%\Solar-Manga-Translator"
) else (
    set "APP_DATA_ROOT=%ROOT_DIR%\.runtime"
)
set "BOOTSTRAP_LOG_DIR=%APP_DATA_ROOT%\logs"
set "BOOTSTRAP_LOG=%BOOTSTRAP_LOG_DIR%\bootstrap.log"
if not exist "%BOOTSTRAP_LOG_DIR%" mkdir "%BOOTSTRAP_LOG_DIR%"
for /f "usebackq delims=" %%T in (`powershell -NoProfile -Command "Get-Date -Format 'yyyy-MM-dd HH:mm:ss'"`) do set "BOOTSTRAP_TIMESTAMP=%%T"
echo.>> "%BOOTSTRAP_LOG%"
echo [!BOOTSTRAP_TIMESTAMP!] Starting dependency bootstrap.>> "%BOOTSTRAP_LOG%"

echo ===================================================
echo Solar-Manga-Translator Start Script
echo ===================================================
echo Detailed installation log: %BOOTSTRAP_LOG%

call :stop_existing_service_on_port 8000 "uvicorn main:app"

:: Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [Error] Python not found. Please install Python.
    pause
    exit /b
)
python -c "import sys; raise SystemExit(0 if sys.version_info[:2] in {(3, 10), (3, 11)} else 1)" >nul 2>&1
if %errorlevel% neq 0 (
    echo [Error] Python 3.10 or 3.11 is required.
    pause
    exit /b
)

:: Check Node.js
node --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [Error] Node.js not found. Please install Node.js.
    pause
    exit /b
)
node -e "const [major, minor] = process.versions.node.split('.').map(Number); process.exit(major > 22 || (major === 22 && minor >= 12) ? 0 : 1)" >nul 2>&1
if %errorlevel% neq 0 (
    echo [Error] Node.js 22.12 or newer is required.
    pause
    exit /b
)

echo.
echo [1/3] Installing Backend Dependencies...
cd /d "%ROOT_DIR%"
cd backend
if not exist venv (
    echo Creating Python venv...
    python -m venv venv >> "%BOOTSTRAP_LOG%" 2>&1
    if !errorlevel! neq 0 (
        echo [Error] Failed to create the Python virtual environment.
        call :show_bootstrap_log
        pause
        exit /b 1
    )
)
call venv\Scripts\activate.bat
set "VENV_PYTHON=%CD%\venv\Scripts\python.exe"

echo Detecting GPU and preparing the matching PyTorch runtime...
"%VENV_PYTHON%" runtime_bootstrap.py --install
if %errorlevel% neq 0 (
    echo [Error] PyTorch runtime setup failed. See the log excerpt below, then rerun start.bat.
    call :show_bootstrap_log
    pause
    exit /b 1
)
set "BACKEND_DEPS_STAMP=%CD%\venv\.solar-dependencies.json"
"%VENV_PYTHON%" dependency_state.py check backend --root "%ROOT_DIR%" --stamp "%BACKEND_DEPS_STAMP%" >nul 2>&1
if %errorlevel% neq 0 (
    echo Backend dependencies changed or are missing; installing...
    "%VENV_PYTHON%" pip_install.py -r requirements.txt >> "%BOOTSTRAP_LOG%" 2>&1
    if !errorlevel! neq 0 (
        echo [Error] Failed to install backend requirements.
        call :show_bootstrap_log
        pause
        exit /b 1
    )
    "%VENV_PYTHON%" install_deps.py >> "%BOOTSTRAP_LOG%" 2>&1
    if !errorlevel! neq 0 (
        echo [Error] Failed to install or prepare manga-image-translator.
        call :show_bootstrap_log
        pause
        exit /b 1
    )
    "%VENV_PYTHON%" dependency_state.py mark backend --root "%ROOT_DIR%" --stamp "%BACKEND_DEPS_STAMP%"
) else (
    echo Backend dependencies are unchanged; skipping pip and Git setup.
)

echo.
echo [2/3] Installing Frontend Dependencies...
cd /d "%ROOT_DIR%"
if not exist frontend\package.json (
    echo [Error] Frontend project files are missing.
    pause
    exit /b
)
cd frontend
set "FRONTEND_DEPS_STAMP=%CD%\node_modules\.solar-dependencies.json"
"%VENV_PYTHON%" "%ROOT_DIR%\backend\dependency_state.py" check frontend --root "%ROOT_DIR%" --stamp "%FRONTEND_DEPS_STAMP%" >nul 2>&1
if %errorlevel% neq 0 (
    echo Frontend dependencies changed or are missing; installing...
    call npm install --registry https://registry.npmmirror.com >> "%BOOTSTRAP_LOG%" 2>&1
    if !errorlevel! neq 0 (
        echo npmmirror failed; retrying with the official npm registry...
        call npm install --registry https://registry.npmjs.org >> "%BOOTSTRAP_LOG%" 2>&1
    )
    if !errorlevel! neq 0 (
        echo [Error] Failed to install frontend dependencies.
        call :show_bootstrap_log
        pause
        exit /b 1
    )
    "%VENV_PYTHON%" "%ROOT_DIR%\backend\dependency_state.py" mark frontend --root "%ROOT_DIR%" --stamp "%FRONTEND_DEPS_STAMP%"
) else (
    echo Frontend dependencies are unchanged; skipping npm install.
)

echo.
echo [3/3] Starting Services...
echo Launching managed browser session...
set "MANAGED_SCRIPT=%ROOT_DIR%\start.managed.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -File "%MANAGED_SCRIPT%" -RootDir "%ROOT_DIR%"
exit /b %errorlevel%

:show_bootstrap_log
echo.
echo Last installation log lines:
powershell -NoProfile -Command "Get-Content -LiteralPath '%BOOTSTRAP_LOG%' -Tail 80 -ErrorAction SilentlyContinue"
echo Full log: %BOOTSTRAP_LOG%
exit /b

:stop_existing_service_on_port
set "TARGET_PORT=%~1"
set "MATCH_TEXT=%~2"
set "FOUND_MATCHING_PROCESS="

for /f "tokens=5" %%P in ('netstat -ano ^| findstr /r /c:":%TARGET_PORT% .*LISTENING"') do (
    set "PID=%%P"
    if not "!PID!"=="" (
        set "PROCESS_CMD="
        for /f "usebackq delims=" %%C in (`powershell -NoProfile -Command "$process = Get-CimInstance Win32_Process -Filter 'ProcessId=!PID!' -ErrorAction SilentlyContinue; if ($null -ne $process) { $process.CommandLine }"`) do (
            set "PROCESS_CMD=%%C"
        )
        echo !PROCESS_CMD! | findstr /i /c:"%MATCH_TEXT%" >nul
        if !errorlevel! equ 0 (
            if not defined FOUND_MATCHING_PROCESS (
                echo.
                echo [Preflight] Found existing service on port %TARGET_PORT%, stopping stale process...
                set "FOUND_MATCHING_PROCESS=1"
            )
            echo [Preflight] taskkill /PID !PID! /F
            taskkill /PID !PID! /F >nul 2>&1
        )
    )
)

if defined FOUND_MATCHING_PROCESS (
    timeout /t 1 >nul
)
exit /b
