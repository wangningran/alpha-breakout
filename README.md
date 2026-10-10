# alpha-breakout

美股小盘生物科技 / AI 制药「突破后第一波」策略研究。策略思路和待办见 [STRATEGY.md](STRATEGY.md)，阶段报告在 `reports/`。

## 环境

```bash
pip install -r requirements.txt
export SEC_USER_AGENT="你的名字 你的邮箱"   # SEC EDGAR 要求带联系方式
```

网络需要放行 `query1.finance.yahoo.com`（行情）和 `data.sec.gov`、`www.sec.gov`（公告）。

## 结构

```
configs/scanner_v0.yaml    突破扫描器 v0 参数（STRATEGY.md 第 3 节）
configs/scanner_v0b.yaml   v0b：低波动条件改为“60 日内出现过”
configs/samples.yaml       事件复盘样本（可手工指定起涨日 manual_day0）
src/alpha_breakout/
  data.py                  yfinance 日线拉取 + data/cache/ 本地缓存
  scanner.py               逐日计算指标 + v0 规则判定
  event_study.py           大涨段识别（zigzag）、day 0 对齐、窗口统计
  sec.py                   SEC EDGAR 公告列表
scripts/event_study.py     跑样本复盘，输出到 reports/data/
```

## 用法

```bash
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
