# Polymarket bot: Student List range trading

The bot trades the market "Will Student List win the most seats in the 2026 Serbian parliamentary
election?" using the rules in `config.json`. By default it runs **in paper mode**: it reads
the real order book but never signs or sends an order.

## Rules

All levels are YES prices.

| Phase | Buy | Sell |
|---|---|---|
| YES | everything at 38% | everything at 42% |
| NO | everything when YES reaches 42% (NO at 58¢) | everything when YES falls to 38% (NO at 62¢) |

Levels and the share of the budget per level are lists in `config.json` (`allocation`,
`yes_buy_levels`, ...), so a multi-level ladder is still possible. A state saved with a different
number of levels is carried over on start: the position and its cost stay, and all unspent cash
goes to the new buy levels.

- **Budget** (`budget_usd`): a dollar amount, or `"all"` to trade the wallet's whole pUSD cash
  balance (live only). With `"all"` the bot reads the balance at every start: a difference of
  $1 or more is counted as a deposit or withdrawal (the profit baseline moves with it), a smaller
  one as fees.
- Once the whole position is sold, the bot switches to the opposite phase with all its cash.
- **Nearest-side entry** (`nearest_side_entry`, on in `config.json`): while the bot holds nothing
  in the current phase, it arms the ladder on the side the price is closer to. At YES ≥ 40.5% it
  cancels the YES buy ladder and places the NO buy ladder; at YES ≤ 39% it switches back. The gap
  keeps it from re-placing orders while the price hovers around 40%. Once anything is bought,
  the side is locked.
- **The stop-loss is currently disabled** (`stop_yes_below: 0`, `stop_yes_above: 1` in `config.json`).
  When enabled: if the best YES bid stays below 31% (while holding YES), or the best YES ask stays
  above 49% (while holding NO), **for 3 hours without interruption**, the bot sells the whole
  position and pauses. It starts a new ladder once the price comes back (YES ≥ 36%, or ≤ 44%;
  `resume_yes_at_or_above`, `resume_no_at_or_below`). If the sale fails or fills only partly, the
  bot retries it every round.
- **Cutoff** (`stop_trading_at_utc`, currently `"2026-10-20T00:00:00Z"`): the bot cancels its orders,
  sells the position and stops. The market resolves on 25 October 2026.
- The bot only cancels orders it placed itself.

## Running (paper mode)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pytest -q
.venv/bin/python -m bot --config config.json --workdir .
```

- `state.json`: state (phase, cash, position, open orders); the bot resumes from it after a restart.
- `ledger.csv`: every fill, stop-loss and close, with cash, average price and realized profit.
- Stopping: Ctrl+C or an empty `STOP` file in the working directory. The bot cancels its orders,
  leaves the position as is and deletes `STOP` so the next start does not exit right away.
- A remainder smaller than the exchange minimum (5 shares) cannot be sold; on a phase change it
  is recorded under `leftover` in `state.json`.

The simulation is optimistic: an order counts as fully filled as soon as the price reaches it,
while a real order would queue behind others at the same price.

## Telegram notifications

The bot can message you on Telegram for every order it places, every fill (buy or sale, with the
realized profit), phase switches, stop-loss and cutoff sales, start and stop, and repeated errors.
Orders placed in the same round arrive as one message. In paper mode every message starts with
`[PAPER]`.

1. In Telegram, open @BotFather, send `/newbot` and keep the token it gives you.
2. Send your new bot any message (e.g. `/start`); a bot can only write to people who wrote to it.
3. Open `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `"chat":{"id": ...}`.
4. Copy `.env.example` to `.env` and fill in `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.

The bot reads `.env` from its working directory (systemd also loads it on the Droplet). Without
those two values notifications are off and the bot runs as before. The token is a secret: `.env`
is in `.gitignore`, and the bot never logs it. Messages go out from a background thread, so a
slow or unreachable Telegram never delays trading.

## Backtest

```bash
.venv/bin/python -m bot.backtest --config config.json --since 2026-09-24
```

Replays historical trades through the same strategy the bot uses. Fees are not included
(this market has them enabled, `politics_fees`).

## Dashboard

A read-only web page with the bot's value, profit (realized and unrealized), cash, position,
phase, market price, running time, time to the cutoff, open orders, a value-over-time chart and
the full ledger. It reads the bot's `status.live.json`, `state.live.json` and `ledger.live.csv`,
refreshes every 10 seconds and cannot place or cancel orders.

```bash
.venv/bin/python -m dashboard --data .           # http://127.0.0.1:8080 (live files)
.venv/bin/python -m dashboard --data . --paper   # paper-trading files
```

In Docker it runs as the `dashboard` service next to the bot, bound to the server's loopback
only. Open it from your machine through an SSH tunnel, then browse to http://localhost:8080:

```bash
ssh -N -L 8080:127.0.0.1:8080 root@DROPLET_IP
```

### Dashboard on the web (HTTPS + password)

With Docker, the optional `caddy` service puts the dashboard on the web behind HTTPS (a free
Let's Encrypt certificate) and a username and password. On the server, in the repo directory:

1. Pick an address. Either a domain whose DNS A record points to the server's IP, or, without a
   domain, `<ip-with-dashes>.sslip.io` (e.g. `165-22-197-17.sslip.io`), which resolves to that IP.
2. Set the username and password (asked twice, not echoed; only a bcrypt hash is stored in
   `deploy/auth.caddy`, which is git-ignored):

   ```bash
   bash deploy/set-dashboard-password.sh
   ```
3. Add to `.env`:

   ```
   COMPOSE_PROFILES=web
   DASHBOARD_DOMAIN=165-22-197-17.sslip.io
   ```
4. Start it with `docker compose up -d`, then open `https://<DASHBOARD_DOMAIN>`.

Ports 80 and 443 must be reachable from the internet (Let's Encrypt checks port 80). Run the
password script again to change the password; it reloads Caddy.

## Docker

The image holds only the code; `config.json` is mounted read-only, secrets come from `.env` via
`env_file`, and state, ledger and `STOP` live in `./data`.

```bash
docker compose up -d --build        # start (with --live, see below)
docker compose logs -f              # follow the log
docker compose stop                 # stop; the bot cancels its own orders first
touch data/STOP                     # or stop it from inside
docker compose run --rm bot -m bot.check --config /app/config.json   # read-only check
```

`docker-compose.yml` runs the bot with `--live`, so it trades real money only when `config.json`
also says `"trading_mode": "live"`; otherwise it exits with 78 and Compose gives up after five
tries. For paper trading in Docker, remove `--live` from `command`. Compose restarts the bot after
a crash but not after a clean exit (cutoff, `STOP`).

To move a running bot into Docker, stop it first and copy its `state.live.json` and
`ledger.live.csv` into `./data`, so the container continues with the same position.

### On a DigitalOcean Droplet

As root on a fresh Ubuntu 24.04 Droplet (region where Polymarket is available, e.g. AMS3):

```bash
curl -fsSL https://raw.githubusercontent.com/Anarhislav/polymarket-studenti-bot/main/deploy/setup-droplet-docker.sh | bash
```

It installs Docker from Ubuntu's repositories, enables clock sync and an SSH-only firewall,
clones the repo to `/opt/polymarket-studenti-bot`, prepares `data/` and an empty `.env` (mode 600),
and starts the container. It also disables the systemd deployment if one exists, so only one bot
runs. Then copy your secrets and state, set `"trading_mode": "live"` in the server's
`config.json`, and restart:

```bash
scp .env root@DROPLET_IP:/opt/polymarket-studenti-bot/.env
scp state.live.json ledger.live.csv root@DROPLET_IP:/opt/polymarket-studenti-bot/data/
ssh root@DROPLET_IP "cd /opt/polymarket-studenti-bot && chmod 600 .env && chown 10001:10001 data/* && docker compose up -d --force-recreate"
```

Update to the latest `main` with `bash /opt/polymarket-studenti-bot/deploy/update-docker.sh`; it
keeps a local `config.json` change across the pull.

## Deploying to a DigitalOcean Droplet without Docker (systemd)

Create a Droplet: Ubuntu 24.04, the smallest Basic plan, SSH-key login, and a region where
Polymarket is available to you. Then, as root on the Droplet:

```bash
curl -fsSL https://raw.githubusercontent.com/Anarhislav/polymarket-studenti-bot/main/deploy/setup-droplet.sh | bash
```

`deploy/setup-droplet.sh` installs git and Python, enables clock sync and a firewall that allows
SSH only, creates a `bot` user, clones this repo to `/home/bot/polymarket-studenti-bot`, runs the
tests and starts the `polymarket-bot` systemd service. Running it again updates and restarts.

| Task | Command (as root) |
|---|---|
| Follow the log | `journalctl -u polymarket-bot -f` |
| Status | `systemctl status polymarket-bot` |
| Stop (cancels the bot's orders, keeps the position) | `systemctl stop polymarket-bot` |
| Deploy the latest `main` | `bash /home/bot/polymarket-studenti-bot/deploy/update.sh` |

- The service runs the bot with `--live`, so it trades real money once the Droplet's
  `config.json` says `"trading_mode": "live"` and `.env` holds the keys. With `"paper"`, missing
  keys, missing approvals or a balance below `budget_usd`, the bot refuses to start (exit 78) and
  the service stays stopped; `journalctl -u polymarket-bot` says why. For paper trading on the
  Droplet, remove `--live` from `/etc/systemd/system/polymarket-bot.service`.
- systemd restarts the bot after a crash or a reboot, and it resumes from `state.live.json`. A
  clean exit (cutoff reached, `STOP` file) is not restarted.
- Run only one instance: stop the bot on any other machine before starting it on the Droplet.
- For live trading, put the secrets in `/home/bot/polymarket-studenti-bot/.env`, owned by `bot`
  with mode `600`. The service loads it if present; it is in `.gitignore` and never committed.

## Live trading

Live trading places real orders through the official SDK (`polymarket-client`) on a Polymarket
Deposit Wallet (accounts created on polymarket.com after 4 May 2026, including MetaMask logins).

1. Fill in `.env` (see `.env.example`): `SIGNER_PRIVATE_KEY` (the key of the account that owns
   the wallet, ideally a MetaMask account used only for the bot) and `POLYMARKET_WALLET_ADDRESS`
   (the account wallet address from the profile menu).
2. Make sure the wallet has traded once on polymarket.com, so its trading approvals are set,
   then run the read-only check (it places no orders and never prints secrets):

   ```bash
   .venv/bin/python -m bot.check --config config.json --workdir .
   ```

3. Set `"trading_mode": "live"` in `config.json` and start with `--live`:

   ```bash
   .venv/bin/python -m bot --config config.json --workdir . --live
   ```

Both switches are required; with only one of them the bot refuses to start. Before trading it
checks that the trading approvals are in place and that the pUSD balance covers `budget_usd`.

- Live state and fills go to `state.live.json` and `ledger.live.csv`, separate from paper mode.
- Telegram messages start with `[LIVE]`.
- Order status is refreshed with one request for all open orders every `status_poll_seconds`.
- Sell orders are capped to the shares actually held, since fees can leave slightly fewer shares
  than were bought. A rejected order is logged, reported on Telegram once and retried after 30 s.
- Fills are booked at the order's limit price; an order that crossed the spread when placed may
  have filled at a better price, so the ledger is slightly conservative.
- The private key is only read from the environment. It is never logged or written to disk by the
  bot, and `.env` is in `.gitignore`.
