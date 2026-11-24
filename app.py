
import json
import os
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests
import streamlit as st
import yfinance as yf
from streamlit_autorefresh import st_autorefresh

st.set_page_config(
    page_title="JARVIS Markets",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="expanded"
)

# =====================================================
# JARVIS CONFIGURATION
# =====================================================

PRIMARY = {
    "Bitcoin": "BTC-USD",
    "Gold": "GC=F",
    "WTI Oil": "CL=F"
}

SECONDARY = {
    "Ethereum": "ETH-USD",
    "Solana": "SOL-USD",
    "XRP": "XRP-USD",
    "Silver": "SI=F",
    "NVIDIA": "NVDA",
    "Apple": "AAPL",
    "Microsoft": "MSFT",
    "Tesla": "TSLA"
}

KALSHI_URL = "https://api.elections.kalshi.com/trade-api/v2/events"
HISTORY_FILE = Path("prediction_history.csv")

st.markdown("""
<style>
.block-container {max-width:1500px;padding-top:1.3rem;}
[data-testid="stAppViewContainer"] {
 background:radial-gradient(ellipse at 15% 0%,#10263a,#08111d 48%,#060b13);
}
[data-testid="stSidebar"] {background:#0a1421;}
.hero {
 border:1px solid #24516a;border-radius:18px;padding:23px;
 background:linear-gradient(115deg,#102b3e,#08121f);
}
.eyebrow {letter-spacing:3px;color:#63e6ff;font-size:11px;font-weight:bold;}
.hero h1 {color:#eefaff;font-size:35px;letter-spacing:2px;}
.hero p {color:#a9c2d2;}
div[data-testid="stMetric"] {
 background:#0c1b2a;border:1px solid #1b3b51;
 padding:13px;border-radius:12px;
}
</style>

<div class="hero">
 <div class="eyebrow">PERSONAL MARKET INTELLIGENCE • BUILD 03</div>
 <h1>◈ JARVIS MARKETS</h1>
 <p>BTC · GOLD · WTI OIL | PREDICTION ENGINE | KALSHI RADAR</p>
</div>
""", unsafe_allow_html=True)

st_autorefresh(interval=300000, key="jarvis_refresh")

# =====================================================
# DATA ENGINE
# =====================================================

@st.cache_data(ttl=180, show_spinner=False)
def market_frame(ticker):
    try:
        df = yf.download(
            ticker,
            period="5d",
            interval="5m",
            auto_adjust=True,
            progress=False,
            threads=False
        )
        if df is None or df.empty:
            return pd.DataFrame()

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        return df.dropna(subset=["Close"])
    except Exception:
        return pd.DataFrame()


# =====================================================
# JARVIS EXPERIMENTAL PREDICTION MODEL
# =====================================================

def calculate_rsi(series, period=14):
    delta = series.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)

    avg_gain = gains.ewm(
        alpha=1 / period, adjust=False
    ).mean()

    avg_loss = losses.ewm(
        alpha=1 / period, adjust=False
    ).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def predict_direction(df, horizon):
    """
    Experimental directional model.
    15m uses recent 5m bars.
    1h uses longer trend and momentum.
    Probabilities are NOT calibrated or validated.
    """

    close = df["Close"].astype(float).dropna()

    if len(close) < 35:
        return None

    ema_fast = close.ewm(span=12).mean()
    ema_slow = close.ewm(span=26).mean()
    rsi = calculate_rsi(close)

    if horizon == "15m":
        momentum = (close.iloc[-1] / close.iloc[-4] - 1) * 100
        trend = (ema_fast.iloc[-1] / ema_slow.iloc[-1] - 1) * 100
        weight = 1.0
    else:
        momentum = (close.iloc[-1] / close.iloc[-13] - 1) * 100
        trend = (ema_fast.iloc[-1] / ema_slow.iloc[-1] - 1) * 100
        weight = 0.8

    current_rsi = float(rsi.iloc[-1])

    if not np.isfinite(current_rsi):
        return None

    # Normalize the signals to prevent one feature dominating.
    momentum_score = np.tanh(momentum / 0.35)
    trend_score = np.tanh(trend / 0.25)
    rsi_score = np.tanh((current_rsi - 50) / 15)

    score = (
        0.50 * momentum_score
        + 0.30 * trend_score
        + 0.20 * rsi_score
    ) * weight

    # Experimental score-to-probability mapping.
    # Must be calibrated against out-of-sample results.
    yes_probability = 50 + 30 * np.tanh(score)

    yes_probability = float(
        np.clip(yes_probability, 20, 80)
    )
    no_probability = 100 - yes_probability

    if yes_probability >= 55:
        direction = "YES"
    elif no_probability >= 55:
        direction = "NO"
    else:
        direction = "NEUTRAL"

    strength = abs(yes_probability - 50)

    if strength >= 18:
        confidence = "HIGH SIGNAL"
    elif strength >= 9:
        confidence = "MODERATE SIGNAL"
    else:
        confidence = "LOW SIGNAL"

    return {
        "direction": direction,
        "yes": yes_probability,
        "no": no_probability,
        "confidence": confidence,
        "rsi": current_rsi,
        "momentum": momentum,
        "trend": trend,
        "score": score,
        "price": float(close.iloc[-1])
    }


# =====================================================
# PREDICTION HISTORY AND ACCURACY
# =====================================================

HISTORY_COLUMNS = [
    "asset", "horizon", "created_at",
    "entry_price", "direction", "yes_probability",
    "settled", "actual_direction", "correct"
]


def load_history():
    if HISTORY_FILE.exists():
        try:
            data = pd.read_csv(HISTORY_FILE)
            for col in HISTORY_COLUMNS:
                if col not in data.columns:
                    data[col] = None
            return data[HISTORY_COLUMNS]
        except Exception:
            pass

    return pd.DataFrame(columns=HISTORY_COLUMNS)


def save_history(history):
    history.to_csv(HISTORY_FILE, index=False)


def update_predictions(history, asset, horizon, prediction):
    now = datetime.now(timezone.utc)

    # Resolve forecasts after their target horizon.
    for idx, row in history.iterrows():
        if row["asset"] != asset or row["horizon"] != horizon:
            continue

        if str(row["settled"]).lower() == "true":
            continue

        try:
            created = pd.Timestamp(row["created_at"])
            if created.tzinfo is None:
                created = created.tz_localize("UTC")

            minutes = 15 if horizon == "15m" else 60

            if (now - created.to_pydatetime()).total_seconds() < minutes * 60:
                continue

            entry = float(row["entry_price"])
            actual = "YES" if prediction["price"] > entry else "NO"

            history.at[idx, "settled"] = True
            history.at[idx, "actual_direction"] = actual
            history.at[idx, "correct"] = (
                str(row["direction"]) == actual
            )
        except Exception:
            continue

    # Create a new forecast at most once per horizon interval.
    existing = history[
        (history["asset"] == asset)
        & (history["horizon"] == horizon)
    ]

    should_add = existing.empty

    if not existing.empty:
        try:
            latest = pd.to_datetime(
                existing["created_at"], utc=True
            ).max()

            interval = 15 if horizon == "15m" else 60

            should_add = (
                now - latest.to_pydatetime()
            ).total_seconds() >= interval * 60
        except Exception:
            should_add = True

    if should_add and prediction["direction"] != "NEUTRAL":
        new_row = {
            "asset": asset,
            "horizon": horizon,
            "created_at": now.isoformat(),
            "entry_price": prediction["price"],
            "direction": prediction["direction"],
            "yes_probability": prediction["yes"],
            "settled": False,
            "actual_direction": "",
            "correct": False
        }

        history = pd.concat(
            [history, pd.DataFrame([new_row])],
            ignore_index=True
        )

    return history


# =====================================================
# HISTORICAL BACKTEST ENGINE
# =====================================================

@st.cache_data(ttl=3600, show_spinner=False)
def historical_frame(ticker):
    try:
        df = yf.download(
            ticker,
            period="60d",
            interval="15m",
            auto_adjust=True,
            progress=False,
            threads=False
        )

        if df is None or df.empty:
            return pd.DataFrame()

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        return df.dropna(subset=["Close"])

    except Exception:
        return pd.DataFrame()


def run_backtest(ticker, horizon):
    df = historical_frame(ticker)

    if df.empty or len(df) < 100:
        return None

    close = df["Close"].astype(float).dropna()

    ema_fast = close.ewm(span=12).mean()
    ema_slow = close.ewm(span=26).mean()
    rsi = calculate_rsi(close)

    bars = 1 if horizon == "15m" else 4

    momentum = (
        close / close.shift(bars) - 1
    ) * 100

    trend = (
        ema_fast / ema_slow - 1
    ) * 100

    score = (
        0.50 * np.tanh(momentum / 0.35)
        + 0.30 * np.tanh(trend / 0.25)
        + 0.20 * np.tanh((rsi - 50) / 15)
    )

    if horizon == "1h":
        score = score * 0.8

    yes_probability = np.clip(
        50 + 30 * np.tanh(score),
        20,
        80
    )

    prediction = np.where(
        yes_probability >= 55, 1,
        np.where(yes_probability <= 45, -1, 0)
    )

    future_return = (
        close.shift(-bars) / close - 1
    ) * 100

    actual = np.where(future_return > 0, 1, -1)

    results = pd.DataFrame({
        "prediction": prediction,
        "actual": actual,
        "future_return": future_return,
        "yes_probability": yes_probability
    }).dropna()

    results = results[results["prediction"] != 0]

    if results.empty:
        return None

    results["correct"] = (
        results["prediction"] == results["actual"]
    )

    accuracy = results["correct"].mean() * 100

    return {
        "accuracy": accuracy,
        "samples": len(results),
        "wins": int(results["correct"].sum()),
        "losses": int((~results["correct"]).sum()),
        "coverage": (
            len(results) / len(future_return.dropna()) * 100
        ),
        "brier": np.mean(
            (
                results["yes_probability"] / 100
                - (results["actual"] == 1).astype(float)
            ) ** 2
        )
    }

# =====================================================
# KALSHI RADAR
# =====================================================

@st.cache_data(ttl=180, show_spinner=False)
def kalshi_events():
    try:
        response = requests.get(
            KALSHI_URL,
            params={
                "status": "open",
                "with_nested_markets": "true",
                "limit": 100
            },
            timeout=12
        )
        response.raise_for_status()

        events = response.json().get("events", [])
        rows = []

        for event in events:
            for market in event.get("markets", []) or []:
                rows.append({
                    "Market": market.get("title") or market.get("ticker"),
                    "YES bid": market.get("yes_bid"),
                    "YES ask": market.get("yes_ask"),
                    "Volume": market.get("volume", 0),
                    "Close": market.get("close_time"),
                    "Ticker": market.get("ticker")
                })

        return pd.DataFrame(rows)

    except Exception:
        return pd.DataFrame()


# =====================================================
# SIDEBAR
# =====================================================

with st.sidebar:
    st.markdown("## ◈ JARVIS CONTROL")

    st.success("BTC / GOLD / OIL PRIORITY")

    st.caption("Automatic dashboard refresh: 5 minutes")
    st.caption("Background scanner: 15 minutes")

    show_secondary = st.toggle(
        "Show secondary markets", value=True
    )

    st.divider()

    if st.button("↻ REFRESH NOW", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.caption("READ ONLY • NO AUTOMATIC TRADING")


# =====================================================
# TOP STATUS
# =====================================================

c1, c2, c3, c4 = st.columns(4)

c1.metric("ENGINE", "BUILD 03")
c2.metric("SCAN", "15 MIN")
c3.metric("EXECUTION", "DISABLED")
c4.metric(
    "UTC TIME",
    datetime.now(timezone.utc).strftime("%H:%M:%S")
)

# =====================================================
# MAIN PREDICTION DASHBOARD
# =====================================================

st.header("⚡ PRIORITY MARKET INTELLIGENCE")
st.caption(
    "YES = model predicts price higher at the selected horizon. "
    "NO = model predicts price lower or unchanged."
)

history = load_history()

for asset, ticker in PRIMARY.items():

    st.markdown("---")
    st.subheader(asset.upper())

    df = market_frame(ticker)

    if df.empty:
        st.warning(f"{asset}: market data unavailable.")
        continue

    close = df["Close"].astype(float).dropna()
    current_price = float(close.iloc[-1])

    st.metric(
        "LATEST PRICE",
        f"${current_price:,.4f}",
        help="Latest available five-minute market bar."
    )

    p15 = predict_direction(df, "15m")
    p60 = predict_direction(df, "1h")

    if p15 is None or p60 is None:
        st.warning("Not enough price history to calculate signals.")
        continue

    history = update_predictions(
        history, asset, "15m", p15
    )
    history = update_predictions(
        history, asset, "1h", p60
    )

    st.markdown("**15-MINUTE FORECAST**")

    a, b, c = st.columns(3)

    a.metric("JARVIS DECISION", p15["direction"])
    b.metric("YES ESTIMATE", f'{p15["yes"]:.1f}%')
    c.metric("NO ESTIMATE", f'{p15["no"]:.1f}%')

    st.progress(p15["yes"] / 100)

    st.caption(
        f'{p15["confidence"]} · RSI {p15["rsi"]:.1f} '
        f'· Momentum {p15["momentum"]:+.3f}%'
    )

    st.markdown("**1-HOUR FORECAST**")

    a, b, c = st.columns(3)

    a.metric("JARVIS DECISION", p60["direction"])
    b.metric("YES ESTIMATE", f'{p60["yes"]:.1f}%')
    c.metric("NO ESTIMATE", f'{p60["no"]:.1f}%')

    st.progress(p60["yes"] / 100)

    st.caption(
        f'{p60["confidence"]} · RSI {p60["rsi"]:.1f} '
        f'· Momentum {p60["momentum"]:+.3f}%'
    )

    st.line_chart(close.tail(48), height=150)

save_history(history)

# =====================================================
# ACCURACY CENTER
# =====================================================

st.divider()
st.header("🎯 JARVIS ACCURACY CENTER")

st.caption(
    "Only resolved forecasts count. Accuracy is measured against "
    "the actual direction after the forecast horizon."
)

if not history.empty:

    settled = history[
        history["settled"].astype(str).str.lower() == "true"
    ].copy()

    for asset in PRIMARY:
        st.markdown(f"**{asset}**")

        for horizon in ["15m", "1h"]:
            subset = settled[
                (settled["asset"] == asset)
                & (settled["horizon"] == horizon)
            ]

            total = len(subset)

            if total:
                wins = (
                    subset["correct"].astype(str).str.lower()
                    == "true"
                ).sum()

                accuracy = wins / total * 100

                x, y, z = st.columns(3)
                x.metric("HORIZON", horizon)
                y.metric("RESOLVED", total)
                z.metric("ACCURACY", f"{accuracy:.1f}%")
            else:
                st.caption(
                    f"{horizon}: Collecting predictions. "
                    "No validated accuracy yet."
                )

    st.subheader("Recent prediction history")

    if not settled.empty:
        st.dataframe(
            settled.tail(30).iloc[::-1],
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info("Waiting for the first forecasts to settle.")

else:
    st.info("Accuracy tracking will begin as forecasts are recorded.")


# =====================================================
# SECONDARY MARKETS
# =====================================================

if show_secondary:
    st.divider()
    st.header("OTHER MARKETS")

    for asset, ticker in SECONDARY.items():
        df = market_frame(ticker)

        if df.empty:
            continue

        close = df["Close"].astype(float).dropna()

        if len(close) < 4:
            continue

        change = (
            close.iloc[-1] / close.iloc[-4] - 1
        ) * 100

        st.metric(
            asset,
            f"${close.iloc[-1]:,.4f}",
            f"{change:+.2f}% / 15m"
        )


# =====================================================
# KALSHI RADAR
# =====================================================

st.divider()
st.header("◈ KALSHI MARKET RADAR")

st.caption(
    "Public contract quotes. These are market-implied prices, "
    "not independent JARVIS predictions."
)

kdf = kalshi_events()

if kdf.empty:
    st.warning("Kalshi public market data unavailable.")
else:
    kdf["Volume"] = pd.to_numeric(
        kdf["Volume"], errors="coerce"
    ).fillna(0)

    kdf = kdf.sort_values(
        "Volume", ascending=False
    ).head(40)

    def quote(value):
        try:
            value = float(value)
            return f"{value:.1f}¢"
        except (ValueError, TypeError):
            return "—"

    kdf["YES BID"] = kdf["YES bid"].map(quote)
    kdf["YES ASK"] = kdf["YES ask"].map(quote)

    st.dataframe(
        kdf[
            ["Market", "YES BID", "YES ASK",
             "Volume", "Close", "Ticker"]
        ],
        use_container_width=True,
        hide_index=True
    )


# =====================================================
# RISK & SYSTEM STATUS
# =====================================================

st.divider()
st.header("SYSTEM STATUS")

st.warning(
    "Experimental model. Probabilities are heuristic estimates, "
    "not calibrated odds or proven trading edges."
)

st.markdown("""
- No automatic bets or trades.
- No deposits or withdrawals.
- No brokerage or Kalshi account access.
- Historical accuracy includes only settled forecasts.
- Data can be delayed or incomplete.
- Prediction history is stored locally in the app's filesystem.
- Free hosting may reset local files; persistent storage or an external database
  will be needed for reliable long-term accuracy tracking.
- Historical accuracy does not guarantee future performance.
""")

st.caption(
    "JARVIS MARKETS BUILD 03 · READ ONLY · "
    + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
)
