import json
from pathlib import Path
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Market Sentinel 2.0", page_icon="📈", layout="wide")
st.title("Market Sentinel 2.0")
st.caption("短线超跌反弹监控｜规则信号仅供观察，不构成投资建议，不会自动下单")

path = Path("signals.json")
if not path.exists():
    st.warning("尚无扫描结果。请先运行 scanner.py，或等待 GitHub Actions 首次执行。")
    st.stop()

data = json.loads(path.read_text(encoding="utf-8"))
df = pd.DataFrame(data)
if df.empty:
    st.info("目前没有数据")
    st.stop()

c1, c2, c3, c4 = st.columns(4)
c1.metric("监控标的", len(df))
c2.metric("超跌预警", int((df["signal"] == "超跌预警").sum()))
c3.metric("反弹启动/确认", int(df["signal"].isin(["反弹启动观察", "反弹确认观察"]).sum()))
c4.metric("数据获取失败", int((df["signal"] == "获取失败").sum()))

st.subheader("反弹机会")
order = {"反弹确认观察": 0, "反弹启动观察": 1, "反弹观察": 2, "超跌预警": 3, "观望": 4}
df["_order"] = df["signal"].map(order).fillna(5)
df = df.sort_values(["_order", "score"], ascending=[True, False]).drop(columns="_order")
cols = [c for c in ["market","symbol","signal","score","close","rsi14","vwap","above_vwap",
                    "volume_ratio","ema20","ema50","ema200","delta_proxy_ratio20","detail"] if c in df]
st.dataframe(df[cols], use_container_width=True, hide_index=True)

st.info("RSI<30仅代表超卖，不代表立即买入。VWAP按每个交易日重置，基于1小时K线HLC3估算。Delta列是K线方向成交量代理值，不是真实逐笔主动买卖Delta。")
st.caption("加密货币：OKX公共1小时K线；美股/韩股：Yahoo Finance 1小时数据，可能延迟、缺失或受限。评分是规则分，不是历史胜率。")
