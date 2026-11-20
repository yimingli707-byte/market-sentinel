import os
import json
import time
from pathlib import Path
from datetime import datetime, timezone

import ccxt
import pandas as pd
import requests
import yfinance as yf

OUT = Path("signals.json")
STATE_FILE = Path("signal_state.json")
PUSHPLUS_TOKEN = os.getenv("PUSHPLUS_TOKEN", "").strip()

CRYPTO = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
# Yahoo Finance symbols; these are market-data symbols, not OKX instrument IDs.
STOCKS = ["QQQ", "SOXX", "NVDA", "AMD", "AVGO", "GOOGL", "MU", "SNDK", "000660.KS"]

def _rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(100).where(avg_loss != 0, 100).where(avg_gain != 0, 0)

def indicators(df, market):
    df = df.copy().sort_index()
    for n in (20, 50, 200):
        df[f"ema{n}"] = df["close"].ewm(span=n, adjust=False).mean()
    df["rsi14"] = _rsi(df["close"], 14)
    df["vol_avg20"] = df["volume"].rolling(20).mean()
    df["prior_high3"] = df["high"].shift(1).rolling(3).max()

    # Session VWAP: reset each exchange-local trading day for stocks,
    # UTC day for crypto. This is a bar-based approximation using HLC3.
    idx = pd.DatetimeIndex(df.index)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    if market == "美股/韩股":
        # yfinance returns the exchange timezone in the index.
        session_key = idx.date
    else:
        session_key = idx.tz_convert("UTC").date
    typical = (df["high"] + df["low"] + df["close"]) / 3
    work = pd.DataFrame({"typical": typical, "volume": df["volume"]}, index=df.index)
    work["pv"] = work["typical"] * work["volume"]
    work["session"] = session_key
    cum_pv = work.groupby("session")["pv"].cumsum()
    cum_vol = work.groupby("session")["volume"].cumsum().replace(0, float("nan"))
    df["vwap"] = cum_pv / cum_vol

    # OHLCV cannot reveal aggressor-side trades. This is only a directional
    # candle-volume proxy, NOT true exchange Delta/CVD.
    candle_direction = (df["close"] > df["open"]).astype(int) - (df["close"] < df["open"]).astype(int)
    df["delta_proxy"] = candle_direction * df["volume"]
    df["delta_proxy_ratio20"] = (
        df["delta_proxy"].rolling(20).sum() /
        df["volume"].rolling(20).sum().replace(0, float("nan"))
    )
    return df.dropna(subset=["ema20", "ema50", "ema200", "rsi14", "vol_avg20", "vwap"])

def classify(symbol, raw, market):
    if raw is None or len(raw) < 220:
        return {"symbol": symbol, "market": market, "signal": "数据不足", "score": 0,
                "detail": "有效K线少于220根，无法可靠计算EMA200"}
    d = indicators(raw, market)
    if len(d) < 2:
        return {"symbol": symbol, "market": market, "signal": "数据不足", "score": 0,
                "detail": "指标计算失败或有效数据不足"}
    x, p = d.iloc[-1], d.iloc[-2]
    vol_ratio = float(x["volume"] / x["vol_avg20"]) if x["vol_avg20"] > 0 else 0
    rsi = float(x["rsi14"])
    prev_rsi = float(p["rsi14"])
    above_vwap = bool(x["close"] > x["vwap"])
    reclaim_vwap = bool(p["close"] <= p["vwap"] and x["close"] > x["vwap"])
    ema_rising = bool(x["ema20"] > p["ema20"])
    rsi_recovering = bool(rsi > prev_rsi and (prev_rsi < 35 or rsi < 40))
    breakout3 = bool(pd.notna(x["prior_high3"]) and x["close"] > x["prior_high3"])
    delta_proxy = float(x["delta_proxy_ratio20"]) if pd.notna(x["delta_proxy_ratio20"]) else 0.0

    score = 0
    reasons = []
    if rsi < 30:
        score += 25; reasons.append(f"RSI超卖({rsi:.1f})")
    elif rsi < 40:
        score += 15; reasons.append(f"RSI偏弱({rsi:.1f})")
    if rsi_recovering:
        score += 15; reasons.append("RSI开始回升")
    if reclaim_vwap:
        score += 25; reasons.append("本根K线重新站上VWAP")
    elif above_vwap:
        score += 15; reasons.append("价格位于VWAP上方")
    if vol_ratio >= 1.5:
        score += 15; reasons.append(f"成交量为20周期均量的{vol_ratio:.1f}倍")
    elif vol_ratio >= 1.2:
        score += 8; reasons.append(f"成交量放大({vol_ratio:.1f}倍)")
    if ema_rising:
        score += 8; reasons.append("EMA20向上")
    if delta_proxy > 0.10:
        score += 7; reasons.append("K线方向成交量代理值偏正")

    # Signal hierarchy: oversold is an alert, not an entry instruction.
    if above_vwap and reclaim_vwap and rsi_recovering and ema_rising and vol_ratio >= 1.2:
        signal = "反弹启动观察"
    elif above_vwap and breakout3 and ema_rising and vol_ratio >= 1.2:
        signal = "反弹确认观察"
        score = min(100, score + 10)
        reasons.append("收盘价突破此前3根K线高点")
    elif rsi < 30 and not above_vwap:
        signal = "超跌预警"
    elif rsi_recovering and not above_vwap:
        signal = "反弹观察"
    else:
        signal = "观望"

    return {
        "symbol": symbol, "market": market, "signal": signal,
        "score": min(score, 100),
        "close": round(float(x["close"]), 6),
        "rsi14": round(rsi, 2),
        "vwap": round(float(x["vwap"]), 6),
        "above_vwap": above_vwap,
        "volume_ratio": round(vol_ratio, 2),
        "ema20": round(float(x["ema20"]), 6),
        "ema50": round(float(x["ema50"]), 6),
        "ema200": round(float(x["ema200"]), 6),
        "delta_proxy_ratio20": round(delta_proxy, 3),
        "time": datetime.now(timezone.utc).isoformat(),
        "detail": "；".join(reasons) if reasons else "暂未满足反弹条件",
        "data_note": "Delta为K线方向成交量代理值，不是真实逐笔主动买卖Delta"
    }

def fetch_crypto(exchange, symbol):
    rows = exchange.fetch_ohlcv(symbol, timeframe="1h", limit=300)
    frame = pd.DataFrame(rows, columns=["ts","open","high","low","close","volume"])
    frame.index = pd.to_datetime(frame.pop("ts"), unit="ms", utc=True)
    return frame[["open","high","low","close","volume"]].astype(float)

def fetch_stock(symbol):
    hist = yf.Ticker(symbol).history(period="3mo", interval="1h", auto_adjust=True, prepost=False)
    if hist.empty:
        return None
    hist.columns = [str(c).lower() for c in hist.columns]
    frame = hist[["open","high","low","close","volume"]].dropna()
    return frame.astype(float)

def send_push(message):
    if not PUSHPLUS_TOKEN:
        print("PUSHPLUS_TOKEN 未设置，跳过微信推送")
        return
    try:
        response = requests.post(
            "https://www.pushplus.plus/send",
            json={"token": PUSHPLUS_TOKEN, "title": "Market Sentinel 2.0 反弹提醒",
                  "content": message, "template": "html"},
            timeout=15
        )
        print("PushPlus:", response.status_code, response.text[:300])
        response.raise_for_status()
    except Exception as exc:
        print("PushPlus error:", exc)

def load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}

def main():
    results = []
    exchange = ccxt.okx({"enableRateLimit": True})
    for symbol in CRYPTO:
        try:
            results.append(classify(symbol, fetch_crypto(exchange, symbol), "加密货币"))
        except Exception as exc:
            results.append({"symbol": symbol, "market": "加密货币", "signal": "获取失败",
                            "score": 0, "detail": str(exc)[:180]})
        time.sleep(0.3)

    for symbol in STOCKS:
        try:
            results.append(classify(symbol, fetch_stock(symbol), "美股/韩股"))
        except Exception as exc:
            results.append({"symbol": symbol, "market": "美股/韩股", "signal": "获取失败",
                            "score": 0, "detail": str(exc)[:180]})
        time.sleep(1)

    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    # Notify only on a signal-state change, to avoid repeated messages every 15 min.
    old_state = load_state()
    new_state = {}
    notify = []
    alert_states = {"超跌预警", "反弹观察", "反弹启动观察", "反弹确认观察"}
    for item in results:
        symbol = item["symbol"]
        state = item.get("signal", "观望")
        new_state[symbol] = state
        if state in alert_states and old_state.get(symbol) != state:
            notify.append(item)
    STATE_FILE.write_text(json.dumps(new_state, ensure_ascii=False, indent=2), encoding="utf-8")

    if notify:
        lines = []
        for item in notify:
            lines.append(
                f"<b>{item['symbol']}</b>：{item['signal']}｜评分 {item['score']}/100"
                f"<br>收盘/最新价：{item.get('close','-')}｜RSI：{item.get('rsi14','-')}"
                f"｜VWAP：{item.get('vwap','-')}｜量比：{item.get('volume_ratio','-')}"
                f"<br>{item.get('detail','')}"
            )
        send_push("<br><br>".join(lines))
    print(json.dumps(results, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
