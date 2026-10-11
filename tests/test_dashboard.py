import json
import threading
import urllib.request

import pytest

from bot.runner import Runner
from bot.strategy import Config, Market
from dashboard.__main__ import make_server
from dashboard.data import summary
from tests.test_notify import FillingExchange, Recorder, fill


def run_bot(tmp_path):
    """Drive the real runner through a buy and a sell so it writes state, status and ledger."""
    ex = FillingExchange()
    runner = Runner(ex, Config(budget_usd=100), tmp_path, notifier=Recorder(), state_suffix=".live")
    runner.strategy.st.started_at = 1_000.0
    runner.tick(1_000)
    fill(runner, ex, ["YES:buy:0"])
    runner.tick(1_010)
    fill(runner, ex, ["YES:sell:0", "YES:sell:1", "YES:sell:2", "YES:sell:3"])
    runner.tick(1_020)
    return runner


def test_summary_from_bot_files(tmp_path):
    run_bot(tmp_path)
    s = summary(tmp_path, ".live", now=1_030)
    assert s["available"] and s["running"] and s["mode"] == "live"
    assert s["budget"] == 100
    assert s["trades"] == {"count": 5, "buys": 1, "sells": 4, "volume": pytest.approx(s["trades"]["volume"])}
    assert s["realized"] > 0
    assert s["profit"] == pytest.approx(s["value"] - 100)
    assert s["phase"] == "NO" and s["cycles"] == 1
    assert s["equity"][0] == {"t": 1_000.0, "value": 100}
    no_orders = [o for o in s["orders"] if o["outcome"] == "NO"]
    assert [o["price"] for o in no_orders] == [0.58, 0.57, 0.56, 0.55]
    assert s["ledger"][0]["side"] == "SELL"  # newest first


def test_stale_status_reads_as_not_running(tmp_path):
    run_bot(tmp_path)
    assert not summary(tmp_path, ".live", now=1_020 + 3_600)["running"]


def test_missing_files(tmp_path):
    s = summary(tmp_path, ".live")
    assert not s["available"] and "state.live.json" in s["message"]


def test_server_serves_page_and_summary(tmp_path):
    run_bot(tmp_path)
    server = make_server(tmp_path, "127.0.0.1", 0, ".live")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        page = urllib.request.urlopen(base + "/").read().decode()
        assert "<title>Studenti bot</title>" in page
        data = json.loads(urllib.request.urlopen(base + "/api/summary").read())
        assert data["available"] and data["budget"] == 100
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(base + "/state.live.json")
    finally:
        server.shutdown()
        server.server_close()
