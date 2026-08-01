---
inclusion: always
---

# Competition Execution Rules

本 workspace 正在進行 HoyaBIT 黑客松競賽升級。剩餘開發時間極短，所有工作以「沿用現有 MVP、完成可展示閉環」為最高優先。

## 核心目標

現有系統必須能在正式執行上限 900 秒內，針對 BTC、ETH、SOL、BNB、XRP 與現場任意題目，產生：

- `report.md`
- `evidence.json`
- `execution_log.json`
- `research_plan.json`
- `claims.json`
- `manifest.json`

報告必須清楚區分：

```text
Fact → Inference → Conclusion
```

並包含支持證據、反方證據、信心、限制、推翻條件及後續觀察重點。

## 既有 MVP 保護邊界

除非目前 Task 明確要求，禁止：

- 重寫現有 Collector。
- 重寫 Orchestrator。
- 更換 Web UI 框架。
- 大規模重構 `src/app.py`、`src/day2_sources.py`。
- 導入 Step Functions、DynamoDB、向量資料庫或新的 Agent framework。
- 新增大量資料來源。
- 擴充回測、Vegas Strategy 或自動交易。
- 刪除 deterministic offline fallback。
- 為了理想架構重做已能工作的模組。

可接受的做法是：新增小型模組，並以最小局部修改接入既有流程。

## 開發執行方式

1. 一次只實作一個 Task ID。
2. 實作前先閱讀：
   - `.kiro/specs/hoyabit-competition-ready/tasks.md`
   - 對應的 `docs/competition-tasks/T*.md`
   - `docs/COMPETITION_TASK_STATUS.yaml`
3. 確認依賴 Task 都是 `PASS`。
4. 將目前 Task 更新為 `IN_PROGRESS`。
5. 只修改 In Scope 檔案或完成目標所必需的最小相鄰檔案。
6. 執行 targeted tests。
7. 執行現有完整測試套件。
8. 更新文件與狀態。
9. 建立單一 commit。
10. 完成後停止，不得自動開始下一個 Task。

## 時間與 blocker

- 單一 blocker 最多投入 20 分鐘。
- 20 分鐘內無法解除時：
  - 實作安全 fallback。
  - 寫入 `docs/COMPETITION_BLOCKERS.md`。
  - 回報 `BLOCKED` 或 `PARTIAL`。
  - 不得大規模重構。
- T5 未完成前，不得做非必要 UI 美化。
- 最後 90 分鐘不得加入新功能。

## Commit 規則

每個 Task 一個 commit：

```text
chore(T0): freeze competition upgrade baseline
feat(T1): add Amazon Bedrock LLM adapter
feat(T2): add question-driven research planner
feat(T3): add explainable evidence credibility engine
feat(T4): add claim evidence graph and confidence engine
feat(T5): enforce citation gate and competition outputs
feat(T6): expose competition evidence and claims in web demo
feat(T7): harden formal run lifecycle and deadline handling
test(T8): freeze competition-ready demo
```

不得宣稱已建立 commit，除非實際執行並取得 hash。

## 回報格式

```text
TASK:
STATUS: PASS / FAIL / BLOCKED / PARTIAL
FILES_CHANGED:
TEST_COMMANDS:
TEST_RESULTS:
COMMIT:
KNOWN_LIMITATIONS:
NEXT_TASK_READY: yes / no
```

## 安全規則

- 不得提交 API key、AWS access key、密碼或競賽 Access Code。
- 測試使用 mock，不要求真實 Bedrock 呼叫。
- AWS credentials 只從正常 credential chain 取得。
- `.env.example` 只能包含變數名稱與非敏感範例。
