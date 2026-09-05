"""Settling a recommendation against what the market actually did.

This is the half of the app that makes the other half honest. A recommendation
picks one of three candidates; the only way to know whether that was skill is to
measure all three over the same window and see where the pick landed.

Two distinct questions are answered separately, because they can disagree:

  "Did the trade make money?"   -- the P&L of what was actually bought and sold.
  "Was the pick the right one?" -- the rank of the picked ticker among all the
                                  candidates over the identical holding period.

A losing trade in a falling market can still be a correct pick, and a winning
trade can still be the worst of three. Conflating the two teaches nothing.
"""
import datetime

from app.data import close_on_or_before, live_quote, DataError
from app.position import economics


def _pct(a, b):
    if a in (None, 0) or b is None:
        return None
    return round(100.0 * (b - a) / a, 3)


def candidate_returns(tickers, entry_date, exit_date):
    """Return of every candidate over the same window, using Yahoo closes.

    Prices come from the market rather than from the stored snapshot so that all
    candidates are measured on identical terms -- the picked one included.
    """
    rows = []
    for t in tickers:
        row = {"ticker": t}
        try:
            p0, d0 = close_on_or_before(t, entry_date)
            p1, d1 = close_on_or_before(t, exit_date)
            row.update({
                "entry_price": None if p0 is None else round(p0, 4),
                "entry_bar": d0,
                "exit_price": None if p1 is None else round(p1, 4),
                "exit_bar": d1,
                "return_pct": _pct(p0, p1),
            })
        except DataError as exc:
            row["error"] = str(exc)
        rows.append(row)
    return rows


def rank_by_return(rows):
    """Sort priced candidates best-first and assign competition ranks.

    Ties share a rank (1, 1, 3): two stocks that returned the same amount did
    equally well, and inventing an order between them would be read as a real
    distinction. Rows that could not be priced are left out entirely.
    """
    usable = [r for r in rows if r.get("return_pct") is not None]
    usable.sort(key=lambda r: r["return_pct"], reverse=True)
    for i, r in enumerate(usable):
        prev = usable[i - 1] if i else None
        if prev and abs(r["return_pct"] - prev["return_pct"]) < 1e-9:
            r["actual_rank"] = prev["actual_rank"]
        else:
            r["actual_rank"] = i + 1
    return usable


def sessions_elapsed(rows):
    """How many candidates actually moved to a different bar between the dates.

    Zero means entry and exit resolved to the same close for every candidate --
    a same-day round trip, or a review run before the next session prints. Every
    return is then 0.0 and any ranking of them would be fabricated.
    """
    return sum(1 for r in rows
               if r.get("entry_bar") and r.get("exit_bar")
               and r["entry_bar"] != r["exit_bar"])


def settle(rec, ticker, entry_date, entry_price, exit_date, exit_price, qty=None,
           pnl=None, traded_return_pct=None):
    """Judge one closed position against its recommendation.

    `rec` may be None -- a trade taken without a recommendation still gets its
    P&L, it just cannot be graded as a pick.

    `pnl` and `traded_return_pct` override the naive entry-to-exit arithmetic.
    A position sold in several tranches, or bought back into after a partial
    sell, has a realised result that only the moving-average walk in
    `position.economics` can produce; the two agree exactly for the simple case
    of one buy and one sell.
    """
    traded_return = traded_return_pct if traded_return_pct is not None \
        else _pct(entry_price, exit_price)
    days = (datetime.date.fromisoformat(exit_date)
            - datetime.date.fromisoformat(entry_date)).days

    out = {
        "settled_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "ticker": ticker,
        "entry_date": entry_date,
        "exit_date": exit_date,
        "held_days": days,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "qty": qty,
        "traded_return_pct": traded_return,
        "pnl": round(pnl, 2) if pnl is not None else (
            None if (qty is None or traded_return is None)
            else round(qty * (exit_price - entry_price), 2)),
        "graded": False,
    }

    if not rec:
        out["verdict"] = "P&L only - this trade was not linked to a recommendation, "\
                         "so there is nothing to grade the pick against."
        return out

    candidates = rec["tickers"]
    rows = candidate_returns(candidates, entry_date, exit_date)
    scored = dict((a["ticker"], a.get("score"))
                  for a in rec["snapshot"] if a.get("ok"))
    for r in rows:
        r["score_at_pick"] = scored.get(r["ticker"])

    usable = rank_by_return(rows)

    pick = rec["pick"]
    pick_row = next((r for r in usable if r["ticker"] == pick), None)
    traded_row = next((r for r in usable if r["ticker"] == ticker), None)

    out["candidates"] = rows
    out["pick"] = pick
    out["pick_score"] = rec["pick_score"]
    out["followed_pick"] = (ticker == pick)
    out["traded_rank"] = traded_row["actual_rank"] if traded_row else None

    out["sessions_elapsed"] = sessions_elapsed(usable)

    if pick_row is None or len(usable) < 2:
        out["verdict"] = "Could not price every candidate over this window, so "\
                         "the pick cannot be ranked."
        return out
    if out["sessions_elapsed"] == 0:
        out["verdict"] = "No trading session has elapsed between %s and %s, so "\
                         "there is nothing to grade yet." % (entry_date, exit_date)
        return out

    best = usable[0]
    worst = usable[-1]
    rank = pick_row["actual_rank"]
    out.update({
        "graded": True,
        "n_candidates": len(usable),
        "pick_rank": rank,
        "pick_return_pct": pick_row["return_pct"],
        "best_ticker": best["ticker"],
        "best_return_pct": best["return_pct"],
        "worst_ticker": worst["ticker"],
        "worst_return_pct": worst["return_pct"],
        # Strictly best. A tie at the top is not a win the model earned, and
        # counting it as one would inflate the hit rate on the quiet days.
        "pick_was_best": rank == 1 and sum(
            1 for r in usable if r["actual_rank"] == 1) == 1,
        # What choosing the pick cost against the best available. Zero when the
        # pick won; never negative.
        "opportunity_cost_pct": round(best["return_pct"] - pick_row["return_pct"], 3),
        # Against simply holding all three equally -- the no-skill baseline.
        "field_avg_return_pct": round(
            sum(r["return_pct"] for r in usable) / len(usable), 3),
    })
    out["edge_vs_field_pct"] = round(
        out["pick_return_pct"] - out["field_avg_return_pct"], 3)

    tied = sum(1 for r in usable if r["actual_rank"] == rank)
    if rank == 1 and tied > 1:
        out["verdict"] = "Tied pick. %s returned %+.2f%% over %d days, level with "\
            "%d other candidate(s)." % (pick, pick_row["return_pct"], days, tied - 1)
    elif rank == 1:
        out["verdict"] = "Correct pick. %s returned %+.2f%% over %d days, the best "\
            "of the %d candidates." % (pick, pick_row["return_pct"], days, len(usable))
    elif rank == len(usable):
        out["verdict"] = "Wrong pick. %s returned %+.2f%% and was the worst of the "\
            "%d; %s returned %+.2f%%." % (pick, pick_row["return_pct"], len(usable),
                                          best["ticker"], best["return_pct"])
    else:
        out["verdict"] = "Middling pick. %s returned %+.2f%%, ranked %d of %d; "\
            "%s returned %+.2f%%." % (pick, pick_row["return_pct"], rank,
                                      len(usable), best["ticker"], best["return_pct"])

    # The verdict above grades the MODEL. When the position taken was not the
    # model's pick, the decision needs grading separately -- the two can point
    # opposite ways, and reporting only the first would be read as approval of
    # a trade the model did not suggest.
    if not out["followed_pick"] and traded_row:
        cost = round(pick_row["return_pct"] - traded_row["return_pct"], 3)
        out["decision_cost_pct"] = cost
        out["decision_verdict"] = (
            "You bought %s (rank %d of %d, %+.2f%%) instead of the pick %s "
            "(%+.2f%%). Overriding %s you %.2f points."
            % (ticker, traded_row["actual_rank"], len(usable),
               traded_row["return_pct"], pick, pick_row["return_pct"],
               "cost" if cost > 0 else "gained", abs(cost)))
    return out


def review_open(rec, trade, today=None):
    """The same comparison for a live position, marked to the latest trade.

    Everything still held is valued at the most recent traded price -- including
    pre- and post-market -- and added to whatever has already been realised, so
    a half-sold position reports one honest number rather than a return on
    shares it no longer owns.

    The two halves use deliberately different prices:

      P&L        the live quote, because "am I up or down right now" is a
                 question about the current price, not about yesterday's close.
      Grading    completed daily closes, because the candidates have to be
                 compared like for like and a saved recommendation must stay
                 reproducible. Marking one side live would corrupt that.
    """
    today = today or datetime.date.today().isoformat()
    ticker = trade["ticker"]
    close, bar = close_on_or_before(ticker, today)
    if close is None:
        return {"error": "No recent close for %s." % ticker}
    close = round(close, 4)

    # Falls back to the daily close, so a failed or unavailable quote degrades
    # to the old behaviour rather than breaking the page.
    quote = live_quote(ticker)
    mark = quote["price"] if quote else close
    if quote:
        # Against the last completed daily close, which is the only baseline
        # here that is definitely the previous session. Yahoo's own
        # `previousClose` on this endpoint is the close before that one.
        quote = dict(quote, last_close=close,
                     change_pct=_pct(close, quote["price"]))

    econ = economics(trade["lots"], trade["exits"], price=mark)
    live = settle(rec, ticker, trade["entry_date"], trade["entry_price"],
                  bar or today, close, econ["qty_open"] or econ["qty_bought"],
                  pnl=econ.get("total_pnl"),
                  traded_return_pct=econ.get("total_return_pct"))
    live["provisional"] = True
    live["econ"] = econ
    live["marked_to"] = bar
    live["quote"] = quote
    live["last_close"] = close
    return live


def scoreboard(trades):
    """Aggregate track record over closed, graded trades.

    The headline number is the hit rate: how often the model's pick turned out
    to be the best of the candidates it chose from. With three candidates,
    picking at random scores 33%, so that is the bar the model has to clear --
    not 50%, and not "did the trade make money".
    """
    graded = [t for t in trades
              if t.get("status") == "closed" and t.get("outcome")
              and t["outcome"].get("graded")]
    closed = [t for t in trades if t.get("status") == "closed" and t.get("outcome")]

    board = {
        "closed_trades": len(closed),
        "graded_trades": len(graded),
        "hit_rate_pct": None,
        "random_baseline_pct": None,
        "avg_pick_return_pct": None,
        "avg_field_return_pct": None,
        "avg_edge_vs_field_pct": None,
        "avg_opportunity_cost_pct": None,
        "avg_traded_return_pct": None,
        "total_pnl": None,
        "win_rate_pct": None,
        "by_rank": {},
    }

    if closed:
        rets = [t["outcome"]["traded_return_pct"] for t in closed
                if t["outcome"].get("traded_return_pct") is not None]
        if rets:
            board["avg_traded_return_pct"] = round(sum(rets) / len(rets), 3)
            board["win_rate_pct"] = round(
                100.0 * sum(1 for r in rets if r > 0) / len(rets), 1)
        pnls = [t["outcome"]["pnl"] for t in closed if t["outcome"].get("pnl") is not None]
        if pnls:
            board["total_pnl"] = round(sum(pnls), 2)

    if graded:
        n = len(graded)
        hits = sum(1 for t in graded if t["outcome"]["pick_was_best"])
        board["hit_rate_pct"] = round(100.0 * hits / n, 1)
        sizes = [t["outcome"]["n_candidates"] for t in graded]
        board["random_baseline_pct"] = round(
            100.0 * sum(1.0 / s for s in sizes) / n, 1)
        for field, key in [("avg_pick_return_pct", "pick_return_pct"),
                           ("avg_field_return_pct", "field_avg_return_pct"),
                           ("avg_edge_vs_field_pct", "edge_vs_field_pct"),
                           ("avg_opportunity_cost_pct", "opportunity_cost_pct")]:
            vals = [t["outcome"][key] for t in graded if t["outcome"].get(key) is not None]
            if vals:
                board[field] = round(sum(vals) / len(vals), 3)
        for t in graded:
            r = str(t["outcome"]["pick_rank"])
            board["by_rank"][r] = board["by_rank"].get(r, 0) + 1

    return board
