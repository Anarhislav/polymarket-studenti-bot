"""Live exchange: real orders on Polymarket through the official SDK (polymarket-client).

The account is a Deposit Wallet (polymarket.com accounts created after 4 May 2026, including
MetaMask logins). The signer key and wallet address come from the environment; they are never
logged or written to disk by the bot.

Order status is refreshed with one request for all open orders every few seconds, instead of one
request per order on every loop round, so the market WebSocket can drive the loop without
hammering the API.
"""

from __future__ import annotations

import logging
import math
import os
import time
from decimal import Decimal

from .book import BookSource, OrderStatus
from .strategy import BUY, Market

log = logging.getLogger(__name__)

TOKEN_DECIMALS = 6
OPEN_STATUSES = {"LIVE", "DELAYED", "UNMATCHED"}


class LiveSetupError(RuntimeError):
    """The account is not ready for live trading; the message says what to fix."""


class OrderRejected(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"order rejected ({code}): {message}")
        self.code = code


def _floor2(x: float) -> float:
    return math.floor(x * 100 + 1e-9) / 100


def _units(raw: int) -> float:
    return raw / 10 ** TOKEN_DECIMALS


def client_from_env():
    """Create an authenticated SecureClient from SIGNER_PRIVATE_KEY and POLYMARKET_WALLET_ADDRESS."""
    from polymarket import RelayerApiKey, SecureClient

    key = os.environ.get("SIGNER_PRIVATE_KEY", "").strip()
    wallet = os.environ.get("POLYMARKET_WALLET_ADDRESS", "").strip()
    if not key or not wallet:
        raise LiveSetupError("SIGNER_PRIVATE_KEY and POLYMARKET_WALLET_ADDRESS must be set in .env")
    relayer_key = os.environ.get("POLYMARKET_RELAYER_API_KEY", "").strip()
    relayer_address = os.environ.get("POLYMARKET_RELAYER_API_KEY_ADDRESS", "").strip()
    api_key = RelayerApiKey(key=relayer_key, address=relayer_address) if relayer_key and relayer_address else None
    return SecureClient.create(private_key=key, wallet=wallet, api_key=api_key)


class LiveExchange(BookSource):
    def __init__(self, client, yes_token: str, no_token: str, condition_id: str, stream=None,
                 status_poll_seconds: float = 2.0, min_order_shares: float = 5.0):
        super().__init__(yes_token, no_token, stream)
        self.client = client
        self.condition_id = condition_id
        self.status_poll = status_poll_seconds
        self.min_shares = min_order_shares
        self._tracked: set = set()
        self._status: dict = {}
        self._refreshed_at = 0.0
        self._force_refresh = False

    # --- startup checks -----------------------------------------------------------------

    def check_ready(self, budget_usd: float) -> float:
        """Fail early, with a clear message, unless the account can trade the budget."""
        approvals = self.client.get_trading_approvals_state()
        if not approvals.is_fully_approved:
            raise LiveSetupError(
                f"trading approvals missing for this wallet ({approvals.missing}); place one small "
                f"order on polymarket.com with this account to set them up, then start again"
            )
        balance = self.collateral_balance()
        if balance + 1e-9 < budget_usd:
            raise LiveSetupError(
                f"collateral balance ${balance:.2f} is below budget_usd ${budget_usd:.2f}; "
                f"lower budget_usd in config.json or deposit more"
            )
        return balance

    # --- market data --------------------------------------------------------------------

    def market(self) -> Market:
        return self.read_books()

    # --- orders -------------------------------------------------------------------------

    def place_limit(self, outcome: str, side: str, price: float, size: float) -> str:
        if side != BUY:
            # Fees can leave slightly fewer shares than the strategy counted; never sell more than held.
            held = _floor2(self.token_balance(outcome))
            if held < size:
                log.info("capping %s sell from %.2f to the %.2f shares held", outcome, size, held)
                size = held
            if size < self.min_shares:
                raise OrderRejected("too_small", f"only {held:.2f} {outcome} shares held")
        size = _floor2(size)
        resp = self.client.place_limit_order(
            token_id=self.tokens[outcome], price=f"{price:.2f}", size=f"{size:.2f}",
            side="BUY" if side == BUY else "SELL",
        )
        if not resp.ok:
            raise OrderRejected(resp.code, resp.message)
        order_id = str(resp.order_id)
        self._tracked.add(order_id)
        self._force_refresh = True
        log.info("placed %s %s %.2f @ %.2f -> %s (%s)", side, outcome, size, price, order_id, resp.status)
        return order_id

    def order_status(self, order_id: str) -> OrderStatus:
        self._refresh()
        return self._status.get(order_id, OrderStatus(matched=0.0, open=True))

    def _refresh(self) -> None:
        now = time.time()
        if not self._force_refresh and now - self._refreshed_at < self.status_poll:
            return
        self._force_refresh = False
        self._refreshed_at = now
        open_now = {}
        for order in self.client.list_open_orders(market=self.condition_id):
            open_now[str(order.id)] = order
        for order_id in list(self._tracked):
            order = open_now.get(order_id)
            if order is None:
                # No longer open: read its final state once (filled, partly filled or cancelled).
                order = self.client.get_order(order_id=order_id)
            matched = float(order.size_matched)
            is_open = str(order.status).upper() in OPEN_STATUSES
            self._status[order_id] = OrderStatus(matched=matched, open=is_open)
            if not is_open:
                self._tracked.discard(order_id)

    def cancel(self, order_id: str) -> None:
        self.client.cancel_order(order_id=order_id)
        self._force_refresh = True

    def sell_now(self, outcome: str, shares: float) -> tuple:
        """Sell up to `shares` at the best available bids; returns (shares sold, average price)."""
        shares = min(shares, _floor2(self.token_balance(outcome)))
        if shares < self.min_shares:
            return 0.0, 0.0
        resp = self.client.place_market_order(
            token_id=self.tokens[outcome], side="SELL", shares=f"{shares:.2f}", order_type="FAK",
        )
        if not resp.ok:
            if resp.code == "fak_not_filled":
                return 0.0, 0.0
            raise OrderRejected(resp.code, resp.message)
        sold, proceeds = Decimal(resp.making_amount), Decimal(resp.taking_amount)
        if sold <= 0:
            order = self.client.get_order(order_id=str(resp.order_id))
            sold, proceeds = Decimal(order.size_matched), Decimal(order.size_matched) * Decimal(order.price)
        if sold <= 0:
            return 0.0, 0.0
        price = proceeds / sold
        if not 0 < price < 1:
            # The amounts came back in the other order (USDC first); the price must be a probability.
            sold, proceeds = proceeds, sold
            price = proceeds / sold
        log.info("sold now %.2f %s @ %.4f", sold, outcome, price)
        return float(sold), float(price)

    # --- balances -----------------------------------------------------------------------

    def collateral_balance(self) -> float:
        return _units(self.client.get_balance_allowance(asset_type="COLLATERAL").balance)

    def token_balance(self, outcome: str) -> float:
        return _units(self.client.get_balance_allowance(
            asset_type="CONDITIONAL", token_id=self.tokens[outcome]).balance)

    def close(self) -> None:
        close = getattr(self.client, "close", None)
        if close is not None:
            close()
