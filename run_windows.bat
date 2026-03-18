@echo off
chcp 65001 >/dev/null
echo ===================================================
echo Manga Auto-Translator Windows Startup Script
echo ===================================================

echo [1/3] Checking Python environment...
python --version >/dev/null 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found! Please ensure Python 3.9+ is installed and added to your system PATH.
    pause
    exit /b
)

echo [2/3] Checking and creating virtual environment (venv)...
if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
)

echo [3/3] Activating virtual environment and installing dependencies...
call venv\Scripts\activate.bat

echo Updating pip and setting resolver behavior...
python -m pip install --upgrade pip >/dev/null

echo Installing project dependencies (This may take a while, especially PyTorch)...
:: Add extra-index-url to ensure we fetch CUDA versions correctly during all dependency resolutions
python -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
python -m pip install -r requirements.txt --extra-index-url https://download.pytorch.org/whl/cu118

echo.
echo ===================================================
echo Environment is ready! Starting Web UI...
echo Please access http://127.0.0.1:7860 in your browser
echo ===================================================
python main.py

pause
