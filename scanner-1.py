import os
import json
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

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


def _session_keys(index, market):
    idx = pd.DatetimeIndex(index)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    if market == "美股/韩股":
        # Yahoo Finance timestamps are normally already in the exchange timezone.
        return idx.date
    return idx.tz_convert("UTC").date


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

    typical = (df["high"] + df["low"] + df["close"]) / 3
    work = pd.DataFrame({"typical": typical, "volume": df["volume"]}, index=df.index)
    work["pv"] = work["typical"] * work["volume"]
    work["session"] = _session_keys(df.index, market)
    cum_pv = work.groupby("session")["pv"].cumsum()
    cum_vol = work.groupby("session")["volume"].cumsum().replace(0, float("nan"))
    df["vwap"] = cum_pv / cum_vol

    # Candle-direction volume proxy; this is NOT true aggressor Delta/CVD.
    direction = (df["close"] > df["open"]).astype(int) - (df["close"] < df["open"]).astype(int)
    df["delta_proxy"] = direction * df["volume"]
    df["delta_proxy_ratio20"] = df["delta_proxy"].rolling(20).sum() / df["volume"].rolling(20).sum().replace(0, float("nan"))
    return df.dropna(subset=["ema20", "ema50", "ema200", "rsi14", "vol_avg20", "vwap", "atr14", "swing_low10", "swing_high10"])


def _bar_timestamp(index_value):
    ts = pd.Timestamp(index_value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def _market_data_status(market, symbol, bar_time):
    now = datetime.now(timezone.utc)
    age_hours = max(0.0, (now - bar_time.to_pydatetime()).total_seconds() / 3600)
    if market == "加密货币":
        status = "实时市场" if age_hours <= 2.2 else "行情延迟/获取异常"
        usable = age_hours <= 2.2
    elif symbol == "000660.KS":
        local = now.astimezone(ZoneInfo("Asia/Seoul"))
        is_regular = local.weekday() < 5 and (9 <= local.hour < 15 or (local.hour == 15 and local.minute < 30))
        status = "交易时段" if is_regular and age_hours <= 2.5 else ("休市/行情陈旧" if age_hours > 3 else "盘前/盘后或休市")
        usable = age_hours <= 3
    else:
        local = now.astimezone(ZoneInfo("America/New_York"))
        is_regular = local.weekday() < 5 and ((local.hour > 9 or (local.hour == 9 and local.minute >= 30)) and local.hour < 16)
        if age_hours > 3:
            status, usable = "休市/行情陈旧", False
        elif is_regular:
            status, usable = "交易时段", True
        else:
            status, usable = ("盘前/盘后行情" if age_hours <= 2.5 else "休市/行情陈旧"), age_hours <= 2.5
    return status, usable, round(age_hours, 2)


def classify(symbol, raw, market):
    if raw is None or len(raw) < 220:
        return {"symbol": symbol, "market": market, "signal": "数据不足", "score": 0,
                "detail": "有效K线少于220根，无法可靠计算EMA200", "data_status": "数据不足"}
    d = indicators(raw, market)
    if len(d) < 3:
        return {"symbol": symbol, "market": market, "signal": "数据不足", "score": 0,
                "detail": "指标计算失败或有效数据不足", "data_status": "数据不足"}

    x, p = d.iloc[-1], d.iloc[-2]
    bar_time = _bar_timestamp(d.index[-1])
    data_status, data_usable, age_hours = _market_data_status(market, symbol, bar_time)
    close, rsi, prev_rsi = float(x.close), float(x.rsi14), float(p.rsi14)
    vwap, atr = float(x.vwap), float(x.atr14)
    vol_ratio = float(x.volume / x.vol_avg20) if x.vol_avg20 > 0 else 0.0
    above_vwap, below_vwap = close > vwap, close < vwap
    reclaim_vwap = float(p.close) <= float(p.vwap) and above_vwap
    lose_vwap = float(p.close) >= float(p.vwap) and below_vwap
    ema_up, ema_down = float(x.ema20) > float(p.ema20), float(x.ema20) < float(p.ema20)
    rsi_up, rsi_down = rsi > prev_rsi, rsi < prev_rsi
    breakout, breakdown = close > float(x.prior_high3), close < float(x.prior_low3)
    delta = float(x.delta_proxy_ratio20) if pd.notna(x.delta_proxy_ratio20) else 0.0
    long_trend, short_trend = float(x.ema20) > float(x.ema50), float(x.ema20) < float(x.ema50)

    # A级：原有严格确认条件。B级：趋势、价格位置、动能与量能/突破中多数条件成立。
    long_a = above_vwap and ema_up and long_trend and rsi_up and rsi >= 40 and breakout and vol_ratio >= 1.2 and delta > 0
    short_a = below_vwap and ema_down and short_trend and rsi_down and rsi <= 60 and breakdown and vol_ratio >= 1.2 and delta < 0
    long_b_score = sum([above_vwap, long_trend, ema_up, rsi_up and rsi >= 40,
                        breakout or reclaim_vwap, vol_ratio >= 0.9, delta > 0])
    short_b_score = sum([below_vwap, short_trend, ema_down, rsi_down and rsi <= 60,
                         breakdown or lose_vwap, vol_ratio >= 0.9, delta < 0])
    long_b = not long_a and above_vwap and long_trend and long_b_score >= 5
    short_b = not short_a and below_vwap and short_trend and short_b_score >= 5

    if not data_usable:
        signal, side, score = "行情陈旧/不推送", "观望", 0
        entry_low = entry_high = stop = None
        invalidation = "等待数据源更新到最新交易时段后重新判断。"
        action = "当前数据可能是上一个交易时段的旧价格，不据此产生交易信号。"
    elif long_a or long_b:
        grade = "A级确认" if long_a else "B级观察"
        signal, side, score = ("做多机会候选" if long_a else "做多观察机会"), "做多", (85 if long_a else 65 + long_b_score)
        entry_low, entry_high = close - atr * 0.15, close + atr * 0.10
        stop = min(float(x.swing_low10), close - atr * 1.5)
        invalidation = f"收盘重新跌回VWAP下方，或跌破止损参考位 {stop:.6f}"
        action = f"{grade}：可列入做多计划；优先等待回踩观察区间，不要追高。"
    elif short_a or short_b:
        grade = "A级确认" if short_a else "B级观察"
        signal, side, score = ("做空机会候选" if short_a else "做空观察机会"), "做空", (85 if short_a else 65 + short_b_score)
        entry_low, entry_high = close - atr * 0.10, close + atr * 0.15
        stop = max(float(x.swing_high10), close + atr * 1.5)
        invalidation = f"收盘重新站回VWAP上方，或突破止损参考位 {stop:.6f}"
        action = f"{grade}：可列入做空计划；优先等待反抽观察区间，不要追跌。"
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
        "symbol": symbol, "market": market, "signal": signal, "score": int(score),
        "side": side, "action": action, "close": round(close, 6),
        "rsi14": round(rsi, 2), "vwap": round(vwap, 6), "above_vwap": above_vwap,
        "volume_ratio": round(vol_ratio, 2), "ema20": round(float(x.ema20), 6),
        "ema50": round(float(x.ema50), 6), "ema200": round(float(x.ema200), 6),
        "atr14": round(atr, 6),
        "entry_zone": [round(entry_low, 6), round(entry_high, 6)] if entry_low is not None else None,
        "stop_reference": round(stop, 6) if stop is not None else None,
        "invalidation": invalidation, "delta_proxy_ratio20": round(delta, 3),
        "bar_time": bar_time.isoformat(), "scan_time": datetime.now(timezone.utc).isoformat(),
        "data_status": data_status, "data_age_hours": age_hours,
        "detail": "RSI回升" if rsi_up else "RSI走弱",
        "data_note": "美股/韩股尝试获取盘前盘后K线；休市或旧数据不产生交易信号。Delta为K线方向成交量代理值，不是真实逐笔主动买卖Delta；评分不是胜率。"
    }


def fetch_crypto(exchange, symbol):
    rows = exchange.fetch_ohlcv(symbol, timeframe="1h", limit=300)
    frame = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    frame.index = pd.to_datetime(frame.pop("ts"), unit="ms", utc=True)
    # Strategy only uses fully closed hourly candles.
    current_hour = pd.Timestamp.now(tz="UTC").floor("h")
    frame = frame[frame.index < current_hour]
    return frame[["open", "high", "low", "close", "volume"]].astype(float)


def fetch_stock(symbol):
    # Include Yahoo Finance pre-market and after-hours bars where the feed provides them.
    hist = yf.Ticker(symbol).history(period="3mo", interval="1h", auto_adjust=True, prepost=True)
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
            json={"token": PUSHPLUS_TOKEN, "title": "Market Sentinel 3.2 交易机会提醒", "content": message, "template": "html"}, timeout=15)
        print("PushPlus:", response.status_code, response.text[:300])
        response.raise_for_status()
        return response.json().get("code") == 200
    except Exception as exc:
        print("PushPlus error:", exc)
        return False


def load_state():
    try:
        obj = json.loads(STATE_FILE.read_text(encoding="utf-8"))
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
            results.append({"symbol": symbol, "market": "加密货币", "signal": "获取失败", "score": 0,
                            "data_status": "获取失败", "detail": str(exc)[:180]})
        time.sleep(0.3)
    for symbol in STOCKS:
        try:
            results.append(classify(symbol, fetch_stock(symbol), "美股/韩股"))
        except Exception as exc:
            results.append({"symbol": symbol, "market": "美股/韩股", "signal": "获取失败", "score": 0,
                            "data_status": "获取失败", "detail": str(exc)[:180]})
        time.sleep(1)

    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    state = load_state()
    now = datetime.now(timezone.utc)
    candidates = []
    pushable = {"做多机会候选", "做空机会候选", "做多观察机会", "做空观察机会"}
    for item in results:
        symbol = item["symbol"]
        signal = item.get("signal", "观望")
        previous = state["symbols"].get(symbol, {})
        previous_signal = previous.get("signal") if isinstance(previous, dict) else previous
        state["symbols"][symbol] = {"signal": signal, "bar_time": item.get("bar_time"),
                                     "scan_time": item.get("scan_time"), "data_status": item.get("data_status")}
        # No alerts from stale/closed stock data or failed requests.
        if signal not in pushable or item.get("data_status") in ("休市/行情陈旧", "行情延迟/获取异常", "获取失败"):
            continue
        last_text = state["last_push"].get(symbol)
        last_time = None
        if last_text:
            try:
                last_time = datetime.fromisoformat(last_text)
                if last_time.tzinfo is None:
                    last_time = last_time.replace(tzinfo=timezone.utc)
            except Exception:
                pass
        # Also require a new completed candle before a same-direction alert can repeat.
        last_bar_text = state.get("last_push_bar", {}).get(symbol)
        new_bar = item.get("bar_time") != last_bar_text
        is_new_opportunity = previous_signal != signal
        cooldown_over = last_time is None or now - last_time >= timedelta(hours=COOLDOWN_HOURS)
        if is_new_opportunity or (cooldown_over and new_bar):
            candidates.append(item)

    if candidates:
        blocks = ["<b>交易机会提醒（A级确认 / B级观察；规则筛选，不是自动下单）</b>"]
        for item in candidates:
            zone = item.get("entry_zone") or ["-", "-"]
            blocks.append(
                f"<b>{item['symbol']}｜{item['signal']}｜评分 {item['score']}/100</b>"
                f"<br>价格：{item.get('close', '-')}｜行情状态：{item.get('data_status', '-')}"
                f"<br>K线时间：{item.get('bar_time', '-')}｜扫描时间：{item.get('scan_time', '-')}"
                f"<br>RSI：{item.get('rsi14', '-')}｜VWAP：{item.get('vwap', '-')}｜量比：{item.get('volume_ratio', '-')}"
                f"<br><b>操作思路：</b>{item.get('action', '-')}"
                f"<br>参考观察区间：{zone[0]} – {zone[1]}｜止损参考：{item.get('stop_reference', '-')}"
                f"<br>失效条件：{item.get('invalidation', '-')}"
                f"<br>注意：止损参考是波动/结构估算，不保证成交；B级是观察信号，不等于立即入场。"
            )
        if send_push("<br><br>".join(blocks)):
            state.setdefault("last_push_bar", {})
            for item in candidates:
                state["last_push"][item["symbol"]] = now.isoformat()
                state["last_push_bar"][item["symbol"]] = item.get("bar_time")

    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
