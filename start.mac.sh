#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
MAC_VENV_DIR="$BACKEND_DIR/.venv-mac"
MAC_PYTHON=""
MAC_OPEN_BROWSER="${MAC_OPEN_BROWSER:-1}"

find_python() {
  local candidate=""

  for candidate in python3.11 python3.10; do
    if command -v "$candidate" >/dev/null 2>&1; then
      command -v "$candidate"
      return 0
    fi
  done

  if command -v python3 >/dev/null 2>&1; then
    local version
    version="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    case "$version" in
      3.10|3.11)
        command -v python3
        return 0
        ;;
    esac
  fi

  return 1
}

echo "==================================================="
echo "Solar-Manga-Translator 启动脚本 (macOS / 独立测试环境)"
echo "==================================================="
echo "此脚本仅使用 backend/.venv-mac，不会触碰 Windows 使用的 backend/venv。"

if ! command -v npm >/dev/null 2>&1; then
  echo "[错误] 未找到 npm，请先安装 Node.js。"
  exit 1
fi
if ! node -e 'const [major, minor] = process.versions.node.split(".").map(Number); process.exit(major > 22 || (major === 22 && minor >= 12) ? 0 : 1)'; then
  echo "[错误] 需要 Node.js 22.12 或更高版本。"
  exit 1
fi

if ! MAC_PYTHON="$(find_python)"; then
  echo "[错误] 未找到 Python 3.10 或 3.11。"
  echo "建议先安装 Python 3.11，然后重新运行本脚本。"
  exit 1
fi

echo "[信息] 使用 Python: $MAC_PYTHON"

if [ ! -d "$MAC_VENV_DIR" ]; then
  echo "[1/4] 创建独立 mac 虚拟环境..."
  if command -v uv >/dev/null 2>&1; then
    uv venv --python "$MAC_PYTHON" "$MAC_VENV_DIR"
  else
    "$MAC_PYTHON" -m venv "$MAC_VENV_DIR"
  fi
fi

echo "[2/4] 检查并安装后端依赖..."
"$MAC_VENV_DIR/bin/python" -m ensurepip --upgrade >/dev/null 2>&1 || true
"$MAC_VENV_DIR/bin/python" -m pip install --upgrade pip >/dev/null 2>&1 || true

if ! "$MAC_VENV_DIR/bin/python" -c "import cv2, numpy, fastapi, uvicorn, PIL, websockets, dotenv" >/dev/null 2>&1; then
  "$MAC_VENV_DIR/bin/python" -m pip install -r "$BACKEND_DIR/requirements.txt" numpy opencv-python pillow
fi

echo "[2.5/4] 准备固定版本的 manga-image-translator 核心引擎..."
(
  cd "$BACKEND_DIR"
  "$MAC_VENV_DIR/bin/python" install_deps.py --prepare-only
)

if ! PYTHONPATH="$BACKEND_DIR/manga-image-translator" "$MAC_VENV_DIR/bin/python" -c "import langcodes, manga_translator" >/dev/null 2>&1; then
  echo "[2.6/4] 补齐 manga-image-translator 运行依赖..."
  (
    cd "$BACKEND_DIR"
    "$MAC_VENV_DIR/bin/python" install_deps.py
    "$MAC_VENV_DIR/bin/python" -m pip install -r "$BACKEND_DIR/requirements.txt" numpy opencv-python pillow
  )
fi

echo "[3/4] 检查前端依赖..."
if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
  (cd "$FRONTEND_DIR" && npm install)
fi

echo "[4/4] 启动服务..."
API_TOKEN="$("$MAC_VENV_DIR/bin/python" -c 'import secrets; print(secrets.token_urlsafe(32))')"
cd "$BACKEND_DIR"
APP_API_TOKEN="$API_TOKEN" "$MAC_VENV_DIR/bin/python" -m uvicorn main:app --host 127.0.0.1 --port 8000 &
BACKEND_PID=$!

echo "等待后端启动..."
sleep 3

cd "$FRONTEND_DIR"
FRONTEND_PORT="$(node "$FRONTEND_DIR/scripts/find-free-port.mjs" "${FRONTEND_PORT:-${VITE_DEV_PORT:-5173}}")"
FRONTEND_URL="http://localhost:$FRONTEND_PORT"
if [ "$MAC_OPEN_BROWSER" = "0" ]; then
  FRONTEND_PORT="$FRONTEND_PORT" VITE_DEV_PORT="$FRONTEND_PORT" VITE_API_TOKEN="$API_TOKEN" npm run dev -- --host 127.0.0.1 --port "$FRONTEND_PORT" --strictPort &
else
  FRONTEND_PORT="$FRONTEND_PORT" VITE_DEV_PORT="$FRONTEND_PORT" VITE_API_TOKEN="$API_TOKEN" npm run dev -- --host 127.0.0.1 --port "$FRONTEND_PORT" --strictPort --open "$FRONTEND_URL" &
fi
FRONTEND_PID=$!

echo "==================================================="
echo "mac 测试环境已启动"
echo "后端 API: http://localhost:8000"
echo "前端 WebUI: $FRONTEND_URL"
echo "按 Ctrl+C 停止所有服务"
echo "==================================================="

cleanup() {
  echo "正在关闭 mac 测试环境..."
  kill "$BACKEND_PID" "$FRONTEND_PID" >/dev/null 2>&1 || true
}

trap cleanup SIGINT SIGTERM EXIT
wait
