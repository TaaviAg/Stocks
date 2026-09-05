"""Tunable settings and scoring weights.

Everything a person might want to change without touching code lives here, and
can be overridden by a `config.json` next to the project root. Weights are the
whole opinion of the app -- keeping them in one visible place is the point.
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "data", "stocks.db")
CONFIG_PATH = os.path.join(ROOT, "config.json")

DEFAULTS = {
    # --- data ---
    "history_period": "2y",      # fetched; indicators need ~250 bars for EMA200
    "interval": "1d",
    "cache_ttl_seconds": 600,    # Yahoo daily bars do not need re-fetching often
    # Candidates compared per analysis. Raising this weakens the no-skill
    # baseline the model must beat: picking at random from n scores 1/n, so 3
    # candidates means 33% and 7 means 14%. The Track record tab computes that
    # baseline from the candidates each trade actually had, so a hotlist that
    # changes size over time still reports an honest bar.
    # 8 is a hard ceiling, not an arbitrary one: the comparison chart assigns a
    # fixed categorical hue per slot and there are exactly 8 validated slots. A
    # 9th would have to fold into an "Other" grouping rather than invent a hue,
    # because generated colours stop being distinguishable under colour-vision
    # deficiency.
    "hotlist_size": 8,

    # --- indicator periods (swing / daily) ---
    "ema_fast": 20,
    "ema_mid": 50,
    "ema_slow": 200,
    "rsi_period": 14,
    "atr_period": 14,
    "adx_period": 14,
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,
    "bb_period": 20,
    "bb_stddev": 2.0,
    "roc_period": 20,
    "obv_slope_period": 20,
    "volume_avg_period": 50,
    "swing_lookback": 20,        # bars used for the stop's swing low
    "high_52w_period": 252,
    # Closes shown on the score-history chart. Each one costs a full re-scoring
    # per candidate (~9 ms), so this is the knob that decides how long an
    # analysis takes: 20 x 7 candidates is ~1.3 s of CPU, ~350 ms threaded.
    "compare_sessions": 20,

    # --- scoring weights (must sum to 1.0; validated at import) ---
    "weights": {
        "trend": 0.30,
        "momentum": 0.25,
        "structure": 0.20,
        "volume": 0.15,
        "risk": 0.10,
    },

    # --- trade plan ---
    "stop_atr_multiple": 2.0,    # stop = min(swing low, close - k*ATR)
    "target_r_multiple": 2.0,    # target = entry + 2R
    "min_bars_required": 60,     # below this we refuse to score rather than guess
}


def load():
    cfg = json.loads(json.dumps(DEFAULTS))  # deep copy
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            user = json.load(fh)
        for k, v in user.items():
            if k == "weights" and isinstance(v, dict):
                cfg["weights"].update(v)
            else:
                cfg[k] = v
    total = sum(cfg["weights"].values())
    if abs(total - 1.0) > 1e-6:
        raise ValueError(
            "Scoring weights must sum to 1.0, got %.4f: %r" % (total, cfg["weights"])
        )
    return cfg


CFG = load()
