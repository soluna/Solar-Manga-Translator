#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"

cd "$ROOT_DIR"
exec /bin/bash "$ROOT_DIR/start.mac.sh"
