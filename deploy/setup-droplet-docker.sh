#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu 24.04 DigitalOcean Droplet that runs the bot in Docker.
# Run as root:
#
#   curl -fsSL https://raw.githubusercontent.com/Anarhislav/polymarket-studenti-bot/main/deploy/setup-droplet-docker.sh | bash
#
# Safe to run again: it updates the checkout and rebuilds. Use this or setup-droplet.sh (systemd),
# not both: two bots on one account would place duplicate orders.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/Anarhislav/polymarket-studenti-bot.git}"
APP_DIR="/opt/polymarket-studenti-bot"
CONTAINER_UID=10001

if [[ "$(id -u)" -ne 0 ]]; then
  echo "run as root" >&2
  exit 1
fi

echo "==> geoblock check (orders from a blocked region are rejected)"
curl -fsS https://polymarket.com/api/geoblock || echo "could not reach the geoblock endpoint"
echo

echo "==> packages: git, Docker and the Compose plugin from Ubuntu's repositories"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q git ufw docker.io docker-compose-v2
systemctl enable --now docker

echo "==> clock sync (the cutoff is a wall-clock time)"
timedatectl set-ntp true

echo "==> firewall: SSH only (the bot opens no ports)"
ufw allow OpenSSH
ufw --force enable

if systemctl list-unit-files polymarket-bot.service >/dev/null 2>&1 \
   && systemctl is-enabled polymarket-bot.service >/dev/null 2>&1; then
  echo "==> disabling the systemd deployment so only one bot runs"
  systemctl disable --now polymarket-bot.service
fi

echo "==> code"
if [[ -d "${APP_DIR}/.git" ]]; then
  # Keep a local config.json change (e.g. "trading_mode": "live") across the pull.
  stashed=false
  if ! git -C "${APP_DIR}" diff --quiet -- config.json; then
    git -C "${APP_DIR}" stash push -m "local config" -- config.json
    stashed=true
  fi
  git -C "${APP_DIR}" pull --ff-only
  if [[ "${stashed}" == true ]]; then
    git -C "${APP_DIR}" stash pop
  fi
else
  git clone "${REPO_URL}" "${APP_DIR}"
fi
cd "${APP_DIR}"

echo "==> data directory and .env"
install -d -o "${CONTAINER_UID}" -g "${CONTAINER_UID}" -m 700 data
if [[ ! -f .env ]]; then
  install -m 600 /dev/null .env
  echo "created an empty .env: copy your secrets into ${APP_DIR}/.env"
fi
chmod 600 .env

echo "==> build and start"
docker compose up -d --build

sleep 5
docker compose ps
docker compose logs --tail 20
cat <<EOF

Done. Useful commands (in ${APP_DIR}):
  docker compose logs -f                      follow the log
  docker compose stop                         stop (the bot cancels its orders)
  docker compose run --rm bot -m bot.check --config /app/config.json   read-only check
  bash deploy/update-docker.sh                deploy the latest main

Live trading needs "trading_mode": "live" in ${APP_DIR}/config.json and the keys in .env.
EOF
