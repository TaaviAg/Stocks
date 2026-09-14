# Stock Trading Tracker — project context for Claude Code

## What this is

A local web app that tracks a three-stock hotlist, scores each candidate on
daily-chart technical analysis, recommends the best pick, **saves that
recommendation immutably**, and grades it against what the market actually did
once the position is closed.

Owner: Taavi. Runs on Windows, Python 3.13, no build step, single user, local
only (`127.0.0.1:8000`). Data comes from Yahoo Finance via `yfinance`.

## Current status

- `app/config.py` — every tunable, plus weight validation
- `app/indicators.py` — pure pandas indicator maths (no I/O)
- `app/data.py` — Yahoo access with a TTL cache
- `app/scoring.py` — the 15-signal rule-based score
- `app/review.py` — settlement, grading, the scoreboard
- `app/db.py` — SQLite schema and queries
- `app/main.py` — FastAPI: JSON API + static frontend
- `static/` — one HTML page, one stylesheet, one script; no framework, no npm
- `app/position.py` — the buy/sell walk: cost basis, realised P&L
- `tests/test_core.py` — 86 offline checks, no network, no pytest
- **VERIFIED END TO END** against live Yahoo data on 2026-09-03.

## The two questions this app keeps apart

This is the design decision everything else follows from. A trade has two
independent verdicts, and they routinely disagree:

| Question | Field | Where it shows |
|---|---|---|
| Did the **trade** make money? | `realised_return_pct`, `realised_pnl` | trade header, "Realised P&L" |
| Was the **model's pick** the best of its field? | `pick_was_best`, `pick_rank` | "The model:" callout, hit rate |
| Was **your decision** right, given the pick? | `decision_cost_pct` | "Your decision:" callout |

A losing trade in a falling market can still be a correct pick. A winning trade
can still have been the worst of three. Blending these into one number would
teach nothing, which is the whole reason the recommendation is stored.

**This was found by looking at the UI, not the code.** v1.0 showed a green
*"Correct pick"* banner on a trade that had *overridden* the pick — technically
true (the model was right) and completely misleading in context.

## The core rule: what the score is and is not

`score` is **0–100, 50 neutral**, a weighted blend of five components each in
−1..+1. It is a **ranking device for these candidates against each other**, not
a probability. The app says so on the recommendation panel, deliberately.

The honest probability is the **hit rate** on the Track record tab: how often the
pick turned out to be the best of the field it was chosen from. With three
candidates, **random picking scores 33%** — that is the bar, not 50%, and not
"did the trade make money". The UI refuses to draw a conclusion below 20 graded
trades and says why.

    composite = Σ (component × weight)      → −1..+1
    score     = (composite + 1) × 50        → 0..100

Weights live in `config.py` (`DEFAULTS["weights"]`) and are validated to sum to
1.0 **at import** — a typo there fails the app rather than silently reweighting
it. A `config.json` in the project root overrides any default.

## Scoring components

| Component | Weight | Signals |
|---|---|---|
| trend | 0.30 | EMA alignment, EMA50 slope in ATRs, +DI vs −DI, **damped by ADX** |
| momentum | 0.25 | RSI (with an overbought penalty), MACD histogram in ATRs, fresh MACD cross (half weight), ROC |
| structure | 0.20 | distance below the 52-week high, Bollinger %B, **extension above EMA20** |
| volume | 0.15 | OBV slope vs average volume, 5-day volume vs 50-day **signed by price direction** |
| risk | 0.10 | ATR%, stop distance — scored so that **low risk is positive** |

The risk component has two signals that deliberately pull against each other,
which an index fund makes obvious: IVV scores a perfect 1.0 on ATR% (0.82%
daily) but its stop lands ~1.6% away, which `stop_distance` scores *negative*
because a stop that tight is hit by noise. Net risk +0.41, not +0.9. Low
volatility is not the same as a good trade, and the scoring says so.

Three rules in there are load-bearing and easy to break by "simplifying":

- **ADX damps, it does not score.** ADX is non-directional. It multiplies the
  other three trend signals by `0.4 + 0.6 × strength`, so a perfect EMA stack in
  a choppy tape cannot earn full marks. Test: `trend component is damped when
  ADX is weak` (0.07 choppy vs 0.97 trending).
- **The anti-chase pair.** RSI above 75 subtracts, and `extension` (close minus
  EMA20, in ATRs) peaks at ~1 ATR and falls away above it. Without these the
  model buys every vertical blow-off. Test: `an overbought blow-off scores below
  a steady trend`.
- **Volume is signed by price.** Heavy volume is conviction on the way up and
  distribution on the way down. Scoring `rel_volume` as bullish on its own would
  reward capitulation.

**Everything with no natural scale is divided by ATR or by average volume.** That
is what lets the same thresholds apply to a 5 EUR stock and a 500 EUR one, and
it is why slopes are reported in ATRs rather than in currency.

## Grading rules that took a bug to find

- **Ties share a rank (1, 1, 3).** `rank_by_return` uses competition ranking.
  Ordering two identical returns would be read as a real distinction.
- **A tie at the top is not a hit.** `pick_was_best` requires rank 1 *and* no
  other candidate at rank 1 — otherwise a flat day inflates the hit rate.
- **A zero-length window is not graded at all.** `sessions_elapsed` counts
  candidates whose entry and exit resolved to different bars. Zero means the
  same close on both ends, every return is 0.0, and any ranking would be
  fabricated. This surfaced immediately: reviewing a same-day recommendation
  reported "pick 1/3" from three identical zeros.
- **Opportunity cost is never negative** — it is the gap to the best candidate,
  and it is 0 when the pick won.
- **`edge_vs_field`** compares the pick against holding all candidates equally.
  That is the no-skill baseline a three-stock hotlist actually has.

## Data notes

- **yfinance must be recent.** 0.2.51 fails against Yahoo's current API with
  `Expecting value: line 1 column 1` (the cookie/crumb handshake changed) and
  returns an empty frame while reporting "possibly delisted". **1.7.0 works.**
  If data stops arriving, check the raw endpoint first —
  `https://query1.finance.yahoo.com/v8/finance/chart/AAPL?range=5d&interval=1d`
  needs no auth, so a 200 there means the library is at fault, not the network.
- **Non-US symbols need their Yahoo suffix**: `TKM1T.TL`, `NOKIA.HE`, `VOW3.DE`.
  A bad symbol is caught when the hotlist commits (on blur, or just before an
  analysis), so it is reported against the slot the user typed it into rather
  than surfacing later as an unscoreable candidate.
- `auto_adjust=True`, so closes are split- and dividend-adjusted. Indicator
  history stays consistent across a split.
- The index is made tz-naive on ingest; everything downstream compares plain
  `YYYY-MM-DD` strings.
- Prices for grading are re-fetched from Yahoo rather than taken from the stored
  snapshot, so **all candidates are measured on identical terms** — the picked
  one included.
- `close_on_or_before` pads the window by a week each side, so a weekend or
  holiday entry/exit still finds a bar.

## Live prices are for P&L only

Asked on 2026-09-03: the app showed 496.82 while Yahoo showed 501.62 pre-market.

**The split, and why.** `data.live_quote()` marks open positions to the latest
traded price. Nothing else uses it. Every indicator, every score, every saved
recommendation and all grading run on **completed daily bars**:

- a half-finished bar makes RSI, MACD and ATR flicker, so the score would
  depend on the minute the button was pressed;
- a saved recommendation must be reproducible, and one built from a mid-session
  price records a session that never existed;
- grading compares all candidates close-to-close, and marking one side live
  breaks the like-for-like comparison the hit rate depends on.

The UI carries a `.mark-note` tag (`pre-market 501.02`, `live 503.10`,
`at close 496.82`) so a P&L figure never silently means "yesterday".

**Candidate cards show both prices.** A `.livebar` under each card head reads
*scored on close 2026-09-02 · 496.82 · pre-market 500.95 · +0.83%*. The scored
close stays the headline because the score, the components and the entry, stop
and target are all derived from it; the live price is the context you need
before acting on those levels -- how far it has already moved since.

Two rules hold this together:

- **The live price is attached only AFTER `save_recommendation`.** The stored
  snapshot must stay reproducible, so it contains nothing but daily-bar values.
  Asserted by reading the snapshot back and confirming no `live` key exists.
- **`Refresh prices` on the pick tab calls `/api/quotes`, not `/api/recommend`.**
  Re-running the analysis would write a second, identical recommendation to the
  log every time someone wanted a fresher quote.

**What Yahoo's `v8/chart?range=1d&interval=1m&includePrePost=true` actually
returns** — measured, because three plausible guesses were wrong:

| Field | Reality |
|---|---|
| `marketState` | **absent.** Do not use it. |
| `regularMarketPrice` | the last *regular* close (496.82). Does not move pre-market, so it is a fallback, not a quote. |
| `previousClose` | the close *before* that (501.02 = 1 Sep). Comparing a pre-market print to it reports the wrong change. |
| `currentTradingPeriod` | **present and correct**, pre/regular/post as epoch seconds. This is what identifies the session, and it works for Tallinn and Helsinki as well as New York. |
| `indicators.quote[0].close` | the only field that moves before the bell. The last non-null minute is the quote. |

So the price comes from the minute series, the label from
`currentTradingPeriod`, and the change percentage is computed by the caller
against the last *daily* close -- never from `previousClose`.

Verified across venues: MSFT/AMZN/META `pre-market`, TKM1T.TL and NOKIA.HE
`live`, at the same instant.

## Storage

SQLite at `data/stocks.db`, three tables: `hotlist`, `recommendations`,
`trades`. A recommendation stores the **complete analysis of every candidate**
as JSON (`snapshot`) — all 15 signals, the plan, the chart series. It is written
once and never updated; that immutability is the point of the log.

Deleting a recommendation leaves its trades intact (`ON DELETE SET NULL`).

## A position is built from packages

A position is rarely one buy. `trade_lots` holds each tranche — date, shares,
price, fee — and the `trades` row keeps the **summary** so nothing downstream
has to know: `qty` is the total, `entry_price` the **weighted average cost**,
`entry_date` the **first** buy.

    10 @ 496.82  +  5 @ 496.93  +  3 @ 501.10 (fee 2.50)
      -> 18 shares, average 497.702778, cost 8 958.65

Four decisions worth keeping:

- **Weighted, never the mean of the prices.** On the example above the plain
  mean is 498.28 against a true 497.70 — small here, and arbitrarily wrong as
  soon as the packages differ in size.
- **Fees are folded into the cost basis.** P&L then comes out net of them with
  no separate subtraction anywhere, which is the kind of term that otherwise
  gets forgotten in one of the three places it is needed.
- **`entry_date` is the first buy, and that is what the grading window starts
  from.** It is when the recommendation was acted on, which is the thing being
  judged. Averaging the dates would compare the candidates over a window that
  never existed.
- **The summary is always re-derived, never edited.** `_recompute` runs after
  every lot change, so adding a package and removing it again restores the
  average *exactly* — asserted in the tests, because a drifting cost basis
  would quietly corrupt every P&L after it.

A trade cannot lose its last package (no cost basis, nothing gradeable); the
error says to delete the trade instead. Deleting a trade cascades to its lots.
Adding to a **closed** position is refused — its cost basis has already been
graded.

`MIGRATE_LOTS` runs on every `init()` and gives any lot-less trade a single lot
from its own summary columns, so there is exactly one code path. It is
idempotent (`NOT IN (SELECT trade_id FROM trade_lots)`) and both properties are
tested.

## Logging a trade from the Trades tab

Added 2026-09-14 after the user traded while away from the laptop and had no
way to record it except by running an analysis and launching from a card.
**+ Log a trade** opens the same package dialog in a third mode (`"free"`):
the ticker is typed, every package takes its real date and price, and the
recommendation to grade against is *chosen* instead of implied. A sale that
also happened while away is recorded afterwards on the position, with its date.

The recommendation picker only offers calls the trade can honestly be graded
against, and the server enforces the same rules rather than trusting the page:

- **The ticker must be one of that call's candidates.** Outside the field there
  is no rank to compute and no decision cost.
- **The call must be scored on or before the first buy.** A recommendation made
  after the trade is hindsight -- grading against it would flatter the model.
  The newest eligible call is the default, since that is the one that was
  current when the decision was made; later ones are hidden with a note saying
  why. "Don't grade — P&L only" is always available.
- **The ticker must exist on Yahoo** (`history(ticker, "1mo")`). A position on a
  typo could never be marked to market or graded, so it is refused on entry
  instead of failing quietly later.

`today()` in the frontend used `toISOString()`, which is UTC -- from midnight
until 03:00 in Tallinn "today" was still yesterday. Harmless as a default date,
wrong as the bound for "a buy can't be in the future", so it now builds the
local date.

## The Trades tab is a list, and one trade is a detail view

Changed 2026-09-14 when four trades as stacked cards already gave no overview.

**List**: one row per trade -- status, ticker, opened, closed, days held,
shares, average cost, exit (or last price for an open trade), invested, P&L,
return, and grading rank. Every column sorts; All/Open/Closed and a ticker
filter narrow it; the footer totals it **per currency**. `tradeRow()` computes
every figure a row shows in one place, so cells, sort keys and totals cannot
disagree. Its totals were checked against the Performance tab and match.

`GET /api/trades?marks=true` adds each trade's currency and live quotes for the
list. It is opt-in because many internal refreshes call `/api/trades` and none
of them should each hit Yahoo; the currency logic is `_currencies_and_quotes`,
shared with `/api/performance`.

**Detail**: the full card, at `#trade/<id>`, so refresh, a direct link and the
browser back button all behave. Switching tab strips the hash so a later reload
lands on the list. Deleting a trade returns to the list.

The detail's buys and sales are **one chronological transactions table** with
a running "held after" column. Separate Bought and Sold tables sized their
columns independently, so shares and prices did not line up, and the running
holding had nowhere to go.

**Amounts are rounded to 6 places before display, as the server rounds its
totals.** 17 x 569.685 + 1 computed in the browser is 9685.644999..., which
showed as 9,685.64 on the row directly under a summary reading 9,685.65 --
one amount, two figures, on the same screen.

## Correcting a trade

Added 2026-09-14: any buy or sale can be corrected in place (✎ on its row, on
open AND closed trades), and a trade's ticker, linked recommendation and note
through **Edit** on its header. `PATCH /api/lots/{id}`, `/api/exits/{id}`,
`/api/trades/{id}`.

**The grade is re-derived after every change, never kept.** `db._changed()`
recomputes the summary and nulls `outcome`; every mutating endpoint then calls
`_grade_if_closed`. Before this, a closed trade's verdict was computed once and
frozen, so correcting a mistyped sale price would have left the old P&L, rank
and decision cost in place. Consequence: adding a forgotten buy to a closed
trade is now allowed (it reopens with the unsold shares) instead of refused.

**An edit is validated as a whole history, atomically.**
`position.first_oversell()` walks the proposed buys and sales in date order and
names the first sale that would sell more than was held -- shrinking a package,
moving a buy past its sale, or deleting a buy whose shares were sold. The check
and the write share one transaction, so a refused edit changes nothing. This
also closed an older gap: `delete_lot` only counted rows, and could leave a
closed trade quietly "sold more than bought".

**Creation and editing share the same rules** (`_check_ticker_exists`,
`_check_recommendation`, `_check_not_future`). Editing a buy's date re-checks
hindsight against the first-buy date the trade *would* have; changing the
ticker re-checks that it is still a candidate of the linked call.

**A dialog must never turn a transient display fallback into a saved choice.**
Typing a wrong ticker hides the recommendations that no longer apply, and the
select falls back to "Don't grade". The first version wrote that fallback back
into the stored choice, so correcting the typo and pressing Save silently
unlinked the trade. The intended choice now lives apart from the select
(`dataset.chosen`, `buy.recChoice`) and only an explicit pick moves it. Caught
by testing typo → fix, not by reading the code.

Verified on a throwaway trade only; the four real trades were fingerprinted
(SHA-256 over every field including stored grades) before and after and matched.

## The Performance tab

Added 2026-09-14: monthly trade counts, monthly realised P&L, and a running
total since the first trade. The maths is `app/performance.py`, pure functions
over trade dicts, served by `GET /api/performance`.

Definitions, each a deliberate choice:

- **P&L is dated by the sale that banked it**, not by the trade. A position
  bought in August and sold in September is September's; one sold in halves
  across two months splits between them. This needed per-sale figures, so the
  cost-basis walk in `position.py` was factored into `_walk()`, which returns
  one dated entry per sale. `economics()` now consumes the same walk, so the
  monthly sums cannot drift from the trade totals. The refactor was checked
  bit-for-bit against the four real trades before anything else was built.
- **Capital traded = the cost of every buy, fees included** (`capital_bought`),
  open positions too. It equals `basis_sold` -- the return denominator -- once
  everything is closed; while something is open the tile also shows how much
  is still in open positions (`capital_open`), so the two figures are never
  confused.
- **Return % = realised P&L / cost basis of the shares sold.** It is return on
  capital actually traded, not an account return -- there is no account size,
  so idle capital is invisible. The page says so.
- **"Opened" counts the first buy's month, "closed" the last sale's month.**
  A trade spanning a month end appears once in each column.
- **Win/loss is judged on the whole trade**, in its closing month. A trade whose
  first sale lost but which closed up overall is a win.
- **Empty months are listed**, from the first trade's month to today's. A month
  with no trades is information.
- **Currencies are never summed.** Everything is grouped by the ticker's
  currency (from the stored hotlist, else from Yahoo); an undeterminable one is
  reported under "?" rather than guessed.
- **Unrealised is shown only when every open position could be priced**, since
  a partial sum would understate exposure without saying so.

The running-total chart uses a **real time axis** with a step at each sale, so a
fortnight without sales looks like a fortnight. Bars round only the end away
from zero; the paired opened/closed bars sit 2px apart rather than bordered.

## Selling in parts

`trade_exits` mirrors `trade_lots`, and `position.economics` walks both in date
order to produce every number the app reports about a trade. One function, one
definition, so no two screens can disagree.

**Moving average cost.** Each sale relieves shares at the average cost of what
is held *at that moment*, and that cost leaves the basis. Using the final
average of all buys would be wrong the moment shares are bought back after a
partial sell — which is what scaling out of a winner and adding again looks
like. Once a position is fully closed any consistent basis gives the same total,
so the difference is only visible while it is open; the test asserts it on
`open_avg_cost` (166.67, not 150) for exactly that reason.

**Buys are applied before sells on the same date.** Neither record carries a
time of day, and you cannot sell shares you do not yet hold.

**Status is derived, never set.** `_recompute` writes `closed` when nothing is
left and `open` otherwise, so there is no second place that can decide a
position is finished. Grading fires from `_grade_if_closed`, only on the
transition to zero — a half-sold position has no final holding period, and
grading it would fix a verdict against a window still running.

**Undoing a sale reopens the position and clears its grade**, because the
verdict belonged to a holding period that no longer ends there.

**Realised and unrealised are reported separately** on a part-sold position.
Blending them into one percentage hides which money is banked and which is
still at risk.

`MIGRATE_EXITS` gives any position closed before this existed a single exit from
its own summary columns, so there is one code path. Idempotent and tested, like
`MIGRATE_LOTS`.

**Not FIFO.** Estonian capital-gains tax is FIFO; these figures are for judging
the trade, not for a tax return.

## The hotlist is what is in the boxes

v1.0 stored a slot only when **Set** was clicked or Enter pressed. Typing three
new tickers and pressing **Analyse & recommend** therefore analysed the *old*
hotlist, and the slot labels underneath still named the old companies — the
screen said AMZN and the app said Apple. Reported by the user, and the screenshot
showed the divergence plainly.

Three changes, and the first is the rule:

- **The input is the source of truth.** `commitHotlist()` runs on blur (`change`)
  and again at the top of the analyse handler, so what is on screen is always
  what gets scored. A failed commit aborts the analysis rather than silently
  scoring something else.
- **A divergence is visible while it exists.** A slot whose box differs from
  storage gets an amber border and reads *"not checked yet — applied when you
  analyse"*. The **Set** button is gone; it only ever existed to paper over this.
- **The whole list is written at once** (`PUT /api/hotlist`,
  `db.replace_hotlist`). Per-slot writes could not express a **swap**: setting
  slot 1 to what is in slot 2 trips the duplicate check on an intermediate state
  the user never asked for. Bulk also makes it atomic — every symbol is resolved
  before anything is written, so a typo in slot 3 no longer leaves slots 1 and 2
  changed.

Changing the hotlist hides any recommendation on screen, because it was scored
over a different set of candidates.

**Lesson**: a control that exists to confirm what the user already typed is a
control that will be forgotten, and its absence has to be silent-safe. The fix
was to delete the button, not to make it more prominent.

## The comparison chart plots SCORES, not price

One multi-series line chart above the cards: each candidate's **score at every
one of the last `compare_sessions` closes** (20), 0-100 with 50 drawn as the
neutral line.

`compare_sessions` lives in `config.py` and the frontend reads it from
`/api/config` at startup -- it is never hardcoded in the JS, because a
mismatch would put "last 10 closes" in the copy above 20 lines of data. The
x-axis label stride adapts to the window (about six labels at any length).

**It used to plot price change, and that was wrong.** The user caught it: the
chart crowned META at +11.9% while the model ranked META fifth. Worse than
inconsistent -- actively misleading, because the scoring *deliberately
penalises* what has already run (the RSI-above-75 penalty and the extension
rule), so the top line of a price chart is often the one the model is warning
against. A chart whose visual hierarchy contradicts the recommendation teaches
the wrong thing.

Plotting the score fixes it by construction: **the top line is the top card,
because they are the same number**. And it answers a question the cards cannot
-- whether a setup is building or decaying. AAPL at 71 having climbed from 49
is a different proposition from AMZN at 49 having fallen from 55.

`scoring.score_history()` recomputes the score on progressively truncated
frames. Slicing the finished indicator series instead would leak future data
into the past -- an EMA at bar N-5 is not the same number once bars N-4..N
exist. About 9 ms per session per ticker: at 20 closes that is ~1.3 s of CPU for
seven candidates, ~2 s of the ~4.4 s cold analysis. This is the knob that
decides how long an analysis takes -- raise it further and the wait is felt.
It is deterministic and daily-bar-only, so it goes into the saved snapshot.

The price move survives as a **table column**, where it informs without
claiming to be the ranking.

**Your own trades are drawn onto it.** `tradeMarks()` shades the holding period
and puts a triangle at every buy and sell -- positioned at the **score on that
date, not at a price**. That is the whole value: it shows what the model thought
at the moment you acted. Buying at 73 on the way up and buying at 73 on the way
down look identical on a price chart and completely different here.

Hovering a marker beats the crosshair, because a triangle is a specific thing
you did and the whole field at that date is not what the hover is asking for.
The hit circles are 26px so there is no pinpoint target. The tooltip carries the
event (shares, price, the score that day) and the position's current standing --
realised P&L and the grading verdict when closed, shares held and unrealised
against the live price when open. Open positions are marked from the quote
already on the card, so the overlay costs no extra request.

Rules that are not cosmetic:

- **Colour follows the ticker, never the rank.** `seriesColours()` assigns from
  hotlist slot order, so re-running the analysis never repaints a line. Colour
  by rank would mislead anyone who had learned "MSFT is blue".
- **The palette is validated, not eyeballed.** Seven categorical slots from the
  dataviz reference palette, re-run through `validate_palette.js` against *this
  app's* surfaces (`#ffffff` / `#1c1f25`): lightness band, chroma floor, CVD
  separation and normal-vision separation pass in both modes. In light mode
  aqua, yellow and magenta fall below 3:1 on white, which obliges the relief
  rule -- hence the **table underneath is required**, not decoration.
- **Eight slots, never cycled, and eight is the ceiling.** `hotlist_size` is 8
  and there are exactly 8 validated hues, so the modulo in `seriesColours()` is
  a guard rather than a cycle. A 9th candidate must fold into an "Other"
  grouping or facet into small multiples -- a generated 9th hue stops being
  distinguishable under colour-vision deficiency, which is the whole reason the
  palette is a fixed ordered set. Raising `hotlist_size` past 8 therefore needs
  a chart change, not just a config change.
- **End labels are de-collided.** Three candidates finishing within half a
  percent overprinted each other into mush. `endLabels()` pushes them apart to a
  minimum gap and draws a leader line where the nudge shows; the dot stays on
  the true value.
- **The y axis is a fixed 10-point grid, not a "nice step" heuristic.** The
  heuristic put a 50.2-point span into the wrong magnitude bucket and produced
  20-point steps that skipped straight over 50 -- while the panel copy claimed
  "50 is neutral". The axis is always a 0-100 score, so tens are simply right.

## Frontend

One page, four tabs, vanilla JS. Charts are hand-drawn inline SVG — no chart
library, nothing loaded from a CDN, so it works offline.

Two traps already hit:

- **CSS specificity.** `button:hover:not(:disabled)` scores 0,2,1 and beats
  `button.primary` at 0,1,1, which repainted primary buttons in the neutral
  surface colour while leaving their white text — invisible on hover. Fixed with
  an explicit `button.primary:hover:not(:disabled)` at 0,3,1. Any new
  `button.<variant>` needs the same treatment.
- **Double-escaping entities.** `esc()` escapes `&`, so passing a string that
  already contains `&middot;` through it renders the entity literally. Escape
  the parts, then join with the entity.

## Build / run

    setup.bat      one-time: venv + dependencies
    run.bat        starts uvicorn on 127.0.0.1:8000 and opens a browser
    .venv\Scripts\python.exe tests\test_core.py     55 offline checks

No compiler, no npm, no Visual Studio. Editing `static/*` needs only a browser
reload; editing `app/*` needs a server restart (uvicorn runs without `--reload`).

## Testing without the network

`tests/test_core.py` builds synthetic OHLCV frames with a known shape
(`trending`, `choppy`) and asserts **properties rather than magic numbers** —
"an uptrend outscores a downtrend", "a blow-off scores below a steady trend",
"opportunity cost is never negative". Grading tests stub
`review.candidate_returns` so the settlement logic runs with no Yahoo call.

**A fixture bug once masqueraded as three indicator failures**: `steps = daily +
(rng.normal(...) if noise else 0.0)` collapsed to a scalar when `noise=0`, so
`np.cumprod` returned a one-element array and every "trending" series was one
bar long. ADX read 0, Bollinger bands collapsed, `analyse` returned `ok=False`.
When several unrelated indicator tests fail at once, suspect the fixture.

## The daily report runs in the cloud, not on this PC

`tools/daily_report.py` scores the watchlist against the latest completed close
and prints a markdown table. A scheduled cloud agent (routine
`trig_01KXBv71RCL4t3SpV3SZnnhF`, weekdays 04:00 UTC = 07:00 Tallinn) clones the
GitHub repo, pip-installs pandas/numpy/yfinance, runs the script and relays its
stdout. No laptop involved -- which was the whole point.

Three separations make that possible:

- **It reads `watchlist.json`, not the SQLite hotlist.** The hotlist is local
  state that never leaves the machine. The watchlist is a committed file, so it
  can be edited from a phone through GitHub's web editor and the next run picks
  it up. The two are deliberately allowed to diverge.
- **It touches no database and writes nothing.** It cannot corrupt a position
  history it cannot see, and it is safe to run repeatedly.
- **The logic is in the repo, not in the cron prompt.** The prompt only installs,
  runs and relays. Anything worth versioning is in the script.

The routine's prompt forbids committing and forbids inventing numbers: every
figure must come from the script's stdout. An LLM relaying share prices from
memory is the failure mode to design against.

**Exit 2 is the interesting failure.** It means nothing could be scored, which
almost always means Yahoo refused the host -- cloud agents run on datacenter
IPs, the same unverified risk that blocks the Supabase plan below. The script
says so explicitly rather than printing an empty table.

## Parked: running it away from this PC

Considered on 2026-09-03 and deliberately deferred. Recorded so it is not
re-litigated from scratch.

**Not an Artifact.** Artifacts run under a CSP that blocks fetch/XHR to every
host, so Yahoo is unreachable. No capability fixes it: `mcp` reaches only the
viewer's connected connectors, and using `sample` (ask Claude) to produce share
prices is out of the question. No prices means no scoring, no mark-to-market and
no grading, which is the whole app.

**The intended target is the pm.reaalprojekt.ee stack**, on personal accounts
rather than Reaalprojekt OÜ:

    React + Vite -> Cloudflare Pages   (GitHub Actions on push to main)
    Supabase Postgres + RLS + Auth     (login that persists on a phone)
    Supabase Edge Function             (Yahoo fetch + scoring, the vies-lookup pattern)

That is a **rewrite, not a config change**: ~950 lines of indicator maths,
scoring, cost-basis and grading move from Python to TypeScript, the SQLite
schema becomes migrations, and the vanilla frontend becomes React. The 86 tests
must be ported with it -- that maths is the product.

**Unverified risk, test it before writing any of the above.** Yahoo answers
fine from a home connection but is known to refuse some datacenter ranges, and
Edge Functions run on Deno Deploy. `supabase/functions/yahoo-probe/index.ts` is
written and ready for exactly this: it checks both Yahoo hosts, with and without
a browser User-Agent, a Tallinn and a Helsinki symbol, and a 10-call burst, and
reports the egress IP. Deploy it, call it once, delete it. If Yahoo blocks Deno
Deploy the architecture needs a different data source and nothing else matters.

The probe is the only artefact of this in the repo; it is not wired into the
running app.

## Open questions

- Position sizing: the plan gives entry/stop/target but no share count. Worth
  adding a risk budget (e.g. 1% of account per trade) to turn `risk_pct` into a
  quantity?
- Weight tuning: once ~30 trades are graded, the stored snapshots contain enough
  to fit the weights against realised rank. That is the natural next feature and
  the reason every signal value is stored, not just the total.
- Alerts: nothing watches for a stop being hit. Currently the app is asked, it
  does not tell.
