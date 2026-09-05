"""Pure indicator maths on an OHLCV DataFrame.

No I/O, no config lookups beyond what is passed in -- so every function here is
testable against a hand-built frame without a network or a database.

All smoothed averages that Wilder defined (RSI, ATR, ADX) use Wilder's
smoothing (alpha = 1/n), not a simple EMA -- the two differ enough to move an
RSI by several points, which matters when a threshold is being crossed.
"""
import numpy as np
import pandas as pd


def _wilder(series, period):
    return series.ewm(alpha=1.0 / period, adjust=False).mean()


def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()


def true_range(df):
    prev_close = df["Close"].shift(1)
    ranges = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - prev_close).abs(),
        (df["Low"] - prev_close).abs(),
    ], axis=1)
    return ranges.max(axis=1)


def atr(df, period=14):
    return _wilder(true_range(df), period)


def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = _wilder(gain, period)
    avg_loss = _wilder(loss, period)
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    # avg_loss == 0 divides by nan above. Two distinct cases hide there:
    #   gain > 0 -> an unbroken run of up days, RSI is 100 by definition
    #   gain == 0 -> a perfectly flat series, which is neutral, not maximal
    flat = (avg_gain <= 0) & (avg_loss <= 0)
    out = out.fillna(100.0)
    out[flat] = 50.0
    return out


def macd(series, fast=12, slow=26, signal=9):
    line = ema(series, fast) - ema(series, slow)
    sig = ema(line, signal)
    return line, sig, line - sig


def adx(df, period=14):
    """Returns (adx, plus_di, minus_di). ADX measures trend STRENGTH only."""
    up_move = df["High"].diff()
    down_move = -df["Low"].diff()
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)

    atr_ = _wilder(true_range(df), period).replace(0.0, np.nan)
    plus_di = 100.0 * _wilder(plus_dm, period) / atr_
    minus_di = 100.0 * _wilder(minus_dm, period) / atr_
    denom = (plus_di + minus_di).replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / denom
    return _wilder(dx.fillna(0.0), period), plus_di, minus_di


def bollinger(series, period=20, stddev=2.0):
    mid = series.rolling(period).mean()
    sd = series.rolling(period).std(ddof=0)
    upper = mid + stddev * sd
    lower = mid - stddev * sd
    width = (upper - lower) / mid.replace(0.0, np.nan)
    span = (upper - lower).replace(0.0, np.nan)
    pct_b = (series - lower) / span
    return mid, upper, lower, pct_b, width


def obv(df):
    direction = np.sign(df["Close"].diff().fillna(0.0))
    return (direction * df["Volume"]).cumsum()


def roc(series, period=20):
    return 100.0 * (series / series.shift(period) - 1.0)


def slope_per_bar(series, period):
    """Least-squares slope of the last `period` values, in units per bar.

    Used instead of a simple endpoint difference so a single spike at either end
    cannot masquerade as a trend.
    """
    y = series.dropna().tail(period)
    if len(y) < max(3, period // 2):
        return np.nan
    x = np.arange(len(y), dtype=float)
    return float(np.polyfit(x, y.to_numpy(dtype=float), 1)[0])


def compute_all(df, cfg):
    """Attach every indicator as a column. Returns a new DataFrame."""
    out = df.copy()
    close = out["Close"]

    out["EMA_FAST"] = ema(close, cfg["ema_fast"])
    out["EMA_MID"] = ema(close, cfg["ema_mid"])
    out["EMA_SLOW"] = ema(close, cfg["ema_slow"])
    out["RSI"] = rsi(close, cfg["rsi_period"])
    out["ATR"] = atr(out, cfg["atr_period"])
    out["ATR_PCT"] = 100.0 * out["ATR"] / close

    line, sig, hist = macd(close, cfg["macd_fast"], cfg["macd_slow"], cfg["macd_signal"])
    out["MACD"], out["MACD_SIGNAL"], out["MACD_HIST"] = line, sig, hist

    adx_, pdi, mdi = adx(out, cfg["adx_period"])
    out["ADX"], out["PLUS_DI"], out["MINUS_DI"] = adx_, pdi, mdi

    mid, upper, lower, pct_b, width = bollinger(close, cfg["bb_period"], cfg["bb_stddev"])
    out["BB_MID"], out["BB_UPPER"], out["BB_LOWER"] = mid, upper, lower
    out["BB_PCT_B"], out["BB_WIDTH"] = pct_b, width

    out["OBV"] = obv(out)
    out["ROC"] = roc(close, cfg["roc_period"])
    out["VOL_AVG"] = out["Volume"].rolling(cfg["volume_avg_period"]).mean()
    out["SWING_LOW"] = out["Low"].rolling(cfg["swing_lookback"]).min()
    out["HIGH_52W"] = out["High"].rolling(cfg["high_52w_period"], min_periods=20).max()
    out["LOW_52W"] = out["Low"].rolling(cfg["high_52w_period"], min_periods=20).min()
    return out
