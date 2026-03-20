@echo off
chcp 65001 >nul
setlocal

:: Get absolute path to the directory containing this script
set "ROOT_DIR=%~dp0"

echo ===================================================
echo Manga Auto-Translator WebUI Start Script
echo ===================================================

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
echo Installing manga-image-translator core engine...
"%VENV_PYTHON%" -m pip install git+https://github.com/zyddnys/manga-image-translator.git

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
