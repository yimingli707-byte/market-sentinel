import os, json, time
from pathlib import Path
from datetime import datetime, timezone

import ccxt
import pandas as pd
import requests
import yfinance as yf

OUT = Path("signals.json")
PUSHPLUS_TOKEN = os.getenv("PUSHPLUS_TOKEN", "").strip()

CRYPTO = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
STOCKS = ["QQQ", "SOXX", "NVDA", "MU", "SNDK", "000660.KS"]

def indicators(df):
    df = df.copy()
    for n in (20, 50, 200):
        df[f"ema{n}"] = df["close"].ewm(span=n, adjust=False).mean()
    df["vol_avg20"] = df["volume"].rolling(20).mean()
    df["prior_high20"] = df["high"].shift(1).rolling(20).max()
    df["prior_low20"] = df["low"].shift(1).rolling(20).min()
    return df.dropna()

def classify(symbol, df, market):
    if df is None or len(df) < 220:
        return {"symbol": symbol, "market": market, "signal": "数据不足", "score": 0,
                "detail": "可用K线少于220根"}
    d = indicators(df)
    if d.empty:
        return {"symbol": symbol, "market": market, "signal": "数据不足", "score": 0,
                "detail": "指标计算失败"}
    x, p = d.iloc[-1], d.iloc[-2]
    vol_ratio = float(x["volume"] / x["vol_avg20"]) if x["vol_avg20"] else 0
    long_trend = x["ema20"] > x["ema50"] > x["ema200"]
    short_trend = x["ema20"] < x["ema50"] < x["ema200"]
    breakout = x["close"] > x["prior_high20"]
    breakdown = x["close"] < x["prior_low20"]
    score = 0
    signal = "观望"
    reasons = []
    if long_trend:
        score += 30; reasons.append("EMA20>EMA50>EMA200")
    if short_trend:
        score += 30; reasons.append("EMA20<EMA50<EMA200")
    if vol_ratio >= 1.5:
        score += 25; reasons.append(f"成交量为20周期均量的{vol_ratio:.1f}倍")
    if breakout and long_trend and vol_ratio >= 1.5:
        signal = "突破偏多"; score += 25; reasons.append("收盘价突破前20根K线高点")
    elif breakdown and short_trend and vol_ratio >= 1.5:
        signal = "跌破偏空"; score += 25; reasons.append("收盘价跌破前20根K线低点")
    elif long_trend:
        signal = "趋势偏多"
    elif short_trend:
        signal = "趋势偏空"
    return {
        "symbol": symbol, "market": market, "signal": signal,
        "score": min(score, 100), "close": round(float(x["close"]), 6),
        "volume_ratio": round(vol_ratio, 2),
        "ema20": round(float(x["ema20"]), 6),
        "ema50": round(float(x["ema50"]), 6),
        "ema200": round(float(x["ema200"]), 6),
        "time": datetime.now(timezone.utc).isoformat(),
        "detail": "；".join(reasons) if reasons else "暂未满足趋势或突破条件"
    }

def fetch_crypto(exchange, symbol):
    rows = exchange.fetch_ohlcv(symbol, timeframe="1h", limit=300)
    return pd.DataFrame(rows, columns=["ts","open","high","low","close","volume"])[
        ["open","high","low","close","volume"]
    ]

def fetch_stock(symbol):
    hist = yf.Ticker(symbol).history(period="3mo", interval="1h", auto_adjust=True)
    if hist.empty:
        return None
    hist.columns = [str(c).lower() for c in hist.columns]
    return hist[["open","high","low","close","volume"]].dropna()

def send_push(message):
    if not PUSHPLUS_TOKEN:
        print("PUSHPLUS_TOKEN 未设置，跳过微信推送")
        return
    try:
        r = requests.post(
            "https://www.pushplus.plus/send",
            json={"token": PUSHPLUS_TOKEN, "title": "Market Sentinel 交易监控",
                  "content": message, "template": "html"},
            timeout=15
        )
        print("PushPlus:", r.status_code, r.text[:300])
    except Exception as e:
        print("PushPlus error:", e)

def main():
    results = []
    exchange = ccxt.okx({"enableRateLimit": True})
    for symbol in CRYPTO:
        try:
            results.append(classify(symbol, fetch_crypto(exchange, symbol), "加密货币"))
        except Exception as e:
            results.append({"symbol": symbol, "market": "加密货币", "signal": "获取失败",
                            "score": 0, "detail": str(e)[:180]})
        time.sleep(0.3)

    for symbol in STOCKS:
        try:
            results.append(classify(symbol, fetch_stock(symbol), "美股/韩股"))
        except Exception as e:
            results.append({"symbol": symbol, "market": "美股/韩股", "signal": "获取失败",
                            "score": 0, "detail": str(e)[:180]})
        time.sleep(1)

    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    actionable = [x for x in results if x.get("signal") in ("突破偏多", "跌破偏空")]
    if actionable:
        lines = [f"<b>{x['symbol']}</b>：{x['signal']}，评分 {x['score']}/100<br>{x['detail']}"
                 for x in actionable]
        send_push("<br><br>".join(lines))
    print(json.dumps(results, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
