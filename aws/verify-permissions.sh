#!/usr/bin/env bash
#
# AWS 部署權限預檢。
#
# 這個腳本只做唯讀呼叫與一次 template 驗證，不會建立、修改或刪除任何 AWS 資源。
# 目的是在真的部署之前，先確認每一類權限是否足夠，避免部署到一半才失敗。
#
# 用法：
#   1. 先設定憑證（Workshop Studio 環境請從活動頁面的 "Get AWS CLI credentials" 取得）：
#        export AWS_ACCESS_KEY_ID="..."
#        export AWS_SECRET_ACCESS_KEY="..."
#        export AWS_SESSION_TOKEN="..."
#      或使用具名 profile：
#        export AWS_PROFILE=hoyabit
#   2. 在專案根目錄執行：
#        bash aws/verify-permissions.sh
#
# 結果會同時印在終端並存到 REPORT 指定的檔案。
# 輸出只含 Account ID、role 名稱與 allowed/denied 判定，不含任何憑證。

set -uo pipefail

REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-west-2}}"
STACK_NAME="${STACK_NAME:-hoyabit-agent-mvp}"
FUNCTION_NAME="hoyabit-market-research-agent"
REPORT="${REPORT:-/tmp/hoyabit-preflight.txt}"

# Python 3.9 的 boto3 淘汰警告與本次檢查無關，靜音以免蓋掉真正的結果。
export PYTHONWARNINGS="${PYTHONWARNINGS:-ignore}"

PASS=0; FAIL=0; SKIP=0

log()  { printf '%s\n' "$*"; }
head2() { printf '\n== %s ==\n' "$*"; }
ok()   { printf '  PASS  %s\n' "$*"; PASS=$((PASS+1)); }
no()   { printf '  FAIL  %s\n' "$*"; FAIL=$((FAIL+1)); }
skip() { printf '  SKIP  %s\n' "$*"; SKIP=$((SKIP+1)); }

# 執行一個命令；成功印 PASS，失敗印 FAIL 加第一行錯誤。
try() {
  local label="$1"; shift
  local out
  if out=$("$@" 2>&1); then
    ok "$label"
    [ -n "$out" ] && printf '        %s\n' "$(printf '%s' "$out" | head -3)"
  else
    no "$label"
    printf '        %s\n' "$(printf '%s' "$out" | tr '\n' ' ' | cut -c1-160)"
  fi
}

run_preflight() {
  log "HoyaBIT AWS 部署權限預檢"
  log "時間：$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  log "region：$REGION    stack：$STACK_NAME"

  # ------------------------------------------------------------------
  head2 "0. 前置檢查"
  # ------------------------------------------------------------------
  if ! command -v aws >/dev/null 2>&1; then
    no "找不到 aws CLI"
    log ""
    log "請先安裝 AWS CLI v2 後重跑。"
    return 1
  fi
  ok "aws CLI 可用（$(aws --version 2>&1 | cut -d' ' -f1)）"

  if [ ! -f aws/template.yaml ]; then
    no "找不到 aws/template.yaml —— 請在專案根目錄執行本腳本"
    return 1
  fi
  ok "在專案根目錄執行"

  # ------------------------------------------------------------------
  head2 "1. 身分"
  # ------------------------------------------------------------------
  local identity account arn
  if ! identity=$(aws sts get-caller-identity --output json 2>&1); then
    no "sts:GetCallerIdentity"
    printf '        %s\n' "$(printf '%s' "$identity" | tr '\n' ' ' | cut -c1-160)"
    log ""
    log "憑證尚未設定或已過期。Workshop Studio 環境請回到活動頁面，"
    log "點 \"Get AWS CLI credentials\"，複製 macOS/Linux 那組三行 export 後重跑。"
    log "注意：Workshop 是臨時憑證，AWS_SESSION_TOKEN 不可省略。"
    return 1
  fi
  account=$(printf '%s' "$identity" | python3 -c 'import json,sys; print(json.load(sys.stdin)["Account"])' 2>/dev/null)
  arn=$(printf '%s' "$identity" | python3 -c 'import json,sys; print(json.load(sys.stdin)["Arn"])' 2>/dev/null)
  ok "sts:GetCallerIdentity"
  log "        Account: $account"
  log "        Arn:     $arn"

  # ------------------------------------------------------------------
  head2 "2. Bedrock：模型清單"
  # ------------------------------------------------------------------
  try "bedrock:ListFoundationModels" \
    aws bedrock list-foundation-models --region "$REGION" \
      --query "modelSummaries[?contains(modelId,'nova')].[modelId,inferenceTypesSupported]" \
      --output text

  # ------------------------------------------------------------------
  head2 "3. Bedrock：實際呼叫（SigV4，不使用 API key）"
  # ------------------------------------------------------------------
  # 依序試裸 foundation model ID 與 cross-Region inference profile 前綴。
  # 只要有一個成功，本專案的 BEDROCK_MODEL_ID 就用那一個。
  if ! python3 - "$REGION" <<'PY'
import sys

try:
    import boto3
    import botocore.exceptions as be
except ImportError:
    print("        boto3 not installed locally; skipping live converse probe")
    sys.exit(2)

region = sys.argv[1]
client = boto3.client("bedrock-runtime", region_name=region)
candidates = [
    "amazon.nova-lite-v1:0",
    "us.amazon.nova-lite-v1:0",
    "apac.amazon.nova-lite-v1:0",
    "amazon.nova-micro-v1:0",
    "us.amazon.nova-micro-v1:0",
]
any_ok = False
for model_id in candidates:
    try:
        response = client.converse(
            modelId=model_id,
            messages=[{"role": "user", "content": [{"text": "say ready"}]}],
            inferenceConfig={"maxTokens": 16, "temperature": 0},
        )
        text = "".join(
            part.get("text", "") for part in response["output"]["message"]["content"]
        ).strip()
        print(f"  PASS  converse {model_id} -> {text!r}")
        any_ok = True
    except be.ClientError as error:
        info = error.response.get("Error", {})
        print(f"  FAIL  converse {model_id} -> {info.get('Code')}: {info.get('Message', '')[:100]}")
    except Exception as error:  # 網路、認證等非 API 錯誤
        print(f"  FAIL  converse {model_id} -> {type(error).__name__}: {str(error)[:100]}")
sys.exit(0 if any_ok else 1)
PY
  then
    no "沒有任何 model ID 可成功呼叫（詳見上方每一行）"
  else
    ok "至少一個 model ID 可成功呼叫"
  fi

  # ------------------------------------------------------------------
  head2 "4. CloudFormation"
  # ------------------------------------------------------------------
  try "cloudformation:ValidateTemplate" \
    aws cloudformation validate-template --region "$REGION" \
      --template-body file://aws/template.yaml \
      --query "Parameters[].ParameterKey" --output text

  try "cloudformation:ListStacks（看環境有沒有預建 stack）" \
    aws cloudformation list-stacks --region "$REGION" \
      --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE \
      --query "StackSummaries[].StackName" --output text

  # ------------------------------------------------------------------
  head2 "5. 部署關鍵動作（IAM 模擬，不實際執行）"
  # ------------------------------------------------------------------
  # iam:CreateRole 是整個部署路線的分水嶺：現行 template 讓 CloudFormation 自建
  # Lambda 執行角色，若此項被拒，就必須改用環境預先建好的角色。
  #
  # simulate-principal-policy 只接受 IAM 實體 ARN，不接受 STS 的 assumed-role ARN，
  # 因此先把 arn:aws:sts::<acct>:assumed-role/<Role>/<session>
  # 轉回 arn:aws:iam::<acct>:role/<Role>。
  local sim sim_arn role_name
  sim_arn="$arn"
  if printf '%s' "$arn" | grep -q ':assumed-role/'; then
    role_name=$(printf '%s' "$arn" | sed -E 's#.*:assumed-role/([^/]+)/.*#\1#')
    sim_arn="arn:aws:iam::${account}:role/${role_name}"
    log "        來源 ARN 轉換：assumed-role -> $sim_arn"
  fi
  if sim=$(aws iam simulate-principal-policy \
        --policy-source-arn "$sim_arn" \
        --action-names iam:CreateRole iam:PassRole iam:GetRole \
                       lambda:CreateFunction lambda:CreateFunctionUrlConfig \
                       lambda:UpdateFunctionCode cloudformation:CreateStack \
                       s3:CreateBucket s3:PutObject logs:CreateLogGroup \
        --query "EvaluationResults[].[EvalActionName,EvalDecision]" \
        --output text 2>&1); then
    ok "iam:SimulatePrincipalPolicy 可用，逐項判定如下"
    printf '%s\n' "$sim" | sed 's/^/        /'
  else
    skip "iam:SimulatePrincipalPolicy 不可用"
    printf '        %s\n' "$(printf '%s' "$sim" | tr '\n' ' ' | cut -c1-160)"
    log "        模擬不可用時無法預判 iam:CreateRole，只能由實際部署驗證。"
    log "        CloudFormation 失敗會自動 rollback，因此直接部署是安全的判斷方式。"
  fi

  # ------------------------------------------------------------------
  head2 "6. 既有資源（判斷是否有預建角色可用）"
  # ------------------------------------------------------------------
  try "iam:ListRoles（尋找可重用的 Lambda 執行角色）" \
    aws iam list-roles \
      --query "Roles[?contains(RoleName,'ambda') || contains(RoleName,'WSParticipant') || contains(RoleName,'Bedrock')].RoleName" \
      --output text

  try "lambda:ListFunctions" \
    aws lambda list-functions --region "$REGION" \
      --query "Functions[].FunctionName" --output text

  # 部署前 function 本來就不存在，因此要區分 NotFound（正常）與 AccessDenied（權限不足）。
  local lam
  lam=$(aws lambda get-function --region "$REGION" --function-name "$FUNCTION_NAME" 2>&1)
  if printf '%s' "$lam" | grep -q "AccessDenied\|not authorized"; then
    no "lambda:GetFunction 被拒（權限不足）"
  elif printf '%s' "$lam" | grep -q "ResourceNotFound"; then
    ok "lambda:GetFunction 可用（函式尚未建立，屬正常）"
  else
    ok "lambda:GetFunction 可用（函式已存在）"
  fi

  # ------------------------------------------------------------------
  head2 "7. CloudWatch Logs"
  # ------------------------------------------------------------------
  try "logs:DescribeLogGroups" \
    aws logs describe-log-groups --region "$REGION" --limit 1 \
      --query "logGroups[].logGroupName" --output text

  # ------------------------------------------------------------------
  head2 "結果"
  # ------------------------------------------------------------------
  log "  PASS $PASS    FAIL $FAIL    SKIP $SKIP"
  log ""
  if [ "$FAIL" -eq 0 ]; then
    log "所有檢查通過，可以進行部署。"
  else
    log "有項目失敗。請把本報告貼回對話，不要自行擴權到 AdministratorAccess——"
    log "失敗項目本身就是判斷部署路線的依據（特別是 iam:CreateRole）。"
  fi
  log ""
  log "完整報告：$REPORT"
  return 0
}

run_preflight 2>&1 | tee "$REPORT"
exit "${PIPESTATUS[0]}"
