"""Paper-trading exchange: simulates fills against the live public order book.

It reads only public market data and never signs or sends an order. A resting buy fills
completely once the best ask reaches its price, a resting sell once the best bid does, and an
immediate sale fills at the best bid. This is optimistic: real orders queue behind others at the
same price and may fill partially.
"""

from __future__ import annotations

import itertools
import logging

from .book import STREAM_STALE_SECONDS, BookSource, OrderStatus, fetch_top_of_book
from .strategy import BUY, SELL, Market

log = logging.getLogger(__name__)

__all__ = ["PaperExchange", "OrderStatus", "fetch_top_of_book", "STREAM_STALE_SECONDS"]


class PaperExchange(BookSource):
    def __init__(self, yes_token: str, no_token: str, stream=None):
        super().__init__(yes_token, no_token, stream)
        self.orders: dict = {}
        self.ids = itertools.count(1)

    def market(self) -> Market:
        market = self.read_books()
        self._simulate()
        return market

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
