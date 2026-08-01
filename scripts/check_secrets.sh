#!/usr/bin/env bash
#
# 敏感資料外洩檢查。push 之前跑，或掛成 pre-commit hook。
#
# 為什麼需要這個：.gitignore 只保護它列出的檔案，管不到文件內容。實際發生過的洩漏是
# AWS 帳號 ID 與 Function URL 被寫進 status.yaml、design.md、aws/README.md、
# docs/aws-architecture.md —— 那些檔案本來就該進版控，問題在內容。人（和 AI）都會忘記，
# 所以需要機器來擋。
#
# 兩種偵測方式：
#   1. 精確比對：讀 .env 的實際值（AWS_ACCOUNT_ID、DEPLOY_FUNCTION_URL）去搜。
#      準確、不誤判、不漏。.env 沒填時自動跳過這部分。
#   2. 樣式比對：access key、私鑰、Function URL 格式等。就算 .env 沒填也能擋。
#
# 用法：
#   bash scripts/check_secrets.sh            # 檢查工作區（追蹤中的檔案）
#   bash scripts/check_secrets.sh --staged   # 只檢查已 staged 的內容（適合 pre-commit）
#   bash scripts/check_secrets.sh --history  # 連 git 歷史一起掃（較慢）
#
# 退出碼：0 乾淨；1 發現疑慮。

set -uo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" || exit 1

MODE="worktree"
case "${1:-}" in
  --staged)  MODE="staged" ;;
  --history) MODE="history" ;;
  -h|--help)
    sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'
    exit 0 ;;
  "") ;;
  *) printf '%s\n' "未知選項：$1（試 --help）" >&2; exit 2 ;;
esac

FINDINGS=0

report() {
  printf '  發現  %s\n' "$1"
  FINDINGS=$((FINDINGS + 1))
}

# ----------------------------------------------------------------------------------
# 從 .env 讀實際值（若有）
# ----------------------------------------------------------------------------------

# 只讀需要的兩個鍵，不 source 檔案。
env_value() {
  [ -f .env ] || return 0
  awk -F= -v want="$1" '
    /^[[:space:]]*#/ { next }
    $1 == want {
      sub(/^[^=]*=/, "", $0)
      gsub(/^["'"'"']|["'"'"']$/, "", $0)
      gsub(/\r/, "", $0)
      print
      exit
    }' .env
}

ACCOUNT_ID="$(env_value AWS_ACCOUNT_ID)"
FUNCTION_URL="$(env_value DEPLOY_FUNCTION_URL)"

# 從完整 URL 取出主機名，避免因為結尾斜線或協定差異而漏掉。
FUNCTION_HOST=""
if [ -n "$FUNCTION_URL" ]; then
  FUNCTION_HOST="$(printf '%s' "$FUNCTION_URL" | sed -e 's#^https\{0,1\}://##' -e 's#/.*$##')"
fi

# ----------------------------------------------------------------------------------
# 決定搜尋目標
# ----------------------------------------------------------------------------------

search() {
  local pattern="$1"
  case "$MODE" in
    staged)   git diff --cached -U0 | grep -nEi -- "$pattern" 2>/dev/null ;;
    history)  git log -p --all 2>/dev/null | grep -nEi -- "$pattern" 2>/dev/null ;;
    worktree) git grep -nEI -- "$pattern" 2>/dev/null ;;
  esac
}

printf 'HoyaBIT 敏感資料檢查（模式：%s）\n' "$MODE"

# ----------------------------------------------------------------------------------
# 1. 精確比對 .env 的實際值
# ----------------------------------------------------------------------------------

printf '\n== 1. 精確比對（來自 .env 的實際值）==\n'

if [ -z "$ACCOUNT_ID" ] && [ -z "$FUNCTION_HOST" ]; then
  printf '  跳過：.env 不存在或 AWS_ACCOUNT_ID／DEPLOY_FUNCTION_URL 未填。\n'
  printf '        填入後本項才能精確比對；目前僅依賴下方的樣式比對。\n'
else
  if [ -n "$ACCOUNT_ID" ]; then
    hits="$(search "$ACCOUNT_ID")"
    if [ -n "$hits" ]; then
      report "AWS 帳號 ID 出現在："
      printf '%s\n' "$hits" | head -20 | sed 's/^/          /'
    else
      printf '  乾淨  AWS 帳號 ID\n'
    fi
  fi
  if [ -n "$FUNCTION_HOST" ]; then
    hits="$(search "$FUNCTION_HOST")"
    if [ -n "$hits" ]; then
      report "Function URL 出現在："
      printf '%s\n' "$hits" | head -20 | sed 's/^/          /'
    else
      printf '  乾淨  Function URL\n'
    fi
  fi
fi

# ----------------------------------------------------------------------------------
# 2. 樣式比對
# ----------------------------------------------------------------------------------

printf '\n== 2. 樣式比對 ==\n'

check_pattern() {
  local label="$1" pattern="$2"
  local hits
  hits="$(search "$pattern")"
  # .env.example 的說明文字與本腳本自身會提到這些樣式，不算洩漏。
  hits="$(printf '%s' "$hits" | grep -v '^\.env\.example[:.]' | grep -v '^scripts/check_secrets\.sh[:.]' || true)"
  if [ -n "$hits" ]; then
    report "$label"
    printf '%s\n' "$hits" | head -10 | sed 's/^/          /'
  else
    printf '  乾淨  %s\n' "$label"
  fi
}

check_pattern "AWS access key（AKIA/ASIA）"      'A(KIA|SIA)[0-9A-Z]{16}'
check_pattern "私鑰"                             'BEGIN [A-Z ]*PRIVATE KEY'
check_pattern "aws_secret_access_key 帶值"       'aws_secret_access_key[[:space:]]*=[[:space:]]*[A-Za-z0-9/+]{20}'
check_pattern "aws_session_token 帶值"           'aws_session_token[[:space:]]*=[[:space:]]*[A-Za-z0-9/+=]{20}'
check_pattern "Lambda Function URL 格式"         '[a-z0-9]{20,}\.lambda-url\.[a-z0-9-]+\.on\.aws'
check_pattern "Bedrock API key（ABSK）"          'ABSK[A-Za-z0-9+/=]{20}'
check_pattern "arn 中的 12 位帳號 ID"            'arn:aws[a-z-]*:[a-z0-9-]*:[a-z0-9-]*:[0-9]{12}:'

# ----------------------------------------------------------------------------------
# 3. .env 本身不得進版控
# ----------------------------------------------------------------------------------

printf '\n== 3. .env 保護 ==\n'

if git ls-files --error-unmatch .env >/dev/null 2>&1; then
  report ".env 已被 git 追蹤（應只存在於本機）"
else
  printf '  乾淨  .env 未被追蹤\n'
fi

if git diff --cached --name-only 2>/dev/null | grep -qx '.env'; then
  report ".env 出現在暫存區，即將被 commit"
else
  printf '  乾淨  .env 不在暫存區\n'
fi

if grep -qE '^\.env$' .gitignore 2>/dev/null; then
  printf '  乾淨  .gitignore 已排除 .env\n'
else
  report ".gitignore 未排除 .env"
fi

# ----------------------------------------------------------------------------------
# 結果
# ----------------------------------------------------------------------------------

printf '\n'
if [ "$FINDINGS" -eq 0 ]; then
  printf '通過：未發現敏感資料。\n'
  exit 0
fi

printf '發現 %d 類疑慮，請在 push 之前處理。\n' "$FINDINGS"
printf '\n處理方式：\n'
printf '  1. 工作區檔案：把實際值改成佔位符（<ACCOUNT_ID>、<FUNCTION_URL_HOST>）。\n'
printf '  2. 已 commit 但未 push：在獨立分支上用 filter-branch 清理歷史，\n'
printf '     tree-filter 清檔案內容，msg-filter 清 commit message，兩者都要。\n'
printf '  3. 已 push 到公開 repo：視為已洩漏。Function URL 應立即 delete-stack 換掉，\n'
printf '     憑證應立即撤銷輪替，光是改 git 歷史不夠。\n'
exit 1
