
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests
import streamlit as st
import yfinance as yf
from streamlit_autorefresh import st_autorefresh

# --------------------------------------------------
# JARVIS MARKETS
# Asset prices + Kalshi 15-minute contracts
# READ ONLY - NO AUTOMATIC TRADES
# --------------------------------------------------

st.set_page_config(
    page_title="JARVIS Markets",
    page_icon="◈",
    layout="wide",
)

KALSHI = "https://external-api.kalshi.com/trade-api/v2"

ASSETS = {
    "Bitcoin": {
        "symbol": "BTC-USD",
        "series": "KXBTC15M",
        "emoji": "₿",
    },
    "Gold": {
        "symbol": "GC=F",
        "series": "KXGOLD15M",
        "emoji": "Au",
    },
    "WTI Oil": {
        "symbol": "CL=F",
        "series": "KXWTI15M",
        "emoji": "🛢️",
    },
}

st_autorefresh(interval=15000, key="jarvis_refresh")


# --------------------------------------------------
# HELPERS
# --------------------------------------------------

def safe_float(value):
    try:
        if value is None:
            return None
        return float(value)
    except (ValueError, TypeError):
        return None


def dollars(value):
    number = safe_float(value)
    return number if number is not None else None


def format_price(value):
    if value is None:
        return "Unavailable"
    return f"${value:,.2f}"


def format_percent(value):
    if value is None:
        return "Unavailable"
    return f"{value:.1f}%"


def parse_time(value):
    if not value:
        return None
    try:
        return pd.to_datetime(value, utc=True)
    except Exception:
        return None


def api_get(path, params=None):
    try:
        response = requests.get(
            KALSHI + path,
            params=params,
            timeout=12,
            headers={"User-Agent": "JARVIS-Markets/1.0"},
        )
        response.raise_for_status()
        return response.json()
    except Exception:
        return None


# --------------------------------------------------
# ASSET PRICE DATA
# --------------------------------------------------

@st.cache_data(ttl=20, show_spinner=False)
def get_asset_data(symbol):
    try:
        df = yf.Ticker(symbol).history(
            period="1d",
            interval="1m",
            prepost=True,
        )

        if df is None or df.empty:
            return None

        df = df.dropna(subset=["Close"]).copy()
        df.index = pd.to_datetime(df.index, utc=True)

        return df.tail(240)

    except Exception:
        return None


def calculate_signal(df):
    if df is None or len(df) < 25:
        return None

    close = df["Close"].astype(float)

    ema_fast = close.ewm(span=9, adjust=False).mean()
    ema_slow = close.ewm(span=21, adjust=False).mean()

    change = close.diff()
    gains = change.clip(lower=0)
    losses = -change.clip(upper=0)

    avg_gain = gains.rolling(14).mean()
    avg_loss = losses.rolling(14).mean().replace(0, np.nan)

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))

    latest = float(close.iloc[-1])
    previous = float(close.iloc[-6])
    momentum = ((latest / previous) - 1) * 100 if previous else 0

    trend = (
        (float(ema_fast.iloc[-1]) / float(ema_slow.iloc[-1])) - 1
    ) * 100

    rsi_value = float(rsi.iloc[-1]) if pd.notna(rsi.iloc[-1]) else 50

    # Experimental directional score, NOT a calibrated probability.
    raw_score = (
        50
        + momentum * 7
        + trend * 35
        + (50 - rsi_value) * -0.08
    )

    probability = float(np.clip(raw_score, 50, 75))

    if probability >= 53:
        direction = "UP"
    elif probability <= 47:
        direction = "DOWN"
    else:
        direction = "NEUTRAL"

    # Keep neutral signals honest.
    if direction == "NEUTRAL":
        probability = 50.0

    return {
        "direction": direction,
        "probability": probability,
        "momentum": momentum,
        "trend": trend,
        "rsi": rsi_value,
        "price": latest,
        "timestamp": df.index[-1],
    }


# --------------------------------------------------
# KALSHI CONTRACT DATA
# --------------------------------------------------

@st.cache_data(ttl=10, show_spinner=False)
def get_kalshi_markets(series):
    data = api_get(
        "/markets",
        {
            "series_ticker": series,
            "status": "open",
            "limit": 200,
        },
    )

    if not data:
        return []

    markets = data.get("markets", [])

    now = datetime.now(timezone.utc)

    valid = []

    for market in markets:
        close_time = parse_time(
            market.get("close_time")
            or market.get("expected_expiration_time")
        )

        if close_time is None:
            continue

        if close_time.to_pydatetime() > now:
            market["_close_dt"] = close_time
            valid.append(market)

    valid.sort(key=lambda m: m["_close_dt"])

    return valid


def get_market_prices(market):
    yes_bid = dollars(market.get("yes_bid_dollars"))
    yes_ask = dollars(market.get("yes_ask_dollars"))
    no_bid = dollars(market.get("no_bid_dollars"))
    no_ask = dollars(market.get("no_ask_dollars"))

    if yes_bid is None:
        cents = safe_float(market.get("yes_bid"))
        yes_bid = cents / 100 if cents is not None else None

    if yes_ask is None:
        cents = safe_float(market.get("yes_ask"))
        yes_ask = cents / 100 if cents is not None else None

    if no_bid is None:
        cents = safe_float(market.get("no_bid"))
        no_bid = cents / 100 if cents is not None else None

    if no_ask is None:
        cents = safe_float(market.get("no_ask"))
        no_ask = cents / 100 if cents is not None else None

    # Derive missing sides from complementary bids/asks.
    if yes_ask is None and no_bid is not None:
        yes_ask = 1 - no_bid

    if no_ask is None and yes_bid is not None:
        no_ask = 1 - yes_bid

    last = dollars(market.get("last_price_dollars"))

    if last is None:
        cents = safe_float(market.get("last_price"))
        last = cents / 100 if cents is not None else None

    yes_mid = None

    if yes_bid is not None and yes_ask is not None:
        yes_mid = (yes_bid + yes_ask) / 2
    elif last is not None:
        yes_mid = last

    no_mid = 1 - yes_mid if yes_mid is not None else None

    return {
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "no_bid": no_bid,
        "no_ask": no_ask,
        "yes_mid": yes_mid,
        "no_mid": no_mid,
        "last": last,
    }


# --------------------------------------------------
# PAGE DESIGN
# --------------------------------------------------

st.title("◈ JARVIS MARKETS")
st.caption(
    "LIVE ASSET MONITOR • KALSHI CONTRACT RADAR • READ ONLY"
)

st.info(
    "Experimental research dashboard. Model estimates are not "
    "verified win probabilities. No automatic orders or bets."
)

st.sidebar.title("JARVIS CONTROL")

refresh = st.sidebar.selectbox(
    "Refresh interval",
    [15, 30, 60],
    index=0,
)

if refresh != 15:
    st.sidebar.caption(
        "The page refreshes every 15 seconds. "
        "Change this setting only as a display preference."
    )

st.sidebar.markdown("---")
st.sidebar.write("DATA SOURCES")
st.sidebar.write("• Yahoo Finance — asset candles")
st.sidebar.write("• Kalshi public API — contracts")
st.sidebar.write("• 1-minute asset chart")
st.sidebar.write("• 15-minute Kalshi markets")


# --------------------------------------------------
# MAIN ASSET DASHBOARD
# --------------------------------------------------

st.header("1. ASSET MARKET MONITOR")

asset_columns = st.columns(3)

asset_results = {}

for column, (name, config) in zip(asset_columns, ASSETS.items()):
    with column:
        st.subheader(f"{config['emoji']} {name}")

        df = get_asset_data(config["symbol"])

        if df is None or df.empty:
            st.error("Asset price unavailable")
            asset_results[name] = None
            continue

        signal = calculate_signal(df)
        asset_results[name] = {
            "df": df,
            "signal": signal,
        }

        current = float(df["Close"].iloc[-1])
        previous = float(df["Close"].iloc[-2]) if len(df) > 1 else current

        change = (
            ((current / previous) - 1) * 100
            if previous else 0
        )

        st.metric(
            "LIVE PRICE",
            format_price(current),
            f"{change:+.3f}%",
        )

        last_time = df.index[-1]
        age = (
            datetime.now(timezone.utc) - last_time.to_pydatetime()
        ).total_seconds()

        st.caption(
            f"Last candle: {last_time.strftime('%H:%M:%S UTC')} "
            f" • Age: {int(max(age, 0))} sec"
        )

        if age > 180:
            st.warning("STALE ASSET DATA — signal disabled")

        if signal is None:
            st.warning("Not enough candles for analysis")
        elif age > 180:
            st.warning("No signal: waiting for fresh data")
        else:
            direction = signal["direction"]

            if direction == "UP":
                st.success(
                    f"JARVIS: UP — {signal['probability']:.1f}%"
                )
            elif direction == "DOWN":
                st.error(
                    f"JARVIS: DOWN — {signal['probability']:.1f}%"
                )
            else:
                st.warning("JARVIS: NEUTRAL — 50.0%")

            st.caption("Experimental model estimate, not calibrated.")

        st.line_chart(
            df["Close"],
            height=180,
        )

        with st.expander("Technical details"):
            if signal:
                st.write(f"Momentum: {signal['momentum']:+.4f}%")
                st.write(f"EMA trend: {signal['trend']:+.4f}%")
                st.write(f"RSI: {signal['rsi']:.2f}")


# --------------------------------------------------
# KALSHI CONTRACT RADAR
# --------------------------------------------------

st.divider()
st.header("2. KALSHI LIVE CONTRACTS")

st.caption(
    "These are actual open contracts from each specific Kalshi series."
)

for name, config in ASSETS.items():

    st.subheader(f"{name} — {config['series']}")

    markets = get_kalshi_markets(config["series"])

    if not markets:
        st.warning(
            "No open contract returned right now. "
            "This can happen outside the asset's trading hours "
            "or between market windows."
        )
        continue

    # The nearest future closing contract is shown first.
    market = markets[0]
    prices = get_market_prices(market)

    title = (
        market.get("title")
        or market.get("subtitle")
        or market.get("ticker")
    )

    st.write(f"**{title}**")
    st.caption(f"Ticker: {market.get('ticker', 'Unknown')}")

    a, b, c, d = st.columns(4)

    a.metric(
        "YES MARKET PRICE",
        format_percent(
            prices["yes_mid"] * 100
            if prices["yes_mid"] is not None else None
        ),
    )

    b.metric(
        "NO MARKET PRICE",
        format_percent(
            prices["no_mid"] * 100
            if prices["no_mid"] is not None else None
        ),
    )

    c.metric(
        "YES BID",
        format_percent(
            prices["yes_bid"] * 100
            if prices["yes_bid"] is not None else None
        ),
    )

    d.metric(
        "YES ASK",
        format_percent(
            prices["yes_ask"] * 100
            if prices["yes_ask"] is not None else None
        ),
    )

    close_time = market.get("_close_dt")

    if close_time is not None:
        seconds_left = (
            close_time.to_pydatetime()
            - datetime.now(timezone.utc)
        ).total_seconds()

        st.write(
            f"**Time until close:** {max(int(seconds_left), 0)} seconds"
        )

    volume = (
        market.get("volume_fp")
        or market.get("volume")
        or 0
    )

    st.caption(f"Volume: {volume}")

    st.markdown("---")

    with st.expander(f"Other open {name} contracts ({len(markets)})"):
        rows = []

        for item in markets:
            p = get_market_prices(item)

            rows.append({
                "Contract": item.get("ticker"),
                "Title": item.get("title") or item.get("subtitle"),
                "YES": (
                    round(p["yes_mid"] * 100, 1)
                    if p["yes_mid"] is not None else None
                ),
                "NO": (
                    round(p["no_mid"] * 100, 1)
                    if p["no_mid"] is not None else None
                ),
                "Closes": str(item.get("close_time", "")),
            })

        if rows:
            st.dataframe(
                pd.DataFrame(rows),
                use_container_width=True,
                hide_index=True,
            )


# --------------------------------------------------
# EXPLANATION / DATA STATUS
# --------------------------------------------------

st.divider()
st.header("3. SIGNAL & DATA STATUS")

st.write(
    "**Asset UP/DOWN estimate:** calculated from the underlying "
    "asset's recent price candles."
)

st.write(
    "**Kalshi YES/NO prices:** market prices for the actual "
    "binary contract. They are not JARVIS's asset prediction."
)

st.write(
    "**Important:** a contract's settlement rules and reference "
    "price can differ from the asset feed displayed above."
)

st.caption(
    f"Last dashboard render: "
    f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
)

st.caption("JARVIS MARKETS • EXPERIMENTAL • READ ONLY")
