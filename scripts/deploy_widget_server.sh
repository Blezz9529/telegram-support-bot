#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/root/telegram-support-bot"
WIDGET_DIR="$ROOT_DIR/widget"
NGINX_HTML_DIR="/root/nginx-proxy/html"

if [ ! -d "$WIDGET_DIR" ]; then
  echo "Widget directory not found: $WIDGET_DIR" >&2
  exit 1
fi

if [ ! -d "$NGINX_HTML_DIR" ]; then
  echo "Nginx html directory not found: $NGINX_HTML_DIR" >&2
  exit 1
fi

cd "$WIDGET_DIR"
npm run build

# Inject favicon and host bridge script into built index.html if missing
if ! grep -q "rel=\"icon\"" "$WIDGET_DIR/dist/index.html"; then
  perl -0777 -i -pe 's#</head>#    <link rel="icon" href="/favicon.ico" />\n  </head>#' "$WIDGET_DIR/dist/index.html"
fi
if ! grep -q "widget-host.js" "$WIDGET_DIR/dist/index.html"; then
  perl -0777 -i -pe 's#</head>#    <script src="./widget-host.js?v=20260311b" data-widget-origin="http://94.103.88.196,https://94.103.88.196"></script>\n  </head>#' "$WIDGET_DIR/dist/index.html"
fi

# Normalize any /widget/ favicon references
perl -0777 -i -pe 's#<link\\s+rel=\\"icon\\"\\s+href=\\"/widget/\\"\\s*/?>#<link rel="icon" href="/favicon.ico" />#g' "$WIDGET_DIR/dist/index.html"

rsync -az --delete "$WIDGET_DIR/dist/" "$NGINX_HTML_DIR/"
cp "$WIDGET_DIR/logs.html" "$NGINX_HTML_DIR/logs.html" || true
if [[ -f "$WIDGET_DIR/dist/widget-loader.js" ]]; then
  cp "$WIDGET_DIR/dist/widget-loader.js" "$NGINX_HTML_DIR/widget-loader.js"
fi

echo "Widget deployed to $NGINX_HTML_DIR"
