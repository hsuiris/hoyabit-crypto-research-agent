# HoyaBIT 完整補強稽核參考（本次衝刺勿執行）

> **不可執行警告：本文件只保留完整稽核與長期技術債參考，不是本次衝刺的可執行任務包。** 本次唯一可貼入 Kiro 的提示詞位於 [KIRO_4H_AWS_DELIVERY_PROMPTS.md](./KIRO_4H_AWS_DELIVERY_PROMPTS.md)。使用者已明確排除簡報、投影片、錄影與 AWS teardown；不得執行本文件的 K8/K9，也不得以本文件擴張 4 小時任務範圍。

本文件把 2026-08-02 的專案稽核結果拆成 10 個獨立 Task。每次開一個新的 Kiro 對話，只貼一個 Task；不要使用 **Run all Tasks**。

稽核的最新程式基準是 `origin/GoToAWS0801` commit `84079f5`（不是當時已分叉的本機 HEAD）：完整測試 683/683 通過，但仍有資料品質閘門、雲端持久化、跨容器 formal lock、Lambda 功能同等、最新部署、公開交付與賽後拆除等缺口。執行時應重新讀取實際 HEAD，不可假設基準仍未改變。

## 執行路線

```text
K0 契約與安全基線
  ↓
K1 統一資料品質閘門
  ↓
K2 S3 持久化產物
  ↓
K3 DynamoDB formal lock
  ↓
K4 ResearchPlan 驅動採集與主動反證（可延後的加分項）
  ↓
K5 本機／Lambda 入口功能同等
  ↓
K6 離線 Release Candidate 獨立驗收
  ↓
K7 經人工核准的 AWS 部署與 live E2E
  ↓
K8 文件、Kiro 證據與公開提交同步
  ↓
K9 決賽結束後 AWS 拆除
```

最短競賽路線：`K0 → K1 → K2 → K3 → K5 → K6 → K7 → K8`。時間不足時可延後 K4，但必須把「ResearchPlan 會主動驅動採集與反證」改成未完成或未驗證，不能保留過度承諾。K9 只在決賽展示及必要證據備份完成後執行。

## 模型與推理強度

| Task | 建議模型 | Effort | 原因 |
|---|---|---:|---|
| K0 | Claude Opus 4.8 | Max | 安全、Git 歷史、跨模組契約與發布閘門 |
| K1 | Claude Opus 4.8 | XHigh | 多來源資料品質、順序約束與大量邊界條件 |
| K2 | Claude Opus 4.8 | XHigh | S3、manifest、下載與本機相容的多檔變更 |
| K3 | Claude Opus 4.8 | Max | 原子條件寫入、併發、正式執行一次性 |
| K4 | Claude Opus 4.8 | XHigh | 計畫驅動、多輪蒐集、deadline 與 provenance |
| K5 | Claude Sonnet 4.6 | Max | 範圍明確的入口整合與回歸測試 |
| K6 | Claude Opus 4.8 | Max | 獨立品質、安全與 release 判定 |
| K7 | Claude Opus 4.8 | Max | 真實 AWS、成本、正式執行與回滾風險 |
| K8 | Claude Sonnet 4.6 | High | 文件一致性、Kiro 證據、簡報與提交清單 |
| K9 | Claude Opus 4.8 | Max | 具破壞性的雲端拆除與憑證撤銷 |

選擇理由：Opus 4.8 與 Sonnet 4.6 目前是 Kiro 的 Active 模型；本計畫不以 experimental 模型作主選。若 Opus 4.8 在帳號或區域不可用，優先改用 Sonnet 4.6 Max；若接受 experimental 風險，複雜任務也可改用 GPT-5.6 Sol，維持同級 effort。不要用 Auto 執行 release/security Task，因為要留下可重現的固定模型紀錄。

Kiro IDE 可從輸入框的模型選單設定模型與 Effort；CLI 可先用 `/model` 選模型，再用 `/effort high|xhigh|max`。每個新對話先確認顯示的模型與 effort 正確。

---

## K0 — 契約、安全與補強 Spec 基線

模型：**Claude Opus 4.8**
推理強度：**Max**

```text
只執行 Task K0，完成後停止；不要開始 K1。

TASK_ID: K0
OBJECTIVE: 以現況證據建立補強 Spec、凍結跨模組契約與安全/發布閘門；本 Task 不實作產品功能、不連 AWS、不發布任何內容。
TARGET_COMMIT: docs(K0): freeze remediation contracts and safety gates

你是本 Task 的唯一寫入者。保留使用者與其他 agent 的既有變更，不得 reset、checkout、stash、清除或覆寫非本 Task 檔案。若既有未提交變更與本 Task 目標檔重疊，立即停止並回報。

SCOPE AUTHORIZATION: K0 只建立使用者要求的 post-T8 remediation plan。它記錄對 feature freeze 的有界例外，但不自動授權 K1–K9；之後只有使用者實際貼上某一 Task，才代表授權該 Task 的本機、明列範圍。安全、測試與外部操作 gate 不受影響。

READ FIRST:
1. AGENTS.md 與其指向的制度文件。
2. 兩份使用者來源文件（唯讀）：
   - /Users/zhangshitong/Desktop/HoyaBIT_決賽提案報告_重新建立.md
   - /Users/zhangshitong/Desktop/(HOYA BIT) 命題文件 - 2026 雲湧智生：臺灣生成式 AI 應用黑客松競賽 更新.docx
   DOCX 若不能直接讀，可用 macOS `textutil -convert txt -stdout "<完整路徑>"` 輸出到終端；不得覆寫或另存 Desktop 檔案。若路徑不可存取，標 PENDING-HUMAN，不可猜內容。
3. .kiro/steering/*.md。
4. .kiro/specs/hoyabit-competition-ready/{requirements.md,design.md,tasks.md}。
5. .kiro/specs/hoyabit-aws-deployment/{requirements.md,design.md,tasks.md,status.yaml}。
6. docs/COMPETITION_TASK_STATUS.yaml、docs/COMPETITION_CHECKLIST.md、docs/aws-architecture.md、docs/project-report.md。
7. src/ports.py、src/run_context.py、src/artifact_store.py、src/run_manager.py、src/orchestrator.py、src/app.py、lambda_handler.py、aws/template.yaml。

PREFLIGHT:
- `git status --short --branch`
- `git log -8 --oneline`
- `git remote`
- `git symbolic-ref --short refs/remotes/origin/HEAD`（ref 不存在可標 N/A）
- `git rev-list --left-right --count origin/main...HEAD`（ref 不存在可標 N/A）
- `python3 --version`
- 禁止執行會先輸出含 token URL 的 `git remote -v`；upstream/divergence 只用 symbolic ref 與 rev-list，不輸出 remote URL。
- 記錄實際 branch、HEAD、upstream、origin/main 與 HEAD 的差異、live fixture 的 code_commit、既有 tag。
- 秘密掃描只能採 exit-code-only：`bash scripts/check_secrets.sh >/dev/null 2>&1` 與 `bash scripts/check_secrets.sh --history >/dev/null 2>&1`，只記 exit code。若非 0，標 BLOCKED/PENDING-HUMAN；在 scanner 有 safe-summary/quiet 模式前，不得執行會印出匹配行的版本。不得顯示 AWS account ID、完整 Function URL、access key、secret 或 session token。
- 不得執行 git filter-repo/filter-branch、force-push、push、merge、tag、PR、default branch 修改、AWS CLI、HTTP live probe、deploy、delete 或憑證操作。

已知稽核事實只能當待重驗假設：
- 683 個本機測試曾全部通過。
- 公開 GitHub default branch 曾落後本機多個 commit。
- Git 歷史曾命中真實 Function URL/帳號識別，現行工作樹掃描則乾淨。
- Function URL template 是 AuthType NONE；D9 仍為 TODO。
- Lambda artifacts/formal lock 使用 /tmp，不能跨容器保證。
- T8.5/T8.6 本機完成但曾未部署，live fixture 仍是舊 commit。

IN_SCOPE:
1. 建立或更新 .kiro/specs/hoyabit-remediation/requirements.md、design.md、tasks.md。
2. 在 docs/COMPETITION_TASK_STATUS.yaml 新增獨立的 remediation 區段 K0–K9；不得改寫既有 T0–T8.6 歷史事實。
3. 將本提示詞文件 docs/KIRO_REMEDIATION_TASK_PROMPTS.md 視為 K0-owned input，納入 K0 唯一 commit；除非修正文義錯誤，保持內容不變。這可避免後續 clean-worktree gate 被本未追蹤檔案阻擋。
4. Requirements 必須涵蓋：資料品質、raw provenance、S3 persistence、durable formal lock、single/comparison 入口同等、plan-driven collection、counter-evidence、offline/live 驗收、文件/提交、teardown。
5. Design 必須凍結下列邏輯契約及相容策略：
   - Public ResearchRequest：single/comparison、coins、question；匿名/public `AuthType NONE` 入口由伺服器強制 test mode，拒絕 formal/rerun 與 client override 的 live/use_llm/fulltext/as_of。
   - Trusted FormalRunCommand/RerunAuthorization：只允許本機 operator 或 IAM direct invocation/control plane 注入；authorization 綁定 formal_subject_hash、rerun_of、approver、one-time nonce、issued_at/expiry，匿名偽造或 replay 必須 403/拒絕。
   - Formal identity：versioned `formal_subject_hash` 固定 normalized question + canonical coin set；所有會影響輸出的執行設定由伺服器固定並另算 `execution_config_hash`，client 不得用 as_of/live/fulltext 等參數繞過 one-shot。兩個 hash 都進 manifest/lock evidence。
   - RawSourceEnvelope/RawSnapshotCandidate：redacted locator、exact original bytes/stream、SHA-256、byte_count、content_type、fetched_at、保存/拒存決策；transport boundary 在 parse 前建立，raw bytes 絕不傳入 LLM。
   - ValidatedEvidenceBundle：accepted、quarantined、flags、metrics、raw hashes/provenance，以及不會傳入 LLM 的 RawSnapshotCandidate handles/sink events。
   - ArtifactStoreFactory + ArtifactDescriptor + ArtifactQuery/DownloadService：write/read/completed-only list/immutable finalize/public-safe descriptor/error contract；Local 與 S3 語意一致，核心不直接假設 Path，internal URI 不進 public response。
   - FormalRunRepository：atomic acquire、conditional state transition、history、authorized rerun lineage。正式狀態順序固定為 acquire → execute → S3 manifest/hash verified → conditional completed；中途失敗保留 failed/indeterminate，不得降級到 local lock/store 後宣稱 formal 成功。
6. 明定 ownership 邊界：K1 只擁有品質閘門；K2 只擁有 artifacts；K3 只擁有 lock；K4 重用 K1；K5 只整合入口；K6 只驗收；K7/K8/K9 各自有人工 gate。
7. Tasks 必須逐項列 dependencies、acceptance、target commit、mandatory stop，K4 標 optional。

OUT_OF_SCOPE:
- 不修改 src/、tests/、aws/、lambda_handler.py 或 UI。
- 不安裝依賴、不呼叫外網/API/Bedrock/AWS。
- 不處理或印出秘密值；不改 Git 歷史。
- 不因 tasks.md checkbox 過期就擅自改成完成；docs/COMPETITION_TASK_STATUS.yaml 是現況正本。

ACCEPTANCE:
- 新 Spec 沒有 TBD 的行為契約；未知外部狀態一律標 PENDING-HUMAN。
- 每一項需求都能追到 Task 與可觀測驗收條件。
- Design 有 migration、local fallback、rollback 與資料/安全風險。
- K0 preflight 表列 VERIFIED、FAIL、PENDING-HUMAN，不把未查的 AWS 狀態寫成 PASS。
- Python <3.10 時記為 release blocker；不要擅自升級系統 Python。

VERIFY:
- git diff --check
- 逐一檢查新 Spec 的需求 ID 是否在 tasks.md 有對應。
- 重新執行 git status --short，確認只修改 Task-owned 文件。

STATUS/COMMIT:
- 開始寫 Spec 前，在新 remediation 區段設定 `current_task: K0`、`K0.status: IN_PROGRESS`、實際 `started_at/last_updated_at`；完成後再依證據轉最終狀態。
- 在同一 task commit 寫 final status、test_summary、artifacts、blockers、known_limitations 與 target commit subject；實際 commit hash 只在 final report 回報，不建立第二個 backfill commit。
- K0 只有在所有本機驗收完成時才標 PASS；外部狀態保留 PENDING-HUMAN 不等於捏造通過。
- Stage 只包含 K0 檔案，建立一個 local commit；禁止 push。
- 回報實際 commit hash 後停止。

FINAL REPORT:
TASK、STATUS、CONCLUSION、FILES_CHANGED（含 file:line）、TEST_COMMANDS、TEST_RESULTS、COMMIT、KNOWN_LIMITATIONS、BLOCKERS、UNVERIFIED、NEXT_TASK_READY，再附 PREFLIGHT MATRIX 與 CONTRACT DECISIONS。不要貼長日誌或秘密值。
```

---

## K1 — 統一資料品質閘門（目前最明確缺少的清洗流程）

模型：**Claude Opus 4.8**
推理強度：**XHigh**

```text
只執行 Task K1，完成後停止；不要開始 K2。

TASK_ID: K1
DEPENDS_ON: K0 PASS
OBJECTIVE: 建立 deterministic raw → normalize → validate → quarantine → metrics 管線，保證不合格資料在任何指標、可信度、訊號庫或 LLM 前被攔截。
TARGET_COMMIT: feat(K1): add deterministic data quality gate

你是唯一寫入者。不得回復他人變更或做無關重構。先讀 AGENTS.md、全部 workspace steering、#spec:hoyabit-remediation；若 context provider 不可用，直接讀 .kiro/specs/hoyabit-remediation/{requirements.md,design.md,tasks.md}。再讀 docs/COMPETITION_TASK_STATUS.yaml、src/ohlcv.py、src/day2_sources.py、src/orchestrator.py、src/validation.py、src/credibility.py 及相關 tests。

SCOPE AUTHORIZATION: 使用者貼上本 K1 即明確授權只在本 Task 範圍內重開 T8 feature freeze，實作資料品質修復。這不授權其他新功能、AWS/live、降低安全或跳過測試。

PREFLIGHT:
- git status --short --branch、git log -5 --oneline、python3 --version。
- K0 必須 PASS；若實際檔案與凍結契約矛盾，停止並提出最小 spec 修正，不要自行發明第二套契約。
- 記錄 baseline targeted tests 與 full suite；不連真實網路。
- Dependency/preflight 通過後，先設定 `current_task: K1`、`K1.status: IN_PROGRESS`、實際 `started_at/last_updated_at`，再修改程式。

IN_SCOPE:
- 新增 src/data_quality.py、tests/test_data_quality_gate.py。
- 只在必要時修改 src/ohlcv.py、src/day2_sources.py、src/orchestrator.py、src/validation.py 與直接相關測試/文件。
- 提供統一 gate_records(kind, records, *, as_of, context) -> QualityResult，名稱可依 K0 契約調整，但只能有一套 canonical contract。
- Transport boundary 必須在 decode/JSON parse 前建立 K0 的 RawSourceEnvelope；QualityResult 至少含 accepted、quarantined(reason codes)、flags、metrics、raw hashes/provenance，以及受大小/授權政策約束的 RawSnapshotCandidate/sink events。raw bytes 不得進 Evidence payload/LLM，且不可用 parse 後重新序列化內容冒充原始 bytes。輸出與 reason 排序必須 deterministic。

REQUIRED BEHAVIOR:
1. Raw provenance：decode/parse 前由 HTTP/CSV transport helper 對 exact source bytes 建 RawSourceEnvelope 並算 SHA-256；記 redacted locator、byte_count、media type、fetched_at。以 bounded RawSnapshotCandidate/sink contract 交給 K2；超過大小、來源授權或隱私政策時只保留 hash/metadata/reason。不得把 authorization headers、tokens、cookies 或秘密寫入 artifact/log。
2. Normalize：canonical coin/pair/unit、數值型別、UTC-aware timestamp、stable chronological sort。無時區日期只有在明示 source contract 下才假設 UTC，並加 timezone_assumed_utc flag。
3. Validate：拒絕 missing/bad date、NaN/Inf、price<=0、volume<0、high<max(open,close)、low>min(open,close)、timestamp>as_of。
4. Closed candle：close_time>as_of 的 K 棒 quarantine；daily CSV 依 UTC 日界推導 close_time。未收盤棒不得進指標。volume==0 僅 warning。
5. Duplicate：完全相同 canonical key/value 保留第一筆並計數；同 key 不同值整組 quarantine 為 conflicting_duplicate，不得 last-write-wins。
6. Outlier：以文件化 deterministic 規則標 extreme_return/extreme_volume；只要 schema/invariant 合法就保留，禁止 winsorize、替換或靜默刪除。
7. Quarantine：保存 key、source/hash、reason_codes、bounded record reference；可用資料不足時走既有 unavailable/fallback，並把品質失敗原因留在 provenance。
8. Metrics：每來源記 input/accepted/quarantined/duplicate/flag counts、reason_counts、as_of、closed_through、raw hashes；聚合到 execution_log.data_quality 與 manifest.validation，不改六項必交 artifact 名稱。
9. Wiring：所有 fetch_* 必須先 gate raw series 再算 return/RSI/bias/trend/sentiment；local CSV 先 gate 再 slice/window。Orchestrator 在 collect/inject 後先做 Evidence structural validation，再 scoring，並在 _signal_inventory/analyze_with_llm 前完成 post-score validation。
10. 所有 live/offline/counter/comparison Evidence 必須走同一閘門；不得留下繞過路徑。

OUT_OF_SCOPE:
- 不改 credibility/claim/citation 公式、LLM prompts、UI、source registry 權重或六項 artifact 名稱。
- 不新增第三方依賴、不連真實 API、不「修飾」歷史價格。
- 不在本 Task 實作 S3；只提供 K2 可持久化的 hash/provenance contract。

REQUIRED TESTS:
- CSV fixture 同時含 unsorted、bad date、null、NaN、Inf、future/open candle、negative volume、OHLC 違規、相同/衝突 duplicate；reason/count 精確，壞列不進 indicator。
- 合法但 100x 跳價保留並標 extreme_return；zero volume 保留警告；重跑結果 byte-stable。
- Binance current kline、CoinGecko future point、TVL/多空比亂序重複、news/social future/duplicate item，驗證 UTC、as_of、closed candle 與 item-level quarantine。
- 固定 raw bytes 的 hash 必須等於 hashlib.sha256(bytes).hexdigest()，並能由 Evidence、execution log、manifest 追到。
- spies/mocks 證明 gate 早於所有 derived calculations、_signal_inventory、analyze_with_llm；全 quarantine 時 consumer 不得被呼叫。
- 目前五份正常 CSV 仍可通過，但疑似未收盤的最後一棒必須依 as_of 正確排除。

VERIFY:
- python3 -m unittest tests.test_data_quality_gate tests.test_real_inputs tests.test_day8_stance_history_tvl tests.test_production_readiness tests.test_funding_rate_providers tests.test_social_sources tests.test_credibility -v
- python3 -m unittest discover -s tests
- python3 -m compileall -q src tests lambda_handler.py
- git diff --check
- 執行一個 live=False,use_llm=False 的 offline E2E，確認六項產物、manifest hash 與 data_quality metrics。

STATUS/COMMIT:
- 更新 docs/COMPETITION_TASK_STATUS.yaml 的 K1；PASS 只在 targeted、full suite、offline E2E 全通過且沒有 bypass 時成立。
- 在同一 task commit 寫 final status、test_summary、artifacts、blockers、known_limitations 與 target commit subject；實際 hash 只回報，不另建 backfill commit。
- Stage 只包含 K1 檔案，建立單一 local commit；禁止 push。

FINAL REPORT: TASK、STATUS、CONCLUSION（最多 10 行）、FILES_CHANGED（file:line）、TEST_COMMANDS、TEST_RESULTS、COMMIT、KNOWN_LIMITATIONS、BLOCKERS、UNVERIFIED、NEXT_TASK_READY，再附 DATA FLOW 與 REASON CODES。不要貼原始長日誌。
```

---

## K2 — S3 持久化 Run Bundle 與跨冷啟動下載

模型：**Claude Opus 4.8**
推理強度：**XHigh**

```text
只執行 Task K2，完成後停止；不要開始 K3，也不要部署 AWS。

TASK_ID: K2
DEPENDS_ON: K0 PASS, K1 PASS
OBJECTIVE: 在保留 LocalArtifactStore/offline fallback 的前提下實作私有 S3 ArtifactStore，使單幣與比較 bundle 可跨 Lambda 冷啟動列出、驗證與下載。
TARGET_COMMIT: feat(K2): persist run artifacts in Amazon S3

你是唯一寫入者。先讀 AGENTS.md、workspace steering、#spec:hoyabit-remediation；若 context provider 不可用，直接讀 .kiro/specs/hoyabit-remediation/{requirements.md,design.md,tasks.md}。再讀 docs/COMPETITION_TASK_STATUS.yaml、src/ports.py、src/artifact_store.py、src/run_manager.py、src/orchestrator.py、src/app.py、lambda_handler.py、aws/template.yaml、aws/deploy.sh 與 download/artifact tests。

SCOPE AUTHORIZATION: 使用者貼上本 K2 即明確授權只在本 Task 範圍內重開 T8 feature freeze，完成先前 D6/S3 持久化；不授權 deploy/live 或其他功能。

PREFLIGHT:
- git status --short --branch、git log -5 --oneline、python3 --version。
- K1 必須 PASS；不處理重疊 dirty changes。
- 所有測試使用 fake/in-memory S3 client；禁止讀 AWS credentials、呼叫真實 S3/SAM deploy 或安裝新套件。
- Dependency/preflight 通過後，先設定 `current_task: K2`、`K2.status: IN_PROGRESS`、實際 `started_at/last_updated_at`，再修改程式。

IN_SCOPE:
1. 依 K0 契約完成 ArtifactStoreFactory、ArtifactDescriptor、ArtifactQuery/DownloadService 與 S3 實作；Local 實作與既有呼叫保持相容。移除 orchestrator/application service 對 direct Path 的核心假設。AWS runtime 可使用 boto3，但 import 必須延遲或可注入，讓本機 offline/test 不依賴 AWS。
2. Canonical key 至少包含 environment/run_id，拒絕 `..`、絕對路徑、編碼後分隔符與越界 key。comparison bundle 必須能追到兩個 child run 與 comparison artifact。
3. 寫入協定：先寫內容檔，再寫 manifest；manifest 是 completed marker。每個物件保存 content type、SHA-256、run_id、code_commit 與 schema version；讀回時重算 hash。
4. 保存六項必交 artifact，以及 K1 提供的 quality/raw provenance。只有來源授權、隱私與大小政策允許時，原樣保存 RawSnapshotCandidate 的 exact bytes；S3 object bytes/hash 必須與 envelope 完全相同，禁止重新序列化後補算 hash。否則只保存 hash、redacted locator、時間與拒存原因，禁止保存 tokens/headers/cookies/個資或未授權全文。
5. K2 唯一擁有 ArtifactQuery/DownloadService；其依 run_id 從 S3 讀取，不再依賴命中同一 /tmp container。Local UI 繼續使用本地 store。K2 可提供 Lambda/local adapter 需要的 service，但不重構 HTTP dispatch；404/409/完整性錯誤需明確，不得落回首頁。
6. aws/template.yaml 加入私有 bucket、BlockPublicAccess、server-side encryption、合理 lifecycle/retention、最小 IAM、環境變數與 outputs；不得建立公開 bucket/ACL。
7. 失敗語意：partial upload 不得被列為 completed；manifest 寫入失敗可安全重試；讀到 hash mismatch 必須 fail closed 並記錄，不回傳損壞 ZIP。
8. 更新 docs/aws-architecture.md，明確區分本機 fallback 與部署模式。
9. 新增 tests/test_s3_artifact_store.py；Template/IAM 回歸優先擴充既有 tests/test_d7_deployment_guardrails.py，不另造功能重疊的測試層。

OUT_OF_SCOPE:
- 不實作 formal lock；S3 普通 read-modify-write 不能當 K3 的 distributed lock。
- 不改研究內容、LLM、collector 或 scoring。
- 不部署、不建立 bucket、不打 live URL、不執行付費呼叫。

REQUIRED TESTS:
- Local/S3 contract tests：write/read/list/finalize/URI、Unicode、content type、hash mismatch、partial manifest、idempotent retry。
- path traversal 與 malicious run_id/key 全部拒絕。
- 模擬兩個獨立 Lambda instance：A 寫入，B 在沒有共享 /tmp 的情況下仍能下載相同 ZIP/單檔。
- single/comparison bundle 均可列出且 manifest/child lineage 正確。
- S3 unavailable 時，local/offline 模式仍正常；部署模式不得假裝已持久化。
- Template 靜態測試驗證 public access block、encryption、IAM resource scope、env wiring。
- 固定 RawSourceEnvelope bytes 經 K1→K2 後，S3 bytes 與 SHA-256 bit-for-bit 相同；重新 serialize JSON 造成 bytes 不同時測試必須抓到。

VERIFY:
- python3 -m unittest tests.test_s3_artifact_store tests.test_lambda_download_routes tests.test_d7_deployment_guardrails -v
- python3 -m unittest discover -s tests
- python3 -m compileall -q src tests lambda_handler.py
- bash aws/deploy.sh --dry-run
- git diff --check

STATUS/COMMIT:
- 本機實作完成但未真實部署時，K2 可對 local implementation 標 PASS；AWS live verification 必須明確留給 K7，不可寫成已驗證。
- 更新 K2 status、test summary、known limitations；建立一個 local commit，禁止 push。
- 同一 commit 必須含 artifacts/blockers/target subject；實際 hash 只回報，不另建 backfill commit。

FINAL REPORT: TASK、STATUS、CONCLUSION、FILES_CHANGED（file:line）、TEST_COMMANDS、TEST_RESULTS、COMMIT、KNOWN_LIMITATIONS、BLOCKERS、UNVERIFIED、NEXT_TASK_READY，再附 KEY/FINALIZE CONTRACT、SECURITY CONTROLS、DRY-RUN RESULT 與 AWS_UNVERIFIED。禁止輸出 bucket 名、帳號 ID、完整 URL 或憑證。
```

---

## K3 — DynamoDB 原子 Formal Lock 與 Authorized Rerun

模型：**Claude Opus 4.8**
推理強度：**Max**

```text
只執行 Task K3，完成後停止；不要開始 K4，也不要部署 AWS。

TASK_ID: K3
DEPENDS_ON: K0 PASS, K2 PASS
OBJECTIVE: 以真正的 atomic conditional write 實作跨容器 formal one-shot；保留 local repository、失敗歷史與明確 authorized rerun lineage。
TARGET_COMMIT: feat(K3): add durable formal run locking

你是唯一寫入者。先讀 AGENTS.md、workspace steering、#spec:hoyabit-remediation；若 context provider 不可用，直接讀 .kiro/specs/hoyabit-remediation/{requirements.md,design.md,tasks.md}。再讀 docs/COMPETITION_TASK_STATUS.yaml、src/run_manager.py、src/ports.py、src/run_context.py、src/orchestrator.py、src/app.py、lambda_handler.py、aws/template.yaml 與 formal-run tests。

SCOPE AUTHORIZATION: 使用者貼上本 K3 即明確授權只在本 Task 範圍內重開 T8 feature freeze，並對 `.kiro/steering/competition-execution.md` 與舊 AWS spec 的「不導入 DynamoDB」作有界例外，以解決跨容器 atomic formal lock。此例外只授權本機程式/template/tests/docs，不授權建立或部署 AWS 資源。

PREFLIGHT:
- git status --short --branch、git log -5 --oneline、python3 --version；K2 必須 PASS。
- 先以短 ADR 重述一致性模型、failure semantics、authorized rerun、migration/rollback；若 K0 契約不足，先停下回報，不要用 S3 read-modify-write 冒充原子鎖。
- 禁止 AWS network、credentials、deploy 或真實 table 操作；使用 deterministic fake client。
- Dependency/preflight 通過後，先設定 `current_task: K3`、`K3.status: IN_PROGRESS`、實際 `started_at/last_updated_at`，再修改程式。

IN_SCOPE:
1. 依 FormalRunRepository contract 保留 Local 實作並新增 DynamoDB 實作；RunManager 不得硬依賴 LocalArtifactStore。
2. 第一次 formal acquire 以 versioned formal_subject_hash 為 key 並使用 ConditionExpression；兩個同時請求必須恰好一個成功。Formal mode 的 live/use_llm/fulltext/as_of/model/dataset config 由 server/operator policy 固定，另存 execution_config_hash；public client 不得改參數繞過 one-shot。
3. Public/匿名 request 一律不得呼叫 acquire 或 authorized rerun。FormalRunCommand/RerunAuthorization 只接受 trusted local/IAM control plane，並驗證 subject hash、rerun_of、approver、one-time nonce、issued_at/expiry；spoof/replay 必須拒絕。
4. Lock 需跨冷啟動持久存在。失敗/timeout 仍保留紀錄，不可自動釋放後悄悄再跑；只有完整且尚未消耗的 RerunAuthorization 才能建立新 lineage。
5. State transition 必須以 run_id/version/latest_run_id 條件更新，舊執行不得覆寫新狀態。兩個同時 authorized rerun 只能一個 CAS 成功；history 的順序與內容 deterministic，不能用 table scan 作正常路徑。
6. 狀態順序固定為 acquire → execute → K2 S3 manifest/hash verified → conditional completed。任何 execution/artifact/finalize/hash/state-update failure 保留 failed/indeterminate，不得 fallback 到 local 後標 completed。
7. Lambda deployed formal 使用 Dynamo repository；本機/offline test 可用 Local repository。未配置 table 時部署 formal 必須 fail closed，不能宣稱 durable。
8. aws/template.yaml 加 table、key/index（如需要）、PITR/加密/最小 IAM/環境變數；正式 lock 與 nonce 不設會意外開放重跑的短 TTL。
9. 既有 409 conflict、canonicalization、authorized rerun UX 與 artifacts lineage 維持可理解相容；匿名 formal/rerun 使用 403。
10. 更新架構與操作文件，包含 control-plane 邊界，以及 rollback 到 Local 僅供非正式模式使用的限制。
11. 新增 tests/test_durable_formal_lock.py，並擴充既有 tests/test_run_manager.py、tests/test_t7_formal_run.py 與 tests/test_d7_deployment_guardrails.py。

OUT_OF_SCOPE:
- 不修改 S3 ArtifactStore、研究邏輯、資料品質、collector、LLM 或 UI 外觀。
- 不執行正式 run、不觸發 live URL、不建立/刪除 DynamoDB table。

REQUIRED TESTS:
- 兩個 thread/process-like client 同時 acquire，同一 hash 恰好 1 success + 1 conflict。
- 兩個不同 repository instance 模擬不同冷啟動，第二個仍拒絕重複 formal。
- failure、timeout、completed、非法 transition、stale writer、authorized/unauthorized rerun、history lineage。
- 不同 coin/order/case/whitespace 的 normalization 規則有測試；comparison coin set 不因順序產生意外重複或逃逸。
- Dynamo unavailable/throttle 時不得放行 formal；test/offline 的明示 local fallback 不受影響。
- Template/IAM 靜態測試。
- 匿名/public formal、client 偽造 authorized_rerun、過期/錯 subject nonce、replay nonce 全部 403/拒絕，且不建立 lock/artifact。
- execution、S3 write、manifest finalize/hash verify、Dynamo completed update 每個 failure point 都留下正確 failed/indeterminate 狀態；不得 completed。
- 兩個同時合法 rerun authorization 競爭同一 latest version 時只有一個成功，另一個 conflict；nonce 只能消耗一次。
- formal_subject_hash/execution_config_hash 與 manifest/lock 使用同一 canonicalization；client 改 as_of/live/fulltext 不得建立第二個 formal entitlement。

VERIFY:
- python3 -m unittest tests.test_durable_formal_lock tests.test_run_manager tests.test_t7_formal_run tests.test_production_readiness tests.test_d7_deployment_guardrails -v
- python3 -m unittest discover -s tests
- python3 -m compileall -q src tests lambda_handler.py
- bash aws/deploy.sh --dry-run
- git diff --check

STATUS/COMMIT:
- 本機 atomic contract/tests 完整可標 implementation PASS；真實 DynamoDB concurrency 留 K7 驗證。
- 更新 K3 status，建立單一 local commit，禁止 push。
- 同一 commit 必須含 test_summary/artifacts/blockers/known_limitations/target subject；實際 hash 只回報，不另建 backfill commit。

FINAL REPORT: TASK、STATUS、CONCLUSION、FILES_CHANGED（file:line）、TEST_COMMANDS、TEST_RESULTS、COMMIT、KNOWN_LIMITATIONS、BLOCKERS、UNVERIFIED、NEXT_TASK_READY，再附 CONSISTENCY MODEL、FAILURE/RERUN SEMANTICS、CONCURRENCY EVIDENCE、DRY-RUN 與 AWS_UNVERIFIED。
```

---

## K4 — ResearchPlan 真正驅動採集與主動反證（加分項）

模型：**Claude Opus 4.8**
推理強度：**XHigh**

```text
只執行 Task K4，完成後停止；不要開始 K5。

TASK_ID: K4
DEPENDS_ON: K1/K2/K3 PASS，避免與 orchestrator/storage/run-lifecycle 變更衝突
PRIORITY: OPTIONAL SCORE ENHANCER；若使用者略過，不要執行本 Task，K4 保持 TODO。後續只能在使用者明確接受 dependency exception 後，以 `PARTIAL`/未完成事實繼續，allowed statuses 沒有 DEFERRED。
OBJECTIVE: 讓 validated ResearchPlan 的 time window/required domains 實際控制 collectors，並為 hypothesis/evidence-gap 執行最多一輪有界 counter-evidence collection；所有新資料重走 K1 gate。
TARGET_COMMIT: feat(K4): drive collection from the research plan

你是唯一寫入者。先讀 AGENTS.md、workspace steering、#spec:hoyabit-remediation；若 context provider 不可用，直接讀 .kiro/specs/hoyabit-remediation/{requirements.md,design.md,tasks.md}。再讀 ResearchPlan/collector/orchestrator/deadline/comparison/claim tests 與 docs/COMPETITION_TASK_STATUS.yaml。

SCOPE AUTHORIZATION: 使用者貼上本 K4 即明確授權只在本 Task 範圍內重開 T8 feature freeze，實作 plan-driven/counter-evidence；不授權新 provider、無界 agent loop、live 或部署。

PREFLIGHT:
- git status --short --branch、git log -5 --oneline、python3 --version。
- 以 code evidence 確認目前 ResearchPlan 是否只被記錄/計分、CoinGecko 是否固定 14 天、collection 是否只有一輪；不要依稽核敘述直接假設。
- 禁止 live/network/Bedrock；planner 用 mocks/deterministic fallback。
- Dependency/preflight 通過後，先設定 `current_task: K4`、`K4.status: IN_PROGRESS`、實際 `started_at/last_updated_at`，再修改程式。

IN_SCOPE:
1. 依 K0 凍結 CollectionPlan/CollectionTask contract，建立單一 domain→collector capability registry；只接受 canonical domains，未知/模型生成值不得變成任意函式或 URL。
2. plan.time_window 轉成 bounded collection parameters；所有來源保留其 API 限制與降級。比較題兩腳共用 as_of、cutoff 與 window。
3. 核心市場/歷史資料的必要最小集合要有明確政策；不能因 planner 漏欄位讓指標靜默失效。
4. 只有 hypothesis 題或 deterministic evidence-gap 規則觸發 counter round；最多 1 round、bounded queries/items/time，保留至少 60 秒 finalization buffer，且不得提高現行較保守的內部 deadline。
5. Counter query 只能由 allowlisted template/coin/domain/claim gap 組合，不得讓模型產生任意 endpoint、程式碼或 shell。
6. 每筆補查 Evidence 記 collection_round、hypothesis/claim、query template、collector、as_of 與 raw provenance；通過 K1 後才能進 scoring/claims/LLM。
7. 無 counter evidence 時誠實記 insufficient/limitations，不用 fallback fixture 偽裝新的反證。
8. execution log 顯示 plan→selected collectors→rounds→budget→stop reason。
9. 固定 stage 順序：baseline collect → K1 validate/quarantine → deterministic gap detection → 最多一輪 counter collection → K1 validate/quarantine → merge/dedupe → recompute credibility/signals/claims → LLM。不得用初輪舊 score/claim 直接混入補查後結果。
10. 新增 tests/test_plan_driven_collection.py，集中驗證 collector selection、bounded counter round、stage order、deadline 與 comparison cutoff。

OUT_OF_SCOPE:
- 不新增付費 provider、不擴增 X/Twitter、不重寫 scoring/claim/citation engine。
- 不允許無上限 retry、遞迴 research loop 或模型自行決定完成標準。
- 不連真實來源或部署。

REQUIRED TESTS:
- 不同 time window/domain plan 會選到預期 collectors/參數，且不需要的 collector 不被呼叫。
- planner 非法 domain/window 會 deterministic clamp/reject/fallback。
- hypothesis 觸發一輪 counter；普通題不觸發；第二輪後必停。
- counter data 的 future/duplicate/invalid payload 被 K1 quarantine，且不進 LLM。
- comparison 兩腳相同 cutoff/window；deadline 接近時跳過補查並完整寫出六項 artifact。
- planner/collector/counter failure 仍產生誠實的 offline fallback/limitations。
- Spy/order test 證明兩輪都經 K1，merge 後才重算 score/claims 並呼叫 LLM；初輪舊衍生結果不殘留。

VERIFY:
- python3 -m unittest tests.test_plan_driven_collection tests.test_planner tests.test_t2_planner_wiring tests.test_day2_orchestrator tests.test_day9_pipeline tests.test_competition_readiness tests.test_data_quality_gate -v
- python3 -m unittest discover -s tests
- python3 -m compileall -q src tests lambda_handler.py
- 以 live=False,use_llm=False 跑一般題、假設題、比較題三種 offline E2E，核對 plan/round/provenance/artifact hash。
- git diff --check

STATUS/COMMIT:
- 更新 K4 status；所有 bounded-loop、deadline 與 provenance 驗收通過才可 PASS。
- 建立單一 local commit，禁止 push。
- 同一 commit 必須含 test_summary/artifacts/blockers/known_limitations/target subject；實際 hash 只回報，不另建 backfill commit。

FINAL REPORT: TASK、STATUS、CONCLUSION、FILES_CHANGED（file:line）、TEST_COMMANDS、TEST_RESULTS、COMMIT、KNOWN_LIMITATIONS、BLOCKERS、UNVERIFIED、NEXT_TASK_READY，再附 PLAN→COLLECTOR MAPPING、COUNTER BOUNDS 與 E2E RESULTS。
```

---

## K5 — 本機與 Lambda 入口功能同等

模型：**Claude Sonnet 4.6**
推理強度：**Max**

```text
只執行 Task K5，完成後停止；不要開始 K6，也不要部署 AWS。

TASK_ID: K5
DEPENDS_ON: K0/K1/K2/K3 PASS；K4 可 PASS，或由使用者明確接受 K4 未完成/PARTIAL 的 dependency exception
OBJECTIVE: 以共用 application service 消除 src/app.py 與 lambda_handler.py 的行為漂移，使兩入口支援相同 single/comparison、history、fulltext、mode、artifact 與 error contract。
TARGET_COMMIT: refactor(K5): unify local and Lambda research entrypoints

你是唯一寫入者。先讀 AGENTS.md、workspace steering、#spec:hoyabit-remediation；若 context provider 不可用，直接讀 .kiro/specs/hoyabit-remediation/{requirements.md,design.md,tasks.md}。再讀 src/app.py、lambda_handler.py、src/orchestrator.py、src/run_manager.py、ArtifactStore/FormalRunRepository ports、全部 route/download/comparison/history tests 與 docs/COMPETITION_TASK_STATUS.yaml。

SCOPE AUTHORIZATION: 使用者貼上本 K5 即明確授權只在本 Task 範圍內重開 T8 feature freeze，進行入口相容重構；不授權 UI 重設計、live 或部署。

PREFLIGHT:
- git status --short --branch、git log -5 --oneline、python3 --version。
- 以實際程式確認：Lambda 是否只呼叫 single run、是否把短期 market CSV 錯當 5 年 history、是否少 fulltext、local/Lambda 是否各自 dispatch routes。
- 不處理重疊 dirty changes；禁止 live/network/AWS/deploy。
- Dependency/preflight 通過後，先設定 `current_task: K5`、`K5.status: IN_PROGRESS`、實際 `started_at/last_updated_at`，再修改程式。

IN_SCOPE:
1. 實作/完成 K0 Public ResearchRequest 與單一 application service；HTML/HTTP adapters 只解析/呈現，不各自複製 business dispatch。ArtifactQuery/DownloadService 由 K2 擁有，K5 只接線，不另造第二套。
2. Local trusted operator 與 IAM direct invocation/control plane 可使用 FormalRunCommand；public Lambda Function URL（尤其 AuthType NONE）只支援 server-forced test mode，任何 client `mode=formal`、authorized_rerun 或受保護設定 override 一律 403 且不得進 pipeline。
3. Local 與 public Lambda 都支援 single coin、two-coin comparison、question 與安全的 test flow；history/fulltext/live/use_llm/as_of 依 server policy 注入，不信任 public client。Trusted formal/rerun 走獨立 control-plane contract。
4. Lambda 的短期 live market download 與封裝內五年 history 必須使用不同路徑/語意；不得覆寫 data/<COIN>.csv 或把 15 天資料當 history。
5. Comparison 兩腳共用 plan/cutoff，輸出可透過 K2 store 下載；single/comparison 都回一致的 status/error/artifact descriptors。
6. /download 與 /artifact 接 K2 共用 service/allowlist；未知 route 正確 404，不得靜默回首頁。
7. Local filesystem path 不得洩漏到 public response；S3 URI/內部 bucket 名也不得直接暴露，使用受控 download route。
8. 保留現有 stdlib UI 與 offline fallback；不要求 HTML 像素一致，只要求功能/語意/狀態一致。
9. 新增 tests/test_entrypoint_parity.py，承載完整 entrypoint matrix、public formal=403、history/fulltext 與 artifact/error parity。

OUT_OF_SCOPE:
- 不重設計 UI、不改研究/清洗/scoring/claim 邏輯、不新增 web framework。
- 不建立 AWS 資源、不打 live URL、不執行 Bedrock。

REQUIRED TEST MATRIX:
- entrypoint ∈ {local,Lambda} × request ∈ {single,comparison} × mode ∈ {test,formal} × provider ∈ {offline,mocked-live}。
- 驗證相同 request 產生同一語意的 request normalization、error code、artifact set、history/fulltext flags。
- Lambda comparison 不再落回 single；兩腳和 comparison artifact 可下載。
- public/anonymous `mode=formal`、authorized_rerun、受保護 config override 均回 403；spoof/replay 不呼叫 RunManager/LLM/collector。Trusted local/IAM command 仍有完整 formal/rerun 測試。
- history 使用 5 年 CSV，短期 market data 保留獨立用途；回歸測試要能抓到兩者互換的舊 bug。
- path traversal、非法 coin、same-coin comparison、missing question、duplicate formal、store unavailable。
- 現有首頁、single、本機 comparison 與 download tests 不退化。

VERIFY:
- python3 -m unittest tests.test_entrypoint_parity tests.test_lambda_download_routes tests.test_t6_web_demo tests.test_t7_formal_run tests.test_run_manager tests.test_day9_pipeline tests.test_production_readiness -v
- python3 -m unittest discover -s tests
- python3 -m compileall -q src tests lambda_handler.py
- bash aws/deploy.sh --dry-run
- tests.test_entrypoint_parity 必須以兩入口 in-process harness 跑 offline single + comparison，驗證六項 artifacts/child lineage/hash；不可只做 shallow route mock。
- git diff --check

STATUS/COMMIT:
- 更新 K5 status；只有完整 matrix、full suite、offline E2E、dry-run 全通過才 PASS。
- 建立單一 local commit，禁止 push。
- 同一 commit 必須含 test_summary/artifacts/blockers/known_limitations/target subject；實際 hash 只回報，不另建 backfill commit。

FINAL REPORT: TASK、STATUS、CONCLUSION、FILES_CHANGED（file:line）、TEST_COMMANDS、TEST_RESULTS、COMMIT、KNOWN_LIMITATIONS、BLOCKERS、UNVERIFIED、NEXT_TASK_READY，再附 SHARED SERVICE CONTRACT、PARITY MATRIX 與 DRY-RUN RESULT。
```

---

## K6 — 離線 Release Candidate 獨立驗收

模型：**Claude Opus 4.8**
推理強度：**Max**

```text
只執行 Task K6。你是獨立 reviewer，不是修復者；發現問題時記 FAIL/BLOCKED 並停止，不得順手改產品程式。

TASK_ID: K6
DEPENDS_ON: K1/K2/K3/K5 PASS；K4 必須是 PASS，或有使用者明確接受其未完成/PARTIAL 的 dependency exception
OBJECTIVE: 對候選 commit 做可重現的離線品質、安全與 release gate，產出證據而非主觀判斷。
TARGET_COMMIT: test(K6): verify the offline release candidate

先讀 AGENTS.md、workspace steering、#spec:hoyabit-remediation；若 context provider 不可用，直接讀 .kiro/specs/hoyabit-remediation/{requirements.md,design.md,tasks.md}。再讀兩個既有 specs、docs/COMPETITION_TASK_STATUS.yaml、docs/COMPETITION_CHECKLIST.md、docs/COMPETITION_BLOCKERS.md、docs/COMPETITION_RUNBOOK.md、docs/DEMO_RUNBOOK.md、docs/recording-runbook.md、aws/template.yaml、scripts/check_secrets.sh 與所有測試/fixtures。

SAFETY:
- 禁止修改 src/、lambda_handler.py、aws/template.yaml 或測試來讓驗收通過。
- 禁止 network/live API/Bedrock/AWS/deploy、git push/merge/tag/PR/history rewrite。
- 秘密掃描只回報類型與位置，不顯示值；若現有 history script 會印值，不直接執行未遮蔽版本。
- 不清除 dirty worktree；若候選 commit 不乾淨，K6 FAIL。
- Dependency/preflight 通過後，先設定 `current_task: K6`、`K6.status: IN_PROGRESS`、實際 `started_at/last_updated_at`；這只授權唯讀驗收與 K6 report/status/manifest verifier 寫入。

RELEASE GATE:
1. 記錄 branch、HEAD、upstream、git status、Python version。Python 3.10+ 是 release requirement；若目前環境較舊，使用既有安全環境重驗或標 BLOCKED，不擅自升級系統。
2. Full suite 全過且 0 skip；記實際數量，不沿用 683。
3. K1 adversarial DQ：future/open candle、NaN/Inf、OHLC、negative volume、duplicates、outlier flag、raw hash、validation-before-consumer。
4. 五幣與四類流程：BTC/ETH/SOL/BNB/XRP；一般多源、hypothesis、comparison、unknown；均 offline/test，不消耗 formal。
5. 每個 single bundle 六檔齊備、manifest hash 正確、同-run identity、Citation Gate 非 FAIL、quality metrics/provenance 完整。
6. K2：模擬不同 container 仍能從 S3 fake store 下載；corrupt/partial/path traversal fail closed。
7. K3：兩個獨立 repository/concurrent acquire 恰好一個 formal success；authorized rerun lineage 正確。只用 fake/local integration，不打 AWS。
8. K5：local/Lambda single/comparison/history/fulltext/download/error parity matrix 全過。
9. Bedrock timeout/invalid JSON、collector failure、deadline、partial-result preservation、offline fallback regression。
10. Secrets scan 使用 `bash scripts/check_secrets.sh >/dev/null 2>&1` 與 `bash scripts/check_secrets.sh --history >/dev/null 2>&1`，只記 exit code，不顯示匹配行。工作樹必須 exit 0；history 已知風險不能因工作樹乾淨就忽略。若證據顯示命中的 Function URL/credential 仍有效且未 containment，K6 必須 BLOCKED；外部狀態尚無法唯讀確認時標 PENDING-HUMAN，且 RELEASE_GO=false。
11. bash aws/deploy.sh --dry-run 成功；package 不含 .env、runs、credentials、cache 或不必要 raw data。

OUTPUT:
- 建立 scripts/verify_fixture_manifests.py：唯讀遞迴驗證指定 root 下 manifest 列出的相對路徑、path containment、檔案存在與 SHA-256；只輸出路徑/結果摘要，不輸出 artifact 內容，也不寫 fixture。新增 tests/test_fixture_manifest_verifier.py 驗證 success、missing、hash mismatch、path traversal。
- 建立 docs/RELEASE_CANDIDATE_REPORT.md，列 candidate SHA、環境、每個 gate 的 PASS/FAIL/BLOCKED、精簡命令證據、未驗證 AWS 項目、rollback point。
- 只有所有 offline 必要 gate PASS 才可記 `K6=PASS (OFFLINE_RC_PASS)`；這不代表可發布，`RELEASE_GO=false` 直到 K7/K8 與安全 containment 全部通過。任何 live/AWS 項目一律標 PENDING K7。
- 不更新 live-success fixture，不建 tag。

VERIFY:
- python3 -m unittest tests.test_competition_readiness.RequiredQuestionReadinessTests tests.test_competition_readiness.FailureInjectionReadinessTests -v
- python3 -m unittest tests.test_data_quality_gate tests.test_s3_artifact_store tests.test_durable_formal_lock tests.test_lambda_download_routes tests.test_t7_formal_run tests.test_day9_pipeline tests.test_production_readiness tests.test_fixture_manifest_verifier -v
- python3 -m unittest discover -s tests
- python3 scripts/verify_fixture_manifests.py demo-fixtures
- 上述 competition-readiness 與 targeted tests 必須涵蓋 offline single、hypothesis、comparison 三種 E2E；若沒有，K6 FAIL，不以手動口頭宣稱替代。
- `bash scripts/check_secrets.sh >/dev/null 2>&1`（只記 exit code）
- `bash scripts/check_secrets.sh --history >/dev/null 2>&1`（只記 exit code）
- bash aws/deploy.sh --dry-run
- git diff --check

STATUS/COMMIT:
- 只可修改 K6 report、status/checklist 的實際驗收欄位；不得把 T8/K7 未驗證項目改 PASS。
- 驗收 PASS 時建立單一 local commit；FAIL/BLOCKED 時也可提交純報告，但必須明確標示，禁止 push。
- 同一 commit 必須含 verifier/tests、final status、test_summary、artifacts、blockers、known_limitations 與 target subject；實際 hash 只回報，不另建 backfill commit。

FINAL REPORT: TASK、STATUS、CONCLUSION/GO-NO-GO、FILES_CHANGED、TEST_COMMANDS、TEST_RESULTS、COMMIT、KNOWN_LIMITATIONS、BLOCKERS、UNVERIFIED、NEXT_TASK_READY，再附 CANDIDATE SHA、ENVIRONMENT、GATE MATRIX、TEST COUNTS、AWS_UNVERIFIED 與 REQUIRED FIX OWNER。不得自行開始修復或 K7。
```

---

## K7 — 人工核准後的 AWS 部署與 Live E2E

模型：**Claude Opus 4.8**
推理強度：**Max**

```text
只執行 Task K7。先完成不接觸 AWS 的 Phase A，然後必須 STOP 等待逐項人工核准；「完成專案」不構成 deploy、付費呼叫或 formal run 授權。

TASK_ID: K7
DEPENDS_ON: K6 PASS，且重新執行 K0 preflight 沒有未接受的 P0 blocker
OBJECTIVE: 將 exact approved release-candidate SHA 部署到指定 AWS 環境，以最少且可控的 live/test 呼叫驗證 Bedrock、collectors、comparison、S3、Dynamo lock、download 與 observability；正式題目只在獨立核准後執行一次。
TARGET_COMMIT: chore(K7): record verified AWS release evidence

GLOBAL STOP RULE:
在任何 AWS deploy/delete/config change、真實 POST、Bedrock/付費呼叫、formal run、credential create/rotate/revoke、git push/merge/tag/PR 前，列出 exact account alias（不得顯示帳號 ID）、region、stack、candidate SHA、預計呼叫數/模式、成本與正式額度影響、回滾與驗證命令，等待使用者明確核准該一項。沒有核准就停止。

Gate A 必須逐項列出並取得批准：exact CloudFormation changeset/parameters、AuthType、CORS、concurrency、log retention、成本上限，以及 exact rollback action。任何未列入核准的 rollback、redeploy、delete、resource replacement 或 config change 都要重新 STOP，不能把「部署核准」擴張成任意補救授權。

禁止要求使用者把 access key/secret/session token/完整 Function URL 貼進聊天、文件或 commit。只使用本機既有 credential chain；輸出一律遮蔽。

PHASE A — LOCAL PREFLIGHT ONLY:
1. 讀 AGENTS.md、steering、#spec:hoyabit-remediation；若 context provider 不可用，直接讀 .kiro/specs/hoyabit-remediation/{requirements.md,design.md,tasks.md}。再讀 docs/RELEASE_CANDIDATE_REPORT.md、.kiro/specs/hoyabit-aws-deployment/{requirements.md,design.md,tasks.md,status.yaml,aws-permissions-guide.md}、aws/template.yaml、aws/deploy.sh、aws/verify-deployment.sh、docs/COMPETITION_TASK_STATUS.yaml。
2. 逐項執行：`git status --short --branch`、`git rev-parse HEAD`、`python3 -m unittest discover -s tests`、`bash scripts/check_secrets.sh >/dev/null 2>&1`、`bash scripts/check_secrets.sh --history >/dev/null 2>&1`、`bash aws/deploy.sh --dry-run`。秘密掃描只記 exit code。正式 deployment package 必須由 approved SHA 的乾淨隔離 checkout/worktree 產生，以 `shasum -a 256 aws/.build/agent.zip` 記錄 ZIP SHA-256；不得從帶未提交變更的目錄部署。
3. 確認 candidate SHA 等於 K6 SHA、worktree clean、rollback SHA/stack plan 已知、region/model/permissions 由人確認。
4. 檢查 public Function URL/AuthType/CORS/成本曝險與 Git history incident。若 durable store/lock 尚未完成、部署 SHA 不一致或安全策略未接受，NO-GO，不得觸發 formal。
5. 提出最小 live call plan：先 read-only infra verify；再 public test-mode smoke；最後才是可選的一次 official formal。Official formal 必須走 IAM/local trusted control plane，不得送到匿名 Function URL。列出每一個 payload 的題型與 mode，但不要代替使用者決定正式題目。
6. STOP，向使用者請求 Gate A（deploy）核准。

開始 Phase A 前先設定 `current_task: K7`、`K7.status: IN_PROGRESS`、實際 `started_at/last_updated_at`；這個 status 變更不授權任何外部操作。

PHASE B — 只有 Gate A 核准後:
- 部署 exact SHA；不得順便修改另一 region/stack。
- 驗證 deployed code_commit == approved SHA、template resources、IAM scope、S3 private/encrypted、Dynamo table/conditional path、log retention、Function URL policy。
- 部署失敗時，只能執行 Gate A 已逐項批准的 exact rollback；若實際狀況需要其他 rollback/redeploy/delete/config change，立即停止並重新請求核准。保留遮蔽證據，不得反覆付費嘗試。
- STOP，列出實際部署結果與最小 test-mode 呼叫，請求 Gate B 核准。

PHASE C — 只有 Gate B 核准後:
- 以 test mode 驗證五幣 collectors 能力、一般/假設/comparison 三題型與至少一條真 Bedrock planner→analyst→critic；精確呼叫數以核准內容為上限。
- 驗證 Citation Gate、quality metrics、實際 collector inventory/plan-selected source count/fallback 誠實紀錄、<900 秒（內部需保留 finalization buffer）；若 K4 已啟用，不得硬要求每題都呼叫全部 collector。
- 驗證 single/comparison bundle 由 S3 跨請求取回，ZIP/單檔 hash 相同；不得以同一 /tmp container 成功冒充持久化。
- 使用專用 synthetic QA key 驗證 Dynamo conditional concurrency/cold-start duplicate；這也是外部寫入，必須在 Gate B 列明。不得消耗官方題目的 formal 額度。
- 驗證匿名/public Function URL 對 formal、rerun、protected-config override 一律 403，且 CloudWatch 沒有 collector/LLM/run creation；trusted IAM control plane 才能進 formal path。
- CloudWatch 要能追 run_id、commit、stage、fallback、duration，但不得記題目原文、Evidence 全文、URL、憑證或秘密。
- 若任一必要項失敗，K7 PARTIAL/FAIL，停止；不刷新 live-success、不標 T8 PASS。
- 全部通過後 STOP，請求 Gate C：是否以使用者提供的 exact official question/coins 執行唯一一次 mode=formal。未核准就不執行。

PHASE D — 只有 Gate C 核准後:
- 透過 trusted IAM/local operator control plane 精確執行一次 formal，不得使用匿名 Function URL。成功需有新 run_id、approved code_commit、formal_subject_hash/execution_config_hash、正確 mode、Bedrock stages、Citation Gate、六項 artifact、manifest hashes、S3 download、runtime 與 lineage。
- 立即重送/冷啟動重送官方題目會影響外部系統；只有 Gate C 同時明確核准 duplicate verification 才可做，否則以 synthetic QA lock 證據驗收。
- 保存遮蔽後的 fresh evidence；舊 fixture 不得改寫成看似新版本。

STATUS/COMMIT:
- 建立/更新 docs/AWS_RELEASE_EVIDENCE.md，保存遮蔽後的 deployed SHA、ZIP hash、changeset 摘要、call matrix、S3/Dynamo/download/CloudWatch 驗收與 rollback 結果；不得保存完整 URL/account ID/credential。
- 更新 K7、T8.5/T8.6、AWS status、live fixture README 與 architecture docs，只寫實測事實。
- K7 PASS 僅在 Gate A/B 的必要雲端驗收全部通過，且正式提交確實要求 formal 時 Gate C 也已完成。任何未核准/未執行、部署 SHA 不符、download/cold-start/lock 未證實，或 fallback 不在事前核准 allowlist，均只能是 PARTIAL/FAIL。
- live-success 僅接受 exact deployed SHA、必要 stages 成功、無不允許的 fallback、hash 完整的真實結果。
- 建立 evidence commit 前，以 stdout/stderr→/dev/null 的 exit-code-only 方式重新執行 worktree 與 `--staged` secret scan；任何非 0 即停止，不得查看/貼出匹配值、commit 或 PASS。
- 建立單一 local evidence commit；禁止 push/tag/PR。
- 同一 commit 必須含 final status、test/verification summary、artifacts、blockers、known_limitations 與 target subject；實際 hash 只回報，不另建 backfill commit。

FINAL REPORT: TASK、STATUS、CONCLUSION、FILES_CHANGED、TEST_COMMANDS、TEST_RESULTS、COMMIT、KNOWN_LIMITATIONS、BLOCKERS、UNVERIFIED、NEXT_TASK_READY，再附 APPROVED SCOPE、DEPLOYED SHA（非帳號/URL）、REGION、CALL MATRIX、FORMAL EXECUTED yes/no、RUNTIME、COLLECTOR/FALLBACK、S3/DYNAMO/DOWNLOAD EVIDENCE、LOGGING REVIEW 與 ROLLBACK。所有敏感值遮蔽。
```

Gate 核准建議回覆格式（不要一次全批准）：

```text
批准 K7 Gate A：只允許將 SHA <COMMIT> 以已列出的 exact changeset/parameters/AuthType/CORS/concurrency/log-retention 部署到 <REGION>/<STACK>，成本上限為 <LIMIT>；只批准已列出的 exact rollback action。不批准其他 rollback/redeploy/delete/config change、live POST、formal run、push、tag 或資源刪除。
```

```text
批准 K7 Gate B：只允許你剛列出的 test-mode 呼叫與 synthetic lock 驗證，總數不得超過 <N>；不批准 official formal run。
```

```text
批准 K7 Gate C：只允許以我提供的 exact question/coins 執行一次 mode=formal；不批准第二次 formal、push、tag 或拆除。
```

---

## K8 — 文件、Kiro 證據、簡報素材與公開提交同步

模型：**Claude Sonnet 4.6**
推理強度：**High**

```text
只執行 Task K8，完成本機文件後停止；任何 push/PR/merge/default branch/tag 仍需獨立人工核准。

TASK_ID: K8
DEPENDS_ON: K6 PASS；K7 若未 PASS，文件必須誠實保留 PARTIAL/PENDING，不得捏造 live 成功
OBJECTIVE: 讓 repo、Kiro artifacts、提案承諾、Demo 證據、簡報素材與公開交付狀態完全一致，移除過時數字與未證明宣稱。
TARGET_COMMIT: docs(K8): align competition submission evidence

你是唯一寫入者。先讀 AGENTS.md、全部 steering/spec、docs/COMPETITION_TASK_STATUS.yaml、docs/COMPETITION_CHECKLIST.md、README.md、docs/project-report.md、docs/aws-architecture.md、demo-fixtures 下的 README/manifests、docs/RELEASE_CANDIDATE_REPORT.md（若存在）、docs/AWS_RELEASE_EVIDENCE.md（若存在）、git log/status/branches/upstream/tags。K7 未執行或 AWS evidence 檔不存在時記 `PENDING-K7`，不得因此中止 K8，也不得猜內容。禁止 `git remote -v`；remote 只讀名稱、symbolic refs 與 rev-list divergence，不輸出可能含 token 的 URL。唯讀比對：
- /Users/zhangshitong/Desktop/HoyaBIT_決賽提案報告_重新建立.md
- /Users/zhangshitong/Desktop/(HOYA BIT) 命題文件 - 2026 雲湧智生：臺灣生成式 AI 應用黑客松競賽 更新.docx
DOCX 必要時用 `textutil -convert txt -stdout "<完整路徑>"` 讀取；不得修改或另存 repo 外檔案。路徑不可存取時標 PENDING-HUMAN，不可猜內容。

PREFLIGHT:
- 記錄 exact HEAD、K7 deployed SHA（若有）、live fixture SHA、public default branch/remote divergence。Remote URL 輸出要遮蔽 token。
- 執行 full suite、exit-code-only worktree/history secret scan（stdout/stderr→/dev/null）、fixture manifest hash 驗證；不執行 live/AWS。
- 若文件想宣稱的事實沒有對應 commit/test/live evidence，必須刪除或降級，而不是補寫想像證據。
- 若 Git history 命中仍有效的 Function URL 或任何 credential，且 rotation/delete containment 未 VERIFIED，K8 必須 BLOCKED/PUBLICATION_NO_GO；禁止 push、PR、merge、default-branch、tag 或 release publish。先處理外部秘密/端點，history rewrite 另案核准。
- Dependency/preflight 通過後，先設定 `current_task: K8`、`K8.status: IN_PROGRESS`、實際 `started_at/last_updated_at`，再寫文件。

IN_SCOPE:
1. 同步 .kiro/specs/*/tasks.md、requirements/design 的實際狀態；修正已實作卻未勾、或不存在 spec 路徑。保留歷史，不能把 PARTIAL 改寫成當時已 PASS。
2. 同步 docs/COMPETITION_TASK_STATUS.yaml、docs/COMPETITION_CHECKLIST.md、docs/COMPETITION_BLOCKERS.md、README.md、docs/project-report.md、docs/aws-architecture.md、docs/COMPETITION_RUNBOOK.md、docs/DEMO_RUNBOOK.md、docs/recording-runbook.md 與 fixture README；所有測試數、collector 數、artifact 數、commit、runtime、fallback 以最新證據為準。
3. 逐條建立 proposal claim reconciliation：IMPLEMENTED+VERIFIED / IMPLEMENTED+LOCAL-ONLY / PARTIAL / NOT IMPLEMENTED。S3、raw artifacts、distributed lock、counter-evidence、AWS services、comparison、Live URL 不得過度承諾。
4. 在 docs/submission/ 建立：
   - SLIDE_OUTLINE.md：問題、價值、架構、最新驗證的 source/agent 數（稽核基準曾為 13/6）、資料品質、claim-evidence、Kiro workflow、Demo、限制、下一步。
   - RECORDING_SCRIPT.md：3–5 分鐘可重現腳本、畫面、旁白、fallback 計畫、不可展示的秘密。
   - SUBMISSION_CHECKLIST.md：PPT/PDF、Demo URL、錄影 URL、GitHub、六項 bundle、授權/歸因、聯絡/截止欄位。
   - PROPOSAL_CORRECTIONS.md：對 repo 外提案檔的精確待改段落/文案，不直接改 Desktop 檔。
5. Kiro 加分證據必須展示 Spec → Task → commit → tests；不能只宣稱「使用 Kiro」。
6. Live URL、錄影 URL、PPT/PDF 路徑只在實際存在且可驗證時填入；否則保留 BLOCKED/PENDING-HUMAN。沒有工具/模板時只產生簡報內容，不可聲稱 PPTX 已交付。
7. 建立 public-release plan：source branch、target branch、exact SHA、diff、CI、PR、default branch、tag、rollback。Git history 風險若未 containment，不得建議直接 force-push。

OUT_OF_SCOPE/STOP:
- 不修改產品程式、不重跑付費/live/formal、不操作 AWS。
- 在 git push、建立/合併 PR、改 default branch、建立/移動 tag、release publish、history rewrite 前，列 exact target/SHA/diff/CI/風險並 STOP 等使用者核准。
- 不把 credentials、完整 Function URL、account ID 或未遮蔽 CloudWatch 輸出放入文件/影片。
- K8 只能修改 fixture README/index/metadata 指標，不得變更既有 report/evidence/execution log/research plan/claims/manifest 的 bytes。只有 K7 fresh verified run 可以建立新的 live fixture。

VERIFY:
- python3 -m unittest discover -s tests
- python3 scripts/verify_fixture_manifests.py demo-fixtures
- `bash scripts/check_secrets.sh >/dev/null 2>&1`（只記 exit code）
- `bash scripts/check_secrets.sh --history >/dev/null 2>&1`（只記 exit code）
- `rg -n '<待填>|TODO|PARTIAL|f94acfb|117 tests|11 (adapters|collectors)|evidence-credibility' README.md docs .kiro demo-fixtures`；每個命中需解釋或修正，不能盲目全刪。
- git diff --check
- 手動 trace 至少 10 個重要宣稱：claim → source file/status → commit/test/live evidence。

STATUS/COMMIT:
- Stage 只包含 K8 文件/fixture metadata；建立單一 local commit，禁止 push/tag。
- K7 非 PASS，或任一必交 Demo URL/PPT/PDF/錄影缺失時，K8 必須 PARTIAL 且 PUBLICATION_NO_GO，不得為了完成感改 PASS。
- 最終 release hard gate：K6 PASS + K7 PASS + K8 PASS + zero unaccepted P0 + deployed SHA=approved/tag SHA + CI PASS + staged secret clean + submission artifacts verified。任一不成立都不得建 release tag。
- 同一 commit 必須含 final status、test_summary、artifacts、blockers、known_limitations 與 target subject；實際 hash 只回報，不另建 backfill commit。

FINAL REPORT: TASK、STATUS、CONCLUSION、FILES_CHANGED（file:line）、TEST_COMMANDS、TEST_RESULTS、COMMIT、KNOWN_LIMITATIONS、BLOCKERS、UNVERIFIED、NEXT_TASK_READY，再附 CLAIM RECONCILIATION SUMMARY、SUBMISSION ARTIFACT MATRIX、KIRO TRACEABILITY、PENDING-HUMAN 與 PROPOSED PUBLICATION ACTION。停止等待發布核准。
```

發布核准建議回覆格式：

```text
批准 K8 發布 Gate P1：只把 exact SHA <COMMIT> push 到 <REMOTE>/<SOURCE_BRANCH>；不批准 force-push、PR、merge、default branch 修改、tag 或 release。
```

```text
批准 K8 發布 Gate P2：只建立從 <SOURCE_BRANCH>/<SHA> 指向 <TARGET_BRANCH> 的 PR；不批准 merge、default branch 修改、tag 或 release。
```

```text
批准 K8 發布 Gate P3：只合併已驗證 PR <ID>。若 diff、CI 或 head SHA 與報告不同，立即停止；不批准 default branch 修改、tag 或 release。
```

```text
批准 K8 發布 Gate P4：在 post-merge CI 通過且 merge SHA=<SHA> 時，只把 default branch 設為 <BRANCH>（若確有需要）；不批准 tag/release。若不需更改 default branch，回報 N/A 並停止。
```

```text
批准 K8 發布 Gate P5：只在已驗證的 merge SHA <SHA> 建立 tag competition-demo-ready；不批准移動既有 tag、force-push 或未列出的 release publish。
```

---

## K9 — 決賽後 AWS 拆除與成本關閉

模型：**Claude Opus 4.8**
推理強度：**Max**

```text
只執行 Task K9。這是賽後 destructive task；除非使用者明確說明展示與必要證據備份已完成，否則立即停止。

TASK_ID: K9
DEPENDS_ON: K8 PASS；若 K8 PARTIAL，必須有使用者明確接受的 dependency exception，且仍須確認展示/必要證據已完成備份
OBJECTIVE: 在保留必要離線證據後，以可核對順序拆除競賽 AWS 資源、撤銷臨時憑證並確認無持續計費。
TARGET_COMMIT: chore(K9): record verified AWS teardown

先讀 AGENTS.md、本提示詞的 destructive rules、.kiro/specs/hoyabit-aws-deployment/status.yaml 的 D9、.kiro/specs/hoyabit-aws-deployment/aws-permissions-guide.md §9、docs/AWS_RELEASE_EVIDENCE.md、docs/submission/SUBMISSION_CHECKLIST.md、aws/template.yaml 與部署腳本。docs/AWS_RELEASE_EVIDENCE.md 與 docs/submission/SUBMISSION_CHECKLIST.md 必須實際存在，且備份驗證通過；任一缺少就立即 BLOCKED，禁止任何 AWS inventory 或外部操作。不得把 account ID、完整 URL、access key/secret/session token 寫進聊天或文件。

使用者明確確認可開始 K9 後，先設定 `current_task: K9`、`K9.status: IN_PROGRESS`、實際 `started_at/last_updated_at`；這不等於核准任何刪除，Phase A 後仍必須再次取得 exact-resource 核准。

PHASE A — READ-ONLY INVENTORY:
- 列出 account alias、region、stack、Lambda、Function URL、S3 buckets、DynamoDB table、log group、IAM temporary user/role/key、budget/cost resources；識別值在輸出遮蔽，只保留足以讓使用者辨認的 alias/尾碼。
- 確認需要保留的六項正式 bundle、manifest/hash、遮蔽後 live evidence、PPT/PDF/錄影已備份到非待刪位置。
- 列出 deletion order、不可逆影響、rollback/不可 rollback 項目與每一步驗證命令。
- STOP，等待使用者針對 exact resources 的最終核准。沒有核准不得刪除任何東西。
- 每個待刪資源都必須以 tags、stack ownership 或建立紀錄證明由本專案建立。Ambient SSO、Workshop roles、WSParticipantRole、共享 user/key/policy 永久禁止碰觸；若本專案未建立專用 credential，credential cleanup 記 N/A。

PHASE B — ONLY AFTER EXACT APPROVAL:
1. 先依核准內容停用/限制公開入口，防止拆除過程被呼叫。
2. 依 stack ownership 正確刪除 CloudFormation stack；不要手動先刪會造成 stack 卡住的受管資源。
3. 依 retention/backup 決策清空並刪除非 stack-managed artifact/deployment buckets；不得碰未列入核准的 bucket/prefix。
4. 確認 Lambda、Function URL、DynamoDB table、log group 與相關 stack resources 消失。
5. 最後撤銷/刪除臨時 access key/user/policy；順序必須確保前面仍有權限完成拆除。
6. 檢查 Cost Explorer/Billing 與資源清單，記錄可能延遲顯示的限制。
7. 不刪本機 repo、demo fixtures、正式 bundle、提案或錄影。禁止 rm -rf；任何本機 build cache 清理也需明確、狹窄且可驗證的 target。
8. 所有 delete target 必須是 Phase A 列出的 exact IDs；禁止 wildcard、glob、空變數或推導出的廣泛 prefix。Versioned objects、delete markers、object lock/legal hold 必須先盤點並另行批准。

FAILURE RULE:
- 任一步失敗立即停止，不做猜測性跨帳號/跨區域刪除；回報失敗資源、錯誤類型、剩餘成本風險與安全重試方案。
- Git history cleanup/force-push 不是本 Task 的一部分，也不能替代 URL/credential rotation。

STATUS:
- 必須等待 stack `DELETE_COMPLETE` 並重新列舉所有核准資源。只有 stack、URL、Lambda、DynamoDB、指定 buckets/logs/project-owned credentials 都依核准消失，D9/K9 的 resource teardown 才可 PASS。
- Billing/Cost Explorer 有延遲時使用 allowed status `PARTIAL`，另記 `monitoring: true` 與明確 `follow_up_at`；由 Human 在延遲窗口後再次確認，不得以一次即時查詢宣稱永久零成本。
- 更新本機 status 文件只記遮蔽後證據；stage 只包含 K9 status/evidence，建立恰好一個 local commit，禁止未核准 push。
- 同一 commit 必須含 final status、verification summary、artifacts、blockers、known_limitations 與 target subject；實際 hash 只回報，不另建 backfill commit。

FINAL REPORT: TASK、STATUS、CONCLUSION、FILES_CHANGED、TEST_COMMANDS、TEST_RESULTS、COMMIT、KNOWN_LIMITATIONS、BLOCKERS、UNVERIFIED、NEXT_TASK_READY，再附 APPROVAL SCOPE、BACKUP VERIFIED、DELETED RESOURCE TYPES（遮蔽）、REMAINING RESOURCES/COST RISK、CREDENTIAL REVOCATION 與 MANUAL FOLLOW-UP。不要輸出秘密或完整 URL。
```

最終拆除核准建議回覆格式：

```text
我確認決賽展示與必要證據已備份。批准 K9 只刪除你在 Phase A 列出的 <ACCOUNT ALIAS>/<REGION>/<STACK AND RESOURCE ALIASES>；不得碰其他 region、stack、bucket、repo 或本機交付物。任一步目標不一致時立即停止。
```

## 使用提醒

1. 每個 Task 用新對話，先設定表格中的模型與 effort。
2. K0 完成後，在後續對話加入 `#spec:hoyabit-remediation`；若 Kiro 無法解析 context provider，就直接要求讀取該 spec 三檔。
3. Kiro 回報 PASS 後，先看實際測試數、diff 與 commit，再貼下一個 Task。
4. `PARTIAL` 不是失敗，但不能當成下一個 hard dependency 的 PASS；必須由你明確接受例外。
5. K7/K8/K9 的 STOP 不代表卡住，而是刻意保留 deploy、付費 formal、發布與刪除的人工決策權。

Kiro 模型與 effort 參考：

- https://kiro.dev/docs/models/
- https://kiro.dev/docs/cli/chat/effort/
- https://kiro.dev/docs/specs/best-practices/
