
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests
import streamlit as st
from streamlit_autorefresh import st_autorefresh

# ==========================================================
# JARVIS MARKETS — PYTH + KALSHI
# READ ONLY. NO ORDERS. NO AUTOMATIC BETS.
# ==========================================================

st.set_page_config(
    page_title="JARVIS Markets",
    page_icon="◈",
    layout="wide",
)

PYTH_BASE = "https://pyth.dourolabs.app/v1"
KALSHI_BASE = "https://external-api.kalshi.com/trade-api/v2"

ASSETS = {
    "Bitcoin": {
        "symbol": "Crypto.BTC/USD",
        "keywords": ["bitcoin", "btc"],
        "emoji": "₿",
    },
    "Gold": {
        "symbol": "Metal.Index.GOLD/USD",
        "keywords": ["gold", "xau"],
        "emoji": "Au",
    },
    "WTI Oil": {
        "symbol": "Commodities.Index.PYTHOIL/USD",
        "keywords": ["wti", "crude oil", "oil"],
        "emoji": "🛢️",
    },
}

# ==========================================================
# SETTINGS
# ==========================================================

refresh_seconds = st.sidebar.selectbox(
    "Refresh interval",
    [15, 30, 60],
    index=0,
)

st_autorefresh(
    interval=refresh_seconds * 1000,
    key="jarvis_refresh",
)

st.sidebar.title("JARVIS CONTROL")
st.sidebar.caption("READ ONLY — no trading permissions")
st.sidebar.markdown("---")
st.sidebar.write("ASSET DATA: PYTH PRO")
st.sidebar.write("CONTRACT DATA: KALSHI PUBLIC API")
st.sidebar.write("CANDLE SIZE: 1 MINUTE")

# ==========================================================
# HELPERS
# ==========================================================

def safe_float(value):
    try:
        if value is None:
            return None
        number = float(value)
        if not np.isfinite(number):
            return None
        return number
    except (ValueError, TypeError):
        return None


def parse_time(value):
    if not value:
        return None
    try:
        return pd.to_datetime(value, utc=True)
    except Exception:
        return None


def format_price(value):
    if value is None:
        return "Unavailable"
    return f"${value:,.4f}" if value < 10 else f"${value:,.2f}"


def format_percent(value):
    if value is None:
        return "Unavailable"
    return f"{value:.1f}%"


def get_pyth_key():
    try:
        key = st.secrets.get("PYTH_API_KEY", "")
        if key:
            return str(key).strip()
    except Exception:
        pass

    return ""


# ==========================================================
# PYTH ASSET CANDLES
# ==========================================================

@st.cache_data(ttl=10, show_spinner=False)
def get_pyth_candles(symbol, api_key):
    if not api_key:
        return None, "PYTH_API_KEY is missing from Streamlit Secrets."

    now = int(time.time())
    start = now - (240 * 60)

    url = f"{PYTH_BASE}/real_time/history"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "JARVIS-Markets/2.0",
    }

    params = {
        "symbol": symbol,
        "from": start,
        "to": now,
        "resolution": "1",
    }

    try:
        response = requests.get(
            url,
            headers=headers,
            params=params,
            timeout=15,
        )

        if response.status_code == 401:
            return None, "Pyth rejected the API key (401)."

        if response.status_code == 403:
            return None, "Pyth key does not have access to this feed (403)."

        response.raise_for_status()
        data = response.json()

        if data.get("s") != "ok":
            return None, f"Pyth returned: {data.get('s', 'no data')}."

        timestamps = data.get("t", [])
        closes = data.get("c", [])

        if not timestamps or not closes:
            return None, "Pyth returned no candles."

        df = pd.DataFrame({
            "Close": pd.to_numeric(closes, errors="coerce"),
        })

        df["Time"] = pd.to_datetime(
            timestamps,
            unit="s",
            utc=True,
        )

        df = df.dropna(subset=["Close"])
        df = df.set_index("Time").sort_index()
        df = df[~df.index.duplicated(keep="last")]

        if df.empty:
            return None, "No valid candle prices."

        return df.tail(240), None

    except requests.exceptions.Timeout:
        return None, "Pyth request timed out."
    except requests.exceptions.RequestException as exc:
        return None, f"Pyth connection error: {exc}"
    except Exception as exc:
        return None, f"Pyth data error: {exc}"


# ==========================================================
# DIRECTIONAL MODEL
# NOT A CALIBRATED PROBABILITY
# ==========================================================

def calculate_signal(df):
    if df is None or len(df) < 30:
        return None

    close = df["Close"].astype(float)

    ema_fast = close.ewm(
        span=9,
        adjust=False,
    ).mean()

    ema_slow = close.ewm(
        span=21,
        adjust=False,
    ).mean()

    latest = float(close.iloc[-1])
    previous = float(close.iloc[-6])

    if previous == 0:
        return None

    momentum = ((latest / previous) - 1) * 100

    trend = (
        (float(ema_fast.iloc[-1]) / float(ema_slow.iloc[-1])) - 1
    ) * 100

    change = close.diff()
    gains = change.clip(lower=0)
    losses = -change.clip(upper=0)

    avg_gain = gains.rolling(14).mean()
    avg_loss = losses.rolling(14).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))

    rsi_value = (
        float(rsi.iloc[-1])
        if pd.notna(rsi.iloc[-1])
        else 50.0
    )

    # This is a directional score, NOT a win probability.
    raw_score = (
        50
        + momentum * 8
        + trend * 120
        + (rsi_value - 50) * 0.08
    )

    score = float(np.clip(raw_score, 0, 100))

    if score >= 55:
        direction = "UP"
    elif score <= 45:
        direction = "DOWN"
    else:
        direction = "NEUTRAL"

    return {
        "direction": direction,
        "score": score,
        "momentum": momentum,
        "trend": trend,
        "rsi": rsi_value,
        "price": latest,
        "timestamp": df.index[-1],
    }


# ==========================================================
# KALSHI PUBLIC MARKET DISCOVERY
# NO ASSUMED SERIES TICKERS
# ==========================================================

@st.cache_data(ttl=10, show_spinner=False)
def get_open_kalshi_markets():
    all_markets = []
    cursor = None

    # Scan up to three pages of currently open markets.
    for _ in range(3):
        params = {
            "status": "open",
            "limit": 1000,
        }

        if cursor:
            params["cursor"] = cursor

        try:
            response = requests.get(
                f"{KALSHI_BASE}/markets",
                params=params,
                headers={"User-Agent": "JARVIS-Markets/2.0"},
                timeout=15,
            )

            response.raise_for_status()
            data = response.json()

            all_markets.extend(data.get("markets", []))
            cursor = data.get("cursor")

            if not cursor:
                break

        except Exception as exc:
            return all_markets, f"Kalshi API error: {exc}"

    return all_markets, None


def market_close_time(market):
    return (
        parse_time(market.get("close_time"))
        or parse_time(market.get("expected_expiration_time"))
        or parse_time(market.get("expiration_time"))
    )


def find_asset_markets(markets, asset_name):
    keywords = ASSETS[asset_name]["keywords"]
    now = pd.Timestamp.now(tz="UTC")

    matches = []

    for market in markets:
        searchable = " ".join([
            str(market.get("ticker", "")),
            str(market.get("title", "")),
            str(market.get("subtitle", "")),
            str(market.get("event_ticker", "")),
        ]).lower()

        if not any(word in searchable for word in keywords):
            continue

        close_time = market_close_time(market)

        if close_time is None or close_time <= now:
            continue

        market["_jarvis_close"] = close_time
        matches.append(market)

    matches.sort(key=lambda item: item["_jarvis_close"])

    return matches


# ==========================================================
# KALSHI PRICE CONVERSION
# ==========================================================

def market_prices(market):
    def read_price(dollar_key, cents_key):
        value = safe_float(market.get(dollar_key))

        if value is not None:
            return value

        cents = safe_float(market.get(cents_key))

        if cents is not None:
            return cents / 100

        return None

    yes_bid = read_price("yes_bid_dollars", "yes_bid")
    yes_ask = read_price("yes_ask_dollars", "yes_ask")
    no_bid = read_price("no_bid_dollars", "no_bid")
    no_ask = read_price("no_ask_dollars", "no_ask")
    last = read_price("last_price_dollars", "last_price")

    if yes_ask is None and no_bid is not None:
        yes_ask = 1 - no_bid

    if no_ask is None and yes_bid is not None:
        no_ask = 1 - yes_bid

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
    }


# ==========================================================
# DASHBOARD
# ==========================================================

st.title("◈ JARVIS MARKETS")
st.caption(
    "PYTH ASSET MONITOR • KALSHI CONTRACT RADAR • READ ONLY"
)

st.info(
    "Experimental research tool. Directional scores are not "
    "calibrated probabilities and are not verified trading signals."
)

api_key = get_pyth_key()

if not api_key:
    st.error(
        "Pyth API key not found. Add PYTH_API_KEY to "
        "Streamlit Cloud → Settings → Secrets."
    )


# ==========================================================
# ASSET MONITOR
# ==========================================================

st.header("1. ASSET MARKET MONITOR")

asset_columns = st.columns(3)
asset_results = {}

for column, (name, config) in zip(asset_columns, ASSETS.items()):
    with column:
        st.subheader(f"{config['emoji']} {name}")

        df, error = get_pyth_candles(
            config["symbol"],
            api_key,
        )

        if df is None or df.empty:
            st.error("Asset data unavailable")
            if error:
                st.caption(error)
            asset_results[name] = None
            continue

        signal = calculate_signal(df)

        last_time = df.index[-1]
        now = pd.Timestamp.now(tz="UTC")

        age = max(
            0,
            int((now - last_time).total_seconds()),
        )

        current = float(df["Close"].iloc[-1])

        previous = (
            float(df["Close"].iloc[-2])
            if len(df) > 1
            else current
        )

        candle_change = (
            ((current / previous) - 1) * 100
            if previous
            else 0
        )

        asset_results[name] = {
            "df": df,
            "signal": signal,
            "age": age,
        }

        st.metric(
            "PYTH PRICE",
            format_price(current),
            f"{candle_change:+.4f}% (last candle)",
        )

        st.caption(
            f"Feed: {config['symbol']}"
        )

        st.caption(
            f"Last candle: {last_time.strftime('%H:%M:%S UTC')} "
            f"• Age: {age}s"
        )

        stale = age > 180

        if stale:
            st.warning("STALE DATA — SIGNAL DISABLED")
        elif signal is None:
            st.warning("Waiting for enough candles.")
        elif signal["direction"] == "UP":
            st.success(
                f"JARVIS: UP — Score {signal['score']:.1f}/100"
            )
        elif signal["direction"] == "DOWN":
            st.error(
                f"JARVIS: DOWN — Score {signal['score']:.1f}/100"
            )
        else:
            st.warning(
                f"JARVIS: NEUTRAL — Score {signal['score']:.1f}/100"
            )

        if signal and not stale:
            with st.expander("Technical details"):
                st.write(
                    f"5-minute momentum: {signal['momentum']:+.5f}%"
                )
                st.write(
                    f"EMA trend: {signal['trend']:+.5f}%"
                )
                st.write(
                    f"RSI: {signal['rsi']:.2f}"
                )
                st.caption(
                    "Score is an experimental directional indicator, "
                    "not a probability of winning."
                )

        st.line_chart(
            df["Close"],
            height=190,
        )


# ==========================================================
# KALSHI CONTRACT RADAR
# ==========================================================

st.divider()
st.header("2. KALSHI LIVE CONTRACT RADAR")

with st.spinner("Scanning open Kalshi markets..."):
    kalshi_markets, kalshi_error = get_open_kalshi_markets()

if kalshi_error:
    st.warning(kalshi_error)

st.caption(
    f"Open markets returned by Kalshi: {len(kalshi_markets)} "
    "• Up to three pages scanned"
)

for name in ASSETS:
    st.subheader(name)

    matches = find_asset_markets(
        kalshi_markets,
        name,
    )

    if not matches:
        st.warning(
            "No matching open contract found in the markets scanned. "
            "This does not prove that Kalshi has no such contract."
        )
        continue

    for market in matches[:3]:
        prices = market_prices(market)

        title = (
            market.get("title")
            or market.get("subtitle")
            or market.get("ticker")
            or "Untitled contract"
        )

        st.write(f"**{title}**")
        st.caption(
            f"Ticker: {market.get('ticker', 'Unknown')}"
        )

        a, b, c, d = st.columns(4)

        a.metric(
            "YES MID",
            format_percent(
                prices["yes_mid"] * 100
                if prices["yes_mid"] is not None
                else None
            ),
        )

        b.metric(
            "NO MID",
            format_percent(
                prices["no_mid"] * 100
                if prices["no_mid"] is not None
                else None
            ),
        )

        c.metric(
            "YES BID",
            format_percent(
                prices["yes_bid"] * 100
                if prices["yes_bid"] is not None
                else None
            ),
        )

        d.metric(
            "YES ASK",
            format_percent(
                prices["yes_ask"] * 100
                if prices["yes_ask"] is not None
                else None
            ),
        )

        close_time = market.get("_jarvis_close")

        if close_time is not None:
            seconds_left = max(
                0,
                int(
                    (
                        close_time
                        - pd.Timestamp.now(tz="UTC")
                    ).total_seconds()
                ),
            )

            st.write(
                f"**Market close countdown:** {seconds_left:,} seconds"
            )

        st.caption(
            f"Volume: {market.get('volume_fp', market.get('volume', 'Unavailable'))}"
        )

        with st.expander("Contract settlement rules"):
            st.write(
                market.get(
                    "rules_primary",
                    "No primary rules supplied by the API.",
                )
            )
            st.write(
                market.get(
                    "rules_secondary",
                    "",
                )
            )

        st.markdown("---")


# ==========================================================
# SYSTEM STATUS
# ==========================================================

st.divider()
st.header("3. JARVIS SYSTEM STATUS")

for name in ASSETS:
    result = asset_results.get(name)

    if result is None:
        st.write(f"🔴 {name}: No usable Pyth data")
    elif result["age"] > 180:
        st.write(
            f"🟠 {name}: Stale candle data "
            f"({result['age']} seconds old)"
        )
    else:
        st.write(
            f"🟢 {name}: Pyth candles available "
            f"({result['age']} seconds old)"
        )

st.markdown("---")

st.write(
    "**Pyth:** underlying asset price candles. "
    "The Gold and Oil feeds are Pyth index feeds."
)

st.write(
    "**Kalshi:** actual contract quotes. YES/NO prices are "
    "market prices, not JARVIS predictions."
)

st.write(
    "**Timing:** the asset candle timestamp and Kalshi market "
    "close timestamp are tracked separately."
)

st.warning(
    "Before relying on any signal, compare it against historical "
    "outcomes and the exact settlement rules of the contract. "
    "No score guarantees a profitable result."
)

st.caption(
    "JARVIS MARKETS • EXPERIMENTAL • READ ONLY • "
    + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
)
