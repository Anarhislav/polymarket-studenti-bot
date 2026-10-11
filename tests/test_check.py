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
