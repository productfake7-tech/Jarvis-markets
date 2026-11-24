
"""JARVIS Markets — Kalshi read-only market scanner.
No betting, trading, or unvalidated predictions.
"""
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE = "https://external-api.kalshi.com/trade-api/v2"
PAGE_SIZE = 200
MAX_PAGES = 5
TIMEOUT = 20


def get_json(url):
    request = urllib.request.Request(
        url, headers={"User-Agent": "JARVIS-Markets/0.4"}
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_markets():
    markets = []
    cursor = None

    for _ in range(MAX_PAGES):
        params = {"status": "open", "limit": PAGE_SIZE}
        if cursor:
            params["cursor"] = cursor

        url = BASE + "/markets?" + urllib.parse.urlencode(params)
        data = get_json(url)
        page = data.get("markets", [])
        markets.extend(page)

        cursor = data.get("cursor")
        if not cursor or not page:
            break

    # Remove duplicates by market ticker.
    unique = {}
    for market in markets:
        ticker = market.get("ticker")
        if ticker:
            unique[ticker] = market

    return list(unique.values())


def number(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def price_cents(market, dollars_key, cents_key):
    if market.get(dollars_key) is not None:
        return number(market[dollars_key]) * 100
    return number(market.get(cents_key))


def parse_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def classify_market(market, now):
    close = parse_time(market.get("close_time"))
    if not close:
        return "Closing time unknown"

    minutes_left = (close - now).total_seconds() / 60

    if minutes_left <= 0:
        return "Closing / expired"
    if minutes_left <= 30:
        return "Closing within 30m"
    if minutes_left <= 120:
        return "Closing within 2h"
    if minutes_left <= 1440:
        return "Closing within 24h"
    return "Longer-term"


def market_summary(market, now):
    bid = price_cents(market, "yes_bid_dollars", "yes_bid")
    ask = price_cents(market, "yes_ask_dollars", "yes_ask")

    if bid > 0 and ask > 0:
        implied = f"{(bid + ask) / 2:.1f}%"
        spread = f"{ask - bid:.1f} cents"
    elif ask > 0:
        implied = f"{ask:.1f}% YES ask"
        spread = "Unavailable"
    else:
        implied = "Unavailable"
        spread = "Unavailable"

    return {
        "ticker": market.get("ticker", "Unknown"),
        "title": market.get("title", "Untitled market"),
        "category": classify_market(market, now),
        "yes_bid_cents": bid,
        "yes_ask_cents": ask,
        "market_implied_yes_probability": implied,
        "spread": spread,
        "volume": number(market.get("volume_fp", market.get("volume"))),
        "volume_24h": number(
            market.get("volume_24h_fp", market.get("volume_24h"))
        ),
        "open_interest": number(
            market.get("open_interest_fp", market.get("open_interest"))
        ),
        "close_time": market.get("close_time") or "Unknown",
        "rules": market.get("rules_primary") or "Rules not provided",
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
        if m["category"] in (
            "Closing within 30m",
            "Closing within 2h",
            "Closing within 24h"
        )
    ]

    longer_term = [
        m for m in active
        if m["category"] in (
            "Longer-term",
            "Closing time unknown"
        )
    ]

    return {
        "short_term_markets": short_term[:10],
        "longer_term_markets": longer_term[:10],
        "markets_scanned": len(markets),
        "markets_with_open_quotes": sum(
            1 for m in active
            if m["yes_bid_cents"] or m["yes_ask_cents"]
        )
    }


def main():
    now = datetime.now(timezone.utc)
    markets = fetch_markets()
    results = rank_markets(markets, now)

    output = {
        "scanner": "JARVIS Markets",
        "timestamp": now.strftime("%Y-%m-%d %H:%M UTC"),
        "mode": "READ ONLY — NO AUTOMATIC BETS",
        "prediction_model": "NOT ENABLED",
        **results
    }

        with open("scanner_results.json", "w", encoding="utf-8") as file:
        json.dump(output, file, indent=2)

    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
