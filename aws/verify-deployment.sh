#!/usr/bin/env bash
# Demo 前確認 Function URL 的實際狀態。
#
# 為什麼需要這支腳本：`aws/README.md` 記錄的部署事實是某次部署當下的快照，而 stack 跑在
# AWS Workshop Studio 的臨時帳號上，憑證與資源都會過期。「文件說已部署」和「現在打得通」
# 是兩件事，上台前必須用實際請求確認，不能靠文件。
#
# 不需要 AWS 憑證：Function URL 的 AuthType 是 NONE，這支腳本只發 HTTP 請求。
# 憑證已過期時仍可用來判斷線上服務是否還活著。
#
#   ./aws/verify-deployment.sh          # 只檢查首頁（不消耗 Bedrock 配額）
#   ./aws/verify-deployment.sh --run    # 額外跑一次 test 模式分析（會消耗 Bedrock 配額）
#
# 公開端點是 test-only demo（E1）：首頁不再提供 formal 選項，且 `lambda_handler.py` 會在
# 建立任何 run record 之前擋下 mode=formal 與 authorized_rerun／rerun 類請求（回 403）。
# 這支腳本永遠只送 mode=test，也不會嘗試送 formal 去驗證 403（那屬於 targeted tests
# 的職責，見 tests/test_final_release_guardrails.py），避免對公開端點做非必要的探測。

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

RUN_ANALYSIS=0
[ "${1:-}" = "--run" ] && RUN_ANALYSIS=1

pass() { printf '  \033[32mPASS\033[0m  %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; FAILED=1; }
warn() { printf '  \033[33mWARN\033[0m  %s\n' "$1"; }
info() { printf '        %s\n' "$1"; }
FAILED=0

# ----------------------------------------------------------------------------------
# 取得 Function URL
# ----------------------------------------------------------------------------------
# 與 deploy.sh 相同的做法：只做 KEY=value 字面解析，不 source .env（那會執行檔案內容）。
URL="${DEPLOY_FUNCTION_URL:-}"
if [ -z "$URL" ] && [ -f "$PROJECT_ROOT/.env" ]; then
  URL=$(grep -E '^DEPLOY_FUNCTION_URL=' "$PROJECT_ROOT/.env" | tail -1 | cut -d= -f2- | tr -d '"'"'"' \r')
fi

printf '\nHoyaBIT 部署驗證\n\n'

if [ -z "$URL" ]; then
  fail "找不到 DEPLOY_FUNCTION_URL（先設環境變數或寫進 .env）"
  printf '\n部署後可從 stack 取回：\n'
  printf '  aws cloudformation describe-stacks --region "$AWS_REGION" \\\n'
  printf '    --stack-name "$DEPLOY_STACK_NAME" \\\n'
  printf "    --query \"Stacks[0].Outputs[?OutputKey=='PublicUrl'].OutputValue\" --output text\n\n"
  exit 1
fi

# URL 含帳號專屬的 Function URL ID，只顯示遮蔽後的形式，避免貼在螢幕或錄影裡。
info "端點：$(printf '%s' "$URL" | sed -E 's#https://[a-z0-9]{10,}#https://<FUNCTION_URL_ID>#')"
printf '\n'

# ----------------------------------------------------------------------------------
# 1. 首頁可達
# ----------------------------------------------------------------------------------
printf '1. 首頁\n'
BODY=$(mktemp) || exit 1
trap 'rm -f "$BODY"' EXIT

HTTP_CODE=$(curl -sS -o "$BODY" -w '%{http_code}' --max-time 20 "$URL" 2>/dev/null)
ELAPSED=$(curl -sS -o /dev/null -w '%{time_total}' --max-time 20 "$URL" 2>/dev/null)

if [ "$HTTP_CODE" = "200" ]; then
  pass "GET / → HTTP 200（${ELAPSED}s）"
elif [ "$HTTP_CODE" = "403" ] && grep -q '來源位址未經授權' "$BODY" 2>/dev/null; then
  # 這個 403 不是部署失敗，是來源 IP 白名單正在生效。沒有這個分支的話，畫面上只會看到
  # 「GET / → HTTP 403」並建議重新部署 —— 而重新部署不會改變任何事情，白名單本來就在擋。
  fail "GET / → HTTP 403：這台機器的來源 IP 不在白名單內"
  info "服務本身是活著的（403 由應用層的白名單守衛回應，不是 AWS 認證）"
  printf '\n偵測到的來源位址（由服務回報）：\n'
  sed -n 's/.*<code>\(.*\)<\/code>.*/  \1/p' "$BODY" | head -1
  printf '\n處理方式（任一）：\n'
  printf '  1. 在白名單內的網路上執行本腳本\n'
  printf '  2. 把這台機器的 IP 加進白名單並重新部署：\n'
  printf '       bash aws/deploy.sh --allow-my-ip\n'
  printf '  3. 暫時解除來源限制：\n'
  printf "       bash aws/deploy.sh --allowed-ips ''\n\n"
  exit 1
else
  fail "GET / → HTTP ${HTTP_CODE:-無回應}"
  printf '\nstack 可能已被拆除或帳號已過期。重新部署：\n  ./aws/deploy.sh\n\n'
  exit 1
fi

grep -q 'HOYA BIT' "$BODY" && pass "回應內容是 HOYA BIT 首頁" \
  || fail "HTTP 200 但內容不是預期的首頁（可能被其他服務佔用同一網址）"

# 幣種池：命題要求的輸入面，缺了就不是可用的 Demo。
MISSING_COINS=""
for coin in BTC ETH SOL BNB XRP; do
  grep -q ">$coin<" "$BODY" || MISSING_COINS="$MISSING_COINS $coin"
done
[ -z "$MISSING_COINS" ] && pass "五個幣種都可選（BTC／ETH／SOL／BNB／XRP）" \
  || fail "首頁缺少幣種：$MISSING_COINS"

# test-only 守衛（E1）：公開端點（AuthType NONE）只能執行 test mode。
# 這裡刻意不再把「有 mode selector」當成 PASS —— selector 本身不代表安全，
# 真正的判準是「formal 選項不存在」且「頁面明確標示 test-only」。
if grep -qE "value=['\"]formal['\"]" "$BODY" || grep -q '>Formal<' "$BODY"; then
  fail "首頁仍可選擇 formal（公開展示必須是 test-only，見 E1）"
else
  pass "首頁未提供 formal 選項（test-only）"
fi

grep -q 'test-only' "$BODY" && pass "首頁明確標示 test-only demo" \
  || warn "首頁沒有找到 test-only 字樣，措辭可能已變更（非阻斷）"

grep -qE "name=['\"]mode['\"][^>]*value=['\"]test['\"]" "$BODY" \
  && pass "固定送出 mode=test（hidden field）" \
  || warn "找不到固定的 mode=test hidden field，測試表單送出的 mode 值時請留意"

# ----------------------------------------------------------------------------------
# 2. 線上程式版本
# ----------------------------------------------------------------------------------
printf '\n2. 線上程式版本\n'
LOCAL_COMMIT=$(git -C "$PROJECT_ROOT" rev-parse HEAD 2>/dev/null || echo unknown)
info "本機 HEAD：${LOCAL_COMMIT}"

if [ "$RUN_ANALYSIS" -eq 0 ]; then
  warn "未執行分析，無法讀出線上 commit（加 --run 才會取得 manifest）"
  info "線上 commit 由 deploy.sh 注入 CODE_COMMIT，顯示在 manifest.json 的 code_commit"
fi

# ----------------------------------------------------------------------------------
# 3. 完整分析（選用）
# ----------------------------------------------------------------------------------
if [ "$RUN_ANALYSIS" -eq 1 ]; then
  printf '\n3. 完整分析（mode=test，會消耗 Bedrock 配額）\n'
  RESULT=$(mktemp) || exit 1
  trap 'rm -f "$BODY" "$RESULT"' EXIT

  START=$(date +%s)
  RUN_CODE=$(curl -sS -o "$RESULT" -w '%{http_code}' --max-time 900 \
    --data-urlencode 'coin=ETH' \
    --data-urlencode 'question=分析 ETH 近兩週市場狀況、主要驅動因素與下行風險' \
    --data-urlencode 'mode=test' \
    "$URL" 2>/dev/null)
  DURATION=$(( $(date +%s) - START ))

  if [ "$RUN_CODE" = "200" ]; then
    pass "POST → HTTP 200（${DURATION}s）"
  else
    fail "POST → HTTP ${RUN_CODE:-無回應}（${DURATION}s）"
  fi

  # 命題上限是 15 分鐘（900 秒）。
  if [ "$DURATION" -le 900 ]; then
    pass "在命題的 900 秒上限內完成"
  else
    fail "超過命題的 900 秒上限（${DURATION}s）"
  fi

  # 六項提交物、Citation Gate 與線上 commit 都能從回應內容判斷。
  MISSING_ARTIFACTS=""
  for artifact in report.md evidence.json execution_log.json research_plan.json claims.json manifest.json; do
    grep -q "$artifact" "$RESULT" || MISSING_ARTIFACTS="$MISSING_ARTIFACTS $artifact"
  done
  [ -z "$MISSING_ARTIFACTS" ] && pass "六項提交物都出現在回應中" \
    || fail "回應缺少提交物：$MISSING_ARTIFACTS"

  if grep -q '"citation_gate_status": "FAIL"' "$RESULT" || grep -q '結果：\*\*FAIL' "$RESULT"; then
    fail "Citation Gate FAIL"
  elif grep -q 'PASS_WITH_WARNINGS' "$RESULT"; then
    pass "Citation Gate PASS_WITH_WARNINGS"
  elif grep -q 'citation_gate' "$RESULT"; then
    pass "Citation Gate PASS"
  else
    warn "回應中找不到 Citation Gate 結果"
  fi

  DEPLOYED_COMMIT=$(grep -o '"code_commit": *"[^"]*"' "$RESULT" | head -1 | cut -d'"' -f4)
  if [ -z "$DEPLOYED_COMMIT" ] || [ "$DEPLOYED_COMMIT" = "unknown" ]; then
    warn "線上 code_commit 是 unknown：這個部署早於 CODE_COMMIT 注入，無法確認版本"
    info "重新部署即可讓版本可驗證：./aws/deploy.sh"
  elif [ "${DEPLOYED_COMMIT%-dirty}" = "$LOCAL_COMMIT" ]; then
    pass "線上程式與本機 HEAD 相同（${DEPLOYED_COMMIT}）"
  else
    fail "線上程式落後或分岔：線上 ${DEPLOYED_COMMIT}，本機 ${LOCAL_COMMIT}"
    info "本機的修正尚未上線。重新部署：./aws/deploy.sh"
  fi

  # 降級是誠實標示而非錯誤，但上台前要知道哪些來源取不到。
  if grep -q 'COMPLETED_DEGRADED' "$RESULT"; then
    warn "run 狀態為 COMPLETED_DEGRADED（部分來源降級，屬預期，見 aws/README.md 的 region 限制）"
  fi
fi

# ----------------------------------------------------------------------------------
# 摘要
# ----------------------------------------------------------------------------------
printf '\n'
if [ "$FAILED" -eq 0 ]; then
  printf '結果：可用\n'
else
  printf '結果：有項目未通過，見上方 FAIL\n'
fi
printf '\n提醒：這個 URL 沒有認證，任何取得的人都能觸發執行並消耗 Bedrock 配額。\n'
printf 'Demo 結束請拆除：aws cloudformation delete-stack --region "$AWS_REGION" --stack-name "$DEPLOY_STACK_NAME"\n\n'

exit "$FAILED"
