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

echo "安装 PyTorch (如果是在 Windows 下请手动调整 CUDA 版本, 此脚本提供兼容支持)..."
pip install torch torchvision torchaudio

echo "安装 manga-image-translator 运行时依赖..."
pip install -r https://raw.githubusercontent.com/zyddnys/manga-image-translator/main/requirements.txt

echo "安装 FastAPI 等依赖..."
pip install -r requirements.txt

echo "安装 manga-image-translator 核心引擎..."
pip install git+https://github.com/zyddnys/manga-image-translator.git

echo -e "\n[2/3] 正在检查并安装前端依赖..."
if [ ! -f "../frontend/package.json" ]; then
    echo "[错误] 缺少 frontend/package.json，前端项目未就绪。"
    exit 1
fi
cd ../frontend
npm install

echo -e "\n[3/3] 正在启动服务..."
echo "后台启动后端 API..."
cd ../backend
uvicorn main:app --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!

echo "等待后端启动..."
sleep 3

echo "启动前端服务..."
cd ../frontend
npm run dev -- --open &
FRONTEND_PID=$!

echo -e "\n==================================================="
echo "服务已全部启动！"
echo "后端 API: http://localhost:8000"
echo "前端 WebUI: http://localhost:5173"
echo "按 Ctrl+C 停止所有服务"
echo "==================================================="

# 捕获 Ctrl+C 关闭子进程
trap "echo '正在关闭服务...'; kill $BACKEND_PID $FRONTEND_PID; exit 0" SIGINT
wait
