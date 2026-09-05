"""SQLite storage: the hotlist, the recommendation log, and the trade log.

The recommendation log is the point of the application. A recommendation is
written once and never updated -- it is the record of what the model believed
at a moment, complete with every indicator value behind it, so that when the
position is finally closed the call can be judged rather than remembered.
"""
import json
import os
import sqlite3

from app import position
from app.config import CFG, DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS hotlist (
    slot        INTEGER PRIMARY KEY,
    ticker      TEXT NOT NULL,
    name        TEXT,
    currency    TEXT,
    exchange    TEXT,
    added_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS recommendations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,          -- ISO-8601 local time
    as_of       TEXT NOT NULL,          -- date of the last bar scored
    pick        TEXT NOT NULL,
    pick_score  REAL NOT NULL,
    runner_up   TEXT,
    margin      REAL,                   -- pick_score - runner_up score
    tickers     TEXT NOT NULL,          -- JSON list, ranked best first
    snapshot    TEXT NOT NULL,          -- JSON: full analysis of every candidate
    note        TEXT
);

CREATE TABLE IF NOT EXISTS trades (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    recommendation_id INTEGER REFERENCES recommendations(id) ON DELETE SET NULL,
    ticker            TEXT NOT NULL,
    followed_pick     INTEGER NOT NULL DEFAULT 1,
    qty               REAL,
    entry_date        TEXT NOT NULL,
    entry_price       REAL NOT NULL,
    exit_date         TEXT,
    exit_price        REAL,
    status            TEXT NOT NULL DEFAULT 'open',
    outcome           TEXT,             -- JSON verdict, written once at close
    note              TEXT
);

-- One buy tranche. A position is often built in several: 10 at 496.82, then 5
-- at 496.93. The `trades` row keeps the summary (total quantity, weighted
-- average cost, first buy date) so everything downstream reads one number, and
-- this table keeps the detail so the summary can always be re-derived.
CREATE TABLE IF NOT EXISTS trade_lots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id    INTEGER NOT NULL REFERENCES trades(id) ON DELETE CASCADE,
    lot_date    TEXT NOT NULL,
    qty         REAL NOT NULL,
    price       REAL NOT NULL,
    fee         REAL NOT NULL DEFAULT 0,
    note        TEXT
);

-- One sell tranche. A position bought in packages is usually sold in them too
-- -- scale out half into strength, keep the rest -- so an exit is a list, not a
-- single price. `trades.exit_date` / `exit_price` stay as the summary.
CREATE TABLE IF NOT EXISTS trade_exits (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id    INTEGER NOT NULL REFERENCES trades(id) ON DELETE CASCADE,
    exit_date   TEXT NOT NULL,
    qty         REAL NOT NULL,
    price       REAL NOT NULL,
    fee         REAL NOT NULL DEFAULT 0,
    note        TEXT
);

CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
CREATE INDEX IF NOT EXISTS idx_exits_trade ON trade_exits(trade_id);
CREATE INDEX IF NOT EXISTS idx_rec_created ON recommendations(created_at);
CREATE INDEX IF NOT EXISTS idx_lots_trade ON trade_lots(trade_id);
"""

# Trades written before lots existed carry their quantity and price on the trade
# row alone. Give each one a single lot so there is exactly one code path.
MIGRATE_LOTS = """
INSERT INTO trade_lots (trade_id, lot_date, qty, price, fee)
SELECT id, entry_date, qty, entry_price, 0 FROM trades
 WHERE qty IS NOT NULL
   AND id NOT IN (SELECT DISTINCT trade_id FROM trade_lots)
"""

# Likewise for positions closed before partial sells existed: the whole quantity
# left at one price on one date, which is exactly one exit.
MIGRATE_EXITS = """
INSERT INTO trade_exits (trade_id, exit_date, qty, price, fee)
SELECT id, exit_date, qty, exit_price, 0 FROM trades
 WHERE status = 'closed' AND exit_date IS NOT NULL AND exit_price IS NOT NULL
   AND qty IS NOT NULL
   AND id NOT IN (SELECT DISTINCT trade_id FROM trade_exits)
"""


def connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init():
    with connect() as conn:
        conn.executescript(SCHEMA)
        conn.execute(MIGRATE_LOTS)
        conn.execute(MIGRATE_EXITS)


def _rows(cur):
    return [dict(r) for r in cur.fetchall()]


# --------------------------------------------------------------------- hotlist

def get_hotlist():
    with connect() as conn:
        rows = _rows(conn.execute(
            "SELECT * FROM hotlist ORDER BY slot"))
    return rows


def _insert_slot(conn, slot, meta, now):
    conn.execute("DELETE FROM hotlist WHERE slot = ?", (slot,))
    if meta:
        conn.execute(
            "INSERT INTO hotlist (slot, ticker, name, currency, exchange, added_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (slot, meta["ticker"], meta.get("name"), meta.get("currency"),
             meta.get("exchange"), now))


def set_hotlist_slot(slot, meta):
    """`meta` is a dict from data.resolve(), or None to clear the slot."""
    import datetime
    with connect() as conn:
        _insert_slot(conn, slot, meta, datetime.datetime.now().isoformat(timespec="seconds"))
    return get_hotlist()


def replace_hotlist(metas):
    """Write every slot at once. `metas` maps slot number -> meta dict or None.

    Doing the whole list in one transaction is what makes a swap possible: set
    slot 1 to what is currently in slot 2 one call at a time and the duplicate
    check rejects the intermediate state, even though the state the user asked
    for is perfectly valid.
    """
    import datetime
    now = datetime.datetime.now().isoformat(timespec="seconds")
    with connect() as conn:
        for slot, meta in sorted(metas.items()):
            _insert_slot(conn, slot, meta, now)
    return get_hotlist()


# ------------------------------------------------------------- recommendations

def save_recommendation(created_at, as_of, ranked, note=None):
    """`ranked` is the list of per-ticker analyses, best first."""
    pick = ranked[0]
    runner = ranked[1] if len(ranked) > 1 else None
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO recommendations"
            " (created_at, as_of, pick, pick_score, runner_up, margin, tickers, snapshot, note)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (created_at, as_of, pick["ticker"], pick["score"],
             runner["ticker"] if runner else None,
             round(pick["score"] - runner["score"], 2) if runner else None,
             json.dumps([r["ticker"] for r in ranked]),
             json.dumps(ranked), note))
        return cur.lastrowid


def get_recommendation(rec_id):
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM recommendations WHERE id = ?", (rec_id,)).fetchone()
    if not row:
        return None
    rec = dict(row)
    rec["tickers"] = json.loads(rec["tickers"])
    rec["snapshot"] = json.loads(rec["snapshot"])
    return rec


def list_recommendations(limit=100):
    """Summaries only -- the snapshot JSON is large and rarely needed in a list."""
    with connect() as conn:
        rows = _rows(conn.execute(
            "SELECT r.id, r.created_at, r.as_of, r.pick, r.pick_score, r.runner_up,"
            "       r.margin, r.tickers, r.note,"
            "       (SELECT COUNT(*) FROM trades t WHERE t.recommendation_id = r.id) AS trade_count"
            " FROM recommendations r ORDER BY r.id DESC LIMIT ?", (limit,)))
    for r in rows:
        r["tickers"] = json.loads(r["tickers"])
    return rows


def recommendation_prices(rec_id):
    """Entry-time price of every candidate, straight from the stored snapshot."""
    rec = get_recommendation(rec_id)
    if not rec:
        return {}
    return dict((a["ticker"], a.get("price")) for a in rec["snapshot"] if a.get("ok"))


# -------------------------------------------------------------------- trades

def _lots_exits(conn, trade_id):
    lots = _rows(conn.execute(
        "SELECT * FROM trade_lots WHERE trade_id = ? ORDER BY lot_date, id",
        (trade_id,)))
    exits = _rows(conn.execute(
        "SELECT * FROM trade_exits WHERE trade_id = ? ORDER BY exit_date, id",
        (trade_id,)))
    return lots, exits


def _recompute(conn, trade_id):
    """Re-derive the trade summary from its buys and sells.

    The summary columns are a cache of what `position.economics` computes, kept
    so ordinary queries and old code read one number:

        qty          total shares bought (not what is still held)
        entry_price  weighted average cost of every buy, fees included
        entry_date   the FIRST buy -- when the recommendation was acted on, and
                     therefore where the grading window starts
        exit_price   weighted average sale price, net of selling fees
        exit_date    the LAST sell
        status       'closed' once nothing is left, otherwise 'open'

    Fees are folded into the basis and netted off the proceeds, so P&L is net of
    them with no separate subtraction anywhere.
    """
    lots, exits = _lots_exits(conn, trade_id)
    if not lots:
        return
    e = position.economics(lots, exits)
    conn.execute(
        "UPDATE trades SET qty = ?, entry_price = ?, entry_date = ?,"
        " exit_date = ?, exit_price = ?, status = ? WHERE id = ?",
        (e["qty_bought"], e["avg_buy_price"], e["first_buy"],
         e["last_exit"], e["avg_exit_price"],
         "closed" if e["fully_closed"] else "open", trade_id))


def open_trade(recommendation_id, ticker, lots, note=None):
    """`lots` is a list of {lot_date, qty, price, fee} -- at least one."""
    followed = 1
    if recommendation_id:
        rec = get_recommendation(recommendation_id)
        followed = 1 if (rec and rec["pick"] == ticker) else 0
    first = min(l["lot_date"] for l in lots)
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO trades"
            " (recommendation_id, ticker, followed_pick, qty, entry_date, entry_price,"
            "  status, note) VALUES (?, ?, ?, 0, ?, 0, 'open', ?)",
            (recommendation_id, ticker, followed, first, note))
        trade_id = cur.lastrowid
        for l in lots:
            conn.execute(
                "INSERT INTO trade_lots (trade_id, lot_date, qty, price, fee, note)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (trade_id, l["lot_date"], l["qty"], l["price"],
                 l.get("fee") or 0.0, l.get("note")))
        _recompute(conn, trade_id)
        return trade_id


def add_lot(trade_id, lot_date, qty, price, fee=0.0, note=None):
    with connect() as conn:
        conn.execute(
            "INSERT INTO trade_lots (trade_id, lot_date, qty, price, fee, note)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (trade_id, lot_date, qty, price, fee or 0.0, note))
        _recompute(conn, trade_id)
    return get_trade(trade_id)


def delete_lot(lot_id):
    with connect() as conn:
        row = conn.execute("SELECT trade_id FROM trade_lots WHERE id = ?",
                           (lot_id,)).fetchone()
        if not row:
            return None
        trade_id = row["trade_id"]
        remaining = conn.execute(
            "SELECT COUNT(*) AS n FROM trade_lots WHERE trade_id = ?",
            (trade_id,)).fetchone()["n"]
        if remaining <= 1:
            # A position with no lots has no cost basis and could not be graded.
            # Deleting the trade is the honest interpretation of removing its
            # last buy, but that is the caller's decision, not a silent one.
            raise ValueError(
                "This is the only buy on the position - delete the whole trade "
                "instead of its last lot.")
        conn.execute("DELETE FROM trade_lots WHERE id = ?", (lot_id,))
        _recompute(conn, trade_id)
    return get_trade(trade_id)


def add_exit(trade_id, exit_date, qty, price, fee=0.0, note=None):
    with connect() as conn:
        conn.execute(
            "INSERT INTO trade_exits (trade_id, exit_date, qty, price, fee, note)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (trade_id, exit_date, qty, price, fee or 0.0, note))
        _recompute(conn, trade_id)
    return get_trade(trade_id)


def delete_exit(exit_id):
    """Undo a sale. The position reopens if it was the one that closed it."""
    with connect() as conn:
        row = conn.execute("SELECT trade_id FROM trade_exits WHERE id = ?",
                           (exit_id,)).fetchone()
        if not row:
            return None
        trade_id = row["trade_id"]
        conn.execute("DELETE FROM trade_exits WHERE id = ?", (exit_id,))
        _recompute(conn, trade_id)
        # The grade belonged to a holding period that no longer ends there.
        if conn.execute("SELECT status FROM trades WHERE id = ?",
                        (trade_id,)).fetchone()["status"] != "closed":
            conn.execute("UPDATE trades SET outcome = NULL WHERE id = ?", (trade_id,))
    return get_trade(trade_id)


def get_exits(trade_id):
    with connect() as conn:
        return _rows(conn.execute(
            "SELECT * FROM trade_exits WHERE trade_id = ? ORDER BY exit_date, id",
            (trade_id,)))


def get_lots(trade_id):
    with connect() as conn:
        return _rows(conn.execute(
            "SELECT * FROM trade_lots WHERE trade_id = ? ORDER BY lot_date, id",
            (trade_id,)))


def record_outcome(trade_id, outcome, note=None):
    """Store the verdict for a position the sells have already closed.

    Quantity, prices, dates and status all come from `_recompute`; this writes
    only the grading, so there is no second place that can decide a trade is
    closed.
    """
    with connect() as conn:
        conn.execute(
            "UPDATE trades SET outcome = ?, note = COALESCE(?, note) WHERE id = ?",
            (json.dumps(outcome), note, trade_id))
    return get_trade(trade_id)


def get_trade(trade_id):
    with connect() as conn:
        row = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
    if not row:
        return None
    t = dict(row)
    t["outcome"] = json.loads(t["outcome"]) if t["outcome"] else None
    t["lots"] = get_lots(trade_id)
    t["exits"] = get_exits(trade_id)
    t["econ"] = position.economics(t["lots"], t["exits"])
    return t


def list_trades(status=None, limit=200):
    sql = ("SELECT t.*, r.pick AS rec_pick, r.pick_score AS rec_pick_score,"
           " r.created_at AS rec_created_at FROM trades t"
           " LEFT JOIN recommendations r ON r.id = t.recommendation_id")
    args = []
    if status:
        sql += " WHERE t.status = ?"
        args.append(status)
    sql += " ORDER BY t.id DESC LIMIT ?"
    args.append(limit)
    with connect() as conn:
        rows = _rows(conn.execute(sql, args))
        lots, exits = {}, {}
        for l in _rows(conn.execute(
                "SELECT * FROM trade_lots ORDER BY lot_date, id")):
            lots.setdefault(l["trade_id"], []).append(l)
        for x in _rows(conn.execute(
                "SELECT * FROM trade_exits ORDER BY exit_date, id")):
            exits.setdefault(x["trade_id"], []).append(x)
    for t in rows:
        t["outcome"] = json.loads(t["outcome"]) if t["outcome"] else None
        t["lots"] = lots.get(t["id"], [])
        t["exits"] = exits.get(t["id"], [])
        t["econ"] = position.economics(t["lots"], t["exits"])
    return rows


def delete_trade(trade_id):
    with connect() as conn:
        conn.execute("DELETE FROM trades WHERE id = ?", (trade_id,))


def delete_recommendation(rec_id):
    with connect() as conn:
        conn.execute("DELETE FROM recommendations WHERE id = ?", (rec_id,))
