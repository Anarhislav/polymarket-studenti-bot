"""Entry point: python -m bot --config config.json

Runs in paper mode: fills are simulated against the live public order book and no order is
ever signed or sent.
"""

import argparse
import json
import logging
import math
import os
from datetime import datetime, timezone
from pathlib import Path

from .notify import from_env
from .paper import PaperExchange
from .runner import Runner
from .stream import MarketStream
from .strategy import Config


# budget_usd "all": live trading sizes the budget from the wallet's whole cash balance at start.
BUDGET_ALL = "all"
# Stand-in budget for "all" where there is no wallet to read (paper mode, backtest, check).
PLACEHOLDER_BUDGET = 100.0


def load_config(path: Path, budget_override: float = None) -> tuple:
    raw = json.loads(path.read_text())
    s = raw["strategy"]
    cutoff = s.get("stop_trading_at_utc")
    budget = s["budget_usd"]
    if budget == BUDGET_ALL:
        budget = budget_override if budget_override is not None else PLACEHOLDER_BUDGET
    cfg = Config(
        budget_usd=float(budget),
        allocation=tuple(s.get("allocation", (0.10, 0.15, 0.25, 0.50))),
        yes_buy_levels=tuple(s.get("yes_buy_levels", (0.38, 0.37, 0.36, 0.35))),
        yes_sell_levels=tuple(s.get("yes_sell_levels", (0.42, 0.43, 0.44, 0.45))),
        no_buy_levels=tuple(s.get("no_buy_levels", (0.42, 0.43, 0.44, 0.45))),
        no_sell_levels=tuple(s.get("no_sell_levels", (0.38, 0.37, 0.36, 0.35))),
        stop_yes_below=float(s.get("stop_yes_below", 0.31)),
        stop_yes_above=float(s.get("stop_yes_above", 0.49)),
        stop_wait_seconds=int(s.get("stop_wait_hours", 3) * 3600),
        hard_stop_yes_below=float(s.get("hard_stop_yes_below") or 0.0),
        hard_stop_yes_above=float(s.get("hard_stop_yes_above") or 1.0),
        nearest_side_entry=bool(s.get("nearest_side_entry", False)),
        entry_no_at_or_above=float(s.get("entry_no_at_or_above", 0.41)),
        entry_yes_at_or_below=float(s.get("entry_yes_at_or_below", 0.39)),
        resume_yes_at_or_above=float(s.get("resume_yes_at_or_above", 0.36)),
        resume_no_at_or_below=float(s.get("resume_no_at_or_below", 0.44)),
        stop_trading_at=(
            datetime.fromisoformat(cutoff.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
            if cutoff else 0.0
        ),
    )
    return raw, cfg


def load_dotenv(path: Path) -> None:
    """Load KEY=VALUE lines from .env without overriding variables already set (e.g. by systemd)."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


# sysexits EX_CONFIG: the service file does not restart on it, so a setup problem stops the bot
# instead of restarting it every few seconds.
EXIT_CONFIG = 78


def config_error(message: str) -> None:
    logging.error(message)
    raise SystemExit(EXIT_CONFIG)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="config.json")
    p.add_argument("--workdir", default=".")
    p.add_argument("--live", action="store_true",
                   help='trade real money; also requires "trading_mode": "live" in the config')
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # The SDK's HTTP client logs every request at INFO; with order status polled every few seconds
    # that drowns the bot's own lines. Its warnings and errors still show.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    workdir = Path(args.workdir)
    load_dotenv(workdir / ".env")
    raw, cfg = load_config(Path(args.config))
    m = raw["market"]

    mode = raw.get("trading_mode", "paper")
    if args.live != (mode == "live"):
        # Both switches must agree, so neither an edited config nor a stray flag alone spends money.
        config_error(f'live trading needs both --live and "trading_mode": "live" (config has "{mode}", '
                     f'--live {"given" if args.live else "not given"})')
    live = args.live
    budget_all = raw["strategy"].get("budget_usd") == BUDGET_ALL

    stream = None
    if raw.get("market_data", "websocket") == "websocket":
        stream = MarketStream([m["yes_token_id"], m["no_token_id"]])
        stream.start()
    exchange = None
    try:
        if live:
            from .live import LiveExchange, LiveSetupError, client_from_env

            try:
                exchange = LiveExchange(client_from_env(), m["yes_token_id"], m["no_token_id"],
                                        m["condition_id"], stream=stream,
                                        status_poll_seconds=float(raw.get("status_poll_seconds", 2)),
                                        min_order_shares=cfg.min_order_shares,
                                        taker_fee_rate=float(m.get("taker_fee_rate", 0.04)))
                if budget_all:
                    cash = math.floor(exchange.collateral_balance() * 100) / 100
                    try:
                        raw, cfg = load_config(Path(args.config), budget_override=cash)
                        cfg.validate()
                    except ValueError as err:
                        config_error(f'budget_usd "all": wallet cash ${cash:.2f} is not enough: {err}')
                balance = exchange.check_ready(cfg.budget_usd)
            except LiveSetupError as err:
                config_error(f"not starting live trading: {err}")
            logging.warning("LIVE TRADING with real money: budget $%.2f, wallet balance $%.2f",
                            cfg.budget_usd, balance)
        else:
            if budget_all:
                logging.warning('budget_usd "all" has no wallet to read in paper mode; using $%.0f',
                                PLACEHOLDER_BUDGET)
            exchange = PaperExchange(m["yes_token_id"], m["no_token_id"], stream=stream)
        runner = Runner(exchange, cfg, workdir, poll_seconds=float(raw.get("poll_seconds", 5)),
                        notifier=from_env(prefix="[LIVE] " if live else "[PAPER] "),
                        state_suffix=".live" if live else "")
        if live and budget_all:
            runner.adopt_balance(cfg.budget_usd)
        runner.run()
    finally:
        if stream is not None:
            stream.stop()
        close = getattr(exchange, "close", None)
        if close is not None:
            close()

if __name__ == "__main__":
    main()
