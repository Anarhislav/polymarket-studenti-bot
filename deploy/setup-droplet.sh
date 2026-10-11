#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu 24.04 DigitalOcean Droplet. Run as root:
#
#   curl -fsSL https://raw.githubusercontent.com/Anarhislav/polymarket-studenti-bot/main/deploy/setup-droplet.sh | bash
#
# or copy this file to the Droplet and run `bash setup-droplet.sh`. Safe to run again: it updates
# the checkout and restarts the service instead of failing.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/Anarhislav/polymarket-studenti-bot.git}"
BOT_USER="bot"
APP_DIR="/home/${BOT_USER}/polymarket-studenti-bot"
UNIT="polymarket-bot.service"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "run as root" >&2
  exit 1
fi

echo "==> packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q git python3-venv ufw

echo "==> clock sync (the cutoff is a wall-clock time)"
timedatectl set-ntp true

echo "==> firewall: SSH only"
ufw allow OpenSSH
ufw --force enable

echo "==> user ${BOT_USER}"
if ! id "${BOT_USER}" >/dev/null 2>&1; then
  adduser --disabled-password --gecos "" "${BOT_USER}"
fi
if [[ -f /root/.ssh/authorized_keys ]]; then
  install -d -m 700 -o "${BOT_USER}" -g "${BOT_USER}" "/home/${BOT_USER}/.ssh"
  install -m 600 -o "${BOT_USER}" -g "${BOT_USER}" /root/.ssh/authorized_keys "/home/${BOT_USER}/.ssh/authorized_keys"
fi

echo "==> code"
if [[ -d "${APP_DIR}/.git" ]]; then
  sudo -u "${BOT_USER}" git -C "${APP_DIR}" pull --ff-only
else
  sudo -u "${BOT_USER}" git clone "${REPO_URL}" "${APP_DIR}"
fi

echo "==> python environment"
sudo -u "${BOT_USER}" python3 -m venv "${APP_DIR}/.venv"
sudo -u "${BOT_USER}" "${APP_DIR}/.venv/bin/pip" install -q --upgrade pip
sudo -u "${BOT_USER}" "${APP_DIR}/.venv/bin/pip" install -q -r "${APP_DIR}/requirements.txt" -r "${APP_DIR}/requirements-dev.txt"

echo "==> tests"
sudo -u "${BOT_USER}" bash -c "cd '${APP_DIR}' && .venv/bin/python -m pytest -q"

if [[ -f "${APP_DIR}/.env" ]]; then
  chown "${BOT_USER}:${BOT_USER}" "${APP_DIR}/.env"
  chmod 600 "${APP_DIR}/.env"
fi

echo "==> systemd service"
install -m 644 "${APP_DIR}/deploy/${UNIT}" "/etc/systemd/system/${UNIT}"
systemctl daemon-reload
systemctl enable "${UNIT}"
systemctl restart "${UNIT}"

sleep 3
systemctl --no-pager --lines=15 status "${UNIT}" || true
echo
echo "Done. Follow the log with: journalctl -u ${UNIT} -f"
