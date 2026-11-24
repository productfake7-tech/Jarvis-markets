import re
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests
import streamlit as st
from streamlit_autorefresh import st_autorefresh

st.set_page_config(
    page_title="JARVIS Markets",
    page_icon="◈",
    layout="wide",
)

PYTH_BASE = "https://pyth.dourolabs.app/v1"
KALSHI_BASE = "https://external-api.kalshi.com/trade-api/v2"

ASSETS = {
    "Bitcoin": {"symbol": "Crypto.BTC/USD", "emoji": "₿"},
    "Gold": {"query": "gold", "emoji": "Au"},
    "WTI Oil": {"query": "oil", "emoji": "🛢️"},
}

# ---------------- SETTINGS ----------------

refresh_seconds = st.sidebar.selectbox(
    "Refresh interval", [15, 30, 60], index=1
)

st_autorefresh(
    interval=refresh_seconds * 1000,
    key="jarvis_refresh",
)

st.sidebar.title("JARVIS CONTROL")
st.sidebar.caption("READ ONLY — no trading permissions")
st.sidebar.write("ASSET DATA: PYTH PRO")
st.sidebar.write("CONTRACT DATA: KALSHI PUBLIC API")

def get_pyth_key():
    try:
        return str(st.secrets.get("PYTH_API_KEY", "")).strip()
    except Exception:
        return ""

def safe_float(value):
    try:
        value = float(value)
        return value if np.isfinite(value) else None
    except (ValueError, TypeError):
        return None

def format_price(value):
    if value is None:
        return "Unavailable"
    return f"${value:,.4f}" if value < 10 else f"${value:,.2f}"

def format_percent(value):
    return "Unavailable" if value is None else f"{value:.1f}%"

def parse_time(value):
    try:
        return pd.to_datetime(value, utc=True)
    except Exception:
        return None

# ---------------- PYTH SYMBOL DISCOVERY ----------------

@st.cache_data(ttl=300, show_spinner=False)
def discover_pyth_symbol(asset_name, api_key):
    if asset_name == "Bitcoin":
        return "Crypto.BTC/USD", "real_time", None

    query = ASSETS[asset_name]["query"]

    headers = {
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "JARVIS-Markets/3.0",
    }

    try:
        response = requests.get(
            f"{PYTH_BASE}/symbols",
            params={
                "query": query,
                "entitled_only": "true",
            },
            headers=headers,
            timeout=15,
        )

        if response.status_code == 401:
            return None, None, "Pyth rejected the API key (401)."

        if response.status_code == 403:
            return None, None, "Pyth denied symbol catalog access (403)."

        response.raise_for_status()
        data = response.json()

        if isinstance(data, dict):
            symbols = data.get("symbols", data.get("data", []))
        else:
            symbols = data

        if not isinstance(symbols, list):
            return None, None, "Unexpected Pyth symbol catalog response."

        candidates = []

        for item in symbols:
            if not isinstance(item, dict):
                continue

            symbol = str(item.get("symbol", ""))
            name = str(item.get("name", ""))
            description = str(item.get("description", ""))

            searchable = f"{symbol} {name} {description}".lower()

            if asset_name == "Gold":
                valid = (
                    "gold" in searchable
                    or "xau" in searchable
                )
                invalid = any(
                    word in searchable
                    for word in ["golden", "goldfish"]
                )
            else:
                valid = (
                    "wti" in searchable
                    or "crude oil" in searchable
                    or "oil" in searchable
                )
                invalid = any(
                    word in searchable
                    for word in ["olive", "oil company"]
                )

            if valid and not invalid and symbol:
                candidates.append(item)

        if not candidates:
            return (
                None,
                None,
                f"No entitled {asset_name} feed appeared in Pyth's symbol catalog.",
            )

        # Prefer a direct WTI feed for oil and a direct gold/USD feed for gold.
        def rank(item):
            symbol = str(item.get("symbol", "")).lower()

            if asset_name == "Gold":
                return (
                    0 if "gold/usd" in symbol else
                    1 if "xau/usd" in symbol else
                    2
                )

            return (
                0 if "wti" in symbol else
                1 if "crude" in symbol else
                2
            )

        candidates.sort(key=rank)

        selected = candidates[0]
        symbol = selected["symbol"]
        channel = selected.get("min_channel") or "real_time"

        return symbol, channel, None

    except requests.exceptions.RequestException as exc:
        return None, None, f"Pyth catalog connection error: {exc}"
    except Exception as exc:
        return None, None, f"Pyth catalog error: {exc}"

# ---------------- PYTH CANDLES ----------------

@st.cache_data(ttl=10, show_spinner=False)
def get_pyth_candles(symbol, channel, api_key):
    if not api_key:
        return None, "Missing PYTH_API_KEY in Streamlit Secrets."

    now = int(time.time())

    headers = {
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "JARVIS-Markets/3.0",
    }

    params = {
        "symbol": symbol,
        "from": now - 240 * 60,
        "to": now,
        "resolution": "1",
    }

    try:
        response = requests.get(
            f"{PYTH_BASE}/{channel}/history",
            headers=headers,
            params=params,
            timeout=15,
        )

        if response.status_code in (401, 403, 404):
            detail = response.text[:300]
            return None, (
                f"Pyth HTTP {response.status_code}: "
                f"{detail or response.reason}"
            )

        if response.status_code == 429:
            return None, "Pyth rate limit reached (429)."

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
            timestamps, unit="s", utc=True
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

# ---------------- EXPERIMENTAL DIRECTIONAL MODEL ----------------

def calculate_signal(df):
    if df is None or len(df) < 30:
        return None

    close = df["Close"].astype(float)

    ema_fast = close.ewm(span=9, adjust=False).mean()
    ema_slow = close.ewm(span=21, adjust=False).mean()

    latest = float(close.iloc[-1])
    previous = float(close.iloc[-6])

    if previous == 0:
        return None

    momentum = (latest / previous - 1) * 100
    trend = (float(ema_fast.iloc[-1]) / float(ema_slow.iloc[-1]) - 1) * 100

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

    score = float(np.clip(
        50 + momentum * 8 + trend * 120
        + (rsi_value - 50) * 0.08,
        0,
        100,
    ))

    direction = (
        "UP" if score >= 55 else
        "DOWN" if score <= 45 else
        "NEUTRAL"
    )

    return {
        "direction": direction,
        "score": score,
        "momentum": momentum,
        "trend": trend,
        "rsi": rsi_value,
    }

# ---------------- KALSHI MARKET DISCOVERY ----------------

@st.cache_data(ttl=30, show_spinner=False)
def get_open_kalshi_markets():
    markets = []
    cursor = None

    for _ in range(3):
        params = {
            "status": "open",
            "limit": 1000,
            "mve_filter": "exclude",
        }

        if cursor:
            params["cursor"] = cursor

        try:
            response = requests.get(
                f"{KALSHI_BASE}/markets",
                params=params,
                headers={"User-Agent": "JARVIS-Markets/3.0"},
                timeout=15,
            )
            response.raise_for_status()

            data = response.json()
            markets.extend(data.get("markets", []))
            cursor = data.get("cursor")

            if not cursor:
                break

        except Exception as exc:
            return markets, f"Kalshi API error: {exc}"

    return markets, None

def market_close_time(market):
    return (
        parse_time(market.get("close_time"))
        or parse_time(market.get("expected_expiration_time"))
        or parse_time(market.get("expiration_time"))
    )

def find_asset_markets(markets, asset_name):
    now = pd.Timestamp.now(tz="UTC")
    matches = []

    # Search human-readable contract text only.
    # Do not search tickers: ticker fragments create false matches.
    for market in markets:
        title = str(market.get("title", ""))
        subtitle = str(market.get("subtitle", ""))

        searchable = f"{title} {subtitle}".lower()

        if asset_name == "Gold":
            pattern = r"\b(gold|xau|troy ounce)\b"
        elif asset_name == "WTI Oil":
            pattern = r"\b(wti|crude oil|crude|oil price|barrel)\b"
        else:
            pattern = r"\b(bitcoin|btc)\b"

        if not re.search(pattern, searchable):
            continue

        close_time = market_close_time(market)

        if close_time is None or close_time <= now:
            continue

        # Reject obvious sports and player-prop language.
        sports_terms = [
            "touchdown", "passing yards", "rushing yards",
            "receiving yards", "rebounds", "assists",
            "points scored", "quarterback", "nba", "nfl",
            "nhl", "mlb", "soccer", "football",
        ]

        if any(term in searchable for term in sports_terms):
            continue

        market["_jarvis_close"] = close_time
        matches.append(market)

    matches.sort(key=lambda item: item["_jarvis_close"])
    return matches

# ---------------- KALSHI PRICES ----------------

def market_prices(market):
    def read_price(dollar_key, cents_key):
        value = safe_float(market.get(dollar_key))
        if value is not None:
            return value

        cents = safe_float(market.get(cents_key))
        return cents / 100 if cents is not None else None

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

    return {
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "yes_mid": yes_mid,
        "no_mid": 1 - yes_mid if yes_mid is not None else None,
    }

# ---------------- DASHBOARD ----------------

st.title("◈ JARVIS MARKETS")
st.caption("PYTH ASSET MONITOR • KALSHI CONTRACT RADAR • READ ONLY")

st.info(
    "Experimental research tool. Directional scores are not "
    "calibrated probabilities or verified trading signals."
)

api_key = get_pyth_key()

if not api_key:
    st.error("PYTH_API_KEY is missing from Streamlit Secrets.")

# ---------------- ASSET MONITOR ----------------

st.header("1. ASSET MARKET MONITOR")

columns = st.columns(3)
asset_results = {}

for column, (name, config) in zip(columns, ASSETS.items()):
    with column:
        st.subheader(f"{config['emoji']} {name}")

        symbol = config.get("symbol")
        channel = "real_time"
        discovery_error = None

        if name != "Bitcoin" and api_key:
            symbol, channel, discovery_error = discover_pyth_symbol(
                name, api_key
            )

        if discovery_error:
            st.error("Feed discovery failed")
            st.caption(discovery_error)
            asset_results[name] = None
            continue

        if not symbol:
            st.error("No entitled Pyth feed found")
            asset_results[name] = None
            continue

        df, error = get_pyth_candles(
            symbol, channel, api_key
        )

        if df is None or df.empty:
            st.error("Asset data unavailable")
            st.caption(error or "No data returned.")
            st.caption(f"Attempted: {symbol} • {channel}")
            asset_results[name] = None
            continue

        last_time = df.index[-1]
        age = max(
            0,
            int(
                (
                    pd.Timestamp.now(tz="UTC") - last_time
                ).total_seconds()
            ),
        )

        current = float(df["Close"].iloc[-1])
        previous = (
            float(df["Close"].iloc[-2])
            if len(df) > 1 else current
        )

        change = (
            (current / previous - 1) * 100
            if previous else 0
        )

        signal = calculate_signal(df)

        asset_results[name] = {
            "age": age,
            "signal": signal,
        }

        st.metric(
            "PYTH PRICE",
            format_price(current),
            f"{change:+.4f}% (last candle)",
        )

        st.caption(f"Feed: {symbol}")
        st.caption(f"Channel: {channel}")
        st.caption(
            f"Last candle: {last_time.strftime('%H:%M:%S UTC')} "
            f"• Age: {age}s"
        )

        if age > 180:
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

        if signal:
            with st.expander("Technical details"):
                st.write(f"Momentum: {signal['momentum']:+.5f}%")
                st.write(f"EMA trend: {signal['trend']:+.5f}%")
                st.write(f"RSI: {signal['rsi']:.2f}")
                st.caption("Score is not a probability of winning.")

        st.line_chart(df["Close"], height=190)

# ---------------- KALSHI RADAR ----------------

st.divider()
st.header("2. KALSHI LIVE CONTRACT RADAR")

with st.spinner("Scanning Kalshi markets..."):
    kalshi_markets, kalshi_error = get_open_kalshi_markets()

if kalshi_error:
    st.warning(kalshi_error)

st.caption(f"Open markets scanned: {len(kalshi_markets)}")

for name in ASSETS:
    st.subheader(name)

    matches = find_asset_markets(kalshi_markets, name)

    if not matches:
        st.info(
            "No clearly matching open commodity/asset contract found "
            "in the markets scanned."
        )
        continue

    for market in matches[:3]:
        prices = market_prices(market)

        st.write(f"**{market.get('title', 'Untitled contract')}**")
        st.caption(f"Ticker: {market.get('ticker', 'Unknown')}")

        a, b, c, d = st.columns(4)

        a.metric(
            "YES MID",
            format_percent(
                prices["yes_mid"] * 100
                if prices["yes_mid"] is not None else None
            ),
        )
        b.metric(
            "NO MID",
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
            st.write(f"**Close countdown:** {seconds_left:,} seconds")

        st.caption(
            f"Volume: {market.get('volume_fp', market.get('volume', 'Unavailable'))}"
        )

        with st.expander("Contract settlement rules"):
            st.write(
                market.get(
                    "rules_primary",
                    "No primary rules supplied.",
                )
            )
            st.write(market.get("rules_secondary", ""))

        st.markdown("---")

# ---------------- SYSTEM STATUS ----------------

st.divider()
st.header("3. JARVIS SYSTEM STATUS")

for name in ASSETS:
    result = asset_results.get(name)

    if result is None:
        st.write(f"🔴 {name}: No usable Pyth data")
    elif result["age"] > 180:
        st.write(
            f"🟠 {name}: Stale data ({result['age']} seconds old)"
        )
    else:
        st.write(
            f"🟢 {name}: Pyth candles available "
            f"({result['age']} seconds old)"
        )

st.markdown("---")
st.write("**Pyth:** underlying asset price candles.")
st.write("**Kalshi:** contract quotes, not JARVIS predictions.")
st.write("**Timing:** asset candle time and contract close time are separate.")

st.warning(
    "Experimental only. No score guarantees a profitable outcome. "
    "Verify contract settlement rules and test signals against historical "
    "outcomes before making any financial decision."
)

st.caption(
    "JARVIS MARKETS • EXPERIMENTAL • READ ONLY • "
    + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
)
