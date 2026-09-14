"""Offline checks for the indicator maths, the scoring rules and the grading.

Run with:  .venv\\Scripts\\python.exe tests\\test_core.py

No network and no pytest. Everything here is built from synthetic series with a
known shape, so a failure points at the code rather than at the market.
"""
import math
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import review                                          # noqa: E402
from app.config import CFG                                      # noqa: E402
from app.indicators import adx, atr, bollinger, ema, rsi        # noqa: E402
from app.scoring import analyse                                 # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (("  -- " + detail) if detail else ""))


def frame(closes, spread=0.01, volume=1_000_000):
    """Build an OHLCV frame from a close series with a plausible daily range."""
    closes = np.asarray(closes, dtype=float)
    idx = pd.bdate_range("2024-01-01", periods=len(closes))
    high = closes * (1 + spread)
    low = closes * (1 - spread)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    vol = np.full(len(closes), float(volume))
    return pd.DataFrame({"Open": opens, "High": high, "Low": low,
                         "Close": closes, "Volume": vol}, index=idx)


def trending(n=400, start=100.0, daily=0.004, noise=0.0, seed=1):
    rng = np.random.default_rng(seed)
    steps = np.full(n, float(daily))
    if noise:
        steps = steps + rng.normal(0, noise, n)
    return start * np.cumprod(1.0 + steps)


def choppy(n=400, start=100.0, amp=0.06, period=18, seed=2):
    """Sideways: a sine wave with no drift -- EMAs cross constantly, ADX stays low."""
    x = np.arange(n)
    return start * (1.0 + amp * np.sin(2 * np.pi * x / period))


# ------------------------------------------------------------- indicators

print("\nINDICATORS")

s = pd.Series([1.0] * 30)
check("EMA of a constant series is that constant", abs(ema(s, 10).iloc[-1] - 1.0) < 1e-12)

up = pd.Series(np.arange(1, 60, dtype=float))
check("RSI of a monotonic rise is 100", abs(rsi(up).iloc[-1] - 100.0) < 1e-9)
check("RSI of a monotonic fall is 0",
      abs(rsi(pd.Series(np.arange(60, 1, -1, dtype=float))).iloc[-1]) < 1e-9)
check("RSI of a flat series is 50 (not 100)",
      abs(rsi(pd.Series([10.0] * 60)).iloc[-1] - 50.0) < 1e-9)

# A 2% band around each close makes the true range 4% of price once the gap
# from the previous close is folded in; Wilder's ATR settles just above that.
df = frame(np.full(200, 100.0), spread=0.02)
a = atr(df, 14).iloc[-1]
check("ATR of a fixed 2%% band settles at ~4.0 (got %.3f)" % a, 3.9 < a < 4.1)

adx_v, pdi, mdi = adx(frame(trending(300)), 14)
check("ADX is high in a clean uptrend (%.1f > 40)" % adx_v.iloc[-1], adx_v.iloc[-1] > 40)
check("+DI exceeds -DI in an uptrend", pdi.iloc[-1] > mdi.iloc[-1])

adx_c, _, _ = adx(frame(choppy(300)), 14)
check("ADX is low in a sideways market (%.1f < 30)" % adx_c.iloc[-1], adx_c.iloc[-1] < 30)

mid, upper, lower, pct_b, width = bollinger(pd.Series(trending(120)), 20, 2.0)
check("Bollinger bands bracket the mid line",
      lower.iloc[-1] < mid.iloc[-1] < upper.iloc[-1])
check("%B is >0.5 while price rides the upper half", pct_b.iloc[-1] > 0.5)


# ---------------------------------------------------------------- scoring

print("\nSCORING")

r_up = analyse("UP", frame(trending(400, daily=0.004, noise=0.004)), CFG)
r_down = analyse("DOWN", frame(trending(400, daily=-0.004, noise=0.004)), CFG)
r_flat = analyse("FLAT", frame(choppy(400)), CFG)

check("a clean uptrend scores above 50 (%.1f)" % r_up["score"], r_up["score"] > 50)
check("a downtrend scores below 50 (%.1f)" % r_down["score"], r_down["score"] < 50)
check("uptrend outscores downtrend", r_up["score"] > r_down["score"])
check("a sideways market lands near neutral (%.1f in 35-65)" % r_flat["score"],
      35 <= r_flat["score"] <= 65)
check("every score stays inside 0..100",
      all(0 <= r["score"] <= 100 for r in (r_up, r_down, r_flat)))
check("every component stays inside -1..+1",
      all(-1.0001 <= v <= 1.0001 for r in (r_up, r_down, r_flat)
          for v in r["components"].values()))

# ADX damping: identical EMA order, but one tape is directionless.
check("trend component is damped when ADX is weak (%.2f vs %.2f)"
      % (r_flat["components"]["trend"], r_up["components"]["trend"]),
      abs(r_flat["components"]["trend"]) < abs(r_up["components"]["trend"]))

# The anti-chase rule: a vertical melt-up must score worse than a steady climb.
steady = analyse("STEADY", frame(trending(400, daily=0.003)), CFG)
blowoff_closes = np.concatenate([trending(370, daily=0.003),
                                 trending(30, start=trending(370, daily=0.003)[-1],
                                          daily=0.035)])
blowoff = analyse("BLOWOFF", frame(blowoff_closes), CFG)
check("an overbought blow-off scores below a steady trend (%.1f < %.1f)"
      % (blowoff["score"], steady["score"]), blowoff["score"] < steady["score"])
rsi_sig = next(s for s in blowoff["signals"] if s["key"] == "rsi")
check("the blow-off's RSI signal is penalised (score %.2f <= 0)" % rsi_sig["score"],
      rsi_sig["score"] <= 0.0, "RSI %.1f" % rsi_sig["value"])
ext_sig = next(s for s in blowoff["signals"] if s["key"] == "extension")
check("the blow-off is flagged as extended above EMA20 (%.2f ATR)" % ext_sig["value"],
      ext_sig["score"] < 0.5)

# The trade plan has to be internally consistent.
p = r_up["plan"]
check("stop sits below entry", p["stop"] < p["entry"])
check("target sits above entry", p["target"] > p["entry"])
check("target is exactly %sR from the stop" % p["reward_risk"],
      abs((p["target"] - p["entry"]) - p["reward_risk"] * (p["entry"] - p["stop"])) < 1e-6)

check("too little history is refused rather than guessed",
      analyse("SHORT", frame(trending(20)), CFG)["ok"] is False)
check("weights sum to exactly 1.0", abs(sum(CFG["weights"].values()) - 1.0) < 1e-9)
check("the composite equals the weighted sum of its components",
      abs(r_up["score"] - (sum(r_up["components"][k] * CFG["weights"][k]
                               for k in CFG["weights"]) + 1) * 50) < 0.01)


# --------------------------------------------------------------- grading

print("\nGRADING")

rows = [{"ticker": "A", "return_pct": 5.0, "entry_bar": "2026-01-02", "exit_bar": "2026-02-02"},
        {"ticker": "B", "return_pct": 5.0, "entry_bar": "2026-01-02", "exit_bar": "2026-02-02"},
        {"ticker": "C", "return_pct": 1.0, "entry_bar": "2026-01-02", "exit_bar": "2026-02-02"},
        {"ticker": "D", "return_pct": None, "error": "no data"}]
ranked = review.rank_by_return(rows)
check("ties share a rank and the next rank skips (1,1,3)",
      [r["actual_rank"] for r in ranked] == [1, 1, 3],
      str([(r["ticker"], r["actual_rank"]) for r in ranked]))
check("an unpriced candidate is left out of the ranking",
      all(r["ticker"] != "D" for r in ranked))
check("sessions_elapsed counts bars that actually moved",
      review.sessions_elapsed(ranked) == 3)
check("sessions_elapsed is 0 when entry and exit share a bar",
      review.sessions_elapsed([{"entry_bar": "2026-01-02", "exit_bar": "2026-01-02"}]) == 0)


def fake_rec(pick, tickers, scores):
    return {"pick": pick, "pick_score": scores[pick], "tickers": tickers,
            "snapshot": [{"ticker": t, "ok": True, "score": scores[t]} for t in tickers]}


def settle_with(returns, pick, entry="2026-01-02", exit_="2026-02-02"):
    """Drive review.settle with candidate returns supplied directly."""
    real = review.candidate_returns

    def stub(tickers, a, b):
        return [{"ticker": t, "entry_price": 100.0,
                 "exit_price": 100.0 * (1 + returns[t] / 100.0),
                 "entry_bar": entry, "exit_bar": exit_,
                 "return_pct": returns[t]} for t in tickers]
    review.candidate_returns = stub
    try:
        rec = fake_rec(pick, list(returns), dict((t, 70.0) for t in returns))
        entry_px = 100.0
        exit_px = 100.0 * (1 + returns[pick] / 100.0)
        return review.settle(rec, pick, entry, entry_px, exit_, exit_px, qty=10)
    finally:
        review.candidate_returns = real


o = settle_with({"A": 12.0, "B": 3.0, "C": -4.0}, "A")
check("the best pick is graded correct", o["pick_was_best"] and o["pick_rank"] == 1)
check("opportunity cost is zero when the pick won", o["opportunity_cost_pct"] == 0.0)
check("edge vs field is positive for the winner (%.2f)" % o["edge_vs_field_pct"],
      o["edge_vs_field_pct"] > 0)
check("P&L follows quantity", abs(o["pnl"] - 10 * 12.0) < 1e-6)

o = settle_with({"A": -4.0, "B": 3.0, "C": 12.0}, "A")
check("the worst pick is graded wrong", not o["pick_was_best"] and o["pick_rank"] == 3)
check("opportunity cost is the gap to the best (%.1f)" % o["opportunity_cost_pct"],
      abs(o["opportunity_cost_pct"] - 16.0) < 1e-6)
check("opportunity cost is never negative", o["opportunity_cost_pct"] >= 0)
check("a losing pick still reports its own P&L", o["traded_return_pct"] == -4.0)

o = settle_with({"A": 5.0, "B": 5.0, "C": 1.0}, "A")
check("a tie at the top is NOT counted as a win", o["pick_rank"] == 1
      and not o["pick_was_best"], o["verdict"])

o = settle_with({"A": 0.0, "B": 0.0, "C": 0.0}, "A",
                entry="2026-01-02", exit_="2026-01-02")
check("an empty window is not graded at all", o["graded"] is False, o["verdict"])

o = settle_with({"A": -8.0, "B": -12.0, "C": -15.0}, "A")
check("a correct pick in a falling market is still correct",
      o["pick_was_best"] and o["traded_return_pct"] < 0,
      "return %.1f%%, rank %d" % (o["traded_return_pct"], o["pick_rank"]))


def settle_override(returns, pick, bought):
    """Settle a trade on a ticker other than the model's pick."""
    real = review.candidate_returns

    def stub(tickers, a, b):
        return [{"ticker": t, "entry_price": 100.0,
                 "exit_price": 100.0 * (1 + returns[t] / 100.0),
                 "entry_bar": "2026-01-02", "exit_bar": "2026-02-02",
                 "return_pct": returns[t]} for t in tickers]
    review.candidate_returns = stub
    try:
        rec = fake_rec(pick, list(returns), dict((t, 70.0) for t in returns))
        return review.settle(rec, bought, "2026-01-02", 100.0, "2026-02-02",
                             100.0 * (1 + returns[bought] / 100.0), qty=10)
    finally:
        review.candidate_returns = real


o = settle_override({"A": 12.0, "B": 3.0, "C": -4.0}, pick="A", bought="B")
check("an override is flagged as not following the pick", o["followed_pick"] is False)
check("the traded ticker gets its own rank (%s)" % o["traded_rank"], o["traded_rank"] == 2)
check("the model is still graded correct on its own pick", o["pick_was_best"])
check("overriding a correct pick is costed (%.1f points)" % o["decision_cost_pct"],
      abs(o["decision_cost_pct"] - 9.0) < 1e-6, o["decision_verdict"])

o = settle_override({"A": -4.0, "B": 12.0, "C": 3.0}, pick="A", bought="B")
check("overriding a WRONG pick shows as a gain, not a cost",
      o["decision_cost_pct"] < 0 and "gained" in o["decision_verdict"],
      o["decision_verdict"])
check("the model is still graded wrong even though the trade won",
      not o["pick_was_best"] and o["traded_return_pct"] > 0)

o = settle_with({"A": 12.0, "B": 3.0, "C": -4.0}, "A")
check("following the pick records no decision verdict",
      o["followed_pick"] and "decision_verdict" not in o)


# ------------------------------------------------------------ buy packages

print("\nBUY PACKAGES")

import tempfile                                                 # noqa: E402
from app import db                                              # noqa: E402

db.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="stocks_test_"), "t.db")
db.init()


def lot(date, qty, price, fee=0.0):
    return {"lot_date": date, "qty": qty, "price": price, "fee": fee}


tid = db.open_trade(None, "MSFT", [lot("2026-09-01", 10, 496.82),
                                   lot("2026-09-01", 5, 496.93),
                                   lot("2026-09-02", 3, 501.10)])
t = db.get_trade(tid)
expect = (10 * 496.82 + 5 * 496.93 + 3 * 501.10) / 18.0
check("three packages sum to one quantity", t["qty"] == 18.0)
check("cost basis is the weighted average, not the mean of the prices (%.6f)"
      % t["entry_price"], abs(t["entry_price"] - expect) < 5e-7,
      "plain mean would be %.4f" % ((496.82 + 496.93 + 501.10) / 3))
check("the entry date is the FIRST buy", t["entry_date"] == "2026-09-01")
check("every package is kept, not just the summary", len(t["lots"]) == 3)

tid2 = db.open_trade(None, "AAPL", [lot("2026-09-01", 10, 100.0, fee=25.0)])
check("a fee is folded into the cost basis (%.3f)" % db.get_trade(tid2)["entry_price"],
      abs(db.get_trade(tid2)["entry_price"] - 102.5) < 1e-9)

t = db.add_lot(tid, "2026-09-03", 2, 499.00)
check("buying more recomputes the average", t["qty"] == 20.0 and len(t["lots"]) == 4)
back = db.delete_lot(t["lots"][-1]["id"])
check("removing that package restores the previous average exactly",
      back["qty"] == 18.0 and abs(back["entry_price"] - expect) < 5e-7)

try:
    db.delete_lot(db.get_trade(tid2)["lots"][0]["id"])
    check("a position refuses to lose its only package", False)
except ValueError as exc:
    check("a position refuses to lose its only package", True, str(exc))

# A trade written before packages existed must still work.
with db.connect() as c:
    c.execute("INSERT INTO trades (ticker, qty, entry_date, entry_price, status)"
              " VALUES ('LEGACY', 7, '2026-01-05', 50.0, 'open')")
db.init()
legacy = [x for x in db.list_trades() if x["ticker"] == "LEGACY"][0]
check("a pre-packages trade is migrated to a single package",
      len(legacy["lots"]) == 1 and legacy["lots"][0]["qty"] == 7
      and legacy["entry_price"] == 50.0)
before = len(db.get_trade(tid)["lots"])
db.init()
check("re-running the migration does not duplicate packages",
      len(db.get_trade(tid)["lots"]) == before)

db.delete_trade(tid)
with db.connect() as c:
    left = c.execute("SELECT COUNT(*) AS n FROM trade_lots WHERE trade_id = ?",
                     (tid,)).fetchone()["n"]
check("deleting a trade takes its packages with it", left == 0)


# -------------------------------------------------------------- partial sells

print("\nPARTIAL SELLS")

from app import position                                        # noqa: E402

tid = db.open_trade(None, "MSFT", [lot("2026-09-02", 18, 496.96, fee=1.0)])
avg = (18 * 496.96 + 1.0) / 18.0

t = db.add_exit(tid, "2026-09-03", 8, 505.00, 1.00)
e = t["econ"]
check("selling part leaves the position open", t["status"] == "open")
check("shares held drops to the remainder", e["qty_open"] == 10.0 and e["qty_sold"] == 8.0)
check("realised P&L is on the sold shares only (%.4f)" % e["realised_pnl"],
      abs(e["realised_pnl"] - (8 * (505.0 - avg) - 1.0)) < 1e-4)
check("the remaining shares keep the same average cost",
      abs(e["open_avg_cost"] - avg) < 1e-6)
check("a part-sold position is flagged as such",
      e["partially_sold"] and not e["fully_closed"])

t = db.add_exit(tid, "2026-09-04", 10, 510.00, 1.00)
e = t["econ"]
check("selling the remainder closes the position",
      t["status"] == "closed" and e["qty_open"] == 0.0 and e["fully_closed"])
check("total realised equals proceeds minus everything paid (%.4f)" % e["realised_pnl"],
      abs(e["realised_pnl"] - (e["proceeds"] - e["total_buy_cost"])) < 1e-6)
check("the summary exit price is the weighted average, net of selling fees",
      abs(t["exit_price"] - ((8 * 505 - 1 + 10 * 510 - 1) / 18.0)) < 1e-6)
check("the summary exit date is the LAST sale", t["exit_date"] == "2026-09-04")

back = db.delete_exit(t["exits"][-1]["id"])
check("undoing a sale reopens the position",
      back["status"] == "open" and back["econ"]["qty_open"] == 10.0)
check("undoing keeps the earlier sale's realised P&L",
      abs(back["econ"]["realised_pnl"] - (8 * (505.0 - avg) - 1.0)) < 1e-4)

# Moving-average cost. Once a position is fully closed any consistent basis
# gives the same total, so the difference only shows while it is still open.
mv = position.economics(
    [lot("2026-01-01", 10, 100.0), lot("2026-01-03", 10, 200.0)],
    [{"exit_date": "2026-01-02", "qty": 5, "price": 120.0, "fee": 0.0, "id": 1}])
check("cost basis moves with each sale, it is not the average of all buys "
      "(%.4f, not 150)" % mv["open_avg_cost"],
      abs(mv["open_avg_cost"] - 500.0 / 3) < 1e-6)
check("the interim realised figure uses the basis at the time of sale",
      abs(mv["realised_pnl"] - 100.0) < 1e-9)
check("buys are applied before sells on the same date",
      position.economics(
          [lot("2026-01-01", 10, 100.0)],
          [{"exit_date": "2026-01-01", "qty": 10, "price": 110.0, "fee": 0.0,
            "id": 1}])["qty_sold"] == 10.0)

open_t = db.get_trade(tid)
check("selling more than is held is refused",
      position.can_sell(open_t["econ"], 999) is not None,
      position.can_sell(open_t["econ"], 999))
check("selling exactly what is held is allowed",
      position.can_sell(open_t["econ"], open_t["econ"]["qty_open"]) is None)
check("selling zero or a negative amount is refused",
      position.can_sell(open_t["econ"], 0) is not None
      and position.can_sell(open_t["econ"], -3) is not None)

# A position closed before partial sells existed must migrate to one exit.
with db.connect() as c:
    c.execute("INSERT INTO trades (ticker, qty, entry_date, entry_price,"
              " exit_date, exit_price, status)"
              " VALUES ('OLDCLOSED', 4, '2026-02-01', 10.0, '2026-03-01', 12.0, 'closed')")
db.init()
oc = [x for x in db.list_trades() if x["ticker"] == "OLDCLOSED"][0]
check("a pre-partial-sell closed trade migrates to one sale",
      len(oc["exits"]) == 1 and oc["exits"][0]["qty"] == 4
      and oc["exits"][0]["price"] == 12.0)
check("its realised P&L survives the migration (%.2f)" % oc["econ"]["realised_pnl"],
      abs(oc["econ"]["realised_pnl"] - 8.0) < 1e-9 and oc["status"] == "closed")
n_before = len(db.get_trade(oc["id"])["exits"])
db.init()
check("re-running the exit migration does not duplicate sales",
      len(db.get_trade(oc["id"])["exits"]) == n_before)


# -------------------------------------------------------------- editing

print("\nEDITING")

from app import main as app_main                                # noqa: E402

eid = db.open_trade(None, "EDT", [lot("2026-03-02", 10, 50.0, fee=1.0)])
db.add_exit(eid, "2026-03-09", 10, 55.0, 1.0)
closed_t = app_main._grade_if_closed(eid)
check("a closed, unlinked trade gets a P&L-only grade (%.2f)" % closed_t["outcome"]["pnl"],
      closed_t["status"] == "closed" and abs(closed_t["outcome"]["pnl"] - 48.0) < 1e-6)

# Correct a typo in the sale price: 55 was really 56.
exit_id = closed_t["exits"][0]["id"]
fixed = db.update_entry("trade_exits", exit_id, {"price": 56.0})
check("editing a sale re-derives the summary", abs(fixed["exit_price"] - (10 * 56 - 1) / 10) < 1e-9)
check("and drops the stale grade rather than keeping the typo's result",
      fixed["outcome"] is None)
regraded = app_main._grade_if_closed(eid)
check("re-grading after the edit reflects the corrected price (%.2f)" % regraded["outcome"]["pnl"],
      abs(regraded["outcome"]["pnl"] - 58.0) < 1e-6)

lot_id = regraded["lots"][0]["id"]
db.update_entry("trade_lots", lot_id, {"qty": 12})
reopened = app_main._grade_if_closed(eid)
check("raising a buy's quantity above what was sold reopens the position",
      reopened["status"] == "open" and reopened["econ"]["qty_open"] == 2.0
      and reopened["outcome"] is None)

before = db.get_trade(eid)
try:
    db.update_entry("trade_lots", lot_id, {"qty": 4})
    check("shrinking a buy below what was later sold is refused", False)
except ValueError as exc:
    check("shrinking a buy below what was later sold is refused", True, str(exc))
check("and a refused edit changes nothing",
      db.get_trade(eid)["lots"][0]["qty"] == before["lots"][0]["qty"]
      and db.get_trade(eid)["qty"] == before["qty"])

try:
    db.update_entry("trade_lots", lot_id, {"lot_date": "2026-03-20"})
    check("moving a buy to after its own sale is refused", False)
except ValueError as exc:
    check("moving a buy to after its own sale is refused", True, str(exc))

try:
    db.update_entry("trade_exits", exit_id, {"exit_date": "2026-03-01"})
    check("moving a sale to before the buy is refused", False)
except ValueError as exc:
    check("moving a sale to before the buy is refused", True)

check("fields outside the editable set are ignored",
      db.update_entry("trade_lots", lot_id, {"trade_id": 999999, "qty": 12})["id"] == eid)

twolot = db.open_trade(None, "TWO", [lot("2026-04-01", 5, 10.0), lot("2026-04-02", 5, 11.0)])
db.add_exit(twolot, "2026-04-10", 8, 12.0)
first_lot = db.get_trade(twolot)["lots"][0]["id"]
try:
    db.delete_lot(first_lot)
    check("deleting a buy whose shares were later sold is refused", False)
except ValueError as exc:
    check("deleting a buy whose shares were later sold is refused", True, str(exc))

renamed = db.update_trade(eid, {"ticker": "EDX", "note": "fixed ticker"})
check("a trade's ticker and note can be corrected",
      renamed["ticker"] == "EDX" and renamed["note"] == "fixed ticker")
check("position.first_oversell names the offending sale",
      position.first_oversell(
          [lot("2026-01-01", 3, 1.0)],
          [{"exit_date": "2026-01-02", "qty": 5, "price": 1.0, "fee": 0.0, "id": 1}])
      == ("2026-01-02", 5, 3.0))


# ------------------------------------------------------------ performance

print("\nPERFORMANCE")

from app import performance                                     # noqa: E402


def sale(date, qty, price, fee=0.0, i=1):
    return {"exit_date": date, "qty": qty, "price": price, "fee": fee, "id": i}


# Per-sale realisations come from the same walk as the totals, so they must add
# up exactly -- including across a partial sell and a buy-back.
walk_lots = [lot("2026-01-05", 10, 100.0, fee=2.0), lot("2026-02-10", 10, 200.0)]
walk_exits = [sale("2026-01-20", 4, 120.0, fee=1.0, i=1),
              sale("2026-03-03", 16, 180.0, fee=1.5, i=2)]
per_sale = position.realisations(walk_lots, walk_exits)
walk_econ = position.economics(walk_lots, walk_exits)
check("per-sale P&L sums to the trade's realised total",
      abs(round(sum(s["pnl"] for s in per_sale), 4) - walk_econ["realised_pnl"]) < 1e-9)
check("each sale carries the date it was banked",
      [s["date"] for s in per_sale] == ["2026-01-20", "2026-03-03"])

split = performance.build(
    [{"id": 1, "ticker": "AAA", "lots": walk_lots, "exits": walk_exits}],
    {"AAA": "USD"}, today="2026-04-15")["currencies"]["USD"]
by_month = dict((m["month"], m) for m in split["monthly"])
check("a position sold across two months puts each sale in its own month",
      by_month["2026-01"]["sales"] == 1 and by_month["2026-03"]["sales"] == 1
      and abs(by_month["2026-01"]["pnl"] - round(per_sale[0]["pnl"], 4)) < 1e-9)
check("months with no activity are still listed, not closed up",
      [m["month"] for m in split["monthly"]] == ["2026-01", "2026-02", "2026-03", "2026-04"]
      and by_month["2026-02"]["sales"] == 0 and by_month["2026-04"]["pnl"] == 0.0)
check("the trade counts as opened in its first-buy month and closed in its last-sale month",
      by_month["2026-01"]["opened"] == 1 and by_month["2026-01"]["closed"] == 0
      and by_month["2026-03"]["closed"] == 1)
check("monthly return is realised P&L over the basis of the shares sold",
      abs(by_month["2026-03"]["return_pct"]
          - round(100 * per_sale[1]["pnl"] / per_sale[1]["basis"], 4)) < 1e-9)
check("capital traded is every buy's cost, fees included (%.2f)" % split["totals"]["capital_bought"],
      abs(split["totals"]["capital_bought"] - (10 * 100.0 + 2.0 + 10 * 200.0)) < 1e-9)
check("once fully sold, capital traded equals the basis return is measured on",
      abs(split["totals"]["capital_bought"] - split["totals"]["basis_sold"]) < 1e-6
      and split["totals"]["capital_open"] == 0.0)
check("cumulative P&L at the last month equals the total",
      abs(split["monthly"][-1]["cum_pnl"] - split["totals"]["realised_pnl"]) < 1e-9)

# Win or loss is judged on the whole trade: here the first sale loses, the
# second wins by more, so the trade -- and its closing month -- is a win.
mixed = performance.build(
    [{"id": 2, "ticker": "BBB", "lots": [lot("2026-05-01", 10, 100.0)],
      "exits": [sale("2026-05-10", 5, 90.0, i=1), sale("2026-06-10", 5, 130.0, i=2)]}],
    {"BBB": "USD"}, today="2026-06-30")["currencies"]["USD"]
mm = dict((m["month"], m) for m in mixed["monthly"])
check("a losing sale inside a winning trade does not make the trade a loss",
      mm["2026-05"]["pnl"] < 0 and mm["2026-06"]["wins"] == 1
      and mm["2026-06"]["losses"] == 0 and mixed["totals"]["wins"] == 1)

ccy = performance.build(
    [{"id": 3, "ticker": "USX", "lots": [lot("2026-05-01", 1, 100.0)],
      "exits": [sale("2026-05-02", 1, 110.0)]},
     {"id": 4, "ticker": "EUX", "lots": [lot("2026-05-01", 1, 100.0)],
      "exits": [sale("2026-05-02", 1, 105.0)]}],
    {"USX": "USD", "EUX": "EUR"}, today="2026-05-31")
check("dollars and euros are never added together",
      sorted(ccy["currencies"]) == ["EUR", "USD"]
      and ccy["currencies"]["USD"]["totals"]["realised_pnl"] == 10.0
      and ccy["currencies"]["EUR"]["totals"]["realised_pnl"] == 5.0)

held = [{"id": 5, "ticker": "OPN", "lots": [lot("2026-05-01", 10, 50.0)], "exits": []}]
check("an open position reports no unrealised figure without a price",
      performance.build(held, {"OPN": "USD"}, today="2026-05-31")
      ["currencies"]["USD"]["totals"]["unrealised_pnl"] is None)
check("capital still in an open position is reported separately",
      performance.build(held, {"OPN": "USD"}, today="2026-05-31")
      ["currencies"]["USD"]["totals"]["capital_open"] == 500.0)
check("and marks it to market when one is given (%.2f)" %
      performance.build(held, {"OPN": "USD"}, {"OPN": 55.0}, today="2026-05-31")
      ["currencies"]["USD"]["totals"]["unrealised_pnl"],
      performance.build(held, {"OPN": "USD"}, {"OPN": 55.0}, today="2026-05-31")
      ["currencies"]["USD"]["totals"]["unrealised_pnl"] == 50.0)

late = performance.build(
    [{"id": 6, "ticker": "LTE", "lots": [lot("2026-05-01", 1, 100.0)],
      "exits": [sale("2026-07-02", 1, 120.0)]}],
    {"LTE": "USD"}, today="2026-05-31")["currencies"]["USD"]
check("a sale dated after today still lands in a month instead of vanishing",
      late["monthly"][-1]["month"] == "2026-07" and late["totals"]["realised_pnl"] == 20.0)


# ------------------------------------------------------------ scoreboard

print("\nSCOREBOARD")


def trade(rank, was_best, ret, n=3):
    return {"status": "closed", "outcome": {
        "graded": True, "pick_rank": rank, "pick_was_best": was_best,
        "n_candidates": n, "pick_return_pct": ret, "traded_return_pct": ret,
        "field_avg_return_pct": 1.0, "edge_vs_field_pct": ret - 1.0,
        "opportunity_cost_pct": 0.0 if was_best else 5.0, "pnl": ret * 10}}


b = review.scoreboard([trade(1, True, 6), trade(3, False, -2), trade(1, True, 4),
                       trade(2, False, 1)])
check("hit rate counts only strictly-best picks (%.0f%%)" % b["hit_rate_pct"],
      b["hit_rate_pct"] == 50.0)
check("random baseline for 3 candidates is 33.3%%", b["random_baseline_pct"] == 33.3)
check("rank histogram is kept", b["by_rank"] == {"1": 2, "2": 1, "3": 1})
check("win rate is separate from hit rate (%.0f%%)" % b["win_rate_pct"],
      b["win_rate_pct"] == 75.0)
check("P&L totals across closed trades", abs(b["total_pnl"] - 90.0) < 1e-6)

b = review.scoreboard([{"status": "closed", "outcome": {
    "graded": False, "traded_return_pct": 3.0, "pnl": 30.0}}])
check("an ungraded trade contributes P&L but no hit rate",
      b["hit_rate_pct"] is None and b["total_pnl"] == 30.0)
check("an empty log produces no numbers rather than zeros",
      review.scoreboard([])["hit_rate_pct"] is None)


# ------------------------------------------------------------------ done

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
if FAIL:
    print("FAILED: " + ", ".join(FAIL))
sys.exit(1 if FAIL else 0)
