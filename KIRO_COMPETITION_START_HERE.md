# HoyaBIT Kiro 競賽升級套件

本套件用於將既有 `hoyabit-crypto-research-agent` MVP，依序升級成可在競賽現場執行與展示的版本。

---

## 本 repo 的整合狀態（2026-08-01）

套件已整合進本 repo（未執行 `apply_to_repo.sh`，改以手動複製以保護既有文件）。與原始套件的差異：

- **`docs/COMPETITION_BASELINE.md` 保留本 repo 已填寫的 T0 版本**，未被套件的空白樣板覆蓋。
- **`docs/COMPETITION_BLOCKERS.md` 為合併版**：保留套件的 blocker 表格與規則，並在
  「Known Deviations」段落保留 T0.5 已記錄的兩項邊界例外。
- **`docs/COMPETITION_TASK_STATUS.yaml` 已更新為實際進度**：T0、T1、T0.5 為 `PASS`，`current_task: T2`。
- **`.kiro/specs/hoyabit-competition-ready/tasks.md`** 已勾選 T0、T1，並補上計畫外的 T0.5 條目。
- 套件的安裝檔（`apply_to_repo.sh`、`PACKAGE_MANIFEST.md`、`SHA256SUMS.txt`、`scripts/verify_package.py`）
  **未複製**：它們用於驗證套件本身，且 `verify_package.py` 會因為 tasks 已勾選而誤判失敗。
- 既有的 `AGENTS.md` 與 `.kiro/steering/{product,tech,structure,team-roles,architecture-review,competition-baseline}.md`
  全部未修改。

執行環境提醒：本機沒有 `python` 指令，所有文件中的 `python ...` 請改用 `python3 ...`；
目前版本為 3.9.6（低於專案要求的 3.10+），新程式碼須避開 3.10+ 專屬語法。
完整套件現況：`python3 -m unittest discover -s tests` → 149 tests, OK。

---

## 套件目的

它不會重建專案，也不會覆寫既有的 Kiro 基礎 steering。套件只新增：

- 一個完整 Kiro Spec：`.kiro/specs/hoyabit-competition-ready/`
- 三個競賽專用 Steering 檔
- T0～T8 詳細施工任務單
- 任務狀態、阻塞、基線與執行手冊
- 安全覆蓋到既有 repo 的安裝腳本

## 安裝到現有 repo

在本套件目錄執行：

```bash
./apply_to_repo.sh /path/to/hoyabit-crypto-research-agent
```

或在現有 repo 根目錄執行：

```bash
/path/to/this-package/apply_to_repo.sh .
```

腳本只會新增或更新本套件專屬檔案，不會覆寫以下既有檔案：

- `AGENTS.md`
- `.kiro/steering/product.md`
- `.kiro/steering/tech.md`
- `.kiro/steering/structure.md`
- `.kiro/steering/team-roles.md`
- 現有產品程式碼

## 在 Kiro IDE 使用

1. 開啟 `hoyabit-crypto-research-agent` workspace。
2. 在 Specs 面板選擇 `hoyabit-competition-ready`。
3. 先閱讀：
   - `requirements.md`
   - `design.md`
   - `tasks.md`
4. 只啟動 `T0`。
5. T0 回報 `PASS` 並完成 commit 後，才啟動 T1。
6. 依序執行至 T8。

建議不要按「Run all Tasks」。本計畫有人工 Phase Gate、時間切點與降級決策，應一次執行一個任務。

## 在 Kiro CLI 使用

```text
/spec hoyabit-competition-ready
```

接著輸入：

```text
Implement only task T0. Read docs/competition-tasks/T0-baseline.md first.
Do not start T1. Follow all workspace steering files and update
docs/COMPETITION_TASK_STATUS.yaml before stopping.
```

每個任務完成後，將 `T0` 換成下一個 Task ID。

## 最低可提交線

如果時間不足：

```text
T0 → T1 → T2 → T3 → T4 → T5
```

T5 完成後，核心競賽提交物已具備：

- Final Report
- Evidence List
- Execution Log
- Research Plan
- Claims
- Manifest
- Citation Gate

T6～T8用於展示、正式執行控制與可靠性補強。

## 任務狀態

狀態檔：

```text
docs/COMPETITION_TASK_STATUS.yaml
```

允許狀態：

```text
TODO
READY
IN_PROGRESS
REVIEW
PASS
FAIL
BLOCKED
PARTIAL
```

## 全域回報格式

每個 Task 完成時，Agent 必須回報：

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

## 緊急原則

- 單一 blocker 超過 20 分鐘：保留 fallback，記錄 blocker，繼續主線。
- T5 尚未完成：禁止投入 UI 美化。
- 最後 90 分鐘：禁止新增功能，只修 blocker、測試、備份與演練。
- 任何 LLM 或外部資料來源失敗：不得破壞 offline fallback。
