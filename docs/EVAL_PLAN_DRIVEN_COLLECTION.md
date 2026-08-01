# 評估：把 Research Plan 回饋到採集層

**狀態**：P0 已實作（第 2 節）；P1 plan→collection 接線仍為評估，未實作
**日期**：2026-08-01
**評估分支**：`GoToAWS0801`（測試時 HEAD 為 T8 凍結後狀態，541 tests 全過）
**動機**：目前題目幾乎不影響報告內容。實測顯示不同題目產生的立場、權重、證據集完全相同。
**硬要求**（使用者指定）：所有 LLM 產出必須可溯源；報告每一項內容必須有依據。

---

## 0. 結論摘要

這條線比預期好接，因為資料契約已經存在——`ResearchPlan.required_domains` 已按題目產出，
adapter 已可參數化，`content_reference` 已記錄可重現的 query 參數。缺的只有一個呼叫參數。

但評估過程中發現一個**與本提案無關、已經存在的漏洞**：LLM 可以藉由縮小
`required_domains` 間接抬高 claim confidence。這違反「LLM 不可以決定最終 claim confidence」。
該漏洞已獨立修復（本文第 2 節，P0），未觸碰採集層與 planner。

實作 P0 時發現本評估初版建議的修法（union）會破壞既有的合理設計意圖，已改採分母下限並
記錄於第 2.4 節。**這是本評估的一個教訓：修法建議在實作前未對照既有測試。**

剩餘工作：P1 plan→collection 接線，建議等 AWS 部署收束後再動。

---

## 1. 現況盤點

### 1.1 已經到位，不需重做

**`required_domains` 已按題目產出。** 以 ETH 離線執行三個題目實測：

| 題目 | `required_domains` | `task_modes` | `time_window` |
|---|---|---|---|
| 分析近期市場狀況、主要驅動因素與風險 | `market, news, derivatives` | 3 個 | 14d（default） |
| 如果聯準會九月降息，ETH 會受益嗎？ | `market` | 1 個 | 14d（default） |
| 比較 ETH 過去 90 天的鏈上鎖倉量與價格走勢是否背離 | `market, onchain` | 3 個 | **90d（explicit）** |

**adapter 已可參數化**：

```text
fetch_coingecko(coin, days=14)
fetch_defillama_tvl(coin, days=90)
fetch_long_short_ratio(coin, period="1h", limit=24)
fetch_news_rss(coin, feed_url=None, limit=5, ...)
```

**`content_reference` 已記錄可重現條件**（不是只記 URL）。`fetch_coingecko` 實例：

```python
{"endpoint": url, "query": {"vs_currency": "usd", "days": days}, "points": len(prices)}
```

**邊界契約已凍結**：`src/ports.py` 的 `LLMClient` 只能回 JSON；`src/planner.py` 已有 schema
驗證與 `TASK_MODES` 白名單，非法值時整份 plan 走 deterministic fallback。

### 1.2 缺的部分

**唯一缺口**：`collect_evidence_detailed()` 沒有收到 plan。`src/orchestrator.py` 第 1201 行：

```python
evidence, collection_log, agent_report = collect_evidence_detailed(
    coin, live=live, deadline=collection_deadline if live else None, fulltext=fulltext,
)
```

後果實測：第三題的 plan 正確辨識出 90 天（`time_window.source = "explicit"`），但證據的
`time_range` 仍全部是 `14d`，`final_score` 與第一題逐一相等。**plan 算出來了，沒有人使用。**

### 1.3 實測：題目對報告的影響幅度

同一幣種、離線模式（確定性）、三個題目：

```text
三題結果完全相同：立場 偏多、多方 2.2 / 空方 0.9、訊號 7 項
證據完全相同：9 筆、同樣 data_type、同樣 time_range、12 個 final_score 逐一相等
```

報告差異（題目 A vs B，共 154 行）：38 行不同，但扣除 `run_id`、三個時間戳與題目回顯後，
**實質差異只有一行** `Task modes`。段落結構完全相同。

題目 A vs C：59 行不同，段落標題只多一個 `### CL-002｜...`（`hypothesis_test` claim）。

**結構性原因**：`_signal_inventory()` 的輸入只有 `evidence`，沒有 `question`；而採集層收不到
plan，所以 11 個來源不論題目一律照抓。訊號是證據的純函式，證據相同則訊號相同。

### 1.4 附帶發現：關鍵字 fallback 的語意限制

「如果聯準會九月降息，ETH 會受益嗎？」在 deterministic fallback 下只得到
`task_modes = ['describe_market_state']`，`hypotheses` 為空，因此沒有產生 `hypothesis_test`
claim。第三題有「比較」「是否」等關鍵字才被辨識。

這是 LLM planner 的價值所在（上 AWS 後 `plan_research` 走模型即可改善），不是本提案的範圍，
但說明了為什麼 plan 品質值得投資。

---

## 2. P0：必須先修的既有漏洞

### 2.1 問題

`src/claim_graph.py` 第 437–447 行，`required_domains` 是 `domain_coverage` 的**分母**：

```python
required = tuple(str(name) for name in (required_domains or ()) if str(name).strip())
if not required:
    required = DEFAULT_REQUIRED_DOMAINS
covered = sorted({pool.domain(item) for item in considered} & set(required))
domain_coverage = round(len(covered) / len(required), 4) if required else 0.0
```

`domain_coverage` 在 confidence 中權重 **0.25**。因此 plan 要求的領域越少，分數越高。

### 2.2 實測幅度

離線三題的 `domain_coverage`：

```text
required_domains = 3 個 → domain_coverage = 0.6667
required_domains = 1 個 → domain_coverage = 1.0
required_domains = 2 個 → domain_coverage = 0.5
```

以 live run 的實際 components 重算同一批證據：

```text
現況（3 個領域）domain_coverage = 0.3333 → 原始分數 0.5797
縮到 1 個領域    domain_coverage = 1.0    → 原始分數 0.7463
                                            差距 +0.1667
```

更嚴重的是 limiter 會消失：`domain_coverage_below_minimum` 的門檻是
`src.schemas.MIN_DOMAIN_COVERAGE = 0.4`（判定在 `claim_graph.py` 第 493 行），`0.3333` 觸發、
`1.0` 不觸發，因此 verdict 會從 `insufficient_evidence` 變成有方向的判斷。

在上述三次離線執行中最終分數都被 hard cap 壓到 0.35（`fallback_only_primary_support`），
漏洞被遮蔽未顯現；在 live 且證據充分時會浮出。

### 2.3 判定

違反 `.kiro/steering/evidence-confidence-standards.md`：「LLM 不可以決定最終 claim confidence」。
Planner 目前已經在產出 `required_domains`，所以這個路徑**現在就是通的**，不需要等本提案實作。

### 2.4 修法（已實作）

**狀態**：已實作，commit 見本節末。

#### 初版建議被否決

本評估初版建議把分母改成
`union(required_domains, DEFAULT_REQUIRED_DOMAINS)`。**這個建議是錯的**，實作時才發現。

`tests/test_claim_graph.py` 的 `test_plan_hypotheses_produce_separate_hypothesis_claims`
（第 627 行）明確鎖定了現行行為：

```python
# required_domains 來自 plan：三個領域全覆蓋，不應被預設的四領域判成資料不足。
self.assertEqual(graph["claims"][0]["confidence"]["components"]["domain_coverage"], 1.0)
```

也就是「題目不相關的領域本來就不該扣分」是刻意設計，不是疏漏。union 會讓一個只問新聞面的
題目因為缺少衍生品資料而被扣分，破壞這個合理意圖。

#### 實際採用：分母下限

保留 plan 縮減範圍的能力，但給分母一個下限：

```python
requested = tuple(dict.fromkeys(...required_domains...))
required = requested or DEFAULT_REQUIRED_DOMAINS
scope_floor_applied = len(required) < MIN_REQUIRED_DOMAIN_COUNT
if scope_floor_applied:
    padded = tuple(dict.fromkeys(required + DEFAULT_REQUIRED_DOMAINS))
    required = padded[:MIN_REQUIRED_DOMAIN_COUNT]
```

`MIN_REQUIRED_DOMAIN_COUNT = 3`。這個值不是任意選的：`MIN_DOMAIN_COVERAGE = 0.4`，
而 `1 / 3 = 0.333 < 0.4`，所以**任何單一領域的 Claim 都無法靠改寫 plan 通過覆蓋率門檻**。

同時保留下限剛好是 3 這件事讓既有測試成為邊界案例（plan 要求 3 個 → 分母不變 → coverage 1.0），
原設計意圖完整保留。

#### 可溯源性

分母被補足時留下三處痕跡，讀者可跨提交物還原計算過程：

| 位置 | 內容 |
|---|---|
| `research_plan.json` | plan 原本要求的 `required_domains` |
| `claims.json` → `confidence.limiters` | 新 limiter `plan_scope_denominator_floor` |
| `report.md` →「生效上限」 | 同一個 limiter 名稱，人類可讀 |
| `evaluate_claim()` 回傳值 | `required_domains`（生效分母）與 `plan_requested_domains`（plan 原始要求） |

註：`plan_requested_domains` 目前只存在於 `evaluate_claim()` 的回傳值，未進入 `claims.json`——
`Claim` dataclass 的欄位集合已凍結，為此擴充風險不成比例。跨 `research_plan.json` 與
limiter 兩處已足以還原，不需要改動凍結契約。

#### 驗證

```text
targeted：python3 -m unittest tests.test_claim_graph → 46 tests OK（含 6 個新增）
完整套件：python3 -m unittest discover -s tests   → 587 tests OK
```

離線端到端（題目 `如果聯準會九月降息，ETH 會受益嗎？`，其 deterministic plan 只要求
`['market']`，正是漏洞觸發條件）：

```text
修正前  domain_coverage = 1.0
修正後  domain_coverage = 0.3333
        limiters 新增 plan_scope_denominator_floor
        domain_coverage_below_minimum 隨之觸發
        verdict = insufficient_evidence
        六項提交物完整
```

---

## 3. 可溯源性設計

以下五條是本提案的接受條件，缺任何一條都不應接線。

### 3.1 封閉詞彙表

LLM 只能從 `_SOURCE_LOADERS` 現有的 11 個 label 中挑選，**不得發明來源、不得提供 URL、
不得指定 endpoint**。出現非法值時整份 plan 走 deterministic fallback——沿用 `planner.py`
既有行為，不新增機制。

### 3.2 plan 決策要有指紋

`research_plan.json` 新增 `plan_provenance`：

```text
path            "llm" | "fallback"
plan_hash       plan 內容的確定性 hash
model_id        實際使用的模型（fallback 時為空）
prompt_version  prompt 樣板版本
decided_at      決策時間
```

`manifest.json` 記錄 `plan_hash`。沒有這個欄位，事後無人能回答「當初為什麼抓 90 天」。

### 3.3 Evidence 要記錄「為什麼抓它」

每筆 Evidence 新增 `collection_rationale`：

```text
requested_by    "plan" | "default"
plan_hash       驅動這次採集的 plan
resolved_params 實際送出的參數值
```

`resolved_params` 必須是實際送出的值而非樣板。現有 `content_reference` 的
`{"endpoint": ..., "query": {...}}` 寫法已經正確，延用同一慣例。

### 3.4 領域被跳過也要留證

**最容易漏的一條。** plan 判定不需要 social 時，報告上看不出差別，讀者會以為系統漏抓。

因此需要 `skipped_domains` 寫入 `execution_log.json`，並在報告明寫「本次未蒐集 X，因為 plan
判定與題目無關」。

**缺席也需要依據**——否則「報告每一項都有依據」只涵蓋了出現的項目，沒涵蓋消失的項目。

### 3.5 LLM 不得觸碰的清單（不變）

```text
訊號權重（_signal_inventory）
立場（_market_stance）
reliability score（credibility.py）
claim confidence（claim_graph.py）
hard cap
```

全部留在 deterministic Python。本提案不改變這條邊界。

---

## 4. 改動範圍

三個檔案，都是加參數而非改邏輯：

| 檔案 | 改動 |
|---|---|
| `src/day2_sources.py` | `collect_evidence_detailed()` 增選填 `plan` 參數，只做兩件事：解析 `days`／`period`／`limit`；決定領域優先序。`plan=None` 時行為與現在逐字相同。 |
| `src/orchestrator.py` | 第 1201 行傳入 plan；`skipped_domains` 寫入執行記錄。 |
| `src/claim_graph.py` | `domain_coverage` 分母改單向（P0，可獨立先做）。 |

**不動**：`src/planner.py`（已完成）、`src/credibility.py`、`src/app.py`、`lambda_handler.py`、
`src/vegas_strategy.py`、`src/report_renderer.py`。

---

## 5. 風險

### 5.1 確定性下降（本提案的本質代價）

plan 由 LLM 產出，證據集就隨模型變動。

緩解：`plan_hash` 讓「相同 plan 得到相同證據集」變成可測條件。但「相同題目得到相同 plan」在
LLM 路徑下**無法保證**，只有 fallback 路徑能保證。這一點必須在報告誠實標明，不得宣稱整體
執行是確定性的。

### 5.2 時間預算

90 天窗口讓 CoinGecko 資料點變多、news limit 變大，採集時間會上升。現有
`stop_conditions.max_evidence = 36` 與 deadline watchdog 應足夠，但需實測，不能假設。

### 5.3 測試衝擊（接線前必須先量測）

541 個測試中有多少依賴 `14d` 固定值，尚未量測。這是接線**之前**要做的事，不是接完再看。

### 5.4 離線 fixture 不一致

`src/day1_mvp.py` 的 fixture 寫死 `14d`。plan 要求 90 天時，離線模式會出現 plan 與證據不符。
必須在報告標明此落差，不得靜默。

---

## 6. 驗收條件

- `plan=None` 時完整測試套件全過，且輸出**逐字不變**（determinism 回歸）
- 同一 `plan_hash` 兩次執行，evidence 的 `resolved_params` 完全相同
- LLM 回傳非法 domain 時整份 plan 走 fallback，且執行記錄可見
- **縮小 `required_domains` 不得提高任何 claim 的 confidence**（對應 P0 的回歸測試）
- 報告包含 `skipped_domains` 說明
- Citation Gate 仍為 PASS
- offline fallback 仍能產出全部六項提交物

---

## 7. 建議時機

**P0（`domain_coverage` 分母）**：**已完成**，見第 2.4 節。獨立於本提案實作，
未觸碰採集層與 planner。

**P1（plan → collection 接線）**：建議等 AWS 部署收束後再動。理由：
`GoToAWS0801` 上有並行的部署工作，且競賽規則規定「最後 90 分鐘不得加入新功能」。

---

## 8. 明確不做

- 不讓 LLM 決定訊號權重、立場或任何分數
- 不新增資料來源
- 不做多輪追問（`stop_conditions.max_followup_rounds` 保持 1）
- 不改 Web UI
- 不重寫 Collector 或 Orchestrator

---

## 附錄：本評估的驗證方式

所有數字來自實際執行，非推估。

```bash
# 完整測試套件（評估時基線）
python3 -m unittest discover -s tests
# → Ran 541 tests, OK

# 三題離線對照
python3 -c "
from pathlib import Path
from src.orchestrator import run
run('ETH', '分析近期市場狀況、主要驅動因素與風險', Path('outputs-q1'), live=False, use_llm=False)
run('ETH', '如果聯準會九月降息，ETH 會受益嗎？', Path('outputs-q2'), live=False, use_llm=False)
run('ETH', '比較 ETH 過去 90 天的鏈上鎖倉量與價格走勢是否背離', Path('outputs-q3'), live=False, use_llm=False)
"

# live 對照（11 來源全部 success，5.4 秒）
python3 -c "
from pathlib import Path
from src.orchestrator import run
run('ETH', '近期市場狀況、主要驅動因素與風險是什麼？', Path('outputs-live-check'),
    live=True, use_llm=False, history_path=Path('data/ETH.csv'), fulltext=True)
"
```

`outputs-q1`／`q2`／`q3`／`outputs-live-check` 受 `.gitignore` 的 `outputs-*/` 忽略，
本文引用的數值已內嵌於上方各節，不依賴這些目錄存在。
