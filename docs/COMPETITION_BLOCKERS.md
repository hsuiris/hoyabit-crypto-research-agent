# Competition Blockers

只記錄會阻止目前 Task 驗收或正式展示的問題。一般改善建議放入 Known Limitations，不要擴大為 blocker。

## Active Blockers

| ID | Task | Detected At | Symptom | Root Cause / Current Theory | Attempts | Time Spent | Safe Fallback | Owner | Status |
|---|---|---|---|---|---|---:|---|---|---|
| — | — | — | — | — | — | — | — | — | — |

## Resolved Blockers

| ID | Task | Resolution | Tests | Commit |
|---|---|---|---|---|
| — | — | — | — | — |

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

## 環境

- 本機 `python` 不存在，實際使用 `python3`，版本 `3.9.6`（低於專案要求的 3.10+）。目前 149 項測試全數通過，
  但新程式碼需避開 3.10+ 專屬語法。正式競賽環境應改用 Python 3.10 以上。
