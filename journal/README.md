# Research journal

Every day `scripts/daily_scan.py` appends the "Probe candidates" stocks to `research_log.csv`. The first columns are filled in automatically by the rules; you fill in the last three:

| Column | What to enter |
| --- | --- |
| decision | `do` / `skip` (you can also enter `wait`; it counts as "skip" in the evaluation) |
| reason | A one-sentence reason, e.g. "partner is a big pharma company, 13D stake filed", "just finished an offering, weak cash flow" |
| notes | Other notes, optional |

After a few months, run `python scripts/evaluate_journal.py` to compare how the "do" and "skip" groups actually performed afterwards and judge whether manual research adds value.
