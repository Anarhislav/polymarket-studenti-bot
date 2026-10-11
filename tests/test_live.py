"""LiveExchange against a fake SDK client: no request leaves the machine."""

import sys
from decimal import Decimal
from types import SimpleNamespace

import pytest

from bot import __main__ as entry
from bot.live import LiveExchange, LiveSetupError, OrderRejected
from bot.runner import Runner
from bot.strategy import Config, Market

YES_T, NO_T, COND = "yes-token", "no-token", "0xcond"


class FakeClient:
    def __init__(self, collateral=100.0, tokens=None, approved=True):
        self.collateral = collateral
        self.tokens = tokens or {}
        self.approved = approved
        self.open = {}
        self.final = {}
        self.placed = []
        self.cancelled = []
        self.reject = None
        self.market_resp = None
        self.list_calls = 0
        self.n = 0

    def get_trading_approvals_state(self):
        return SimpleNamespace(is_fully_approved=self.approved, missing=() if self.approved else ("CTF",))

    def get_balance_allowance(self, asset_type, token_id=None):
        amount = self.collateral if asset_type == "COLLATERAL" else self.tokens.get(token_id, 0.0)
        return SimpleNamespace(balance=int(round(amount * 1e6)))

    def place_limit_order(self, token_id, price, size, side):
        self.placed.append((token_id, price, size, side))
        if self.reject:
            return SimpleNamespace(ok=False, code=self.reject, message="no")
        self.n += 1
        oid = f"o{self.n}"
        self.open[oid] = SimpleNamespace(id=oid, status="LIVE", size_matched=Decimal("0"))
        return SimpleNamespace(ok=True, order_id=oid, status="live")

    def list_open_orders(self, market):
        assert market == COND
        self.list_calls += 1
        return list(self.open.values())

    def get_order(self, order_id):
        return self.final[order_id]

    def cancel_order(self, order_id):
        self.cancelled.append(order_id)

    def fill(self, oid, matched, still_open=False):
        order = SimpleNamespace(id=oid, status="LIVE" if still_open else "MATCHED", size_matched=Decimal(str(matched)))
        if still_open:
            self.open[oid] = order
        else:
            self.open.pop(oid, None)
            self.final[oid] = order

    def place_market_order(self, token_id, side, shares, order_type):
        self.placed.append((token_id, "market", shares, side))
        return self.market_resp


def make(client, **kw):
    return LiveExchange(client, YES_T, NO_T, COND, status_poll_seconds=0, **kw)


def test_check_ready_requires_approvals_and_budget():
    with pytest.raises(LiveSetupError, match="approvals missing"):
        make(FakeClient(approved=False)).check_ready(100)
    with pytest.raises(LiveSetupError, match=r"below budget_usd \$100.00"):
        make(FakeClient(collateral=99.5)).check_ready(100)
    assert make(FakeClient(collateral=100.0)).check_ready(100) == pytest.approx(100.0)


def test_limit_order_is_sent_with_two_decimals_and_tracked():
    client = FakeClient()
    ex = make(client)
    oid = ex.place_limit("YES", "BUY", 0.37, 27.0270)
    assert client.placed == [(YES_T, "0.37", "27.02", "BUY")]
    status = ex.order_status(oid)
    assert status.open and status.matched == 0


def test_rejected_order_raises():
    client = FakeClient()
    client.reject = "not_enough_balance"
    with pytest.raises(OrderRejected, match="not_enough_balance"):
        make(client).place_limit("YES", "BUY", 0.37, 27)


def test_sell_is_capped_to_shares_held():
    client = FakeClient(tokens={YES_T: 26.9})
    make(client).place_limit("YES", "SELL", 0.42, 27.02)
    assert client.placed[-1] == (YES_T, "0.42", "26.90", "SELL")


def test_sell_below_minimum_is_not_sent():
    client = FakeClient(tokens={YES_T: 3.0})
    with pytest.raises(OrderRejected, match="too_small"):
        make(client).place_limit("YES", "SELL", 0.42, 27)
    assert client.placed == []


def test_partial_then_final_fill():
    client = FakeClient()
    ex = make(client)
    oid = ex.place_limit("YES", "BUY", 0.37, 27)
    client.fill(oid, 10, still_open=True)
    assert ex.order_status(oid).matched == 10 and ex.order_status(oid).open
    client.fill(oid, 27)
    status = ex.order_status(oid)
    assert status.matched == 27 and not status.open


def test_status_is_refreshed_once_per_interval():
    client = FakeClient()
    ex = LiveExchange(client, YES_T, NO_T, COND, status_poll_seconds=60)
    ids = [ex.place_limit("YES", "BUY", p, 27) for p in (0.37, 0.36)]
    for _ in range(5):
        for oid in ids:
            ex.order_status(oid)
    assert client.list_calls == 1


@pytest.mark.parametrize("making,taking,want_sold,want_price", [
    ("100", "38", 100.0, 0.38),
    ("38", "100", 100.0, 0.38),
])
def test_sell_now_reads_shares_and_price(making, taking, want_sold, want_price):
    client = FakeClient(tokens={YES_T: 150.0})
    client.market_resp = SimpleNamespace(ok=True, order_id="m1", making_amount=Decimal(making),
                                         taking_amount=Decimal(taking))
    sold, price = make(client).sell_now("YES", 100)
    assert sold == pytest.approx(want_sold) and price == pytest.approx(want_price)


def test_sell_now_without_bids_sells_nothing():
    client = FakeClient(tokens={YES_T: 150.0})
    client.market_resp = SimpleNamespace(ok=False, code="fak_not_filled", message="no match")
    assert make(client).sell_now("YES", 100) == (0.0, 0.0)


def test_runner_retries_rejected_order_after_cooldown(tmp_path, monkeypatch):
    client = FakeClient()
    client.reject = "not_enough_balance"
    runner = Runner(make(client), Config(budget_usd=100), tmp_path, state_suffix=".live")
    monkeypatch.setattr(LiveExchange, "market", lambda self: Market(0.40, 0.41))
    runner.tick(0)
    first = len(client.placed)
    runner.tick(1)
    assert len(client.placed) == first  # within the cooldown nothing is resent
    assert runner.orders == {}
    assert (tmp_path / "state.live.json").exists()


def test_live_needs_both_switches(monkeypatch, tmp_path, caplog):
    cfg = tmp_path / "config.json"
    cfg.write_text('{"market": {"yes_token_id": "y", "no_token_id": "n", "condition_id": "c"},'
                   ' "strategy": {"budget_usd": 100}, "trading_mode": "paper", "market_data": "rest"}')
    monkeypatch.setattr(sys, "argv", ["bot", "--config", str(cfg), "--workdir", str(tmp_path), "--live"])
    with pytest.raises(SystemExit) as exit_info:
        entry.main()
    assert exit_info.value.code == entry.EXIT_CONFIG
    assert "needs both --live" in caplog.text
