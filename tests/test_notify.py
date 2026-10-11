import json
import time
import urllib.error

import pytest

from bot import notify
from bot.notify import NullNotifier, TelegramNotifier, from_env
from bot.paper import OrderStatus
from bot.runner import Runner
from bot.strategy import Config, Market


class Recorder:
    def __init__(self):
        self.messages = []

    def send(self, text):
        self.messages.append(text)

    def close(self, timeout=5.0):
        pass


class FillingExchange:
    """Fills the orders listed in self.fill_next on the next status check."""

    def __init__(self):
        self.orders = {}
        self.fill_next = set()
        self.n = 0

    def market(self):
        return Market(0.40, 0.41)

    def place_limit(self, outcome, side, price, size):
        self.n += 1
        oid = f"o{self.n}"
        self.orders[oid] = dict(outcome=outcome, side=side, price=price, size=size)
        return oid

    def order_status(self, order_id):
        if order_id in self.fill_next:
            self.fill_next.discard(order_id)
            return OrderStatus(matched=self.orders[order_id]["size"], open=False)
        return OrderStatus(matched=0.0, open=True)

    def cancel(self, order_id):
        pass

    def sell_now(self, outcome, shares):
        return shares, 0.30


def make_runner(tmp_path, **cfg):
    rec = Recorder()
    ex = FillingExchange()
    runner = Runner(ex, Config(budget_usd=100, **cfg), tmp_path, notifier=rec)
    return runner, ex, rec


def fill(runner, ex, keys):
    ex.fill_next = {runner.orders[k]["id"] for k in keys}


def test_placed_orders_arrive_in_one_message(tmp_path):
    runner, _, rec = make_runner(tmp_path)
    runner.tick(0)
    assert len(rec.messages) == 1
    msg = rec.messages[0]
    assert msg.startswith("📋 Postavljeni nalozi:")
    assert "KUPI DA 26.31 @ 0.38" in msg and "KUPI DA 142.85 @ 0.35" in msg


def test_buy_and_sell_fills_report_position_and_profit(tmp_path):
    runner, ex, rec = make_runner(tmp_path)
    runner.tick(0)
    fill(runner, ex, ["YES:buy:0"])
    runner.tick(1)
    assert any(m.startswith("🟢 Kupljeno 26.31 DA @ 0.38") for m in rec.messages)
    fill(runner, ex, ["YES:sell:0"])
    runner.tick(2)
    sold = next(m for m in rec.messages if m.startswith("💰 Prodato"))
    assert "DA @ 0.42" in sold and "zarada +$" in sold


def test_phase_switch_is_reported(tmp_path):
    runner, ex, rec = make_runner(tmp_path)
    runner.tick(0)
    fill(runner, ex, [k for k in runner.orders if ":buy:" in k])
    runner.tick(1)
    fill(runner, ex, [k for k in runner.orders if ":sell:" in k])
    runner.tick(2)
    assert any("prelazim na NE" in m for m in rec.messages)
    assert runner.strategy.st.side == "NO"


def test_cutoff_sale_is_reported(tmp_path):
    runner, ex, rec = make_runner(tmp_path, stop_trading_at=10)
    runner.tick(0)
    fill(runner, ex, ["YES:buy:0"])
    runner.tick(1)
    runner.tick(20)
    msg = next(m for m in rec.messages if m.startswith("⚠️"))
    assert "Zatvaranje" in msg and "@ 0.30" in msg and "-$" in msg


def test_start_and_stop_are_reported(tmp_path):
    runner, _, rec = make_runner(tmp_path)
    runner.poll = 0
    (tmp_path / "STOP").touch()
    runner.run()
    assert rec.messages[0].startswith("▶️ Bot pokrenut")
    assert rec.messages[-1].startswith("⏹ Bot zaustavljen (STOP fajl)")


def test_disabled_without_credentials(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert isinstance(from_env(), NullNotifier)


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b"{}"


@pytest.mark.parametrize("error,attempts", [(None, 1), (400, 1)])
def test_telegram_request(monkeypatch, error, attempts):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        if error:
            raise urllib.error.HTTPError(request.full_url, error, "bad", {}, None)
        return FakeResponse()

    monkeypatch.setattr(notify.urllib.request, "urlopen", fake_urlopen)
    n = TelegramNotifier("123:ABC", "42", prefix="[PAPER] ")
    n.send("hello")
    deadline = time.time() + 2
    while len(calls) < attempts and time.time() < deadline:
        time.sleep(0.01)
    time.sleep(0.05)
    assert len(calls) == attempts
    assert calls[0].full_url == "https://api.telegram.org/bot123:ABC/sendMessage"
    body = json.loads(calls[0].data)
    assert body["chat_id"] == "42" and body["text"] == "[PAPER] hello"
