# AWS 權限開通引導

這份文件的目的只有一個：讓你在 AWS 端把權限開到「剛好夠部署 HoyaBIT」，然後用命令證明它真的開好了。

每個步驟都標了執行者：

- **`HUMAN`** — 只有你能做（AWS Console 操作、同意條款、設定帳單）。
- **`BOTH`** — Kiro 給命令，你執行並把輸出貼回來。

全程**不需要**把任何 access key、session token 或密碼貼給 Kiro。如果某個步驟看起來需要，
那是誤解，請停下來確認。

---

## 0. 開始前：填好這張表

後面的 policy JSON 與命令都會用到這些值。先決定，再往下做。

| 項目 | 佔位符 | 你的值 | 怎麼取得 |
|---|---|---|---|
| AWS Account ID | `<ACCOUNT_ID>` | | Console 右上角帳號選單，或 `aws sts get-caller-identity` |
| 部署 region | `<REGION>` | `us-west-2`（本專案已定案） | 見下方說明 |
| Bedrock model ID | `<MODEL_ID>` | | **步驟 5 實測後才填**，不要現在猜 |
| Stack 名稱 | `<STACK_NAME>` | `hoyabit-agent-mvp` | 專案預設，沿用即可 |
| Lambda 函式名稱 | — | `hoyabit-market-research-agent` | `aws/template.yaml` 已硬編 |

### 關於 region 的建議

專案目前預設 `ap-northeast-1`（東京）。但 Amazon Nova 系列在亞太區主要是
[透過 cross-Region inference profile 提供](https://aws.amazon.com/about-aws/whats-new/2025/02/amazon-nova-understanding-models-europe-asia-pacific)，
這會讓 model ID 與 IAM 資源條件都變複雜（詳見 `design.md` 決策 D-1）。

**已定案：`us-west-2`。** 這是競賽環境指定的 region，並已實測確認
`amazon.nova-lite-v1:0` 在該 region 支援 `ON_DEMAND`，可用裸 model ID 直接呼叫 Converse，
不需要 inference profile（詳見 `design.md` 決策 D-1 的實測結果表）。

若你之後要換 region，步驟 5 的實測**必須重跑**。這不是形式要求：同一帳號下
`amazon.nova-micro-v1:0` 的裸 ID 在 `us-east-1` 可呼叫，在 `us-west-2` 卻被拒絕，
必須改用 `us.` 前綴的 profile。亞太區（含東京）的 Nova 需要 `apac.` 前綴，
屆時步驟 3 的 policy 已預留對應權限，但 `aws/template.yaml` 的執行角色需要擴充。

### 關於帳號類型

| 你的情況 | 往下怎麼走 |
|---|---|
| 自己的個人 AWS 帳號 | 照本文件全部步驟做 |
| 公司／組織帳號（AWS Organizations 成員） | 一樣做，但若步驟 5 出現 `AccessDeniedException` 且權限已齊備，很可能是 SCP 擋住，需要找 Organization 管理者放行 Bedrock |
| 競賽方提供的帳號 | 先確認你拿到的身分是否已含所需權限；若已含，可跳過步驟 2–3，直接做步驟 4 起 |

---

## 1. `HUMAN` — 確認你能登入 AWS Console

開 https://console.aws.amazon.com/ 並登入。

登入後在右上角切換到你在步驟 0 決定的 region。這件事容易忘，而且忘了會很難查：
Bedrock 的模型清單、Lambda、CloudFormation 全都是 region 隔離的，你在錯的 region 看不到對的東西。

**安全提醒**：接下來的步驟**不要**使用 root 帳號建立 access key。root access key 一旦洩漏
等於整個帳號失守，且無法用權限邊界限制。如果你目前只有 root 登入，步驟 2 就是在解決這件事。

---

## 2. `HUMAN` — 建立部署身分

兩條路，選一條。**優先選 A**。

### 路線 A（建議）— IAM Identity Center，短期憑證

短期憑證會自動過期，不會有一把長期有效的密鑰躺在你的硬碟裡。

1. Console 搜尋列輸入 `IAM Identity Center`，進入服務。
2. 若尚未啟用，點 **Enable**（會要求選一個 region 作為 identity store，選你的 `<REGION>`）。
3. 左側 **Users** → **Add user**，填入你的名稱與 email，建立。
4. 左側 **Permission sets** → **Create permission set** → 選 **Custom permission set**。
5. 在 **Inline policy** 貼上步驟 3 的 JSON（替換佔位符後）。命名為 `HoyaBITDeployer`。
6. 左側 **AWS accounts** → 勾選你的帳號 → **Assign users or groups** → 選剛建的 user
   → 選 `HoyaBITDeployer` permission set → 送出。
7. 記下左側 **Settings** 裡的 **AWS access portal URL**，步驟 4 會用到。

### 路線 B（次選）— IAM user + access key

**風險說明**：這會產生一組長期有效的憑證。它們存在 `~/.aws/credentials` 明文檔案中，
沒有自動過期機制。如果你選這條路，請在步驟 9 完成後主動刪除該 access key。

1. Console 搜尋 `IAM` → 左側 **Users** → **Create user**。
2. 名稱填 `hoyabit-deployer`。**不要**勾選 Console 存取（這個身分只給 CLI 用）。
3. 下一步的權限頁面選 **Attach policies directly**，但先跳過（步驟 3 才建 policy），直接建立。
4. 進入該 user → **Security credentials** → **Create access key** →
   用途選 **Command Line Interface (CLI)** → 確認警告 → 建立。
5. **立刻**把 Access key ID 與 Secret access key 存進你的密碼管理器。
   Secret 只顯示這一次。**不要**貼進聊天視窗、不要寫進專案檔案、不要 commit。

---

## 3. `HUMAN` — 建立並附加 deployer policy

這份 policy 是「剛好夠部署這個 stack」的權限。它刻意**不含** `AdministratorAccess`，
也不含任何跟本專案無關的資源。

### 3.1 建立 policy

Console → `IAM` → 左側 **Policies** → **Create policy** → 切到 **JSON** 分頁 → 貼上以下內容。

**貼上前先替換三個佔位符**：`<ACCOUNT_ID>`、`<REGION>`、`<STACK_NAME>`（預設 `hoyabit-agent-mvp`）。

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "IdentityAndBedrockDiscovery",
      "Effect": "Allow",
      "Action": [
        "sts:GetCallerIdentity",
        "bedrock:ListFoundationModels",
        "bedrock:GetFoundationModel",
        "bedrock:ListInferenceProfiles",
        "bedrock:GetInferenceProfile"
      ],
      "Resource": "*"
    },
    {
      "Sid": "BedrockInvokeForSmokeTest",
      "Effect": "Allow",
      "Action": "bedrock:InvokeModel",
      "Resource": [
        "arn:aws:bedrock:*::foundation-model/amazon.nova-*",
        "arn:aws:bedrock:*::foundation-model/anthropic.claude-*",
        "arn:aws:bedrock:<REGION>:<ACCOUNT_ID>:inference-profile/*"
      ]
    },
    {
      "Sid": "CloudFormationStackLifecycle",
      "Effect": "Allow",
      "Action": [
        "cloudformation:CreateStack",
        "cloudformation:UpdateStack",
        "cloudformation:DeleteStack",
        "cloudformation:DescribeStacks",
        "cloudformation:DescribeStackEvents",
        "cloudformation:DescribeStackResource",
        "cloudformation:DescribeStackResources",
        "cloudformation:GetTemplate",
        "cloudformation:GetTemplateSummary",
        "cloudformation:CreateChangeSet",
        "cloudformation:DescribeChangeSet",
        "cloudformation:ExecuteChangeSet",
        "cloudformation:DeleteChangeSet",
        "cloudformation:ListChangeSets",
        "cloudformation:TagResource",
        "cloudformation:UntagResource"
      ],
      "Resource": [
        "arn:aws:cloudformation:<REGION>:<ACCOUNT_ID>:stack/<STACK_NAME>/*",
        "arn:aws:cloudformation:<REGION>:<ACCOUNT_ID>:changeSet/*"
      ]
    },
    {
      "Sid": "CloudFormationAccountLevelReads",
      "Effect": "Allow",
      "Action": [
        "cloudformation:ValidateTemplate",
        "cloudformation:ListStacks"
      ],
      "Resource": "*"
    },
    {
      "Sid": "DeployAndArtifactBuckets",
      "Effect": "Allow",
      "Action": [
        "s3:CreateBucket",
        "s3:ListBucket",
        "s3:GetBucketLocation",
        "s3:GetBucketPublicAccessBlock",
        "s3:PutBucketPublicAccessBlock",
        "s3:PutBucketVersioning",
        "s3:PutObject",
        "s3:GetObject",
        "s3:DeleteObject",
        "s3:DeleteBucket"
      ],
      "Resource": [
        "arn:aws:s3:::hoyabit-agent-deploy-*",
        "arn:aws:s3:::hoyabit-agent-deploy-*/*",
        "arn:aws:s3:::hoyabit-agent-artifacts-*",
        "arn:aws:s3:::hoyabit-agent-artifacts-*/*"
      ]
    },
    {
      "Sid": "ManageLambdaExecutionRoleOnly",
      "Effect": "Allow",
      "Action": [
        "iam:CreateRole",
        "iam:GetRole",
        "iam:DeleteRole",
        "iam:UpdateAssumeRolePolicy",
        "iam:AttachRolePolicy",
        "iam:DetachRolePolicy",
        "iam:PutRolePolicy",
        "iam:DeleteRolePolicy",
        "iam:GetRolePolicy",
        "iam:ListRolePolicies",
        "iam:ListAttachedRolePolicies",
        "iam:TagRole",
        "iam:UntagRole"
      ],
      "Resource": "arn:aws:iam::<ACCOUNT_ID>:role/<STACK_NAME>-*"
    },
    {
      "Sid": "PassExecutionRoleToLambdaOnly",
      "Effect": "Allow",
      "Action": "iam:PassRole",
      "Resource": "arn:aws:iam::<ACCOUNT_ID>:role/<STACK_NAME>-*",
      "Condition": {
        "StringEquals": {
          "iam:PassedToService": "lambda.amazonaws.com"
        }
      }
    },
    {
      "Sid": "LambdaFunctionLifecycle",
      "Effect": "Allow",
      "Action": [
        "lambda:CreateFunction",
        "lambda:GetFunction",
        "lambda:GetFunctionConfiguration",
        "lambda:UpdateFunctionCode",
        "lambda:UpdateFunctionConfiguration",
        "lambda:DeleteFunction",
        "lambda:AddPermission",
        "lambda:RemovePermission",
        "lambda:GetPolicy",
        "lambda:CreateFunctionUrlConfig",
        "lambda:GetFunctionUrlConfig",
        "lambda:UpdateFunctionUrlConfig",
        "lambda:DeleteFunctionUrlConfig",
        "lambda:PutFunctionConcurrency",
        "lambda:GetFunctionConcurrency",
        "lambda:DeleteFunctionConcurrency",
        "lambda:TagResource",
        "lambda:UntagResource",
        "lambda:ListTags",
        "lambda:InvokeFunction",
        "lambda:InvokeFunctionUrl"
      ],
      "Resource": "arn:aws:lambda:<REGION>:<ACCOUNT_ID>:function:hoyabit-market-research-agent"
    },
    {
      "Sid": "LambdaLayerForAwsSdk",
      "Effect": "Allow",
      "Action": [
        "lambda:PublishLayerVersion",
        "lambda:GetLayerVersion",
        "lambda:DeleteLayerVersion",
        "lambda:ListLayerVersions"
      ],
      "Resource": "arn:aws:lambda:<REGION>:<ACCOUNT_ID>:layer:hoyabit-*"
    },
    {
      "Sid": "LogGroupManagementAndReads",
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:DeleteLogGroup",
        "logs:PutRetentionPolicy",
        "logs:DescribeLogStreams",
        "logs:GetLogEvents",
        "logs:FilterLogEvents",
        "logs:TagResource"
      ],
      "Resource": [
        "arn:aws:logs:<REGION>:<ACCOUNT_ID>:log-group:/aws/lambda/hoyabit-market-research-agent",
        "arn:aws:logs:<REGION>:<ACCOUNT_ID>:log-group:/aws/lambda/hoyabit-market-research-agent:*"
      ]
    },
    {
      "Sid": "LogGroupDiscovery",
      "Effect": "Allow",
      "Action": "logs:DescribeLogGroups",
      "Resource": "*"
    }
  ]
}
```

命名為 `HoyaBITDeployerPolicy`，建立。

### 3.2 這份 policy 為什麼長這樣

| Statement | 為什麼需要 | 為什麼這樣限制 |
|---|---|---|
| `IdentityAndBedrockDiscovery` | 驗證身分、查模型清單 | 這些 API 不支援資源層級限制，只能 `*`；它們都是唯讀 |
| `BedrockInvokeForSmokeTest` | 步驟 5 與本機 live smoke 要真的呼叫模型 | 限定 Nova／Claude 家族與你帳號的 inference profile。`Converse` API 的授權動作就是 `bedrock:InvokeModel` |
| `CloudFormationStackLifecycle` | 建立、更新、刪除 stack | 綁死在 `<STACK_NAME>`，動不到別的 stack |
| `CloudFormationAccountLevelReads` | `validate-template` 不吃資源條件 | 唯讀 |
| `DeployAndArtifactBuckets` | 上傳部署 zip；D6 的產物 bucket | 只限 `hoyabit-agent-deploy-*` 與 `hoyabit-agent-artifacts-*` 前綴 |
| `ManageLambdaExecutionRoleOnly` | CloudFormation 要幫你建 Lambda 的執行角色 | 只限 `<STACK_NAME>-*` 名稱前綴。這是整份 policy 最敏感的部分，因此限制最緊 |
| `PassExecutionRoleToLambdaOnly` | 把角色交給 Lambda 服務 | 加了 `iam:PassedToService` 條件，這個角色只能給 Lambda，不能拿去 assume 成別的東西 |
| `LambdaFunctionLifecycle` | 建函式、設 Function URL、設併發上限 | 綁死單一函式名稱 |
| `LambdaLayerForAwsSdk` | D4 若選 Layer 方式打包 SDK | 只限 `hoyabit-*` 前綴的 layer |
| `LogGroupManagementAndReads` | 設 log 保留期、讀執行記錄 | 只限本函式的 log group |
| `LogGroupDiscovery` | `describe-log-groups` 不吃資源條件 | 唯讀 |

**刻意沒給的權限**，以及若缺會怎樣：

- `iam:CreatePolicy`／`iam:*` 全域 — 你不需要它。若 CloudFormation 報 IAM 相關
  `AccessDenied`，先確認 role 名稱是否落在 `<STACK_NAME>-*` 前綴內。
- `bedrock:PutFoundationModelEntitlement` — 只有需要在 Console 外「申請模型存取」時才用到。
  步驟 5 若要求申請，用 Console 做（`HUMAN`），不要為此擴權。
- `budgets:*`／`ce:*` — 成本告警建議用你的主要登入身分在 Console 設定（步驟 8）。
- `AdministratorAccess` — 不要附加。若你「為了快」附加了它，請在部署完成後移除。

### 3.3 附加 policy

- **路線 A**：回到 IAM Identity Center → **Permission sets** → `HoyaBITDeployer` →
  **Permissions** → 把 `HoyaBITDeployerPolicy` 加為 customer managed policy（或直接用 inline policy 貼上同一份 JSON）。
- **路線 B**：IAM → **Users** → `hoyabit-deployer` → **Add permissions** →
  **Attach policies directly** → 勾選 `HoyaBITDeployerPolicy` → 儲存。

---

## 4. `BOTH` — 本機設定憑證並驗證

本機已有 AWS CLI 2.36.2（已確認），所以直接設定就好。

### 路線 A — SSO

```bash
aws configure sso --profile hoyabit
```

互動式提示會問：

| 提示 | 填什麼 |
|---|---|
| SSO session name | `hoyabit` |
| SSO start URL | 步驟 2 記下的 AWS access portal URL |
| SSO region | 你的 `<REGION>` |
| SSO registration scopes | 直接 Enter 用預設 |

瀏覽器會開啟要你授權。回到終端後選擇帳號與 `HoyaBITDeployer` permission set，
CLI default region 填 `<REGION>`，output format 填 `json`。

之後每次憑證過期就重新登入：

```bash
aws sso login --profile hoyabit
```

### 路線 B — access key

```bash
aws configure --profile hoyabit
```

依序貼入 Access key ID、Secret access key、`<REGION>`、`json`。

### 驗證（兩條路線都要做）

```bash
export AWS_PROFILE=hoyabit
aws sts get-caller-identity
```

**通過的樣子**：輸出含 `Account` 與 `Arn` 三個欄位。

**失敗處理**：

| 訊息 | 意思 | 怎麼辦 |
|---|---|---|
| `Unable to locate credentials` | profile 沒設好或 `AWS_PROFILE` 沒生效 | 確認 `aws configure list --profile hoyabit` 有值 |
| `ExpiredToken` | SSO session 過期 | `aws sso login --profile hoyabit` |
| `InvalidClientTokenId` | access key 被刪或打錯 | 重新確認 key，或改走路線 A |

把 `aws sts get-caller-identity` 的輸出貼給 Kiro 時，**Account ID 可以保留**（它不是機密），
但**不要**貼任何 access key 或 session token。

---

## 5. `BOTH` — Bedrock 模型存取：開通與實測

這是整個流程最容易卡住的一步，也是唯一必須「實測而非查文件」的一步。

### 5.1 現在的模型存取規則

AWS 在 2025 年 10 月
[簡化了 Bedrock 模型存取流程](https://aws.amazon.com/blogs/security/simplified-amazon-bedrock-model-access)：
多數 serverless foundation model 預設可用，不必逐個申請；但部分供應商的模型
（例如 Anthropic）首次使用時仍可能要求填寫使用案例資訊。

換句話說：**你可能什麼都不用開**。所以先測，不要先申請。

> 上述內容依授權要求改寫自 AWS 官方公告。

### 5.2 先看模型在不在

```bash
export AWS_PROFILE=hoyabit
export REGION=us-west-2   # 本專案已定案的 <REGION>

aws bedrock list-foundation-models \
  --region "$REGION" \
  --by-output-modality TEXT \
  --query "modelSummaries[?contains(modelId, 'nova')].[modelId,modelName,inferenceTypesSupported]" \
  --output table
```

看 `inferenceTypesSupported` 這一欄，它決定你要用哪種 model ID：

| `inferenceTypesSupported` 包含 | 意思 | `<MODEL_ID>` 填什麼 |
|---|---|---|
| `ON_DEMAND` | 可以直接呼叫這個 region 的模型 | 裸 model ID，例如 `amazon.nova-lite-v1:0` |
| 只有 `INFERENCE_PROFILE` | 必須透過 cross-Region inference profile | 見 5.3 |

### 5.3 若需要 inference profile

```bash
aws bedrock list-inference-profiles \
  --region "$REGION" \
  --query "inferenceProfileSummaries[?contains(inferenceProfileId, 'nova-lite')].[inferenceProfileId,status]" \
  --output table
```

拿到的 ID 會帶地理前綴（例如 `us.amazon.nova-lite-v1:0` 或 `apac.amazon.nova-lite-v1:0`）。
把它填進 `<MODEL_ID>`。

**這種情況下 IAM 要注意**：cross-Region inference 會把請求路由到其他 region 的
foundation model，所以權限必須同時涵蓋 inference profile 與**目的 region** 的 foundation model
（[官方前置條件](https://docs.aws.amazon.com/bedrock/latest/userguide/cross-region-inference-prereq.html)）。
步驟 3 的 policy 已用 `arn:aws:bedrock:*::foundation-model/amazon.nova-*` 預留這點，
但 `aws/template.yaml` 裡的 **Lambda execution role 還沒有**——那是 D2 任務要修的（見 §7）。

### 5.4 實際呼叫一次

這一步才是真正的驗收。不要跳過，也不要用完整研究管線測（浪費時間與配額）。

```bash
export AWS_PROFILE=hoyabit
export REGION=us-west-2                      # 本專案已定案的 <REGION>
export MODEL_ID=amazon.nova-lite-v1:0        # 5.2/5.3 實測得到的 <MODEL_ID>

python3 - <<'PY'
import os, boto3
client = boto3.client("bedrock-runtime", region_name=os.environ["REGION"])
resp = client.converse(
    modelId=os.environ["MODEL_ID"],
    messages=[{"role": "user", "content": [{"text": "Reply with the single word: ready"}]}],
    inferenceConfig={"maxTokens": 16, "temperature": 0},
)
text = "".join(p.get("text", "") for p in resp["output"]["message"]["content"])
print("MODEL_OK:", repr(text.strip()))
PY
```

**通過的樣子**：印出 `MODEL_OK: 'ready'`（或任何非空文字）。

這裡用 `python3` + boto3 而不是 AWS CLI，是因為專案的 `src/llm.py` 走的就是
boto3 的 `bedrock-runtime.converse()`。用同一條路徑測，結果才有意義。

**失敗處理**：

| 訊息 | 根因 | 怎麼辦 |
|---|---|---|
| `AccessDeniedException` + 提到 model | 沒有模型存取權，或 SCP 擋住 | Console → Bedrock → 左側 **Model access** 檢查狀態；組織帳號請找管理者確認 SCP |
| `AccessDeniedException` + 提到 `inference-profile` | IAM 少了 profile 或目的 region 的 FM 授權 | 回步驟 3 確認 `BedrockInvokeForSmokeTest` 的 Resource 清單 |
| `ValidationException` + `on-demand throughput isn't supported` | 用了裸 model ID 但該 region 只支援 inference profile | 回 5.3 換成 profile ID |
| `ResourceNotFoundException` | model ID 拼錯，或該 region 沒這個模型 | 回 5.2 確認實際 ID |
| `Unknown service: 'bedrock-runtime'` | 本機 boto3 太舊 | 本機實測為 1.42.97，含 `Converse`；若你換了 Python 環境，`pip3 install -U boto3` |
| `botocore.exceptions.NoRegionError` | 沒帶 region | 確認 `REGION` 環境變數已 export |

### 5.5 若需要手動開通（`HUMAN`）

只有在 5.4 明確回報缺少模型存取權時才做：

1. Console → 搜尋 `Bedrock` → 確認右上角 region 是你的 `<REGION>`。
2. 左側 **Bedrock configurations** → **Model access**。
3. 找到目標模型，若狀態不是可用，點 **Modify model access** 或 **Enable**。
4. 部分供應商會要求填寫使用案例與同意條款，填完送出。
5. 等狀態變為可用（Amazon 自家模型通常即時；其他供應商可能要等）。
6. 回到 5.4 重測。

### 5.6 把實測結果寫回專案

實測通過後，`<REGION>` 與 `<MODEL_ID>` 這兩個值就定了。它們要更新到三個地方
（這部分交給 Kiro 在 D2 任務做，你只要把值告訴 Kiro）：

- `.env.example` 的 `AWS_REGION` 與 `BEDROCK_MODEL_ID` 預設值
- `aws/template.yaml` 的 `BedrockModelId` 參數預設值
- `aws/template.yaml` 的 Lambda execution role IAM 資源清單（見 §7）

---

## 6. `BOTH` — 一次驗證所有部署權限

在真的部署之前，用這段腳本確認每一類權限都通。它**不會建立任何資源**，
全部是唯讀呼叫加一次 template 驗證。

```bash
#!/usr/bin/env bash
# 權限預檢：不建立任何資源
set -uo pipefail

: "${AWS_PROFILE:?請先 export AWS_PROFILE=hoyabit}"
: "${REGION:?請先 export REGION=<你的 region>}"
STACK_NAME="${STACK_NAME:-hoyabit-agent-mvp}"
PASS=0; FAIL=0

check() {
  local label="$1"; shift
  if out=$("$@" 2>&1); then
    printf '  PASS  %s\n' "$label"; PASS=$((PASS+1))
  else
    printf '  FAIL  %s\n        %s\n' "$label" "$(printf '%s' "$out" | head -1)"; FAIL=$((FAIL+1))
  fi
}

echo "== 身分 =="
check "sts:GetCallerIdentity" \
  aws sts get-caller-identity

echo "== Bedrock =="
check "bedrock:ListFoundationModels" \
  aws bedrock list-foundation-models --region "$REGION" --max-results 1
check "bedrock:ListInferenceProfiles" \
  aws bedrock list-inference-profiles --region "$REGION" --max-results 1

echo "== CloudFormation =="
check "cloudformation:ValidateTemplate" \
  aws cloudformation validate-template --region "$REGION" \
      --template-body "file://aws/template.yaml"
check "cloudformation:ListStacks" \
  aws cloudformation list-stacks --region "$REGION" --max-items 1

echo "== S3 =="
check "s3:ListBucket（不存在也算通過，403 才是失敗）" \
  bash -c 'aws s3api list-objects-v2 --bucket "hoyabit-agent-deploy-$(aws sts get-caller-identity --query Account --output text)-'"$REGION"'" --max-items 1 2>&1 | grep -qv "AccessDenied"'

echo "== Lambda =="
check "lambda:GetFunction（NotFound 算通過，AccessDenied 才是失敗）" \
  bash -c 'out=$(aws lambda get-function --region '"$REGION"' --function-name hoyabit-market-research-agent 2>&1); echo "$out" | grep -q "AccessDenied" && exit 1 || exit 0'

echo "== CloudWatch Logs =="
check "logs:DescribeLogGroups" \
  aws logs describe-log-groups --region "$REGION" --limit 1

printf '\n結果：%d 通過，%d 失敗\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] || { echo "有項目失敗，請回步驟 3 檢查 policy 是否完整貼上並附加成功。"; exit 1; }
echo "權限預檢通過，可以進行部署。"
```

在專案根目錄執行（因為它會讀 `aws/template.yaml`）。
D1 任務會把這段落地成 `aws/verify-permissions.sh`，屆時直接跑檔案即可。

注意兩個「不存在也算通過」的檢查：部署前 bucket 與 function 本來就不存在，
我們要區分的是 `NotFound`（正常）與 `AccessDenied`（權限不足）。

---

## 7. Lambda 執行角色：你不用手動建，但要知道它

Lambda 執行時用的角色**不是**你的部署身分。它由 CloudFormation 依
`aws/template.yaml` 的 `AgentRole` 自動建立，附加：

- `AWSLambdaBasicExecutionRole`（寫 CloudWatch Logs）
- 條件式的 `bedrock:InvokeModel`，資源限定為
  `arn:${AWS::Partition}:bedrock:${AWS::Region}::foundation-model/${BedrockModelId}`

**已知缺口**：這個資源條件只涵蓋「同 region 的裸 foundation model」。如果步驟 5 的實測結果是
需要 inference profile，這個角色會在雲端拿到 `AccessDeniedException`，
而且失敗會被 orchestrator 攔下降級成 offline fallback——**執行看起來成功，模型其實沒跑**。
這正是 `docs/COMPETITION_BLOCKERS.md` 記載的 B2 在雲端的翻版。

修這個缺口是 **D2 任務**的工作，屬於 `KIRO`。你只需要把步驟 5 的實測結果告訴 Kiro。

---

## 8. `HUMAN` — 成本護欄

部署後這個 stack 會產生費用的地方：

| 項目 | 計費方式 | 主要風險 |
|---|---|---|
| Lambda | 執行時間 × 記憶體 | 單次執行最長 900 秒；併發失控時放大 |
| Bedrock | 輸入／輸出 token | 公開 URL 被大量呼叫時放大 |
| S3 | 儲存量 + 請求數 | 部署 zip 與產物，量小 |
| CloudWatch Logs | 寫入量 + 儲存量 | 無保留期限時長期累積 |

以低併發展示用途估計，費用很小。但這是**估算，不是承諾**——實際取決於呼叫次數與 token 量。

建議設一個告警（用你的主要登入身分在 Console 做，不需要擴充 deployer 權限）：

1. Console → 搜尋 `Billing and Cost Management` → 左側 **Budgets** → **Create budget**。
2. 選 **Cost budget** → 設一個你能接受的月額度。
3. 設定告警門檻（例如實際花費達 50% 與 80% 時）與收件 email。
4. 建立。

查實際用量：

```bash
aws ce get-cost-and-usage \
  --time-period Start=$(date -u -v-7d +%Y-%m-%d),End=$(date -u +%Y-%m-%d) \
  --granularity DAILY --metrics UnblendedCost \
  --group-by Type=DIMENSION,Key=SERVICE
```

這個命令需要 `ce:GetCostAndUsage`，不在 deployer policy 內。用你的主要身分執行，
或直接看 Console 的 Cost Explorer。

---

## 9. `HUMAN` — 收尾：撤銷不再需要的權限

Demo 或驗收結束後：

1. **先刪 stack**（順序很重要，權限還在才刪得掉）：
   ```bash
   aws cloudformation delete-stack --region "$REGION" --stack-name hoyabit-agent-mvp
   aws cloudformation wait stack-delete-complete --region "$REGION" --stack-name hoyabit-agent-mvp
   ```
2. 清空並刪除部署 bucket（`delete-stack` 不會動它，因為它不是 stack 資源）。
3. **若走路線 B**：IAM → Users → `hoyabit-deployer` → Security credentials →
   刪除 access key。這是唯一能確保那組長期憑證失效的方式。
4. **若走路線 A**：不需特別處理，SSO session 會自然過期。
   要更徹底就移除 permission set 指派。
5. 本機清理：`rm -rf aws/.build`（部署 zip 的暫存目錄）。

完整拆除步驟與驗證見 `tasks.md` 的 D9。

---

## 常見卡點速查

| 症狀 | 最可能的原因 | 先檢查什麼 |
|---|---|---|
| 所有 AWS 命令都 `NoCredentials` | `AWS_PROFILE` 沒 export，或 profile 名稱打錯 | `aws configure list-profiles` |
| Console 看不到 Bedrock 模型 | region 切錯 | Console 右上角 region |
| CLI 通但 Console 不通（或反之） | 兩者用的是不同身分 | `aws sts get-caller-identity` 對照 Console 右上角 |
| 權限都給了還是 `AccessDenied` | Organization SCP 或 permission boundary | 找 Organization 管理者；SCP 的拒絕無法用 IAM policy 覆蓋 |
| `delete-stack` 卡在 `DELETE_FAILED` | 有資源被外部改動或有依賴殘留 | `describe-stack-events` 看最早的失敗事件 |
| 部署成功但報告顯示 offline fallback | Lambda 的 Bedrock 呼叫失敗被降級 | CloudWatch Logs 找 `boto3`／`AccessDenied`／`Unknown service` |

---

## 安全底線

- 不要把 access key、secret、session token 貼進聊天視窗或專案檔案。
- 不要 commit `.env`。專案的 `.gitignore` 已排除，但別繞過它。
- 不要用 root 帳號建立 access key。
- 不要為了讓命令通過而附加 `AdministratorAccess`。若你已經這麼做，部署完成後移除它。
- Function URL 是 `AuthType: NONE`，**任何拿到 URL 的人都能觸發完整執行並消耗你的模型配額**。
  URL 不要公開貼在網路上，Demo 結束就刪 stack。
- Account ID 不是機密，可以貼給 Kiro。access key 是機密，不可以。

---

## 參考來源

- [Bedrock 模型存取](https://docs.aws.amazon.com/bedrock/latest/userguide/model-access.html)
- [簡化的 Bedrock 模型存取（2025-10）](https://aws.amazon.com/blogs/security/simplified-amazon-bedrock-model-access)
- [新增或移除 Bedrock 模型存取](https://docs.aws.amazon.com/bedrock/latest/userguide/model-access-modify.html)
- [cross-Region inference 前置條件與 IAM](https://docs.aws.amazon.com/bedrock/latest/userguide/cross-region-inference-prereq.html)
- [Amazon Nova understanding 模型在歐洲與亞太的可用性](https://aws.amazon.com/about-aws/whats-new/2025/02/amazon-nova-understanding-models-europe-asia-pacific)
- [排解 Bedrock 的 AccessDeniedException](https://repost.aws/knowledge-center/bedrock-access-denied-exception)
- [升級 Lambda 中的 boto3／botocore](https://repost.aws/knowledge-center/lambda-upgrade-boto3-botocore)

外部來源內容均經改寫與摘要，以符合授權限制。
