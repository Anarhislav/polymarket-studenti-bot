"""Top of book from Polymarket's public market WebSocket.

A background thread keeps the best bid and ask of each subscribed token current and signals the
main loop whenever they change. The connection is re-opened with a growing pause if it drops.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Optional

import websocket

log = logging.getLogger(__name__)

MARKET_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
PING_EVERY_SECONDS = 10
MAX_RECONNECT_PAUSE_SECONDS = 60.0


def _best(levels: list, highest: bool) -> Optional[float]:
    prices = [float(level["price"]) for level in levels or [] if float(level.get("size", 0)) > 0]
    if not prices:
        return None
    return max(prices) if highest else min(prices)


def _price(value) -> Optional[float]:
    if value in (None, ""):
        return None
    p = float(value)
    return p if 0 < p < 1 else None


class TopOfBook:
    """Best bid and ask per asset, updated from market-channel events."""

    def __init__(self, asset_ids: list):
        self.assets = set(asset_ids)
        self.top: dict = {}
        self.updated_at: Optional[float] = None
        self.lock = threading.Lock()
        self.changed = threading.Event()

    def apply(self, message, now: float) -> bool:
        """Apply one decoded WebSocket message (an event or a list of events)."""
        events = message if isinstance(message, list) else [message]
        changed = False
        for event in events:
            if not isinstance(event, dict):
                continue
            kind = event.get("event_type")
            if kind == "book":
                changed |= self._set(event.get("asset_id"), _best(event.get("bids"), True),
                                     _best(event.get("asks"), False))
            elif kind == "best_bid_ask":
                changed |= self._set(event.get("asset_id"), _price(event.get("best_bid")),
                                     _price(event.get("best_ask")))
            elif kind == "price_change":
                for change in event.get("price_changes", []):
                    if "best_bid" in change or "best_ask" in change:
                        changed |= self._set(change.get("asset_id"), _price(change.get("best_bid")),
                                             _price(change.get("best_ask")))
        if events:
            self.updated_at = now
        if changed:
            self.changed.set()
        return changed

    def _set(self, asset_id, bid, ask) -> bool:
        if asset_id not in self.assets:
            return False
        with self.lock:
            if self.top.get(asset_id) == (bid, ask):
                return False
            self.top[asset_id] = (bid, ask)
        return True

    def get(self, asset_id: str) -> Optional[tuple]:
        with self.lock:
            return self.top.get(asset_id)


class MarketStream:
    def __init__(self, asset_ids: list):
        self.asset_ids = list(asset_ids)
        self.book = TopOfBook(self.asset_ids)
        self._stop = threading.Event()
        self._ws: Optional[websocket.WebSocketApp] = None
        self._thread = threading.Thread(target=self._run, name="market-ws", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._ws is not None:
            self._ws.close()

    def age(self, now: float) -> Optional[float]:
        """Seconds since the last message, or None before the first one."""
        return None if self.book.updated_at is None else now - self.book.updated_at

    def wait_for_change(self, timeout: float) -> None:
        self.book.changed.wait(timeout)
        self.book.changed.clear()

    def _run(self) -> None:
        pause = 1.0
        while not self._stop.is_set():
            opened_at = time.time()
            self._ws = websocket.WebSocketApp(
                MARKET_WS_URL,
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=lambda _ws, err: log.warning("market websocket error: %s", err),
            )
            self._ws.run_forever()
            if self._stop.is_set():
                break
            # A connection that stayed up for a while resets the backoff.
            pause = 1.0 if time.time() - opened_at > 60 else min(pause * 2, MAX_RECONNECT_PAUSE_SECONDS)
            log.warning("market websocket closed, reconnecting in %.0fs", pause)
            self._stop.wait(pause)

    def _on_open(self, ws) -> None:
        ws.send(json.dumps({"assets_ids": self.asset_ids, "type": "market", "custom_feature_enabled": True}))
        log.info("market websocket connected, subscribed to %d assets", len(self.asset_ids))
        threading.Thread(target=self._ping, args=(ws,), name="market-ws-ping", daemon=True).start()

    def _ping(self, ws) -> None:
        while not self._stop.is_set() and ws.sock and ws.sock.connected:
            try:
                ws.send("PING")
            except Exception:
                return
            self._stop.wait(PING_EVERY_SECONDS)

    def _on_message(self, _ws, raw: str) -> None:
        if raw == "PONG":
            return
        try:
            message = json.loads(raw)
        except ValueError:
            log.debug("ignoring non-JSON message: %.80s", raw)
            return
        self.book.apply(message, time.time())
