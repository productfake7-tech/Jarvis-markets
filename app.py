
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests
import streamlit as st
from streamlit_autorefresh import st_autorefresh

BASE = "https://api.elections.kalshi.com/trade-api/v2"
REFRESH_SECONDS = 30
MAX_CANDLE_AGE = 150

st.set_page_config(
    page_title="JARVIS Markets",
    page_icon="◈",
    layout="wide"
)

st.markdown("""
<style>
.block-container {max-width:1500px;padding-top:1rem;}
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
 <div class="eyebrow">KALSHI NATIVE DATA • BUILD 05</div>
 <h1>◈ JARVIS MARKETS</h1>
 <p>BITCOIN · GOLD · WTI OIL · ALL KALSHI MARKETS</p>
</div>
""", unsafe_allow_html=True)

st_autorefresh(
    interval=REFRESH_SECONDS * 1000,
    key="jarvis_refresh"
)

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "JARVIS-Markets/5.0"})


# =====================================================
# API CONNECTION
# =====================================================

def api_get(path, params=None):
    response = SESSION.get(
        BASE + path,
        params=params,
        timeout=15
    )
    response.raise_for_status()
    return response.json()


@st.cache_data(ttl=30, show_spinner=False)
def get_open_markets():
    """Fetch multiple pages of currently open Kalshi markets."""
    all_markets = []
    cursor = None

    for _ in range(5):
        params = {
            "status": "open",
            "limit": 1000
        }

        if cursor:
            params["cursor"] = cursor

        data = api_get("/markets", params)
        all_markets.extend(data.get("markets", []))

        cursor = data.get("cursor")

        if not cursor:
            break

    return all_markets


@st.cache_data(ttl=10, show_spinner=False)
def get_market(ticker):
    data = api_get(f"/markets/{ticker}")
    return data.get("market", {})


@st.cache_data(ttl=10, show_spinner=False)
def get_candles(series, ticker):
    now = int(time.time())

    data = api_get(
        f"/series/{series}/markets/{ticker}/candlesticks",
        {
            "start_ts": now - 3 * 60 * 60,
            "end_ts": now,
            "period_interval": 1
        }
    )

    return data.get("candlesticks", [])


# =====================================================
# MARKET DISCOVERY AND CATEGORIES
# =====================================================

def market_text(market):
    fields = [
        market.get("ticker", ""),
        market.get("title", ""),
        market.get("subtitle", ""),
        market.get("yes_sub_title", ""),
        market.get("event_ticker", ""),
    ]
    return " ".join(str(x) for x in fields if x).lower()


def categorize_market(market):
    text = market_text(market)

    bitcoin_terms = [
        "bitcoin", "btc", "crypto price"
    ]

    gold_terms = [
        "gold", "xau"
    ]

    oil_terms = [
        "wti", "crude oil", "oil price",
        "west texas intermediate"
    ]

    if any(term in text for term in bitcoin_terms):
        return "Bitcoin"

    if any(term in text for term in gold_terms):
        return "Gold"

    if any(term in text for term in oil_terms):
        return "WTI Oil"

    return "Other"


def market_title(market):
    return (
        market.get("title")
        or market.get("yes_sub_title")
        or market.get("ticker")
        or "Untitled market"
    )


def market_volume(market):
    for key in [
        "volume_24h_fp", "volume_24h",
        "volume_fp", "volume"
    ]:
        try:
            return float(market.get(key) or 0)
        except (ValueError, TypeError):
            pass
    return 0


def dollars_or_cents(market, dollars_key, cents_key):
    try:
        value = market.get(dollars_key)
        if value is not None:
            return float(value)
    except (ValueError, TypeError):
        pass

    try:
        value = market.get(cents_key)
        if value is not None:
            return float(value) / 100
    except (ValueError, TypeError):
        pass

    return None


# =====================================================
# CANDLE DATA
# =====================================================

def parse_candles(raw):
    rows = []

    for candle in raw:
        price = candle.get("price") or {}
        bid = candle.get("yes_bid") or {}
        ask = candle.get("yes_ask") or {}

        close = price.get("close")

        try:
            close = float(close) if close is not None else None
        except (ValueError, TypeError):
            close = None

        if close is None:
            try:
                bid_close = float(bid["close"])
                ask_close = float(ask["close"])
                close = (bid_close + ask_close) / 2
            except (KeyError, ValueError, TypeError):
                continue

        timestamp = (
            candle.get("end_period_ts")
            or candle.get("ts")
            or candle.get("timestamp")
        )

        if timestamp is None:
            continue

        rows.append({
            "time": pd.to_datetime(
                int(timestamp), unit="s", utc=True
            ),
            "YES price": close
        })

    if not rows:
        return pd.DataFrame()

    frame = pd.DataFrame(rows)
    frame = frame.drop_duplicates("time")
    return frame.sort_values("time").set_index("time")


def countdown(close_time):
    try:
        end = pd.Timestamp(close_time)

        if end.tzinfo is None:
            end = end.tz_localize("UTC")

        seconds = max(
            0,
            int(
                (end - pd.Timestamp.now(tz="UTC")).total_seconds()
            )
        )

        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)

        return f"{hours:02}:{minutes:02}:{seconds:02}"

    except Exception:
        return "Unavailable"


# =====================================================
# EXPERIMENTAL CONTRACT ANALYSIS
# =====================================================

def analyze_contract(frame):
    if frame.empty or len(frame) < 30:
        return None

    close = frame["YES price"].astype(float)

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

    current_rsi = rsi.iloc[-1]

    if not np.isfinite(current_rsi):
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
        "rsi": float(current_rsi),
        "momentum": float(momentum),
        "trend": float(trend)
    }


# =====================================================
# HEADER AND MARKET BROWSER
# =====================================================

now = datetime.now(timezone.utc)

a, b, c = st.columns(3)
a.metric("ENGINE", "BUILD 05")
b.metric("DATA SOURCE", "KALSHI")
c.metric("REFRESH", "30 SECONDS")

st.caption(
    "Dashboard time: "
    + now.strftime("%Y-%m-%d %H:%M:%S UTC")
)

st.divider()
st.header("◈ LIVE MARKET BROWSER")

try:
    with st.spinner("Loading open Kalshi markets..."):
        markets = get_open_markets()

except Exception as error:
    st.error(f"Could not load Kalshi markets: {error}")
    st.stop()

if not markets:
    st.warning("Kalshi returned no open markets.")
    st.stop()

for market in markets:
    market["_jarvis_category"] = categorize_market(market)

counts = {
    category: sum(
        m["_jarvis_category"] == category for m in markets
    )
    for category in ["Bitcoin", "Gold", "WTI Oil", "Other"]
}

count_cols = st.columns(4)

for col, category in zip(
    count_cols, ["Bitcoin", "Gold", "WTI Oil", "Other"]
):
    col.metric(category.upper(), counts[category])

st.caption(
    f"Loaded {len(markets):,} open markets from Kalshi."
)

category = st.selectbox(
    "MARKET CATEGORY",
    ["Bitcoin", "Gold", "WTI Oil", "Other", "All markets"]
)

search = st.text_input(
    "SEARCH MARKETS",
    placeholder="Search a market, ticker, price, event..."
).strip().lower()

filtered = markets

if category != "All markets":
    filtered = [
        m for m in filtered
        if m["_jarvis_category"] == category
    ]

if search:
    filtered = [
        m for m in filtered
        if search in market_text(m)
    ]

filtered.sort(
    key=market_volume,
    reverse=True
)

st.subheader(f"{category} — {len(filtered):,} markets")

if not filtered:
    st.warning(
        "No open markets match this category/search. "
        "Try All markets or a different search."
    )
    st.stop()

table_rows = []

for m in filtered:
    bid = dollars_or_cents(
        m, "yes_bid_dollars", "yes_bid"
    )
    ask = dollars_or_cents(
        m, "yes_ask_dollars", "yes_ask"
    )

    table_rows.append({
        "Market": market_title(m),
        "YES bid": "—" if bid is None else f"{bid * 100:.1f}¢",
        "YES ask": "—" if ask is None else f"{ask * 100:.1f}¢",
        "Volume": market_volume(m),
        "Closes": m.get("close_time", "—"),
        "Ticker": m.get("ticker", "")
    })

st.dataframe(
    pd.DataFrame(table_rows),
    use_container_width=True,
    hide_index=True
)

# =====================================================
# CONTRACT SELECTOR
# =====================================================

st.divider()
st.header("⚡ SELECT A CONTRACT")

ticker_options = [
    m.get("ticker")
    for m in filtered
    if m.get("ticker")
]

selected_ticker = st.selectbox(
    "Choose a market to analyze",
    ticker_options,
    format_func=lambda t: (
        next(
            (
                market_title(m)
                for m in filtered
                if m.get("ticker") == t
            ),
            t
        ) + "  |  " + t
    )
)

selected_market = next(
    m for m in filtered
    if m.get("ticker") == selected_ticker
)

st.caption(f"Selected ticker: {selected_ticker}")

# =====================================================
# SELECTED CONTRACT INTELLIGENCE
# =====================================================

try:
    market = get_market(selected_ticker)

    series = market.get("series_ticker")

    if not series:
        st.error("This contract has no series ticker.")
        st.stop()

    raw = get_candles(series, selected_ticker)
    candles = parse_candles(raw)

except Exception as error:
    st.error(f"Could not load contract data: {error}")
    st.stop()

st.divider()
st.header("CONTRACT INTELLIGENCE")
st.subheader(market_title(market))

if candles.empty:
    st.warning("No one-minute candles are available for this contract.")
    st.stop()

last_time = candles.index[-1]
age = (
    pd.Timestamp.now(tz="UTC") - last_time
).total_seconds()

fresh = 0 <= age <= MAX_CANDLE_AGE

last_price = dollars_or_cents(
    market, "last_price_dollars", "last_price"
)

bid = dollars_or_cents(
    market, "yes_bid_dollars", "yes_bid"
)

ask = dollars_or_cents(
    market, "yes_ask_dollars", "yes_ask"
)

if last_price is None:
    last_price = float(candles["YES price"].iloc[-1])

close_time = market.get("close_time")

x, y, z, q = st.columns(4)

x.metric("LAST YES", f"{last_price * 100:.1f}¢")
y.metric("YES BID", "—" if bid is None else f"{bid * 100:.1f}¢")
z.metric("YES ASK", "—" if ask is None else f"{ask * 100:.1f}¢")
q.metric("TIME TO CLOSE", countdown(close_time))

st.metric(
    "LATEST CANDLE AGE",
    f"{max(0, age):.0f} seconds",
    delta="FRESH" if fresh else "STALE",
    delta_color="normal" if fresh else "inverse"
)

st.caption(
    "Last candle: "
    + last_time.strftime("%Y-%m-%d %H:%M:%S UTC")
)

if not fresh:
    st.error(
        "SIGNAL LOCKED: contract candle is stale. "
        "Waiting for fresh market data."
    )
    st.stop()

prediction = analyze_contract(candles)

if prediction is None:
    st.warning("Not enough candles for analysis.")
    st.stop()

if prediction["signal"] == "YES PRICE RISING":
    st.success(prediction["signal"])
elif prediction["signal"] == "YES PRICE FALLING":
    st.warning(prediction["signal"])
else:
    st.info(prediction["signal"])

st.caption(
    "This signal describes YES contract price direction. "
    "It does not predict the probability of settlement."
)

m1, m2, m3 = st.columns(3)
m1.metric("MODEL SCORE", f'{prediction["score"]:+.3f}')
m2.metric("RSI", f'{prediction["rsi"]:.1f}')
m3.metric("MOMENTUM", f'{prediction["momentum"]:+.3f}%')

st.subheader("ONE-MINUTE CONTRACT PRICE CHART")
st.line_chart(candles["YES price"].tail(90), height=280)

# =====================================================
# SAFETY
# =====================================================

st.divider()
st.header("SYSTEM STATUS")

st.write("Market discovery: Paginated Kalshi API")
st.write("Market categories: Bitcoin / Gold / WTI Oil / Other")
st.write("Contract candles: 1 minute")
st.write("Refresh: 30 seconds")
st.write("Stale-data lock: 150 seconds")
st.write("Automatic trading: DISABLED")

st.warning(
    "Experimental directional analysis only. "
    "No proven predictive edge or guaranteed accuracy. "
    "Do not use this signal alone to place a bet."
)

st.caption(
    "JARVIS MARKETS BUILD 05 · READ ONLY · "
    + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
)
