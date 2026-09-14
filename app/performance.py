"""Monthly and since-inception performance across every logged trade.

Pure functions over trade dicts -- no database, no network -- so every number on
the dashboard can be asserted in a test.

Three definitions decide what the dashboard means, and each is a choice:

**P&L is dated by the sale that banked it.** A position bought in August and sold
in September is September's result. A position sold in two halves across two
months puts each half in its own month. `position.realisations` supplies one
dated entry per sale from the same cost-basis walk that produces the trade
totals, so the monthly figures always add up to the trade figures.

**Return % is realised P&L over the cost basis of the shares sold.** It answers
"what did the capital I actually traded earn". It is not a portfolio return:
the app has no account size, so it cannot know how much sat idle.

**A trade counts as "closed" in the month its last share was sold** and as
"opened" in the month of its first buy. A trade opened in one month and closed
in the next appears in both columns, once each.

Currencies are never mixed. Summing USD with EUR produces a number that means
nothing, so every figure is grouped by the currency the ticker trades in.
"""
import datetime

from app import position


def _month(date_str):
    return date_str[:7]


def _months_between(first, last):
    """Every YYYY-MM from `first` to `last` inclusive, empty months included --
    a month with no trades is information, not a gap to close up."""
    y, m = int(first[:4]), int(first[5:7])
    ly, lm = int(last[:4]), int(last[5:7])
    out = []
    while (y, m) <= (ly, lm):
        out.append("%04d-%02d" % (y, m))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def _pct(num, den):
    return round(100.0 * num / den, 4) if den and den > 1e-9 else None


def build(trades, currencies, quotes=None, today=None):
    """`trades`: dicts with ticker, lots, exits. `currencies`: ticker -> code.
    `quotes`: ticker -> latest price, used only to mark OPEN shares."""
    quotes = quotes or {}
    today = today or datetime.date.today().isoformat()

    by_ccy = {}
    for t in trades:
        if not t.get("lots"):
            continue
        by_ccy.setdefault(currencies.get(t["ticker"]) or "?", []).append(t)

    return {
        "today": today,
        "currencies": dict((ccy, _one_currency(ts, quotes, today))
                           for ccy, ts in sorted(by_ccy.items())),
    }


def _one_currency(trades, quotes, today):
    rows = []          # per-trade facts, computed once
    for t in trades:
        lots, exits = t["lots"], t.get("exits") or []
        econ = position.economics(lots, exits, price=quotes.get(t["ticker"]))
        rows.append({
            "id": t.get("id"),
            "ticker": t["ticker"],
            "econ": econ,
            "sales": position.realisations(lots, exits),
            "first_buy": econ["first_buy"],
            "closed_on": econ["last_exit"] if econ["fully_closed"] else None,
        })

    first_buy = min(r["first_buy"] for r in rows)
    # Normally today, but a sale typed with a later date must still have a
    # month to land in rather than vanish from the totals.
    last_date = max([today] + [s["date"] for r in rows for s in r["sales"]])
    months = _months_between(_month(first_buy), _month(last_date))
    bucket = dict((m, {"month": m, "opened": 0, "closed": 0, "sales": 0,
                       "wins": 0, "losses": 0, "pnl": 0.0, "basis": 0.0,
                       "fees": 0.0, "closed_tickers": []}) for m in months)

    for r in rows:
        bucket[_month(r["first_buy"])]["opened"] += 1
        for s in r["sales"]:
            b = bucket[_month(s["date"])]
            b["sales"] += 1
            b["pnl"] += s["pnl"]
            b["basis"] += s["basis"]
            b["fees"] += s["fee"]
        if r["closed_on"]:
            b = bucket[_month(r["closed_on"])]
            b["closed"] += 1
            # A closed trade is a win or a loss on its whole result, not on
            # whichever of its sales happened to land in this month.
            if r["econ"]["realised_pnl"] > 0:
                b["wins"] += 1
            else:
                b["losses"] += 1
            b["closed_tickers"].append(r["ticker"])

    cum_pnl = cum_basis = 0.0
    monthly = []
    for m in months:
        b = bucket[m]
        cum_pnl += b["pnl"]
        cum_basis += b["basis"]
        monthly.append(dict(
            b,
            pnl=round(b["pnl"], 4),
            basis=round(b["basis"], 4),
            fees=round(b["fees"], 4),
            return_pct=_pct(b["pnl"], b["basis"]),
            cum_pnl=round(cum_pnl, 4),
            cum_return_pct=_pct(cum_pnl, cum_basis),
        ))

    # Since inception, one point per sale day, starting flat on the first buy.
    by_day = {}
    for r in rows:
        for s in r["sales"]:
            d = by_day.setdefault(s["date"], {"pnl": 0.0, "basis": 0.0, "events": []})
            d["pnl"] += s["pnl"]
            d["basis"] += s["basis"]
            d["events"].append({"ticker": r["ticker"], "trade_id": r["id"],
                                "qty": round(s["qty"], 8), "pnl": round(s["pnl"], 4)})
    cumulative = [{"date": first_buy, "cum_pnl": 0.0, "cum_return_pct": None,
                   "day_pnl": 0.0, "events": []}]
    run_pnl = run_basis = 0.0
    for day in sorted(by_day):
        d = by_day[day]
        run_pnl += d["pnl"]
        run_basis += d["basis"]
        cumulative.append({"date": day, "cum_pnl": round(run_pnl, 4),
                           "cum_return_pct": _pct(run_pnl, run_basis),
                           "day_pnl": round(d["pnl"], 4), "events": d["events"]})
    if cumulative[-1]["date"] < today:
        cumulative.append(dict(cumulative[-1], date=today, day_pnl=0.0, events=[]))

    closed = [r for r in rows if r["closed_on"]]
    wins = [r for r in closed if r["econ"]["realised_pnl"] > 0]
    open_rows = [r for r in rows if r["econ"]["qty_open"] > 0]
    unrealised = [r["econ"].get("unrealised_pnl") for r in open_rows]
    active = [m for m in monthly if m["sales"]]

    return {
        "first_trade": first_buy,
        "monthly": monthly,
        "cumulative": cumulative,
        "totals": {
            "trades": len(rows),
            "closed": len(closed),
            "open": len(open_rows),
            "wins": len(wins),
            "losses": len(closed) - len(wins),
            "win_rate_pct": _pct(len(wins), len(closed)),
            "realised_pnl": round(run_pnl, 4),
            # Everything ever spent on buys, fees included, open positions too.
            "capital_bought": round(sum(r["econ"]["total_buy_cost"] for r in rows), 4),
            # The part of it that has been sold -- the denominator of return_pct.
            # Equal to capital_bought once every position is closed.
            "basis_sold": round(run_basis, 4),
            "capital_open": round(sum(r["econ"]["open_cost"] for r in open_rows), 4),
            "return_pct": _pct(run_pnl, run_basis),
            "fees": round(sum(m["fees"] for m in monthly), 4),
            # Mean of the per-trade returns: each trade weighted equally,
            # unlike return_pct above, which weights by capital.
            "avg_trade_return_pct": round(
                sum(r["econ"]["realised_return_pct"] for r in closed) / len(closed), 4)
                if closed else None,
            "best_month": max(active, key=lambda m: m["pnl"])["month"] if active else None,
            "worst_month": min(active, key=lambda m: m["pnl"])["month"] if active else None,
            # Only counted when every open position could be priced; a partial
            # sum would understate the exposure without saying so.
            "unrealised_pnl": round(sum(unrealised), 4)
                if open_rows and all(u is not None for u in unrealised) else None,
        },
    }
