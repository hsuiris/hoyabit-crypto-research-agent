# Competition Blockers

只記錄會阻止目前 Task 驗收或正式展示的問題。一般改善建議放入 Known Limitations，不要擴大為 blocker。

## Active Blockers

| ID | Task | Detected At | Symptom | Root Cause / Current Theory | Attempts | Time Spent | Safe Fallback | Owner | Status |
|---|---|---|---|---|---|---:|---|---|---|
| — | — | — | 目前無 active blocker | — | — | — | — | — | — |

## Resolved Blockers

| ID | Task | Resolution | Tests | Commit |
|---|---|---|---|---|
| B2 | T8 → D8 | 在 AWS Lambda（us-west-2）以 `amazon.nova-lite-v1:0` 執行唯一一次 BTC formal live smoke，analyst 與 critic 的 stage status 皆為 `success`；解除過程中發現原記錄的根因不成立，並修掉一個真正的 Bedrock JSON 缺陷 | `scripts/verify_live_smoke.py` 判定 B2 解除條件滿足；完整套件 581 tests OK | `fix(D5)` `3269142`（fence 修復）、`docs(D8)`（本次記錄） |
| B1 | T2 | Track B 於 `85bc551` commit `src/planner.py`，Track A 隨即完成接線（Orchestrator 呼叫 planner、產出 `research_plan.json`、Execution Log 新增 `plan_research` step） | `tests/test_t2_planner_wiring.py` 16 tests OK；完整套件 326 tests OK | `feat(T2): add question-driven research planner` |

### B1（已解除）：T2 依賴的 Track B 模組尚未 commit

- **解除**：Track B 在 `85bc551` commit `src/planner.py`（同批另有 `ff9d226` credibility、
  `a262378` claim graph、`2e32eea` run manager、`f57f4f9` report renderer、`config/source_registry.json`），
  T3-T5 的依賴模組也一併到位。Track A 在 `feat(T2): add question-driven research planner`
  完成 T2 接線。以下為當時的檢查紀錄，保留備查。

- **Task**：T2（question-driven research planner 接線）
- **Track A 的範圍**：把 Track B 已 commit 的純模組接進 `src/orchestrator.py`、`src/llm.py`、
  `src/validation.py`、`src/app.py`、`lambda_handler.py`，並更新 `docs/COMPETITION_TASK_STATUS.yaml`。
  Track A 不得建立或修改 `src/planner.py`。

- **實際檢查與結果**（HEAD = `3b7a251`，branch `hackathon/competition-ready`）：

  | 檢查 | 結果 |
  |---|---|
  | `ls src/planner.py` | 不存在 |
  | `git log --all --oneline -- src/planner.py` | 無任何 commit |
  | `git stash list` | 空 |
  | `git branch -a` | 只有 `hackathon/competition-ready`、`main`、`remotes/origin/main` |
  | `git worktree list` | 只有目前 worktree |
  | `git status --short` | 乾淨，無未追蹤的 planner 檔案 |

- **順帶確認的後續依賴**（同樣不存在、無 commit）：

  | 路徑 | 需要它的 Task |
  |---|---|
  | `src/credibility.py` | T3 |
  | `config/source_registry.json` | T3 |
  | `src/claim_graph.py` | T4 |
  | `src/run_manager.py` | T7（`src/artifact_store.py` 已存在，T5 的 manifest 可用） |

- **安全 fallback**：不做任何功能性修改。既有 deterministic offline pipeline 完全未動，
  `report.md`／`evidence.json`／`execution_log.json` 照常產出。基線回歸已確認
  `python3 -m unittest discover -s tests` → 175 tests、175 passed、0 failed、0 errors。

- **解除條件**：Track B commit `src/planner.py`（含 T2 spec 要求的 ResearchPlan schema、
  7 種 task mode、關鍵字 deterministic fallback）之後，Track A 即可執行 T2 接線：
  orchestrator 呼叫 planner、輸出 `research_plan.json`、Execution Log 記錄
  planner provider／model／duration／fallback used／time window assumptions。

- **未執行的事項（刻意）**：未建立 `src/planner.py`、未改動 `src/orchestrator.py`、
  未改動 `src/llm.py`、未新增 T2 測試。

## Blocker Rules

1. 單一 blocker 最多投入 20 分鐘。
2. 超時後優先建立安全 fallback。
3. 不得用大規模重構解一個局部 blocker。
4. 不得刪除 offline fallback。
5. 不得隱藏錯誤或把失敗測試改成跳過。
6. 對正式環境權限問題，先完成 mock path、文件與 offline fallback。

---

# Known Deviations（非 blocker，已記錄待後續任務處理）

以下項目不阻止目前 Task 驗收，也不影響離線提交物產出，但屬於刻意保留的邊界例外。

## T0.5：邊界介面凍結

### 例外 1：`src/app.py` 的 `_summarize_sources()` 仍直接呼叫 Gemini／OpenAI 端點

- **狀態**：PARTIAL（已記錄，未修正）
- **原因**：該函式屬呈現層的來源摘要（新聞／社群／公告的中文 digest），不是 Planner／Analyst／Critic
  三個推理角色。全域限制禁止改寫 Web UI，且將它遷到 `LLMClient` 會改動既有的 HTTP 錯誤分類
  （429 配額訊息、URLError 訊息），風險與當時任務目標不成比例。
- **影響**：Bedrock 目前不會被用於來源摘要 —— `_summarize_sources()` 遇到 `provider=bedrock`
  會回「未支援的 LLM provider」並退回純標題清單，報告主流程（分析與稽核）不受影響。
- **Fallback**：摘要失敗時頁面照常顯示標題清單並說明原因，離線模式完全不受影響。
- **後續**：在 T6（Web Demo）處理時，把該函式改為
  `client.generate_json(prompt=..., schema=_DIGEST_SCHEMA, schema_name="source_digest", ...)`，
  並保留現有的錯誤訊息分類。
- **測試上的處理**：`tests/test_t05_boundaries.py::test_reasoning_modules_do_not_import_model_sdks_directly`
  的檢查範圍為 `orchestrator.py`、`day2_sources.py`、`ports.py`、`artifact_store.py`、
  `run_context.py`；`src/app.py` 暫時排除，`src/llm.py` 是唯一允許持有 provider SDK 的模組。

### 例外 2：`lambda_handler.py` 仍硬編 `/tmp/outputs`

- **狀態**：PARTIAL（已記錄，未修正）
- **原因**：T0.5 明確排除真實 S3 上傳與 Lambda 資源調整；此處等 `S3ArtifactStore` 完成後一併改。
- **影響**：Lambda 產物仍寫在容器本地 `/tmp`，不影響本地與 Web Demo。
- **後續**：在 T7（formal run lifecycle）或 S3 任務中改為
  `LocalArtifactStore.for_run(context, Path("/tmp"))` 或 `S3ArtifactStore(bucket, context.output_prefix)`，
  由 `RunContext` 決定落點。

## T5：Citation Gate 與提交物

### 例外 3：`lambda_handler.py` 的回應仍只含三個檔案

- **狀態**：PARTIAL（已記錄，未修正）
- **原因**：T5 的範圍是 gate 與提交物產出；磁碟上六個檔案已齊備，但 Lambda 的 JSON 回應
  仍只回 `report`／`evidence`／`execution_log`。改動它會同時碰到 `/tmp/outputs` 落點問題
  （見例外 2），兩者應一起處理。
- **影響**：本地與 Web Demo 不受影響；Lambda 呼叫者暫時拿不到 `research_plan`／`claims`／`manifest`
  的內容（檔案仍在 `/tmp/outputs` 下）。
- **後續**：T7 處理 formal run 落點時，一併把回應改為列出 `manifest["files"]`。

### 例外 4：「Critic 不得改寫 Evidence」的守衛沒有觸發測試

- **狀態**：已記錄，刻意保留
- **原因**：`run()` 在呼叫 Critic 前後比對 Evidence 的 `asdict` 快照，但注入的 `LLMClient`
  只拿到序列化後的 payload，拿不到 Evidence 物件本身，因此無法從測試端真的改寫它。
- **影響**：守衛本身是防禦性程式碼，正常路徑不會觸發；其餘 Critic 邊界（不得新增 Evidence、
  不得提高信心）都有對應測試。
- **後續**：若日後 Critic 改為可傳入物件參考，需同時補上此路徑的測試。

## 環境

- 本機 `python` 不存在，實際使用 `python3`，版本 `3.9.6`（低於專案要求的 3.10+）。目前 488 項測試全數通過，
  但新程式碼需避開 3.10+ 專屬語法。正式競賽環境應改用 Python 3.10 以上。

## B2（已解除）：Bedrock live smoke 無法實際呼叫模型

**解除於 D8**（2026-08-01）。解除過程推翻了原本記錄的根因，並發現一個真正的程式缺陷。
以下先記錄解除結果，再保留當時的原始記錄備查。

### 解除結果

在 AWS Lambda（`us-west-2`，Function URL）執行唯一一次 BTC `formal` live smoke：

| 項目 | 結果 |
|---|---|
| run_id | `RUN-20260801T094240Z-BTC-f3d916b3` |
| analyst | `provider=bedrock`，`status=success` |
| critic | `provider=bedrock`，`status=success` |
| 六項提交物 | 齊備 |
| manifest | 五個 SHA-256 全部相符（落地成 fixture 後重驗仍相符） |
| Citation Gate | `PASS_WITH_WARNINGS`（0 error、0 warning、10 個語意 finding） |
| 耗時 | 31.2 秒 |
| run_status | `COMPLETED_DEGRADED`（原因見下方，非模型失敗） |

產物保存於 `demo-fixtures/competition-ready/live-success/`。

### 原記錄的根因不成立（重要）

原本寫的是「本機依零第三方相依限制未安裝 boto3」。**這個描述是錯的，不要照它去裝套件。**

- 實測本機 `python3` 已有 `boto3`／`botocore` 1.42.97，且 `bedrock-runtime` 含
  `Converse` operation（`botocore.session.get_service_model("bedrock-runtime").operation_names`）。
- 當時真正的阻塞是**缺少 AWS credentials 與模型存取權**，在取得 Workshop Studio 憑證後即解除。

### 解除過程發現的真正缺陷

取得憑證並部署後，模型路徑**仍然**失敗，但根因是第三個、完全不同的問題：

`amazon.nova-lite-v1:0` 在 Converse 下會把 JSON 包在 markdown code fence 裡
（```json ... ```），而 `src/llm.py` 直接 `json.loads()`，因此拋
`JSONDecodeError: Expecting value: line 1 column 1 (char 0)`。既有的單次重試**無效**——
模型行為一致，重試只會拿到同一個 fence。結果是 planner／analyst／critic 三個階段全部
降級成 deterministic fallback，而報告照樣產出，只有 stage status 記著 `fallback:ValueError`。

更深層的原因：Converse 沒有等同 Gemini `response_mime_type` 或 OpenAI `response_format`
的強制 JSON 模式，因此模型可自行決定輸出格式。

修復：新增 `src.llm.strip_json_fence()`（只剝最外層 fence）並在 prompt 明確要求不要使用
code fence。Gemini／OpenAI 走各自的結構化輸出模式，不經過此函式，行為完全未變。
commit `3269142`，測試 `tests/test_d5_bedrock_json_fence.py`（15 tests）。

### 為什麼 run_status 仍是 COMPLETED_DEGRADED

與模型無關。Binance 封鎖美國 IP，而部署 region 是 `us-west-2`，因此 `derivatives`、
`vegas_channel`、`long_short_ratio` 三個 collector 取不到資料（`HTTPError`），
改用可靠度 0.20 的 fallback fixture，11 個 collector 為 8 success + 3 fallback。

已實測對比：本機（台灣 IP）對 `api.binance.com` 與 `fapi.binance.com` 回 `HTTP 200`，
雲端 `us-west-2` 回 `HTTPError`。這是 region 的固有限制，改程式碼無法修復，詳見
`aws/README.md`。這個降級標示是誠實的，且 Citation Gate 仍 `PASS`。

### 原始記錄（備查）

- **當時執行**：以 `LLM_PROVIDER=bedrock`、`AWS_REGION=ap-northeast-1`、`BEDROCK_MODEL_ID=amazon.nova-lite-v1:0` 執行唯一一次 BTC live smoke。
- **當時結果**：11 個外部 collector 都是 `success`；Citation Gate `PASS`；manifest 完整；runtime **2.9 秒**。Planner、分析、Critic 與 claim model 因 `RuntimeError: Amazon Bedrock requires boto3; install it locally or run in AWS Lambda` 改走 deterministic fallback，run 狀態為 `COMPLETED_DEGRADED`。
- **當時判定**：這不是功能性 regression，也不允許為了本機 smoke 引入第三方套件；但它無法證明現場 Bedrock 模型成功，因此 T8 必須是 `PARTIAL`，不得建立 `competition-demo-ready` tag。
- **當時的安全 fallback**：固定展示 `demo-fixtures/competition-ready/offline-backup/`（ETH 假設題）與 `comparison-backup/`（SOL vs BNB）。這兩份備案在 D8 後仍然保留，未刪除。
