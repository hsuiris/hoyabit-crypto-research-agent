# T7 — Formal Run、Deadline 與不可覆寫輸出

## Metadata

```yaml
task_id: T7
depends_on: [T6]
target_minutes: 45
target_commit: "feat(T7): harden formal run lifecycle and deadline handling"
```

## Goal

讓正式競賽執行有唯一 run ID、不可覆寫輸出、重跑 lineage、900 秒 hard deadline 與 840 秒 finalization deadline。

## New File

```text
src/run_manager.py
```

## Local Integration

- `src/orchestrator.py`
- `src/app.py`
- `lambda_handler.py`
- manifest
- Execution Log
- tests

## Run Modes

```text
test
formal
```

## Run ID

建議：

```text
RUN-YYYYMMDDTHHMMSSZ-BTC-xxxx
```

Comparison 可包含兩幣縮寫，但不得過長。

## Output Directory

```text
runs/{run_id}/
```

不可覆寫已存在 run。

## Formal Lock

保存：

```text
question_hash
started_at
mode
status
```

相同 question hash：

- test：可重複。
- formal：預設拒絕意外重跑。
- authorized rerun：允許，但需 lineage。

## Rerun

至少：

```text
rerun_of
rerun_reason
authorized_rerun=true
```

第一次失敗紀錄不可刪除。

## Status

```text
CREATED
RUNNING
COMPLETED
COMPLETED_DEGRADED
FAILED
```

單一 domain missing 但有可用報告時，應為 `COMPLETED_DEGRADED`，不是全部 FAILED。

## Deadline

```text
hard_deadline_seconds <= 900
finalization_deadline_seconds <= 840
```

保留 repo 既有更保守 phase budget，除非實際測試顯示不合理。

接近 finalization deadline：

1. 停止 follow-up collection。
2. 停止 optional semantic Critic。
3. 不開始另一輪 LLM repair。
4. 使用 available Evidence 建 Claim。
5. deterministic render。
6. 優先保存六項 artifacts。

## Checkpoints

至少在下列階段保存可恢復狀態：

```text
plan
collected evidence
scored evidence
claims
final artifacts
```

後續階段失敗不得刪除前面成果。

## Lambda

`/tmp` 或其他短暫目錄也使用唯一 run directory。

## Required Tests

- [ ] test run repeatable。
- [ ] formal accidental duplicate rejected。
- [ ] authorized rerun。
- [ ] output not overwritten。
- [ ] unique run ID。
- [ ] deadline approaching。
- [ ] Critic skipped but report complete。
- [ ] Collector failure → COMPLETED_DEGRADED。
- [ ] rerun lineage in manifest。
- [ ] existing watchdog remains valid。
- [ ] full suite no regression。

## Acceptance Criteria

- [ ] 每次執行唯一。
- [ ] 正式輸出不可覆寫。
- [ ] 重跑 lineage 完整。
- [ ] deadline 前優先保存。
- [ ] partial evidence preserved。
- [ ] status 正確。
- [ ] commit hash 已回報。

## Stop Condition

完成後停止。不得開始 T8。
