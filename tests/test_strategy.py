import pytest

from bot.strategy import (ACTIVE, CLOSING, DONE, NO, STOPPED, YES, Config, Market, Strategy)

HOUR = 3600


def make(**kw) -> Strategy:
    return Strategy(Config(budget_usd=1000, **kw))


def fill_all_buys(s: Strategy) -> None:
    for intent in [i for i in s.desired_orders() if i.side == "BUY"]:
        s.on_fill(intent.key, intent.size, intent.price)


def sell_all(s: Strategy) -> None:
    for intent in [i for i in s.desired_orders() if i.side == "SELL"]:
        s.on_fill(intent.key, intent.size, intent.price)


@pytest.mark.parametrize("level,usd,price", [(0, 100, 0.38), (1, 150, 0.37), (2, 250, 0.36), (3, 500, 0.35)])
def test_yes_buy_ladder(level, usd, price):
    buys = {i.key: i for i in make().desired_orders()}
    intent = buys[f"YES:buy:{level}"]
    assert intent.outcome == YES and intent.price == price
    assert intent.size == pytest.approx(usd / price, abs=0.01)


def test_sells_quarter_of_position_per_level():
    s = make()
    fill_all_buys(s)
    sells = [i for i in s.desired_orders() if i.side == "SELL"]
    assert [i.price for i in sells] == [0.42, 0.43, 0.44, 0.45]
    assert sum(i.size for i in sells) == pytest.approx(s.st.shares, abs=0.05)
    assert s.st.cost / s.st.shares == pytest.approx(0.358, abs=0.001)


def test_flips_to_no_after_full_exit_and_back():
    s = make()
    fill_all_buys(s)
    sell_all(s)
    assert s.st.side == NO and s.st.mode == ACTIVE and s.st.cash == pytest.approx(1214, abs=1)
    no_buys = [i for i in s.desired_orders() if i.side == "BUY"]
    assert [i.price for i in no_buys] == [0.58, 0.57, 0.56, 0.55]
    fill_all_buys(s)
    sell_all(s)
    assert s.st.side == YES and s.st.cycles == 2
    assert s.st.cash == pytest.approx(1381, abs=2)


def test_partial_ladder_then_exit_flips():
    s = make()
    first = next(i for i in s.desired_orders() if i.key == "YES:buy:0")
    s.on_fill(first.key, first.size, first.price)
    sell_all(s)
    assert s.st.side == NO
    assert s.st.cash == pytest.approx(1000 + first.size * (0.435 - 0.38), abs=1)


def test_small_position_rolls_quarters_into_sellable_orders():
    s = Strategy(Config(budget_usd=70))
    first = next(i for i in s.desired_orders() if i.key == "YES:buy:0")
    s.on_fill(first.key, first.size, first.price)
    assert first.size / 4 < 5
    sells = [i for i in s.desired_orders() if i.side == "SELL"]
    assert all(i.size >= 5 for i in sells)
    assert sum(i.size for i in sells) == pytest.approx(first.size, abs=0.02)
    sell_all(s)
    assert s.st.side == NO


def test_hundred_dollar_ladder():
    s = Strategy(Config(budget_usd=100))
    buys = {i.key: i for i in s.desired_orders()}
    assert [round(buys[f"YES:buy:{n}"].size, 2) for n in range(4)] == [26.31, 40.54, 69.44, 142.85]
    fill_all_buys(s)
    sell_all(s)
    assert s.st.cash == pytest.approx(121.4, abs=0.2)


def test_stop_loss_waits_three_hours():
    s = make()
    fill_all_buys(s)
    low = Market(yes_bid=0.30, yes_ask=0.31)
    assert s.check_risk(low, 0) is None
    assert s.check_risk(low, 3 * HOUR - 1) is None
    assert s.check_risk(low, 3 * HOUR) == "stop"
    assert s.st.mode == STOPPED
    s.on_liquidation(s.st.shares, 0.30)
    assert s.desired_orders() == []


def test_stop_timer_resets_when_price_returns():
    s = make()
    fill_all_buys(s)
    s.check_risk(Market(0.30, 0.31), 0)
    s.check_risk(Market(0.33, 0.34), 2 * HOUR)
    assert s.check_risk(Market(0.30, 0.31), 4 * HOUR) is None
    assert s.check_risk(Market(0.30, 0.31), 7 * HOUR) == "stop"


def test_no_phase_stop_uses_yes_ask():
    s = make()
    fill_all_buys(s)
    sell_all(s)
    fill_all_buys(s)
    assert s.st.side == NO
    high = Market(yes_bid=0.50, yes_ask=0.51)
    s.check_risk(high, 0)
    assert s.check_risk(high, 3 * HOUR) == "stop"


def test_resumes_new_ladder_after_stop():
    s = make()
    fill_all_buys(s)
    s.check_risk(Market(0.30, 0.31), 0)
    s.check_risk(Market(0.30, 0.31), 3 * HOUR)
    s.on_liquidation(s.st.shares, 0.30)
    cash = s.st.cash
    s.check_risk(Market(0.35, 0.36), 4 * HOUR)
    assert s.st.mode == STOPPED
    s.check_risk(Market(0.36, 0.37), 5 * HOUR)
    assert s.st.mode == ACTIVE and s.st.side == YES
    assert sum(s.st.buy_remaining_usd) == pytest.approx(cash)


def test_cutoff_closes_position():
    s = Strategy(Config(budget_usd=1000, stop_trading_at=100))
    fill_all_buys(s)
    assert s.check_risk(Market(0.40, 0.41), 100) == "close"
    assert s.st.mode == CLOSING and s.desired_orders() == []
    s.on_liquidation(s.st.shares, 0.40)
    assert s.st.mode == DONE


@pytest.mark.parametrize("kw,msg", [
    (dict(budget_usd=10), "budget too small"),
    (dict(budget_usd=1000, allocation=(0.5, 0.5, 0.5, 0.5)), "allocation must sum to 1"),
    (dict(budget_usd=1000, yes_buy_levels=(0.44, 0.37, 0.36, 0.35)), "below every YES sell level"),
])
def test_config_validation(kw, msg):
    with pytest.raises(ValueError, match=msg):
        Strategy(Config(**kw))


def test_failed_cutoff_sale_is_retried():
    s = Strategy(Config(budget_usd=1000, stop_trading_at=100))
    fill_all_buys(s)
    assert s.check_risk(Market(0.40, 0.41), 100) == "close"
    # the sale never happened (e.g. network error): ask again
    assert s.check_risk(Market(0.40, 0.41), 200) == "close"
    s.on_liquidation(s.st.shares, 0.40)
    assert s.st.mode == DONE and s.check_risk(Market(0.40, 0.41), 300) is None


def test_failed_stop_sale_is_retried_and_resume_keeps_position():
    s = make()
    fill_all_buys(s)
    s.check_risk(Market(0.30, 0.31), 0)
    assert s.check_risk(Market(0.30, 0.31), 3 * HOUR) == "stop"
    shares = s.st.shares
    # price came back, but the position was never sold: sell it, don't resume over it
    assert s.check_risk(Market(0.40, 0.41), 4 * HOUR) == "stop"
    assert s.st.mode == STOPPED and s.st.shares == shares
    s.on_liquidation(shares - 3, 0.40)  # partial sale leaves 3 unsellable shares
    s.check_risk(Market(0.40, 0.41), 5 * HOUR)
    assert s.st.mode == ACTIVE and s.st.shares == pytest.approx(3)
    assert s.st.bought == pytest.approx(3)


def test_dust_is_recorded_on_phase_flip():
    s = make()
    fill_all_buys(s)
    total = s.st.shares
    sells = [i for i in s.desired_orders() if i.side == "SELL"]
    for intent in sells[:-1]:
        s.on_fill(intent.key, intent.size, intent.price)
    last = sells[-1]
    s.on_fill(last.key, last.size - 2, last.price)  # 2 shares stay behind
    assert s.st.side == NO and s.st.shares == 0
    assert s.st.leftover[YES] == pytest.approx(2)
    assert sum(s.st.sold) == 0 and total > 0


def test_sold_counts_only_shares_actually_held():
    s = make()
    first = next(i for i in s.desired_orders() if i.key == "YES:buy:0")
    s.on_fill(first.key, first.size, first.price)
    s.on_fill("YES:sell:0", first.size - 10, 0.42)
    s.on_fill("YES:sell:1", 50, 0.43)  # more than is left
    assert s.st.side == NO and s.st.cycles == 1


def test_hard_stop_fires_without_waiting():
    s = Strategy(Config(budget_usd=1000, hard_stop_yes_below=0.25))
    fill_all_buys(s)
    assert s.check_risk(Market(yes_bid=0.28, yes_ask=0.29), 0) is None
    assert s.check_risk(Market(yes_bid=0.24, yes_ask=0.25), 60) == "stop"


def test_hard_stop_disabled_by_default():
    s = make()
    fill_all_buys(s)
    assert s.check_risk(Market(yes_bid=0.05, yes_ask=0.06), 0) is None


def test_nearest_side_entry_switches_while_flat_with_hysteresis():
    s = Strategy(Config(budget_usd=100, nearest_side_entry=True))
    s.check_risk(Market(0.39, 0.40), 0)
    assert s.st.side == YES
    s.check_risk(Market(0.41, 0.42), 1)
    assert s.st.side == NO
    assert [i.price for i in s.desired_orders() if i.side == "BUY"] == [0.58, 0.57, 0.56, 0.55]
    s.check_risk(Market(0.39, 0.40), 2)  # mid 0.395 is inside the gap: stays NO
    assert s.st.side == NO
    s.check_risk(Market(0.38, 0.39), 3)
    assert s.st.side == YES


def test_nearest_side_entry_locks_once_something_is_bought():
    s = Strategy(Config(budget_usd=100, nearest_side_entry=True))
    first = next(i for i in s.desired_orders() if i.key == "YES:buy:0")
    s.on_fill(first.key, first.size, first.price)
    s.check_risk(Market(0.45, 0.46), 0)
    assert s.st.side == YES and s.st.shares == pytest.approx(first.size)


def test_late_fill_of_cancelled_side_becomes_the_position():
    s = Strategy(Config(budget_usd=100, nearest_side_entry=True))
    s.check_risk(Market(0.42, 0.43), 0)
    assert s.st.side == NO
    s.on_external_fill(YES, "buy", 26.31, 0.38)
    assert s.st.side == YES and s.st.shares == pytest.approx(26.31)
    assert s.st.cash == pytest.approx(100 - 26.31 * 0.38)
