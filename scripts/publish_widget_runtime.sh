#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-/root/telegram-support-bot}"
WIDGET_DIR="${WIDGET_DIR:-$PROJECT_ROOT/widget}"
PUBLIC_ROOT="${PUBLIC_ROOT:-/root/nginx-proxy/html}"
WIDGET_PUBLIC_PATH="${WIDGET_PUBLIC_PATH:-widget}"
DEPLOY_LOGS_PAGE="${DEPLOY_LOGS_PAGE:-0}"

TARGET_WIDGET_DIR="${PUBLIC_ROOT}/${WIDGET_PUBLIC_PATH}"

require_cmd() { command -v "$1" >/dev/null 2>&1 || { echo "Required command not found: $1" >&2; exit 1; }; }

require_cmd rsync

if [[ ! -d "$WIDGET_DIR" ]]; then
  echo "Widget directory not found: $WIDGET_DIR" >&2
  exit 1
fi

if [[ ! -d "$PUBLIC_ROOT" ]]; then
  echo "Public root not found: $PUBLIC_ROOT" >&2
  exit 1
fi

cd "$WIDGET_DIR"

build_with_local_node() {
  if command -v npm >/dev/null 2>&1; then
    if [[ ! -d node_modules || ! -x node_modules/.bin/vite ]]; then
      # Ensure devDependencies are installed even if production npm config is set
      NPM_CONFIG_PRODUCTION=false npm ci --no-audit --progress=false --include=dev || return 1
    fi
    # If vite still missing, bail to caller to try docker
    if [[ ! -x node_modules/.bin/vite ]]; then
      return 1
    fi
    npm run build --silent || return 1
    return $?
  fi
  return 127
}

build_with_docker_node() {
  if ! command -v docker >/dev/null 2>&1; then
    return 127
  fi
  docker run --rm \
    -v "$WIDGET_DIR":/app \
    -w /app \
    -e CI=1 \
    node:18-alpine sh -lc "npm install --no-audit --progress=false --include=dev && npm run build --silent"
}

echo "==> Building widget (docker node preferred, fallback to local)"
# Prefer Docker to avoid host npm inconsistencies
if ! build_with_docker_node; then
  echo ".. docker build failed or docker not available, trying local node"
  build_with_local_node
fi

mkdir -p "$TARGET_WIDGET_DIR"

rsync -az --delete "$WIDGET_DIR/dist/" "$TARGET_WIDGET_DIR/"

if [[ -f "$WIDGET_DIR/dist/widget-host.js" ]]; then
  cp "$WIDGET_DIR/dist/widget-host.js" "$PUBLIC_ROOT/widget-host.js"
fi
if [[ -f "$WIDGET_DIR/dist/widget-loader.js" ]]; then
  cp "$WIDGET_DIR/dist/widget-loader.js" "$PUBLIC_ROOT/widget-loader.js"
fi

if [[ "$DEPLOY_LOGS_PAGE" == "1" && -f "$WIDGET_DIR/logs.html" ]]; then
  cp "$WIDGET_DIR/logs.html" "$PUBLIC_ROOT/logs.html"
fi

echo "Widget runtime published to $TARGET_WIDGET_DIR"
