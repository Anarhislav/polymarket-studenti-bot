"""Main loop: keeps the resting orders in line with the strategy and records every fill.

The runner works with any exchange object that provides market(), place_limit(), order_status(),
cancel() and sell_now(). It only ever cancels orders it placed itself (tracked in the state file).
"""

from __future__ import annotations

import csv
import json
import logging
import os
import signal
import time
from dataclasses import asdict
from pathlib import Path

from .strategy import CLOSING, DONE, Config, OrderIntent, State, Strategy

log = logging.getLogger(__name__)

STATUS_EVERY_SECONDS = 60
MAX_BACKOFF_SECONDS = 30.0

LEDGER_FIELDS = ["time_utc", "event", "outcome", "side", "key", "shares", "price", "cash",
                 "position_shares", "avg_cost", "realized_pnl", "phase", "mode"]


class Runner:
    def __init__(self, exchange, config: Config, workdir: Path, poll_seconds: float = 15.0):
        self.ex = exchange
        self.cfg = config
        self.dir = workdir
        self.state_path = workdir / "state.json"
        self.ledger_path = workdir / "ledger.csv"
        self.kill_path = workdir / "STOP"
        self.poll = poll_seconds
        self.orders: dict = {}
        self.strategy = self._load()
        self._running = True
        self._last_snapshot = None
        self._last_status = 0.0

    # --- persistence ------------------------------------------------------------------

    def _load(self) -> Strategy:
        if self.state_path.exists():
            data = json.loads(self.state_path.read_text())
            self.orders = data.get("orders", {})
            log.info("resumed state from %s", self.state_path)
            return Strategy(self.cfg, State.from_dict(data["strategy"]))
        return Strategy(self.cfg)

    def _save(self) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"strategy": self.strategy.st.to_dict(), "orders": self.orders}, indent=2))
        os.replace(tmp, self.state_path)

    def _ledger(self, event: str, outcome: str, side: str, key: str, shares: float, price: float) -> None:
        st = self.strategy.st
        new = not self.ledger_path.exists()
        with self.ledger_path.open("a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=LEDGER_FIELDS)
            if new:
                w.writeheader()
            w.writerow(dict(
                time_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                event=event, outcome=outcome, side=side, key=key,
                shares=f"{shares:.2f}", price=f"{price:.4f}", cash=f"{st.cash:.2f}",
                position_shares=f"{st.shares:.2f}",
                avg_cost=f"{(st.cost / st.shares) if st.shares else 0:.4f}",
                realized_pnl=f"{st.realized_pnl:.2f}", phase=st.side, mode=st.mode,
            ))

    # --- order reconciliation -----------------------------------------------------------

    def _sync_fills(self) -> None:
        for key in list(self.orders):
            rec = self.orders[key]
            status = self.ex.order_status(rec["id"])
            delta = status.matched - rec["matched"]
            if delta > 1e-9:
                side, kind, _ = key.split(":")
                if side == self.strategy.st.side:
                    self.strategy.on_fill(key, delta, rec["price"])
                else:
                    self.strategy.on_external_fill(side, kind, delta, rec["price"])
                rec["matched"] = status.matched
                self._ledger("fill", side, kind.upper(), key, delta, rec["price"])
            if not status.open:
                del self.orders[key]

    def _cancel_all_tracked(self) -> None:
        for rec in list(self.orders.values()):
            try:
                self.ex.cancel(rec["id"])
            except Exception:  # keep going: the remaining orders still need cancelling
                log.exception("cancel failed for %s", rec["id"])
        self._sync_fills()

    def _apply_intents(self, intents: list) -> None:
        wanted = {i.key: i for i in intents}
        for key in list(self.orders):
            rec = self.orders[key]
            intent = wanted.get(key)
            remaining = rec["size"] - rec["matched"]
            if intent is None or abs(intent.price - rec["price"]) > 1e-9 or abs(intent.size - remaining) >= 1.0:
                self.ex.cancel(rec["id"])
        self._sync_fills()
        for key, intent in wanted.items():
            if key in self.orders:
                continue
            order_id = self.ex.place_limit(intent.outcome, intent.side, intent.price, intent.size)
            self.orders[key] = dict(id=order_id, price=intent.price, size=intent.size, matched=0.0)

    def _liquidate(self, reason: str) -> None:
        self._cancel_all_tracked()
        st = self.strategy.st
        if st.shares >= self.cfg.min_order_shares:
            # If this raises or sells less than the position, check_risk asks again next tick.
            sold, price = self.ex.sell_now(st.side, st.shares)
            if sold <= 0:
                log.warning("%s: nothing sold (no bid?), retrying next tick", reason)
                return
            self.strategy.on_liquidation(sold, price)
            self._ledger(reason, st.side, "SELL", reason, sold, price)
        elif st.mode == CLOSING:
            st.mode = DONE

    # --- loop -------------------------------------------------------------------------

    def tick(self, now: float) -> None:
        market = self.ex.market()
        self._sync_fills()
        action = self.strategy.check_risk(market, now)
        if action == "stop":
            log.warning("stop-loss: selling the position (price stayed beyond the limit for %ss)",
                        self.cfg.stop_wait_seconds)
            self._liquidate("stop_loss")
        elif action == "close":
            log.warning("cutoff reached, closing the position")
            self._liquidate("cutoff")
        self._apply_intents(self.strategy.desired_orders())
        st = self.strategy.st
        # At sub-second polling, writing the state and logging every round would mostly repeat
        # itself; do both when something changed, plus a status line once a minute.
        snapshot = (market.yes_bid, market.yes_ask, st.side, st.mode, round(st.cash, 4),
                    round(st.shares, 4), tuple(sorted(self.orders)))
        changed = snapshot != self._last_snapshot
        if changed:
            self._save()
        if changed or now - self._last_status >= STATUS_EVERY_SECONDS:
            log.info("YES bid/ask %s/%s | %s %s | cash $%.2f | %.2f shares | value $%.2f | realized $%.2f",
                     market.yes_bid, market.yes_ask, st.side, st.mode, st.cash, st.shares,
                     self.strategy.mark_to_market(market), st.realized_pnl)
            self._last_status = now
        self._last_snapshot = snapshot

    def run(self) -> None:
        signal.signal(signal.SIGINT, self._stop)
        signal.signal(signal.SIGTERM, self._stop)
        failures = 0
        while self._running and self.strategy.st.mode != DONE:
            if self.kill_path.exists():
                log.warning("STOP file found, cancelling orders and exiting")
                self.kill_path.unlink(missing_ok=True)  # otherwise the next start exits at once
                break
            try:
                self.tick(time.time())
                failures = 0
                delay = self.poll
            except Exception:  # a transient API error must not kill the loop
                failures += 1
                # Back off so errors such as rate limiting are not hammered several times a second.
                delay = min(self.poll * 2 ** failures, MAX_BACKOFF_SECONDS)
                log.exception("tick failed (%d in a row), retrying in %.1fs", failures, delay)
            wait = getattr(self.ex, "wait_for_update", None)
            if failures == 0 and wait is not None:
                wait(delay)
            else:
                time.sleep(delay)
        self._cancel_all_tracked()
        self._save()
        log.info("stopped; open bot orders cancelled, position left as is: %s", asdict(self.strategy.st))

    def _stop(self, *_):
        self._running = False
