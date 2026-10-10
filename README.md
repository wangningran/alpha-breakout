# alpha-breakout

美股全行业小盘股「异动 → 观察 → 试仓 → 加仓」策略研究。策略思路和待办见 [STRATEGY.md](STRATEGY.md)，阶段报告在 `reports/`。

## 环境

```bash
pip install -r requirements.txt
export SEC_USER_AGENT="你的名字 你的邮箱"   # SEC EDGAR 要求带联系方式
```

网络需要放行 `query1.finance.yahoo.com`（行情）和 `data.sec.gov`、`www.sec.gov`（公告）。

## 结构

```
configs/strategy_v3.yaml   策略 v3 参数（当前默认：v2 + 基本面检查 + 3R 分批止盈 + 组合参数）
configs/strategy_v2.yaml   策略 v2 参数（v1 + 数据清洗 + 增发过滤 + 催化剂标签）
configs/strategy_v1.yaml   策略 v1 参数（异动 / 观察 / 试仓 / 加仓 / 出场）
configs/scanner_v0.yaml    旧版平台突破扫描器 v0 参数（已被 v1 替换）
configs/scanner_v0b.yaml   v0b：低波动条件改为“60 日内出现过”
configs/samples.yaml       事件复盘样本（可手工指定起涨日 manual_day0）
src/alpha_breakout/
  data.py                  yfinance 日线拉取 + data/cache/prices/ 本地缓存（含批量下载）
  universe.py              全市场股票池（SEC 列表）+ 按时点股本估算市值
  strategy.py              状态机回测（staged 分批 / immediate 对照）
  filings.py               全市场 SEC 公告索引、增发表格、催化剂分类、13D/13G 计数
  fundamentals.py          SEC XBRL 季度基本面（营收、净利润、现金），按时点取值
  research.py              试仓前检查（13D/13G、基本面）和交易特征
  portfolio.py             组合层面模拟（资金、仓位上限、盯市）
  scanner.py               逐日计算指标 + v0 规则判定
  event_study.py           大涨段识别（zigzag）、day 0 对齐、窗口统计
  sec.py                   SEC EDGAR 公告列表
scripts/build_universe.py  建股票池并下载全市场行情（首次约 25 分钟）
scripts/fetch_filings.py   拉全市场 SEC 公告索引（首次约 15 分钟）
scripts/fetch_fundamentals.py  拉全市场季度基本面（首次约 10 分钟）
scripts/backtest.py        全市场回测（--config 选 v1 / v2 / v3），输出到 reports/data/
scripts/portfolio.py       组合模拟（读回测输出的交易）
scripts/daily_scan.py      每日扫描：输出 reports/daily/<日期>.md，并追加研究日志
scripts/evaluate_journal.py  评估研究日志（做 / 不做 两组之后的表现）
scripts/event_study.py     v0 样本复盘（第一阶段）
```

## 用法

```bash
python scripts/build_universe.py
python scripts/fetch_filings.py
python scripts/fetch_fundamentals.py
python scripts/backtest.py --tag v3 --features        # 默认 configs/strategy_v3.yaml
python scripts/portfolio.py --tag v3
python scripts/daily_scan.py                         # 每天收盘后
python scripts/evaluate_journal.py
python scripts/backtest.py --config configs/strategy_v1.yaml --tag v1
python scripts/backtest.py --tag v3_atr --set position.exit_mode=atr   # 改单个参数
python scripts/event_study.py --tag v0
python scripts/event_study.py --config configs/scanner_v0b.yaml --tag v0b
```

在代码里使用：

```python
from alpha_breakout.data import get_daily
from alpha_breakout.scanner import scan
from alpha_breakout.config import load_config

cfg = load_config(overrides={"volume": {"min_volume_mult": 2.5}})
signals = scan(get_daily("SDGR"), get_daily("XBI"), cfg)
signals[signals["signal"]]
```
