
"""JARVIS Markets — Kalshi market scanner.
Read-only. No betting or trading. Prediction engine not yet enabled.
"""
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE = "https://api.elections.kalshi.com/trade-api/v2"
PAGE_SIZE = 100
MAX_PAGES = 5
TIMEOUT = 15


def get_json(url):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "JARVIS-Markets/0.3"}
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def number(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def parse_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def fetch_markets():
    markets = []
    cursor = None

    for _ in range(MAX_PAGES):
        params = {"status": "open", "limit": PAGE_SIZE}
        if cursor:
            params["cursor"] = cursor

        url = BASE + "/markets?" + urllib.parse.urlencode(params)
        data = get_json(url)
        markets.extend(data.get("markets", []))

        cursor = data.get("cursor")
        if not cursor:
            break

    return markets


def classify_market(market, now):
    close = parse_time(market.get("close_time"))

    if not close:
        return "Longer-term / closing time unknown"

    hours_left = (close - now).total_seconds() / 3600

    if hours_left <= 0:
        return "Closing / expired"
    if hours_left <= 0.5:
        return "15-minute / near-expiry"
    if hours_left <= 2:
        return "Hourly"
    if hours_left <= 24:
        return "Daily"
    return "Longer-term"


def market_summary(market, now):
    bid = number(market.get("yes_bid"))
    ask = number(market.get("yes_ask"))

    if bid > 0 and ask > 0:
        midpoint = (bid + ask) / 2
        spread = ask - bid
        implied = f"{midpoint:.1f}%"
        spread_text = f"{spread:.1f} cents"
    elif ask > 0:
        implied = f"{ask:.1f}% YES ask"
        spread_text = "Unavailable"
    else:
        implied = "Unavailable"
        spread_text = "Unavailable"

    close = market.get("close_time") or "Unknown"

    return {
        "ticker": market.get("ticker", "Unknown"),
        "title": market.get("title", "Untitled market"),
        "category": classify_market(market, now),
        "yes_bid_cents": bid,
        "yes_ask_cents": ask,
        "market_implied_yes_probability": implied,
        "spread": spread_text,
        "volume": number(market.get("volume")),
        "volume_24h": number(market.get("volume_24h")),
        "open_interest": number(market.get("open_interest")),
        "close_time": close,
        "rules": (
            market.get("rules_primary")
            or market.get("rules_primary_doc")
            or "Rules not provided by this response"
        ),
        "jarvis_decision": "NO BET",
        "reason": "Prediction model not yet validated."
    }


def rank_markets(markets, now):
    summaries = [market_summary(m, now) for m in markets]

    active = [
        m for m in summaries
        if m["category"] != "Closing / expired"
    ]

    active.sort(
        key=lambda m: (
            m["volume_24h"],
            m["open_interest"],
            m["volume"]
        ),
        reverse=True
    )

    short_term = [
        m for m in active
        if m["category"] != "Longer-term / closing time unknown"
        and m["category"] != "Longer-term"
    ]

    longer_term = [
        m for m in active
        if m["category"].startswith("Longer-term")
    ]

    return {
        "short_term_markets": short_term[:10],
        "longer_term_markets": longer_term[:10],
        "markets_scanned": len(markets),
        "markets_with_open_quotes": sum(
            1 for m in active if m["yes_bid_cents"] or m["yes_ask_cents"]
        )
    }


def main():
    now = datetime.now(timezone.utc)

    try:
        markets = fetch_markets()
        results = rank_markets(markets, now)

        output = {
            "scanner": "JARVIS Markets",
            "timestamp": now.strftime("%Y-%m-%d %H:%M UTC"),
            "mode": "READ ONLY — NO AUTOMATIC BETS",
            "prediction_model": "NOT ENABLED",
            **results
        }

        print(json.dumps(output, indent=2))

    except Exception as error:
        print(json.dumps({
            "scanner": "JARVIS Markets",
            "status": "ERROR",
            "message": str(error)
        }, indent=2))
        raise


if __name__ == "__main__":
    main()
