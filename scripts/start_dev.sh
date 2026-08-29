#!/usr/bin/env bash
set -u

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/rag_llm_server"
ENV_FILE="$BACKEND_DIR/.env"
LOG_DIR="$ROOT_DIR/.dev-logs"

FRONTEND_PORT=3000
BACKEND_PORT=3001
CONDA_ENV_NAME="${CONDA_ENV_NAME:-project1}"

pids=()

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

read_env_value() {
  local key="$1"
  local file="$2"

  if [[ ! -f "$file" ]]; then
    return 0
  fi

  grep -E "^${key}=" "$file" | tail -n 1 | cut -d '=' -f 2- | sed "s/^['\"]//;s/['\"]$//"
}

activate_conda_env() {
  if ! command_exists conda; then
    echo "[backend] conda not found, using current python."
    return 0
  fi

  local conda_base
  conda_base="$(conda info --base 2>/dev/null || true)"

  if [[ -n "$conda_base" && -f "$conda_base/etc/profile.d/conda.sh" ]]; then
    # shellcheck source=/dev/null
    source "$conda_base/etc/profile.d/conda.sh"
  else
    eval "$(conda shell.bash hook)"
  fi

  conda activate "$CONDA_ENV_NAME"
}

cleanup() {
  echo
  echo "[dev] stopping frontend, backend and ngrok..."
  for pid in "${pids[@]}"; do
    if kill -0 "$pid" >/dev/null 2>&1; then
      kill "$pid" >/dev/null 2>&1 || true
    fi
  done
}

trap cleanup INT TERM EXIT

mkdir -p "$LOG_DIR"
: > "$LOG_DIR/frontend.log"
: > "$LOG_DIR/backend.log"
: > "$LOG_DIR/ngrok.log"

SERVER_URL="$(read_env_value "SERVER_URL" "$ENV_FILE")"
NGROK_DOMAIN="${NGROK_DOMAIN:-}"

if [[ -z "$NGROK_DOMAIN" && "$SERVER_URL" =~ ^https?://([^/]+) ]]; then
  SERVER_HOST="${BASH_REMATCH[1]}"
  case "$SERVER_HOST" in
    *.ngrok-free.dev | *.ngrok-free.app | *.ngrok.app | *.ngrok.io)
      NGROK_DOMAIN="$SERVER_HOST"
      ;;
  esac
fi

echo "[dev] frontend: http://localhost:${FRONTEND_PORT}"
echo "[dev] backend:  http://localhost:${BACKEND_PORT}"
if [[ -n "$NGROK_DOMAIN" ]]; then
  echo "[dev] ngrok:    https://${NGROK_DOMAIN}"
else
  echo "[dev] ngrok:    random public url; keep SERVER_URL in rag_llm_server/.env in sync."
fi
echo "[dev] logs:     $LOG_DIR"
echo "[dev] press Control+C to stop all services."
echo

(
  cd "$BACKEND_DIR" || exit 1
  activate_conda_env
  python main.py
) > "$LOG_DIR/backend.log" 2>&1 &
pids+=("$!")

(
  cd "$ROOT_DIR" || exit 1
  npm run start
) > "$LOG_DIR/frontend.log" 2>&1 &
pids+=("$!")

if command_exists ngrok; then
  if [[ -n "$NGROK_DOMAIN" ]]; then
    ngrok http --domain="$NGROK_DOMAIN" "$BACKEND_PORT" > "$LOG_DIR/ngrok.log" 2>&1 &
  else
    ngrok http "$BACKEND_PORT" > "$LOG_DIR/ngrok.log" 2>&1 &
  fi
  pids+=("$!")
else
  echo "[ngrok] ngrok not found. Install or configure ngrok before testing voice callback." > "$LOG_DIR/ngrok.log"
fi

tail -n +1 -f "$LOG_DIR/backend.log" "$LOG_DIR/frontend.log" "$LOG_DIR/ngrok.log" &
pids+=("$!")

wait
