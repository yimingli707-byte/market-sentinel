import json
from pathlib import Path
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Market Sentinel 3.0", page_icon="📈", layout="wide")
st.title("Market Sentinel 3.0")
st.caption("每15分钟扫描；仅在规则确认的候选交易机会时推送。只作辅助观察，不自动下单。")
path = Path("signals.json")
if not path.exists():
    st.warning("尚无扫描结果。请先运行 GitHub Actions。")
    st.stop()
data = json.loads(path.read_text(encoding="utf-8"))
df = pd.DataFrame(data)
if df.empty:
    st.info("目前没有数据")
    st.stop()
c1, c2, c3, c4 = st.columns(4)
c1.metric("监控标的", len(df))
c2.metric("做多机会候选", int((df["signal"] == "做多机会候选").sum()))
c3.metric("做空机会候选", int((df["signal"] == "做空机会候选").sum()))
c4.metric("数据获取失败", int((df["signal"] == "获取失败").sum()))
st.subheader("扫描结果")
order = {"做多机会候选": 0, "做空机会候选": 1, "反弹观察": 2, "下行趋势观察": 3, "超跌预警": 4, "观望": 5}
df["_order"] = df["signal"].map(order).fillna(6)
df = df.sort_values(["_order", "score"], ascending=[True, False]).drop(columns="_order")
cols = [c for c in ["market", "symbol", "signal", "score", "side", "action", "close", "rsi14", "vwap", "volume_ratio", "atr14", "entry_zone", "stop_reference", "invalidation", "detail"] if c in df]
st.dataframe(df[cols], use_container_width=True, hide_index=True)
st.info("仅做多/做空候选会触发微信推送；相同标的同方向信号至少间隔6小时。RSI超卖不等于买入。止损价为结构与ATR估算参考，评分不是胜率。")
st.caption("加密货币：OKX公共1小时K线，排除未收盘小时K线；美股/韩股：Yahoo Finance 1小时数据，可能延迟或缺失。Delta为K线方向成交量代理值，不是真实逐笔主动买卖Delta。")
