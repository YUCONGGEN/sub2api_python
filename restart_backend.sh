#!/bin/bash
set -euo pipefail

APP_DIR="${SUB2API_DIR:-/Users/yu/bin/sub2api}"
PYTHON="${SUB2API_PYTHON:-$APP_DIR/.venv/bin/python}"
HOST="${SUB2API_BACKEND_HOST:-0.0.0.0}"
PORT="${SUB2API_BACKEND_PORT:-8241}"
RUN_DIR="$APP_DIR/.run"
LOG_DIR="$APP_DIR/log"
PID_FILE="$RUN_DIR/backend.pid"
LOG_FILE="$LOG_DIR/backend-console.log"

mkdir -p "$RUN_DIR" "$LOG_DIR"

stop_managed_process() {
  if [ ! -f "$PID_FILE" ]; then
    return
  fi
  local pid
  pid="$(tr -dc '0-9' < "$PID_FILE")"
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    kill -TERM "$pid"
    local remaining=30
    while kill -0 "$pid" 2>/dev/null && [ "$remaining" -gt 0 ]; do
      sleep 1
      remaining=$((remaining - 1))
    done
    if kill -0 "$pid" 2>/dev/null; then
      kill -KILL "$pid"
    fi
  fi
  rm -f "$PID_FILE"
}

stop_managed_process

if [ ! -x "$PYTHON" ]; then
  echo "Python virtual environment not found: $PYTHON" >&2
  exit 1
fi

occupied="$(/usr/sbin/lsof -nP -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
if [ -n "$occupied" ]; then
  echo "Port $PORT is already occupied by PID(s): $occupied" >&2
  exit 1
fi

if [ -f "$APP_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$APP_DIR/.env"
  set +a
fi

cd "$APP_DIR"
nohup "$PYTHON" -m uvicorn app:app --host "$HOST" --port "$PORT" --no-access-log >> "$LOG_FILE" 2>&1 < /dev/null &
pid=$!
echo "$pid" > "$PID_FILE"

for _ in $(seq 1 60); do
  if /usr/bin/curl -fsS --max-time 3 "http://127.0.0.1:$PORT/api/config/public" >/dev/null 2>&1; then
    echo "Backend restarted: pid=$pid host=$HOST port=$PORT"
    exit 0
  fi
  if ! kill -0 "$pid" 2>/dev/null; then
    break
  fi
  sleep 1
done

echo "Backend failed to become healthy. See $LOG_FILE" >&2
tail -n 80 "$LOG_FILE" >&2 || true
exit 1
