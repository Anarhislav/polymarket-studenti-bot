"""Order-book access shared by the paper and live exchanges.

The top of book comes from the market WebSocket when one is attached and fresh, and from the
public REST /book endpoint otherwise.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .strategy import Market

log = logging.getLogger(__name__)

CLOB_HOST = "https://clob.polymarket.com"
STREAM_STALE_SECONDS = 30.0


@dataclass
class OrderStatus:
    matched: float  # shares filled so far
    open: bool  # still resting on the book


def fetch_top_of_book(token_id: str, timeout: float = 10.0) -> tuple:
    url = f"{CLOB_HOST}/book?" + urllib.parse.urlencode({"token_id": token_id})
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "studenti-bot"}),
                                timeout=timeout) as resp:
        book = json.load(resp)
    bids = [float(b["price"]) for b in book.get("bids", [])]
    asks = [float(a["price"]) for a in book.get("asks", [])]
    return (max(bids) if bids else None, min(asks) if asks else None)


class BookSource:
    def __init__(self, yes_token: str, no_token: str, stream=None):
        self.tokens = {"YES": yes_token, "NO": no_token}
        self.stream = stream
        self.books: dict = {}
        self._using_rest = None

    def read_books(self) -> Market:
        for outcome, token in self.tokens.items():
            self.books[outcome] = self._top_of_book(token)
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
            if rest:
                log.warning("order book source: REST (websocket missing or stale)")
            else:
                log.info("order book source: websocket")
        self._using_rest = rest

    def wait_for_update(self, timeout: float) -> None:
        """Block until the order book changes or timeout passes."""
        if self.stream is None:
            time.sleep(timeout)
        else:
            self.stream.wait_for_change(timeout)
