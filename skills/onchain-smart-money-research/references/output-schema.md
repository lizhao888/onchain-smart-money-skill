# 报告字段（schema: onchain-sm/v0.1）

```json
{
  "schema": "onchain-sm/v0.1",
  "chain": "ethereum",
  "token": "0x...",
  "pool": "0x...",
  "generated_at": "ISO8601",
  "rules_version": "v0",
  "token_meta": {"symbol": "", "name": "", "mcap": 0, "holders": 0},
  "steps": {
    "<name>": {
      "step": "", "status": "PASSED|WARNING|BLOCKED|FAILED",
      "metrics": {}, "evidence": [{"kind":"tx|pool|token|risk","ref":"","source":"","retrieved_at":""}],
      "errors": [], "warnings": [], "unknowns": []
    }
  },
  "buyers": [{"address": "", "block": 0, "tx_hash": ""}],
  "activity": [{"address": "", "label": "", "swap_30d": 0, "transfer_30d": 0, "days_since": 0}],
  "risk": {"verified": true, "blockers": [], "buy_tax": 0, "sell_tax": 0},
  "score": 0,
  "final_grade": "BLOCKED|INCOMPLETE|RESEARCH|WATCH",
  "complete": true
}
```

**不变量**
1. `steps[*].status == FAILED` ⇒ 该节点 `metrics.count` 不存在或为 `null`。
2. `risk.blockers` 非空 ⇒ `final_grade == "BLOCKED"`。
3. `complete == false` 时，报告不得被引用为结论。
