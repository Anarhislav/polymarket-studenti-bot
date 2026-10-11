"""Entry point: python -m bot --config config.json

Runs in paper mode: fills are simulated against the live public order book and no order is
ever signed or sent.
"""

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .paper import PaperExchange
from .runner import Runner
from .stream import MarketStream
from .strategy import Config


def load_config(path: Path) -> tuple:
    raw = json.loads(path.read_text())
    s = raw["strategy"]
    cutoff = s.get("stop_trading_at_utc")
    cfg = Config(
        budget_usd=float(s["budget_usd"]),
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
        resume_yes_at_or_above=float(s.get("resume_yes_at_or_above", 0.36)),
        resume_no_at_or_below=float(s.get("resume_no_at_or_below", 0.44)),
        stop_trading_at=(
            datetime.fromisoformat(cutoff.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
            if cutoff else 0.0
        ),
    )
    return raw, cfg


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="config.json")
    p.add_argument("--workdir", default=".")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    raw, cfg = load_config(Path(args.config))
    m = raw["market"]
    stream = None
    if raw.get("market_data", "websocket") == "websocket":
        stream = MarketStream([m["yes_token_id"], m["no_token_id"]])
        stream.start()
    exchange = PaperExchange(m["yes_token_id"], m["no_token_id"], stream=stream)
    try:
        Runner(exchange, cfg, Path(args.workdir), poll_seconds=float(raw.get("poll_seconds", 5))).run()
    finally:
        if stream is not None:
            stream.stop()


if __name__ == "__main__":
    main()
