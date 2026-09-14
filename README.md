# Stock Trading Tracker

Track a three-stock hotlist, get a technical-analysis recommendation for which
to buy, and — this is the point — **keep the recommendation so it can be graded
against what actually happened** when you sell.

Local, single-user, Yahoo Finance data, no accounts and nothing leaves the
machine except the price requests.

## Setup

Run once:

```bash
setup.bat
```

Then start it any time with:

```bash
run.bat
```

It opens <http://127.0.0.1:8000> in your browser. Ctrl+C in the console stops it.

## On your phone (same Wi-Fi)

1. Once, set a password: `set-password.bat`.
2. Once, in an **admin** PowerShell, allow the phone in. Replace `<Wi-Fi name>`
   with your network's name:

   ```powershell
   Set-NetConnectionProfile -Name "<Wi-Fi name>" -NetworkCategory Private
   New-NetFirewallRule -DisplayName "Stock Tracker 8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -Profile Private
   ```

3. Start the server with `run-lan.bat` instead of `run.bat`. It prints the
   address to open on the phone, such as `http://192.168.1.171:8000`.
4. Log in on the phone, then use **Share → Add to Home Screen** (iPhone) or
   **⋮ → Add to Home screen** (Android). It opens full screen like an app.

The laptop stays logged in without a password. The phone login lasts 30 days.
Running `set-password.bat` again signs out every device. The laptop must be on.
There is no offline mode.

## How to use it

1. **Hotlist & pick** — type three Yahoo symbols into the slots. Just type;
   there is nothing to click to save them. A slot you have changed turns amber
   until it is checked, and it is checked when you click away or when you
   analyse. Non-US listings need their suffix: `TKM1T.TL` (Tallinn), `NOKIA.HE`
   (Helsinki), `VOW3.DE` (Frankfurt). A symbol Yahoo does not know is rejected
   with an explanation, and nothing else in the hotlist is disturbed.
2. **Analyse & recommend** — scores all three on daily candles and ranks them.
   Each card shows the score, a price chart, the five component scores, a trade
   plan (entry / stop / target), and an expandable list of all 15 signals with
   what each one means. Under the header it shows both the **close it was
   scored on** and the **live price** — including pre- and post-market — so you
   can see how far a stock has already moved since the levels were computed.
   **Refresh prices** updates those quotes without re-scoring.
3. **Log a buy** — on the pick or on any candidate. Enter each package you
   bought on its own row: `10 @ 496.82`, `5 @ 496.93`, and so on, with an
   optional fee. The running total shows the shares, the weighted average cost
   and the total spent as you type. You can add more packages to an open
   position later with **Buy more**.
4. **Trades** — open positions are marked to the latest close, with every
   candidate's return so far. **Sell all or part**: enter the number of shares,
   a date and a price, and the rest keeps running. Each sale is listed and can
   be undone. Selling the last share closes the position and grades the
   recommendation.
5. **Track record** — the hit rate, once you have enough closed trades.

Above the cards is a chart of **each candidate's score at every one of the last
20 closes**. It plots the score rather than the price, so the top line is always
the top card — and it shows whether a setup is *building or decaying*, which the
score alone cannot. A stock at 71 that climbed from 49 is a different
proposition from one at 49 that fell from 55. The price move is still there, as
a column in the table underneath.

**Your own trades appear on that chart** once you have any: the holding period
is shaded, and a triangle marks every buy and sell — placed at the score on that
date, so you can see what the model thought at the moment you acted. Hover one
for the shares and price, the score that day, and where the position stands now
(realised P&L and the verdict if closed, unrealised if still open).

## What the score means

The **score is 0–100 with 50 neutral**, and it ranks *these three candidates
against each other*. It is not a probability that the trade will win, and the
app does not pretend otherwise.

The honest probability is the **hit rate** on the Track record tab: how often the
pick actually turned out to be the best of the field. Picking at random from a
field of *n* scores **1/n** — 33% from three, 12.5% from eight — and the app
computes that bar from the candidates each trade actually had, so it stays
honest as the hotlist grows. Below about 20
closed trades the hit rate is noise, and the app says so rather than flattering
a lucky streak.

### The five components

| Component | Weight | What it measures |
|---|---|---|
| Trend | 30% | EMA alignment, EMA50 slope, +DI vs −DI — all damped when ADX says the tape is choppy |
| Momentum | 25% | RSI, MACD histogram and fresh crosses, 20-bar rate of change |
| Structure | 20% | How close to the 52-week high, position in the Bollinger band, and how far price has run above EMA20 |
| Volume | 15% | OBV trend and recent volume vs average, signed by which way price went |
| Risk | 10% | Volatility and stop distance — a calmer chart with a sensible stop scores higher |

Two rules are there to stop it chasing: RSI above 75 is penalised, and a price
more than ~1 ATR above its 20-day average starts losing points. A stock that has
already gone vertical is a worse *entry* than one that has not, whatever the
momentum says.

## What gets graded when you sell

Closing a position answers three separate questions, and the app keeps them
apart because they often disagree:

- **Did the trade make money?** Your entry and exit, your P&L.
- **Was the model's pick right?** All three candidates are re-priced over the
  exact same window and ranked by what actually happened. If the pick came
  first, it was a correct call — even if the whole market fell and the trade
  lost money.
- **Was your decision right?** If you bought something other than the pick, you
  are told what that override cost or gained you.

## Tuning it

Create `config.json` in this folder to override anything in
`app/config.py`. For example, to weight momentum more heavily and use a tighter
stop:

```json
{
  "weights": {
    "trend": 0.25, "momentum": 0.35, "structure": 0.20,
    "volume": 0.12, "risk": 0.08
  },
  "stop_atr_multiple": 1.5,
  "target_r_multiple": 3.0
}
```

Weights must sum to exactly 1.0 — the app refuses to start otherwise rather than
quietly renormalising them. Restart after editing.

## Tests

```bash
.venv\Scripts\python.exe tests\test_core.py
```

86 checks covering the indicator maths, the scoring rules, the cost-basis
arithmetic, partial sells and the grading logic. No network needed — everything runs on
synthetic price series and a temporary database.

## Caveats worth reading once

- **This is a decision-support tool, not advice.** It reads charts. It knows
  nothing about earnings, news, sector, or why a stock is moving.
- Yahoo data is free and occasionally wrong or delayed. Do not trade off a
  single number without looking at the chart.
- **Scores only change once a day.** Everything the model judges is computed
  from completed daily bars, so between one close and the next the score is
  genuinely unchanged — it is not stuck. Live prices appear next to it, but
  they deliberately do not feed the score: a half-finished bar would make RSI
  and MACD flicker, and the saved recommendation would depend on the minute you
  clicked.
- Grading uses **daily closes**, so an intraday entry or exit is approximated by
  that day's close. Over a multi-week swing hold this is small; over two days it
  is not.
- The hit rate measures *relative* skill — picking the best of the field. It
  says nothing about whether any of them were worth buying. **Putting a broad
  index fund such as `IVV` in a slot fixes that**: it becomes a candidate like
  any other, so the grading will tell you when you would have been better off
  just holding the index.
- **Cost basis is moving-average, not FIFO.** Estonian capital-gains tax is
  computed FIFO, so the realised figures here are for judging your trading, not
  for a tax return.
- A part-sold position shows **banked** and **open** profit separately. The
  blended total is there too, but the split is the number that matters while
  you still hold something.

## Layout

```
app/config.py       every tunable, and the weight validation
app/indicators.py   indicator maths, pure functions
app/data.py         Yahoo fetch and cache
app/scoring.py      the 15 signals and the composite score
app/position.py     buys, sells, cost basis, realised P&L
app/review.py       settlement, grading, scoreboard
app/db.py           SQLite schema and queries
app/main.py         FastAPI routes
static/             the single-page frontend
data/stocks.db      your hotlist, recommendations and trades
```
