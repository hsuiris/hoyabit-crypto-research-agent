#!/usr/bin/env bash
#
# 安裝 git hooks。
#
# 為什麼需要手動安裝：`.git/hooks/` 不進版控，因此 clone 下來不會自動有 hook。
# 這個腳本讓每個開發環境都能一行裝好。
#
# 用法：
#   bash scripts/install-hooks.sh          # 安裝
#   bash scripts/install-hooks.sh --remove # 移除
#
# 安裝的 hook：
#   pre-commit  在 commit 前執行 scripts/check_secrets.sh --staged，
#               發現敏感資料就阻止 commit。

set -uo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" || exit 1

HOOK_PATH=".git/hooks/pre-commit"

if [ ! -d .git ]; then
  printf '錯誤：這裡不是 git repository 的根目錄。\n' >&2
  exit 1
fi

if [ "${1:-}" = "--remove" ]; then
  if [ -f "$HOOK_PATH" ]; then
    rm -f "$HOOK_PATH"
    printf '已移除 %s\n' "$HOOK_PATH"
  else
    printf '沒有安裝的 pre-commit hook。\n'
  fi
  exit 0
fi

if [ -f "$HOOK_PATH" ] && ! grep -q 'check_secrets.sh' "$HOOK_PATH" 2>/dev/null; then
  printf '警告：%s 已存在且不是本專案安裝的，先備份為 %s.bak\n' "$HOOK_PATH" "$HOOK_PATH"
  cp "$HOOK_PATH" "$HOOK_PATH.bak"
fi

cat > "$HOOK_PATH" <<'HOOK'
#!/usr/bin/env bash
# 由 scripts/install-hooks.sh 安裝。移除：bash scripts/install-hooks.sh --remove
#
# 檢查即將 commit 的內容有沒有敏感資料。這個 repository 是公開的，而實際發生過
# AWS 帳號 ID 與 Function URL 被寫進文件的洩漏，因此需要機器來擋。
set -uo pipefail

if [ ! -x scripts/check_secrets.sh ]; then
  exit 0
fi

if bash scripts/check_secrets.sh --staged; then
  exit 0
fi

cat >&2 <<'MSG'

commit 已被阻止：暫存的內容含敏感資料。

處理方式：
  1. 把實際值改成佔位符（<ACCOUNT_ID>、<FUNCTION_URL_HOST>），實際值只留在 .env
  2. 重新 git add 後再 commit

確認是誤判時可略過本檢查：
  git commit --no-verify
MSG
exit 1
HOOK

chmod +x "$HOOK_PATH"
printf '已安裝 %s\n' "$HOOK_PATH"
printf '\n驗證：\n'
printf '  bash scripts/check_secrets.sh --staged\n'
printf '\n移除：\n'
printf '  bash scripts/install-hooks.sh --remove\n'
