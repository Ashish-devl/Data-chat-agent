# Evaluation results

Run 2026-11-05, 100 questions. Model: gemini-3.6-flash (backups: gemini-3.5-flash, gemini-3.5-flash-lite).

| Metric | Result | Target | Met | Questions |
| --- | --- | --- | --- | --- |
| SQL execution accuracy | 98% | 80% | yes | 65 |
| Schema retrieval recall | 100% | 95% | yes | 65 |
| Document answer accuracy | 100% | 85% | yes | 38 |
| Citation precision | 100% | 90% | yes | 38 |
| Refusal correctness | 100% | 90% | yes | 7 |
| Median latency (s) | 9.0 s | under 4 s | no | 100 |

## Misses

- **chn-08** How many customers does each support employee look after? — sql_ok
