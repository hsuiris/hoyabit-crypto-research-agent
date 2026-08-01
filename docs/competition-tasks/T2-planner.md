# T2 — Question Planner 與 Research Plan

## Metadata

```yaml
task_id: T2
depends_on: [T1]
target_minutes: 60
target_commit: "feat(T2): add question-driven research planner"
```

## Goal

讓系統在分析前先把現場題目轉換成結構化研究計畫，而不是只將問題塞入最終 Prompt。

## New File

```text
src/planner.py
```

## Local Integration

- `src/orchestrator.py`
- `src/llm.py`
- 比較流程需要的最小傳遞點
- Execution Log
- 對應 tests

## ResearchPlan Schema

至少包含：

```json
{
  "coins": ["BTC"],
  "task_modes": [],
  "primary_question": "",
  "time_window": {
    "days": 14,
    "source": "explicit"
  },
  "hypotheses": [
    {
      "hypothesis_id": "H1",
      "statement": "",
      "support_questions": [],
      "contradiction_questions": [],
      "falsification_conditions": []
    }
  ],
  "required_domains": [],
  "comparison_dimensions": [],
  "assumptions": [],
  "stop_conditions": {
    "max_evidence": 36,
    "max_followup_rounds": 1
  }
}
```

Allowed task modes：

```text
describe_market_state
test_hypothesis
compare_assets
explain_driver
assess_consistency
identify_risks
identify_attention_conditions
```

## Implementation Requirements

1. 使用 T1 的 JSON LLM helper。
2. Planner 只規劃，不產生市場結論。
3. 一題可同時具有多個 task mode。
4. 未明確指定時間時，沿用產品預設 14 天或 repo 既有穩定預設，並在 `assumptions` 明示。
5. 假設驗證必須產生：
   - support questions
   - contradiction questions
   - falsification conditions
6. 比較題必須：
   - 共用 as-of。
   - 共用 time window。
   - 共用 comparison dimensions。
7. Planner 失敗時使用 deterministic fallback：
   ```text
   比較 / vs / 相較       → compare_assets
   認為 / 是否 / 支持反對 → test_hypothesis
   原因 / 驅動            → explain_driver
   一致 / 整合            → assess_consistency
   風險                    → identify_risks
   值得關注 / 條件         → identify_attention_conditions
   default                 → describe_market_state
   ```
8. 本 Task 不重寫現有 Collector 選擇；可以先繼續全域蒐集，降低整合風險。
9. 單幣執行建立一份 plan。
10. 比較執行建立一份共享 plan。
11. 保存：
   ```text
   research_plan.json
   ```
12. Execution Log 記錄：
   - planner provider/model
   - duration
   - fallback used
   - time window assumptions

## Required Test Questions

### A. 多源整合

```text
分析 BTC 過去兩週市場表現，整合價格、鏈上、新聞與社群。
```

預期：

- `describe_market_state`
- `assess_consistency`
- 14 天 explicit
- multiple required domains

### B. 假設驗證

```text
市場認為 ETH 短期將維持盤整，請找支持與反對證據。
```

預期：

- `test_hypothesis`
- 至少一個 hypothesis
- support / contradiction / falsification 非空

### C. 比較

```text
比較 SOL 與 BNB 在當前宏觀環境下的市場位置與風險。
```

預期：

- `compare_assets`
- shared time window
- shared dimensions

### D. 模糊題

```text
分析 XRP 當前市場狀況。
```

預期：

- default window 明示在 assumptions
- `describe_market_state`

### E. Invalid LLM Output

預期：

- deterministic fallback
- run continues
- log records fallback

## Acceptance Criteria

- [ ] 所有測試產生合法 schema。
- [ ] Planner 沒有市場結論。
- [ ] 假設題對稱找正反。
- [ ] 比較題對稱。
- [ ] Invalid LLM output 不會中止。
- [ ] `research_plan.json` 產生。
- [ ] Execution Log 完整。
- [ ] 完整測試套件無 regression。

## Stop Condition

完成後停止。不得開始 T3。
