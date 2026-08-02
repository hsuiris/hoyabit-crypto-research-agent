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

# 可連入 Function URL 的來源 IP 白名單（逗號分隔，接受單一 IP 與 CIDR）。空字串＝不限制。
#
# 這個預設值必須與 aws/template.yaml 的 AllowedSourceIps 預設值逐字相同，否則「用本腳本部署」
# 與「直接用 CloudFormation 部署」會得到不同的白名單。
# tests/test_source_ip_allowlist.py 有一條靜態測試擋住這兩處分岔。
DEFAULT_ALLOWED_IPS="60.250.15.18,60.250.15.19,60.250.15.34,60.250.15.35,60.250.15.36,60.250.15.50,60.250.15.51,60.250.15.52"
ALLOW_MY_IP=0
# ALLOWED_IPS 的實際值在 load_env_file 之後才決定（見該處說明），因此這裡不設。

# Converse 的輸出上限。刻意用 DEPLOY_ 前綴而不是直接讀 BEDROCK_MAX_TOKENS：後者是 Lambda 的
# 執行期變數，本機 .env 裡的值不該決定雲端要部署什麼。預設 5000 對齊 amazon.nova-lite-v1:0
# 的文件輸出上限；換模型時要一併確認該模型的上限（見 template.yaml 的 BedrockMaxTokens）。
MAX_TOKENS="${DEPLOY_BEDROCK_MAX_TOKENS:-5000}"

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

# 白名單刻意在 load_env_file **之後**才解析，這樣 .env 的 DEPLOY_ALLOWED_SOURCE_IPS 才真的
#有效果（上方那些預設值在 .env 載入之前就已定案，是既有行為，本次不改動）。
#
# 用 ${VAR-default} 而不是 ${VAR:-default}：前者只在變數「未設定」時取預設，因此
# DEPLOY_ALLOWED_SOURCE_IPS=（明確設成空字串）代表「不限制任何來源」，而不是退回預設清單。
# 這個區別很重要——現場要臨時解除限制時，必須有一個明確、不會被預設值蓋掉的方式。
ALLOWED_IPS="${DEPLOY_ALLOWED_SOURCE_IPS-$DEFAULT_ALLOWED_IPS}"

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
  --max-tokens N         Converse 輸出上限（預設 5000，即 nova-lite 的文件上限）
                         撞到上限時 JSON 會被截斷，整份分析降級成離線推理
  --secret-arn ARN       Secrets Manager ARN，僅 gemini／openai 需要；bedrock 不使用
  --profile PROFILE      AWS CLI profile（等同 export AWS_PROFILE）
  --bundle-sdk           把 boto3／botocore 打包進部署包（僅在 Lambda 內建版本
                         不支援 bedrock-runtime Converse 時才需要，見 D4）
  --auth-type TYPE       Function URL 認證：NONE | AWS_IAM（預設 NONE）
                         NONE 讓評審不需憑證即可開啟，但任何取得 URL 的人都能觸發執行
  --allowed-ips LIST     可連入的來源 IP 白名單，逗號分隔，接受單一 IP 與 CIDR
                         （預設為競賽現場的 8 個位址；傳空字串 '' 代表不限制任何來源）
                         非白名單來源會拿到 403，不會觸發研究執行也不消耗 Bedrock 配額
  --allow-my-ip          把「本機當下的公開 IP」追加到白名單（現場最快的自救方式）
  --concurrency N        Lambda reserved concurrency 上限（預設 5，最大 10）
                         本帳號 unreserved 只有 110，AWS 要求保留至少 100，故上限為 10
  --log-retention DAYS   CloudWatch log 保留天數（預設 7）
  --dry-run              只打包，不呼叫任何 AWS API；無憑證也能執行
  -h, --help             顯示本說明

範例：
  bash aws/deploy.sh --dry-run
  AWS_PROFILE=hoyabit bash aws/deploy.sh
  bash aws/deploy.sh --profile hoyabit --region us-west-2 --model-id amazon.nova-lite-v1:0
  bash aws/deploy.sh --allow-my-ip                       # 現場白名單 + 自己這條網路
  bash aws/deploy.sh --allowed-ips '60.250.15.0/24'       # 用整個網段
  bash aws/deploy.sh --allowed-ips ''                     # 解除來源限制（回到全開放）

注意：
  Function URL 的 AuthType 是 NONE，AWS 這一層沒有任何認證。來源限制由
  lambda_handler.py 在應用層比對 requestContext.http.sourceIp 完成（Function URL
  不能掛 WAF，resource policy 也沒有 IP 條件鍵）。因此非白名單來源仍會叫用一次
  Lambda，但在觸發研究執行之前就會拿到 403。

  白名單預設會擋掉清單外的所有位址，**包含部署者自己**。本腳本會比對你當下的公開 IP
  並在不在清單內時警告。Demo 結束請執行：
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
    --max-tokens)  MAX_TOKENS="${2:?--max-tokens 需要值}"; shift 2 ;;
    --secret-arn)  SECRET_ARN="${2:?--secret-arn 需要值}"; shift 2 ;;
    --profile)     export AWS_PROFILE="${2:?--profile 需要值}"; shift 2 ;;
    --bundle-sdk)  BUNDLE_SDK=1; shift ;;
    --auth-type)   AUTH_TYPE="${2:?--auth-type 需要值}"; shift 2 ;;
    # 刻意用 ${2?...} 而不是 ${2:?...}：白名單允許明確傳入空字串代表「不限制」。
    --allowed-ips) ALLOWED_IPS="${2?--allowed-ips 需要值（傳 '' 代表不限制）}"; shift 2 ;;
    --allow-my-ip) ALLOW_MY_IP=1; shift ;;
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

# 在打包前就攔下非數字，而不是讓 CloudFormation 在部署到一半才拒絕。
case "$MAX_TOKENS" in
  ''|*[!0-9]*) printf '%s\n' "錯誤：--max-tokens 必須是正整數，收到：$MAX_TOKENS" >&2; exit 2 ;;
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

# 白名單格式在部署前就驗證，不要等到部署完才從 403 發現打錯字。這一步不呼叫網路，
# 因此 --dry-run 也會執行。空字串代表不限制，直接跳過。
#
# 為什麼要「先驗證再送出」：src/ip_allowlist.py 對「有設定但全部無效」是一律拒絕
# （fail closed）。那是誠實的執行期行為，但如果讓打錯的清單真的部署上去，結果會是
# 一個對所有人回 403 的網站。在這裡攔下來，就不會走到那條路。
if [ -n "$ALLOWED_IPS" ]; then
  if ! command -v python3 >/dev/null 2>&1; then
    printf '%s\n' "錯誤：驗證 --allowed-ips 需要 python3" >&2; exit 2
  fi
  ALLOWED_IPS="$(PYTHONPATH="$PROJECT_ROOT" python3 - "$ALLOWED_IPS" <<'PY'
import sys

from src.ip_allowlist import format_entries, validate_entries

raw = sys.argv[1]
invalid = validate_entries([raw])
if invalid:
    sys.stderr.write("錯誤：--allowed-ips 有無法解析的項目：%s\n" % ", ".join(invalid))
    sys.stderr.write("      每一項必須是單一 IP（60.250.15.18）或 CIDR 網段（60.250.15.0/24）。\n")
    raise SystemExit(2)
print(format_entries([raw]))
PY
  )" || exit 2
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
# 4b. 來源 IP 白名單與部署者自己的位址
# ----------------------------------------------------------------------------------
#
# 這一段的存在理由只有一個：**避免把自己鎖在外面**。白名單預設是競賽現場的位址，若部署者
# 當下不在那條網路上，部署完成後連自己都打不開 Function URL —— 而那個 403 看起來與
# 「部署失敗」一模一樣。與其事後除錯，不如部署前就講清楚。
#
# 位址查詢是 best-effort：失敗只警告不中止（沒有網路資訊時，唯一誠實的說法是「無法確認」）。
# 刻意放在憑證確認之後，因此 --dry-run 不會走到這裡，那個模式的「不呼叫任何外部服務」維持成立。

MY_IP=""
if command -v curl >/dev/null 2>&1; then
  MY_IP="$(curl -sS --max-time 5 https://checkip.amazonaws.com 2>/dev/null | tr -d '[:space:]')"
fi

if [ "$ALLOW_MY_IP" -eq 1 ]; then
  if [ -n "$MY_IP" ]; then
    if [ -n "$ALLOWED_IPS" ]; then
      ALLOWED_IPS="$ALLOWED_IPS,$MY_IP"
    else
      # 原本不限制，卻要求「只加自己」——那會把限制從無變成有，是使用者不會預期的收緊。
      printf '警告：--allowed-ips 為空（不限制），--allow-my-ip 因此不套用\n' >&2
    fi
  else
    printf '警告：查不到本機公開 IP，--allow-my-ip 未套用\n' >&2
  fi
fi

if [ -n "$ALLOWED_IPS" ]; then
  ALLOWED_IPS="$(PYTHONPATH="$PROJECT_ROOT" python3 - "$ALLOWED_IPS" <<'PY'
import sys

from src.ip_allowlist import format_entries

print(format_entries([sys.argv[1]]))
PY
  )"
  COVERAGE="$(PYTHONPATH="$PROJECT_ROOT" python3 - "$ALLOWED_IPS" "$MY_IP" <<'PY'
import sys

from src.ip_allowlist import parse_allowlist

allowlist = parse_allowlist(sys.argv[1])
source_ip = sys.argv[2] if len(sys.argv) > 2 else ""
if not source_ip:
    print("unknown")
else:
    print("covered" if allowlist.allows(source_ip) else "uncovered")
PY
  )"
  step 4b/6 "來源 IP 白名單"
  log "  白名單： $ALLOWED_IPS"
  case "$COVERAGE" in
    covered)
      log "  本機 IP：${MY_IP}（在白名單內）" ;;
    uncovered)
      log "  本機 IP：${MY_IP}"
      printf '\n警告：你當下的公開 IP 不在白名單內。\n' >&2
      printf '      部署後你自己也會拿到 HTTP 403，./aws/verify-deployment.sh 會失敗。\n' >&2
      printf '      要保留自己的存取權，改用：\n' >&2
      printf '        bash aws/deploy.sh --allow-my-ip\n' >&2
      printf '      或完全解除限制：\n' >&2
      printf "        bash aws/deploy.sh --allowed-ips ''\n\n" >&2 ;;
    *)
      printf '警告：查不到本機公開 IP，無法確認你是否在白名單內\n' >&2 ;;
  esac
else
  step 4b/6 "來源 IP 白名單"
  log "  未設定：任何取得 Function URL 的來源都能連入"
fi

# ----------------------------------------------------------------------------------
# 5. 部署 stack
# ----------------------------------------------------------------------------------

step 5/6 "部署 CloudFormation stack"
log "  stack：   $STACK_NAME"
log "  provider：$PROVIDER"
# 變數一律用大括號：全形括號等非 ASCII 字元緊接在 `$VAR` 後面時，bash 會把它算進變數名，
# 於是在 `set -u` 下變成未綁定變數並中止腳本。`bash -n` 抓不到（這是執行期錯誤），
# `--dry-run` 也抓不到（它在步驟 1 就結束，走不到這裡）。
[ "$PROVIDER" = "bedrock" ] && log "  model：   ${MODEL_ID}（輸出上限 ${MAX_TOKENS} tokens）"
log "  護欄：    auth=$AUTH_TYPE  concurrency=$CONCURRENCY  log 保留=${LOG_RETENTION} 天"
if [ -n "$ALLOWED_IPS" ]; then
  log "  來源限制：${ALLOWED_IPS}"
else
  log "  來源限制：無（任何取得網址的來源都能連入）"
fi
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
    "BedrockMaxTokens=$MAX_TOKENS" \
    "FunctionUrlAuthType=$AUTH_TYPE" \
    "AllowedSourceIps=$ALLOWED_IPS" \
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
if [ -n "$ALLOWED_IPS" ]; then
  log "來源限制已生效：只有白名單內的位址會拿到 200，其餘一律 403。"
  log "  白名單：${ALLOWED_IPS}"
  log "  白名單外的來源仍會叫用一次 Lambda，但在觸發研究執行之前就被擋下（不消耗 Bedrock 配額）。"
  log "  要加位址就重跑本腳本並帶 --allowed-ips 或 --allow-my-ip；不需要改任何程式碼。"
else
  log "這個 URL 沒有認證也沒有來源限制，任何人取得即可觸發執行並消耗 Bedrock 配額。"
fi
log ""
log "Demo 結束請拆除："
log "  aws cloudformation delete-stack --region $REGION --stack-name $STACK_NAME"
