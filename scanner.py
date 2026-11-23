"""15-minute, read-only market scanner. Standard-library HTTP; Telegram alerts only."""
import os, json, urllib.parse, urllib.request
from datetime import datetime, timezone

TELEGRAM_TOKEN=os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT=os.getenv("TELEGRAM_CHAT_ID", "")
BASE="https://api.elections.kalshi.com/trade-api/v2"
ASSETS={"BTC":"BTC-USD","ETH":"ETH-USD","SOL":"SOL-USD","XRP":"XRP-USD","NVDA":"NVDA","AAPL":"AAPL","MSFT":"MSFT","TSLA":"TSLA","AMZN":"AMZN","AMD":"AMD","META":"META","Gold":"GC=F","Silver":"SI=F","WTI":"CL=F"}
THRESHOLD=float(os.getenv("MOVE_ALERT_THRESHOLD_PCT", "0.8"))

def get_json(url, timeout=12):
    req=urllib.request.Request(url, headers={"User-Agent":"JARVIS-Markets/0.2"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))

def telegram(message):
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT):
        print("Telegram secrets not configured; alert skipped."); return
    url=f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    body=urllib.parse.urlencode({"chat_id":TELEGRAM_CHAT,"text":message}).encode()
    req=urllib.request.Request(url, data=body, method="POST")
    with urllib.request.urlopen(req, timeout=12) as response:
        response.read()

def scan_assets():
    hits=[]
    for name,ticker in ASSETS.items():
        try:
            url=f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(ticker)}?range=1d&interval=5m"
            data=get_json(url, timeout=10)["chart"]["result"]
            if not data: continue
            values=data[0]["indicators"]["quote"][0]["close"]
            values=[float(x) for x in values if x is not None]
            if len(values)<4: continue
            move=(values[-1]/values[-4]-1)*100
            if abs(move)>=THRESHOLD:
                hits.append(f"{name} ({ticker}): {move:+.2f}% over ~15m")
        except Exception as e:
            print(f"{ticker}: {e}")
    return hits

def scan_kalshi():
    try:
        url=BASE+"/events?"+urllib.parse.urlencode({"status":"open","with_nested_markets":"true","limit":100})
        events=get_json(url).get("events",[])
        markets=[]
        for ev in events:
            markets.extend(ev.get("markets",[]) or [])
        markets.sort(key=lambda m: float(m.get("volume") or 0), reverse=True)
        return [f"{m.get('title') or m.get('ticker')} | YES quote: {m.get('yes_ask', m.get('last_price','n/a'))}¢ | volume: {m.get('volume',0)}" for m in markets[:5]]
    except Exception as e:
        print(f"Kalshi scan error: {e}"); return []

if __name__=="__main__":
    now=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    asset_hits=scan_assets(); kalshi=scan_kalshi()
    print(json.dumps({"timestamp":now,"asset_moves":asset_hits,"kalshi_top_volume":kalshi},indent=2))
    if asset_hits:
        telegram("JARVIS MARKETS — unusual 15m move(s)\n"+now+"\n\n"+"\n".join(asset_hits)+"\n\nMovement alert only; not a trade signal.")
