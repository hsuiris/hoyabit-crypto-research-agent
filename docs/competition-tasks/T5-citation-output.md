# T5 — Citation Gate 與完整競賽輸出

## Metadata

```yaml
task_id: T5
depends_on: [T4]
target_minutes: 75
target_commit: "feat(T5): enforce citation gate and competition outputs"
milestone: minimum_competition_ready_core
```

## Goal

確保所有主要判斷可回溯，阻止未知引用、跨 run 引用、fallback sole support 與明顯過度推論，並生成完整競賽提交 bundle。

## Primary Modifications

- `src/validation.py`
- `src/orchestrator.py`
- report renderer
- Critic prompt/schema
- manifest/export logic
- tests

## Structural Citation Gate

必須驗證：

1. Evidence ID 存在。
2. Evidence 屬於 current run。
3. Evidence 具有：
   - source
   - fetched_at
   - content_reference
   - related_claim_ids
4. Evidence 不為 rejected。
5. fallback 不為 primary Claim 唯一支持。
6. supporting／contradicting relation 合理。
7. 主要 Claim 沒有支持時必須 `insufficient_evidence`。
8. confidence 沒有突破 hard cap。
9. related Claim ID 互相一致。
10. Comparison 不引用不同 as-of 的不對稱資料，除非明確揭露。

Gate 結果：

```text
PASS
PASS_WITH_WARNINGS
FAIL
```

## Semantic Critic

至少分類：

```text
over_claim
unsupported
ignored_counter
stale_or_weak
correlation_as_causation
official_statement_as_outcome
onchain_transfer_as_intent
confidence_too_high
```

Critic 可以：

- 產生 warning。
- 建議保守改寫。
- 降低 confidence。

Critic 不可：

- 新增 Evidence。
- 提高 confidence。
- 修改 Raw Evidence。
- 在 structural pass 後因自身 timeout 讓所有產出消失。

若 Critic timeout／invalid：

- 使用 structural gate。
- 使用 deterministic conservative report。
- Execution Log 記錄 skipped/degraded。

## Deterministic Report

Markdown 結構固定：

```text
分析標的與題目
資料截止與時間範圍
市場判斷
關鍵依據
Fact → Inference → Conclusion
支持與反方證據
跨來源一致程度
信心 components
已知限制
可能推翻結論的條件
後續觀察重點
Evidence 摘要
```

LLM 不能自由決定整份報告結構。

## Required Outputs

```text
report.md
evidence.json
execution_log.json
research_plan.json
claims.json
manifest.json
```

所有檔案使用同一 run ID。

## Manifest

至少包含：

```text
run_id
started_at
completed_at
question
coins
mode
provider/model/region
code commit
config version
scoring version
files + sha256
validation status
rerun lineage when applicable
```

## Execution Log

每個 stage 至少：

```text
started_at
completed_at
duration_ms
stage
agent/tool/collector
source/query summary
status
fallback reason
created Evidence IDs
provider/model when applicable
```

不保存模型私有內部推理；只保存可稽核流程與工具紀錄。

## Required Tests

- [ ] unknown Evidence ID。
- [ ] cross-run Evidence。
- [ ] rejected Evidence。
- [ ] fallback sole support。
- [ ] related_claim mismatch。
- [ ] confidence over cap。
- [ ] official announcement overclaim。
- [ ] on-chain intent overclaim。
- [ ] correlation as causation。
- [ ] Critic timeout。
- [ ] deterministic report fallback。
- [ ] manifest hash。
- [ ] all six files。
- [ ] consistent run IDs。
- [ ] full suite no regression。

## Acceptance Criteria

- [ ] 所有主要 Claim 可回溯。
- [ ] 報告有來源、時間、解釋。
- [ ] Structural Gate 可阻擋未知引用。
- [ ] Critic failure 仍有保守報告。
- [ ] 所有六項 artifacts 完整。
- [ ] Manifest hashes 可驗證。
- [ ] Execution Log 可重建主要流程。
- [ ] commit hash 已回報。

## Gate

T5 PASS 即達最低可提交核心。若時間告急，T6 可只做最小展示、T7 只做 deadline/output protection、T8 只做核心故障與備份。

## Stop Condition

完成後停止。不得開始 T6。
