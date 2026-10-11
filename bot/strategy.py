"""Ladder range strategy for one binary Polymarket market.

All levels are expressed as prices of the YES token. In the YES phase the bot buys YES on the
way down and sells it on the way up; once the YES position is fully sold it switches to the NO
phase and trades the mirror image (buys NO when YES is high, sells NO when YES is low).

The strategy is pure: it never talks to the network. The executor feeds it order-book snapshots
and fills, asks it which orders should be resting, and tells it what a stop-loss sale returned.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Optional

YES = "YES"
NO = "NO"

ACTIVE = "ACTIVE"
STOPPED = "STOPPED"
CLOSING = "CLOSING"
DONE = "DONE"

BUY = "BUY"
SELL = "SELL"


@dataclass(frozen=True)
class Config:
    budget_usd: float
    allocation: tuple = (0.10, 0.15, 0.25, 0.50)
    yes_buy_levels: tuple = (0.38, 0.37, 0.36, 0.35)
    yes_sell_levels: tuple = (0.42, 0.43, 0.44, 0.45)
    no_buy_levels: tuple = (0.42, 0.43, 0.44, 0.45)
    no_sell_levels: tuple = (0.38, 0.37, 0.36, 0.35)
    stop_yes_below: float = 0.31
    stop_yes_above: float = 0.49
    stop_wait_seconds: int = 3 * 3600
    # Beyond these the stop fires at once, without waiting; 0 and 1 disable them.
    hard_stop_yes_below: float = 0.0
    hard_stop_yes_above: float = 1.0
    # While flat (nothing bought in the current phase), arm the ladder on the side the price is
    # closer to: switch to NO at or above the first threshold, back to YES at or below the second.
    # The gap between them keeps the bot from re-placing orders while the price hovers in between.
    nearest_side_entry: bool = False
    entry_no_at_or_above: float = 0.41
    entry_yes_at_or_below: float = 0.39
    resume_yes_at_or_above: float = 0.36
    resume_no_at_or_below: float = 0.44
    stop_trading_at: float = 0.0  # unix seconds; 0 disables the cutoff
    min_order_shares: float = 5.0

    def validate(self) -> None:
        if self.budget_usd <= 0:
            raise ValueError("budget_usd must be positive")
        if abs(sum(self.allocation) - 1.0) > 1e-9:
            raise ValueError("allocation must sum to 1")
        for name in ("yes_buy_levels", "yes_sell_levels", "no_buy_levels", "no_sell_levels"):
            if len(getattr(self, name)) != len(self.allocation):
                raise ValueError(f"{name} must have {len(self.allocation)} levels")
        if max(self.yes_buy_levels) >= min(self.yes_sell_levels):
            raise ValueError("every YES buy level must be below every YES sell level")
        if min(self.no_buy_levels) <= max(self.no_sell_levels):
            raise ValueError("every NO buy level (as YES price) must be above every NO sell level")
        smallest = self.budget_usd * min(self.allocation) / max(self.yes_buy_levels)
        if smallest < self.min_order_shares:
            raise ValueError(
                f"budget too small: the smallest ladder step buys {smallest:.2f} shares, "
                f"the exchange minimum is {self.min_order_shares}"
            )


@dataclass(frozen=True)
class Market:
    """Top of the YES order book. NO prices are the mirror image (NO bid = 1 - YES ask)."""

    yes_bid: Optional[float]
    yes_ask: Optional[float]

    @property
    def yes_mid(self) -> Optional[float]:
        if self.yes_bid is None or self.yes_ask is None:
            return self.yes_bid if self.yes_ask is None else self.yes_ask
        return (self.yes_bid + self.yes_ask) / 2


@dataclass(frozen=True)
class OrderIntent:
    key: str  # stable identity, e.g. "YES:buy:2"
    outcome: str  # YES or NO token
    side: str  # BUY or SELL
    price: float  # price of the traded token
    size: float  # shares


@dataclass
class State:
    side: str = YES
    mode: str = ACTIVE
    cash: float = 0.0
    buy_remaining_usd: list = field(default_factory=list)
    bought: float = 0.0
    sold: list = field(default_factory=list)
    shares: float = 0.0
    cost: float = 0.0
    realized_pnl: float = 0.0
    breach_since: Optional[float] = None
    cycles: int = 0
    started_at: Optional[float] = None  # first start of this state (unix seconds)
    leftover: dict = field(default_factory=dict)  # outcome -> shares below the exchange minimum

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "State":
        return cls(**data)


def floor_shares(x: float) -> float:
    return math.floor(x * 100 + 1e-9) / 100


def token_price(side: str, yes_level: float) -> float:
    return round(yes_level if side == YES else 1 - yes_level, 4)


class Strategy:
    def __init__(self, config: Config, state: Optional[State] = None):
        config.validate()
        self.cfg = config
        if state is None:
            state = State(cash=config.budget_usd)
            self._start_phase(state, YES)
        self.st = state

    # --- phase management -------------------------------------------------------------

    def _start_phase(self, st: State, side: str) -> None:
        if side != st.side and st.shares > 0:
            # Too few shares of the old token to sell on the exchange; keep a record of them
            # instead of dropping them from the books.
            st.leftover[st.side] = round(st.leftover.get(st.side, 0.0) + st.shares, 6)
            st.shares = 0.0
            st.cost = 0.0
        st.side = side
        st.mode = ACTIVE
        st.buy_remaining_usd = [st.cash * a for a in self.cfg.allocation]
        # A restart on the same side keeps what is still held, so the new sell ladder includes it.
        st.bought = st.shares
        st.sold = [0.0] * len(self.cfg.allocation)
        st.breach_since = None

    def _levels(self, kind: str) -> tuple:
        if self.st.side == YES:
            return self.cfg.yes_buy_levels if kind == BUY else self.cfg.yes_sell_levels
        return self.cfg.no_buy_levels if kind == BUY else self.cfg.no_sell_levels

    # --- what should be resting -----------------------------------------------------

    def desired_orders(self) -> list:
        st = self.st
        if st.mode != ACTIVE:
            return []
        intents = []
        for i, level in enumerate(self._levels(BUY)):
            px = token_price(st.side, level)
            size = floor_shares(min(st.buy_remaining_usd[i], st.cash) / px)
            if size >= self.cfg.min_order_shares:
                intents.append(OrderIntent(f"{st.side}:buy:{i}", st.side, BUY, px, size))
        # A quarter of a small position can be below the exchange minimum; such a slice rolls
        # into the next level so the position can always be sold, and the last level takes the rest.
        unassigned = st.shares
        carry = 0.0
        sell_levels = self._levels(SELL)
        for i, level in enumerate(sell_levels):
            want = carry + self.sell_target(i)
            if i == len(sell_levels) - 1:
                want = unassigned
            target = floor_shares(min(want, unassigned))
            if target >= self.cfg.min_order_shares:
                px = token_price(st.side, level)
                intents.append(OrderIntent(f"{st.side}:sell:{i}", st.side, SELL, px, target))
                unassigned -= target
                carry = 0.0
            else:
                carry = want
        return intents

    def sell_target(self, i: int) -> float:
        share = 1.0 / len(self.cfg.allocation)
        return max(0.0, share * self.st.bought - self.st.sold[i])

    # --- fills ----------------------------------------------------------------------

    def on_fill(self, key: str, qty: float, price: float) -> None:
        """Apply a fill of a resting ladder order identified by its intent key."""
        if qty <= 0:
            return
        st = self.st
        side, kind, idx = key.split(":")
        if side != st.side:
            return  # late fill of an order from the previous phase is handled by on_external_fill
        i = int(idx)
        if kind == "buy":
            usd = qty * price
            st.cash -= usd
            st.buy_remaining_usd[i] = max(0.0, st.buy_remaining_usd[i] - usd)
            st.shares += qty
            st.cost += usd
            st.bought += qty
        else:
            st.sold[i] += self._sell(qty, price)
        self._maybe_close_phase()

    def on_external_fill(self, side: str, kind: str, qty: float, price: float) -> None:
        """Fill that cannot be attributed to a current ladder level (e.g. after a phase change)."""
        st = self.st
        if side != st.side and kind == "buy" and st.bought == 0 and st.shares == 0:
            # A buy of the side just switched away from filled before its cancel landed: those
            # shares are the position now, so trade that side.
            self._start_phase(st, side)
        if side != st.side:
            if kind == "sell":
                st.cash += qty * price
            else:
                st.cash -= qty * price
            return
        if kind == "sell":
            self._sell(qty, price)
        else:
            st.cash -= qty * price
            st.shares += qty
            st.cost += qty * price
            st.bought += qty

    def _sell(self, qty: float, price: float) -> float:
        """Sell up to qty held shares; returns the quantity actually sold."""
        st = self.st
        qty = min(qty, st.shares)
        if qty <= 0:
            return 0.0
        avg = st.cost / st.shares
        st.cash += qty * price
        st.realized_pnl += qty * (price - avg)
        st.cost -= avg * qty
        st.shares -= qty
        if st.shares < 1e-9:
            st.shares = 0.0
            st.cost = 0.0
        return qty

    def _maybe_close_phase(self) -> None:
        st = self.st
        if st.bought <= 0:
            return
        if st.shares < self.cfg.min_order_shares and sum(st.sold) > 0:
            st.cycles += 1
            self._start_phase(st, NO if st.side == YES else YES)

    # --- risk controls ----------------------------------------------------------------

    def check_risk(self, market: Market, now: float) -> Optional[str]:
        """Return "close" when the cutoff passed, "stop" when the stop-loss fires, else None.

        The same action is returned again on every call until the position is actually sold, so a
        liquidation that failed or filled only partly is retried.
        """
        st = self.st
        holding = st.shares >= self.cfg.min_order_shares
        if st.mode == DONE:
            return None
        if st.mode == CLOSING:
            if holding:
                return "close"
            st.mode = DONE
            return None
        if self.cfg.stop_trading_at and now >= self.cfg.stop_trading_at:
            st.mode = CLOSING
            return "close"
        if st.mode == ACTIVE:
            self._pick_entry_side(market)
        if st.mode == STOPPED:
            if holding:
                return "stop"
            mid = market.yes_mid
            if mid is not None:
                back = (
                    mid >= self.cfg.resume_yes_at_or_above
                    if st.side == YES
                    else mid <= self.cfg.resume_no_at_or_below
                )
                if back:
                    self._start_phase(st, st.side)
            return None
        # The stop compares the price we could sell at right now: the YES bid while holding YES,
        # the NO bid (1 - YES ask) while holding NO.
        if st.side == YES:
            beyond = market.yes_bid is not None and market.yes_bid < self.cfg.stop_yes_below
            far = market.yes_bid is not None and market.yes_bid < self.cfg.hard_stop_yes_below
        else:
            beyond = market.yes_ask is not None and market.yes_ask > self.cfg.stop_yes_above
            far = market.yes_ask is not None and market.yes_ask > self.cfg.hard_stop_yes_above
        if far and holding:
            st.mode = STOPPED
            st.breach_since = None
            return "stop"
        if not beyond:
            st.breach_since = None
            return None
        if st.breach_since is None:
            st.breach_since = now
        if st.shares >= self.cfg.min_order_shares and now - st.breach_since >= self.cfg.stop_wait_seconds:
            st.mode = STOPPED
            st.breach_since = None
            return "stop"
        return None

    def _pick_entry_side(self, market: Market) -> None:
        st = self.st
        mid = market.yes_mid
        if not self.cfg.nearest_side_entry or mid is None or st.bought > 0 or st.shares > 0:
            return
        if st.side == YES and mid >= self.cfg.entry_no_at_or_above:
            self._start_phase(st, NO)
        elif st.side == NO and mid <= self.cfg.entry_yes_at_or_below:
            self._start_phase(st, YES)

    def on_liquidation(self, qty: float, price: float) -> None:
        """Record a stop-loss or cutoff sale of the held position."""
        self._sell(qty, price)
        if self.st.mode == CLOSING and self.st.shares < self.cfg.min_order_shares:
            self.st.mode = DONE

    def mark_to_market(self, market: Market) -> float:
        st = self.st
        if st.shares <= 0:
            return st.cash
        if st.side == YES:
            px = market.yes_bid if market.yes_bid is not None else 0.0
        else:
            px = 1 - market.yes_ask if market.yes_ask is not None else 0.0
        return st.cash + st.shares * px
