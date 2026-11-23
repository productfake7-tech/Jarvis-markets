import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh
import yfinance as yf
import requests
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import accuracy_score, balanced_accuracy_score, brier_score_loss
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline

st.set_page_config(page_title="JARVIS Markets", page_icon="📈", layout="wide")

ASSETS = {
    "Bitcoin": "BTC-USD",
    "Ethereum": "ETH-USD",
    "Solana": "SOL-USD",
    "Gold futures": "GC=F",
    "Silver futures": "SI=F",
    "WTI crude oil futures": "CL=F",
}

FEATURES = [
    "ret_1", "ret_2", "ret_3", "ret_6",
    "ema_gap", "rsi_14", "volatility_12",
    "range_pct", "volume_change", "volume_z",
]

def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

@st.cache_data(ttl=60, show_spinner=False)
def load_data(ticker):
    # Yahoo Finance intraday data is a convenient free prototype source.
    # Availability, delays and history limits can vary by symbol and market.
    df = yf.download(
        ticker, period="30d", interval="5m",
        auto_adjust=True, progress=False, threads=False
    )
    if df is None or df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.title)
    needed = ["Open", "High", "Low", "Close"]
    if not all(c in df.columns for c in needed):
        return pd.DataFrame()
    if "Volume" not in df.columns:
        df["Volume"] = np.nan
    df = df[~df.index.duplicated(keep="last")].dropna(subset=["Close"])
    return df

def make_features(df):
    x = pd.DataFrame(index=df.index)
    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    volume = df["Volume"].astype(float)

    for n in [1, 2, 3, 6]:
        x[f"ret_{n}"] = close.pct_change(n)
    ema_fast = close.ewm(span=6, adjust=False).mean()
    ema_slow = close.ewm(span=18, adjust=False).mean()
    x["ema_gap"] = (ema_fast / ema_slow) - 1
    x["rsi_14"] = rsi(close)
    x["volatility_12"] = close.pct_change().rolling(12).std()
    x["range_pct"] = (high - low) / close.replace(0, np.nan)
    x["volume_change"] = volume.pct_change().replace([np.inf, -np.inf], np.nan)
    vol_mean = volume.rolling(30).mean()
    vol_std = volume.rolling(30).std()
    x["volume_z"] = (volume - vol_mean) / vol_std.replace(0, np.nan)
    return x.replace([np.inf, -np.inf], np.nan)

def analyze(df):
    # Each bar is 5 minutes; target is whether close is higher after 3 bars.
    feat = make_features(df)
    future_return = df["Close"].shift(-3) / df["Close"] - 1
    dataset = feat.copy()
    dataset["target"] = (future_return > 0).astype(int)
    dataset["future_return"] = future_return
    dataset = dataset.iloc[:-3].dropna(subset=FEATURES + ["target"])

    if len(dataset) < 180 or dataset["target"].nunique() < 2:
        return None

    split = int(len(dataset) * 0.8)
    train, test = dataset.iloc[:split], dataset.iloc[split:]
    if len(test) < 20 or train["target"].nunique() < 2:
        return None

    model = Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("classifier", HistGradientBoostingClassifier(
            max_iter=120, learning_rate=0.06, max_leaf_nodes=7,
            l2_regularization=2.0, random_state=42
        ))
    ])
    model.fit(train[FEATURES], train["target"])
    test_probs = model.predict_proba(test[FEATURES])[:, 1]
    test_preds = (test_probs >= 0.5).astype(int)
    metrics = {
        "accuracy": accuracy_score(test["target"], test_preds),
        "balanced": balanced_accuracy_score(test["target"], test_preds),
        "brier": brier_score_loss(test["target"], test_probs),
        "n_test": len(test),
    }

    # Refit on all labeled history to produce the current estimate.
    final_model = Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("classifier", HistGradientBoostingClassifier(
            max_iter=120, learning_rate=0.06, max_leaf_nodes=7,
            l2_regularization=2.0, random_state=42
        ))
    ])
    final_model.fit(dataset[FEATURES], dataset["target"])
    latest = feat.iloc[[-1]][FEATURES]
    probability_up = float(final_model.predict_proba(latest)[0, 1])
    last_price = float(df["Close"].iloc[-1])
    last_time = df.index[-1]
    return {
        "prob_up": probability_up,
        "price": last_price,
        "timestamp": last_time,
        "metrics": metrics,
        "bars": len(dataset),
    }

def send_telegram(token, chat_id, message):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    response = requests.post(url, data={"chat_id": chat_id, "text": message}, timeout=10)
    response.raise_for_status()

st.title("📈 JARVIS Markets")
st.caption("Experimental 15-minute direction estimates · 5-minute market data · educational prototype")
# Refresh while the dashboard is open so Telegram alerts can be evaluated periodically.
st_autorefresh(interval=60_000, key="jarvis_markets_refresh")

with st.sidebar:
    st.header("Settings")
    selected = st.multiselect(
        "Markets",
        list(ASSETS.keys()),
        default=["Bitcoin", "Ethereum", "Gold futures", "Silver futures", "WTI crude oil futures"]
    )
    threshold = st.slider("Alert confidence threshold", 0.55, 0.90, 0.70, 0.01)
    st.markdown("---")
    st.subheader("iPhone alerts (optional)")
    st.caption("Uses Telegram push notifications. Create a Telegram bot with @BotFather, then enter its token and your chat ID.")
    tg_token = st.text_input("Telegram bot token", type="password")
    tg_chat = st.text_input("Telegram chat ID")
    enable_alerts = st.checkbox("Enable alerts", value=False)
    st.markdown("---")
    if st.button("Refresh market data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.caption("Free data can be delayed, incomplete, or unavailable. App refreshes when opened.")

st.warning(
    "Prototype only: probabilities are model estimates, not verified odds or trading signals. "
    "The current model uses a simple chronological holdout, but market regime changes, fees, "
    "slippage and data quality can make real results worse. Paper-test before risking money."
)

if not selected:
    st.info("Choose at least one market in the sidebar.")
    st.stop()

cards = st.columns(2)
for idx, name in enumerate(selected):
    ticker = ASSETS[name]
    with st.spinner(f"Loading {name}..."):
        df = load_data(ticker)

    with cards[idx % 2]:
        st.subheader(name)
        if df.empty:
            st.error(f"No intraday data returned for {ticker}. This provider may not support this symbol right now.")
            continue

        result = analyze(df)
        st.metric("Latest available price", f"{df['Close'].iloc[-1]:,.4f}")
        if result is None:
            st.info("Not enough clean 5-minute history to train this model yet.")
            continue

        p = result["prob_up"]
        confidence = max(p, 1 - p)
        direction = "UP" if p >= 0.5 else "DOWN"
        if confidence < threshold:
            signal = "NO CLEAR EDGE"
        else:
            signal = direction

        c1, c2 = st.columns(2)
        c1.metric("15-minute direction estimate", signal)
        c2.metric("Model probability of UP", f"{p:.1%}")
        st.progress(float(p))
        st.caption(
            f"Data timestamp: {result['timestamp']} · "
            f"Confidence in selected direction: {confidence:.1%}"
        )

        m = result["metrics"]
        with st.expander("Out-of-sample test (historical holdout)"):
            st.write(f"Chronological test accuracy: **{m['accuracy']:.1%}**")
            st.write(f"Balanced accuracy: **{m['balanced']:.1%}**")
            st.write(f"Brier score (lower is better): **{m['brier']:.3f}**")
            st.write(f"Test observations: **{m['n_test']}**")
            st.caption("One holdout is not proof of a profitable strategy. It does not include execution costs.")

        st.line_chart(df["Close"].tail(120), height=150)

        # Avoid repeatedly sending the same alert on every app rerun.
        alert_key = f"alert_{ticker}"
        alert_signature = f"{result['timestamp']}_{signal}_{round(p, 3)}"
        if enable_alerts and tg_token and tg_chat and signal != "NO CLEAR EDGE":
            if confidence >= threshold and st.session_state.get(alert_key) != alert_signature:
                msg = (
                    f"JARVIS Markets — {name}\n"
                    f"15-minute estimate: {direction}\n"
                    f"Model probability UP: {p:.1%}\n"
                    f"Selected-direction confidence: {confidence:.1%}\n"
                    f"Latest data: {result['timestamp']}\n"
                    "Experimental estimate; not a trading instruction."
                )
                try:
                    send_telegram(tg_token, tg_chat, msg)
                    st.session_state[alert_key] = alert_signature
                    st.success("Telegram alert sent.")
                except Exception as exc:
                    st.error(f"Telegram alert failed: {exc}")

st.markdown("---")
st.caption(
    f"Last page load: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} · "
    "Yahoo Finance via yfinance · 5-minute candles · 3-candle (15-minute) target"
)
