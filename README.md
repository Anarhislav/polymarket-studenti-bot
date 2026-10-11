# Polymarket bot: Student List range trading

The bot trades the market "Will Student List win the most seats in the 2026 Serbian parliamentary
election?" using the rules in `config.json`. It currently runs **in paper mode only**: it reads
the real order book but never signs or sends an order.

## Rules

All levels are YES prices.

| Phase | Buy (share of budget) | Sell |
|---|---|---|
| YES | 37% (10%), 36% (15%), 35% (25%), 34% (50%) | 25% of the position at each of 42, 43, 44, 45% |
| NO | when YES reaches 42% (10%), 43% (15%), 44% (25%), 45% (50%) | 25% each when YES falls to 38, 37, 36, 35% |

- Once the whole position is sold, the bot switches to the opposite phase with all its cash.
- **The stop-loss is currently disabled** (`stop_yes_below: 0`, `stop_yes_above: 1` in `config.json`).
  When enabled: if the best YES bid stays below 31% (while holding YES), or the best YES ask stays
  above 49% (while holding NO), **for 3 hours without interruption**, the bot sells the whole
  position and pauses. It starts a new ladder once the price comes back (YES ≥ 36%, or ≤ 44%;
  `resume_yes_at_or_above`, `resume_no_at_or_below`). If the sale fails or fills only partly, the
  bot retries it every round.
- **Cutoff** (`stop_trading_at_utc`, currently `"2026-10-20T00:00:00Z"`): the bot cancels its orders,
  sells the position and stops. The market resolves on 25 October 2026.
- The bot only cancels orders it placed itself.

## Running (paper mode)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pytest -q
.venv/bin/python -m bot --config config.json --workdir .
```

- `state.json`: state (phase, cash, position, open orders); the bot resumes from it after a restart.
- `ledger.csv`: every fill, stop-loss and close, with cash, average price and realized profit.
- Stopping: Ctrl+C or an empty `STOP` file in the working directory. The bot cancels its orders,
  leaves the position as is and deletes `STOP` so the next start does not exit right away.
- A remainder smaller than the exchange minimum (5 shares) cannot be sold; on a phase change it
  is recorded under `leftover` in `state.json`.

The simulation is optimistic: an order counts as fully filled as soon as the price reaches it,
while a real order would queue behind others at the same price.

## Backtest

```bash
.venv/bin/python -m bot.backtest --config config.json --since 2026-09-24
```

Replays historical trades through the same strategy the bot uses. Fees are not included
(this market has them enabled, `politics_fees`).

## Live trading

Not included. It needs an adapter for `py-clob-client-v2` (1.2.0, the new CLOB V2 with pUSD)
with the same methods as `bot/paper.py`: `market`, `place_limit`, `order_status`, `cancel`, `sell_now`.
The private key is never written into the code or into `config.json`.
