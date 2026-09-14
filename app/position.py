"""Position arithmetic: buys, partial sells, and what is realised along the way.

A position is a chronological sequence of buys and sells. Everything the rest of
the app reports about a trade -- average cost, shares still held, realised P&L,
whether it is closed -- is derived here by walking that sequence once, so there
is a single definition and no chance of two screens disagreeing.

**Moving average cost.** At each sale, the shares sold are relieved at the
average cost of everything held *at that moment*, and that cost leaves the
basis. Using the final average of all buys instead would be wrong the moment
shares are bought back after a partial sell, which is exactly what someone
scaling out of a winner does.

**Not FIFO.** Estonian (and most) capital-gains tax is computed FIFO, so the
realised figures here are for judging the trade, not for a tax return.
"""

TOL = 1e-9          # share counts are floats; anything under this is zero


def _events(lots, exits):
    """Buys and sells in the order they must be applied.

    On a shared date buys are applied first: you cannot sell shares you do not
    yet hold, and neither record carries a time of day to decide otherwise.
    """
    out = [(l["lot_date"], 0, l.get("id") or 0, "buy", l) for l in lots]
    out += [(e["exit_date"], 1, e.get("id") or 0, "sell", e) for e in exits]
    out.sort(key=lambda x: (x[0], x[1], x[2]))
    return out


def _walk(lots, exits):
    """The one moving-average-cost walk. Returns (state, realisations).

    `realisations` is one entry per sale, carrying the date it happened, so
    realised P&L can be bucketed by when it was actually banked -- a position
    bought in August and sold in September belongs to September. `economics`
    consumes the same walk, so the per-sale figures always sum to the totals.
    """
    qty = 0.0            # shares held right now in the walk
    cost = 0.0           # cost basis of those shares, fees included
    bought_qty = bought_cost = 0.0
    oversold = 0.0
    realisations = []

    for _date, _rank, _id, kind, rec in _events(lots, exits):
        if kind == "buy":
            qty += rec["qty"]
            cost += rec["qty"] * rec["price"] + (rec["fee"] or 0.0)
            bought_qty += rec["qty"]
            bought_cost += rec["qty"] * rec["price"] + (rec["fee"] or 0.0)
            continue

        take = min(rec["qty"], qty)
        if rec["qty"] - take > TOL:
            # Should be impossible -- the API refuses to store it -- but a
            # silently wrong basis is worse than a reported inconsistency.
            oversold += rec["qty"] - take
        if take <= TOL:
            continue
        avg = cost / qty if qty > TOL else 0.0
        basis = take * avg
        # Prorate the fee if only part of the requested quantity could be sold.
        fee = (rec["fee"] or 0.0) * (take / rec["qty"] if rec["qty"] else 1.0)
        got = take * rec["price"] - fee

        realisations.append({
            "date": rec["exit_date"], "qty": take, "basis": basis,
            "proceeds": got, "pnl": got - basis, "fee": fee,
        })
        cost -= basis
        qty -= take

    state = {"qty": qty, "cost": cost, "bought_qty": bought_qty,
             "bought_cost": bought_cost, "oversold": oversold}
    return state, realisations


def realisations(lots, exits):
    """Per-sale realised P&L, each dated by its sale. See `_walk`."""
    return _walk(lots, exits)[1]


def economics(lots, exits, price=None):
    """Walk a position. `price`, when given, marks the open shares to market."""
    state, sales = _walk(lots, exits)
    qty, cost = state["qty"], state["cost"]
    bought_qty, bought_cost = state["bought_qty"], state["bought_cost"]
    oversold = state["oversold"]
    sold_qty = sum(s["qty"] for s in sales)
    sold_basis = sum(s["basis"] for s in sales)
    proceeds = sum(s["proceeds"] for s in sales)
    realised = sum(s["pnl"] for s in sales)

    open_qty = qty if qty > TOL else 0.0
    out = {
        "qty_bought": round(bought_qty, 8),
        "qty_sold": round(sold_qty, 8),
        "qty_open": round(open_qty, 8),
        "total_buy_cost": round(bought_cost, 6),
        "avg_buy_price": round(bought_cost / bought_qty, 6) if bought_qty else None,
        "open_cost": round(cost, 6) if open_qty else 0.0,
        "open_avg_cost": round(cost / open_qty, 6) if open_qty else None,
        "realised_pnl": round(realised, 4) if sold_qty else None,
        "realised_return_pct": round(100.0 * realised / sold_basis, 4)
                               if sold_basis > TOL else None,
        "avg_exit_price": round(proceeds / sold_qty, 6) if sold_qty else None,
        "proceeds": round(proceeds, 4) if sold_qty else None,
        "first_buy": min([l["lot_date"] for l in lots], default=None),
        "last_exit": max([e["exit_date"] for e in exits], default=None),
        "n_buys": len(lots),
        "n_sells": len(exits),
        "fully_closed": bool(sold_qty > TOL and open_qty <= 0.0),
        "partially_sold": bool(sold_qty > TOL and open_qty > 0.0),
        "oversold": round(oversold, 8) if oversold > TOL else 0.0,
    }

    if price is not None and open_qty > 0.0:
        out["mark_price"] = round(price, 6)
        out["open_value"] = round(open_qty * price, 4)
        out["unrealised_pnl"] = round(open_qty * price - cost, 4)
        out["unrealised_return_pct"] = round(
            100.0 * (open_qty * price - cost) / cost, 4) if cost > TOL else None
        # Realised plus unrealised, against everything ever put in. This is the
        # only figure that answers "how is this position doing overall".
        total = (realised if sold_qty else 0.0) + (open_qty * price - cost)
        out["total_pnl"] = round(total, 4)
        out["total_return_pct"] = round(100.0 * total / bought_cost, 4) \
            if bought_cost > TOL else None
    elif open_qty <= 0.0:
        out["total_pnl"] = out["realised_pnl"]
        out["total_return_pct"] = out["realised_return_pct"]

    return out


def first_oversell(lots, exits):
    """The first sale that would sell more shares than were held at the time.

    Returns (date, requested, held) or None. The walk itself clamps an oversell
    so a bad history cannot produce nonsense numbers, but an edit that CREATES
    one -- moving a buy after its sale, shrinking a package below what was later
    sold -- must be refused with a reason rather than quietly clamped.
    """
    held = 0.0
    for date, _rank, _id, kind, rec in _events(lots, exits):
        if kind == "buy":
            held += rec["qty"]
        else:
            if rec["qty"] - held > 1e-6:
                return date, rec["qty"], max(held, 0.0)
            held -= rec["qty"]
    return None


def can_sell(econ, qty):
    """Validate a proposed sale against the shares actually held."""
    if qty is None or qty <= 0:
        return "A sale needs a share count greater than zero."
    if qty - econ["qty_open"] > 1e-6:
        return ("You hold %g share(s); selling %g would be more than the "
                "position." % (econ["qty_open"], qty))
    return None
