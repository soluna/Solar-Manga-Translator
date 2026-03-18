@echo off
echo ===================================================
echo Manga Auto-Translator Windows 启动脚本
echo ===================================================

echo [1/3] 检查 Python 环境...
python --version >/dev/null 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未找到 Python！请确保已安装 Python 3.9+ 并且添加到了系统环境变量 (PATH) 中。
    pause
    exit /b
)

echo [2/3] 检查并创建虚拟环境 (venv)...
if not exist "venv" (
    echo 正在创建虚拟环境...
    python -m venv venv
)

echo [3/3] 激活虚拟环境并安装依赖...
call venv\Scripts\activate.bat

echo 正在更新 pip...
python -m pip install --upgrade pip >/dev/null

echo 正在安装项目依赖 (这可能需要一些时间，特别是 PyTorch)...
:: 为了利用您的 Nvidia GPU，我们特别指定安装 CUDA 11.8 版本的 PyTorch
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
pip install -r requirements.txt

echo.
echo ===================================================
echo 环境准备就绪！正在启动 Web UI...
echo 请在浏览器中访问 http://127.0.0.1:7860
echo ===================================================
python main.py

pause
