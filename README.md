# Polymarket bot: Studentska lista, trgovanje u rasponu

Bot trguje na tržištu „Will Student List win the most seats in the 2026 Serbian parliamentary
election?" po pravilima iz `config.json`. Trenutno radi **samo u režimu bez novca** (paper):
čita pravu knjigu naloga, ali nijedan nalog ne potpisuje niti šalje.

## Pravila

Svi nivoi su cene DA.

| Faza | Kupovina (deo budžeta) | Prodaja |
|---|---|---|
| DA | 38% (10%), 37% (15%), 36% (25%), 35% (50%) | po 25% pozicije na 42, 43, 44, 45% |
| NE | kad DA dođe na 42% (10%), 43% (15%), 44% (25%), 45% (50%) | po 25% kad DA padne na 38, 37, 36, 35% |

- Kad se cela pozicija proda, bot prelazi u suprotnu fazu sa svim novcem.
- **Stop-loss je trenutno isključen** (`stop_yes_below: 0`, `stop_yes_above: 1` u `config.json`).
  Kad je uključen: ako najbolja ponuda za DA ostane ispod 31% (dok drži DA), ili najbolja tražnja
  iznad 49% (dok drži NE), **3 sata bez prekida**, bot prodaje celu poziciju i pauzira.
  Nastavlja novom lestvicom kad se cena vrati (DA ≥ 36%, odnosno ≤ 44%; `resume_yes_at_or_above`,
  `resume_no_at_or_below`). Ako prodaja ne uspe ili prođe delimično, bot je ponavlja svakog kruga.
- **Datum prestanka** (`stop_trading_at_utc`, npr. `"2026-10-18T00:00:00Z"`): bot otkazuje
  naloge, prodaje poziciju i staje. Tržište se rešava 25.10.2026.
- Bot otkazuje samo naloge koje je sam postavio.

## Pokretanje (bez novca)

```bash
python3 -m venv .venv
.venv/bin/pip install pytest
.venv/bin/python -m pytest -q
.venv/bin/python -m bot --config config.json --workdir .
```

- `state.json`: stanje (faza, gotovina, pozicija, otvoreni nalozi); bot nastavlja od njega posle restarta.
- `ledger.csv`: svaka popuna, stop-loss i zatvaranje, sa gotovinom, prosečnom cenom i ostvarenom zaradom.
- Gašenje: Ctrl+C ili prazan fajl `STOP` u radnom direktorijumu. Bot otkazuje svoje naloge, poziciju ostavlja
  i briše `STOP`, da sledeće pokretanje ne bi odmah stalo.
- Ostatak pozicije manji od minimuma berze (5 shares) ne može da se proda; pri promeni faze se
  beleži u `leftover` u `state.json`.

Simulacija je optimistična: nalog se računa kao ceo popunjen čim cena dođe do njega, iako bi
pravi nalog stajao u redu iza drugih po istoj ceni.

## Istorijski test

```bash
.venv/bin/python -m bot.backtest --config config.json --since 2026-09-24
```

Pušta istorijske trgovine kroz istu strategiju koju koristi bot. Naknade nisu uračunate
(na ovom tržištu su uključene, `politics_fees`).

## Pravo trgovanje

Nije uključeno. Za to je potreban adapter za `py-clob-client-v2` (1.2.0, novi CLOB V2 sa pUSD)
sa istim metodama kao `bot/paper.py`: `market`, `place_limit`, `order_status`, `cancel`, `sell_now`.
Privatni ključ se nikad ne upisuje u kod ni u `config.json`.
