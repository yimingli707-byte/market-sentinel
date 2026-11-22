import os
import json
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta

import ccxt
import pandas as pd
import requests
import yfinance as yf

OUT = Path("signals.json")
STATE_FILE = Path("signal_state.json")
PUSHPLUS_TOKEN = os.getenv("PUSHPLUS_TOKEN", "").strip()
COOLDOWN_HOURS = 6

CRYPTO = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
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
    df["prior_low3"] = df["low"].shift(1).rolling(3).min()
    prev_close = df["close"].shift(1)
    tr = pd.concat([(df["high"] - df["low"]), (df["high"] - prev_close).abs(),
                    (df["low"] - prev_close).abs()], axis=1).max(axis=1)
    df["atr14"] = tr.rolling(14).mean()
    df["swing_low10"] = df["low"].shift(1).rolling(10).min()
    df["swing_high10"] = df["high"].shift(1).rolling(10).max()

    idx = pd.DatetimeIndex(df.index)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    session_key = idx.date if market == "美股/韩股" else idx.tz_convert("UTC").date
    typical = (df["high"] + df["low"] + df["close"]) / 3
    work = pd.DataFrame({"typical": typical, "volume": df["volume"]}, index=df.index)
    work["pv"] = work["typical"] * work["volume"]
    work["session"] = session_key
    cum_pv = work.groupby("session")["pv"].cumsum()
    cum_vol = work.groupby("session")["volume"].cumsum().replace(0, float("nan"))
    df["vwap"] = cum_pv / cum_vol

    # OHLCV candle-direction volume proxy, not true aggressor Delta/CVD.
    direction = (df["close"] > df["open"]).astype(int) - (df["close"] < df["open"]).astype(int)
    df["delta_proxy"] = direction * df["volume"]
    df["delta_proxy_ratio20"] = df["delta_proxy"].rolling(20).sum() / df["volume"].rolling(20).sum().replace(0, float("nan"))
    return df.dropna(subset=["ema20", "ema50", "ema200", "rsi14", "vol_avg20", "vwap", "atr14", "swing_low10", "swing_high10"])


def classify(symbol, raw, market):
    if raw is None or len(raw) < 220:
        return {"symbol": symbol, "market": market, "signal": "数据不足", "score": 0, "detail": "有效K线少于220根，无法可靠计算EMA200"}
    d = indicators(raw, market)
    if len(d) < 3:
        return {"symbol": symbol, "market": market, "signal": "数据不足", "score": 0, "detail": "指标计算失败或有效数据不足"}
    x, p = d.iloc[-1], d.iloc[-2]
    close, rsi, prev_rsi = float(x.close), float(x.rsi14), float(p.rsi14)
    vwap, atr = float(x.vwap), float(x.atr14)
    vol_ratio = float(x.volume / x.vol_avg20) if x.vol_avg20 > 0 else 0.0
    above_vwap = close > vwap
    below_vwap = close < vwap
    reclaim_vwap = float(p.close) <= float(p.vwap) and above_vwap
    lose_vwap = float(p.close) >= float(p.vwap) and below_vwap
    ema_up = float(x.ema20) > float(p.ema20)
    ema_down = float(x.ema20) < float(p.ema20)
    rsi_up = rsi > prev_rsi
    rsi_down = rsi < prev_rsi
    breakout = close > float(x.prior_high3)
    breakdown = close < float(x.prior_low3)
    delta = float(x.delta_proxy_ratio20) if pd.notna(x.delta_proxy_ratio20) else 0.0
    long_trend = float(x.ema20) > float(x.ema50)
    short_trend = float(x.ema20) < float(x.ema50)

    long_confirm = above_vwap and ema_up and long_trend and rsi_up and rsi >= 40 and breakout and vol_ratio >= 1.2 and delta > 0
    short_confirm = below_vwap and ema_down and short_trend and rsi_down and rsi <= 60 and breakdown and vol_ratio >= 1.2 and delta < 0

    if long_confirm:
        signal, side, score = "做多机会候选", "做多", 80
        entry_low, entry_high = close - atr * 0.15, close + atr * 0.10
        stop = min(float(x.swing_low10), close - atr * 1.5)
        invalidation = f"收盘重新跌回VWAP下方，或跌破止损参考位 {stop:.6f}"
        action = "条件满足，可考虑小仓分批试多；不要追高，优先等待回踩。"
    elif short_confirm:
        signal, side, score = "做空机会候选", "做空", 80
        entry_low, entry_high = close - atr * 0.10, close + atr * 0.15
        stop = max(float(x.swing_high10), close + atr * 1.5)
        invalidation = f"收盘重新站回VWAP上方，或突破止损参考位 {stop:.6f}"
        action = "条件满足，可考虑小仓分批试空；不要追跌，优先等待反抽。"
    elif rsi < 30 and below_vwap:
        signal, side, score = "超跌预警", "观望", 25
        entry_low = entry_high = stop = None
        invalidation = "RSI回升且价格收复VWAP后，重新评估反弹机会。"
        action = "暂不抄底；超卖可能持续，等待止跌和收复VWAP。"
    elif rsi_up and (reclaim_vwap or above_vwap):
        signal, side, score = "反弹观察", "观望", 45
        entry_low = entry_high = stop = None
        invalidation = "重新跌破VWAP且RSI转弱，反弹观察失效。"
        action = "继续观察，不把单次站上VWAP当作买入信号。"
    elif below_vwap and ema_down and rsi_down:
        signal, side, score = "下行趋势观察", "观望", 45
        entry_low = entry_high = stop = None
        invalidation = "重新站回VWAP且EMA20转升，空头观察失效。"
        action = "暂不追空；等待反抽受阻或更明确的破位确认。"
    else:
        signal, side, score = "观望", "观望", 0
        entry_low = entry_high = stop = None
        invalidation = "当前没有形成明确交易结构。"
        action = "暂时不操作，等待新的确认信号。"

    return {
        "symbol": symbol, "market": market, "signal": signal, "score": score,
        "side": side, "action": action,
        "close": round(close, 6), "rsi14": round(rsi, 2), "vwap": round(vwap, 6),
        "above_vwap": above_vwap, "volume_ratio": round(vol_ratio, 2),
        "ema20": round(float(x.ema20), 6), "ema50": round(float(x.ema50), 6),
        "ema200": round(float(x.ema200), 6), "atr14": round(atr, 6),
        "entry_zone": [round(entry_low, 6), round(entry_high, 6)] if entry_low is not None else None,
        "stop_reference": round(stop, 6) if stop is not None else None,
        "invalidation": invalidation,
        "delta_proxy_ratio20": round(delta, 3),
        "time": datetime.now(timezone.utc).isoformat(),
        "detail": "RSI回升" if rsi_up else "RSI走弱",
        "data_note": "Delta为K线方向成交量代理值，不是真实逐笔主动买卖Delta；评分是规则匹配分，不是胜率。"
    }


def fetch_crypto(exchange, symbol):
    rows = exchange.fetch_ohlcv(symbol, timeframe="1h", limit=300)
    frame = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    frame.index = pd.to_datetime(frame.pop("ts"), unit="ms", utc=True)
    # Exclude the still-forming hourly candle to avoid intrabar signal flicker.
    current_hour = pd.Timestamp.now(tz="UTC").floor("h")
    frame = frame[frame.index < current_hour]
    return frame[["open", "high", "low", "close", "volume"]].astype(float)


def fetch_stock(symbol):
    hist = yf.Ticker(symbol).history(period="3mo", interval="1h", auto_adjust=True, prepost=False)
    if hist.empty:
        return None
    hist.columns = [str(c).lower() for c in hist.columns]
    return hist[["open", "high", "low", "close", "volume"]].dropna().astype(float)


def send_push(message):
    if not PUSHPLUS_TOKEN:
        print("PUSHPLUS_TOKEN 未设置，跳过微信推送")
        return False
    try:
        response = requests.post("https://www.pushplus.plus/send",
            json={"token": PUSHPLUS_TOKEN, "title": "Market Sentinel 3.0 交易机会提醒", "content": message, "template": "html"}, timeout=15)
        print("PushPlus:", response.status_code, response.text[:300])
        response.raise_for_status()
        try:
            return response.json().get("code") == 200
        except Exception:
            return False
    except Exception as exc:
        print("PushPlus error:", exc)
        return False


def load_state():
    try:
        obj = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        # Migrate v2's {symbol: signal} format.
        if isinstance(obj, dict) and "symbols" not in obj:
            return {"symbols": {k: {"signal": v} for k, v in obj.items()}, "last_push": {}}
        if isinstance(obj, dict):
            obj.setdefault("symbols", {})
            obj.setdefault("last_push", {})
            return obj
    except Exception:
        pass
    return {"symbols": {}, "last_push": {}}


def main():
    results = []
    exchange = ccxt.okx({"enableRateLimit": True})
    for symbol in CRYPTO:
        try:
            results.append(classify(symbol, fetch_crypto(exchange, symbol), "加密货币"))
        except Exception as exc:
            results.append({"symbol": symbol, "market": "加密货币", "signal": "获取失败", "score": 0, "detail": str(exc)[:180]})
        time.sleep(0.3)
    for symbol in STOCKS:
        try:
            results.append(classify(symbol, fetch_stock(symbol), "美股/韩股"))
        except Exception as exc:
            results.append({"symbol": symbol, "market": "美股/韩股", "signal": "获取失败", "score": 0, "detail": str(exc)[:180]})
        time.sleep(1)

    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    state = load_state()
    now = datetime.now(timezone.utc)
    candidates = []
    for item in results:
        symbol = item["symbol"]
        signal = item.get("signal", "观望")
        previous = state["symbols"].get(symbol, {})
        previous_signal = previous.get("signal") if isinstance(previous, dict) else previous
        state["symbols"][symbol] = {"signal": signal, "time": item.get("time")}
        if signal not in ("做多机会候选", "做空机会候选"):
            continue
        last_text = state["last_push"].get(symbol)
        last_time = None
        if last_text:
            try:
                last_time = datetime.fromisoformat(last_text)
            except Exception:
                pass
        is_new_opportunity = previous_signal != signal
        cooldown_over = last_time is None or now - last_time >= timedelta(hours=COOLDOWN_HOURS)
        if is_new_opportunity or cooldown_over:
            candidates.append(item)

    if candidates:
        blocks = ["<b>交易机会候选（规则筛选，不是自动下单）</b>"]
        for item in candidates:
            zone = item.get("entry_zone") or ["-", "-"]
            blocks.append(
                f"<b>{item['symbol']}｜{item['signal']}｜评分 {item['score']}/100</b>"
                f"<br>现价：{item.get('close', '-')}｜RSI：{item.get('rsi14', '-')}｜VWAP：{item.get('vwap', '-')}｜量比：{item.get('volume_ratio', '-')}"
                f"<br><b>操作思路：</b>{item.get('action', '-') }"
                f"<br>参考观察区间：{zone[0]} – {zone[1]}｜止损参考：{item.get('stop_reference', '-')}"
                f"<br>失效条件：{item.get('invalidation', '-')}"
                f"<br>注意：止损参考是波动/结构估算，不保证成交；请自行核对行情和仓位。"
            )
        if send_push("<br><br>".join(blocks)):
            for item in candidates:
                state["last_push"][item["symbol"]] = now.isoformat()

    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
