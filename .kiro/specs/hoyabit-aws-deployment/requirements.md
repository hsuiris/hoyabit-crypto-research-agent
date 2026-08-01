# Requirements — HoyaBIT AWS 部署上線

## Introduction

本 spec 處理**單一關注點**：把已完成的 HoyaBIT 研究 Agent（`hackathon/competition-ready`，
HEAD `5f369ce`，T0–T7 PASS、T8 PARTIAL）實際部署到 AWS 並完成可展示驗收。

這不是 `hoyabit-competition-ready` spec 的延伸，而是它的**下游交付動作**。原因有三：

1. T8 已宣告 `feature_freeze: true`，在該 spec 內插入 T9+ 會讓 freeze 語意失效。
2. 部署工作的 owner 有一部分**不是 Kiro**：AWS Console 的身分建立、Bedrock 模型存取與帳單設定
   必須由持有帳號的人執行，Kiro 只能提供引導與驗證命令。
3. 部署失敗的降級路徑與產品功能的降級路徑不同：產品降級是 offline fallback，
   部署降級是「改用本機 Demo 展示」，兩者不應共用同一份 blocker 判準。

因此任務獨立編號為 **D0–D9**（Deploy），狀態記錄在本目錄的 `status.yaml`。
唯一與舊 spec 的交會點是 **D8**：live Bedrock smoke 成功後，回寫
`docs/COMPETITION_BLOCKERS.md` 的 B2 與 `docs/COMPETITION_TASK_STATUS.yaml` 的 T8。

## 責任分工（重要）

| 標記 | 意義 | 舉例 |
|---|---|---|
| `HUMAN` | 只能由帳號持有者在 AWS Console 或本機終端執行，Kiro 無法代勞 | 建立 IAM 身分、同意模型 EULA、設定預算告警 |
| `KIRO` | Kiro 可直接完成，產出程式碼或文件 | 撰寫 `aws/deploy.sh`、擴充 IAM policy、更新架構文件 |
| `BOTH` | Kiro 準備好命令與檔案，由人執行並回報輸出 | 首次 `deploy.sh`、live smoke |

`HUMAN` 步驟的完整操作引導見 `aws-permissions-guide.md`。

## 必須保留的既有邊界（違反即視為 FAIL）

部署不得為了「上雲順利」而降低既有能力：

- **零第三方相依的本機路徑**：`python -m src.app`、`python -m unittest discover -s tests`
  在只有標準函式庫的環境仍須成功。`boto3` 只能是 `src/llm.py` 現有的 optional import，
  以及部署包內的相依，不得成為 import 時的硬需求。
- **deterministic offline fallback**：Bedrock 不可用時仍須產出六項提交物。
- **六項提交物**：`report.md`、`evidence.json`、`execution_log.json`、`research_plan.json`、
  `claims.json`、`manifest.json`，且 manifest 的 SHA-256 須可驗證。
- **Evidence 可追溯性與 Citation Gate**：雲端執行的 gate 判準與本機一致。
- **既有測試套件**：541 tests 全數通過，不得因部署改動而 regression。
- **不得提交任何憑證**：AWS access key、session token、Bedrock 相關密鑰、競賽 Access Code
  一律不進 git，`.env` 不提交。

## Requirements

### Requirement 1 — 可用的部署身分與最小權限

**User Story:** 作為專案持有者，我要有一個能部署此 stack 但權限不過大的 AWS 身分，
這樣我不必用 root 或 AdministratorAccess 就能完成部署。

#### Acceptance Criteria

1. WHEN 在本機執行 `aws sts get-caller-identity` THEN 系統 SHALL 回傳 Account ID 與身分 ARN，
   且不再出現 `NoCredentials`。
2. WHEN 檢視部署身分的權限 THEN 該身分 SHALL NOT 附加 `AdministratorAccess`，
   而 SHALL 使用本 spec 定義的 deployer policy。
3. WHEN 部署身分嘗試 `cloudformation`、`s3`、`iam`、`lambda`、`logs`、`bedrock` 六類必要操作
   THEN 全部 SHALL 成功；WHEN 嘗試本 stack 範圍外的資源 THEN SHALL 被拒絕。
4. IF 帳號屬於 AWS Organization 且有 SCP 限制 THEN 引導文件 SHALL 提供
   `AccessDeniedException` 的判別方式與升級路徑（找 Organization 管理者）。
5. WHEN 選擇憑證形式 THEN 引導文件 SHALL 以 IAM Identity Center（短期憑證）為首選，
   IAM user access key 為明確標示風險的次選，並 SHALL NOT 建議使用 root access key。

### Requirement 2 — Bedrock 模型在目標 region 實際可呼叫

**User Story:** 作為部署者，我要在寫任何部署腳本之前就確認模型真的能呼叫，
這樣我不會在 stack 部署完成後才發現 `AccessDeniedException`。

#### Acceptance Criteria

1. WHEN 選定 region THEN 該 region SHALL 經 `aws bedrock list-foundation-models` 實測確認
   目標模型存在，而 SHALL NOT 僅依文件推定。
2. WHEN 目標模型在該 region 只以 cross-Region inference profile 提供
   THEN 系統 SHALL 使用 inference profile ID（例如 `apac.` 或 `us.` 前綴）作為 `BEDROCK_MODEL_ID`,
   AND IAM policy SHALL 同時授權 inference profile ARN 與其目的 region 的 foundation model ARN。
3. WHEN 執行一次最小 `Converse` 呼叫 THEN SHALL 取得非空文字回應，
   AND 該呼叫 SHALL NOT 走完整研究管線（避免耗用配額與時間）。
4. IF 模型需要先同意 EULA 或提交使用案例表單 THEN 引導文件 SHALL 標明此步驟為 `HUMAN`。
5. WHEN 實測結果與 `.env.example`／`aws/template.yaml` 的預設值不一致
   THEN 預設值 SHALL 被更新為實測可用的組合。

### Requirement 3 — 部署腳本可在本機實際執行

**User Story:** 作為在 macOS 上工作的部署者，我要能直接執行部署腳本，
這樣我不必為了跑一個 PowerShell 腳本另外安裝 pwsh。

#### Acceptance Criteria

1. WHEN 在 macOS 或 Linux 的 bash/zsh 執行部署 THEN SHALL 存在可直接執行的 `aws/deploy.sh`，
   且行為與既有 `aws/deploy.ps1` 對等。
2. WHEN 選擇 `bedrock` 作為 provider THEN 腳本 SHALL 接受該值，
   AND SHALL 能傳遞 `BedrockModelId` 到 CloudFormation。
   （現況：`deploy.ps1` 的 `ValidateSet` 只有 `gemini`／`openai`，且無法覆寫 model ID。）
3. WHEN 以 `--dry-run` 執行 THEN 腳本 SHALL 產生部署 zip 並在不呼叫任何 AWS API 的情況下結束成功。
4. WHEN AWS 憑證不可用 THEN 腳本 SHALL 保留已產生的 zip 並輸出可行動的錯誤訊息，
   而 SHALL NOT 留下半完成的 stack。
5. WHEN 部署包被打包 THEN SHALL 包含 `lambda_handler.py`、`src/`、`data/`，
   AND SHALL NOT 包含 `.env`、`__pycache__`、`outputs-*/`、`.git/`。
6. WHEN 部署完成 THEN 腳本 SHALL 輸出 Function URL。

### Requirement 4 — Lambda 執行環境能實際完成 Bedrock Converse

**User Story:** 作為部署者，我要確保 Lambda 內的 SDK 版本足以呼叫 Converse，
這樣不會在雲端遇到 `Unknown service: 'bedrock-runtime'` 而靜默降級成 offline fallback。

#### Acceptance Criteria

1. WHEN Lambda 冷啟動 THEN 執行環境 SHALL 具備支援 `bedrock-runtime` `Converse` 的
   `boto3`／`botocore`，來源可為部署包內含或 Lambda Layer。
2. WHEN 部署包內含 AWS SDK THEN 本機測試路徑 SHALL 仍不需要 `boto3`，
   AND `tests/` SHALL 維持全程 mock、不呼叫真實 AWS。
3. WHEN Lambda 執行且模型呼叫成功 THEN `execution_log.json` 的分析與 Critic stage
   SHALL 為 `success`，而非 fallback。
4. IF SDK 版本不足 THEN 系統 SHALL 在 log 中留下明確可辨識的失敗原因，
   而 SHALL NOT 只回報「已降級」而無根因。
5. WHEN 評估 SDK 打包方式 THEN 決策 SHALL 記錄在 `design.md`，
   並說明它為何不違反專案的零第三方相依原則。

### Requirement 5 — 首次部署可驗收

**User Story:** 作為部署者，我要一次可重現的部署與明確的驗收清單，
這樣我知道「部署成功」的定義不是「命令沒報錯」。

#### Acceptance Criteria

1. WHEN stack 部署完成 THEN `aws cloudformation describe-stacks` SHALL 回報
   `CREATE_COMPLETE` 或 `UPDATE_COMPLETE`。
2. WHEN 對 Function URL 發出 GET THEN SHALL 回應 HTTP 200 且包含輸入表單。
3. WHEN 以 `mode=test` 發出 POST THEN SHALL 在 Lambda timeout 內回應，
   AND 回應 SHALL 包含六項提交物，AND `manifest.json` 的 SHA-256 SHALL 可驗證。
4. WHEN 同一題目以 `mode=formal` 連續送出兩次 THEN 第二次 SHALL 回應 409，
   證明 formal lock 在雲端仍生效。
5. WHEN 檢視 CloudWatch Logs THEN SHALL 能找到該次 `run_id` 的執行記錄，
   AND log SHALL NOT 包含任何憑證或 API key。
6. IF 回應 payload 超過 Lambda 的 6 MB buffered 上限 THEN 系統 SHALL 改回傳產物參照而非完整內容，
   AND 此行為 SHALL 記錄為已知限制。

### Requirement 6 — 產物在雲端不隨容器消失

**User Story:** 作為評審或事後檢查者，我要能在執行結束後仍取得六項提交物，
這樣證據可追溯性不會因為 Lambda 容器回收而失效。

#### Acceptance Criteria

1. WHEN Lambda 完成一次執行 THEN 六項提交物 SHALL 可在執行結束後被再次取得。
2. WHEN 實作雲端產物落點 THEN SHALL 透過既有 `ArtifactStore` 介面新增實作，
   AND SHALL NOT 修改 `src/orchestrator.py` 的產出邏輯。
   （現況：`src/artifact_store.py` 只有 `LocalArtifactStore`，Lambda 寫在 `/tmp`。）
3. WHEN 產物寫入雲端儲存 THEN `manifest.json` 的 URI SHALL 指向實際落點，
   AND SHA-256 SHALL 與內容相符。
4. WHEN 未部署雲端儲存 THEN 系統 SHALL 仍可運作並明確標示產物為容器本地暫存，
   使本需求成為可延後但不可誤稱已完成的項目。
5. WHEN 新增儲存資源 THEN 該資源 SHALL 不公開可讀，
   AND 存取 SHALL 限於 Lambda execution role 與部署身分。

### Requirement 7 — 公開端點的安全邊界被明確處理

**User Story:** 作為專案持有者，我要知道這個公開 URL 的風險並有收斂手段，
這樣 Demo 用的無認證端點不會變成長期暴露的攻擊面。

#### Acceptance Criteria

1. WHEN 部署使用 `AuthType: NONE` 的 Function URL THEN 該風險 SHALL 在文件中明確標示為
   「任何人取得 URL 即可觸發執行並消耗模型配額」。
2. WHEN 需要收斂暴露面 THEN 系統 SHALL 提供至少一種可選手段
   （`AuthType: AWS_IAM`、共享 secret 標頭，或 Demo 後立即刪除 stack）。
3. WHEN 部署 Lambda THEN SHALL 設定 reserved concurrency 上限，
   使失控呼叫的成本與配額消耗有天花板。
4. WHEN 設定 CloudWatch log group THEN SHALL 設定保留期限，而 SHALL NOT 使用永久保留。
5. WHEN Demo 結束 THEN SHALL 存在一份可執行的資源拆除步驟，
   AND 執行後 SHALL 無殘留計費資源。

### Requirement 8 — 成本可預期且有告警

**User Story:** 作為帳號持有者，我要在花費異常時收到通知，
這樣公開端點被大量呼叫時我能及時發現。

#### Acceptance Criteria

1. WHEN 部署前 THEN 引導文件 SHALL 提供本 stack 的成本構成說明
   （Lambda 執行時間、Bedrock token、S3 儲存、CloudWatch Logs）。
2. WHEN 設定護欄 THEN SHALL 建立 AWS Budgets 告警，此步驟標記為 `HUMAN`。
3. WHEN 估算成本 THEN 文件 SHALL 明確標示為估算而非承諾，
   AND SHALL 提供實際查詢用量的命令或 Console 路徑。

### Requirement 9 — Live Bedrock smoke 與 B2 解除

**User Story:** 作為競賽準備者，我要一次可信的 live 執行紀錄，
這樣我能誠實宣稱模型路徑成功，而不是把降級執行說成成功。

#### Acceptance Criteria

1. WHEN 執行 live smoke THEN SHALL 只執行一次，符合既有測試規則。
2. WHEN live smoke 完成 THEN analyst 與 critic 的 stage status SHALL 皆為 `success`,
   AND 六項提交物與 manifest hash SHALL 全部正確，AND Citation Gate SHALL PASS。
3. WHEN live smoke 成功 THEN SHALL 更新 `docs/COMPETITION_BLOCKERS.md` 的 B2 為已解除，
   AND 更新 `docs/COMPETITION_TASK_STATUS.yaml` 的 T8。
4. IF live smoke 仍降級 THEN SHALL NOT 宣稱 B2 解除，
   AND SHALL NOT 將降級輸出複製到 `demo-fixtures/competition-ready/live-success/`,
   AND SHALL 記錄實際失敗原因。
5. WHEN 現場展示 THEN SHALL 保留 `demo-fixtures/competition-ready/offline-backup/` 與
   `comparison-backup/` 作為備案，不因雲端可用而刪除。

### Requirement 10 — 架構文件與現況一致

**User Story:** 作為讀者，我要架構文件描述的是實際部署的東西，
這樣我不會照著過期的文件做出錯誤判斷。

#### Acceptance Criteria

1. WHEN 部署完成 THEN `docs/aws-architecture.md` SHALL 反映實際 provider、timeout 與資源。
   （現況已知落後：仍寫 OpenAI Responses API 與 300 秒 timeout。）
2. WHEN 文件描述已驗證與未驗證的路徑 THEN SHALL 明確區分，
   AND SHALL NOT 把「模板中有定義」寫成「已部署驗證」。
3. WHEN 更新文件 THEN SHALL 保留 T0 baseline 與 T1–T8 的歷史驗收結果，而 SHALL NOT 覆寫。

## Out of Scope

以下明確不在本 spec 範圍內，即使技術上相關：

- 把 `src/app.py` 的完整 Web UI（比較頁、回測頁、`/artifact` 檢視）搬上 Lambda。
  雲端只提供 `lambda_handler.py` 的精簡表單；完整 UI 仍以本機展示。
- 導入 API Gateway、CloudFront、WAF、Cognito、自訂網域或 TLS 憑證。
- 導入 Step Functions、DynamoDB、SQS、EventBridge、向量資料庫。
- 改用容器部署（ECS／App Runner／EC2）或 Lambda Web Adapter。
- 導入 CDK、SAM、Terraform 或 CI/CD pipeline 取代現有 CloudFormation + 腳本。
- 多環境（dev／staging／prod）、多帳號或多 region 部署。
- 修改任何研究邏輯：collector、planner、credibility、claim graph、citation gate、回測、Vegas 策略。
- 新增資料來源或調整既有來源的可靠度分數。

## 降級與中止條件

| 情況 | 動作 |
|---|---|
| 單一 blocker 投入超過 20 分鐘 | 記錄到 `docs/COMPETITION_BLOCKERS.md`，回報 `BLOCKED`／`PARTIAL`，不重構 |
| AWS 權限在合理時間內無法取得 | 停在 D2 之前，改以本機 Demo 展示，D 任務整體標記 `BLOCKED` |
| Bedrock 在任何可用 region 都無法呼叫 | 保留 offline fallback 展示，D8 記為 `FAIL` 並保留 B2 |
| 部署後行為與本機不一致 | 先確認是環境差異或 regression；若是 regression 必須修復，否則記為已知限制 |
| 距離展示不足 90 分鐘 | 不得開始未完成的 D 任務，改為確認備案 fixture 可用 |
