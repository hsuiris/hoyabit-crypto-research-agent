# T6 — 現有 Web Demo 競賽展示對齊

## Metadata

```yaml
task_id: T6
depends_on: [T5]
target_minutes: 60
target_commit: "feat(T6): expose competition evidence and claims in web demo"
```

## Goal

沿用現有 `src/app.py` 與 UI，只增加競賽必要的 Research Plan、Claims、Evidence traceability、confidence breakdown 與 Execution Log。

## Strict Boundary

禁止：

- 換前端框架。
- 重寫整個 `src/app.py`。
- 做新 design system。
- 修改回測／Vegas 功能。
- 複雜動畫。
- 因美化破壞 offline demo。

## Required Display

### Research Plan

- task modes
- time window
- hypotheses
- required domains
- comparison dimensions
- assumptions
- fallback used

### Market Judgment

- direction／verdict
- final confidence
- `type=heuristic`
- main limitations

### Claim Cards

每個 Claim：

- statement
- Fact
- Inference
- Conclusion
- supporting Evidence
- contradicting Evidence
- limitations
- invalidation conditions
- watchpoints

### Confidence Breakdown

- evidence quality
- domain coverage
- source diversity
- signal consistency
- counter-evidence coverage
- hard-cap limiters

### Evidence Table / Detail

- Evidence ID
- source
- source type
- source URL／locator
- fetched_at
- published/event time
- content reference
- reliability
- verification status
- related Claim
- score limiters

大型 raw content 不要預設全部展開。

### Execution Log

- stage
- status
- duration
- fallback
- model/tool/collector
- created Evidence IDs

### Artifact Access

提供檢視或下載：

- report
- evidence
- execution log
- research plan
- claims
- manifest

## Compatibility

- 舊 fixture 缺欄位：顯示 N/A。
- partial run：UI 不 crash。
- comparison：顯示 shared as-of/window。
- HTML/UI 不可用時：Markdown 與 JSON 仍可展示。

## Required Tests

- [ ] single coin render。
- [ ] comparison render。
- [ ] old fixture missing new fields。
- [ ] partial/degraded run。
- [ ] no claims edge case。
- [ ] Evidence detail。
- [ ] Execution Log。
- [ ] artifact link/path。
- [ ] existing routes still work。
- [ ] full suite no regression。

## Acceptance Criteria

- [ ] 首頁可輸入現有題目與幣種。
- [ ] Plan 可見。
- [ ] Claim chain 可見。
- [ ] 支持/反方可見。
- [ ] Confidence components/limiters 可見。
- [ ] Evidence traceability 可見。
- [ ] Log 可見。
- [ ] 缺資料不 crash。
- [ ] offline demo 正常。
- [ ] commit hash 已回報。

## Time Cut

若 60 分鐘內 UI 仍不穩：

1. 停止美化。
2. 只加入 Plan、Claim Cards、Evidence metadata 三區。
3. 保留現有 UI。
4. 使用 `report.md` 作備援。

## Stop Condition

完成後停止。不得開始 T7。
