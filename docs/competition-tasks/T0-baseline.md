# T0 — 凍結競賽升級前基線

## Metadata

```yaml
task_id: T0
depends_on: []
target_minutes: 30
target_commit: "chore(T0): freeze competition upgrade baseline"
status_file: docs/COMPETITION_TASK_STATUS.yaml
```

## Goal

確認目前 repository 的實際可執行狀態，建立可隨時回復的競賽升級基線。本任務原則上不修改產品功能。

## Required Reading

- `README.md`
- `SETUP.md`
- `AGENTS.md`
- `.kiro/steering/`
- `src/day1_mvp.py`
- `src/day2_sources.py`
- `src/orchestrator.py`
- `src/llm.py`
- `src/validation.py`
- `src/app.py`
- `lambda_handler.py`
- `aws/template.yaml`
- 現有 `tests/`

## In Scope

- Git 狀態與分支確認。
- 實際測試命令與結果。
- Offline end-to-end smoke。
- Web startup smoke。
- Baseline 文件。
- 僅修復阻止 baseline 啟動的最小環境問題。

## Out of Scope

- Bedrock。
- Planner。
- Evidence score。
- Claim Graph。
- UI 新功能。
- 大規模 refactor。

## Implementation Steps

1. 檢查：
   ```bash
   git status --short
   git branch --show-current
   git rev-parse HEAD
   python --version
   ```

2. 建立或切換：
   ```bash
   git switch -c hackathon/competition-ready
   ```
   若分支已存在，先確認它是否正確，不得強制重設或覆寫未提交變更。

3. 閱讀 Required Reading，記錄實際架構與任何和 spec 假設不同的地方。

4. 執行 repo 文件指定的完整測試。若沒有唯一命令，至少嘗試：
   ```bash
   python -m unittest discover -s tests -v
   ```

5. 執行 offline smoke，確認可產生既有必要輸出：
   - `report.md`
   - `evidence.json`
   - `execution_log.json`

6. 啟動 Web Demo：
   ```bash
   python -m src.app
   ```
   確認首頁可回應；不得長時間進行 UI 除錯。

7. 更新 `docs/COMPETITION_BASELINE.md`：
   - 日期時間
   - branch／commit
   - Python
   - 測試命令及結果
   - offline smoke 指令及結果
   - Web smoke 指令及結果
   - 現有 artifacts
   - 已知 baseline failures
   - 實際重要檔案與介面
   - rollback 指令

8. 更新 `docs/COMPETITION_TASK_STATUS.yaml`：
   - T0 → `REVIEW`
   - 寫入測試摘要及 commit 前狀態。

9. 建立 commit：
   ```bash
   git add docs/COMPETITION_BASELINE.md docs/COMPETITION_TASK_STATUS.yaml
   git commit -m "chore(T0): freeze competition upgrade baseline"
   ```

10. 寫回 commit hash，T0 → `PASS`，再用第二個文件狀態 commit 不是必要；若需保持一 task 一 commit，將 hash 寫入回報即可，不要額外建立無意義 commit。

## Acceptance Criteria

- [ ] branch 與 commit 已記錄。
- [ ] 實際完整測試已執行並記錄。
- [ ] offline smoke 已執行並記錄。
- [ ] Web smoke 已執行並記錄。
- [ ] baseline failure 與 regression 可區分。
- [ ] 沒有新增產品功能。
- [ ] 有可回復基線。
- [ ] 實際 commit hash 已回報。

## Gate

`PASS` 的最低條件：

- 至少 offline path 可完成；或其既有 failure 已精確定位且不由本 Task 引入。
- 文件足以讓另一個 Agent 重現現況。
- 未破壞工作樹。

## Failure / Blocked Handling

- 缺少套件：只修最小依賴或記錄安裝命令。
- 外部網路失敗：不阻擋 offline baseline。
- Web port 衝突：使用可用 port 或只記錄明確原因。
- 既有測試失敗：不得偷偷修大量產品邏輯；記錄為 baseline failure。

## Stop Condition

完成後停止。不得開始 T1。

## Required Final Report

```text
TASK: T0
STATUS:
FILES_CHANGED:
TEST_COMMANDS:
TEST_RESULTS:
COMMIT:
KNOWN_LIMITATIONS:
NEXT_TASK_READY:
```
