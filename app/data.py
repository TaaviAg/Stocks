"""Yahoo Finance access, with a small TTL cache.

Daily bars change once a day, so re-fetching on every page load is pure latency
and an easy way to get rate-limited. The cache is in-process and deliberately
simple -- this is a single-user desktop app, not a service.
"""
import datetime
import threading
import time
from urllib.parse import quote_plus

import pandas as pd
import requests
import yfinance as yf

_lock = threading.Lock()
_cache = {}          # (ticker, period, interval) -> (fetched_at, DataFrame)

REQUIRED = ["Open", "High", "Low", "Close", "Volume"]


class DataError(Exception):
    pass


def _normalise(df, ticker):
    if df is None or len(df) == 0:
        raise DataError("Yahoo returned no rows for '%s'. Check the symbol -- "
                        "non-US listings need their suffix, e.g. TKM1T.TL, "
                        "NOKIA.HE, VOW3.DE." % ticker)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise DataError("Yahoo response for '%s' is missing %s." % (ticker, missing))
    df = df[REQUIRED].copy()
    df = df.dropna(subset=["Close"])
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df


def history(ticker, period="2y", interval="1d", ttl=600, force=False):
    """Return a clean OHLCV frame with a naive DatetimeIndex."""
    ticker = ticker.strip().upper()
    if not ticker:
        raise DataError("Empty ticker.")
    key = (ticker, period, interval)
    now = time.time()

    with _lock:
        hit = _cache.get(key)
        if hit and not force and (now - hit[0]) < ttl:
            return hit[1].copy()

    try:
        raw = yf.Ticker(ticker).history(period=period, interval=interval,
                                        auto_adjust=True, raise_errors=False)
    except Exception as exc:                       # network, parse, Yahoo outage
        raise DataError("Could not fetch '%s' from Yahoo: %s" % (ticker, exc))

    df = _normalise(raw, ticker)
    with _lock:
        _cache[key] = (now, df)
    return df.copy()


def closes_between(ticker, start, end, ttl=600):
    """Daily closes over [start, end] inclusive, as a date-keyed dict.

    Used when settling a trade: the outcome of every hotlist candidate over the
    same holding window is what makes the recommendation checkable.
    """
    start = pd.Timestamp(start).normalize()
    end = pd.Timestamp(end).normalize()
    # Pad both ends so a weekend or holiday entry/exit still finds a bar.
    try:
        raw = yf.Ticker(ticker).history(
            start=(start - pd.Timedelta(days=7)).strftime("%Y-%m-%d"),
            end=(end + pd.Timedelta(days=8)).strftime("%Y-%m-%d"),
            interval="1d", auto_adjust=True, raise_errors=False)
    except Exception as exc:
        raise DataError("Could not fetch '%s' from Yahoo: %s" % (ticker, exc))
    df = _normalise(raw, ticker)
    return dict((ts.strftime("%Y-%m-%d"), float(v)) for ts, v in df["Close"].items())


def close_on_or_before(ticker, when, ttl=600):
    """The last close at or before `when` -- the price a trade actually met."""
    when = pd.Timestamp(when).normalize()
    series = closes_between(ticker, when - pd.Timedelta(days=10), when, ttl=ttl)
    eligible = [d for d in series if d <= when.strftime("%Y-%m-%d")]
    if not eligible:
        return None, None
    day = max(eligible)
    return series[day], day


_quote_cache = {}        # ticker -> (fetched_at, dict)

# A quote is only ever used to mark an open position to market. It is never fed
# to an indicator: the score, the saved recommendation and the grading all run
# on completed daily bars, so that a recommendation is reproducible and every
# candidate is compared close-to-close.
QUOTE_URL = ("https://query1.finance.yahoo.com/v8/finance/chart/%s"
             "?range=1d&interval=1m&includePrePost=true")
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")

# Observed on this endpoint 2026-09-03, and the reason the code below looks the
# way it does:
#   marketState        ABSENT. Never rely on it.
#   regularMarketPrice the last REGULAR close (496.82 for MSFT) -- it does not
#                      move during pre-market, so it is a fallback, not a quote.
#   previousClose      the close BEFORE that (501.02 = MSFT's 1 Sep). Comparing
#                      a pre-market print against it reports the wrong change,
#                      so it is not used at all; the caller has the real
#                      previous close from the daily bars already.
#   currentTradingPeriod  PRESENT and correct, with pre/regular/post windows as
#                      epoch seconds. This is what actually identifies the
#                      session, and it works for Tallinn as well as New York
#                      (a venue with no pre-market has a zero-length window,
#                      which simply never matches).
_PHASE_LABEL = [("pre", "pre-market"), ("regular", "live"), ("post", "after hours")]


def _session_label(meta, when):
    periods = meta.get("currentTradingPeriod") or {}
    for key, label in _PHASE_LABEL:
        window = periods.get(key) or {}
        start, end = window.get("start"), window.get("end")
        if start and end and start <= when < end:
            return label
    return "at close"


def live_quote(ticker, ttl=30):
    """Latest traded price, including pre- and post-market.

    Returns None rather than raising: a missing quote must degrade to the last
    daily close, never break the trades page.
    """
    ticker = ticker.strip().upper()
    now = time.time()
    with _lock:
        hit = _quote_cache.get(ticker)
        if hit and (now - hit[0]) < ttl:
            return hit[1]

    try:
        res = requests.get(QUOTE_URL % quote_plus(ticker),
                           headers={"User-Agent": _UA, "Accept": "application/json"},
                           timeout=10)
        result = res.json()["chart"]["result"][0]
        meta = result["meta"]
    except Exception:
        return None

    # The last printed minute is the primary source: `includePrePost=true` puts
    # pre- and post-market minutes into this series, and it is the only field
    # here that actually moves before the opening bell.
    price = stamp = None
    try:
        closes = result["indicators"]["quote"][0]["close"]
        stamps = result["timestamp"]
        for i in range(len(closes) - 1, -1, -1):
            if closes[i] is not None:
                price, stamp = closes[i], stamps[i]
                break
    except (KeyError, IndexError, TypeError):
        pass

    stale = False
    if price is None:                     # no minute bars: fall back to the close
        price = meta.get("regularMarketPrice")
        stamp = meta.get("regularMarketTime")
        stale = True
    if price is None:
        return None

    label = "at close" if stale else _session_label(meta, stamp or now)
    out = {
        "price": round(float(price), 4),
        "label": label,
        "is_live": label in ("pre-market", "live", "after hours"),
        "currency": meta.get("currency"),
        "exchange_tz": meta.get("exchangeTimezoneName"),
        "as_of": (datetime.datetime.fromtimestamp(stamp).isoformat(timespec="seconds")
                  if isinstance(stamp, (int, float)) else None),
    }
    with _lock:
        _quote_cache[ticker] = (now, out)
    return out


def resolve(ticker):
    """Confirm a symbol exists and report what Yahoo calls it."""
    ticker = ticker.strip().upper()
    df = history(ticker, period="1mo", interval="1d")
    name, currency, exchange = ticker, None, None
    try:
        info = yf.Ticker(ticker).get_info()
        name = info.get("longName") or info.get("shortName") or ticker
        currency = info.get("currency")
        exchange = info.get("fullExchangeName") or info.get("exchange")
    except Exception:
        pass          # a symbol that returns bars but no profile is still usable
    return {
        "ticker": ticker,
        "name": name,
        "currency": currency,
        "exchange": exchange,
        "last_close": round(float(df["Close"].iloc[-1]), 4),
        "last_date": df.index[-1].strftime("%Y-%m-%d"),
    }


def clear_cache():
    with _lock:
        _cache.clear()
        _quote_cache.clear()
