
import time
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests
import streamlit as st
from streamlit_autorefresh import st_autorefresh

# =====================================================
# JARVIS MARKETS — KALSHI BUILD 04
# READ ONLY — NO AUTOMATIC TRADING
# =====================================================

BASE = "https://api.elections.kalshi.com/trade-api/v2"
HISTORY_FILE = Path("kalshi_prediction_history.csv")
REFRESH_SECONDS = 30
MAX_CANDLE_AGE = 150

st.set_page_config(
    page_title="JARVIS Markets",
    page_icon="◈",
    layout="wide"
)

st.markdown("""
<style>
.block-container {max-width:1450px;padding-top:1rem;}
[data-testid="stAppViewContainer"] {
 background:radial-gradient(ellipse at 15% 0%,#10263a,#08111d 55%,#060b13);
}
[data-testid="stSidebar"] {background:#0a1421;}
.hero {
 border:1px solid #24516a;border-radius:18px;padding:22px;
 background:linear-gradient(115deg,#102b3e,#08121f);
}
.eyebrow {letter-spacing:3px;color:#63e6ff;font-size:11px;font-weight:bold;}
.hero h1 {color:#eefaff;font-size:34px;}
.hero p {color:#a9c2d2;}
div[data-testid="stMetric"] {
 background:#0c1b2a;border:1px solid #1b3b51;
 padding:12px;border-radius:12px;
}
</style>
<div class="hero">
 <div class="eyebrow">KALSHI NATIVE DATA • BUILD 04</div>
 <h1>◈ JARVIS MARKETS</h1>
 <p>ONE-MINUTE CONTRACT CANDLES · LIVE DATA CHECKS · READ ONLY</p>
</div>
""", unsafe_allow_html=True)

st_autorefresh(
    interval=REFRESH_SECONDS * 1000,
    key="kalshi_refresh"
)

# =====================================================
# API
# =====================================================

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "JARVIS-Markets/4.0"})


def api_get(path, params=None):
    response = SESSION.get(
        BASE + path,
        params=params,
        timeout=12
    )
    response.raise_for_status()
    return response.json()


@st.cache_data(ttl=10, show_spinner=False)
def get_market(ticker):
    data = api_get(f"/markets/{ticker}")
    return data.get("market", {})


@st.cache_data(ttl=15, show_spinner=False)
def get_candles(series, ticker):
    now = int(time.time())

    data = api_get(
        f"/series/{series}/markets/{ticker}/candlesticks",
        params={
            "start_ts": now - 3 * 60 * 60,
            "end_ts": now,
            "period_interval": 1
        }
    )

    return data.get("candlesticks", [])


@st.cache_data(ttl=30, show_spinner=False)
def get_open_markets():
    data = api_get(
        "/markets",
        params={"status": "open", "limit": 100}
    )
    return data.get("markets", [])


# =====================================================
# HELPERS
# =====================================================

def number(value):
    try:
        result = float(value)
        return result if np.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def dollars_or_cents(market, dollars_key, cents_key):
    value = number(market.get(dollars_key))
    if value is not None:
        return value

    value = number(market.get(cents_key))
    if value is not None:
        return value / 100

    return None


def parse_candles(raw):
    rows = []

    for candle in raw:
        price = candle.get("price") or {}
        yes_bid = candle.get("yes_bid") or {}
        yes_ask = candle.get("yes_ask") or {}

        close = number(price.get("close"))

        # If no traded price exists, use the YES quote midpoint.
        if close is None:
            bid = number(yes_bid.get("close"))
            ask = number(yes_ask.get("close"))

            if bid is not None and ask is not None:
                close = (bid + ask) / 2

        timestamp = (
            candle.get("end_period_ts")
            or candle.get("ts")
            or candle.get("timestamp")
        )

        if close is None or timestamp is None:
            continue

        rows.append({
            "time": pd.to_datetime(
                int(timestamp), unit="s", utc=True
            ),
            "close": close
        })

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df = df.drop_duplicates("time")
    return df.sort_values("time").set_index("time")


def countdown(close_time):
    try:
        end = pd.Timestamp(close_time)

        if end.tzinfo is None:
            end = end.tz_localize("UTC")

        seconds = max(
            0,
            int((end - pd.Timestamp.now(tz="UTC")).total_seconds())
        )

        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)

        return f"{hours:02}:{minutes:02}:{seconds:02}"

    except Exception:
        return "Unavailable"


# =====================================================
# SIGNAL ENGINE
# =====================================================

def analyze_contract(df):
    if df.empty or len(df) < 30:
        return None

    close = df["close"].astype(float)

    ema_fast = close.ewm(span=9, adjust=False).mean()
    ema_slow = close.ewm(span=21, adjust=False).mean()

    delta = close.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)

    avg_gain = gains.ewm(
        alpha=1 / 14, adjust=False
    ).mean()

    avg_loss = losses.ewm(
        alpha=1 / 14, adjust=False
    ).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))

    momentum = (
        close.iloc[-1] / close.iloc[-6] - 1
    ) * 100

    trend = (
        ema_fast.iloc[-1] / ema_slow.iloc[-1] - 1
    ) * 100

    current_rsi = number(rsi.iloc[-1])

    if current_rsi is None:
        return None

    score = (
        0.45 * np.tanh(momentum / 0.30)
        + 0.35 * np.tanh(trend / 0.20)
        + 0.20 * np.tanh((current_rsi - 50) / 18)
    )

    if score >= 0.20:
        signal = "YES PRICE RISING"
    elif score <= -0.20:
        signal = "YES PRICE FALLING"
    else:
        signal = "NO CLEAR DIRECTION"

    return {
        "signal": signal,
        "score": float(score),
        "rsi": current_rsi,
        "momentum": momentum,
        "trend": trend,
        "price": float(close.iloc[-1])
    }


# =====================================================
# HISTORY
# =====================================================

def load_history():
    if HISTORY_FILE.exists():
        try:
            return pd.read_csv(HISTORY_FILE)
        except Exception:
            pass

    return pd.DataFrame(columns=[
        "ticker", "created_at", "entry_price",
        "signal", "target_time", "actual_price",
        "correct"
    ])


def save_prediction(ticker, prediction, history):
    now = pd.Timestamp.now(tz="UTC")

    # Record at most one forecast every five minutes.
    previous = history[history["ticker"] == ticker]

    if not previous.empty:
        latest = pd.to_datetime(
            previous["created_at"], utc=True
        ).max()

        if (now - latest).total_seconds() < 300:
            return history

    row = {
        "ticker": ticker,
        "created_at": now.isoformat(),
        "entry_price": prediction["price"],
        "signal": prediction["signal"],
        "target_time": (
            now + pd.Timedelta(minutes=5)
        ).isoformat(),
        "actual_price": np.nan,
        "correct": ""
    }

    return pd.concat(
        [history, pd.DataFrame([row])],
        ignore_index=True
    )


def settle_history(ticker, candles, history):
    if history.empty or candles.empty:
        return history

    for index, row in history.iterrows():
        if row["ticker"] != ticker:
            continue

        if pd.notna(row["actual_price"]):
            continue

        target = pd.Timestamp(row["target_time"])
        if target.tzinfo is None:
            target = target.tz_localize("UTC")

        eligible = candles[candles.index >= target]

        if eligible.empty:
            continue

        actual_price = float(eligible.iloc[0]["close"])
        entry_price = float(row["entry_price"])

        actual_direction = (
            "YES PRICE RISING"
            if actual_price > entry_price
            else "YES PRICE FALLING"
        )

        predicted = str(row["signal"])

        history.at[index, "actual_price"] = actual_price
        history.at[index, "correct"] = (
            predicted == actual_direction
        )

    return history


# =====================================================
# SIDEBAR
# =====================================================

with st.sidebar:
    st.header("◈ CONTROL")

    ticker = st.text_input(
        "Kalshi market ticker",
        placeholder="Paste exact market ticker"
    ).strip().upper()

    st.caption("Use the full ticker from the Kalshi market page.")

    if st.button("REFRESH DATA", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.divider()
    st.caption("Automatic refresh: 30 seconds")
    st.caption("Execution: DISABLED")
    st.caption("No account access or automatic bets.")


# =====================================================
# MAIN
# =====================================================

now = datetime.now(timezone.utc)

a, b, c = st.columns(3)
a.metric("ENGINE", "BUILD 04")
b.metric("DATA SOURCE", "KALSHI API")
c.metric("REFRESH", "30 SEC")

st.caption(
    "Dashboard time: " + now.strftime("%Y-%m-%d %H:%M:%S UTC")
)

if not ticker:
    st.info(
        "Enter the exact Kalshi contract ticker in the sidebar "
        "to load its one-minute candles and signal."
    )

    st.subheader("Open Kalshi markets")

    try:
        markets = get_open_markets()

        if markets:
            table = pd.DataFrame([{
                "Ticker": m.get("ticker"),
                "Market": m.get("yes_sub_title") or m.get("ticker"),
                "Status": m.get("status"),
                "Close": m.get("close_time"),
                "Volume": m.get("volume_fp", m.get("volume", 0))
            } for m in markets])

            st.dataframe(
                table,
                use_container_width=True,
                hide_index=True
            )
        else:
            st.warning("No open markets returned.")

    except Exception as error:
        st.error(f"Could not load open markets: {error}")

    st.stop()


# =====================================================
# CONTRACT DATA
# =====================================================

try:
    market = get_market(ticker)

    if not market:
        st.error("Kalshi did not return this market ticker.")
        st.stop()

    series = market.get("series_ticker")

    if not series:
        st.error("Market has no series ticker. Cannot load candles.")
        st.stop()

    raw_candles = get_candles(series, ticker)
    candles = parse_candles(raw_candles)

except requests.RequestException as error:
    st.error(f"Kalshi API connection failed: {error}")
    st.stop()

except Exception as error:
    st.error(f"Data error: {error}")
    st.stop()


st.divider()
st.header("⚡ CONTRACT INTELLIGENCE")

st.subheader(
    market.get("yes_sub_title")
    or market.get("ticker")
)

st.caption(f"Contract: {ticker}")

if candles.empty:
    st.error("No one-minute candle data returned.")
    st.stop()

last_candle_time = candles.index[-1]
age = (
    pd.Timestamp.now(tz="UTC") - last_candle_time
).total_seconds()

fresh = 0 <= age <= MAX_CANDLE_AGE

last_price = dollars_or_cents(
    market, "last_price_dollars", "last_price"
)

yes_bid = dollars_or_cents(
    market, "yes_bid_dollars", "yes_bid"
)

yes_ask = dollars_or_cents(
    market, "yes_ask_dollars", "yes_ask"
)

close_time = market.get("close_time")

# Use the latest candle if the quote is unavailable.
if last_price is None:
    last_price = float(candles["close"].iloc[-1])

if yes_bid is not None and yes_ask is not None:
    spread = (yes_ask - yes_bid) * 100
else:
    spread = None

remaining = countdown(close_time)

x, y, z, q = st.columns(4)

x.metric("LAST YES PRICE", f"{last_price * 100:.2f}¢")
y.metric("YES BID", "—" if yes_bid is None else f"{yes_bid * 100:.2f}¢")
z.metric("YES ASK", "—" if yes_ask is None else f"{yes_ask * 100:.2f}¢")
q.metric("TIME TO CLOSE", remaining)

st.metric(
    "LATEST CANDLE AGE",
    f"{max(0, age):.0f} seconds",
    delta="FRESH" if fresh else "STALE",
    delta_color="normal" if fresh else "inverse"
)

st.caption(
    "Last candle timestamp: "
    + last_candle_time.strftime("%Y-%m-%d %H:%M:%S UTC")
)

if not fresh:
    st.error(
        "SIGNALS LOCKED — latest one-minute candle is older than "
        f"{MAX_CANDLE_AGE} seconds. Waiting for fresh data."
    )
    st.stop()

if remaining == "00:00:00":
    st.warning("Contract closing time has passed. Signals disabled.")
    st.stop()

prediction = analyze_contract(candles)

if prediction is None:
    st.warning("Not enough one-minute candles to calculate a signal.")
    st.stop()

st.subheader("JARVIS SIGNAL")

if prediction["signal"] == "YES PRICE RISING":
    st.success(prediction["signal"])
elif prediction["signal"] == "YES PRICE FALLING":
    st.warning(prediction["signal"])
else:
    st.info(prediction["signal"])

st.caption(
    "This describes the direction of the YES contract price, "
    "not the probability that the contract settles YES."
)

m1, m2, m3 = st.columns(3)
m1.metric("MODEL SCORE", f'{prediction["score"]:+.3f}')
m2.metric("RSI", f'{prediction["rsi"]:.1f}')
m3.metric("5-MIN MOMENTUM", f'{prediction["momentum"]:+.3f}%')

if spread is not None:
    st.metric("YES BID-ASK SPREAD", f"{spread:.2f}¢")

st.subheader("One-minute YES contract candles")
st.line_chart(candles["close"].tail(90), height=280)

# =====================================================
# HISTORY AND VALIDATION
# =====================================================

st.divider()
st.header("PREDICTION TRACKER")

history = load_history()

history = settle_history(ticker, candles, history)
history = save_prediction(ticker, prediction, history)

history.to_csv(HISTORY_FILE, index=False)

settled = history[
    (history["ticker"] == ticker)
    & history["actual_price"].notna()
].copy()

if not settled.empty:
    settled["correct_bool"] = (
        settled["correct"].astype(str).str.lower() == "true"
    )

    accuracy = settled["correct_bool"].mean() * 100

    h1, h2, h3 = st.columns(3)
    h1.metric("RESOLVED FORECASTS", len(settled))
    h2.metric("DIRECTIONAL ACCURACY", f"{accuracy:.1f}%")
    h3.metric("CORRECT", int(settled["correct_bool"].sum()))

    st.dataframe(
        settled.tail(30).iloc[::-1],
        use_container_width=True,
        hide_index=True
    )
else:
    st.info(
        "Collecting forecasts. Accuracy will appear only after "
        "five-minute forecasts have actual candle results."
    )

# =====================================================
# SYSTEM STATUS
# =====================================================

st.divider()
st.header("SYSTEM STATUS")

st.write("**Market feed:** Kalshi REST API")
st.write("**Candle interval:** 1 minute")
st.write("**Dashboard refresh:** 30 seconds")
st.write("**Stale-data threshold:** 150 seconds")
st.write("**Automatic trading:** Disabled")
st.write("**Account access:** None")

st.warning(
    "A directional signal is experimental, not a proven edge. "
    "The five-minute tracker measures contract-price direction, "
    "not final settlement accuracy. Do not treat it as a betting instruction."
)

st.caption(
    "JARVIS MARKETS BUILD 04 · READ ONLY · "
    + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
)
