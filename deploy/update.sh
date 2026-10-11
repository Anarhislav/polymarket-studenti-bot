#!/usr/bin/env bash
# Pull the latest code and restart the bot. Run as root on the Droplet:
#
#   bash /home/bot/polymarket-studenti-bot/deploy/update.sh
#
# The bot cancels its open orders on stop and resumes from state.json on start, so a restart
# keeps the position and the ledger.
set -euo pipefail

BOT_USER="bot"
APP_DIR="/home/${BOT_USER}/polymarket-studenti-bot"
UNIT="polymarket-bot.service"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "run as root" >&2
  exit 1
fi

sudo -u "${BOT_USER}" git -C "${APP_DIR}" pull --ff-only
sudo -u "${BOT_USER}" "${APP_DIR}/.venv/bin/pip" install -q -r "${APP_DIR}/requirements.txt" -r "${APP_DIR}/requirements-dev.txt"
sudo -u "${BOT_USER}" bash -c "cd '${APP_DIR}' && .venv/bin/python -m pytest -q"

install -m 644 "${APP_DIR}/deploy/${UNIT}" "/etc/systemd/system/${UNIT}"
systemctl daemon-reload
systemctl restart "${UNIT}"

sleep 3
systemctl --no-pager --lines=10 status "${UNIT}" || true
