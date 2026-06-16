# Weekly Trend Roll Strategy Dashboard

这是一个初版周线趋势滚仓仪表盘，用于每周扫描：

- BTC
- XAU
- SI
- CL
- QQQ
- AUDUSD
- GBPUSD
- USDJPY
- EURUSD

## 策略逻辑

方向过滤：

- 多头：上一周收盘在 20WMA 上方，20WMA 上行，上一周收在自身振幅上 30%。
- 空头：上一周收盘在 20WMA 下方，且上一周不是强收。

入场：

- 周开盘后 24 小时内，等待第一根 4H 收盘价站在周开盘价方向内。
- 多头：4H 收盘 > 本周开盘价。
- 空头：4H 收盘 < 本周开盘价。

进攻模式：

- 首仓使用固定价格止损；单笔本金风险建议为账户权益的 1%–2%。
- 到第一目标后建议平 35%，剩余仓位止损推保本。
- **BTC / XAU（趋势周）**：质量分 ≥ 75 且均线与方向一致时，首目标后可最多加 2 腿，仅用浮盈滚仓，累计名义不超过首仓 3 倍。
- **其它标的**：只跟信号与止损止盈，不做进攻型滚仓。
- 加仓只允许使用浮盈，不扩大本金风险。

## 滚仓 Playbook 摘要（BTC / XAU）

| 阶段 | 规则 |
|---|---|
| 首仓 | 周线 + 4H 确认；账户风险 1%–2% |
| 趋势周 | 质量分 ≥ 75，20WMA 斜率与方向一致，非追价入场 |
| 首目标 | 平 35%，止损推保本；趋势周保留余仓进入滚仓模式 |
| 有限滚仓 | 4H 回踩确认后加腿，每周最多 2 腿，名义 ≤ 首仓 3×，只用浮盈 |
| 退出 | 4H 反向破周开盘或 Weekly 逻辑失效 → 全平 |

## 默认参数

| 标的 | 数据源 | 默认止损 | 第一目标 |
|---|---|---:|---:|
| BTC | Yahoo BTC-USD spot proxy | 1.0% | 2.5R |
| XAU | Yahoo GC=F proxy | 1.25% | 2.5R |
| SI | Yahoo SI=F proxy | 1.5% | 2.5R |
| CL | Yahoo CL=F proxy | 1.25% | 2.5R |
| QQQ | Yahoo QQQ ETF | 1.25% | 2.5R |
| AUDUSD | Yahoo AUDUSD=X | 0.75% | 2R |
| GBPUSD | Yahoo GBPUSD=X | 0.75% | 2R |
| USDJPY | Yahoo JPY=X | 0.75% | 2R |
| EURUSD | Yahoo EURUSD=X | 0.6% | 2R |

## 本地运行

```bash
python3 scripts/generate_signals.py
python3 scripts/backtest_strategy.py
python3 scripts/backtest_3y_report.py
python3 -m http.server 8000
```

`backtest_3y_report.py` 会按当前生产策略（周线过滤 + 4H 确认 + ATR 止损）生成 `data/backtest_3y_report.json` 和 `data/backtest_3y_report.md`。

然后打开：

```text
http://localhost:8000
```

## GitHub Pages 部署

1. 推送代码到 GitHub 仓库。
2. 在仓库 Settings -> Pages 中选择 GitHub Actions。
3. 手动运行 `Update Weekly Strategy Dashboard` workflow，或等待定时任务。

workflow 会：

- 每周一北京时间 08:05 刷新一次。
- 北京时间工作日每 4 小时刷新一次（00:10、04:10、08:10 …）。
- 生成 `data/signals.json` 和 `data/backtest_report.json` 并部署静态站。

网站上的「重新加载部署数据」只会读取 GitHub Pages 上已部署的 JSON，不会重新拉取行情。如需立即更新，请到仓库 Actions 页手动运行 `Update Weekly Strategy Dashboard`。

## 策略研究

`scripts/backtest_strategy.py` 会用现有公开行情做轻量历史模拟，对比：

- Baseline：当前首 24h 4H 突破周开盘 + 固定止损。
- Stricter 4H Confirmation：首 24h 内要求连续两根 4H 确认。
- Confirm Then Pullback：先确认方向，再等 48h 内回踩/重新站回周开盘。
- ATR-Aware Stop：当前入场，止损至少使用 1.2 倍近期 4H ATR。
- Structure-Aware Stop：当前入场，止损放在前 6 根 4H 结构外。

报告会输出每个标的和规则的胜率、平均 R、首目标率、止损率、漏入场率和最大连亏。当前仪表盘也会显示综合评分最高的规则摘要，以及每个当前信号的质量评分和风险提示。

## 已知限制

- 这是策略监控初版，不是自动下单系统。
- Yahoo 行情是免费代理数据，XAU 使用期货代理符号，QQQ 为 ETF 现货，不等于你券商的真实成交价。
- 回测使用简化的 4H K 线内路径假设；同一根 4H 同时触发止损和目标时，保守按先止损计算。
- 当前加仓提示较简单，只识别首目标后的顺势动量延续。
- 实盘前需要接入你的真实交易数据源，并进一步优化不同标的的止损、止盈和滚仓规则。
