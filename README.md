# Market Sentinel 1.0

第一版：每15分钟尝试扫描 BTC/ETH/SOL（OKX 1小时K线）及 QQQ/SOXX/NVDA/MU/SNDK/海力士（Yahoo Finance 1小时K线），识别趋势与放量突破/跌破，并可通过 PushPlus 推送。

## 部署步骤
1. 将整个文件夹内容上传到一个 GitHub **Public** 仓库（GitHub Actions 免费额度更适合公开仓库；不要把任何 Token 写进代码）。
2. 仓库 Settings -> Secrets and variables -> Actions -> New repository secret。
3. Name 填 `PUSHPLUS_TOKEN`，Secret 填你的 PushPlus Token。
4. Actions 页面启用工作流，进入 `Market scan`，点击 Run workflow 测试。
5. Streamlit Community Cloud 创建 App，选择该仓库、`app.py` 作为入口文件。

## 重要限制
- GitHub Actions 定时任务不是实时行情服务，可能延迟、排队或漏跑；不能保证严格每15分钟执行。
- 免费 Yahoo Finance 数据可能延迟、缺失或限流；海力士代码为 000660.KS。
- 当前是1小时K线的实验性规则扫描，不含真实逐笔 Delta/CVD、订单簿、OI 或可靠的假突破识别。
- 评分是规则分，不是胜率；先观察和回测，不据此直接交易。
- 不会自动下单。
