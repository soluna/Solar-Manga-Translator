@echo off
chcp 65001 >nul
setlocal

echo ===================================================
echo Manga Auto-Translator WebUI 启动脚本 (Windows + CUDA)
echo ===================================================

:: 检查 Python 环境
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未找到 Python，请确保已安装 Python 并添加到 PATH 环境变量。
    pause
    exit /b
)

:: 检查 Node.js 环境
node --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未找到 Node.js，请确保已安装 Node.js (用于运行前端)。
    pause
    exit /b
)

echo.
echo [1/3] 正在检查并安装后端依赖...
cd backend
if not exist venv (
    echo 创建 Python 虚拟环境...
    python -m venv venv
)
call venv\Scripts\activate.bat

echo 安装 PyTorch (CUDA 11.8)...
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
echo 安装 FastAPI 等依赖...
pip install -r requirements.txt
echo 安装 manga-image-translator 核心引擎...
pip install git+https://github.com/zyddnys/manga-image-translator.git

echo.
echo [2/3] 正在检查并安装前端依赖...
cd ..\frontend
call npm install

echo.
echo [3/3] 正在启动服务...
echo 正在后台启动后端 API...
cd ..\backend
start "Manga Translator API" cmd /c "call venv\Scripts\activate.bat && uvicorn main:app --host 0.0.0.0 --port 8000"

echo 等待后端启动...
timeout /t 3 >nul

echo 正在启动前端服务并打开浏览器...
cd ..\frontend
start "Manga Translator WebUI" cmd /c "npm run dev -- --open"

echo.
echo ===================================================
echo 服务已全部启动！
echo 后端 API: http://localhost:8000
echo 前端 WebUI: http://localhost:5173 (如果有冲突会是下一个端口)
echo.
echo 请不要关闭弹出的两个命令行窗口，关闭它们即可停止服务。
echo ===================================================
pause
