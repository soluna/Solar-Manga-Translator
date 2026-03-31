@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

:: Get absolute path to the directory containing this script
set "ROOT_DIR=%~dp0"

echo ===================================================
echo Manga Auto-Translator WebUI Start Script
echo ===================================================

call :stop_existing_service_on_port 8000 "uvicorn main:app"
call :stop_existing_service_on_port 5173 "vite"

:: Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [Error] Python not found. Please install Python.
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

echo.
echo [1/3] Installing Backend Dependencies...
cd /d "%ROOT_DIR%"
cd backend
if not exist venv (
    echo Creating Python venv...
    python -m venv venv
)
call venv\Scripts\activate.bat
set "VENV_PYTHON=%CD%\venv\Scripts\python.exe"

echo Installing PyTorch (CUDA 11.8)...
"%VENV_PYTHON%" -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
echo Installing critical runtime dependencies...
"%VENV_PYTHON%" -m pip install python-dotenv colorama
echo Installing manga-image-translator runtime requirements...
"%VENV_PYTHON%" install_deps.py
echo Installing FastAPI and requirements...
"%VENV_PYTHON%" -m pip install -r requirements.txt
echo Preparing manga-image-translator core engine...
if not exist manga-image-translator (
    echo Downloading manga-image-translator repository...
    git clone https://github.com/zyddnys/manga-image-translator.git manga-image-translator
)

echo Patching manga-image-translator to bypass Windows C++ build errors...
"%VENV_PYTHON%" patch_pydensecrf.py

echo.
echo [2/3] Installing Frontend Dependencies...
cd /d "%ROOT_DIR%"
if not exist frontend\package.json (
    echo [Error] Frontend project files are missing.
    pause
    exit /b
)
cd frontend
call npm install

echo.
echo [3/3] Starting Services...
echo Starting Backend API...
cd /d "%ROOT_DIR%"
cd backend
start "Manga Translator API" cmd /c "call venv\Scripts\activate.bat && uvicorn main:app --host 0.0.0.0 --port 8000"

echo Waiting for backend to start...
timeout /t 3 >nul

echo Starting Frontend WebUI...
cd /d "%ROOT_DIR%"
cd frontend
start "Manga Translator WebUI" cmd /c "npm run dev -- --open"

echo.
echo ===================================================
echo All services started!
echo Backend API: http://localhost:8000
echo Frontend WebUI: http://localhost:5173
echo.
echo Please keep the two new command prompt windows open.
echo ===================================================
pause
exit /b

:stop_existing_service_on_port
set "TARGET_PORT=%~1"
set "MATCH_TEXT=%~2"
set "FOUND_MATCHING_PROCESS="

for /f "tokens=5" %%P in ('netstat -ano ^| findstr /r /c:":%TARGET_PORT% .*LISTENING"') do (
    set "PID=%%P"
    if not "!PID!"=="" (
        set "PROCESS_CMD="
        for /f "tokens=* delims=" %%C in ('wmic process where "ProcessId=!PID!" get CommandLine ^| findstr /r /v "^$"') do (
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
