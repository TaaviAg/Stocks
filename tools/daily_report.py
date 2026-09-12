"""Score the watchlist against the latest completed close and print a table.

Written to be run anywhere, including a cloud agent with no access to the
laptop: it reads its tickers from `watchlist.json` rather than from the SQLite
hotlist, and it touches no database at all. Nothing here writes state, so it is
safe to run repeatedly.

    python tools/daily_report.py
    python tools/daily_report.py AMZN MSFT        # override the watchlist

Exit codes: 0 all scored, 2 nothing could be scored (usually Yahoo refusing),
3 some scored and some failed.
"""
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.config import CFG                                      # noqa: E402
from app.data import DataError, history                         # noqa: E402
from app.scoring import analyse, score_history                  # noqa: E402

FALLBACK = ["AMZN", "MSFT", "META", "GOOGL", "AAPL", "TSLA", "NVDA", "IVV"]


def watchlist():
    if len(sys.argv) > 1:
        return [t.strip().upper() for t in sys.argv[1:] if t.strip()]
    path = os.path.join(ROOT, "watchlist.json")
    try:
        with open(path, encoding="utf-8") as fh:
            tickers = json.load(fh)["tickers"]
        return [t.strip().upper() for t in tickers if t.strip()]
    except Exception as exc:
        print("!! could not read watchlist.json (%s); using the built-in list" % exc)
        return FALLBACK


def score_one(ticker):
    """Today's score, yesterday's score, and the 20-session change."""
    try:
        df = history(ticker, CFG["history_period"], force=True)
        r = analyse(ticker, df, CFG)
        if not r.get("ok"):
            return {"ticker": ticker, "ok": False, "error": r.get("error")}
        # The score as it read on the previous close, for the one-day move.
        prev = analyse(ticker, df.iloc[:-1], CFG, chart_bars=0)
        hist = score_history(ticker, df, CFG, CFG["compare_sessions"])
        r["prev_score"] = prev["score"] if prev.get("ok") else None
        r["prev_close"] = float(df["Close"].iloc[-2]) if len(df) > 1 else None
        r["chg20"] = (hist[-1]["score"] - hist[0]["score"]) if len(hist) > 1 else None
        return r
    except DataError as exc:
        return {"ticker": ticker, "ok": False, "error": str(exc)}
    except Exception as exc:
        return {"ticker": ticker, "ok": False,
                "error": "%s: %s" % (type(exc).__name__, exc)}


def signed(v, digits=1, suffix=""):
    if v is None:
        return "-"
    return "%+.*f%s" % (digits, v, suffix)


def main():
    tickers = watchlist()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(score_one, tickers))

    ok = [r for r in results if r.get("ok")]
    bad = [r for r in results if not r.get("ok")]
    ok.sort(key=lambda r: r["score"], reverse=True)

    if not ok:
        print("NOTHING COULD BE SCORED. Every ticker failed to fetch:")
        for r in bad:
            print("  %-8s %s" % (r["ticker"], r.get("error")))
        print()
        print("If these are all HTTP 429 or empty frames, Yahoo is refusing this")
        print("host -- most likely a datacenter IP being rate-limited. That is a")
        print("property of where this ran, not of the code.")
        return 2

    print("Scored on the close of **%s** - %d of %d candidates"
          % (ok[0]["as_of"], len(ok), len(results)))
    print()
    print("| # | Ticker | Score | 1-day | 20-day | Verdict | Close | 1-day price | Stop | Target |")
    print("|---|--------|-------|-------|--------|---------|-------|-------------|------|--------|")
    for i, r in enumerate(ok, 1):
        p = r["plan"]
        day = (r["score"] - r["prev_score"]) if r["prev_score"] is not None else None
        move = (100.0 * (r["price"] - r["prev_close"]) / r["prev_close"]) \
            if r.get("prev_close") else None
        print("| %d | %s | %.1f | %s | %s | %s | %.2f | %s | %.2f | %.2f |" % (
            i, r["ticker"], r["score"], signed(day), signed(r["chg20"]),
            r["verdict"], r["price"], signed(move, 2, "%"), p["stop"], p["target"]))

    print()
    pick = ok[0]
    runner = ok[1] if len(ok) > 1 else None
    print("**Pick: %s at %.1f**%s" % (
        pick["ticker"], pick["score"],
        (" - %.1f ahead of %s" % (pick["score"] - runner["score"], runner["ticker"]))
        if runner else ""))
    print("Plan: entry %.2f, stop %.2f (%s), target %.2f, risk %.1f%%" % (
        pick["plan"]["entry"], pick["plan"]["stop"], pick["plan"]["stop_basis"],
        pick["plan"]["target"], pick["plan"]["risk_pct"]))

    # An index fund in the watchlist is the benchmark: beating six stocks means
    # little if the index would have done better.
    index = next((r for r in ok if r["ticker"] in ("IVV", "SPY", "VOO")), None)
    if index and index is not pick:
        print()
        print("Benchmark: %s scores %.1f, ranked %d of %d. %s is %.1f above it."
              % (index["ticker"], index["score"], ok.index(index) + 1, len(ok),
                 pick["ticker"], pick["score"] - index["score"]))
    elif index and index is pick:
        print()
        print("Benchmark: **%s is itself the top pick** - nothing in the watchlist "
              "currently scores above the index." % index["ticker"])

    if bad:
        print()
        print("Could not score:")
        for r in bad:
            print("  - %s: %s" % (r["ticker"], r.get("error")))

    print()
    print("The score ranks these candidates against each other. It is not a "
          "probability, and nothing here is advice.")
    return 3 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
