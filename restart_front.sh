#!/bin/bash
set -euo pipefail

APP_DIR="${SUB2API_DIR:-/Users/yu/bin/sub2api}"
NODE="${SUB2API_NODE:-/usr/local/bin/node}"
HOST="${SUB2API_FRONTEND_HOST:-0.0.0.0}"
PORT="${SUB2API_FRONTEND_PORT:-8240}"
RUN_DIR="$APP_DIR/.run"
LOG_DIR="$APP_DIR/log"
PID_FILE="$RUN_DIR/frontend.pid"
LOG_FILE="$LOG_DIR/frontend.log"
VITE="$APP_DIR/frontend/node_modules/vite/bin/vite.js"

mkdir -p "$RUN_DIR" "$LOG_DIR"

stop_managed_process() {
  if [ ! -f "$PID_FILE" ]; then
    return
  fi
  local pid
  pid="$(tr -dc '0-9' < "$PID_FILE")"
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    kill -TERM "$pid"
    local remaining=20
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

if [ ! -x "$NODE" ]; then
  echo "Node.js not found: $NODE" >&2
  exit 1
fi
if [ ! -f "$VITE" ]; then
  echo "Vite is not installed. Run npm ci in $APP_DIR/frontend first." >&2
  exit 1
fi

occupied="$(/usr/sbin/lsof -nP -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
if [ -n "$occupied" ]; then
  echo "Port $PORT is already occupied by PID(s): $occupied" >&2
  exit 1
fi

cd "$APP_DIR/frontend"
nohup "$NODE" "$VITE" preview --host "$HOST" --port "$PORT" --strictPort >> "$LOG_FILE" 2>&1 < /dev/null &
pid=$!
echo "$pid" > "$PID_FILE"

for _ in $(seq 1 30); do
  if /usr/bin/curl -fsS --max-time 3 "http://127.0.0.1:$PORT/" >/dev/null 2>&1; then
    echo "Frontend restarted: pid=$pid host=$HOST port=$PORT"
    exit 0
  fi
  if ! kill -0 "$pid" 2>/dev/null; then
    break
  fi
  sleep 1
done

echo "Frontend failed to become healthy. See $LOG_FILE" >&2
tail -n 80 "$LOG_FILE" >&2 || true
exit 1
