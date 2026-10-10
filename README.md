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
configs/strategy_v1.yaml   策略 v1 参数（异动 / 观察 / 试仓 / 加仓 / 出场）
configs/scanner_v0.yaml    旧版平台突破扫描器 v0 参数（已被 v1 替换）
configs/scanner_v0b.yaml   v0b：低波动条件改为“60 日内出现过”
configs/samples.yaml       事件复盘样本（可手工指定起涨日 manual_day0）
src/alpha_breakout/
  data.py                  yfinance 日线拉取 + data/cache/prices/ 本地缓存（含批量下载）
  universe.py              全市场股票池（SEC 列表）+ 按时点股本估算市值
  strategy.py              v1 状态机回测（staged 分批 / immediate 对照）
  scanner.py               逐日计算指标 + v0 规则判定
  event_study.py           大涨段识别（zigzag）、day 0 对齐、窗口统计
  sec.py                   SEC EDGAR 公告列表
scripts/build_universe.py  建股票池并下载全市场行情（首次约 25 分钟）
scripts/backtest_v1.py     v1 全市场回测，输出到 reports/data/
scripts/event_study.py     v0 样本复盘（第一阶段）
```

## 用法

```bash
python scripts/build_universe.py
python scripts/backtest_v1.py --tag v1
python scripts/backtest_v1.py --tag v1_mkt --set market.enabled=true   # 改单个参数
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
