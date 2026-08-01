#!/usr/bin/env bash
#
# HoyaBIT Agent 的 POSIX 部署腳本（macOS / Linux）。
#
# 與 aws/deploy.ps1 行為對等，額外提供三件 deploy.ps1 沒有的東西：
#   1. bedrock 作為合法的 provider 值
#   2. 可覆寫 BedrockModelId
#   3. --dry-run：只打包不呼叫任何 AWS API，因此打包邏輯可以在沒有憑證時被驗證
#
# 存在的理由不是便利性：本專案的開發機沒有 pwsh，deploy.ps1 完全無法執行。
#
# 用法：
#   bash aws/deploy.sh --help
#   bash aws/deploy.sh --dry-run
#   AWS_PROFILE=hoyabit bash aws/deploy.sh
#
# 不會做的事：不修改任何 src/ 程式碼、不改變研究邏輯、不刪除 deploy.ps1。

set -uo pipefail

# ----------------------------------------------------------------------------------
# 預設值
# ----------------------------------------------------------------------------------

# region 與 model ID 是一組，不可分開改。us-west-2 + amazon.nova-lite-v1:0 已於
# 2026-08-01 實測確認支援 ON_DEMAND（見 aws/README.md）。換 region 前先跑
# aws/verify-permissions.sh 的第 3 項重新實測，模型可用形式依 region 而異。
# 每一項都可由 .env 或環境變數覆寫，命令行參數優先序最高。
REGION="${AWS_REGION:-us-west-2}"
STACK_NAME="${DEPLOY_STACK_NAME:-hoyabit-agent-mvp}"
PROVIDER="${LLM_PROVIDER:-bedrock}"
MODEL_ID="${BEDROCK_MODEL_ID:-amazon.nova-lite-v1:0}"
SECRET_ARN="${LLM_SECRET_ARN:-}"
DRY_RUN=0
BUNDLE_SDK=0

# D7 護欄。AuthType NONE 是 Demo 的刻意取捨；併發上限受帳號 unreserved 額度限制（見 template）。
AUTH_TYPE="${DEPLOY_AUTH_TYPE:-NONE}"
CONCURRENCY="${DEPLOY_CONCURRENCY:-5}"
LOG_RETENTION="${DEPLOY_LOG_RETENTION:-7}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# 打包的 commit。Lambda 套件裡沒有 .git，不注入的話 manifest 的 code_commit 永遠是 unknown，
# 「線上跑的是哪一版」就無法從產物回答。工作區有未提交變更時加 `-dirty`，避免把「本機改過但
# 沒 commit」的部署誤標成某個乾淨 commit。
CODE_COMMIT="$(git -C "$PROJECT_ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
if [ "$CODE_COMMIT" != "unknown" ] && ! git -C "$PROJECT_ROOT" diff --quiet HEAD 2>/dev/null; then
  CODE_COMMIT="$CODE_COMMIT-dirty"
fi

# ----------------------------------------------------------------------------------
# 載入 .env（選用）
# ----------------------------------------------------------------------------------

# 行為與 src/app.py 的 load_dotenv() 一致：不覆蓋已存在的環境變數，因此
# 優先序為 命令行參數 > 既有環境變數 > .env > 本腳本預設值。
#
# 刻意不使用 `source .env`：那會執行檔案內容，一個手誤或惡意的值就能跑任意命令。
# 這裡只做 KEY=value 的字面解析，並且只接受合法的變數名稱。
load_env_file() {
  local file="$PROJECT_ROOT/.env"
  [ -f "$file" ] || return 0
  local line key value
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|'#'*) continue ;; esac
    case "$line" in *=*) ;; *) continue ;; esac
    key="${line%%=*}"
    value="${line#*=}"
    key="$(printf '%s' "$key" | tr -d '[:space:]')"
    # 只接受 A-Z / 0-9 / _ 的變數名，其餘一律忽略。
    case "$key" in
      ''|*[!A-Za-z0-9_]*) continue ;;
    esac
    # 去掉包住值的成對引號，但不做任何展開。
    value="${value%$'\r'}"
    case "$value" in
      \"*\") value="${value#\"}"; value="${value%\"}" ;;
      \'*\') value="${value#\'}"; value="${value%\'}" ;;
    esac
    [ -n "$value" ] || continue
    # 已由環境或呼叫端設定時不覆蓋。
    [ -n "${!key:-}" ] && continue
    export "$key=$value"
  done < "$file"
}

load_env_file
BUILD_ROOT="$SCRIPT_DIR/.build"
STAGE_ROOT="$BUILD_ROOT/package"
ZIP_PATH="$BUILD_ROOT/agent.zip"

usage() {
  cat <<'USAGE'
用法：bash aws/deploy.sh [選項]

選項：
  --region REGION        部署 region（預設 us-west-2，或環境變數 AWS_REGION）
  --stack-name NAME      CloudFormation stack 名稱（預設 hoyabit-agent-mvp）
  --provider PROVIDER    LLM provider：bedrock | gemini | openai | none（預設 bedrock）
  --model-id ID          Bedrock model ID（預設 amazon.nova-lite-v1:0）
  --secret-arn ARN       Secrets Manager ARN，僅 gemini／openai 需要；bedrock 不使用
  --profile PROFILE      AWS CLI profile（等同 export AWS_PROFILE）
  --bundle-sdk           把 boto3／botocore 打包進部署包（僅在 Lambda 內建版本
                         不支援 bedrock-runtime Converse 時才需要，見 D4）
  --auth-type TYPE       Function URL 認證：NONE | AWS_IAM（預設 NONE）
                         NONE 讓評審不需憑證即可開啟，但任何取得 URL 的人都能觸發執行
  --concurrency N        Lambda reserved concurrency 上限（預設 5，最大 10）
                         本帳號 unreserved 只有 110，AWS 要求保留至少 100，故上限為 10
  --log-retention DAYS   CloudWatch log 保留天數（預設 7）
  --dry-run              只打包，不呼叫任何 AWS API；無憑證也能執行
  -h, --help             顯示本說明

範例：
  bash aws/deploy.sh --dry-run
  AWS_PROFILE=hoyabit bash aws/deploy.sh
  bash aws/deploy.sh --profile hoyabit --region us-west-2 --model-id amazon.nova-lite-v1:0

注意：
  部署會建立公開的 Lambda Function URL（AuthType: NONE）。任何取得該 URL 的人
  都能觸發完整研究執行並消耗 Bedrock 配額。Demo 結束請執行：
    aws cloudformation delete-stack --region <REGION> --stack-name <STACK_NAME>
USAGE
}

# ----------------------------------------------------------------------------------
# 參數解析
# ----------------------------------------------------------------------------------

while [ $# -gt 0 ]; do
  case "$1" in
    --region)      REGION="${2:?--region 需要值}"; shift 2 ;;
    --stack-name)  STACK_NAME="${2:?--stack-name 需要值}"; shift 2 ;;
    --provider)    PROVIDER="${2:?--provider 需要值}"; shift 2 ;;
    --model-id)    MODEL_ID="${2:?--model-id 需要值}"; shift 2 ;;
    --secret-arn)  SECRET_ARN="${2:?--secret-arn 需要值}"; shift 2 ;;
    --profile)     export AWS_PROFILE="${2:?--profile 需要值}"; shift 2 ;;
    --bundle-sdk)  BUNDLE_SDK=1; shift ;;
    --auth-type)   AUTH_TYPE="${2:?--auth-type 需要值}"; shift 2 ;;
    --concurrency) CONCURRENCY="${2:?--concurrency 需要值}"; shift 2 ;;
    --log-retention) LOG_RETENTION="${2:?--log-retention 需要值}"; shift 2 ;;
    --dry-run)     DRY_RUN=1; shift ;;
    -h|--help)     usage; exit 0 ;;
    *)             printf '未知選項：%s\n\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

case "$AUTH_TYPE" in
  NONE|AWS_IAM) ;;
  *) printf '%s\n' "錯誤：--auth-type 必須是 NONE 或 AWS_IAM，收到：$AUTH_TYPE" >&2; exit 2 ;;
esac

case "$PROVIDER" in
  bedrock|gemini|openai|none) ;;
  # 格式字串刻意不以 "--" 開頭：printf 會把它當成自己的選項而報 invalid option。
  *) printf '%s\n' "錯誤：--provider 必須是 bedrock、gemini、openai 或 none，收到：$PROVIDER" >&2
     exit 2 ;;
esac

# bedrock 透過 Lambda execution role 認證，不讀 Secrets Manager。若同時給了 secret ARN
# 就是設定衝突，明講出來比默默忽略好。
if [ "$PROVIDER" = "bedrock" ] && [ -n "$SECRET_ARN" ]; then
  printf '警告：provider=bedrock 不使用 Secrets Manager，--secret-arn 將被忽略\n' >&2
  SECRET_ARN=""
fi

log()  { printf '%s\n' "$*"; }
step() { printf '\n[%s] %s\n' "$1" "$2"; }
die()  { printf '\n錯誤：%s\n' "$*" >&2; exit 1; }

# ----------------------------------------------------------------------------------
# 1. 打包
# ----------------------------------------------------------------------------------

step 1/6 "打包部署套件"

cd "$PROJECT_ROOT" || die "無法進入專案根目錄 $PROJECT_ROOT"
[ -f lambda_handler.py ] || die "找不到 lambda_handler.py，請確認在專案根目錄執行"
command -v python3 >/dev/null 2>&1 || die "找不到 python3"

rm -rf "$BUILD_ROOT"
mkdir -p "$STAGE_ROOT"

# 只複製 Lambda 執行時真正需要的東西。tests/、docs/、demo-fixtures/、outputs-*/、
# .kiro/ 與 .env 一律不進部署包：前者是體積，後者是憑證外洩風險。
#
# config/ 是必要的，不是可選的：src/credibility.py 的 DEFAULT_REGISTRY_PATH 指向
# config/source_registry.json，缺少它時 load_source_registry() 會靜默退回保守預設
# （所有 source_type 都變成 source_quality=0.35），因此高品質來源被大幅低估
# （blockchain_raw 0.90 -> 0.35）而 fallback fixture 反被高估（0.20 -> 0.35）。
# 這會經由 weighted_evidence_quality（佔 claim confidence 30%）傳導到最終信心分數，
# 使雲端與本機對同一批證據算出不同結果。
cp lambda_handler.py "$STAGE_ROOT/"
cp -R src "$STAGE_ROOT/"
[ -d data ] && cp -R data "$STAGE_ROOT/"
[ -d config ] && cp -R config "$STAGE_ROOT/"

# 打包前就確認關鍵設定檔在位，不要等部署後才從 execution_log 的
# registry_version=unavailable 發現——那是靜默降級，很容易被當成正常。
if [ -f config/source_registry.json ] && [ ! -f "$STAGE_ROOT/config/source_registry.json" ]; then
  die "config/source_registry.json 未進入部署包；credibility 計分會退回保守預設"
fi

find "$STAGE_ROOT" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null
find "$STAGE_ROOT" -name '*.pyc' -delete 2>/dev/null
find "$STAGE_ROOT" -name '.DS_Store' -delete 2>/dev/null
rm -f "$STAGE_ROOT/.env"

if [ "$BUNDLE_SDK" -eq 1 ]; then
  step 1b/6 "把 AWS SDK 打包進部署包"
  command -v pip3 >/dev/null 2>&1 || die "--bundle-sdk 需要 pip3"
  # 版本 pin 死，不使用開放範圍：Lambda 執行環境的行為必須可重現。
  pip3 install --quiet --target "$STAGE_ROOT" \
    "boto3==1.42.97" "botocore==1.42.97" \
    || die "boto3／botocore 安裝失敗"
  find "$STAGE_ROOT" -name '*.dist-info' -type d -prune -exec rm -rf {} + 2>/dev/null
  log "  已加入 pin 版 boto3／botocore"
fi

python3 - "$STAGE_ROOT" "$ZIP_PATH" <<'PY'
import os
import sys
import zipfile

stage, target = sys.argv[1], sys.argv[2]
count = 0
with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
    for base, dirs, files in os.walk(stage):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in sorted(files):
            if name.endswith((".pyc", ".pyo")):
                continue
            full = os.path.join(base, name)
            archive.write(full, os.path.relpath(full, stage))
            count += 1
print(f"  {count} 個檔案寫入 {os.path.basename(target)}")
PY
[ -f "$ZIP_PATH" ] || die "打包失敗，未產生 $ZIP_PATH"

ZIP_BYTES=$(wc -c < "$ZIP_PATH" | tr -d ' ')
log "  壓縮後大小：$((ZIP_BYTES / 1024)) KB"
log "  路徑：$ZIP_PATH"

# Lambda 直接上傳上限 50 MB、解壓後 250 MB。超過就必須改用 Layer（見 D4）。
if [ "$ZIP_BYTES" -gt 52428800 ]; then
  log "  警告：壓縮後超過 50 MB，接近 Lambda 限制；考慮改用 Lambda Layer"
fi

if [ "$DRY_RUN" -eq 1 ]; then
  step "完成" "dry-run 結束，未呼叫任何 AWS API"
  log "部署套件已就緒：$ZIP_PATH"
  exit 0
fi

# ----------------------------------------------------------------------------------
# 2. 憑證
# ----------------------------------------------------------------------------------

step 2/6 "確認 AWS 憑證"

command -v aws >/dev/null 2>&1 || die "找不到 aws CLI"

if ! ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text 2>&1); then
  printf '\n錯誤：AWS 憑證不可用。\n' >&2
  printf '%s\n' "$ACCOUNT_ID" >&2
  printf '\n部署套件已保留於 %s，設定憑證後可直接重跑本腳本。\n' "$ZIP_PATH" >&2
  printf 'Workshop Studio 環境請回活動頁面取 "Get AWS CLI credentials"（含 AWS_SESSION_TOKEN）。\n' >&2
  exit 1
fi
[ -n "$ACCOUNT_ID" ] || die "無法取得 Account ID"
log "  Account：$ACCOUNT_ID"
log "  Region： $REGION"

BUCKET="hoyabit-agent-deploy-${ACCOUNT_ID}-${REGION}"

# ----------------------------------------------------------------------------------
# 3. 部署用 S3 bucket
# ----------------------------------------------------------------------------------

step 3/6 "確認部署用 S3 bucket"

if aws s3api head-bucket --bucket "$BUCKET" --region "$REGION" >/dev/null 2>&1; then
  log "  已存在：$BUCKET"
else
  log "  建立：$BUCKET"
  # us-east-1 是特例：CreateBucket 不接受 LocationConstraint。
  if [ "$REGION" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" >/dev/null \
      || die "建立 bucket 失敗"
  else
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
      --create-bucket-configuration "LocationConstraint=$REGION" >/dev/null \
      || die "建立 bucket 失敗"
  fi
  # 部署產物不應公開可讀。失敗不中止：部分環境由帳號層級的 Block Public Access 統一控制。
  aws s3api put-public-access-block --bucket "$BUCKET" --region "$REGION" \
    --public-access-block-configuration \
    "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true" \
    >/dev/null 2>&1 || log "  提醒：無法設定 public access block，請確認帳號層級已封鎖公開存取"
fi

# ----------------------------------------------------------------------------------
# 4. 上傳
# ----------------------------------------------------------------------------------

step 4/6 "上傳部署套件"

CODE_KEY="releases/agent-$(date -u '+%Y%m%d%H%M%S').zip"
aws s3 cp "$ZIP_PATH" "s3://$BUCKET/$CODE_KEY" --region "$REGION" >/dev/null \
  || die "上傳失敗"
log "  s3://$BUCKET/$CODE_KEY"

# ----------------------------------------------------------------------------------
# 5. 部署 stack
# ----------------------------------------------------------------------------------

step 5/6 "部署 CloudFormation stack"
log "  stack：   $STACK_NAME"
log "  provider：$PROVIDER"
[ "$PROVIDER" = "bedrock" ] && log "  model：   $MODEL_ID"
log "  護欄：    auth=$AUTH_TYPE  concurrency=$CONCURRENCY  log 保留=${LOG_RETENTION} 天"
log "  commit：  $CODE_COMMIT"

DEPLOY_OUTPUT=$(aws cloudformation deploy \
  --template-file "$SCRIPT_DIR/template.yaml" \
  --stack-name "$STACK_NAME" \
  --region "$REGION" \
  --capabilities CAPABILITY_IAM \
  --no-fail-on-empty-changeset \
  --parameter-overrides \
    "CodeBucket=$BUCKET" \
    "CodeKey=$CODE_KEY" \
    "LLMSecretArn=$SECRET_ARN" \
    "LLMProvider=$PROVIDER" \
    "BedrockModelId=$MODEL_ID" \
    "FunctionUrlAuthType=$AUTH_TYPE" \
    "ReservedConcurrency=$CONCURRENCY" \
    "LogRetentionDays=$LOG_RETENTION" \
    "CodeCommit=$CODE_COMMIT" 2>&1)
DEPLOY_STATUS=$?

if [ "$DEPLOY_STATUS" -ne 0 ]; then
  printf '%s\n' "$DEPLOY_OUTPUT" >&2
  printf '\n部署失敗。診斷最早的失敗事件：\n' >&2
  printf '  aws cloudformation describe-stack-events --region %s --stack-name %s \\\n' "$REGION" "$STACK_NAME" >&2
  printf '    --query "reverse(StackEvents[?ResourceStatus==\x27CREATE_FAILED\x27])" --output table\n' >&2
  exit 1
fi
printf '%s\n' "$DEPLOY_OUTPUT" | sed 's/^/  /'

# ----------------------------------------------------------------------------------
# 6. 輸出 Function URL
# ----------------------------------------------------------------------------------

step 6/6 "取得 Function URL"

PUBLIC_URL=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='PublicUrl'].OutputValue" \
  --output text 2>/dev/null)

if [ -z "$PUBLIC_URL" ] || [ "$PUBLIC_URL" = "None" ]; then
  die "stack 已部署但取不到 PublicUrl，請檢查 describe-stacks 輸出"
fi

log ""
log "部署完成"
log "  Function URL：$PUBLIC_URL"
log ""
log "驗收（見 .kiro/specs/hoyabit-aws-deployment/tasks.md 的 D5）："
log "  curl -sS -o /dev/null -w 'GET %{http_code}\\n' '$PUBLIC_URL'"
log ""
log "這個 URL 沒有認證，任何人取得即可觸發執行並消耗 Bedrock 配額。"
log "Demo 結束請拆除："
log "  aws cloudformation delete-stack --region $REGION --stack-name $STACK_NAME"
