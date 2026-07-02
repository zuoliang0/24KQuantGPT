#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${PROJECT_ROOT}/.env"
VENV_DIR="${PROJECT_ROOT}/.venv"
VENV_PYTHON="${VENV_DIR}/bin/python"
FRONTEND_ROOT="${PROJECT_ROOT}/frontend"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8004}"
OPEN_BROWSER="${OPEN_BROWSER:-1}"
PROXY_URL="${PROXY_URL:-socks5://127.0.0.1:7890}"
FACTOR_DASHBOARD_URL="http://${HOST}:${PORT}/#factor-mining"

require_macos() {
  if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "这个启动脚本只面向 Mac 本地调试。当前系统：$(uname -s)" >&2
    exit 1
  fi
}

require_command() {
  local command_name="$1"
  local install_hint="$2"

  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "找不到 ${command_name}。${install_hint}" >&2
    exit 1
  fi
}

pick_python() {
  if command -v python3.12 >/dev/null 2>&1; then
    command -v python3.12
    return
  fi

  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return
  fi

  echo "找不到 Python。请先执行：brew install python@3.12" >&2
  exit 1
}

validate_python_version() {
  local python_bin="$1"

  "${python_bin}" - <<'PY'
import sys

version = sys.version_info
if not ((3, 10) <= (version.major, version.minor) <= (3, 12)):
    raise SystemExit(
        f"需要 Python 3.10 到 3.12，当前版本是 {version.major}.{version.minor}.{version.micro}。"
        "建议执行：brew install python@3.12"
    )
PY
}

run_network_command() {
  local action_name="$1"
  shift

  if "$@"; then
    return
  fi

  echo "${action_name} 失败，正在使用本地代理重试一次：${PROXY_URL}" >&2
  ALL_PROXY="${PROXY_URL}" HTTPS_PROXY="${PROXY_URL}" HTTP_PROXY="${PROXY_URL}" "$@"
}

ensure_env_file() {
  if [[ -f "${ENV_FILE}" ]]; then
    return
  fi

  if [[ ! -f "${PROJECT_ROOT}/.env.example" ]]; then
    echo "找不到 .env，也找不到 .env.example。请先创建 ${ENV_FILE}" >&2
    exit 1
  fi

  cp "${PROJECT_ROOT}/.env.example" "${ENV_FILE}"
  echo "已从 .env.example 创建 .env。需要接入 PostgreSQL、DeepSeek 或 WQ 时，请先编辑这个文件。"
}

ensure_port_available() {
  if [[ ! "${PORT}" =~ ^[0-9]+$ ]]; then
    echo "PORT 必须是数字，当前值：${PORT}" >&2
    exit 1
  fi

  if ! lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
    return
  fi

  echo "端口 ${PORT} 已被占用，当前监听进程如下：" >&2
  lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN >&2
  echo "可以换端口启动，例如：PORT=8005 ./start.sh" >&2
  exit 1
}

ensure_python_env() {
  local python_bin="$1"

  if [[ ! -x "${VENV_PYTHON}" ]]; then
    echo "正在创建 Python 虚拟环境：${VENV_DIR}"
    "${python_bin}" -m venv "${VENV_DIR}"
  fi

  validate_python_version "${VENV_PYTHON}"

  if "${VENV_PYTHON}" -c "import asyncpg, fastapi, sqlalchemy, uvicorn" >/dev/null 2>&1; then
    echo "Python 依赖已就绪。"
    return
  fi

  echo "正在安装 Python 依赖..."
  run_network_command "升级 pip" "${VENV_PYTHON}" -m pip install --upgrade pip
  run_network_command "安装后端依赖" "${VENV_PYTHON}" -m pip install -e ".[dev,postgresql]"
  "${VENV_PYTHON}" -c "import asyncpg, fastapi, sqlalchemy, uvicorn" >/dev/null
}

ensure_frontend_env() {
  require_command npm "请先执行：brew install node"

  if [[ ! -d "${FRONTEND_ROOT}/node_modules" ]]; then
    echo "正在安装前端依赖..."
    (
      cd "${FRONTEND_ROOT}"
      run_network_command "安装前端依赖" npm ci
    )
  else
    echo "前端依赖已就绪。"
  fi

  echo "正在构建前端页面..."
  (
    cd "${FRONTEND_ROOT}"
    npm run build
  )
}

open_browser_after_start() {
  if [[ "${OPEN_BROWSER}" != "1" ]]; then
    return
  fi

  (
    sleep 2
    open "${FACTOR_DASHBOARD_URL}"
  ) >/dev/null 2>&1 &
}

main() {
  require_macos
  require_command lsof "Mac 默认应该自带 lsof，请检查系统环境。"
  ensure_env_file
  ensure_port_available

  local system_python
  system_python="$(pick_python)"
  validate_python_version "${system_python}"
  ensure_python_env "${system_python}"
  ensure_frontend_env

  echo "因子看板地址：${FACTOR_DASHBOARD_URL}"
  echo "本地服务地址：http://${HOST}:${PORT}"
  echo "按 Control+C 停止服务。"
  open_browser_after_start

  exec "${VENV_PYTHON}" -m quantgpt --transport http --host "${HOST}" --port "${PORT}"
}

main
