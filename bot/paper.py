"""Paper-trading exchange: simulates fills against the live public order book.

It reads only the public /book endpoint and never signs or sends an order. A resting buy fills
completely once the best ask reaches its price, a resting sell once the best bid does, and an
immediate sale fills at the best bid. This is optimistic: real orders queue behind others at the
same price and may fill partially.
"""

from __future__ import annotations

import itertools
import json
import logging
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .strategy import BUY, SELL, Market

log = logging.getLogger(__name__)

CLOB_HOST = "https://clob.polymarket.com"


@dataclass
class OrderStatus:
    matched: float  # shares filled so far
    open: bool  # still resting on the book


def fetch_top_of_book(token_id: str, timeout: float = 10.0) -> tuple:
    url = f"{CLOB_HOST}/book?" + urllib.parse.urlencode({"token_id": token_id})
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "paper-bot"}),
                                timeout=timeout) as resp:
        book = json.load(resp)
    bids = [float(b["price"]) for b in book.get("bids", [])]
    asks = [float(a["price"]) for a in book.get("asks", [])]
    return (max(bids) if bids else None, min(asks) if asks else None)


STREAM_STALE_SECONDS = 30.0


class PaperExchange:
    def __init__(self, yes_token: str, no_token: str, stream=None):
        self.tokens = {"YES": yes_token, "NO": no_token}
        self.orders: dict = {}
        self.ids = itertools.count(1)
        self.books: dict = {}
        self.stream = stream
        self._using_rest = None

    def market(self) -> Market:
        for outcome, token in self.tokens.items():
            self.books[outcome] = self._top_of_book(token)
        self._simulate()
        bid, ask = self.books["YES"]
        return Market(yes_bid=bid, yes_ask=ask)

    def _top_of_book(self, token: str) -> tuple:
        """Prefer the WebSocket view; fall back to REST while it is missing or stale."""
        if self.stream is not None:
            age = self.stream.age(time.time())
            top = self.stream.book.get(token)
            if top is not None and age is not None and age <= STREAM_STALE_SECONDS:
                self._note_source(rest=False)
                return top
        self._note_source(rest=True)
        return fetch_top_of_book(token)

    def _note_source(self, rest: bool) -> None:
        if self.stream is not None and rest != self._using_rest:
            log.warning("order book source: %s", "REST (websocket missing or stale)" if rest else "websocket")
        self._using_rest = rest

    def wait_for_update(self, timeout: float) -> None:
        """Block until the order book changes or timeout passes."""
        if self.stream is None:
            time.sleep(timeout)
        else:
            self.stream.wait_for_change(timeout)

    def _simulate(self) -> None:
        for order in self.orders.values():
            if not order["open"]:
                continue
            bid, ask = self.books[order["outcome"]]
            if order["side"] == BUY and ask is not None and ask <= order["price"]:
                order["matched"], order["open"] = order["size"], False
            elif order["side"] == SELL and bid is not None and bid >= order["price"]:
                order["matched"], order["open"] = order["size"], False

    def place_limit(self, outcome: str, side: str, price: float, size: float) -> str:
        order_id = f"paper-{next(self.ids)}"
        self.orders[order_id] = dict(outcome=outcome, side=side, price=price, size=size,
                                     matched=0.0, open=True)
        log.info("[paper] rest %s %s %.2f shares @ %.2f", side, outcome, size, price)
        return order_id

    def order_status(self, order_id: str) -> OrderStatus:
        o = self.orders.get(order_id)
        if o is None:
            return OrderStatus(matched=0.0, open=False)
        return OrderStatus(matched=o["matched"], open=o["open"])

    def cancel(self, order_id: str) -> None:
        if order_id in self.orders:
            self.orders[order_id]["open"] = False

    def sell_now(self, outcome: str, shares: float) -> tuple:
        bid, _ = self.books.get(outcome) or fetch_top_of_book(self.tokens[outcome])
        if bid is None:
            return 0.0, 0.0
        log.info("[paper] sell now %s %.2f shares @ %.2f", outcome, shares, bid)
        return shares, bid
