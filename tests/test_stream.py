import pytest

from bot.stream import TopOfBook

YES, NO = "yes-token", "no-token"


@pytest.mark.parametrize("message,want", [
    ({"event_type": "book", "asset_id": YES,
      "bids": [{"price": "0.38", "size": "10"}, {"price": "0.39", "size": "5"}],
      "asks": [{"price": "0.41", "size": "3"}, {"price": "0.40", "size": "7"}]},
     (0.39, 0.40)),
    ({"event_type": "best_bid_ask", "asset_id": YES, "best_bid": "0.37", "best_ask": "0.38"},
     (0.37, 0.38)),
    ({"event_type": "price_change", "price_changes": [
        {"asset_id": YES, "price": "0.40", "size": "0", "side": "SELL", "best_bid": "0.39", "best_ask": "0.41"}]},
     (0.39, 0.41)),
    ([{"event_type": "book", "asset_id": YES, "bids": [{"price": "0.30", "size": "1"}], "asks": []}],
     (0.30, None)),
])
def test_applies_market_events(message, want):
    book = TopOfBook([YES, NO])
    assert book.apply(message, now=100.0)
    assert book.get(YES) == want
    assert book.updated_at == 100.0
    assert book.changed.is_set()


def test_ignores_other_assets_and_empty_levels():
    book = TopOfBook([YES, NO])
    assert not book.apply({"event_type": "best_bid_ask", "asset_id": "other", "best_bid": "0.5",
                           "best_ask": "0.6"}, now=1.0)
    assert book.get("other") is None
    book.apply({"event_type": "book", "asset_id": NO,
                "bids": [{"price": "0.55", "size": "0"}, {"price": "0.54", "size": "2"}], "asks": []}, now=2.0)
    assert book.get(NO) == (0.54, None)


def test_unchanged_top_does_not_signal():
    book = TopOfBook([YES])
    event = {"event_type": "best_bid_ask", "asset_id": YES, "best_bid": "0.39", "best_ask": "0.40"}
    book.apply(event, now=1.0)
    book.changed.clear()
    assert not book.apply(event, now=2.0)
    assert not book.changed.is_set()
    assert book.updated_at == 2.0


def test_trade_events_only_refresh_freshness():
    book = TopOfBook([YES])
    assert not book.apply({"event_type": "last_trade_price", "asset_id": YES, "price": "0.4"}, now=5.0)
    assert book.updated_at == 5.0
