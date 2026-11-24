import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests
import streamlit as st
from streamlit_autorefresh import st_autorefresh

# ============================================================
# JARVIS MARKETS 5.0
# Pyth Pro candles + Kalshi public market radar
# READ ONLY: no orders, no bets, no account access.
# ============================================================

st.set_page_config(
    page_title="JARVIS Markets",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="expanded",
)

PYTH_BASE = "https://pyth.dourolabs.app/v1"
KALSHI_BASE = "https://external-api.kalshi.com/trade-api/v2"

ASSETS = {
    "Gold": {
        "asset_type": "metal",
        "kalshi_terms": ("gold", "xau", "troy ounce"),
        "emoji": "Au",
    },
    "WTI Oil": {
        "asset_type": "commodity",
        "kalshi_terms": ("wti", "west texas intermediate", "crude oil", "oil price"),
        "emoji": "Oil",
    },
    "Bitcoin": {
        "asset_type": "crypto",
        "kalshi_terms": ("bitcoin", "btc"),
        "emoji": "₿",
    },
}

SPORTS_EXCLUSIONS = (
    "nba", "nfl", "mlb", "nhl", "soccer", "football", "basketball",
    "baseball", "hockey", "tennis", "golf", "player", "team",
    "match", "tournament", "championship",
)

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "JARVIS-Markets/5.0"})


def get_api_key():
    """Read the key only from Streamlit Cloud Secrets; never display it."""
    try:
        return str(st.secrets.get("PYTH_API_KEY", "")).strip()
    except Exception:
        return ""


def pyth_headers(api_key):
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


@st.cache_data(ttl=300, show_spinner=False)
def get_pyth_catalog(asset_type, entitled_only, api_key):
    """
    One documented /symbols request per asset type.
    No limit/offset pagination: those parameters are not documented
    for this endpoint.
    """
    params = {
        "asset_type": asset_type,
        "entitled_only": "true" if entitled_only else "false",
    }
    response = SESSION.get(
        f"{PYTH_BASE}/symbols",
        params=params,
        headers=pyth_headers(api_key),
        timeout=20,
    )

    if response.status_code == 401:
        return [], "Pyth rejected the API key (HTTP 401). Check PYTH_API_KEY."
    if response.status_code == 403:
        return [], "Pyth denied catalog access (HTTP 403)."
    response.raise_for_status()

    payload = response.json()
    if isinstance(payload, list):
        return payload, None
    if isinstance(payload, dict):
        for key in ("symbols", "data", "results"):
            if isinstance(payload.get(key), list):
                return payload[key], None
    return [], "Pyth returned an unexpected catalog format."


def searchable_text(item):
    return " ".join(
        str(item.get(field, "") or "")
        for field in ("symbol", "name", "description", "base", "quote_currency")
    ).lower()


def feed_matches(item, asset_name):
    text = searchable_text(item)
    if asset_name == "Gold":
        return "gold" in text or "xau" in text
    if asset_name == "WTI Oil":
        # Commodity asset_type is already filtered by Pyth.
        # PYTHOIL is included because some Pyth symbols use that name.
        return any(term in text for term in (
            "wti", "west texas intermediate", "pythoil", "crude oil", "oil/usd"
        ))
    if asset_name == "Bitcoin":
        symbol = str(item.get("symbol", "")).lower()
        return (
            symbol in ("crypto.btc/usd", "crypto.btc/usdt", "btc/usd")
            or "bitcoin" in text
        )
    return False


def feed_rank(item, asset_name):
    symbol = str(item.get("symbol", "")).lower()
    score = 0
    if str(item.get("state", "")).lower() == "stable":
        score += 100
    if asset_name == "Bitcoin" and symbol == "crypto.btc/usd":
        score += 100
    if asset_name == "Gold" and "gold" in symbol:
        score += 50
    if asset_name == "WTI Oil" and "pythoil" in symbol:
        score += 70
    if str(item.get("instrument_type", "")).lower() in ("spot", "index", "future"):
        score += 10
    if "usd" in symbol:
        score += 5
    return score


@st.cache_data(ttl=300, show_spinner=False)
def discover_pyth_feed(asset_name, asset_type, api_key):
    """
    First check feeds entitled to this key. If not found, inspect the
    visible catalog to distinguish 'not entitled' from 'not listed'.
    """
    entitled_error = None

    if api_key:
        entitled, entitled_error = get_pyth_catalog(asset_type, True, api_key)
        entitled_matches = [
            item for item in entitled
            if isinstance(item, dict) and feed_matches(item, asset_name)
        ]
        if entitled_matches:
            selected = max(
                entitled_matches,
                key=lambda item: feed_rank(item, asset_name),
            )
            return selected, "entitled", None

    public, public_error = get_pyth_catalog(asset_type, False, api_key)
    public_matches = [
        item for item in public
        if isinstance(item, dict) and feed_matches(item, asset_name)
    ]

    if public_matches:
        selected = max(
            public_matches,
            key=lambda item: feed_rank(item, asset_name),
        )
        if not api_key:
            return selected, "key_missing", "Add PYTH_API_KEY to Streamlit Secrets to request candles."
        if entitled_error and "HTTP 401" in entitled_error:
            return selected, "key_error", entitled_error
        return selected, "not_entitled", (
            "Feed is listed in Pyth's catalog but is not returned by entitled_only=true. "
            "Your API key may not include this feed."
        )

    if entitled_error and not public_error:
        detail = entitled_error
    elif public_error:
        detail = public_error
    else:
        detail = "No matching feed appeared in Pyth's visible catalog."
    return None, "not_found", detail


@st.cache_data(ttl=20, show_spinner=False)
def get_pyth_candles(symbol, channel, api_key, lookback_minutes=240):
    if not api_key:
        return None, "Missing PYTH_API_KEY in Streamlit Secrets."

    now = int(time.time())
    params = {
        "symbol": symbol,
        "from": now - int(lookback_minutes * 60),
        "to": now,
        "resolution": "1",
    }
    response = SESSION.get(
        f"{PYTH_BASE}/{channel}/history",
        params=params,
        headers=pyth_headers(api_key),
        timeout=20,
    )

    if response.status_code == 401:
        return None, "Pyth rejected the API key (HTTP 401)."
    if response.status_code == 403:
        return None, "Pyth returned HTTP 403: this key is not entitled to this feed/channel."
    if response.status_code == 404:
        return None, "Pyth returned HTTP 404: the catalog symbol was not found on the history endpoint."
    if response.status_code == 400:
        return None, f"Pyth rejected the history request (HTTP 400): {response.text[:250]}"
    response.raise_for_status()

    payload = response.json()
    if payload.get("s") != "ok":
        return None, f"Pyth history status: {payload.get('s', 'unknown')}."

    arrays = [payload.get(key, []) for key in ("t", "o", "h", "l", "c")]
    if not all(isinstance(values, list) for values in arrays):
        return None, "Pyth returned malformed candle arrays."
    if not arrays[0]:
        return None, "Pyth returned no candles for this time window."

    length = min(len(values) for values in arrays)
    if length < 25:
        return None, f"Only {length} one-minute candles returned; not enough for the trend calculation."

    frame = pd.DataFrame({
        "timestamp": arrays[0][:length],
        "open": arrays[1][:length],
        "high": arrays[2][:length],
        "low": arrays[3][:length],
        "close": arrays[4][:length],
    })
    for column in ("open", "high", "low", "close"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["timestamp"] = pd.to_numeric(frame["timestamp"], errors="coerce")
    frame = frame.dropna().drop_duplicates("timestamp").sort_values("timestamp")
    frame = frame[frame["close"] > 0].reset_index(drop=True)

    if len(frame) < 25:
        return None, "Not enough valid candles after cleaning the Pyth response."
    return frame, None


def calculate_signal(frame):
    close = frame["close"].astype(float)
    ema_fast = close.ewm(span=9, adjust=False).mean()
    ema_slow = close.ewm(span=21, adjust=False).mean()

    delta = close.diff()
    gains = delta.clip(lower=0).rolling(14).mean()
    losses = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gains / losses.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    latest_rsi = float(rsi.iloc[-1]) if pd.notna(rsi.iloc[-1]) else 50.0

    ema_gap_pct = (float(ema_fast.iloc[-1]) / float(ema_slow.iloc[-1]) - 1) * 100
    momentum_pct = (float(close.iloc[-1]) / float(close.iloc[-6]) - 1) * 100
    raw_score = 50 + (ema_gap_pct * 180) + ((latest_rsi - 50) * 0.45) + (momentum_pct * 90)
    score = float(np.clip(raw_score, 0, 100))

    if score >= 58:
        direction = "UP"
    elif score <= 42:
        direction = "DOWN"
    else:
        direction = "NEUTRAL"

    return {
        "direction": direction,
        "score": score,
        "rsi": latest_rsi,
        "ema_gap_pct": ema_gap_pct,
        "momentum_pct": momentum_pct,
    }


def format_price(value):
    value = float(value)
    if abs(value) >= 1000:
        return f"${value:,.2f}"
    if abs(value) >= 1:
        return f"${value:,.3f}"
    return f"${value:.6f}"


def kalshi_price_cents(market, dollar_key, cents_key):
    """Handle both current *_dollars fields and legacy cent fields."""
    dollars = market.get(dollar_key)
    if dollars not in (None, ""):
        try:
            return float(dollars) * 100
        except (TypeError, ValueError):
            pass
    cents = market.get(cents_key)
    if cents not in (None, ""):
        try:
            return float(cents)
        except (TypeError, ValueError):
            pass
    return None


def yes_midpoint_cents(market):
    yes_bid = kalshi_price_cents(market, "yes_bid_dollars", "yes_bid")
    yes_ask = kalshi_price_cents(market, "yes_ask_dollars", "yes_ask")
    no_bid = kalshi_price_cents(market, "no_bid_dollars", "no_bid")
    no_ask = kalshi_price_cents(market, "no_ask_dollars", "no_ask")

    if yes_ask is None and no_bid is not None:
        yes_ask = 100 - no_bid
    if yes_bid is None and no_ask is not None:
        yes_bid = 100 - no_ask

    # 0 / 100 commonly represent an absent quote, not a real midpoint.
    valid_bid = yes_bid if yes_bid is not None and 0 < yes_bid < 100 else None
    valid_ask = yes_ask if yes_ask is not None and 0 < yes_ask < 100 else None

    if valid_bid is not None and valid_ask is not None and valid_ask >= valid_bid:
        return (valid_bid + valid_ask) / 2, "bid/ask midpoint"
    if valid_bid is not None:
        return valid_bid, "best YES bid only"
    if valid_ask is not None:
        return valid_ask, "best YES ask only"

    last = kalshi_price_cents(market, "last_price_dollars", "last_price")
    if last is not None and 0 < last < 100:
        return last, "last traded price"
    return None, "no usable quote"


@st.cache_data(ttl=30, show_spinner=False)
def get_kalshi_open_markets():
    markets = []
    cursor = None

    # Kalshi's public endpoint supports up to 1,000 markets per page.
    for _ in range(3):
        params = {"status": "open", "limit": 1000, "mve_filter": "exclude"}
        if cursor:
            params["cursor"] = cursor
        response = SESSION.get(
            f"{KALSHI_BASE}/markets",
            params=params,
            timeout=25,
        )
        response.raise_for_status()
        payload = response.json()
        page = payload.get("markets", [])
        if not isinstance(page, list):
            raise ValueError("Kalshi returned an unexpected markets response.")
        markets.extend(page)
        cursor = payload.get("cursor")
        if not cursor or not page:
            break
    return markets


def market_matches(market, asset_name):
    text = " ".join(str(market.get(key, "") or "") for key in (
        "title", "subtitle", "yes_sub_title", "no_sub_title",
        "rules_primary", "rules_secondary", "ticker",
    )).lower()

    if any(term in text for term in SPORTS_EXCLUSIONS):
        return False

    return any(term in text for term in ASSETS[asset_name]["kalshi_terms"])


def market_close_sort(market):
    return str(market.get("close_time", market.get("expiration_time", "")))


def render_market(market):
    title = market.get("title") or market.get("ticker") or "Untitled market"
    ticker = market.get("ticker", "")
    midpoint, source = yes_midpoint_cents(market)
    close_time = market.get("close_time") or market.get("expiration_time") or "Not provided"

    st.markdown(f"**{title}**")
    if ticker:
        st.caption(ticker)
    if midpoint is None:
        st.write("YES: —  |  NO: —")
    else:
        yes_probability = float(np.clip(midpoint, 0, 100))
        no_probability = 100 - yes_probability
        left, right = st.columns(2)
        left.metric("YES · market-implied", f"{yes_probability:.1f}%")
        right.metric("NO · market-implied", f"{no_probability:.1f}%")
        st.caption(f"Quote source: {source}. These are market prices, not JARVIS predictions.")
    st.caption(f"Closes: {close_time}")
    st.divider()


# ------------------------- SIDEBAR -------------------------

with st.sidebar:
    st.title("◈ JARVIS MARKETS")
    st.caption("Market data monitor · Read only")
    refresh_seconds = st.selectbox("Auto-refresh", [15, 30, 60], index=0)
    st.caption("Prices and public markets refresh automatically.")
    st.divider()
    st.markdown("**Safety mode**")
    st.success("READ ONLY — no automatic bets or orders")
    st.caption("Directional scores are technical indicators, not calibrated probabilities.")

st_autorefresh(interval=refresh_seconds * 1000, key="jarvis_refresh")

api_key = get_api_key()
if not api_key:
    st.warning("PYTH_API_KEY is not configured in Streamlit Cloud → Settings → Secrets. Public Kalshi markets can still load, but Pyth candles need the key.")

st.title("JARVIS Markets")
st.caption(f"Updated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")

# ------------------------- ASSET DATA -------------------------

asset_results = {}
asset_columns = st.columns(3)

for index, asset_name in enumerate(("Gold", "WTI Oil", "Bitcoin")):
    config = ASSETS[asset_name]
    with asset_columns[index]:
        st.subheader(f"{config['emoji']} {asset_name}")
        feed, discovery_status, discovery_message = discover_pyth_feed(
            asset_name, config["asset_type"], api_key
        )

        if feed is None:
            st.error("Feed discovery failed")
            st.caption(discovery_message or "No matching Pyth feed was found.")
            asset_results[asset_name] = None
            continue

        symbol = str(feed.get("symbol", "")).strip()
        channel = str(feed.get("min_channel", "") or "real_time").strip()

        if discovery_status == "not_entitled":
            st.warning("Feed listed, but key entitlement not confirmed")
            st.caption(discovery_message)
            asset_results[asset_name] = None
            continue
        if discovery_status == "key_missing":
            st.warning("Feed found; API key required for candles")
            st.caption(discovery_message)
            asset_results[asset_name] = None
            continue
        if discovery_status == "key_error":
            st.error("Pyth API key rejected")
            st.caption(discovery_message)
            asset_results[asset_name] = None
            continue

        st.caption(f"Feed: {symbol}")
        st.caption(f"Channel: {channel}")

        try:
            frame, candle_error = get_pyth_candles(symbol, channel, api_key)
        except requests.RequestException as exc:
            frame, candle_error = None, f"Pyth network error: {exc}"
        except Exception as exc:
            frame, candle_error = None, f"Pyth data error: {exc}"

        if frame is None:
            st.error("Price data unavailable")
            st.caption(candle_error)
            asset_results[asset_name] = None
            continue

        latest = frame.iloc[-1]
        candle_time = datetime.fromtimestamp(
            int(latest["timestamp"]), tz=timezone.utc
        )
        age_seconds = max(0, int(time.time() - int(latest["timestamp"])))
        st.metric("Pyth price", format_price(latest["close"]))
        st.caption(
            f"Last 1-min candle: {candle_time.strftime('%H:%M:%S UTC')} · "
            f"Age: {age_seconds}s"
        )

        if age_seconds > 180:
            st.warning("STALE DATA — signal disabled")
            st.caption("The latest candle is more than 3 minutes old.")
            asset_results[asset_name] = {
                "frame": frame, "signal": None, "age": age_seconds,
            }
            continue

        signal = calculate_signal(frame)
        if signal["direction"] == "UP":
            st.success(f"JARVIS: UP · Score {signal['score']:.1f}/100")
        elif signal["direction"] == "DOWN":
            st.error(f"JARVIS: DOWN · Score {signal['score']:.1f}/100")
        else:
            st.info(f"JARVIS: NEUTRAL · Score {signal['score']:.1f}/100")

        st.caption(
            f"RSI(14): {signal['rsi']:.1f} · "
            f"EMA spread: {signal['ema_gap_pct']:+.3f}% · "
            f"5-min momentum: {signal['momentum_pct']:+.3f}%"
        )
        asset_results[asset_name] = {
            "frame": frame, "signal": signal, "age": age_seconds,
        }

# ------------------------- KALSHI RADAR -------------------------

st.divider()
st.header("Kalshi Market Radar")
st.caption("Open public contracts only. YES/NO percentages are inferred from available quotes.")

try:
    kalshi_markets = get_kalshi_open_markets()
    st.caption(f"Open markets scanned: {len(kalshi_markets):,}")

    tabs = st.tabs(["Commodities", "Crypto", "All matches"])
    category_map = {
        "Commodities": ("Gold", "WTI Oil"),
        "Crypto": ("Bitcoin",),
        "All matches": ("Gold", "WTI Oil", "Bitcoin"),
    }

    for tab, (tab_name, category) in zip(tabs, category_map.items()):
    with tab:
        matched = []
        for asset_name in category:
            asset_markets = [
                market for market in kalshi_markets
                if isinstance(market, dict) and market_matches(market, asset_name)
            ]
            asset_markets.sort(key=market_close_sort)
            for market in asset_markets[:8]:
                matched.append((asset_name, market))
            if not matched:
                st.info("No clearly matching open contracts were found in the scanned pages.")
            else:
                for asset_name, market in matched:
                    st.markdown(f"**{asset_name}**")
                    render_market(market)

except requests.RequestException as exc:
    st.error(f"Kalshi market feed unavailable: {exc}")
except Exception as exc:
    st.error(f"Kalshi scanner error: {exc}")

# ------------------------- FOOTER -------------------------

st.divider()
st.caption(
    "Data sources: Pyth Pro REST candles and Kalshi public market data. "
    "A directional score is not a probability of winning. "
    "Market-implied YES/NO prices can be stale, wide, or illiquid. "
    "This dashboard does not place trades."
)
