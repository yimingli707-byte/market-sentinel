# Market Sentinel 3.0

每15分钟扫描一次，重点减少低价值微信推送：普通超跌、反弹观察和观望状态只写入 `signals.json`，不推送；仅规则同时满足多项确认条件的“做多机会候选”或“做空机会候选”才推送。

## 监控标的
- 加密货币：BTC/USDT、ETH/USDT、SOL/USDT（OKX 公共 1 小时 K 线）
- 美股/韩股：QQQ、SOXX、NVDA、AMD、AVGO、GOOGL、MU、SNDK、000660.KS（Yahoo Finance 1 小时 K 线）

## 推送规则
- 做多候选：价格在 VWAP 上方、EMA20 上行且高于 EMA50、RSI回升并不低于40、收盘突破此前3根K线高点、量比至少1.2、K线方向成交量代理值为正。
- 做空候选：价格在 VWAP 下方、EMA20 下行且低于 EMA50、RSI走弱且不高于60、收盘跌破此前3根K线低点、量比至少1.2、K线方向成交量代理值为负。
- 其他状态（超跌预警、反弹观察、下行趋势观察、观望）只记录，不发微信。
- 同一标的、同一方向候选推送后设置6小时冷却；方向/候选状态发生变化可作为新机会提醒。扫描频率不变。

## 推送内容
包含操作思路、观察区间、ATR/结构止损参考、失效条件。参考价不是保证成交价，也不是个性化投资建议；程序不会连接交易账户或自动下单。请自行核对实时行情、交易时段、流动性和仓位风险。

## 重要限制
- 评分是规则匹配分，不是概率、历史胜率或收益预测。策略尚未经过充分历史回测，先观察至少一段时间，不应直接据此实盘。
- 加密货币排除未收盘的1小时K线；扫描每15分钟并不代表每15分钟产生新K线。
- 股票/韩股使用Yahoo Finance 1小时数据，可能延迟、缺失、限流或在非交易时段不更新。
- OHLCV无法识别真实主动买卖方向；`delta_proxy_ratio20`只是K线方向成交量代理值，不是真实Delta/CVD。
- VWAP使用HLC3和成交量按交易日估算，不是逐笔精确VWAP。

## 部署
1. 解压ZIP，将文件覆盖到原GitHub仓库，保留 `.github/workflows/scan.yml` 的目录结构。
2. 不要删除仓库 Secrets 中的 `PUSHPLUS_TOKEN`。
3. Actions → Market scan → Run workflow，手动运行测试。
4. 检查 `Run scanner` 与 `Save latest scan and alert state` 是否成功，并确认微信推送。
5. `signal_state.json` 用于跨运行去重和6小时冷却；workflow 已提交该文件。

GitHub Actions cron 每15分钟触发，但可能延迟、排队或漏跑，不保证精确时刻。
