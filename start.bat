@echo off
chcp 65001 >nul
setlocal

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
cd backend
if not exist venv (
    echo Creating Python venv...
    python -m venv venv
)
call venv\Scripts\activate.bat

echo Installing PyTorch (CUDA 11.8)...
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
echo Installing FastAPI and requirements...
pip install -r requirements.txt
echo Installing manga-image-translator core engine...
pip install git+https://github.com/zyddnys/manga-image-translator.git

echo.
echo [2/3] Installing Frontend Dependencies...
cd ..\frontend
call npm install

echo.
echo [3/3] Starting Services...
echo Starting Backend API...
cd ..\backend
start "Manga Translator API" cmd /c "call venv\Scripts\activate.bat && uvicorn main:app --host 0.0.0.0 --port 8000"

echo Waiting for backend to start...
timeout /t 3 >nul

echo Starting Frontend WebUI...
cd ..\frontend
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
