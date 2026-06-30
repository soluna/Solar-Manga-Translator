#!/bin/bash
set -e

echo "==================================================="
echo "Manga Auto-Translator WebUI 启动脚本 (Linux/Mac)"
echo "==================================================="

# 检查 Python 环境
if ! command -v python3 &> /dev/null; then
    echo "[错误] 未找到 Python3，请确保已安装。"
    exit 1
fi

# 检查 Node.js 环境
if ! command -v npm &> /dev/null; then
    echo "[错误] 未找到 Node.js (npm)，请确保已安装。"
    exit 1
fi

echo -e "\n[1/3] 正在检查并安装后端依赖..."
cd backend
if [ ! -d "venv" ]; then
    echo "创建 Python 虚拟环境..."
    python3 -m venv venv
fi
source venv/bin/activate
VENV_PYTHON="$(pwd)/venv/bin/python"

echo "安装 PyTorch (如果是在 Windows 下请手动调整 CUDA 版本, 此脚本提供兼容支持)..."
"$VENV_PYTHON" -m pip install --upgrade torch torchvision torchaudio

echo "安装关键运行时依赖..."
"$VENV_PYTHON" -m pip install python-dotenv colorama

echo "安装并准备固定版本的 manga-image-translator 核心引擎..."
"$VENV_PYTHON" install_deps.py

echo "安装 FastAPI 等项目依赖和安全约束..."
"$VENV_PYTHON" -m pip install -r requirements.txt

echo -e "\n[2/3] 正在检查并安装前端依赖..."
if [ ! -f "../frontend/package.json" ]; then
    echo "[错误] 缺少 frontend/package.json，前端项目未就绪。"
    exit 1
fi
cd ../frontend
npm install

echo -e "\n[3/3] 正在启动服务..."
API_TOKEN="$("$VENV_PYTHON" -c 'import secrets; print(secrets.token_urlsafe(32))')"
echo "后台启动后端 API..."
cd ../backend
APP_API_TOKEN="$API_TOKEN" uvicorn main:app --host 127.0.0.1 --port 8000 &
BACKEND_PID=$!

echo "等待后端启动..."
sleep 3

echo "启动前端服务..."
cd ../frontend
FRONTEND_PORT="$(node scripts/find-free-port.mjs "${FRONTEND_PORT:-${VITE_DEV_PORT:-5173}}")"
FRONTEND_URL="http://localhost:$FRONTEND_PORT"
FRONTEND_PORT="$FRONTEND_PORT" VITE_DEV_PORT="$FRONTEND_PORT" VITE_API_TOKEN="$API_TOKEN" npm run dev -- --host 127.0.0.1 --port "$FRONTEND_PORT" --strictPort --open "$FRONTEND_URL" &
FRONTEND_PID=$!

echo -e "\n==================================================="
echo "服务已全部启动！"
echo "后端 API: http://localhost:8000"
echo "前端 WebUI: $FRONTEND_URL"
echo "按 Ctrl+C 停止所有服务"
echo "==================================================="

# 捕获 Ctrl+C 关闭子进程
trap "echo '正在关闭服务...'; kill $BACKEND_PID $FRONTEND_PID; exit 0" SIGINT
wait
