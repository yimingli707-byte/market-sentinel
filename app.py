import json
from pathlib import Path
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Market Sentinel", page_icon="📈", layout="wide")
st.title("Market Sentinel 1.0")
st.caption("实验版行情扫描面板｜不是投资建议，不会自动下单")

path = Path("signals.json")
if not path.exists():
    st.warning("尚无扫描结果。请先运行 scanner.py，或等待 GitHub Actions 首次执行。")
    st.stop()

data = json.loads(path.read_text(encoding="utf-8"))
df = pd.DataFrame(data)
if df.empty:
    st.info("目前没有数据")
    st.stop()

c1, c2, c3 = st.columns(3)
c1.metric("监控标的", len(df))
c2.metric("突破/跌破信号", int(df["signal"].isin(["突破偏多", "跌破偏空"]).sum()))
c3.metric("数据获取失败", int((df["signal"] == "获取失败").sum()))

st.subheader("扫描结果")
cols = [c for c in ["market","symbol","signal","score","close","volume_ratio","ema20","ema50","ema200","detail"] if c in df]
st.dataframe(df[cols], use_container_width=True, hide_index=True)
st.caption("评分是规则匹配分，不代表胜率。美股免费行情可能延迟；加密货币使用 OKX 公共K线。")
