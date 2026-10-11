"""Replay historical trades through the same Strategy the bot uses.

python -m bot.backtest --config config.json --since 2026-09-24

Each historical trade is treated as the market price. A resting order fills only when a trade
prints strictly beyond its level, and at most the size of that trade. Fees are not included.
"""

from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .__main__ import load_config
from .strategy import NO, YES, BUY, Market, Strategy

DATA_API = "https://data-api.polymarket.com/trades"


def fetch_trades(condition_id: str) -> list:
    url = DATA_API + "?" + urllib.parse.urlencode(
        {"market": condition_id, "limit": 10000, "takerOnly": "false"})
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "backtest"}),
                                timeout=30) as resp:
        return json.load(resp)


def normalize(raw: list, since: float) -> list:
    """One (timestamp, YES price, shares) per match, oldest first."""
    seen, out = set(), []
    for x in sorted(raw, key=lambda r: r["timestamp"]):
        if x["timestamp"] < since:
            continue
        key = (x["transactionHash"], round(float(x["size"]), 4))
        if key in seen:
            continue
        seen.add(key)
        price = float(x["price"])
        out.append((x["timestamp"], price if x["outcome"] == "Yes" else 1 - price, float(x["size"])))
    return out


def crosses(intent, yes_price: float) -> bool:
    level = intent.price if intent.outcome == YES else 1 - intent.price
    buys_yes_low = (intent.outcome == YES) == (intent.side == BUY)
    return yes_price < level - 1e-9 if buys_yes_low else yes_price > level + 1e-9


def run(strategy: Strategy, trades: list) -> dict:
    events, low = [], strategy.st.cash
    for ts, p, size in trades:
        market = Market(yes_bid=p, yes_ask=p)
        action = strategy.check_risk(market, ts)
        if action:
            st = strategy.st
            px = p if st.side == YES else 1 - p
            events.append((ts, f"{action}: sell {st.shares:.0f} {st.side} @ {px:.2f}"))
            strategy.on_liquidation(st.shares, px)
        volume = size
        for intent in strategy.desired_orders():
            if volume <= 0 or not crosses(intent, p):
                continue
            phase = (strategy.st.side, strategy.st.cycles)
            qty = min(intent.size, volume)
            strategy.on_fill(intent.key, qty, intent.price)
            volume -= qty
            if (strategy.st.side, strategy.st.cycles) != phase:
                events.append((ts, f"{phase[0]} position closed, cash ${strategy.st.cash:.0f}"))
                break
        low = min(low, strategy.mark_to_market(market))
    last = trades[-1][1]
    return dict(value=strategy.mark_to_market(Market(last, last)), low=low, events=events,
                last_price=last, last_ts=trades[-1][0])


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="config.json")
    p.add_argument("--since", default="2026-09-24", help="UTC date, YYYY-MM-DD")
    p.add_argument("--trades", help="JSON file with data-api trades instead of downloading them")
    args = p.parse_args()
    raw_cfg, cfg = load_config(Path(args.config))
    condition_id = raw_cfg["market"].get("condition_id",
                                         "0x258ff19b4baff0ed24bc28f58e83c8be6f2f91d9134700703e0aa51810c51a63")
    raw = json.loads(Path(args.trades).read_text()) if args.trades else fetch_trades(condition_id)
    since = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc).timestamp()
    trades = normalize(raw, since)
    strategy = Strategy(cfg)
    result = run(strategy, trades)
    fmt = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%d.%m %H:%M")
    for ts, text in result["events"]:
        print(f"{fmt(ts)}  {text}")
    st = strategy.st
    budget = cfg.budget_usd
    print(f"\nend value ${result['value']:.0f} from ${budget:.0f} ({(result['value'] - budget) / budget:+.1%}), "
          f"lowest ${result['low']:.0f}")
    print(f"holding {st.side} {st.shares:.0f} shares, cash ${st.cash:.0f}, "
          f"YES {result['last_price']:.2f} at {fmt(result['last_ts'])} UTC")


if __name__ == "__main__":
    main()
