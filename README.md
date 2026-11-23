# JARVIS Markets — Build 02

A read-only market dashboard and optional 15-minute background scanner for Kalshi public markets, crypto, stocks, and commodity futures.

## What's included

- Dark JARVIS-style Streamlit dashboard.
- Public Kalshi event/market snapshots (no Kalshi account credentials).
- Crypto, equity, and commodity snapshots through Yahoo Finance via `yfinance`.
- Separate GitHub Actions workflow scheduled every 15 minutes.
- Optional Telegram alerts for unusually large ~15-minute price moves.
- No automated betting/trading, brokerage connection, deposits, or withdrawals.

## Update the existing Streamlit app

Upload/replace the project files in the root of your existing private GitHub repository. Keep `app.py`, `requirements.txt`, `README.md`, `scanner.py`, `requirements-scanner.txt`, and `.github/workflows/scan.yml` at the repository root / listed paths. Streamlit Community Cloud should continue using `app.py` as its main file.

## Enable background scans

1. In your private GitHub repo, open **Settings → Secrets and variables → Actions**.
2. Add repository secrets `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` (optional; omit them to run scans without push alerts).
3. Open **Actions** and enable workflows if GitHub prompts you.
4. Open **JARVIS 15-minute market scan** and choose **Run workflow** for a manual test.
5. Scheduled runs use `*/15 * * * *` and run from the default branch. GitHub may delay scheduled jobs during busy periods; this is not an exact real-time guarantee.

## Telegram setup

Create a bot with Telegram's official `@BotFather`, open the bot and press **Start**, then obtain your chat ID. Never paste the bot token into `app.py`, commit it to GitHub, or share it in chat. Store it only as a GitHub Actions secret.

## Data and signal limitations

- Kalshi uses its public read-only events endpoint. The YES quote is a market quote, not an independent JARVIS probability.
- Yahoo Finance data can be delayed, incomplete, rate-limited, or unavailable. Futures tickers are proxies.
- Asset alerts trigger on absolute price movement of at least 0.8% over approximately 15 minutes. This is a configurable threshold, not a prediction or a validated edge.
- The dashboard's 15-minute refresh is separate from GitHub Actions background scans.
- The workflow uses the lightweight `ubuntu-slim` runner. GitHub Free currently includes $12/month of compute usage for eligible private-repository Actions; actual use depends on run duration and any other Actions usage. A 15-minute schedule means up to 2,880 runs/month, so check usage and enable a hard $0 extra-spend budget before leaving it on. If the included allowance is exhausted, runs may stop rather than incur extra charges when no payment method is on file.
- This project is experimental and paper-only. It does not place trades or bets.
