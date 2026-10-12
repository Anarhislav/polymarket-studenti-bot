"""Read-only setup check: python -m bot.check --config config.json --workdir .

Verifies .env, geoblock, Telegram and the Polymarket account without placing, cancelling or
changing anything. Secret values are never printed; only whether they are set.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from .__main__ import load_config, load_dotenv
from .notify import API_URL

REQUIRED_LIVE = ("SIGNER_PRIVATE_KEY", "POLYMARKET_WALLET_ADDRESS")
TELEGRAM = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")


class Report:
    def __init__(self):
        self.failed = 0

    def ok(self, text: str) -> None:
        print(f"  OK    {text}")

    def warn(self, text: str) -> None:
        print(f"  WARN  {text}")

    def fail(self, text: str) -> None:
        self.failed += 1
        print(f"  FAIL  {text}")


def check_env(r: Report) -> None:
    print("1. .env")
    for name in TELEGRAM + REQUIRED_LIVE:
        if os.environ.get(name, "").strip():
            r.ok(f"{name} is set")
        elif name in REQUIRED_LIVE:
            r.fail(f"{name} is missing")
        else:
            r.warn(f"{name} is missing (Telegram notifications off)")
    for name in ("POLYMARKET_RELAYER_API_KEY", "POLYMARKET_RELAYER_API_KEY_ADDRESS", "PK", "FUNDER", "SIGNATURE_TYPE"):
        if name in os.environ:
            r.warn(f"{name} is in the environment but the bot does not use it")


def check_geoblock(r: Report) -> None:
    print("2. geoblock (this machine's IP)")
    try:
        with urllib.request.urlopen(urllib.request.Request(
                "https://polymarket.com/api/geoblock", headers={"User-Agent": "studenti-bot"}), timeout=10) as resp:
            geo = json.load(resp)
    except Exception as err:
        r.fail(f"could not reach polymarket.com/api/geoblock: {err}")
        return
    where = f"{geo.get('country')}-{geo.get('region')}"
    if geo.get("blocked"):
        r.fail(f"orders are blocked from {where}")
    else:
        r.ok(f"not blocked ({where})")


def check_telegram(r: Report) -> None:
    print("3. Telegram")
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        r.warn("skipped (not configured)")
        return
    body = json.dumps({"chat_id": chat, "text": "✅ [CHECK] Telegram notifications work"}).encode()
    request = urllib.request.Request(API_URL.format(token=token), data=body,
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10):
            pass
        r.ok("test message sent, check your Telegram")
    except urllib.error.HTTPError as err:
        hint = {401: "token is wrong or revoked", 400: "chat id is wrong",
                403: "send /start to the bot first"}.get(err.code, "")
        r.fail(f"Telegram rejected the message (HTTP {err.code}) {hint}")
    except Exception as err:
        r.fail(f"could not reach Telegram: {err}")


def check_account(r: Report, raw: dict, budget: float) -> None:
    print("4. Polymarket account (read only)")
    if any(not os.environ.get(n, "").strip() for n in REQUIRED_LIVE):
        r.fail("skipped: SIGNER_PRIVATE_KEY and POLYMARKET_WALLET_ADDRESS are required")
        return
    from .live import LiveExchange, client_from_env, split_missing_approvals

    m = raw["market"]
    try:
        client = client_from_env()
    except Exception as err:
        r.fail(f"could not connect with the signer key: {type(err).__name__}: {err}")
        return
    try:
        expected = os.environ["POLYMARKET_WALLET_ADDRESS"].strip().lower()
        wallet = str(client.wallet).lower()
        (r.ok if wallet == expected else r.fail)(f"wallet {client.wallet}, type {client.wallet_type}, "
                                                 f"signer {client.signer}")
        ex = LiveExchange(client, m["yes_token_id"], m["no_token_id"], m["condition_id"])
        approvals = client.get_trading_approvals_state()
        if approvals.is_fully_approved:
            r.ok("trading approvals are set")
        else:
            blocking, unused = split_missing_approvals(approvals.missing)
            if blocking:
                r.fail(f"approvals missing for this market: {'; '.join(blocking)}")
            else:
                r.ok("approvals for this market are set")
                r.warn(f"approvals this market does not use are missing: {'; '.join(unused)}")
        balance = ex.collateral_balance()
        if budget is None:
            r.ok(f'pUSD balance ${balance:.2f} (budget_usd "all": the bot trades all of it)')
        else:
            (r.ok if balance + 1e-9 >= budget else r.fail)(
                f"pUSD balance ${balance:.2f}, budget_usd ${budget:.2f}")
        r.ok(f"positions: YES {ex.token_balance('YES'):.2f}, NO {ex.token_balance('NO'):.2f}")
        open_orders = list(client.list_open_orders(market=m["condition_id"]).iter_items())
        (r.ok if not open_orders else r.warn)(f"{len(open_orders)} open order(s) on this market")
    except Exception as err:
        r.fail(f"{type(err).__name__}: {err}")
    finally:
        client.close()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="config.json")
    p.add_argument("--workdir", default=".")
    args = p.parse_args()
    load_dotenv(Path(args.workdir) / ".env")
    raw, cfg = load_config(Path(args.config))
    r = Report()
    check_env(r)
    check_geoblock(r)
    check_telegram(r)
    budget_all = raw["strategy"].get("budget_usd") == "all"
    check_account(r, raw, None if budget_all else cfg.budget_usd)
    budget_text = "all" if budget_all else f"${cfg.budget_usd:.2f}"
    print(f'5. config: trading_mode "{raw.get("trading_mode", "paper")}", budget_usd {budget_text}, '
          f'cutoff {raw["strategy"].get("stop_trading_at_utc")}')
    print("\nall checks passed" if not r.failed else f"\n{r.failed} check(s) failed")
    raise SystemExit(1 if r.failed else 0)


if __name__ == "__main__":
    main()
