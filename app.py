
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests
import streamlit as st
from streamlit_autorefresh import st_autorefresh

# ============================================================
# JARVIS MARKETS — BUILD 06
# KALSHI DATA | READ ONLY | NO AUTOMATIC TRADING
# ============================================================

BASE = "https://api.elections.kalshi.com/trade-api/v2"

REFRESH_SECONDS = 30
CANDLE_CACHE_SECONDS = 10
MARKET_CACHE_SECONDS = 60
MAX_CANDLE_AGE = 150

st.set_page_config(
    page_title="JARVIS Markets",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
.block-container {
    max-width: 1550px;
    padding-top: 1rem;
}
[data-testid="stAppViewContainer"] {
    background: radial-gradient(
        ellipse at 15% 0%,
        #10263a,
        #08111d 55%,
        #060b13
    );
}
[data-testid="stSidebar"] {
    background: #0a1421;
}
.hero {
    border: 1px solid #24516a;
    border-radius: 18px;
    padding: 22px;
    background: linear-gradient(115deg,#102b3e,#08121f);
}
.eyebrow {
    letter-spacing: 3px;
    color: #63e6ff;
    font-size: 11px;
    font-weight: bold;
}
.hero h1 {
    color: #eefaff;
    font-size: 35px;
    letter-spacing: 2px;
}
.hero p {
    color: #a9c2d2;
}
div[data-testid="stMetric"] {
    background: #0c1b2a;
    border: 1px solid #1b3b51;
    padding: 12px;
    border-radius: 12px;
}
</style>

<div class="hero">
    <div class="eyebrow">KALSHI NATIVE DATA • BUILD 06</div>
    <h1>◈ JARVIS MARKETS</h1>
    <p>BITCOIN · GOLD · WTI OIL · YES/NO INTELLIGENCE</p>
</div>
""", unsafe_allow_html=True)

st_autorefresh(
    interval=REFRESH_SECONDS * 1000,
    key="jarvis_refresh"
)

# ============================================================
# API CONNECTION
# ============================================================

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "JARVIS-Markets/6.0"
})


def api_get(path, params=None):
    response = SESSION.get(
        BASE + path,
        params=params,
        timeout=15
    )
    response.raise_for_status()
    return response.json()


# ============================================================
# MARKET DISCOVERY
# ============================================================

@st.cache_data(ttl=MARKET_CACHE_SECONDS, show_spinner=False)
def get_open_markets():
    markets = []
    cursor = None

    # Up to 10,000 markets, subject to available pages.
    for _ in range(10):
        params = {
            "status": "open",
            "limit": 1000
        }

        if cursor:
            params["cursor"] = cursor

        data = api_get("/markets", params)

        markets.extend(data.get("markets", []))

        cursor = data.get("cursor")

        if not cursor:
            break

    return markets


def market_text(market):
    fields = [
        market.get("ticker", ""),
        market.get("title", ""),
        market.get("subtitle", ""),
        market.get("yes_sub_title", ""),
        market.get("event_ticker", ""),
        market.get("rules_primary", ""),
        market.get("rules_secondary", "")
    ]

    return " ".join(
        str(value) for value in fields if value
    ).lower()


def categorize_market(market):
    text = market_text(market)

    oil_terms = [
        "wti", "crude oil",
        "west texas intermediate",
        "oil price", "crude"
    ]

    gold_terms = [
        "gold price", "gold futures",
        "gold above", "gold below",
        "gold", "xau"
    ]

    bitcoin_terms = [
        "bitcoin", "btc"
    ]

    if any(term in text for term in oil_terms):
        return "WTI Oil"

    if any(term in text for term in gold_terms):
        return "Gold"

    if any(term in text for term in bitcoin_terms):
        return "Bitcoin"

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
        "volume_24h_fp",
        "volume_24h",
        "volume_fp",
        "volume"
    ]:
        try:
            return float(market.get(key) or 0)
        except (ValueError, TypeError):
            continue

    return 0.0


# ============================================================
# PRICE HELPERS
# ============================================================

def number(value):
    try:
        result = float(value)
        if np.isfinite(result):
            return result
    except (ValueError, TypeError):
        pass

    return None


def dollars_or_cents(market, dollar_key, cent_key):
    value = number(market.get(dollar_key))

    if value is not None:
        return value

    value = number(market.get(cent_key))

    if value is not None:
        return value / 100

    return None


def cents(value):
    if value is None:
        return "—"

    return f"{value * 100:.1f}¢"


def volume_display(value):
    try:
        return f"{float(value):,.0f}"
    except (ValueError, TypeError):
        return "0"


# ============================================================
# INDIVIDUAL MARKET DATA
# ============================================================

@st.cache_data(ttl=10, show_spinner=False)
def get_market(ticker):
    data = api_get(f"/markets/{ticker}")
    return data.get("market", {})


@st.cache_data(ttl=CANDLE_CACHE_SECONDS, show_spinner=False)
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


# ============================================================
# CANDLE PARSER
# ============================================================

def candle_close(obj):
    if not isinstance(obj, dict):
        return None

    for key in ["close_dollars", "close"]:
        value = number(obj.get(key))
        if value is not None:
            return value

    return None


def parse_candles(raw):
    rows = []

    for candle in raw:
        price = candle.get("price") or {}
        bid = candle.get("yes_bid") or {}
        ask = candle.get("yes_ask") or {}

        close = candle_close(price)

        # Fallback to YES quote midpoint.
        if close is None:
            bid_close = candle_close(bid)
            ask_close = candle_close(ask)

            if bid_close is not None and ask_close is not None:
                close = (bid_close + ask_close) / 2

        if close is None:
            continue

        timestamp = (
            candle.get("end_period_ts")
            or candle.get("period_start_ts")
            or candle.get("ts")
            or candle.get("timestamp")
        )

        if timestamp is None:
            continue

        rows.append({
            "time": pd.to_datetime(
                int(timestamp),
                unit="s",
                utc=True
            ),
            "YES price": close
        })

    if not rows:
        return pd.DataFrame()

    frame = pd.DataFrame(rows)

    frame = frame.drop_duplicates("time")

    return (
        frame.sort_values("time")
        .set_index("time")
    )


# ============================================================
# MARKET-IMPLIED PROBABILITY
# ============================================================

def market_probability(market):
    bid = dollars_or_cents(
        market,
        "yes_bid_dollars",
        "yes_bid"
    )

    ask = dollars_or_cents(
        market,
        "yes_ask_dollars",
        "yes_ask"
    )

    last = dollars_or_cents(
        market,
        "last_price_dollars",
        "last_price"
    )

    if bid is not None and ask is not None and bid <= ask:
        midpoint = (bid + ask) / 2
        source = "YES bid/ask midpoint"

    elif last is not None:
        midpoint = last
        source = "Last traded YES price"

    else:
        return None

    return {
        "yes": float(np.clip(midpoint * 100, 0, 100)),
        "no": float(100 - midpoint * 100),
        "bid": bid,
        "ask": ask,
        "last": last,
        "source": source
    }


# ============================================================
# EXPERIMENTAL MODEL
# ============================================================

def analyze_contract(frame):
    if frame.empty or len(frame) < 30:
        return None

    close = frame["YES price"].astype(float)

    ema_fast = close.ewm(
        span=9,
        adjust=False
    ).mean()

    ema_slow = close.ewm(
        span=21,
        adjust=False
    ).mean()

    delta = close.diff()

    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)

    avg_gain = gains.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    avg_loss = losses.ewm(
        alpha=1 / 14,
        adjust=False
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

    # Experimental score mapping, NOT calibrated odds.
    model_yes = float(
        np.clip(50 + 30 * np.tanh(score), 20, 80)
    )

    model_no = 100 - model_yes

    if model_yes >= 55:
        decision = "YES"
    elif model_no >= 55:
        decision = "NO"
    else:
        decision = "NEUTRAL"

    return {
        "decision": decision,
        "yes": model_yes,
        "no": model_no,
        "score": float(score),
        "rsi": float(current_rsi),
        "momentum": float(momentum),
        "trend": float(trend),
        "price": float(close.iloc[-1])
    }


# ============================================================
# COUNTDOWN
# ============================================================

def countdown(close_time):
    try:
        end = pd.Timestamp(close_time)

        if end.tzinfo is None:
            end = end.tz_localize("UTC")

        seconds = max(
            0,
            int(
                (
                    end - pd.Timestamp.now(tz="UTC")
                ).total_seconds()
            )
        )

        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)

        return f"{hours:02}:{minutes:02}:{seconds:02}"

    except Exception:
        return "Unavailable"


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:
    st.header("◈ JARVIS CONTROL")

    st.success("KALSHI DATA CONNECTED")

    category = st.selectbox(
        "MARKET CATEGORY",
        [
            "Bitcoin",
            "Gold",
            "WTI Oil",
            "Other",
            "All markets"
        ]
    )

    search = st.text_input(
        "SEARCH MARKETS",
        placeholder="Search BTC, price, ticker..."
    ).strip().lower()

    sort_by = st.selectbox(
        "SORT MARKETS",
        ["Volume", "Closing soonest", "Name"]
    )

    st.divider()

    if st.button(
        "↻ REFRESH NOW",
        use_container_width=True
    ):
        st.cache_data.clear()
        st.rerun()

    st.caption("Dashboard refresh: 30 seconds")
    st.caption("Contract candles: 1 minute")
    st.caption("Automatic trading: DISABLED")


# ============================================================
# TOP STATUS
# ============================================================

now = datetime.now(timezone.utc)

a, b, c, d = st.columns(4)

a.metric("ENGINE", "BUILD 06")
b.metric("DATA SOURCE", "KALSHI")
c.metric("REFRESH", "30 SEC")
d.metric(
    "UTC TIME",
    now.strftime("%H:%M:%S")
)


# ============================================================
# LOAD MARKETS
# ============================================================

st.divider()
st.header("◈ LIVE KALSHI MARKET BROWSER")

try:
    with st.spinner("Loading Kalshi markets..."):
        markets = get_open_markets()

except Exception as error:
    st.error(f"Kalshi market connection failed: {error}")
    st.stop()

if not markets:
    st.warning("No open markets were returned.")
    st.stop()


for market in markets:
    market["_category"] = categorize_market(market)


# ============================================================
# CATEGORY COUNTS
# ============================================================

counts = {
    name: sum(
        m["_category"] == name
        for m in markets
    )
    for name in [
        "Bitcoin",
        "Gold",
        "WTI Oil",
        "Other"
    ]
}

count_cols = st.columns(4)

for col, name in zip(
    count_cols,
    ["Bitcoin", "Gold", "WTI Oil", "Other"]
):
    col.metric(name.upper(), counts[name])

st.caption(
    f"{len(markets):,} open contracts loaded from available pages."
)


# ============================================================
# FILTER MARKETS
# ============================================================

filtered = markets

if category != "All markets":
    filtered = [
        m for m in filtered
        if m["_category"] == category
    ]

if search:
    filtered = [
        m for m in filtered
        if search in market_text(m)
    ]


if sort_by == "Volume":
    filtered.sort(
        key=market_volume,
        reverse=True
    )

elif sort_by == "Name":
    filtered.sort(
        key=lambda m: market_title(m).lower()
    )

else:
    filtered.sort(
        key=lambda m: m.get("close_time") or "9999"
    )


st.subheader(
    f"{category.upper()} MARKETS — {len(filtered):,} RESULTS"
)

if not filtered:
    st.warning(
        "No matching contracts found. Try All markets "
        "or a different search."
    )
    st.stop()


# ============================================================
# MARKET TABLE
# ============================================================

table_rows = []

for market in filtered:
    probability = market_probability(market)

    yes_bid = (
        cents(probability["bid"])
        if probability else "—"
    )

    yes_ask = (
        cents(probability["ask"])
        if probability else "—"
    )

    yes_probability = (
        f'{probability["yes"]:.1f}%'
        if probability else "—"
    )

    table_rows.append({
        "Market": market_title(market),
        "YES probability": yes_probability,
        "YES bid": yes_bid,
        "YES ask": yes_ask,
        "Volume": volume_display(market_volume(market)),
        "Closes": market.get("close_time", "—"),
        "Ticker": market.get("ticker", "")
    })

st.dataframe(
    pd.DataFrame(table_rows),
    use_container_width=True,
    hide_index=True
)


# ============================================================
# CONTRACT SELECTOR
# ============================================================

st.divider()
st.header("⚡ SELECT MARKET TO ANALYZE")

ticker_options = [
    m.get("ticker")
    for m in filtered
    if m.get("ticker")
]

selected_ticker = st.selectbox(
    "CHOOSE A KALSHI CONTRACT",
    ticker_options,
    format_func=lambda ticker: (
        next(
            (
                market_title(m)
                for m in filtered
                if m.get("ticker") == ticker
            ),
            ticker
        ) + " | " + ticker
    )
)

# ============================================================
# FETCH SELECTED CONTRACT
# ============================================================

try:
    market = get_market(selected_ticker)

    if not market:
        st.error("Could not find selected contract.")
        st.stop()

    series = market.get("series_ticker")

    if not series:
        st.error("Contract is missing its series ticker.")
        st.stop()

    raw = get_candles(series, selected_ticker)
    candles = parse_candles(raw)

except requests.RequestException as error:
    st.error(f"Contract data connection failed: {error}")
    st.stop()

except Exception as error:
    st.error(f"Contract data error: {error}")
    st.stop()


# ============================================================
# CONTRACT INTELLIGENCE
# ============================================================

st.divider()
st.header("⚡ CONTRACT INTELLIGENCE")

st.subheader(market_title(market))

st.caption(f"Ticker: {selected_ticker}")

if candles.empty:
    st.warning(
        "No one-minute candle history available for this contract."
    )
    st.stop()

last_candle_time = candles.index[-1]

age = (
    pd.Timestamp.now(tz="UTC") - last_candle_time
).total_seconds()

fresh = 0 <= age <= MAX_CANDLE_AGE

probability = market_probability(market)

last_price = (
    probability["last"]
    if probability and probability["last"] is not None
    else float(candles["YES price"].iloc[-1])
)

close_time = market.get("close_time")

bid = probability["bid"] if probability else None
ask = probability["ask"] if probability else None

spread = (
    (ask - bid) * 100
    if bid is not None and ask is not None
    else None
)


# ============================================================
# LIVE QUOTE METRICS
# ============================================================

x, y, z, q = st.columns(4)

x.metric("LAST YES PRICE", cents(last_price))
y.metric("YES BID", cents(bid))
z.metric("YES ASK", cents(ask))
q.metric("TIME TO CLOSE", countdown(close_time))

if probability:
    st.subheader("KALSHI MARKET-IMPLIED PROBABILITY")

    p1, p2 = st.columns(2)

    p1.metric(
        "YES PROBABILITY",
        f'{probability["yes"]:.1f}%'
    )

    p2.metric(
        "NO PROBABILITY",
        f'{probability["no"]:.1f}%'
    )

    st.progress(probability["yes"] / 100)

    st.caption(
        "Source: " + probability["source"]
        + ". Based on the YES midpoint or last trade."
    )

else:
    st.warning(
        "Current YES quote unavailable. Market-implied "
        "probability cannot be calculated."
    )


# ============================================================
# DATA SYNCHRONIZATION
# ============================================================

st.subheader("DATA SYNCHRONIZATION")

s1, s2 = st.columns(2)

s1.metric(
    "LATEST CANDLE AGE",
    f"{max(0, age):.0f} seconds"
)

s2.metric(
    "LATEST CANDLE",
    last_candle_time.strftime("%H:%M:%S UTC")
)

if not fresh:
    st.error(
        "SIGNAL LOCKED — candle is stale or has an invalid "
        "timestamp. JARVIS will not display a fresh signal."
    )
    st.stop()

if countdown(close_time) == "00:00:00":
    st.warning("Contract close time has passed. Signal disabled.")
    st.stop()


# ============================================================
# JARVIS PREDICTION
# ============================================================

prediction = analyze_contract(candles)

st.divider()
st.header("◈ JARVIS YES / NO PREDICTION")

if prediction is None:
    st.warning(
        "Not enough candle history to calculate the experimental model."
    )
    st.stop()

if prediction["decision"] == "YES":
    st.success("JARVIS DECISION: YES")

elif prediction["decision"] == "NO":
    st.error("JARVIS DECISION: NO")

else:
    st.info("JARVIS DECISION: NEUTRAL")


m1, m2, m3 = st.columns(3)

m1.metric(
    "MODEL YES ESTIMATE",
    f'{prediction["yes"]:.1f}%'
)

m2.metric(
    "MODEL NO ESTIMATE",
    f'{prediction["no"]:.1f}%'
)

m3.metric(
    "MODEL SCORE",
    f'{prediction["score"]:+.3f}'
)

st.progress(prediction["yes"] / 100)

st.caption(
    f'RSI: {prediction["rsi"]:.1f} | '
    f'Momentum: {prediction["momentum"]:+.3f}% | '
    f'Trend: {prediction["trend"]:+.3f}%'
)

st.warning(
    "MODEL ESTIMATES ARE EXPERIMENTAL AND UNCALIBRATED. "
    "They are not proven probabilities of final YES/NO settlement."
)


# ============================================================
# CHART
# ============================================================

st.divider()
st.header("ONE-MINUTE CONTRACT PRICE CHART")

st.line_chart(
    candles["YES price"].tail(90),
    height=300
)


# ============================================================
# RECENT MARKET DATA
# ============================================================

with st.expander("VIEW RECENT CANDLE DATA"):
    recent = candles.tail(30).copy()

    recent.index = recent.index.strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )

    st.dataframe(
        recent.iloc[::-1],
        use_container_width=True
    )


# ============================================================
# SYSTEM STATUS
# ============================================================

st.divider()
st.header("SYSTEM STATUS")

st.write("**Market discovery:** Kalshi paginated API")
st.write("**Price feed:** Kalshi contract candles")
st.write("**Candle interval:** 1 minute")
st.write("**Dashboard refresh:** 30 seconds")
st.write("**Stale-data threshold:** 150 seconds")
st.write("**YES/NO market probability:** Kalshi quotes")
st.write("**JARVIS model:** Experimental")
st.write("**Automatic trading:** Disabled")
st.write("**Account access:** None")

st.caption(
    "JARVIS MARKETS BUILD 06 · READ ONLY · "
    + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
)
