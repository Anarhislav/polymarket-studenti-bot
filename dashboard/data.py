"""Read the bot's files (read-only) and build the dashboard summary.

Sources, all written by the bot into its workdir:
- status{suffix}.json: latest market view, valuation, open orders and a heartbeat;
- state{suffix}.json: the strategy state (fallback when no status file exists yet);
- ledger{suffix}.csv: every fill, stop-loss and close.
"""

from __future__ import annotations

import calendar
import csv
import json
import time
from pathlib import Path
from typing import Optional

STALE_AFTER_SECONDS = 180  # the bot writes its status at least once a minute


def _read_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return None


def read_ledger(path: Path) -> list:
    try:
        with path.open(newline="") as f:
            rows = list(csv.DictReader(f))
    except FileNotFoundError:
        return []
    out = []
    for r in rows:
        try:
            out.append(dict(
                time=r["time_utc"], event=r["event"], outcome=r["outcome"], side=r["side"],
                shares=float(r["shares"]), price=float(r["price"]), cash=float(r["cash"]),
                position_shares=float(r["position_shares"]), avg_cost=float(r["avg_cost"]),
                realized_pnl=float(r["realized_pnl"]), phase=r["phase"], mode=r["mode"],
            ))
        except (KeyError, ValueError):
            continue  # a half-written last line is skipped, not fatal
    return out


def _ts(iso: str) -> float:
    return float(calendar.timegm(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")))


def equity_series(budget: float, started_at: Optional[float], ledger: list,
                  now_value: Optional[float], now: float) -> list:
    """Portfolio value over time: the budget at start, then cash plus the position valued at the
    price of each fill, then the current mark-to-market value."""
    points = []
    if started_at:
        points.append(dict(t=started_at, value=round(budget, 4)))
    for row in ledger:
        value = row["cash"] + row["position_shares"] * row["price"] if row["phase"] == row["outcome"] \
            else row["cash"]
        points.append(dict(t=_ts(row["time"]), value=round(value, 4)))
    if now_value is not None:
        points.append(dict(t=now, value=round(now_value, 4)))
    return points


def summary(data_dir: Path, suffix: str = ".live", now: Optional[float] = None) -> dict:
    now = time.time() if now is None else now
    status = _read_json(data_dir / f"status{suffix}.json")
    state = _read_json(data_dir / f"state{suffix}.json")
    ledger = read_ledger(data_dir / f"ledger{suffix}.csv")

    st = (status or {}).get("strategy") or (state or {}).get("strategy")
    if st is None:
        return dict(available=False, mode=suffix.strip(".") or "paper",
                    message=f"no bot data in {data_dir} yet (state{suffix}.json missing)")

    budget = (status or {}).get("budget_usd")
    if budget is None:
        budget = st["cash"] + st["cost"] - st["realized_pnl"]  # no status yet: reconstruct the start
    market = (status or {}).get("market") or {}
    value = (status or {}).get("value")
    if value is None:
        value = st["cash"] + st["cost"]  # no market view yet: position at cost
    shares, cost = st["shares"], st["cost"]
    position_value = value - st["cash"]
    unrealized = position_value - cost if shares > 0 else 0.0
    updated_at = (status or {}).get("updated_at")
    running = bool(status and status.get("running") and updated_at and now - updated_at <= STALE_AFTER_SECONDS)

    fills = [r for r in ledger if r["event"] == "fill"]
    return dict(
        available=True,
        mode=(status or {}).get("mode", suffix.strip(".") or "paper"),
        running=running,
        updated_at=updated_at,
        started_at=st.get("started_at"),
        stop_trading_at=(status or {}).get("stop_trading_at"),
        now=now,
        budget=budget,
        value=value,
        profit=value - budget,
        profit_pct=(value - budget) / budget * 100 if budget else None,
        realized=st["realized_pnl"],
        unrealized=unrealized,
        cash=st["cash"],
        phase=st["side"],
        state_mode=st["mode"],
        cycles=st["cycles"],
        position=dict(outcome=st["side"], shares=shares, avg_cost=(cost / shares) if shares else None,
                      cost=cost, value=position_value),
        leftover=st.get("leftover") or {},
        market=market,
        orders=(status or {}).get("orders") or [],
        trades=dict(count=len(fills), buys=sum(1 for r in fills if r["side"] == "BUY"),
                    sells=sum(1 for r in fills if r["side"] == "SELL"),
                    volume=round(sum(r["shares"] * r["price"] for r in fills), 2)),
        ledger=list(reversed(ledger))[:200],
        equity=equity_series(budget, st.get("started_at"), ledger, value, now),
    )
