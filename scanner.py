
"""JARVIS Markets - read-only Kalshi scanner."""

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE = "https://api.elections.kalshi.com/trade-api/v2"


def fetch_markets():
    params = urllib.parse.urlencode({
        "status": "open",
        "limit": 100
    })
    url = BASE + "/markets?" + params

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "JARVIS-Markets/1.0"}
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        data = json.loads(response.read().decode("utf-8"))

    return data.get("markets", [])


def main():
    now = datetime.now(timezone.utc)
    markets = fetch_markets()

    results = []

    for market in markets:
        bid = float(market.get("yes_bid_dollars") or 0) * 100
        ask = float(market.get("yes_ask_dollars") or 0) * 100

        results.append({
            "ticker": market.get("ticker", "Unknown"),
            "title": market.get("title", "Untitled"),
            "yes_bid_cents": bid,
            "yes_ask_cents": ask,
            "decision": "NO BET",
            "reason": "Prediction model not validated."
        })

    output = {
        "scanner": "JARVIS Markets",
        "timestamp": now.strftime("%Y-%m-%d %H:%M UTC"),
        "mode": "READ ONLY - NO AUTOMATIC BETS",
        "markets_scanned": len(markets),
        "markets": results
    }

    with open("scanner_results.json", "w", encoding="utf-8") as file:
        json.dump(output, file, indent=2)

    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
