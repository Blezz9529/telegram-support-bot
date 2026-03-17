#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-/root/telegram-support-bot}"
COMPOSE_FILE="${COMPOSE_FILE:-$PROJECT_ROOT/docker-compose.yml}"
PUBLISH_WIDGET_RUNTIME="${PUBLISH_WIDGET_RUNTIME:-1}"
CHECK_WIDGET_API_URL="${CHECK_WIDGET_API_URL:-http://127.0.0.1:8001/api/widget/ui-config}"
CHECK_LOG_STREAMER_URL="${CHECK_LOG_STREAMER_URL:-http://127.0.0.1:8002/openapi.json}"
CHECK_WIDGET_STATIC_FILE="${CHECK_WIDGET_STATIC_FILE:-/root/nginx-proxy/html/widget/index.html}"

require_cmd() { command -v "$1" >/dev/null 2>&1 || { echo "Required command not found: $1" >&2; exit 1; }; }

retry() {
  local attempts="$1"; local delay="$2"; shift 2
  local n=1
  until "$@"; do
    if [[ "$n" -ge "$attempts" ]]; then return 1; fi
    sleep "$delay"; n=$((n + 1))
  done
}

require_cmd docker
require_cmd curl

if [[ ! -d "$PROJECT_ROOT" ]]; then
  echo "Project root not found: $PROJECT_ROOT" >&2; exit 1
fi

if [[ ! -f "$COMPOSE_FILE" ]]; then
  echo "docker-compose.yml not found: $COMPOSE_FILE" >&2; exit 1
fi

cd "$PROJECT_ROOT"

echo "==> Selecting compose implementation"
if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose -f "$COMPOSE_FILE")
else
  echo "docker compose plugin not found — attempting to install..."
  if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y >/dev/null 2>&1 || true
    apt-get install -y --no-install-recommends docker-compose-plugin >/dev/null 2>&1 || true
  fi
  if docker compose version >/dev/null 2>&1; then
    COMPOSE=(docker compose -f "$COMPOSE_FILE")
  else
    echo "compose plugin still unavailable, falling back to docker-compose v1"
    require_cmd docker-compose
    COMPOSE=(docker-compose -f "$COMPOSE_FILE")
  fi
fi

echo "==> Validating docker-compose"
"${COMPOSE[@]}" config -q

echo "==> Ensuring runtime directories"
mkdir -p "$PROJECT_ROOT/logs" "$PROJECT_ROOT/data"

if [[ "$PUBLISH_WIDGET_RUNTIME" == "1" ]]; then
  echo "==> Publishing widget runtime"
  bash "$PROJECT_ROOT/scripts/publish_widget_runtime.sh"
fi

echo "==> Rebuilding containers"
"${COMPOSE[@]}" down --remove-orphans || true
"${COMPOSE[@]}" up -d --build || true

echo "==> Waiting for services"
sleep 5

echo "==> Running checks"
retry 10 3 curl -fsS "$CHECK_WIDGET_API_URL" >/dev/null || true
if ! retry 5 2 curl -fsS "$CHECK_LOG_STREAMER_URL" >/dev/null; then
  echo "==> log-streamer not responding via compose, attempting docker run fallback"
  # Stop compose-managed log-streamer if exists
  "${COMPOSE[@]}" stop log-streamer >/dev/null 2>&1 || true
  docker rm -f log-streamer >/dev/null 2>&1 || true
  # Run log-streamer manually (port 8002 and docker.sock ro)
  docker run -d --name log-streamer \
    -p 8002:8000 \
    -v /var/run/docker.sock:/var/run/docker.sock:ro \
    "$(basename "$PROJECT_ROOT")_log-streamer:latest" \
    uvicorn services.log_streamer:app --host 0.0.0.0 --port 8000
  retry 10 2 curl -fsS "$CHECK_LOG_STREAMER_URL" >/dev/null
fi

if [[ ! -f "$CHECK_WIDGET_STATIC_FILE" ]]; then
  echo "Widget static file not found: $CHECK_WIDGET_STATIC_FILE" >&2; exit 1
fi

echo "==> Container status"
"${COMPOSE[@]}" ps || true

echo "==> Tail logs"
"${COMPOSE[@]}" logs --tail=20 || true

# Verify widget static is served from API; if not, inject dist into container and restart
echo "==> Verifying widget static via API"
if ! curl -fsS -o /dev/null http://127.0.0.1:8001/widget/; then
  echo ".. widget static missing in container; copying dist and restarting widget-api"
  if [[ -d "$PROJECT_ROOT/widget/dist" ]]; then
    CID=$(docker ps --format '{{.ID}} {{.Names}}' | awk '/widget-api/ {print $1}' | head -n1)
    if [[ -n "$CID" ]]; then
      docker exec "$CID" sh -lc 'rm -rf /app/widget/dist && mkdir -p /app/widget/dist' || true
      docker cp "$PROJECT_ROOT/widget/dist/." "$CID":/app/widget/dist/
      "${COMPOSE[@]}" restart widget-api || true
      sleep 2
      curl -fsS -o /dev/null http://127.0.0.1:8001/widget/ || echo "WARNING: widget static still not available"
    fi
  fi
fi

echo "Deployment completed successfully."
