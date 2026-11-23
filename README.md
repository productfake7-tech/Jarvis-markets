# JARVIS Markets — fast MVP

A free, experimental Streamlit dashboard for 15-minute direction estimates on BTC, ETH, SOL, gold futures, silver futures, and WTI crude futures.

## Run locally (Windows)

1. Install Python 3.11 or newer from https://www.python.org/downloads/ (check "Add Python to PATH").
2. Extract this folder.
3. Open PowerShell in this folder.
4. Run:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
streamlit run app.py
```

5. Open the local URL Streamlit prints (usually http://localhost:8501).

## iPhone access

For a quick local test, your iPhone and laptop must be on the same Wi-Fi. Find the laptop's local IP with `ipconfig`, then run:

```powershell
streamlit run app.py --server.address 0.0.0.0
```

Open `http://LAPTOP-IP:8501` on the iPhone. This is local-network access only; it is not available when the laptop is off or away from Wi-Fi.

For internet access, deploy the folder to a GitHub repository and connect it to Streamlit Community Cloud: https://share.streamlit.io/. The free service may sleep when inactive and is not a guaranteed always-on alert server.

## Free iPhone push alerts

This MVP sends alerts through Telegram (free). This avoids relying on iOS web-push support inside Streamlit.

1. In Telegram, message `@BotFather`, create a bot with `/newbot`, and copy the bot token.
2. Open your new bot and press Start.
3. Find your numeric chat ID using a trusted Telegram bot such as `@userinfobot`, or use Telegram's `getUpdates` API after messaging your bot.
4. Enter the bot token and chat ID in the app sidebar; enable alerts.

Do not share your bot token. If deploying publicly, do not put the token in source code. Use Streamlit Secrets or enter it in the app session. The current sidebar entry is session-only.

## Important limitations

- Yahoo Finance intraday data through `yfinance` is unofficial, can be delayed, incomplete, rate-limited, or unavailable for particular symbols.
- Gold, silver, and WTI tickers are futures proxies, not every broker's spot/CFD instrument.
- The model uses 5-minute bars and predicts whether the close three bars later is above the current close.
- The reported test metrics use a chronological holdout, but this is not a full walk-forward test and does not include fees, spread, slippage, execution, or changing market regimes.
- A probability is not a guarantee and should not be treated as a trade recommendation.
- The app does not place trades and does not request exchange credentials or brokerage access.
- The open dashboard refreshes every 60 seconds and checks for alert conditions. Free hosting is not suitable for guaranteed 24/7 alerts. A later version can add scheduled background jobs and a more reliable market-data provider.
