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
echo "[2.1/4] 检测硬件并准备匹配的 PyTorch 运行时..."
"$MAC_VENV_DIR/bin/python" "$BACKEND_DIR/runtime_bootstrap.py" --install
BACKEND_DEPS_STAMP="$MAC_VENV_DIR/.solar-dependencies.json"
if ! "$MAC_VENV_DIR/bin/python" "$BACKEND_DIR/dependency_state.py" check backend --root "$ROOT_DIR" --stamp "$BACKEND_DEPS_STAMP"; then
  "$MAC_VENV_DIR/bin/python" -m pip install --upgrade pip >/dev/null 2>&1 || true
  echo "[2.5/4] 准备固定版本的 manga-image-translator 核心引擎..."
  (
    cd "$BACKEND_DIR"
    "$MAC_VENV_DIR/bin/python" install_deps.py
  )
  "$MAC_VENV_DIR/bin/python" "$BACKEND_DIR/pip_install.py" -r "$BACKEND_DIR/requirements.txt" numpy opencv-python pillow
  "$MAC_VENV_DIR/bin/python" "$BACKEND_DIR/dependency_state.py" mark backend --root "$ROOT_DIR" --stamp "$BACKEND_DEPS_STAMP"
else
  echo "[2.5/4] 后端依赖未变化，跳过 pip 与 Git 准备。"
fi

echo "[3/4] 检查前端依赖..."
FRONTEND_DEPS_STAMP="$FRONTEND_DIR/node_modules/.solar-dependencies.json"
if ! "$MAC_VENV_DIR/bin/python" "$BACKEND_DIR/dependency_state.py" check frontend --root "$ROOT_DIR" --stamp "$FRONTEND_DEPS_STAMP"; then
  (cd "$FRONTEND_DIR" && (npm install --registry https://registry.npmmirror.com || npm install --registry https://registry.npmjs.org))
  "$MAC_VENV_DIR/bin/python" "$BACKEND_DIR/dependency_state.py" mark frontend --root "$ROOT_DIR" --stamp "$FRONTEND_DEPS_STAMP"
else
  echo "[3/4] 前端依赖未变化，跳过 npm install。"
fi

echo "[4/4] 启动服务..."
API_TOKEN="$("$MAC_VENV_DIR/bin/python" -c 'import secrets; print(secrets.token_urlsafe(32))')"
cd "$BACKEND_DIR"
APP_API_TOKEN="$API_TOKEN" "$MAC_VENV_DIR/bin/python" -m uvicorn main:app --host 127.0.0.1 --port 8000 &
BACKEND_PID=$!

echo "等待后端启动..."
sleep 3

cd "$FRONTEND_DIR"
FRONTEND_PORT="$(node "$FRONTEND_DIR/scripts/find-free-port.mjs" "${FRONTEND_PORT:-${VITE_DEV_PORT:-5173}}" 127.0.0.1)"
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
