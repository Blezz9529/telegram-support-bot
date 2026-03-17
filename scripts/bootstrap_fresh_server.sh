#!/usr/bin/env bash
set -euo pipefail

# Idempotent bootstrap for a fresh Ubuntu/Debian server.
# Installs Docker, docker-compose (v2 shim), Nginx, prepares static root,
# installs Nginx site config from repo, then runs deploy_production_server.sh.

PROJECT_ROOT="${PROJECT_ROOT:-/root/telegram-support-bot}"
NGINX_HTML_ROOT="${NGINX_HTML_ROOT:-/root/nginx-proxy/html}"
NGINX_SITE_NAME="${NGINX_SITE_NAME:-widget}"

require_root() { [[ ${EUID:-0} -eq 0 ]] || { echo "Run as root" >&2; exit 1; }; }
has() { command -v "$1" >/dev/null 2>&1; }

require_root

apt_update_once() {
  if [[ ! -f /var/lib/apt/periodic/update-success-stamp ]] || \
     find /var/lib/apt/periodic/update-success-stamp -mtime +1 >/dev/null 2>&1; then
    apt-get update -y
  fi
}

echo "==> Installing base packages (Docker, Nginx, curl, rsync)"
apt_update_once
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  docker.io docker-compose-plugin nginx curl rsync ca-certificates >/dev/null

# Enable/start services
systemctl enable --now docker >/dev/null 2>&1 || true
systemctl enable --now nginx >/dev/null 2>&1 || true

# Provide docker-compose shim if only `docker compose` exists
if ! has docker-compose && has docker; then
  if docker compose version >/dev/null 2>&1; then
    cat >/usr/local/bin/docker-compose <<'SH'
#!/usr/bin/env bash
exec docker compose "$@"
SH
    chmod +x /usr/local/bin/docker-compose
  fi
fi

echo "==> Ensuring static root: $NGINX_HTML_ROOT"
mkdir -p "$NGINX_HTML_ROOT"

echo "==> Installing Nginx site config"
SITE_AVAIL="/etc/nginx/sites-available/$NGINX_SITE_NAME"
SITE_ENABLED="/etc/nginx/sites-enabled/$NGINX_SITE_NAME"

if [[ -f "$PROJECT_ROOT/widget-nginx.conf" ]]; then
  cp "$PROJECT_ROOT/widget-nginx.conf" "$SITE_AVAIL"
  # No templating is required; config already proxies to 8001/8002 and serves static.
  ln -sf "$SITE_AVAIL" "$SITE_ENABLED"
  # Remove default site to avoid conflicts
  rm -f /etc/nginx/sites-enabled/default || true
  nginx -t
  systemctl reload nginx
else
  echo "WARNING: widget-nginx.conf not found in repo. Skipping Nginx site install." >&2
fi

echo "==> Running project deploy script"
bash "$PROJECT_ROOT/scripts/deploy_production_server.sh"

echo "Bootstrap finished."
