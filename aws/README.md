# AWS 部署資產

這個目錄放部署到 AWS 所需的全部檔案。完整規劃、任務清單與權限開通引導在
`.kiro/specs/hoyabit-aws-deployment/`。

## 檔案

| 檔案 | 用途 | 需要 AWS 憑證 |
|---|---|---|
| `template.yaml` | CloudFormation：IAM 執行角色 + Lambda + Function URL | 部署時需要 |
| `verify-permissions.sh` | 部署前的權限預檢；只做唯讀呼叫，不建立任何資源 | 需要 |
| `iam/deployer-policy.json` | 部署身分的最小權限參考 policy | 不需要 |
| `deploy.ps1` | PowerShell 部署腳本（Windows） | 部署時需要 |

## 執行順序

```bash
# 1. 設定憑證（見下方「憑證來源」）
export AWS_PROFILE=hoyabit

# 2. 權限預檢（不建立資源）
AWS_REGION=us-west-2 bash aws/verify-permissions.sh

# 3. 部署（腳本尚未建立，見下方「待補」）
```

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

| 項目 | 對應任務 |
|---|---|
| `deploy.sh`（POSIX 部署腳本，本機無 pwsh 因此必要） | D3 |
| Lambda 執行環境的 boto3／botocore 版本控制 | D4 |
| S3 產物持久化（`S3ArtifactStore`） | D6 |
| reserved concurrency、log 保留期、Budgets 告警 | D7 |
