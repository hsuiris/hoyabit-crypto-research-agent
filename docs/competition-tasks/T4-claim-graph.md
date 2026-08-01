# T4 — Claim–Evidence Graph 與 Deterministic Confidence

## Metadata

```yaml
task_id: T4
depends_on: [T3]
target_minutes: 90
target_commit: "feat(T4): add claim evidence graph and confidence engine"
```

## Goal

正式把分析拆成 Fact → Inference → Conclusion，並讓每個主要 Claim 有支持、反對、限制、推翻條件、觀察點與 deterministic confidence。

## New File

```text
src/claim_graph.py
```

## Local Modifications

- `src/llm.py` 的分析 schema／prompt
- `src/orchestrator.py`
- `src/validation.py`
- 核心結果 schema
- tests

## Claim Schema

```json
{
  "claim_id": "CL-001",
  "statement": "",
  "claim_type": "market_judgment",
  "verdict": "partially_supported",
  "facts": [
    {
      "statement": "",
      "evidence_ids": []
    }
  ],
  "inference": "",
  "conclusion": "",
  "supporting_evidence_ids": [],
  "contradicting_evidence_ids": [],
  "confidence": {
    "score": 0.0,
    "level": "low",
    "type": "heuristic",
    "components": {},
    "limiters": []
  },
  "limitations": [],
  "invalidation_conditions": [],
  "watchpoints": []
}
```

Allowed verdicts：

```text
supported
partially_supported
mixed
contradicted
insufficient_evidence
```

## LLM Boundary

LLM 可以：

- 提出 Claim。
- 列 Fact / Inference / Conclusion。
- 分類 supporting / contradicting / context。
- 提出 alternative explanations。
- 提出 limitations / invalidation / watchpoints。

LLM 不可以：

- 設 final confidence。
- 引用未知 Evidence ID。
- 把 fallback 當主要確認。
- 隱藏強反方。
- 改寫 Raw Evidence。

## Deterministic Fallback

如果 LLM：

- 失敗。
- JSON invalid。
- 沒有 claims。
- 引用未知 ID。
- 產生不合法 relation。

則使用既有 signal inventory 與有效 Evidence 建立保守 fallback claims。

至少產生：

- 一個 market judgment claim，或
- 一個 `insufficient_evidence` claim。

## Evidence Relationship

- 驗證每個 ID 存在。
- 寫回 `Evidence.related_claim_ids`。
- 不接受 rejected Evidence 作 primary support。
- Fact 必須可直接追溯。
- Inference 記錄 derived Evidence IDs。

## Claim Confidence

```text
confidence =
  0.30 × weighted_evidence_quality
+ 0.25 × domain_coverage
+ 0.20 × source_diversity
+ 0.15 × signal_consistency
+ 0.10 × counter_evidence_coverage
```

Rules：

```text
one supporting domain            → cap 0.60
strong support + contradiction   → cap 0.70
coverage < 0.40                  → insufficient_evidence
fallback-only primary support    → insufficient_evidence
Critic                           → reduce only
```

Confidence output:

```text
score
level
type=heuristic
components
limiters
```

## Hypothesis Mode

必須分開計算：

```text
support_strength
contradiction_strength
```

不得只數 Evidence 數量，應使用 quality × relevance × independence。

## Comparison Mode

強制：

- same as-of
- same window
- same dimensions
- same calculation method
- missing remains missing

每個 comparison dimension 保留 Evidence IDs。

## Output

保存：

```text
claims.json
```

## Required Tests

- [ ] 多 domain 一致。
- [ ] 高品質 support/contradiction 同時存在。
- [ ] 單一 news source。
- [ ] fallback-only。
- [ ] missing on-chain。
- [ ] unknown Evidence ID。
- [ ] hypothesis supported。
- [ ] hypothesis mixed。
- [ ] comparison missing value。
- [ ] Critic cannot increase confidence。
- [ ] same input produces same confidence。
- [ ] claims JSON serializable。
- [ ] full suite no regression。

## Acceptance Criteria

- [ ] 至少一個主要 Claim 或 insufficient Claim。
- [ ] 每個 Claim 可追溯。
- [ ] Fact/Inference/Conclusion 分離。
- [ ] 正反 Evidence 都保留。
- [ ] confidence deterministic。
- [ ] 矛盾會降低信心。
- [ ] `claims.json` 產生。
- [ ] commit hash 已回報。

## Stop Condition

完成後停止。不得開始 T5。
