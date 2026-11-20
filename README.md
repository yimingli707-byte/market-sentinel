# Market Sentinel 2.0

在 1.0 基础上增加短线超跌反弹观察：RSI(14)、日内 session VWAP、EMA20/50/200、成交量比、K线方向成交量代理值，以及 0-100 规则评分。

## 监控标的
- 加密货币：BTC/USDT、ETH/USDT、SOL/USDT（OKX 公共 1 小时 K 线）
- 美股/韩股：QQQ、SOXX、NVDA、AMD、AVGO、GOOGL、MU、SNDK、000660.KS（Yahoo Finance 1 小时 K 线）

## 信号解释
- `超跌预警`：RSI < 30 且价格在 VWAP 下方。只提醒关注，不是买入信号。
- `反弹观察`：RSI 开始回升，但价格尚未站回 VWAP。
- `反弹启动观察`：价格重新站上 VWAP、RSI回升、EMA20上行且量比至少1.2。
- `反弹确认观察`：价格站上 VWAP、EMA20上行、量比至少1.2，并突破此前3根K线高点。
- `观望`：不满足上述规则。

评分是规则匹配分，不是概率、胜率或收益预测。请先模拟观察和回测，设置止损；不要仅凭单一信号交易。

## Delta 的限制
普通 OHLCV K 线没有逐笔买卖方向，无法计算真实主动买入/卖出 Delta 或 CVD。本项目的 `delta_proxy_ratio20` 仅根据每根K线收涨/收跌，将整根成交量记为正/负方向的粗略代理值，不能称为真实 Delta，也不能据此判断真实吸筹。

## VWAP 的限制
VWAP 使用每根K线的 HLC3=(最高+最低+收盘)/3 与成交量估算，并按交易日重置；这是基于 1 小时K线的近似值，不等同于交易所逐笔成交计算的精确 VWAP。

## 部署
1. 将 ZIP 中的文件覆盖到原 GitHub 仓库，保留 `.github/workflows/scan.yml` 路径。
2. GitHub 仓库 Settings -> Secrets and variables -> Actions，确认 `PUSHPLUS_TOKEN` 已设置。
3. Actions -> Market scan -> Run workflow，手动运行一次。
4. 检查 `signals.json`、Actions 日志及微信推送。
5. Streamlit Cloud 如已连接原仓库，通常会自动重新部署；入口仍为 `app.py`。

## 定时运行
GitHub Actions cron 设置为每15分钟触发一次，但 GitHub 定时任务可能延迟、排队或漏跑，不保证精确每15分钟。扫描数据是 1 小时 K 线，因此15分钟扫描并不会产生15分钟级别的新K线；它主要用于重复检查最新可用数据和状态变化。

## 数据和交易限制
- Yahoo Finance 免费行情可能延迟、缺失、限流；某些标的可能暂时获取失败。
- `000660.KS` 是海力士 Yahoo Finance 行情代码；不是 OKX 交易代码。
- QQQ、NVDA 等是行情源代码，不代表在 OKX 一定有对应可交易代币。
- 本项目只发观察提醒，不提供确定性买卖建议，不连接交易账户，不自动下单。
