#!/bin/bash
set -e

echo "==================================================="
echo "Solar-Manga-Translator 启动脚本 (Linux/Mac)"
echo "==================================================="

# 检查 Python 环境
if ! command -v python3 &> /dev/null; then
    echo "[错误] 未找到 Python3，请确保已安装。"
    exit 1
fi
if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info[:2] in {(3, 10), (3, 11)} else 1)'; then
    echo "[错误] 需要 Python 3.10 或 3.11。"
    exit 1
fi

# 检查 Node.js 环境
if ! command -v node &> /dev/null || ! command -v npm &> /dev/null; then
    echo "[错误] 未找到 Node.js (npm)，请确保已安装。"
    exit 1
fi
if ! node -e 'const [major, minor] = process.versions.node.split(".").map(Number); process.exit(major > 22 || (major === 22 && minor >= 12) ? 0 : 1)'; then
    echo "[错误] 需要 Node.js 22.12 或更高版本。"
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

echo "检测硬件并准备对应的 PyTorch 运行时..."
"$VENV_PYTHON" runtime_bootstrap.py --install

BACKEND_DEPS_STAMP="$(pwd)/venv/.solar-dependencies.json"
if ! "$VENV_PYTHON" dependency_state.py check backend --root "$(cd .. && pwd)" --stamp "$BACKEND_DEPS_STAMP"; then
    echo "后端依赖有变化或缺失，正在安装..."
    "$VENV_PYTHON" install_deps.py
    "$VENV_PYTHON" pip_install.py -r requirements.txt
    "$VENV_PYTHON" dependency_state.py mark backend --root "$(cd .. && pwd)" --stamp "$BACKEND_DEPS_STAMP"
else
    echo "后端依赖未变化，跳过 pip 与 Git 准备。"
fi

echo -e "\n[2/3] 正在检查并安装前端依赖..."
if [ ! -f "../frontend/package.json" ]; then
    echo "[错误] 缺少 frontend/package.json，前端项目未就绪。"
    exit 1
fi
cd ../frontend
FRONTEND_DEPS_STAMP="$(pwd)/node_modules/.solar-dependencies.json"
if ! "$VENV_PYTHON" ../backend/dependency_state.py check frontend --root "$(cd .. && pwd)" --stamp "$FRONTEND_DEPS_STAMP"; then
    npm install --registry https://registry.npmmirror.com || npm install --registry https://registry.npmjs.org
    "$VENV_PYTHON" ../backend/dependency_state.py mark frontend --root "$(cd .. && pwd)" --stamp "$FRONTEND_DEPS_STAMP"
else
    echo "前端依赖未变化，跳过 npm install。"
fi

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
