from bot.paper import OrderStatus
from bot.runner import Runner
from bot.strategy import CLOSING, DONE, Config, Market


class FakeExchange:
    def __init__(self, failures: int = 0):
        self.failures = failures
        self.sales = []
        self.n = 0

    def market(self):
        return Market(0.40, 0.41)

    def place_limit(self, outcome, side, price, size):
        self.n += 1
        return f"o{self.n}"

    def order_status(self, order_id):
        return OrderStatus(matched=0.0, open=True)

    def cancel(self, order_id):
        pass

    def sell_now(self, outcome, shares):
        if self.failures:
            self.failures -= 1
            raise RuntimeError("network")
        self.sales.append(shares)
        return shares, 0.40


def holding(runner: Runner) -> None:
    st = runner.strategy.st
    st.shares, st.cost, st.bought = 100.0, 36.0, 100.0


def test_cutoff_sale_retried_after_error(tmp_path):
    ex = FakeExchange(failures=1)
    r = Runner(ex, Config(budget_usd=100, stop_trading_at=100), tmp_path)
    holding(r)
    try:
        r.tick(200)
    except RuntimeError:
        pass
    assert r.strategy.st.mode == CLOSING and r.strategy.st.shares == 100
    r.tick(300)
    assert ex.sales == [100] and r.strategy.st.mode == DONE and r.strategy.st.shares == 0


def test_stop_file_is_removed(tmp_path):
    (tmp_path / "STOP").touch()
    r = Runner(FakeExchange(), Config(budget_usd=100), tmp_path, poll_seconds=0)
    r.run()
    assert not (tmp_path / "STOP").exists()
    assert (tmp_path / "state.json").exists()
