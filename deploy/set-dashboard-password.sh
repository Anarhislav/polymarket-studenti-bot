#!/usr/bin/env bash
# Set (or change) the dashboard's username and password. Run on the server, from the repo root:
#
#   bash deploy/set-dashboard-password.sh
#
# The password is read without echo and only its bcrypt hash is stored, in deploy/auth.caddy
# (git-ignored, readable by root only). Caddy is reloaded if it is running.
set -euo pipefail

cd "$(dirname "$0")/.."
AUTH_FILE="deploy/auth.caddy"

read -r -p "Dashboard username: " username
if [[ ! "${username}" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "use letters, digits, dot, dash or underscore only" >&2
  exit 1
fi
read -r -s -p "Password: " password; echo
read -r -s -p "Repeat password: " again; echo
if [[ "${password}" != "${again}" ]]; then
  echo "passwords do not match" >&2
  exit 1
fi
if (( ${#password} < 12 )); then
  echo "use at least 12 characters" >&2
  exit 1
fi

hash="$(printf '%s\n' "${password}" | docker run --rm -i caddy:2-alpine caddy hash-password)"
unset password again

umask 077
cat > "${AUTH_FILE}" <<EOF
basic_auth {
	${username} ${hash}
}
EOF
chmod 600 "${AUTH_FILE}"
echo "saved ${AUTH_FILE}"

if docker compose ps --services --status running 2>/dev/null | grep -qx caddy; then
  docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile
  echo "caddy reloaded"
fi
