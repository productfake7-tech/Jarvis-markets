import os
from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st
import yfinance as yf
from streamlit_autorefresh import st_autorefresh

st.set_page_config(page_title="JARVIS Markets", page_icon="◈", layout="wide", initial_sidebar_state="expanded")

ASSETS = {
    "Crypto": {"Bitcoin": "BTC-USD", "Ethereum": "ETH-USD", "Solana": "SOL-USD", "XRP": "XRP-USD"},
    "Stocks": {"NVIDIA": "NVDA", "Apple": "AAPL", "Microsoft": "MSFT", "Tesla": "TSLA", "Amazon": "AMZN", "AMD": "AMD", "Meta": "META"},
    "Commodities": {"Gold futures": "GC=F", "Silver futures": "SI=F", "WTI crude": "CL=F"},
}
KALSHI_URL = "https://api.elections.kalshi.com/trade-api/v2/events"

st.markdown("""
<style>
:root { --cyan:#63e6ff; }
.block-container {padding-top:1.5rem; padding-bottom:2rem; max-width:1500px;}
[data-testid="stAppViewContainer"] {background: radial-gradient(ellipse at 15% 0%, #10263a 0%, #08111d 42%, #060b13 100%);}
[data-testid="stSidebar"] {background:#0a1421; border-right:1px solid #1e3b50;}
.hero {border:1px solid #24516a; border-radius:18px; padding:22px 24px; background:linear-gradient(115deg,rgba(16,43,62,.94),rgba(8,18,31,.85)); box-shadow:0 10px 40px #0005;}
.eyebrow {letter-spacing:3px; color:#63e6ff; font-size:11px; font-weight:700;}
.hero h1 {font-size:34px; margin:4px 0 2px; color:#eefaff; letter-spacing:2px;}
.hero p {color:#a9c2d2; margin:0;}
.panel {background:rgba(13,28,43,.88); border:1px solid #1c4057; border-radius:14px; padding:16px;}
.small-label {color:#8baabd; font-size:11px; letter-spacing:1.5px; text-transform:uppercase;}
.status-dot {display:inline-block;width:8px;height:8px;border-radius:50%;background:#50e3a4;margin-right:7px;box-shadow:0 0 12px #50e3a4;}
div[data-testid="stMetric"] {background:#0c1b2a;border:1px solid #1b3b51;padding:13px;border-radius:12px;}
.stTabs [data-baseweb="tab-list"] {gap:8px;}
.stTabs [data-baseweb="tab"] {background:#0c1b2a;border-radius:8px 8px 0 0;padding:10px 18px;}
</style>
<div class="hero">
 <div class="eyebrow">PERSONAL MARKET INTELLIGENCE • BUILD 02</div>
 <h1>◈ JARVIS MARKETS</h1>
 <p><span class="status-dot"></span>MONITORING CONSOLE &nbsp; / &nbsp; KALSHI · CRYPTO · EQUITIES · COMMODITIES</p>
</div>
""", unsafe_allow_html=True)

st_autorefresh(interval=300_000, key="jarvis_refresh")

with st.sidebar:
    st.markdown("### ◈ CONTROL PANEL")
    st.caption("Dashboard refresh: 5 minutes while this page is open")
    groups = st.multiselect("Asset classes", list(ASSETS), default=list(ASSETS))
    chosen = {}
    for group in groups:
        chosen[group] = st.multiselect(group, list(ASSETS[group]), default=list(ASSETS[group]))
    st.divider()
    st.markdown("**BACKGROUND SCANNER**")
    st.caption("GitHub Actions workflow runs every 15 minutes after you install this project update.")
    st.markdown("**ALERTS**")
    st.caption("Add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID as GitHub repository Actions secrets to enable background alerts.")
    st.divider()
    if st.button("↻ Refresh data now", use_container_width=True):
        st.cache_data.clear(); st.rerun()

# headline status row
m1, m2, m3, m4 = st.columns(4)
m1.metric("SCAN MODE", "15 MIN", "GitHub Actions")
m2.metric("EXECUTION", "PAPER ONLY", "No order access")
m3.metric("DATA MODE", "PUBLIC FEEDS", "No account keys")
m4.metric("LAST PAGE LOAD", datetime.now().strftime("%H:%M:%S"), "Local time")

@st.cache_data(ttl=180, show_spinner=False)
def market_frame(ticker):
    try:
        df = yf.download(ticker, period="5d", interval="5m", auto_adjust=True, progress=False, threads=False)
        if df is None or df.empty: return pd.DataFrame()
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
        return df.dropna(subset=["Close"])
    except Exception:
        return pd.DataFrame()

@st.cache_data(ttl=180, show_spinner=False)
def kalshi_events():
    try:
        r = requests.get(KALSHI_URL, params={"status":"open", "with_nested_markets":"true", "limit":100}, timeout=12)
        r.raise_for_status()
        events = r.json().get("events", [])
        rows=[]
        for event in events:
            for market in event.get("markets", []) or []:
                bid = market.get("yes_bid")
                ask = market.get("yes_ask")
                last = market.get("last_price")
                px = ask if ask is not None else last if last is not None else bid
                rows.append({"Market":market.get("title") or market.get("ticker"), "Yes price":px, "Volume":market.get("volume",0), "Close":market.get("close_time"), "Ticker":market.get("ticker")})
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame()

tab_overview, tab_kalshi, tab_assets, tab_risk = st.tabs(["◈ OVERVIEW", "KALSHI", "MARKET RADAR", "RISK & NOTES"])

with tab_overview:
    st.subheader("Market overview")
    st.caption("Live snapshots from public data providers. Quotes may be delayed, incomplete, or unavailable.")
    if not groups:
        st.info("Select an asset class in the Control Panel.")
    else:
        for group in groups:
            names = chosen.get(group, [])
            if not names: continue
            st.markdown(f"#### {group}")
            cols = st.columns(3)
            for i, name in enumerate(names):
                ticker = ASSETS[group][name]
                with cols[i % 3]:
                    df = market_frame(ticker)
                    if df.empty:
                        st.metric(name, "Unavailable")
                        st.caption(ticker)
                        continue
                    close = df["Close"].astype(float).dropna()
                    price = float(close.iloc[-1])
                    change = float((close.iloc[-1] / close.iloc[-4] - 1) * 100) if len(close) >= 4 else 0.0
                    st.metric(name, f"{price:,.4f}", f"{change:+.2f}% / 15m")
                    st.caption(f"{ticker} · latest bar {df.index[-1]}")
                    st.line_chart(close.tail(48), height=105)

with tab_kalshi:
    st.subheader("Kalshi public market radar")
    st.caption("Read-only public market data. The displayed YES price is a market quote, not JARVIS's independent probability.")
    kdf = kalshi_events()
    if kdf.empty:
        st.warning("Kalshi data is temporarily unavailable or the public endpoint returned no nested markets.")
    else:
        kdf["Volume"] = pd.to_numeric(kdf["Volume"], errors="coerce").fillna(0)
        kdf = kdf.sort_values("Volume", ascending=False).head(40)
        if "Yes price" in kdf:
            kdf["YES quote"] = pd.to_numeric(kdf["Yes price"], errors="coerce").map(lambda x: f"{x/100:.1%}" if pd.notna(x) and x > 1 else (f"{x:.1%}" if pd.notna(x) else "—"))
        st.dataframe(kdf[["Market", "YES quote", "Volume", "Close", "Ticker"]], use_container_width=True, hide_index=True)
        st.caption("Public market quotes can change quickly. Volume is shown as returned by Kalshi.")

with tab_assets:
    st.subheader("Signal monitor")
    st.info("This build shows transparent 15-minute price movement snapshots. A statistically validated prediction model is not enabled in this version; no win-rate or edge is claimed.")
    rows=[]
    for group in groups:
        for name in chosen.get(group, []):
            ticker=ASSETS[group][name]; df=market_frame(ticker)
            if df.empty: continue
            c=df["Close"].astype(float).dropna()
            if len(c)<4: continue
            ret=(float(c.iloc[-1])/float(c.iloc[-4])-1)*100
            rows.append({"Asset":name,"Class":group,"15m move":f"{ret:+.2f}%","Status":"UP MOVE" if ret>0.15 else "DOWN MOVE" if ret < -0.15 else "FLAT"})
    if rows: st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else: st.caption("Choose assets to populate the radar.")

with tab_risk:
    st.subheader("Risk monitor")
    st.warning("JARVIS Markets is an experimental information tool, not financial advice or a betting recommendation.")
    st.markdown("- No automatic bets, trades, deposits, or withdrawals.\n- No brokerage, Kalshi account, or payment credentials are requested.\n- A model probability is not a calibrated probability unless tested out-of-sample over time.\n- Public data may be delayed, rate-limited, or missing.\n- Background scans are best-effort; GitHub Actions can delay scheduled runs.")
    st.markdown("**Build status**")
    st.write("Dashboard: public-data snapshot · Background scanner: configured by workflow · Alerts: optional Telegram secrets · Execution: disabled")

st.divider()
st.caption("JARVIS MARKETS // EXPERIMENTAL BUILD 02 // No automated execution // " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))

st.divider()
st.header("⚡ JARVIS Live Market Scanner")

import json

from pathlib import Path

results_file = Path("scanner_results.json")

if results_file.exists():

    with open(results_file, "r", encoding="utf-8") as f:

        scan_data = json.load(f)

    st.caption(f"Last scan: {scan_data.get('timestamp', 'Unknown')}")

    st.metric("Markets scanned", scan_data.get("markets_scanned", 0))

    markets = scan_data.get("markets", [])

    def get_category(market):

        title = market.get("title", "").lower()

        ticker = market.get("ticker", "").lower()

        if any(x in title for x in ["gold", "silver", "crude oil", "natural gas", "wti"]):

            return "Commodities"

        if any(x in title for x in ["bitcoin", "ethereum", "solana"]) or any(

            x in ticker for x in ["btc", "eth", "sol"]

        ):

            return "Crypto"

        if any(x in title for x in ["s&p", "nasdaq", "dow", "stock"]):

            return "Stocks & Indices"

        return "Other Markets"

    order = {

        "Commodities": 0,

        "Crypto": 1,

        "Stocks & Indices": 2,

        "Other Markets": 3

    }

    markets.sort(key=lambda m: order[get_category(m)])

    selected = st.selectbox(

        "Filter markets",

        ["All Markets", "Commodities", "Crypto", "Stocks & Indices", "Other Markets"]

    )

    filtered = markets if selected == "All Markets" else [

        m for m in markets if get_category(m) == selected

    ]

    for market in filtered:

        with st.container(border=True):

            st.subheader(market.get("title", "Untitled market"))

            st.caption(f"{get_category(market)} · {market.get('ticker', 'Unknown')}")

            bid = float(market.get("yes_bid_cents") or 0)

            ask = float(market.get("yes_ask_cents") or 0)

            if bid > 0 and ask > 0 and ask >= bid:

                yes_probability = (bid + ask) / 2

                no_probability = 100 - yes_probability

                c1, c2 = st.columns(2)

                c1.metric("YES probability", f"{yes_probability:.1f}%")

                c2.metric("NO probability", f"{no_probability:.1f}%")

                st.caption("Estimated from the midpoint of the YES bid and ask. These are market-implied estimates, not JARVIS predictions.")

            else:

                st.info("Probability unavailable — insufficient price data.")

            st.warning("NO BET — JARVIS prediction model is not validated.")

else:

    st.info("Waiting for the first scanner results.")
