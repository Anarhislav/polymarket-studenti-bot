from bot.check import Report, check_env


def test_env_check_never_prints_secret_values(monkeypatch, capsys):
    monkeypatch.setenv("SIGNER_PRIVATE_KEY", "0xsecretkeyvalue")
    monkeypatch.setenv("POLYMARKET_WALLET_ADDRESS", "0xwallet")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:secrettoken")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    r = Report()
    check_env(r)
    out = capsys.readouterr().out
    assert r.failed == 0
    assert "secretkeyvalue" not in out and "secrettoken" not in out
    assert "SIGNER_PRIVATE_KEY is set" in out


def test_env_check_fails_without_live_keys(monkeypatch):
    for name in ("SIGNER_PRIVATE_KEY", "POLYMARKET_WALLET_ADDRESS", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(name, raising=False)
    r = Report()
    check_env(r)
    assert r.failed == 2


def test_budget_all_uses_placeholder_or_override(tmp_path):
    from bot.__main__ import PLACEHOLDER_BUDGET, load_config
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text('{"market": {}, "strategy": {"budget_usd": "all", "allocation": [1.0], '
                        '"yes_buy_levels": [0.38], "yes_sell_levels": [0.42], "no_buy_levels": [0.42], '
                        '"no_sell_levels": [0.38]}}')
    assert load_config(cfg_file)[1].budget_usd == PLACEHOLDER_BUDGET
    assert load_config(cfg_file, budget_override=81.41)[1].budget_usd == 81.41
