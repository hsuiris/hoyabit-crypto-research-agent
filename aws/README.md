# AWS 部署資產

這個目錄放部署到 AWS 所需的全部檔案。完整規劃、任務清單與權限開通引導在
`.kiro/specs/hoyabit-aws-deployment/`。

## 檔案

| 檔案 | 用途 | 需要 AWS 憑證 |
|---|---|---|
| `template.yaml` | CloudFormation：IAM 執行角色 + Lambda + Function URL | 部署時需要 |
| `verify-permissions.sh` | 部署前的權限預檢；只做唯讀呼叫，不建立任何資源 | 需要 |
| `deploy.sh` | POSIX 部署腳本（macOS／Linux） | 除 `--dry-run` 外需要 |
| `deploy.ps1` | PowerShell 部署腳本（Windows），與 `deploy.sh` 行為對等 | 部署時需要 |
| `iam/deployer-policy.json` | 部署身分的最小權限參考 policy | 不需要 |

## 執行順序

```bash
# 1. 設定憑證（見下方「憑證來源」）
export AWS_PROFILE=hoyabit

# 2. 權限預檢（不建立資源）
AWS_REGION=us-west-2 bash aws/verify-permissions.sh

# 3. 先確認打包正確（不呼叫任何 AWS API，無憑證也能跑）
bash aws/deploy.sh --dry-run

# 4. 部署
bash aws/deploy.sh --region us-west-2 --provider bedrock --model-id amazon.nova-lite-v1:0

# 5. Demo 結束後拆除
aws cloudformation delete-stack --region us-west-2 --stack-name hoyabit-agent-mvp
```

`--dry-run` 是刻意設計的：它讓打包邏輯與 AWS 憑證解耦，因此可以在還沒拿到權限時
就先驗證部署套件的內容是否正確。

部署套件只含 `lambda_handler.py`、`src/`、`data/`。`tests/`、`docs/`、`demo-fixtures/`、
`outputs-*/`、`.kiro/` 與 `.env` 一律排除——前面幾項是體積，最後一項是憑證外洩風險。

## 憑證來源

### AWS Workshop Studio 環境（目前使用的方式）

憑證由活動頁面提供，不需要自己建立 IAM 身分。回到 workshop 活動頁面找
**Get AWS CLI credentials**，複製 macOS/Linux 那組三行 `export`。

Workshop 憑證是**臨時的**，包含 `AWS_SESSION_TOKEN`，會在活動結束或數小時後失效。
因此部署完成後應盡快錄製 Demo 或截圖存證，不要假設 Function URL 會長期存在。

寫成具名 profile 以免每個新終端都要重貼：

```bash
aws configure set aws_access_key_id     "$AWS_ACCESS_KEY_ID"     --profile hoyabit
aws configure set aws_secret_access_key "$AWS_SECRET_ACCESS_KEY" --profile hoyabit
aws configure set aws_session_token     "$AWS_SESSION_TOKEN"     --profile hoyabit
aws configure set region                us-west-2                --profile hoyabit
```

### 自己的 AWS 帳號

需要自建部署身分並套用 `iam/deployer-policy.json`。逐步操作見
`.kiro/specs/hoyabit-aws-deployment/aws-permissions-guide.md`。

`deployer-policy.json` 裡的 `<ACCOUNT_ID>`、`<REGION>`、`<STACK_NAME>` 是佔位符，
**刻意保留不填**，避免把特定帳號資訊 commit 進 repo。套用前先替換。

該 policy 刻意不含 `AdministratorAccess`。各 Statement 的用途與限制範圍說明在
上述 guide 的 §3.2。

## LLM provider 設定

部署時透過 CloudFormation 參數選擇 provider：

| 參數 | 預設 | 說明 |
|---|---|---|
| `LLMProvider` | `gemini` | 部署 Bedrock 版本時要顯式傳 `bedrock` |
| `BedrockModelId` | `amazon.nova-lite-v1:0` | 見下方 region 注意事項 |
| `LLMSecretArn` | 空 | 僅 Gemini／OpenAI 需要；Bedrock 走執行角色，不讀 Secrets Manager |

### region 與 model ID 必須成對驗證

模型可用性依 region 而異，**不要只改 region 就部署**。

已實測（2026-08-01，部署 region `us-west-2`）：`amazon.nova-lite-v1:0` 支援 `ON_DEMAND`，
可用裸 foundation model ID 直接呼叫 Converse，`template.yaml` 現有的 IAM 資源條件
`foundation-model/${BedrockModelId}` 即足夠。

同一次實測也證明 region 差異是真的會咬人的：`amazon.nova-micro-v1:0` 的裸 ID
在 `us-east-1` 可呼叫，在 `us-west-2` 卻回報 `on-demand throughput isn't supported`，
必須改用 `us.amazon.nova-micro-v1:0`。所以下表的「成對驗證」不是形式要求。

若改用亞太區，Nova 系列可能只透過 cross-Region inference profile 提供
（model ID 會帶 `apac.` 前綴），此時 IAM 必須同時授權 inference profile ARN 與目的 region
的 foundation model ARN，否則 Lambda 會拿到 `AccessDeniedException`。

**這種失敗特別隱蔽**：Bedrock 呼叫失敗會被 orchestrator 攔下並降級成 deterministic
offline fallback，執行仍然成功、報告仍然產出，只是模型路徑沒跑。切換 region 前
務必用 `verify-permissions.sh` 的第 3 項重新實測。

## 公開端點的安全性

`template.yaml` 的 Function URL 使用 `AuthType: NONE`，`Principal: '*'`。

**任何取得該 URL 的人都能觸發完整研究執行並消耗 Bedrock 配額。** 這是 Demo 情境下的
刻意取捨（評審不需要憑證），但代表：

- URL 不要公開張貼。
- Demo 結束就刪 stack（`aws cloudformation delete-stack`）。
- 收斂手段見 `.kiro/specs/hoyabit-aws-deployment/tasks.md` 的 D7：
  reserved concurrency 上限、CloudWatch log 保留期、AWS Budgets 告警，
  以及可選的 `AuthType: AWS_IAM`。

## us-west-2 的固有限制：Binance 來源會降級

**這不是 bug，改程式碼修不掉。** Binance 封鎖美國 IP，而 `us-west-2` 是美國 region，
因此三個 collector 在雲端一律取不到資料，改用可靠度 0.20 的 fallback fixture：

| Collector | 端點 |
|---|---|
| `derivatives` | `fapi.binance.com/fapi/v1/premiumIndex` |
| `vegas_channel` | `api.binance.com/api/v3/klines` |
| `long_short_ratio` | `fapi.binance.com/futures/data/topLongShortPositionRatio` |

已實測對比（2026-08-01）：本機（台灣 IP）對上述端點回 `HTTP 200`，
雲端 `us-west-2` 回 `HTTPError`。

實際影響：

- 11 個 collector 變成 **8 個 success + 3 個 fallback**，仍有 8 筆實質證據。
- `run_status` 因此一律是 `COMPLETED_DEGRADED`，`degradation_reasons` 會列出這三項。
  **這是誠實標示，不是失敗**——系統的單一來源失敗隔離機制正常運作。
- Citation Gate 仍 `PASS`，因為 fallback 證據不會成為任何主要 Claim 的唯一支持。

可選的處置方式：

| 做法 | 代價 |
|---|---|
| 接受降級（目前採用） | 少 3 筆衍生品／技術面證據，須在 Demo 中說明 |
| 換到非美國 region | 需重跑 `verify-permissions.sh` 確認 Bedrock 模型可用形式；競賽環境指定 us-west-2 |
| 本機執行 Demo | 11/11 collector 都成功，但失去「部署在 AWS」的展示點 |

現場 Demo 建議直接說明這一點：它剛好展示了 per-source fallback 的設計價值。

## 環境約束

目前使用的 Workshop Studio 帳號中已存在這些資源，**不要動它們**：

| 資源 | 說明 |
|---|---|
| `WSParticipantRole` | 目前使用的參與者角色 |
| `WSLambdaCurtailerRole` | Workshop 的 Lambda 併發抑制機制 |
| `WSConcurrencyCurtailer-DO-NOT-USE`（Lambda） | 名稱明示不可使用 |

最後一項暗示環境會監控並限制 Lambda 併發，因此設定 reserved concurrency 時
不要假設可以自由取用帳號的全部併發額度。

## 待補

| 項目 | 對應任務 | 狀態 |
|---|---|---|
| `deploy.sh`（POSIX 部署腳本，本機無 pwsh 因此必要） | D3 | 已完成 |
| Lambda 執行環境的 boto3／botocore 版本控制 | D4 | 進行中 |
| S3 產物持久化（`S3ArtifactStore`） | D6 | 未開始 |
| reserved concurrency、log 保留期、Budgets 告警 | D7 | 未開始 |

### D4 為什麼重要

Lambda 受管 runtime 內建的 boto3 版本常落後於 runtime 發布時點。若內建版本不支援
`bedrock-runtime` 的 `Converse`，`src/llm.py` 會拋出例外、被 orchestrator 攔下、
降級成 deterministic offline fallback——**執行仍然成功、六項提交物仍然產出，只是模型
路徑沒跑**。這正是 `docs/COMPETITION_BLOCKERS.md` 的 B2 在雲端的翻版，而且更難發現。

因此 `deploy.sh` 提供 `--bundle-sdk`，會把 pin 版 boto3／botocore 打包進部署包。
它預設關閉：先用內建版本部署並檢查 log，確認不支援才開啟，避免不必要的套件體積。

## 修改程式後要重新部署

Lambda 執行的是**部署時打包的程式碼快照**，不會自動跟著本機的檔案變動。

### 需要重新部署

改動這些路徑後，雲端行為不會變，必須重跑 `deploy.sh`：

| 路徑 | 說明 |
|---|---|
| `src/**` | 所有研究邏輯：orchestrator、llm、collector、credibility、claim_graph… |
| `lambda_handler.py` | Function URL 進入點 |
| `data/*.csv` | OHLCV 基準資料 |
| `config/source_registry.json` | credibility 計分基準 |
| `aws/template.yaml` | 基礎設施（timeout、記憶體、IAM、護欄） |

### 不需要重新部署

| 路徑 | 原因 |
|---|---|
| `tests/**` | 不進部署包 |
| `docs/**`、`.kiro/**` | 不進部署包 |
| `demo-fixtures/**` | 本機展示用 |
| `src/app.py` 的純 UI 調整 | 雲端走 `lambda_handler.py`，不使用 `app.py` 的頁面 |

`src/app.py` 要注意：它雖然不被雲端使用，但**仍在部署包內**（整個 `src/` 都會複製）。
如果你改的是 `app.py` 裡被其他模組共用的函式，那還是要重新部署。

### 重新部署的指令

```bash
export AWS_PROFILE=hoyabit

# 1. 先確認本機測試沒壞
python3 -m unittest discover -s tests

# 2. 確認打包內容正確（不呼叫 AWS）
bash aws/deploy.sh --dry-run

# 3. 部署
bash aws/deploy.sh --region us-west-2 --provider bedrock --model-id amazon.nova-lite-v1:0
```

CloudFormation 只會更新有變動的資源，通常 30–60 秒完成。Function URL **不會改變**。

### 部署後要驗證什麼

「命令沒報錯」不等於部署成功。至少確認：

```bash
URL=$(aws cloudformation describe-stacks --region us-west-2 \
  --stack-name hoyabit-agent-mvp \
  --query "Stacks[0].Outputs[?OutputKey=='PublicUrl'].OutputValue" --output text)

# 首頁會顯示執行環境的 SDK 能力
curl -sS "$URL" | tail -3

# 跑一次並檢查模型路徑與計分基準是否正常
curl -sS -X POST "$URL" -H 'content-type: application/json' \
  -d '{"coin":"ETH","question":"部署後驗證","mode":"test"}' -o /tmp/check.json
python3 scripts/verify_live_smoke.py /tmp/check.json
```

`scripts/verify_live_smoke.py` 會判定模型路徑、六項提交物、manifest hash 與 Citation Gate。

**特別留意這兩個容易靜默降級的欄位**，它們不會讓執行失敗，報告也照樣產出：

| 欄位 | 正常值 | 異常代表 |
|---|---|---|
| `stage_providers.analyst.status` | `success` | `fallback:*` 表示模型沒跑，走了離線推理 |
| `score_evidence.registry_version` | `source-registry-v1` | `unavailable` 表示 `config/` 沒進部署包 |

這兩個問題都真的發生過（見 `.kiro/specs/hoyabit-aws-deployment/status.yaml` 的 D5 與
`demo-fixtures/competition-ready/live-success/README.md`），所以值得每次部署後看一眼。

### Lambda 版本與回滾

每次部署會上傳一個帶時間戳的新 zip（`releases/agent-<UTC>.zip`），舊的仍保留在 S3。
要回滾就用舊的 key 重新指定：

```bash
aws s3 ls s3://hoyabit-agent-deploy-<ACCOUNT_ID>-us-west-2/releases/
aws cloudformation deploy --template-file aws/template.yaml \
  --stack-name hoyabit-agent-mvp --region us-west-2 \
  --capabilities CAPABILITY_IAM --no-fail-on-empty-changeset \
  --parameter-overrides CodeBucket=hoyabit-agent-deploy-<ACCOUNT_ID>-us-west-2 \
    CodeKey=releases/agent-<舊的時間戳>.zip LLMProvider=bedrock \
    BedrockModelId=amazon.nova-lite-v1:0
```
