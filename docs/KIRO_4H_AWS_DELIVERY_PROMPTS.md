# HoyaBIT：4 小時 AWS 決賽交付提示詞

> 適用情境：距離截止不到 4 小時。今天的完成定義是「系統可部署、雲端可操作、三份報告可閱讀、逐論點可溯源、證據可下載」，不是把所有架構技術債一次補完。
>
> 已稽核的分支狀態（2026-08-02）：本機 `GoToAWS0801` 是 `4ba7c0a`，`origin/GoToAWS0801` 是 `84079f5`；兩者分叉，不能直接 `pull`、`merge`、`rebase` 或 `reset`。遠端版本含 T8.1–T8.6，且曾有 683/683 測試通過紀錄。

## 今天的取捨

必做順序：`E0 → E1 → E2 → E3 → E4`。目前的產品核心不是擴充網站數，而是把既有資料做成「問題相關性與影響可解釋、三項報告可在雲端閱讀、每個 Claim 可點回來源」的閉環。

| Task | 時間上限 | 優先級 | 模型 | Effort |
|---|---:|---|---|---|
| E0 安全建立 release 基準 | 15 分 | 必做 | Claude Sonnet 4.6 | High |
| E1 公開 Lambda 強制 test-only | 20 分 | 必做 | Claude Sonnet 4.6 | Max |
| E2 問題相關性／影響／權重 | 40–50 分 | 必做 | Claude Opus 4.8 | Max |
| E3 雲端三報告與 Claim 溯源 UI | 35–45 分 | 必做 | Claude Sonnet 4.6 | Max |
| E4 AWS 部署與一次 live smoke | 70–90 分 | 必做 | Claude Opus 4.8 | Max |

建議時鐘：

```text
00:00–00:15  E0
00:15–00:35  E1
00:35–01:25  E2
01:25–02:10  E3
02:10–03:40  E4（含 CloudFormation、唯一 smoke、雲端 UI 與 ZIP 驗收）
03:40–04:00  只修 release blocker、重驗雲端，不加新功能
```

目前不是「只有一個網站」：最新程式已有 6 個 domain agents 以 `ThreadPoolExecutor` 平行執行 13 個固定 collectors（市場、新聞、總經／官方、三個社群、技術／衍生品、鏈上）。真正缺口是 Planner 沒有驅動這些 collectors，且 `claim_relevance` 從未按 question 填值。今天不做無界全網搜尋；搜尋網站多不等於證據獨立或有說服力，同源轉載與無關內容只會灌水。

今天明確不做：通用全網搜尋 API、自主 Agent 無限迴圈、第二輪 counter-query、S3 artifacts、DynamoDB durable formal lock、Lambda 雙幣比較、完整 raw quarantine/metrics、OI/basis/liquidation、進階 on-chain。這些一律在系統報告中標成限制或下一步。若未來要加 web discovery，必須是 question-driven、domain-bounded、來源 allowlist、deadline/max-results/cost cap 與 robots/SSRF 防護齊全，且仍部署在 Lambda 上。

語言硬契約：所有使用者可見內容、報告、頁面標籤、錯誤訊息與說明以臺灣繁體中文（`zh-Hant-TW`）為主；英文只作括號補充。JSON key、enum、Evidence/Claim ID、幣種、模型名、工具名與原始來源標題/作者可保留英文以維持契約與溯源。不得把原始英文來源偷偷改寫成中文原文；需要中文時另列「繁中摘要」。不得輸出簡體中文。

每個 Task 開一個新的 Kiro 對話，只貼一段。不要同時讓兩個 Kiro 對話修改同一個 worktree。

---

## E0 — 從正確遠端版本建立安全 release 分支

模型：**Claude Sonnet 4.6**

推理強度：**High**

硬停損：**15 分鐘**

```text
只執行 Task E0，完成後停止；不要開始 E1。

TASK_ID: E0
OBJECTIVE: 保留本機分叉的 GoToAWS0801，不合併、不重寫歷史；以 origin/GoToAWS0801 最新提交建立乾淨的 release/final-4h 分支，驗證基準可測試、可封裝。
TARGET_COMMIT: docs(E0): add four-hour final delivery runbook
LANGUAGE_CONTRACT: 所有新文件與 FINAL_REPORT 以臺灣繁體中文為主；命令、SHA、路徑與 Git subject 可保留英文。

你是本 Task 的唯一寫入者。這個 Task 明確授權建立本機分支與一個文件 commit，但不授權 push、merge、rebase、reset、stash、刪分支、部署或呼叫付費 API。

已知稽核狀態：
- local GoToAWS0801 = 4ba7c0aa864496c4ed4993a052f58d5ef696ffb0
- origin/GoToAWS0801 = 84079f586844880855980d3997258a52f1cff00f
- 兩者分叉：remote-only 19、local-only 11
- 預期未追蹤檔只有：
  docs/KIRO_REMEDIATION_TASK_PROMPTS.md
  docs/KIRO_4H_AWS_DELIVERY_PROMPTS.md

READ FIRST:
1. AGENTS.md 及其制度文件。
2. docs/KIRO_4H_AWS_DELIVERY_PROMPTS.md。
3. git status --short --branch
4. git log --oneline --decorate --graph --all -25
5. git rev-list --left-right --count origin/GoToAWS0801...GoToAWS0801

PREFLIGHT GATE:
- 若未提交內容不只上述兩個 docs 檔，停止並逐檔回報，不得清理。
- 若 release/final-4h 已存在，停止並回報其 SHA，不得覆寫。
- 執行 git fetch origin GoToAWS0801 後，記錄實際 origin SHA。
- `REMOTE_SHA=$(git rev-parse origin/GoToAWS0801)`；若不等於 `84079f586844880855980d3997258a52f1cff00f`，立即 BLOCKED，只回報 old/new SHA、subjects 與 name-status，等待人工明確給 `APPROVED_SOURCE_SHA=<完整 SHA>`；不得自行判斷採用新版。

執行：
1. 只從已批准的完整 SHA 使用 `git switch -c release/final-4h <APPROVED_SOURCE_SHA>` 建立 release 分支；若 remote 未變，APPROVED_SOURCE_SHA 就是上面的 `84079f5...` 完整值。
2. 確認兩份未追蹤提示詞仍存在，且 tracked 工作樹無其他差異。
3. 執行：
   - python3 -m unittest discover -s tests
   - bash aws/deploy.sh --dry-run
   - git diff --check
4. 只有上述全部通過才 stage 兩份提示詞；核對 `git diff --cached --name-only` 只有這兩檔，再執行 `git diff --cached --check` 與 `bash scripts/check_secrets.sh --staged >/dev/null 2>&1`，只記 exit code。全部通過才建立 TARGET_COMMIT；不要 stage 其他檔案。
5. 再輸出 `git status --short --branch`、HEAD SHA、測試總數與 dry-run 結果。

禁止：
- git pull、merge、rebase、reset、checkout --、restore、clean、stash、force push。
- 修改 local GoToAWS0801 或刪除任何 branch。
- 連 AWS、部署、執行 live/formal、輸出 secrets。

FINAL_REPORT:
- STATUS: PASS/BLOCKED
- SOURCE_REMOTE_SHA
- RELEASE_BRANCH_AND_SHA
- TEST_RESULT
- DRY_RUN_RESULT
- CHANGED_FILES
- NEXT_TASK: E1（只有 PASS 才可開始）
```

---

## E1 — 公開 Lambda 強制 test-only

模型：**Claude Sonnet 4.6**

推理強度：**Max**

硬停損：**20 分鐘**

```text
只執行 Task E1，完成後停止；不要開始 E2/E3。

TASK_ID: E1
OBJECTIVE: 讓公開 AWS Function URL 只能執行 test mode，移除匿名 formal 選項，並在任何 RunManager、collector、Bedrock 或正式鎖定邏輯之前拒絕 formal/rerun 請求。
TARGET_COMMIT: fix(E1): make public lambda demo test-only
LANGUAGE_CONTRACT: 所有頁面、錯誤訊息、測試 fixture 說明與文件以臺灣繁體中文為主；test/formal 等固定 enum 可保留英文並附繁中說明。

前置條件：E0 PASS；目前在 release/final-4h，工作樹乾淨。若不成立立即停止。

你是唯一寫入者。只允許修改：
- lambda_handler.py
- aws/verify-deployment.sh（只更新 test-only 驗證與提示文字）
- tests/test_final_release_guardrails.py（新增）
- 與此限制直接相關的一小段 README/docs 狀態說明

READ FIRST:
- lambda_handler.py
- tests/test_lambda*.py、tests/test_*download*.py、tests/test_*run*.py
- src/run_manager.py
- aws/template.yaml
- aws/verify-deployment.sh

需求：
1. Lambda 首頁保留 query/coin 與執行按鈕，但不得顯示 formal 選項；可保留固定 `mode=test` hidden field，並明示「公開雲端展示只提供 test mode」。
2. JSON 與 form request：
   - 未帶 mode 或 mode=test：維持既有行為。
   - mode=formal、任何等價大小寫，或帶有 authorized_rerun/rerun 類正式重跑旗標：回 HTTP 403。
   - 其他未知 mode：回 HTTP 400。
3. 403/400 必須發生在 `_prepare_run`、RunManager、collector、LLM/Bedrock 之前；不得偷偷降級 formal 為 test。
4. 本機 CLI/app 的 formal 能力不在本 Task 範圍，不要刪除。
5. GET、artifact、`/download?scope=required`、JSON/form 相容性不可回歸。
6. 錯誤回應不可包含 stack trace、AWS account、Function URL、token 或原始 exception。
7. aws/verify-deployment.sh 的首頁檢查必須改為確認 test-only，不能再把 test/formal selector 當成 PASS；其 `--run` 仍只能送 mode=test。

必加測試：
- GET HTML 不含 formal selectable option，且清楚標示 test-only。
- form mode=formal -> 403，mock 證明執行管線 0 次呼叫。
- JSON mode=formal -> 403，執行管線 0 次呼叫。
- authorized_rerun/rerun -> 403。
- omitted mode 與 mode=test 仍走既有成功路徑。
- download/artifact route 回歸測試仍通過。

驗證：
- python3 -m unittest discover -s tests -p 'test_final_release_guardrails.py'
- python3 -m unittest discover -s tests
- bash -n aws/verify-deployment.sh
- bash aws/deploy.sh --dry-run
- git diff --check

只有全部通過才 stage 本 Task allowlist；核對 staged name list，執行 staged diff check 與 `bash scripts/check_secrets.sh --staged >/dev/null 2>&1`，只記 exit code。全部通過才建立 TARGET_COMMIT。不得連 AWS、不得 push、不得執行付費模型。

20 分鐘停損：先完成 formal 403 與測試；不要趁機重構 handler。若全測試未綠，不可 commit，也不可進 E3。

FINAL_REPORT:
- STATUS: PASS/BLOCKED
- HEAD_SHA
- SECURITY_BEHAVIOR
- TEST_COUNTS
- DRY_RUN_RESULT
- CHANGED_FILES
- NEXT_TASK: E2（只有 PASS 才可開始）
```

---

## E2 — 問題相關性、影響方向與可解釋權重

模型：**Claude Opus 4.8**

推理強度：**Max**

硬停損：**40–50 分鐘**

```text
只執行 Task E2，完成後停止；不要開始 E3。

TASK_ID: E2
OBJECTIVE: 對每筆 Evidence 相對使用者 question 做可稽核的語意評估，分開呈現來源可信度、問題相關性、支持/反對關係、影響方向與最終有效權重；不得把 LLM 自評分數當成最終 confidence。
TARGET_COMMIT: feat(E2): add question-aware evidence assessment
LANGUAGE_CONTRACT: assessment rationale、excluded_reason、報告標籤與使用者可見文字一律臺灣繁體中文；JSON key/enum/ID 與原始 title/author 保持來源原文，若需要翻譯另存 optional `summary_zh_tw`，不得覆寫原文。

前置條件：E1 PASS；目前在 release/final-4h，工作樹乾淨。若不成立立即停止。你是唯一寫入者，不得連 AWS、push、改基礎設施或新增資料供應商。

先確認並在 commit 訊息/報告記錄現況缺口：
1. Evidence.claim_relevance 預設 0.0，正式 src 流程沒有寫入者。
2. src/claim_graph.py `_relevance()` 把 0.0 轉成 0.50，導致「明確不相關」也可能被當半相關。
3. credibility/reliability 衡量來源品質，不等於與 question 相關。
4. `_signal_inventory()` 不讀 question；news 沒有方向規則，announcement 固定 neutral。
5. 新聞最多五篇包成 EV-NEWS-001，需保留 item-level locator 才能知道實際引用哪篇。

READ FIRST:
- src/day1_mvp.py、src/schemas.py、src/day2_sources.py
- src/credibility.py、src/claim_graph.py、src/llm.py、src/orchestrator.py、src/validation.py
- tests/test_credibility.py、tests/test_claim_graph.py、tests/test_t4_claim_graph_wiring.py、tests/test_day9_pipeline.py
- .kiro/steering/evidence-confidence-standards.md

唯一允許修改：
- src/day1_mvp.py、src/schemas.py
- src/day2_sources.py
- src/llm.py
- src/claim_graph.py
- src/orchestrator.py
- src/validation.py（只加 assessment contract/gate）
- tests/test_semantic_evidence_assessment.py（新增）
- 因 schema 相容性直接受影響的既有 tests
- docs/COMPETITION_TASK_STATUS.yaml 一小段真實狀態

資料契約：
1. Evidence 追加向後相容的 `source_items`（default 空 list）。每個含 `items` 或 `posts` 的 Evidence，在既有 deterministic 順序下正規化成 source_items，並加入穩定 `source_item_id`，格式由 parent evidence_id + 兩位序號組成；不可用隨機值。
2. 每個子項盡可能保留：title/text、url、publisher/platform、author/author_handle、published_at/created_at/indexed_at。來源沒有提供時存 null，禁止以 fetched_at 冒充 published_at，禁止猜作者。
3. Feed parser 補讀 RSS author/DC creator 與 Atom author/name；Bluesky/Hacker News/Reddit 只保存 API/feed 真正提供的作者欄位。
   同時修正 social time semantics：`social`、`social_bluesky`、`social_hackernews` 都要讀各自真正提供的 published/created/indexed 時間；不得只支援 Reddit 或把擷取時間當發布時間。
4. Evidence 追加向後相容的 `semantic_assessment`（default 空 dict），至少含：
   - relevance_label: direct | indirect | context | irrelevant | unclear
   - relevance_score: 由 Python 固定映射 direct=1.0、indirect=0.65、context=0.35、irrelevant=0.0、unclear=0.20
   - relationship_to_question: support | contradict | context | irrelevant | unclear
   - impact_direction: bullish | bearish | neutral | mixed | not_applicable | unclear
   - impact_horizon: immediate | short_term | medium_term | long_term | unclear
   - rationale: 臺灣繁體中文短句，最多 120 字
   - source_item_ids: 最多 3 個，只能引用該 Evidence 內存在的 source_item_id
   - assessment_source: llm | deterministic_fallback
   - effective_weight

評估與計分邊界：
1. 不新增額外 Bedrock 呼叫。擴充既有 analysis structured output，讓同一次分析回傳每個 Evidence ID 的離散 assessment；不得要求模型輸出 numeric relevance、effective_weight、reliability 或 final confidence。
2. Python 驗證：assessment 必須恰好引用本次已存在 Evidence ID；source_item_ids 必須屬於 parent；enum 合法；重複/未知 ID 失敗。模型結果不合法時，全批 assessment 作廢並走 deterministic fallback，不可部分接受。
3. deterministic fallback 對結構化市場/技術資料可依既有 signal 規則分類；無法理解新聞語意時必須 `unclear`/0.20，不可假裝知道。coin 不符、明確超出 plan time window 或內容不談題目時標 irrelevant/0.0，但仍保留在 evidence.json 並寫 excluded_reason，不可靜默刪除。
4. Python 算 `effective_weight = reliability_score × relevance_score × independence_factor`，四捨五入且限制 0–1。relationship/impact 是解釋欄位，不得繞過公式自行加權。
5. Claim 的 supporting/contradicting 關係仍由 proposal 提出並由 Python 驗 ID；被標 irrelevant 的 Evidence 不得進 Claim，context 不能當唯一主要支持。Claim confidence 繼續由 deterministic 既有公式與 hard caps 計算，並明示 `heuristic`、不是校準過的正確機率。
6. 修正 `_relevance()`：缺值與明確 0 必須可區分；明確 0 永遠保持 0，不能再變 0.50。
7. `evidence.json` 必須保存 assessment、計算版本與 source_item metadata；`execution_log.json` 的 build/assessment step 必須保存 path、fallback reason、count、excluded IDs，但不可保存完整新聞全文。
   Citation Gate 必須驗證：非 insufficient Claim 的每個 Fact/support/contrad parent Evidence 都存在；聚合 news/announcement/social 被引用時，assessment 至少有一個可解析且含 http/https URL、fetched_at 的 source_item_id。缺 author/published_at 可為 null 並 warning，但不得偽造。
8. 不改 market stance 的既有固定權重公式；今天只讓 Claim/evidence 層的問題相關性可稽核，避免在 50 分鐘內同時改兩套決策邏輯。文件要明示 stance 與 Claim confidence 仍是兩條不同訊號層。

必加測試：
- explicit irrelevant 保持 0，不會回到 0.50，也不得被 Claim 引用。
- 五種 relevance label 映射與 effective_weight 公式可重現。
- credibility 高但 irrelevant 的新聞權重為 0；credibility 低但 direct 的資料仍受 reliability 壓低。
- unknown Evidence ID、跨 parent source_item_id、重複 ID、非法 enum 全批 fallback。
- title-only/news 無法判斷時 fallback=unclear，不可自動 bullish/bearish。
- author/published 缺值保留 null，不以 fetched_at 代填。
- 同一輸入產生相同 source_item_id/assessment/weight。
- spy 證明沒有增加 LLM 呼叫次數。
- offline 與 Bedrock-invalid assessment 都能產出六項 artifacts，且 execution log 記錄 fallback。

驗證：
- python3 -m unittest discover -s tests -p 'test_semantic_evidence_assessment.py'
- python3 -m unittest discover -s tests
- python3 -m compileall -q src lambda_handler.py tests
- bash aws/deploy.sh --dry-run
- git diff --check

只有 targeted/full/dry-run 全通過才 stage 本 Task allowlist；核對 staged name list，執行 staged diff check 與 `bash scripts/check_secrets.sh --staged >/dev/null 2>&1`，只記 exit code。全部通過才建立 TARGET_COMMIT。第 35 分鐘 targeted tests 不綠就停止擴 scope；第 50 分鐘仍未全綠則 STATUS=BLOCKED、不 commit、不部署舊語意版本。

FINAL_REPORT:
- STATUS: PASS/BLOCKED
- HEAD_SHA
- ASSESSMENT_CONTRACT
- EFFECTIVE_WEIGHT_FORMULA
- NO_EXTRA_LLM_CALL_EVIDENCE
- TEST_COUNTS
- KNOWN_LIMITATIONS（必含 record-level、非歷史機率校準、非全網搜尋）
- CHANGED_FILES
- NEXT_TASK: E3（只有 PASS 才可開始）
```

---

## E3 — 雲端 Final Report／Evidence List／Execution Log 與 Claim 溯源 UI

模型：**Claude Sonnet 4.6**

推理強度：**Max**

硬停損：**35–45 分鐘**

```text
只執行 Task E3，完成後停止；不要開始 E4。

TASK_ID: E3
OBJECTIVE: 把 Lambda Function URL 的結果頁改成可閱讀的三報告介面：Final Report、Evidence List、Execution Log；Final Report 每個 Claim 的支持/反對引用都能點到 Evidence 及實際 source item，並顯示 URL、擷取時間、作者與發布時間。
TARGET_COMMIT: feat(E3): render traceable cloud research reports
LANGUAGE_CONTRACT: HTML `lang='zh-Hant-TW'`；導覽、欄名、狀態、錯誤、報告與 aria-label 以臺灣繁體中文為主，必要英文放括號。原始來源標題、作者、URL 保留原文，可另顯示繁中摘要，但不得冒充來源原文。

前置條件：E2 PASS；目前在包含 E2 commit 的乾淨 release 分支。這是雲端可部署功能，禁止只修改本機 src/app.py 而漏掉 lambda_handler.py。

READ FIRST:
- lambda_handler.py
- src/report_renderer.py、src/schemas.py、src/validation.py
- src/app.py 的既有較完整 HTML，能安全重用才重用，不做大型抽象重構
- tests/test_report_renderer.py、tests/test_lambda_download_routes.py、tests/test_t6_web_demo.py
- aws/template.yaml、aws/deploy.sh

唯一允許修改：
- lambda_handler.py
- src/report_renderer.py（只補報告所需 metadata/anchor，保持 deterministic）
- 如需共用純渲染 helper，可新增一個 src/cloud_report_view.py
- tests/test_cloud_report_traceability.py（新增）
- 因 HTML 契約直接受影響的 Lambda/report tests
- docs/COMPETITION_TASK_STATUS.yaml 一小段真實狀態

雲端頁面驗收：
1. 不使用 React、Node、CDN、外部 CSS/JS、markdown 套件或瀏覽器自動化；只用 Python stdlib + 內嵌 CSS，必須進現有 Lambda ZIP 且不增加外部 runtime dependency。
2. 結果頁上方顯示 run_id、test mode、status、UTC started/completed、data as_of、Citation Gate、degradation；保留「下載三份提交物 ZIP」與「下載全部」按鈕。
   新增不觸發研究管線的 `GET /report?run=<run_id>`，只讀同一個 run 的三份 artifacts 並渲染；找不到同容器 `/tmp` 時明確 404 說明 ephemeral 限制，絕不重跑分析。
3. 三個可鍵盤操作的 tab/section，無 JavaScript時也能閱讀：
   A. Final Report：研究問題、結論、每個 Claim 的 statement/verdict/confidence/components/limiters、Fact→Inference→Conclusion、支持/反對證據、限制與推翻條件。
   B. Evidence List：每筆 Evidence 的 id、source、可點擊原始 URL、fetched_at、published_at/event_time、source type、verification、reliability breakdown、question relevance、relationship/impact/effective weight、related claim IDs；聚合新聞/貼文展開 relevant source items，顯示 item URL、title、publisher/platform、author、published_at。
   C. Execution Log：以時間線/表格顯示 stage、status、started/finished/duration、tool/provider/model、collector query/locator、產生 Evidence IDs、fallback reason；不得只丟一大段未格式化 JSON。
4. Final Report 的每個 Fact、supporting_evidence_id、contradicting_evidence_id 都是站內 anchor，可跳到 Evidence card；news/social assessment 的 source_item_ids 再直接指向 item row。unknown/rejected ID 必須讓 Citation Gate FAIL，不能渲染成假連結。
5. 缺 author/published_at 顯示 `N/A（來源未提供）`；不得以 fetched_at 代替。fetched_at 要明確標「系統擷取時間」，published_at 標「來源發布時間」。
6. 外部 URL 只允許 http/https，HTML attribute/text 全部 escape，連結加 `target=_blank rel='noopener noreferrer'`；`javascript:`、data URL、含 `<script>` 的標題/作者必須當純文字且不得成為可點連結。
7. 頁面手機與桌面可讀：最大內容寬、清楚字級/行高、表格可水平捲動、support/contrad 色彩不作唯一辨識、focus state、details/summary 可展開。
8. JSON API 回應與 `/artifact`、`/download?scope=required|all&run=` 契約不可改壞。三份 required artifacts 仍是 report.md、evidence.json、execution_log.json；UI 是相同 artifact 的可閱讀投影，不是第二份真相來源。
9. 首頁保留 E1 的 public test-only；不得恢復 formal selector。
10. `report.md` 本身也必須以繁中固定結構呈現每個 Claim 與可點擊 Evidence/source-item URL；章節標題以繁中為主、英文括號為輔。`evidence.json`、`execution_log.json` 可保留英文 JSON keys，但所有人類可讀的 rationale、status 說明與 summary 以繁中為主。前端不得只把 raw Markdown/JSON 塞進 `<pre>` 當主畫面。

必加測試：
- 結果 HTML 同時包含三報告標題與三份 artifact download。
- 每個 Claim support/contrad ID 都有有效 anchor target；source_item_id 亦可定位。
- URL、fetched_at、published_at、author、reliability、relevance、effective_weight 可見；缺值顯示 N/A。
- 外部惡意 title/author/url 不產生 XSS 或 javascript link。
- rejected/unknown evidence 不可偽裝成可追溯引用。
- execution log 顯示時間、tool、collector locator、fallback。
- JSON/form/GET/download/artifact 與 E1 test-only 回歸。
- `GET /report?run=` 只渲染既有 artifacts；spy 證明不呼叫 collector/Bedrock/RunManager create_run，未知或 traversal run_id 回 404。

驗證：
- python3 -m unittest discover -s tests -p 'test_cloud_report_traceability.py'
- python3 -m unittest discover -s tests
- python3 -m compileall -q src lambda_handler.py tests
- bash -n aws/deploy.sh
- bash -n aws/verify-deployment.sh
- bash aws/deploy.sh --dry-run
- git diff --check

只有全部通過才 stage 本 Task allowlist；核對 staged name list，執行 staged diff check 與 `bash scripts/check_secrets.sh --staged >/dev/null 2>&1`，只記 exit code。全部通過才建立 TARGET_COMMIT。不得連 AWS、呼叫 Bedrock、push 或修改 CloudFormation。第 45 分鐘仍未全綠則 BLOCKED，不得用「本機 UI 看起來可以」取代 Lambda handler 測試。

FINAL_REPORT:
- STATUS: PASS/BLOCKED
- HEAD_SHA
- THREE_REPORT_UI_EVIDENCE
- CLAIM_TO_EVIDENCE_ANCHOR_EVIDENCE
- SOURCE_METADATA_FIELDS
- XSS_AND_ROUTE_TESTS
- FULL_TEST_COUNT_AND_DRY_RUN
- CHANGED_FILES
- NEXT_TASK: E4（只有 PASS 才可開始）
```

---

## E4 — 部署 AWS 並執行一次 test/live smoke

模型：**Claude Opus 4.8**

推理強度：**Max**

硬停損：**70–90 分鐘**

```text
只執行 Task E4，部署與 smoke 完成後停止；這是最後一個 Task。

TASK_ID: E4
OBJECTIVE: 從明確、已測試、乾淨的 candidate SHA 部署到 AWS，公開 endpoint 只能 test mode；執行一次付費 test/live smoke，驗證繁中三報告與逐 Claim 溯源，保存三份 required artifacts 及六檔完整 bundle。
TARGET_COMMIT: docs(E4): record final aws smoke evidence
LANGUAGE_CONTRACT: 使用者可見的雲端頁面與 smoke 記錄以臺灣繁體中文為主；AWS resource ID、CLI、模型名、JSON key 與原始來源可保留英文。不得把完整 Function URL、account ID、ARN 或 secret 放進對話、報告或 commit。

輸入：只使用 E3 PASS 的 HEAD_SHA。E2/E3 任一失敗都不得部署較舊 candidate，也不得因目前 checkout 狀態猜 SHA。

授權邊界：
- 可做本機唯讀/preflight 與 dry-run。
- 真實 deploy 前必須取得 HUMAN GATE A。
- 任何會呼叫 Bedrock/外部 API 的 smoke 前必須另取 HUMAN GATE B。
- 唯一允許的 S3 是 `aws/deploy.sh` 使用的私有 deployment staging bucket 與本次 `releases/*.zip` object；它不是 artifact persistence，系統報告不得說成 S3 Evidence Store。
- 唯一允許的 IAM 是本 CloudFormation stack 管理、且只授權指定 Bedrock model 的 Lambda execution role/policy。禁止手動加 managed admin policy或改其他 role。
- 不授權 formal run、artifact S3、DynamoDB、手動 rollback/redeploy/delete、push、force 或修改其他 AWS 資源；每一次 manual rollback/redeploy/delete 都要新的 HUMAN GATE。

PHASE 0 — candidate isolation：
1. 在原 workspace 記錄 FINAL_BRANCH 與 candidate SHA；要求 HEAD 正好等於 E3 PASS SHA、tracked/untracked 都乾淨，且 candidate 含 E1/E2/E3 commits。任一不符即 BLOCKED。
2. 一律用明確 candidate 完整 SHA 建立新的 temporary detached worktree；所有 test、build、package、deploy 都只從該隔離目錄執行。隔離目錄的 `git status --porcelain --untracked-files=all` 必須完全空白。不得從目前 checkout 或含未追蹤檔的目錄部署。
3. detached worktree 只負責 exact candidate 部署，不在裡面建立 commit。E4 evidence 最後回到原 FINAL_BRANCH 寫入；寫入前再次要求原分支 HEAD==candidate 且乾淨。
4. 不要複製或 commit `.env`/credentials；使用既有 AWS profile/credential chain。完整 URL 只存在程序環境或 mode 0600 的 temporary file。

PHASE 1 — 免費 preflight：
- python3 -m unittest discover -s tests
- python3 -m compileall -q src lambda_handler.py tests
- bash -n aws/deploy.sh
- bash -n aws/verify-deployment.sh
- bash aws/deploy.sh --dry-run
- git diff --check
- Python 必須 >=3.10；完整 suite 必須 0 failure/error、0 unexpected skip，且測試數不得少於 683 加上 E1/E2/E3 事前列出的新增 test 數；不得靠刪測試過關。
- 記錄 dry-run deployment ZIP 的 SHA-256，確認 package 不含 `.env`、credentials、tests、docs、runs 或 cache；確認部署時傳入的 CodeCommit 參數等於 candidate、不是 `-dirty`。
- 所有 secret scan stdout/stderr 導向 mode 0600 的 `mktemp`；只回傳 exit code 與 PASS/FAIL。FAIL 時不得讀取或貼出 raw log。commit 前另做 staged scan。
- `aws sts get-caller-identity` 的 raw output 也不得貼出；只回報 account ID 末四碼與 principal 類型。
- Gate B 前禁止執行 aws/verify-permissions.sh、任何 bedrock-runtime InvokeModel/Converse、collector probe 或任何 POST。Bedrock model access 只由 Gate B 後的唯一 smoke 驗證。
- 以不輸出 URL 值的方式檢查既有 Function URL 是否仍存活、是否曾命中公開 Git history；若同一公開 URL 已外洩且仍有效，AuthType=NONE 必須 BLOCKED，除非人工在 Gate A 明確批准 containment/rotation 方案。

HUMAN GATE A 必須逐字詢問並等待：
「是否批准把 candidate <SHA>（ZIP SHA-256 <ZIP_SHA>）部署到 AWS profile <PROFILE>、account 末四碼 <LAST4>、region <REGION>、stack <STACK>？參數為 provider=bedrock、model=<MODEL_ID>、Function URL AuthType=NONE、CORS=*、reserved concurrency=1、log retention=<DAYS>。deploy.sh 將建立/使用私有 deployment staging bucket/object；CloudFormation 將建立/更新 Lambda、此 stack 專用 IAM role/policy、Function URL/permissions 與 CloudWatch log group；不建立 artifact S3/DynamoDB。只允許 CloudFormation 自動 rollback，不批准 manual redeploy/delete。本步會產生雲端成本，但尚不執行模型 smoke。是否批准？」

未收到明確批准不得部署。

PHASE 2 — deploy：
- 只有 E1/E2/E3 targeted/full tests 全通過時才可部署；任何失敗一律 BLOCKED，不得改用 AWS_IAM 繞過 release gate。
- 批准後只執行 repo 既有 `aws/deploy.sh`，參數必須等於 Gate A：`--profile <PROFILE> --region <REGION> --stack-name <STACK> --provider bedrock --model-id <MODEL_ID> --auth-type NONE --concurrency 1 --log-retention <DAYS>`。不得手寫第二套部署或臨時改 template。
- deploy.sh stdout/stderr 全部寫進 mode 0600 temp log。任何摘要顯示前必須遮蔽完整 account、ARN、deployment bucket 與 Function URL；raw log 不進對話、報告或 commit。
- 保存 deploy UTC 時間、region、stack、candidate/ZIP SHA、CloudFormation 狀態、Lambda LastUpdateStatus、Function URL auth type、CodeCommit；不得保存完整 URL。
- Gate A 已批准的非模型 GET 首頁最多一次：應 200、`lang=zh-Hant-TW`、含繁中 test-only、三報告功能說明，不含 formal selector。不要做線上 mode=formal probe；若 guard 未生效，該 probe 會意外觸發正式執行。只用 E1 tests、exact deployed SHA 與首頁 HTML 驗 guard。

HUMAN GATE B 必須逐字詢問並等待：
「是否批准對剛部署的 test-only endpoint 執行恰好 1 次 ETH test/live smoke？此步可能呼叫 Bedrock 與外部資料來源並產生費用；不會執行 formal。」

PHASE 3 — exactly one smoke：
1. Gate B 後只送恰好一次 `Content-Type: application/json`、`mode=test` 的 POST；response 寫入 mode 0600 temp file，不得用 `verify-deployment.sh --run`（它不保存 response，也不做完整驗證）。timeout/4xx/5xx 一律不重送；若要重送必須有 CloudWatch 證明 collector/Bedrock 0 次呼叫及新的 HUMAN GATE B2。
2. 執行 `python3 scripts/verify_live_smoke.py <0600_RESPONSE_FILE>`；另嚴格驗證 HTTP 成功、總時間 <=900 秒、run_mode=test、execution_flags live/use_llm=true、manifest.code_commit 完整等於 candidate 且不得含 `-dirty`、Citation Gate 無 error。
3. 驗證 planner provider=Bedrock、analyst/critic=`bedrock + success`、assessment 無未知 fallback；13 個 collectors 都有明示狀態。`vegas_channel`/`long_short_ratio` 的已知 us-west-2 geo fallback 可揭露；其他未知或關鍵 domain fallback 使 live 結果降為 PARTIAL/NO-GO。
4. 從同一次 JSON response 取得 run_id 與六項 artifacts；呼叫不觸發 pipeline 的 `GET /report?run=<run_id>` 驗證繁中三報告 UI、每個 Claim anchor、至少一個外部 source hyperlink、author/published/fetched 欄位。跨 container 404 時標 UI live check PARTIAL，不得重跑分析。
5. 立即呼叫 `/download?scope=all&run=<run_id>` 下載 six-item evidence bundle：
   - report.md
   - evidence.json
   - execution_log.json
   - research_plan.json
   - claims.json
   - manifest.json
6. ZIP 必須 HTTP 200、合法、含六檔，且 manifest 所列五檔 bytes/SHA-256 重算全部相符。跨 container 404 時使用同一次 JSON response 保存六檔，但 download/UI 只能 PARTIAL；不得宣稱 persistence、不得重送 smoke。
7. evidence 應有 id/source/http(s) URL/fetched_at/reliability/semantic assessment；每個非 insufficient Claim 的來源 anchor 與 source_item 均可解析。缺 author/published 可是明示 N/A，不可偽造。
8. 不測 cloud comparison，不測 formal，不追加新功能。

PHASE 4 — evidence：
- 新增 `docs/AWS_FINAL_SMOKE.md`，只記錄非敏感結果、candidate SHA、UTC 時間、stack/region、test mode、驗證項目、已知限制與「帳號生命週期由主辦方直接取消帳號權限」。
- Function URL 只用 `<redacted>` 或 host hash；不得把完整 URL commit。
- 只有證據支持時，才把 docs/COMPETITION_TASK_STATUS.yaml 對應 T8.5/T8.6 更新 PASS；否則保留 PARTIAL 並說明。
- 回到原 FINAL_BRANCH，確認 HEAD==candidate 且工作樹仍乾淨，只 stage E4 allowlist；執行 `git diff --cached --name-only`、`git diff --cached --check`，以及 stdout/stderr 全抑制的 staged secret scan，只記 exit code。全部通過才建立 TARGET_COMMIT；不要 push。

失敗策略：部署問題 15 分鐘未定位就停止新增功能。保留 CloudFormation events 與 rollback 方向，集中修復 release blocker；不得用離線 fixture 冒充 live final。

FINAL_REPORT:
- STATUS: PASS/PARTIAL/BLOCKED
- CANDIDATE_SHA
- DEPLOYMENT_SUMMARY（不含 URL/secrets）
- TESTS_AND_DRY_RUN
- SMOKE_COUNT_AND_MODE
- SIX_ARTIFACTS_AND_HASH_RESULT
- COST_SCOPE
- LIMITATIONS
- FINAL_BRANCH_AND_COMMIT
- ACCOUNT_LIFECYCLE: 主辦方直接取消帳號權限；本專案不規劃或執行 teardown
- NEXT_TASK: STOP（系統與 AWS 驗收完成）
```

---

## 人工同時進行的 AWS 準備

Kiro 執行 E2/E3 時，人只需要同步完成部署準備：

1. 確認要使用的 AWS profile、account 末四碼、region、stack name 與預算，不把 credentials 貼進 Kiro。
2. 唯讀盤點既有 stack/Lambda/Function URL，確認是否能安全 update、是否有舊公開 URL 仍有效。
3. 確認 Bedrock `amazon.nova-lite-v1:0` 是既定模型；不要執行會實際 Converse 的 permissions script。
4. 準備一個本機私密目錄保存唯一 smoke response、三份 required artifacts、六檔 bundle 與 hash；不得放進 Git。
5. E4 完成後用瀏覽器實際操作繁中三報告、逐 Claim 點 Evidence/source item，並立即下載 ZIP。

## 最後 15 分鐘停止規則

最後 15 分鐘禁止新增功能或資料來源。只允許修 release blocker、重新跑 targeted/full tests、重新打包 exact SHA、檢查 CloudFormation/Lambda 狀態、驗證繁中三報告、保存唯一 smoke artifacts。若 live endpoint 此時仍不穩，不再臨時加 AWS 元件。
