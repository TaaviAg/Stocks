"""Rule-based, fully transparent scoring of a single ticker.

Every number the app shows traces back to one of the signals built here. Each
signal reports its raw indicator value, its normalised score in -1..+1, the
weight it carries, and a sentence saying what it means -- so a pick can always
be argued with rather than merely trusted.

Normalisation principle: wherever a raw value has no natural scale (a slope, a
distance from a moving average), it is divided by ATR or by average volume so
the same thresholds apply to a 5 EUR stock and a 500 EUR one.
"""
import math

import numpy as np

from app.indicators import compute_all, slope_per_bar


def _f(x, default=float("nan")):
    """Coerce to a plain float, mapping None and non-finite values to `default`."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    return default if (math.isnan(v) or math.isinf(v)) else v


def _clip(x, lo=-1.0, hi=1.0):
    if x is None or math.isnan(x):
        return 0.0
    return max(lo, min(hi, x))


def _ok(x):
    return x is not None and not math.isnan(x)


class Signal(object):
    """One scored observation. `score` is always in -1..+1."""

    __slots__ = ("key", "label", "value", "display", "score", "note")

    def __init__(self, key, label, value, display, score, note):
        self.key = key
        self.label = label
        self.value = value
        self.display = display
        self.score = round(float(score), 4)
        self.note = note

    def as_dict(self, component):
        return {
            "key": self.key,
            "component": component,
            "label": self.label,
            "value": None if not _ok(self.value) else round(self.value, 4),
            "display": self.display,
            "score": self.score,
            "note": self.note,
        }


def _fmt(v, suffix="", digits=2):
    if not _ok(v):
        return "n/a"
    return ("%." + str(digits) + "f%s") % (v, suffix)


# --------------------------------------------------------------------------
# component builders -- each returns (component_score, [Signal, ...])
# --------------------------------------------------------------------------

def _trend(d, ind, cfg):
    close = d["Close"]
    ema_f, ema_m, ema_s = d["EMA_FAST"], d["EMA_MID"], d["EMA_SLOW"]
    atr_ = d["ATR"]

    pairs = [(close, ema_f), (ema_f, ema_m), (ema_m, ema_s)]
    known = [(a, b) for a, b in pairs if _ok(a) and _ok(b)]
    in_order = sum(1 for a, b in known if a > b)
    stack = (2.0 * in_order / len(known) - 1.0) if known else 0.0

    # EMA(mid) movement over the last 20 bars, expressed in ATRs.
    slope = slope_per_bar(ind["EMA_MID"], 20)
    slope_atr = (slope * 20.0 / atr_) if (_ok(atr_) and atr_ > 0) else float("nan")
    slope_score = _clip(slope_atr / 1.5)

    pdi, mdi = d["PLUS_DI"], d["MINUS_DI"]
    di_sum = (pdi + mdi) if (_ok(pdi) and _ok(mdi)) else float("nan")
    di_dir = ((pdi - mdi) / di_sum) if (_ok(di_sum) and di_sum > 0) else float("nan")

    adx_v = d["ADX"]
    strength = _clip((adx_v - 15.0) / 25.0, 0.0, 1.0) if _ok(adx_v) else 0.0
    damping = 0.4 + 0.6 * strength

    raw = float(np.mean([stack, slope_score, _clip(di_dir)]))
    # A directionless tape cannot earn full marks however the EMAs happen to sit.
    score = raw * damping

    order = "Price > EMA%d > EMA%d > EMA%d" % (
        cfg["ema_fast"], cfg["ema_mid"], cfg["ema_slow"])
    sigs = [
        Signal("ema_stack", "EMA alignment", stack,
               "%d of %d in order" % (in_order, len(known)), stack,
               "Full marks means %s. Price above its averages, and the averages "
               "in order, is the definition of an uptrend." % order),
        Signal("ema_slope", "EMA%d slope (20 bars)" % cfg["ema_mid"], slope_atr,
               _fmt(slope_atr, " ATR"), slope_score,
               "How far the mid-term average has travelled in 20 bars, measured "
               "in ATRs so it compares across tickers."),
        Signal("di_direction", "+DI vs -DI", di_dir,
               "+DI %s / -DI %s" % (_fmt(pdi, "", 1), _fmt(mdi, "", 1)), _clip(di_dir),
               "Which side of the daily range is doing the work: buyers or sellers."),
        Signal("adx_strength", "ADX (trend strength)", adx_v, _fmt(adx_v, "", 1), 0.0,
               "Not directional, so it scores nothing on its own. Below 20 the "
               "tape is choppy, so it damps the three signals above by %.2f."
               % damping),
    ]
    return score, sigs


def _momentum(d, ind, cfg):
    rsi_v, atr_pct, atr_ = d["RSI"], d["ATR_PCT"], d["ATR"]

    rsi_score = _clip((rsi_v - 50.0) / 20.0) if _ok(rsi_v) else 0.0
    overbought = 0.0
    if _ok(rsi_v) and rsi_v > 75.0:
        overbought = min(1.0, (rsi_v - 75.0) / 10.0)
        rsi_score = _clip(rsi_score - overbought)

    hist = d["MACD_HIST"]
    hist_norm = (hist / (0.5 * atr_)) if (_ok(atr_) and atr_ > 0) else float("nan")
    hist_score = _clip(hist_norm)

    # A cross inside the last 5 bars is a fresh event, worth flagging separately.
    tail = ind["MACD_HIST"].tail(6).to_numpy(dtype=float)
    cross = 0.0
    if len(tail) == 6 and not np.isnan(tail).any():
        sign = np.sign(tail)
        if sign[-1] > 0 and (sign[:-1] <= 0).any():
            cross = 1.0
        elif sign[-1] < 0 and (sign[:-1] >= 0).any():
            cross = -1.0

    roc_v = d["ROC"]
    # A 20-bar move measured in daily-ATR units, scaled by sqrt of time.
    roc_norm = (roc_v / (atr_pct * math.sqrt(cfg["roc_period"]))) \
        if (_ok(atr_pct) and atr_pct > 0) else float("nan")
    roc_score = _clip(roc_norm)

    score = float(np.mean([rsi_score, hist_score, 0.5 * cross, roc_score]))

    sigs = [
        Signal("rsi", "RSI(%d)" % cfg["rsi_period"], rsi_v, _fmt(rsi_v, "", 1), rsi_score,
               "Above 50 is momentum. Above 75 it is extended, so a penalty of "
               "%.2f applies -- chasing a vertical move is how swing trades get "
               "bought at the top." % overbought),
        Signal("macd_hist", "MACD histogram", hist_norm, _fmt(hist_norm, " ATR"),
               hist_score,
               "Gap between MACD and its signal line, in ATRs. Positive and "
               "widening means momentum is still building."),
        Signal("macd_cross", "MACD cross (last 5 bars)", cross,
               {1.0: "bullish cross", -1.0: "bearish cross"}.get(cross, "none"),
               0.5 * cross,
               "A timing signal. It carries half weight because it says when, "
               "not how strongly."),
        Signal("roc", "Rate of change (%d bars)" % cfg["roc_period"], roc_v,
               _fmt(roc_v, "%"), roc_score,
               "Raw 20-bar return divided by the ticker's own volatility, so a "
               "quiet stock is not penalised against a wild one."),
    ]
    return score, sigs


def _structure(d, ind, cfg):
    close, high52 = d["Close"], d["HIGH_52W"]

    below = (100.0 * (high52 - close) / high52) if (_ok(high52) and high52 > 0) else float("nan")
    high_score = _clip(1.0 - below / 12.5) if _ok(below) else 0.0

    pct_b = d["BB_PCT_B"]
    bb_score = _clip((pct_b - 0.5) * 2.0) if _ok(pct_b) else 0.0
    if _ok(pct_b) and pct_b > 1.0:
        bb_score = _clip(bb_score - min(1.0, (pct_b - 1.0) * 4.0))

    ema_f, atr_ = d["EMA_FAST"], d["ATR"]
    ext = ((close - ema_f) / atr_) if (_ok(atr_) and atr_ > 0 and _ok(ema_f)) else float("nan")
    # Healthy is just above the fast average. Far above is a chase; below it is
    # either a pullback or a broken trend, and the trend component judges that.
    if not _ok(ext):
        ext_score = 0.0
    elif ext < 0:
        ext_score = _clip(ext / 2.0)
    elif ext <= 1.0:
        ext_score = ext
    else:
        ext_score = _clip(1.0 - (ext - 1.0) / 1.5)

    score = float(np.mean([high_score, bb_score, ext_score]))

    sigs = [
        Signal("dist_52w_high", "Below 52-week high", below, _fmt(below, "%"), high_score,
               "Stocks near their highs tend to keep making them. More than ~12% "
               "off the high is a stock in repair rather than in trend."),
        Signal("bollinger_b", "Bollinger %B", pct_b, _fmt(pct_b), bb_score,
               "Where price sits inside its own volatility band. Above 1.0 it has "
               "closed outside the band and is stretched."),
        Signal("extension", "Extension above EMA%d" % cfg["ema_fast"], ext,
               _fmt(ext, " ATR"), ext_score,
               "The anti-chase check. About 1 ATR above the fast average is the "
               "sweet spot; 2.5 ATR above means the entry is late."),
    ]
    return score, sigs


def _volume(d, ind, cfg):
    vol_avg = d["VOL_AVG"]
    obv_slope = slope_per_bar(ind["OBV"], cfg["obv_slope_period"])
    obv_norm = (obv_slope * cfg["obv_slope_period"] / vol_avg) \
        if (_ok(vol_avg) and vol_avg > 0) else float("nan")
    obv_score = _clip(obv_norm / 3.0)

    recent = _f(ind["Volume"].tail(5).mean())
    rel = (recent / vol_avg) if (_ok(vol_avg) and vol_avg > 0 and _ok(recent)) else float("nan")
    price_up = (len(ind) > 6 and _f(ind["Close"].iloc[-1]) > _f(ind["Close"].iloc[-6]))
    if not _ok(rel):
        rel_score = 0.0
    else:
        # Heavy volume confirms whichever way price went; it is not bullish alone.
        magnitude = _clip((rel - 1.0) / 0.8, 0.0, 1.0)
        rel_score = magnitude if price_up else -magnitude

    score = float(np.mean([obv_score, rel_score]))

    sigs = [
        Signal("obv_slope", "OBV trend (%d bars)" % cfg["obv_slope_period"], obv_norm,
               _fmt(obv_norm, "x avg vol"), obv_score,
               "On-balance volume rising means up-days carry more volume than "
               "down-days -- accumulation rather than drift."),
        Signal("rel_volume", "5-day volume vs %d-day avg" % cfg["volume_avg_period"],
               rel, _fmt(rel, "x"), rel_score,
               "Volume confirms direction. Heavy volume into a %s week reads as %s."
               % ("rising" if price_up else "falling",
                  "conviction" if price_up else "distribution")),
    ]
    return score, sigs


def _risk(d, ind, cfg, stop):
    """Scored so that LOW risk is positive -- this component is a brake."""
    close, atr_pct = d["Close"], d["ATR_PCT"]
    atr_score = _clip(1.0 - (atr_pct - 1.5) / 2.25) if _ok(atr_pct) else 0.0

    stop_pct = (100.0 * (close - stop) / close) if (_ok(stop) and _ok(close) and close) \
        else float("nan")
    # A swing stop wants to be roughly 3-8% away: closer gets noise-stopped,
    # wider means the position must be tiny to keep the loss bounded.
    if not _ok(stop_pct):
        stop_score = 0.0
    elif stop_pct < 2.0:
        stop_score = _clip(stop_pct / 2.0 - 1.0)
    else:
        stop_score = _clip(1.0 - (stop_pct - 3.0) / 7.0)

    score = float(np.mean([atr_score, stop_score]))

    sigs = [
        Signal("atr_pct", "ATR as % of price", atr_pct, _fmt(atr_pct, "%"), atr_score,
               "Daily volatility. Higher is not wrong, but it widens the stop and "
               "shrinks the position a fixed risk budget allows."),
        Signal("stop_distance", "Stop distance", stop_pct, _fmt(stop_pct, "%"), stop_score,
               "How far price must fall to prove the idea wrong. Under 2% will be "
               "hit by noise; over 10% makes being wrong expensive."),
    ]
    return score, sigs


# --------------------------------------------------------------------------

VERDICTS = [
    (70.0, "Strong", "Trend, momentum and volume agree."),
    (60.0, "Constructive", "The setup is sound but not exceptional."),
    (45.0, "Neutral", "Mixed signals - no edge either way."),
    (35.0, "Weak", "More against than for."),
    (0.0, "Avoid", "The technical picture is negative."),
]

COMPONENT_ORDER = ["trend", "momentum", "structure", "volume", "risk"]

BUILDERS = {
    "trend": _trend,
    "momentum": _momentum,
    "structure": _structure,
    "volume": _volume,
}


def verdict_for(score):
    for threshold, label, note in VERDICTS:
        if score >= threshold:
            return label, note
    return VERDICTS[-1][1], VERDICTS[-1][2]


LAST_FIELDS = [
    "Open", "High", "Low", "Close", "Volume", "EMA_FAST", "EMA_MID", "EMA_SLOW",
    "RSI", "ATR", "ATR_PCT", "MACD", "MACD_SIGNAL", "MACD_HIST", "ADX",
    "PLUS_DI", "MINUS_DI", "BB_MID", "BB_UPPER", "BB_LOWER", "BB_PCT_B",
    "BB_WIDTH", "OBV", "ROC", "VOL_AVG", "SWING_LOW", "HIGH_52W", "LOW_52W",
]


def score_history(ticker, df, cfg, sessions=10):
    """The score as it would have read at each of the last `sessions` closes.

    Computed by truncating the frame and re-scoring, which is the only honest
    way: every indicator is a function of the bars available at the time, so
    slicing the finished indicator series would leak future data into the past
    (an EMA at bar N-5 is not the same number once bars N-4..N exist).

    About 9 ms per session per ticker on two years of daily bars, so ~90 ms for
    a ten-session history -- cheap enough to compute on every analysis rather
    than storing and invalidating it.
    """
    out = []
    n = len(df)
    for back in range(sessions - 1, -1, -1):
        if n - back < cfg["min_bars_required"]:
            continue
        sub = df.iloc[:n - back] if back else df
        r = analyse(ticker, sub, cfg, chart_bars=0)
        if r.get("ok"):
            out.append({"date": r["as_of"], "score": r["score"]})
    return out


def _series(frame, col, digits=4):
    return [None if not _ok(_f(v)) else round(_f(v), digits) for v in frame[col]]


def analyse(ticker, df, cfg, chart_bars=180):
    """Score one ticker. `df` is raw OHLCV; returns a JSON-ready dict."""
    have = 0 if df is None else len(df)
    if have < cfg["min_bars_required"]:
        return {
            "ticker": ticker,
            "ok": False,
            "error": "Only %d bars of history available; %d are needed to score."
                     % (have, cfg["min_bars_required"]),
        }

    ind = compute_all(df, cfg)
    last = ind.iloc[-1]
    d = dict((k, _f(last.get(k))) for k in LAST_FIELDS)

    close, atr_, swing_low = d["Close"], d["ATR"], d["SWING_LOW"]
    atr_stop = (close - cfg["stop_atr_multiple"] * atr_) if _ok(atr_) else float("nan")
    # The tighter of the two: whichever level price reaches first is the real stop.
    candidates = [x for x in (swing_low, atr_stop) if _ok(x) and x < close]
    stop = max(candidates) if candidates else close * 0.95
    risk = close - stop
    target = close + cfg["target_r_multiple"] * risk

    components, signals = {}, []
    for name in ["trend", "momentum", "structure", "volume"]:
        s, sg = BUILDERS[name](d, ind, cfg)
        components[name] = round(float(s), 4)
        signals.extend([x.as_dict(name) for x in sg])
    s, sg = _risk(d, ind, cfg, stop)
    components["risk"] = round(float(s), 4)
    signals.extend([x.as_dict("risk") for x in sg])

    w = cfg["weights"]
    raw = sum(components[k] * w[k] for k in w)
    score = round((raw + 1.0) * 50.0, 2)
    label, note = verdict_for(score)

    hist = ind.tail(chart_bars)
    return {
        "ticker": ticker,
        "ok": True,
        "as_of": ind.index[-1].strftime("%Y-%m-%d"),
        "bars": have,
        "price": round(close, 4),
        "score": score,
        "raw_score": round(float(raw), 4),
        "verdict": label,
        "verdict_note": note,
        "components": components,
        "weights": dict(w),
        "weighted": dict((k, round(components[k] * w[k], 4)) for k in w),
        "signals": signals,
        "plan": {
            "entry": round(close, 4),
            "stop": round(stop, 4),
            "target": round(target, 4),
            "risk_per_share": round(risk, 4),
            "risk_pct": round(100.0 * risk / close, 2) if close else None,
            "reward_risk": cfg["target_r_multiple"],
            "stop_basis": "%d-bar swing low" % cfg["swing_lookback"]
                          if (_ok(swing_low) and abs(stop - swing_low) < 1e-9)
                          else "%.1f x ATR" % cfg["stop_atr_multiple"],
        },
        "levels": {
            "ema_fast": round(d["EMA_FAST"], 4) if _ok(d["EMA_FAST"]) else None,
            "ema_mid": round(d["EMA_MID"], 4) if _ok(d["EMA_MID"]) else None,
            "ema_slow": round(d["EMA_SLOW"], 4) if _ok(d["EMA_SLOW"]) else None,
            "high_52w": round(d["HIGH_52W"], 4) if _ok(d["HIGH_52W"]) else None,
            "low_52w": round(d["LOW_52W"], 4) if _ok(d["LOW_52W"]) else None,
            "atr": round(atr_, 4) if _ok(atr_) else None,
        },
        "chart": {
            "dates": [x.strftime("%Y-%m-%d") for x in hist.index],
            "close": _series(hist, "Close"),
            "ema_fast": _series(hist, "EMA_FAST"),
            "ema_mid": _series(hist, "EMA_MID"),
            "volume": _series(hist, "Volume", 0),
            "rsi": _series(hist, "RSI", 2),
        },
    }
