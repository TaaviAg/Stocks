"""FastAPI application: JSON API plus the static single-page frontend."""
import datetime
import os
import traceback
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import db, performance, position, review
from app.config import CFG, ROOT
from app.data import (DataError, clear_cache, close_on_or_before, history,
                      live_quote, resolve)
from app.scoring import analyse, score_history

STATIC_DIR = os.path.join(ROOT, "static")

app = FastAPI(title="Stock Trading Tracker", version="1.0")


@app.on_event("startup")
def _startup():
    db.init()


@app.exception_handler(DataError)
def _data_error(request, exc):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


# ------------------------------------------------------------------ schemas

class SlotBody(BaseModel):
    ticker: str = Field(default="", max_length=32)


class HotlistBody(BaseModel):
    """Every slot at once. Index 0 is slot 1; "" clears that slot."""
    tickers: list[str]


class RecommendBody(BaseModel):
    tickers: list[str] | None = None      # defaults to the saved hotlist
    save: bool = True
    note: str | None = None


class LotBody(BaseModel):
    """One buy tranche. Date and price default to the latest close."""
    lot_date: str | None = None
    qty: float
    price: float | None = None
    fee: float | None = 0.0
    note: str | None = None


class OpenTradeBody(BaseModel):
    ticker: str
    recommendation_id: int | None = None
    lots: list[LotBody] | None = None
    note: str | None = None
    # Accepted for scripted use and for anything still posting the flat form.
    qty: float | None = None
    entry_date: str | None = None
    entry_price: float | None = None


class SellBody(BaseModel):
    """One sale. `qty` omitted means everything still held."""
    exit_date: str | None = None
    qty: float | None = None
    price: float | None = None
    fee: float | None = 0.0
    note: str | None = None


class CloseTradeBody(BaseModel):
    exit_date: str | None = None
    exit_price: float | None = None
    note: str | None = None


# ------------------------------------------------------------------ hotlist

@app.get("/api/hotlist")
def api_hotlist():
    return {"slots": CFG["hotlist_size"], "hotlist": db.get_hotlist()}


@app.put("/api/hotlist/{slot}")
def api_set_slot(slot: int, body: SlotBody):
    if not 1 <= slot <= CFG["hotlist_size"]:
        raise HTTPException(400, "Slot must be 1..%d." % CFG["hotlist_size"])
    ticker = (body.ticker or "").strip().upper()
    if not ticker:
        return {"hotlist": db.set_hotlist_slot(slot, None)}
    meta = resolve(ticker)                # raises DataError on a bad symbol
    existing = [h for h in db.get_hotlist() if h["slot"] != slot]
    if any(h["ticker"] == meta["ticker"] for h in existing):
        raise HTTPException(400, "%s is already in the hotlist." % meta["ticker"])
    return {"hotlist": db.set_hotlist_slot(slot, meta), "resolved": meta}


@app.put("/api/hotlist")
def api_set_hotlist(body: HotlistBody):
    """Replace the whole hotlist in one go.

    Resolving every symbol before writing anything means a typo in slot 3 does
    not leave slots 1 and 2 changed and slot 3 stale -- the call either takes
    effect entirely or not at all.
    """
    wanted = [t.strip().upper() for t in body.tickers][:CFG["hotlist_size"]]
    wanted += [""] * (CFG["hotlist_size"] - len(wanted))

    filled = [t for t in wanted if t]
    dupes = sorted(set(t for t in filled if filled.count(t) > 1))
    if dupes:
        raise HTTPException(400, "%s appears more than once in the hotlist."
                            % ", ".join(dupes))

    metas, errors = {}, []
    for i, ticker in enumerate(wanted):
        if not ticker:
            metas[i + 1] = None
            continue
        try:
            metas[i + 1] = resolve(ticker)
        except DataError as exc:
            errors.append(str(exc))
    if errors:
        raise HTTPException(400, " ".join(errors))

    return {"hotlist": db.replace_hotlist(metas)}


@app.get("/api/lookup/{ticker}")
def api_lookup(ticker: str):
    return resolve(ticker)


# --------------------------------------------------------------- the analysis

def _analyse_one(ticker):
    try:
        df = history(ticker, CFG["history_period"], CFG["interval"],
                     ttl=CFG["cache_ttl_seconds"])
        result = analyse(ticker, df, CFG)
        if result.get("ok"):
            # Deterministic and derived purely from completed daily bars, so it
            # belongs in the saved snapshot alongside everything else.
            result["score_history"] = score_history(
                ticker, df, CFG, CFG["compare_sessions"])
        return result
    except DataError as exc:
        return {"ticker": ticker, "ok": False, "error": str(exc)}
    except Exception as exc:              # never let one bad ticker kill the page
        traceback.print_exc()
        return {"ticker": ticker, "ok": False,
                "error": "%s: %s" % (type(exc).__name__, exc)}


def _analyse_many(tickers):
    """Score every candidate, fetching them concurrently.

    The work is almost entirely waiting on Yahoo, so doing it in sequence made
    the wait grow linearly with the hotlist -- noticeable at three candidates
    and tiresome at seven. The pool is deliberately small: enough to overlap the
    waiting, not enough to look like scraping.
    """
    if len(tickers) <= 1:
        return [_analyse_one(t) for t in tickers]
    with ThreadPoolExecutor(max_workers=min(4, len(tickers))) as pool:
        return list(pool.map(_analyse_one, tickers))


def _pct_change(base, now):
    """Move from the scored close to the live price, as a percentage."""
    if not base or now is None:
        return None
    return round(100.0 * (now - base) / base, 3)


def _rank(results):
    ok = [r for r in results if r.get("ok")]
    ok.sort(key=lambda r: r["score"], reverse=True)
    for i, r in enumerate(ok):
        r["rank"] = i + 1
    return ok, [r for r in results if not r.get("ok")]


@app.post("/api/recommend")
def api_recommend(body: RecommendBody):
    tickers = [t.strip().upper() for t in (body.tickers or []) if t.strip()]
    if not tickers:
        tickers = [h["ticker"] for h in db.get_hotlist()]
    if not tickers:
        raise HTTPException(400, "The hotlist is empty - add at least one ticker.")

    results = _analyse_many(tickers)
    ranked, failed = _rank(results)
    if not ranked:
        raise HTTPException(
            400, "None of %s could be scored. %s"
                 % (", ".join(tickers), "; ".join(f.get("error", "") for f in failed)))

    now = datetime.datetime.now().isoformat(timespec="seconds")
    rec_id = None
    if body.save:
        rec_id = db.save_recommendation(now, ranked[0]["as_of"], ranked, body.note)

    # Attached only AFTER the snapshot is written. The stored recommendation is
    # the record of what was scored, and everything in it comes from completed
    # daily bars; folding a live price into it would make the log depend on the
    # minute the button was pressed.
    quotes = _quotes_for([r["ticker"] for r in ranked])
    for r in ranked:
        quote = quotes.get(r["ticker"])
        if quote:
            r["live"] = dict(quote, change_pct=_pct_change(r["price"], quote["price"]))

    pick, runner = ranked[0], (ranked[1] if len(ranked) > 1 else None)
    margin = round(pick["score"] - runner["score"], 2) if runner else None
    return {
        "recommendation_id": rec_id,
        "created_at": now,
        "as_of": ranked[0]["as_of"],
        "pick": pick["ticker"],
        "pick_score": pick["score"],
        "margin": margin,
        "confidence": _confidence(margin, pick["score"]),
        "ranked": ranked,
        "failed": failed,
        "weights": CFG["weights"],
    }


def _confidence(margin, score):
    """How much to trust the ordering, as opposed to the score itself.

    Two scores three points apart are, for practical purposes, a tie -- saying
    so is more useful than presenting a winner the model does not really have.
    """
    if margin is None:
        return {"level": "n/a", "note": "Only one candidate could be scored."}
    if margin < 3.0:
        return {"level": "tie", "note":
                "The top two are within %.1f points. Treat this as a tie and let "
                "the trade plan or your own read decide." % margin}
    if margin < 8.0:
        return {"level": "slight", "note":
                "A %.1f point lead - a real but slim preference." % margin}
    return {"level": "clear", "note":
            "A %.1f point lead over the runner-up." % margin}


def _quotes_for(tickers):
    """Live quotes for several tickers at once, fetched concurrently."""
    wanted = [t for t in dict.fromkeys(tickers) if t]
    if not wanted:
        return {}
    if len(wanted) == 1:
        return {wanted[0]: live_quote(wanted[0])}
    with ThreadPoolExecutor(max_workers=min(4, len(wanted))) as pool:
        return dict(zip(wanted, pool.map(live_quote, wanted)))


@app.get("/api/quotes")
def api_quotes(tickers: str):
    """Latest traded price for each comma-separated ticker.

    Deliberately separate from `/api/recommend`: quotes move every minute and
    scores only once a day, so the page can refresh one without re-running --
    or worse, re-saving -- the other.
    """
    return {"quotes": _quotes_for(
        [t.strip().upper() for t in tickers.split(",")])}


@app.get("/api/analyse/{ticker}")
def api_analyse(ticker: str):
    df = history(ticker.strip().upper(), CFG["history_period"], CFG["interval"],
                 ttl=CFG["cache_ttl_seconds"])
    return analyse(ticker.strip().upper(), df, CFG)


@app.post("/api/refresh")
def api_refresh():
    clear_cache()
    return {"ok": True}


# ------------------------------------------------------------ recommendations

@app.get("/api/recommendations")
def api_recommendations(limit: int = 100):
    return {"recommendations": db.list_recommendations(limit)}


@app.get("/api/recommendations/{rec_id}")
def api_recommendation(rec_id: int):
    rec = db.get_recommendation(rec_id)
    if not rec:
        raise HTTPException(404, "No recommendation %d." % rec_id)
    return rec


@app.get("/api/recommendations/{rec_id}/review")
def api_review_recommendation(rec_id: int, until: str | None = None):
    """How the call has aged, whether or not a trade was taken on it."""
    rec = db.get_recommendation(rec_id)
    if not rec:
        raise HTTPException(404, "No recommendation %d." % rec_id)
    until = until or datetime.date.today().isoformat()
    rows = review.candidate_returns(rec["tickers"], rec["as_of"], until)
    usable = review.rank_by_return(rows)
    elapsed = review.sessions_elapsed(usable)
    scored = dict((a["ticker"], a.get("score")) for a in rec["snapshot"] if a.get("ok"))
    for r in rows:
        r["score_at_pick"] = scored.get(r["ticker"])
    pick_row = next((r for r in usable if r["ticker"] == rec["pick"]), None)
    if not elapsed:
        # No bar has printed since the call was scored; every return is 0.0.
        for r in usable:
            r.pop("actual_rank", None)
        pick_row = None
    return {
        "recommendation_id": rec_id,
        "from": rec["as_of"],
        "until": until,
        "pick": rec["pick"],
        "candidates": rows,
        "sessions_elapsed": elapsed,
        "pick_rank": pick_row["actual_rank"] if pick_row else None,
        "pick_was_best": bool(pick_row and pick_row["actual_rank"] == 1
                              and sum(1 for r in usable if r["actual_rank"] == 1) == 1),
        "n_ranked": len(usable) if elapsed else 0,
    }


@app.delete("/api/recommendations/{rec_id}")
def api_delete_recommendation(rec_id: int):
    db.delete_recommendation(rec_id)
    return {"ok": True}


# -------------------------------------------------------------------- trades

@app.get("/api/trades")
def api_trades(status: str | None = None, marks: bool = False):
    """Every trade. `marks=true` also returns currency and live quotes, which
    the trade table needs for totals and open P&L -- off by default so the many
    internal refreshes do not each hit Yahoo."""
    trades = db.list_trades(status)
    out = {"trades": trades, "scoreboard": review.scoreboard(db.list_trades())}
    if marks:
        currencies, quotes = _currencies_and_quotes(trades)
        for t in trades:
            t["currency"] = currencies.get(t["ticker"])
        out["quotes"] = dict((k, v) for k, v in quotes.items() if v)
    return out


def _resolve_lot(ticker, lot):
    """Fill in a lot's date and price from the market when they are omitted."""
    when = (lot.lot_date or datetime.date.today().isoformat()).strip()
    price = lot.price
    if price is None:
        price, _bar = close_on_or_before(ticker, when)
        if price is None:
            raise HTTPException(400, "No close for %s on or before %s - enter "
                                     "the price you paid." % (ticker, when))
        price = round(price, 4)
    if lot.qty is None or lot.qty <= 0:
        raise HTTPException(400, "A buy needs a share count greater than zero.")
    if price <= 0:
        raise HTTPException(400, "A buy needs a price greater than zero.")
    _check_not_future(when, "Buy")
    return {"lot_date": when, "qty": float(lot.qty), "price": float(price),
            "fee": float(lot.fee or 0.0), "note": lot.note}


@app.post("/api/trades")
def api_open_trade(body: OpenTradeBody):
    ticker = body.ticker.strip().upper()
    if not ticker:
        raise HTTPException(400, "A trade needs a ticker.")
    lots = body.lots
    if not lots:
        # Flat single-buy form: turn it into the one-lot case rather than
        # keeping a second way for a position to exist.
        lots = [LotBody(lot_date=body.entry_date, qty=body.qty or 1.0,
                        price=body.entry_price)]

    _check_ticker_exists(ticker)
    resolved = [_resolve_lot(ticker, l) for l in lots]
    _check_recommendation(ticker, body.recommendation_id,
                          min(l["lot_date"] for l in resolved))
    tid = db.open_trade(body.recommendation_id, ticker, resolved, body.note)
    return db.get_trade(tid)


def _check_ticker_exists(ticker):
    """A position on a symbol Yahoo does not know could never be marked to
    market or graded, so refuse it on entry instead of failing quietly later."""
    history(ticker, "1mo", CFG["interval"], ttl=CFG["cache_ttl_seconds"])


def _check_recommendation(ticker, rec_id, first_buy):
    """The two conditions under which a trade can honestly be graded against a
    call. Shared by creating and editing, so an edit cannot sneak past a rule
    that creation enforces."""
    if not rec_id:
        return
    rec = db.get_recommendation(rec_id)
    if not rec:
        raise HTTPException(404, "No recommendation %d." % rec_id)
    # Grading ranks the traded ticker among the recommendation's candidates.
    # Outside that field there is no rank and no decision cost to compute.
    if ticker not in rec["tickers"]:
        raise HTTPException(
            400, "%s was not a candidate in recommendation #%d (%s), so the "
                 "trade cannot be graded against it."
                 % (ticker, rec["id"], ", ".join(rec["tickers"])))
    # A call scored after the buy is hindsight: it did not exist when the
    # decision was made, and grading against it would flatter the model.
    if first_buy < rec["as_of"]:
        raise HTTPException(
            400, "The first buy on %s is before recommendation #%d was "
                 "scored (close of %s). Link an earlier recommendation, or "
                 "grade it against none." % (first_buy, rec["id"], rec["as_of"]))


def _check_not_future(date_str, what):
    if date_str > datetime.date.today().isoformat():
        raise HTTPException(400, "%s date %s is in the future." % (what, date_str))


@app.post("/api/trades/{trade_id}/lots")
def api_add_lot(trade_id: int, body: LotBody):
    trade = db.get_trade(trade_id)
    if not trade:
        raise HTTPException(404, "No trade %d." % trade_id)
    # Adding a forgotten buy to a closed trade used to be refused because its
    # grade was frozen. Grades are now re-derived after every change, so the
    # correction is allowed: the position reopens with the unsold shares.
    lot = _resolve_lot(trade["ticker"], body)
    _check_recommendation(trade["ticker"], trade["recommendation_id"],
                          min([lot["lot_date"]] + [l["lot_date"] for l in trade["lots"]]))
    db.add_lot(trade_id, lot["lot_date"], lot["qty"], lot["price"],
               lot["fee"], lot["note"])
    return _grade_if_closed(trade_id)


@app.delete("/api/lots/{lot_id}")
def api_delete_lot(lot_id: int):
    try:
        trade = db.delete_lot(lot_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if trade is None:
        raise HTTPException(404, "No lot %d." % lot_id)
    return _grade_if_closed(trade["id"])


class EntryEdit(BaseModel):
    """A correction to one buy or sale. Omitted fields are left as they are."""
    date: str | None = None
    qty: float | None = None
    price: float | None = None
    fee: float | None = None
    note: str | None = None


def _edit_entry(table, row_id, body, what):
    if body.qty is not None and body.qty <= 0:
        raise HTTPException(400, "%s needs a share count greater than zero." % what)
    if body.price is not None and body.price <= 0:
        raise HTTPException(400, "%s needs a price greater than zero." % what)
    if body.fee is not None and body.fee < 0:
        raise HTTPException(400, "A fee cannot be negative.")
    date = body.date.strip() if body.date else None
    if date:
        try:
            datetime.date.fromisoformat(date)
        except ValueError:
            raise HTTPException(400, "Not a date: %r." % body.date)
        _check_not_future(date, what)

    date_key = "lot_date" if table == "trade_lots" else "exit_date"
    fields = {date_key: date, "qty": body.qty, "price": body.price,
              "fee": body.fee, "note": body.note}

    if table == "trade_lots" and date:
        # Moving a buy can change the first-buy date, which is what the
        # hindsight rule checks. Test the date the trade WOULD have.
        owner = _trade_of("trade_lots", row_id)
        if owner:
            dates = [date if l["id"] == row_id else l["lot_date"] for l in owner["lots"]]
            _check_recommendation(owner["ticker"], owner["recommendation_id"], min(dates))

    try:
        trade = db.update_entry(table, row_id, fields)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if trade is None:
        raise HTTPException(404, "No such %s." % what.lower())
    return _grade_if_closed(trade["id"])


def _trade_of(table, row_id):
    with db.connect() as conn:
        row = conn.execute("SELECT trade_id FROM %s WHERE id = ?" % table,
                           (row_id,)).fetchone()
    return db.get_trade(row["trade_id"]) if row else None


@app.patch("/api/lots/{lot_id}")
def api_edit_lot(lot_id: int, body: EntryEdit):
    return _edit_entry("trade_lots", lot_id, body, "Buy")


@app.patch("/api/exits/{exit_id}")
def api_edit_exit(exit_id: int, body: EntryEdit):
    return _edit_entry("trade_exits", exit_id, body, "Sale")


class TradeEdit(BaseModel):
    """A correction to the trade itself. `recommendation_id: 0` unlinks it."""
    ticker: str | None = None
    recommendation_id: int | None = None
    note: str | None = None


@app.patch("/api/trades/{trade_id}")
def api_edit_trade(trade_id: int, body: TradeEdit):
    trade = db.get_trade(trade_id)
    if not trade:
        raise HTTPException(404, "No trade %d." % trade_id)

    ticker = body.ticker.strip().upper() if body.ticker else trade["ticker"]
    if not ticker:
        raise HTTPException(400, "A trade needs a ticker.")
    if ticker != trade["ticker"]:
        _check_ticker_exists(ticker)

    if body.recommendation_id is None:
        rec_id = trade["recommendation_id"]
    else:
        rec_id = body.recommendation_id or None
    # Re-checked even when only the ticker changed: a MSFT trade linked to a
    # MSFT/META/AMZN call stops being gradeable against it if it becomes NVDA.
    _check_recommendation(ticker, rec_id, trade["entry_date"])

    fields = {"ticker": ticker, "recommendation_id": rec_id}
    if rec_id:
        fields["followed_pick"] = 1 if db.get_recommendation(rec_id)["pick"] == ticker else 0
    if body.note is not None:
        fields["note"] = body.note.strip() or None
    db.update_trade(trade_id, fields)
    return _grade_if_closed(trade_id)


def _grade_if_closed(trade_id, note=None):
    """Grade a position the moment the last share leaves, and not before.

    A half-sold position has no final holding period, so grading it would fix a
    verdict against a window that is still running.
    """
    trade = db.get_trade(trade_id)
    if trade["status"] != "closed" or trade["outcome"]:
        return trade
    rec = db.get_recommendation(trade["recommendation_id"]) \
        if trade["recommendation_id"] else None
    econ = trade["econ"]
    outcome = review.settle(
        rec, trade["ticker"], trade["entry_date"], trade["entry_price"],
        trade["exit_date"], trade["exit_price"], econ["qty_sold"],
        pnl=econ["realised_pnl"], traded_return_pct=econ["realised_return_pct"])
    outcome["econ"] = econ
    return db.record_outcome(trade_id, outcome, note)


@app.post("/api/trades/{trade_id}/sell")
def api_sell(trade_id: int, body: SellBody):
    """Sell some or all of a position. Omitting `qty` sells the remainder."""
    trade = db.get_trade(trade_id)
    if not trade:
        raise HTTPException(404, "No trade %d." % trade_id)
    if trade["status"] == "closed":
        raise HTTPException(400, "Trade %d is already fully sold." % trade_id)

    when = (body.exit_date or datetime.date.today().isoformat()).strip()
    _check_not_future(when, "Sale")
    if when < trade["entry_date"]:
        raise HTTPException(400, "Sell date %s is before the first buy on %s."
                            % (when, trade["entry_date"]))

    qty = body.qty if body.qty is not None else trade["econ"]["qty_open"]
    problem = position.can_sell(trade["econ"], qty)
    if problem:
        raise HTTPException(400, problem)

    price = body.price
    if price is None:
        price, _bar = close_on_or_before(trade["ticker"], when)
        if price is None:
            raise HTTPException(400, "No close for %s on or before %s - enter "
                                     "the price you sold at."
                                % (trade["ticker"], when))
        price = round(price, 4)
    if price <= 0:
        raise HTTPException(400, "A sale needs a price greater than zero.")

    db.add_exit(trade_id, when, float(qty), float(price), float(body.fee or 0.0),
                body.note)
    return _grade_if_closed(trade_id, body.note)


@app.delete("/api/exits/{exit_id}")
def api_delete_exit(exit_id: int):
    trade = db.delete_exit(exit_id)
    if trade is None:
        raise HTTPException(404, "No sale %d." % exit_id)
    return _grade_if_closed(trade["id"])


@app.post("/api/trades/{trade_id}/close")
def api_close_trade(trade_id: int, body: CloseTradeBody):
    """Sell everything still held -- the one-click form of /sell."""
    return api_sell(trade_id, SellBody(exit_date=body.exit_date, qty=None,
                                       price=body.exit_price, fee=0.0,
                                       note=body.note))


@app.get("/api/trades/{trade_id}/review")
def api_review_trade(trade_id: int):
    trade = db.get_trade(trade_id)
    if not trade:
        raise HTTPException(404, "No trade %d." % trade_id)
    if trade["status"] == "closed":
        return trade["outcome"]
    rec = db.get_recommendation(trade["recommendation_id"]) \
        if trade["recommendation_id"] else None
    return review.review_open(rec, trade)


@app.delete("/api/trades/{trade_id}")
def api_delete_trade(trade_id: int):
    db.delete_trade(trade_id)
    return {"ok": True}


@app.get("/api/performance")
def api_performance():
    """Monthly and since-inception results across every logged trade."""
    trades = db.list_trades()
    currencies, quotes = _currencies_and_quotes(trades)
    open_tickers = set(t["ticker"] for t in trades if t["econ"]["qty_open"] > 0)
    prices = dict((t, quotes[t]["price"]) for t in open_tickers if quotes.get(t))
    return performance.build(trades, currencies, prices)


def _currencies_and_quotes(trades):
    """Currency per ticker, plus live quotes for anything open or unknown.

    Currency comes from the stored hotlist where known, otherwise from Yahoo.
    It decides which figures may be added together, so a ticker whose currency
    cannot be established is left out of the map (reported as "?") rather than
    guessed.
    """
    tickers = sorted(set(t["ticker"] for t in trades))
    currencies = dict((h["ticker"], h["currency"]) for h in db.get_hotlist()
                      if h.get("currency"))
    missing = [t for t in tickers if t not in currencies]
    open_tickers = sorted(set(t["ticker"] for t in trades
                              if t["econ"]["qty_open"] > 0))
    quotes = _quotes_for(sorted(set(missing) | set(open_tickers)))
    for t in missing:
        if quotes.get(t) and quotes[t].get("currency"):
            currencies[t] = quotes[t]["currency"]
    return currencies, quotes


@app.get("/api/scoreboard")
def api_scoreboard():
    return review.scoreboard(db.list_trades())


@app.get("/api/config")
def api_config():
    return {"weights": CFG["weights"], "hotlist_size": CFG["hotlist_size"],
            "compare_sessions": CFG["compare_sessions"],
            "history_period": CFG["history_period"], "interval": CFG["interval"]}


# -------------------------------------------------------------------- static

class FreshStatic(StaticFiles):
    """Serve the frontend with revalidation instead of blind browser caching.

    Editing `static/*` needs only a reload -- but the browser was happily
    serving a cached `app.js`, so a change appeared not to have happened until
    someone thought to hard-refresh. `no-cache` still allows a 304 via ETag, so
    this costs a conditional request, not a re-download.
    """

    def is_not_modified(self, response_headers, request_headers=None):
        return super().is_not_modified(response_headers, request_headers)

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


@app.get("/")
def index():
    """Serve the page with the asset URLs stamped by file modification time.

    Headers alone were not enough: a browser that had already cached `app.js`
    from before those headers existed went on serving it, so an edit looked like
    it had not taken effect and only a hard refresh fixed it. A changed file now
    means a changed URL, which no cache can second-guess.
    """
    with open(os.path.join(STATIC_DIR, "index.html"), encoding="utf-8") as fh:
        html = fh.read()
    for asset in ("app.js", "style.css"):
        try:
            stamp = int(os.path.getmtime(os.path.join(STATIC_DIR, asset)))
        except OSError:
            continue
        html = html.replace("/static/%s" % asset, "/static/%s?v=%d" % (asset, stamp))
    return HTMLResponse(html, headers={"Cache-Control": "no-cache, must-revalidate"})


app.mount("/static", FreshStatic(directory=STATIC_DIR), name="static")
