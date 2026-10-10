# 研究日志

`scripts/daily_scan.py` 每天会把"可试仓"的票追加到 `research_log.csv`，前面几列由规则自动填好，后面三列由你填写：

| 列 | 填什么 |
| --- | --- |
| decision | `做` / `不做`（也可以填 `观望`，评估时按“不做”算） |
| reason | 一句话理由，例如“合作方是大药企，有 13D 入股”“刚增发完，现金流差” |
| notes | 其他备注，可空 |

几个月后跑 `python scripts/evaluate_journal.py`，比较“做”和“不做”两组之后的实际表现，判断人工研究有没有增益。
