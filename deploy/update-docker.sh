#!/usr/bin/env bash
# Pull the latest code and rebuild the container. Run as root on the Droplet:
#
#   bash /opt/polymarket-studenti-bot/deploy/update-docker.sh
#
# The bot cancels its open orders on stop and resumes from data/state.live.json on start.
# A local config.json change (e.g. "trading_mode": "live") is kept: git refuses to pull over it,
# so the change is stashed around the pull and restored afterwards.
set -euo pipefail

APP_DIR="/opt/polymarket-studenti-bot"
cd "${APP_DIR}"

stashed=false
if ! git diff --quiet -- config.json; then
  git stash push -m "local config" -- config.json
  stashed=true
fi
git pull --ff-only
if [[ "${stashed}" == true ]]; then
  git stash pop
fi

docker compose up -d --build
sleep 5
docker compose ps
docker compose logs --tail 20
