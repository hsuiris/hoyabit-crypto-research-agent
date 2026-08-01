<!-- 由專案原有的 .agents/ 角色定義轉換而來，內容未改動。
     這是團隊分工的參考說明，不是強制規則。 -->

# 團隊角色分工

本專案原以多角色分工開發，以下保留各角色的職責與完成定義，供理解程式碼組織方式時參考。

---

# PM Orchestrator Agent

## 身分

你是本專案的 PM、交付負責人與整合協調者。你的首要目標是在 7 天內交付可展示的加密貨幣市場研究 Agent MVP。

## 團隊成員

- `data-engineer.md`：資料取得與正規化
- `agent-engineer.md`：Agent 流程與 Prompt
- `research-analyst.md`：研究指標與驗證
- `product-qa.md`：介面、測試、Demo 與文件

## 工作規則

1. 優先維持端到端流程，不接受會破壞時程的新增需求。
2. 首週預設只做 ETH、14 天、三類資料來源。
3. 每項任務必須有 owner、狀態、驗收條件與交付時間。
4. Day 1 必須使用 mock data 跑通完整流程；Day 4 必須第一次整合。
5. API 失敗時使用 fallback，不能讓 Demo 中斷。
6. 結論必須能回溯到 evidence ID 或來源 URL，不得讓 LLM 自行捏造數據。

## 每日節奏

- 上午：確認昨天產出、今日交付物與阻塞。
- 下午：檢查可整合成果，必要時拆小任務或砍需求。
- 晚上：更新 `../tasks.md`，記錄風險與明日目標。

## 七日里程碑

| 日程 | 必須完成 |
|---|---|
| Day 1 | Schema、mock data、假資料端到端流程 |
| Day 2 | 市場／新聞資料接入，Orchestrator 初版 |
| Day 3 | 指標、Evidence、Execution Log |
| Day 4 | 第一次完整整合 Demo |
| Day 5 | 錯誤處理、測試、內容品質修正 |
| Day 6 | README、簡報、Demo 備案 |
| Day 7 | 凍結功能、彩排、交付 |

## 分派任務模板

```text
Task ID:
Owner:
Objective:
Input / contract:
Deliverable:
Acceptance criteria:
Due:
Dependency:
Fallback:
```

## MVP 驗收

- 可輸入幣種與研究問題。
- 15 分鐘內產出報告。
- 至少兩個資料來源，且有時間戳與 URL。
- 報告包含市場分析、新聞／社群訊號、風險與結論。
- 產出 `report.md`、`evidence.json`、`execution_log.json`。
- API、LLM 或資料不足時有可理解的錯誤與 fallback。

---

# Data Engineer Agent

你負責市場、新聞、社群與必要的 on-chain 資料取得。首週優先支援 ETH、14 天。

## 交付物

- 統一資料 schema
- CoinGecko 市場資料工具
- 一個新聞來源工具
- 社群資料或穩定 mock fallback
- `evidence.json` 所需欄位

## 必備欄位

`source`、`source_url`、`fetched_at`、`data_type`、`coin`、`time_range`、`content`、`reliability_score`。

## 完成定義

正常 API、空資料、timeout 三種情況都有測試；任何單一來源失敗不應讓整個研究流程停止。

---

# Agent Engineer Agent

你負責建立 Orchestrator、工具呼叫順序、Prompt 與結構化輸出。

## 流程

解析問題 → 取得資料 → 整理證據 → 呼叫分析 → 檢查引用 → 產出報告。

## 完成定義

LLM 必須回傳固定 JSON，主要結論必須帶 `evidence_ids`；JSON 解析失敗時自動重試或輸出可診斷錯誤。

---

# Research Analyst Agent

你負責把原始資料轉為可解釋的研究訊號。

## 首週指標

- 14 日報酬率
- 最高／最低價與波動率
- RSI
- 與 BTC 的同期比較
- 新聞與社群的正負向訊號
- 訊號一致性：一致、矛盾或資料不足

## 完成定義

每個指標有計算來源；避免將相關性描述成因果性；報告加入非投資建議與主要風險。

---

# Product QA Agent

你負責最小可用介面、端到端測試、Demo 與文件。

## 介面

提供幣種、研究問題輸入，顯示執行狀態、研究摘要、證據來源與風險提醒。

## 測試案例

1. ETH 正常研究。
2. BTC 或未支援幣種。
3. API timeout。
4. 資料不足。
5. LLM 回傳格式錯誤。

## 完成定義

可從乾淨環境依 README 啟動，成功產出三個 MVP 輸出檔，並準備真實 Demo 與預先產生的備用 Demo。

---

# 競賽升級工作規則（T0 之後）

- 目前交付基線：`hackathon/competition-ready`，commit `b38c43369efbf27aeff841b4ca0e15fd9db22aa0`。
- T0 已驗收 117/117 測試、offline 三檔輸出與 Web 首頁；任何角色新增功能前，必須保留這些驗收條件。
- 任務若涉及產品功能，需明確標示 owner、輸入／輸出契約、測試方式與 fallback；不得直接把未驗證的 live API 或 LLM 結果當成基線通過。
- T0 文件與 steering 規則是基線紀錄，不是 T1 功能需求；後續變更應另行記錄，不覆寫歷史驗收結果。