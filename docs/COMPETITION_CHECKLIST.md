# 競賽驗收檢查清單（T8 Competition Readiness）

> 本清單用於正式展示或賽事提交前的最終驗證。須將勾選項全數完成或明確標註理由。

---

## 第一階段：開發凍結前（啟動前 1 天）

### 代碼與測試

- [ ] 完整測試套件通過：`python3 -m unittest discover -s tests` → 267 tests, 267 passed
- [ ] 無 regression：與 T0 baseline commit `b38c43369efbf27aeff841b4ca0e15fd9db22aa0` 對比
- [ ] 離線工作流程驗證通過：三項核心提交物（`report.md`、`evidence.json`、`execution_log.json`）產出無誤
- [ ] 五幣 smoke 測試：BTC、ETH、SOL、BNB、XRP 各執行一次離線分析
- [ ] Web UI smoke 通過：首頁、研究摘要、互動圖表、執行流程、回測頁均可訪問
- [ ] 比較模式 smoke 通過：`/compare?coin1=BTC&coin2=ETH` 類似頁面正常載入

### 安全檢查

- [ ] 無 API key / AWS 金鑰 / 密碼在 committed 檔案中
- [ ] `.env` 使用 `.env.example` 作範本，未提交 `.env` 本身
- [ ] `.gitignore` 含 `.env`、`outputs-*/`、`demo-fixtures/` 等敏感目錄
- [ ] 所有 mock 測試完整（不依賴真實外部服務呼叫）

### 文件準備

- [ ] `docs/DEMO_RUNBOOK.md` 已補全：環境、命令、故障應對、限制、檢查清單
- [ ] `docs/COMPETITION_CHECKLIST.md` 已補全（本檔案）
- [ ] `README.md` 已更新：專案目標、支援幣種、運行方式
- [ ] 所有文件使用繁體中文（zh-Hant），英文保留為必要技術術語與命令
- [ ] 無過期的虛假承諾或未實現功能在文件中

### Artifact 與 Fixtures

- [ ] `demo-fixtures/report.md` / `evidence.json` / `execution_log.json` 格式最新
- [ ] 若新增 fixtures 路徑，已在 `.gitignore` 中排除可重建產物（`outputs-*/`）但保留固定 Demo 檔
- [ ] Manifest 校驗機制已測試（若已實現 T5 與 T6）

### Git 準備

- [ ] 當前 branch：`hackathon/competition-ready`
- [ ] 最新 commit 有清晰的 message，形如 `test(T8): freeze competition-ready demo`
- [ ] 沒有未 commit 的重要變更（`git status` 應為 clean 或只含 `.env`）
- [ ] 可從 commit hash 重現當前演示狀態

---

## 第二階段：正式運行前（展示前 2 小時）

### 環境確認

- [ ] Python 版本：`python3 --version` → 3.10 以上（或若 3.9.6 已驗證可行）
- [ ] 依賴完整：所有 import 皆來自 Python 標準庫（無外部 pip 套件）
- [ ] 資料檔完整：`data/*.csv` 五個幣種檔案存在，大小 > 100 KB 各
- [ ] 網路連線正常（若使用 live 模式）

### LLM 與配額確認

- [ ] 若使用 Gemini：API key 有效，剩餘配額 ≥ 18 次（≈6 次完整分析）
- [ ] 若使用 OpenAI：key 有效，帳戶有正預付金或信用卡
- [ ] 若使用 Bedrock：AWS 帳戶、region、model ID 已驗證可訪問
- [ ] 若無 LLM 金鑰：已驗證 offline fallback 正常運作

### 現場軟體檢查

- [ ] `python3 -m src.app` 可啟動，首頁 HTTP 200
- [ ] 首頁標題「HOYA BIT 市場研究 Agent」正確顯示
- [ ] 五幣選擇器（BTC / ETH / SOL / BNB / XRP）可點擊
- [ ] 問題輸入框與「開始研究」按鈕正常
- [ ] 結果分頁標籤齊全：研究摘要、互動圖表、執行流程、回測

### 演示內容準備

- [ ] 五分鐘講稿已準備（參考 `docs/demo-script.md`）
- [ ] 已決定展示題目：
  - [ ] 多源題（如「分析 BTC 過去兩週市場表現」）
  - [ ] 假設題（如「市場認為 ETH 短期將維持盤整，請蒐集支持與反對證據」）
  - [ ] 比較題（如「比較 SOL 與 BNB 的流動性與風險敞口」）
- [ ] 已預先執行這些題目一次（確保結果在 outputs 或快取中）
- [ ] 已記錄典型耗時（通常 5–30 秒）

### 備援準備

- [ ] 離線備援方案已測試：
  ```bash
  python3 -c "from pathlib import Path; from src.orchestrator import run; run('ETH', '備援查詢', Path('outputs-backup'), live=False, use_llm=False)"
  ```
- [ ] 備援結果已保存至 `outputs-backup/`
- [ ] 若 live 失敗，已知如何快速切換到備援檔案
- [ ] 已準備降級說詞：「API 暫不可用，切換到離線模式，報告照樣產出」

---

## 第三階段：正式運行中（運行期間）

### 監控檢查

- [ ] 研究在 900 秒內完成（競賽正式上限）
- [ ] 執行流程分頁顯示三階段時間預算，實際耗時遠低於上限
- [ ] 平行 Agent 進度表顯示 6 個 agent 狀態與各自耗時
- [ ] 每個來源標註狀態：success / fallback / missing

### 結果驗證

- [ ] 立場標籤（偏多／中性／偏空）已展示，配有信心分數
- [ ] 立場下方有「依據」說明（推力、淨優勢百分比）
- [ ] 至少 3 項事實（EV-XXX）有 evidence ID 與來源 URL
- [ ] 「反方證據」區塊非空（系統主動挑選反向訊號）
- [ ] 獨立稽核區塊有內容（Critic agent 的分析與信心調整）
- [ ] 資料來源清單完整（編號、來源名、時間戳、URL）

### 故障應對

若出現故障（API 失敗、LLM 超時、網頁卡住）：

- [ ] 立即檢查 Execution Log（執行流程分頁或 `execution_log.json`）
- [ ] 若日誌顯示 `offline_fallback`：正常降級，報告已自動轉用確定性推理
- [ ] 若無任何輸出：
  - 停止等待（超 60 秒）
  - 手動切換到備援檔（`outputs-backup/`）
  - 向評審說明：「此次 live 呼叫超時，已使用預備的離線結果展示」
- [ ] 若網頁 500 錯誤：查 terminal 錯誤訊息，可能需重啟 server

### 現場示範要點

- [ ] 展示「立場不是模型自由生成」：展開推力與 evidence ID，指出同一批證據永遠得同一方向
- [ ] 展示「證據可點回原始來源」：點擊一個 evidence ID，確認能開啟該新聞、公告或資料源
- [ ] 展示「單一失敗不中斷」：(若當時有失敗) 指出 Execution Log 記錄了失敗與降級
- [ ] 展示「報告結構」：事實 → 推論 → 反證 → 信心 → 稽核（按 demo-script.md 順序）
- [ ] **必講限制**：回測樣本數 1–3 筆不足證明，新聞全文抓取僅 2 個白名單網域，鏈上資料受 API 限制

---

## 第四階段：正式運行後

### 成果驗證

六項提交物（目前三項完整產出）：

已完成產出：
- [ ] `report.md` 存在，內容包含所有標準區塊
- [ ] `evidence.json` 存在，每筆 evidence 含 `evidence_id`、`source`、`source_url`、`fetched_at`
- [ ] `execution_log.json` 存在，記錄三階段時間、provider、fallback 使用狀況

待 T2、T5 完成後產出：
- [ ] `research_plan.json` 存在（T2 接線後）
- [ ] `claims.json` 存在（T4 接線後）
- [ ] `manifest.json` 存在，包含所有檔案的 SHA256 hash（T5 接線後）

### 內容驗證

**Report 檢查**：
- [ ] 標題含幣種與問題
- [ ] 立場、事實、推論、反方、信心、稽核、來源各自成段
- [ ] 腳註編號連續（[1]、[2]、[3]... 或 EV-001、EV-002...）
- [ ] 沒有幻覺證據（未在 evidence.json 中出現的 ID）

**Evidence 檢查**：
- [ ] 總數 ≥ 5 筆
- [ ] 每筆都有 `source_url` 或其他可溯源欄位
- [ ] `fetched_at` 時間戳合理（距現在 ≤ 14 天）
- [ ] `reliability_score` 在 0–1 之間
- [ ] 無重複的 `evidence_id`

**Execution Log 檢查**：
- [ ] 三階段預算與實際耗時記錄清晰
- [ ] Provider 信息與 model ID 正確
- [ ] 若有 fallback，記錄理由（timeout / invalid JSON / quota / offline）
- [ ] 沒有隱藏的警告或錯誤

### 檔案備份

- [ ] 完整輸出目錄已備份（例如 `outputs-final/` 或帶時間戳的目錄）
- [ ] 備份包含六項產物（目前三項，待 T5 完成後補全）
- [ ] 備份位置已告知評審或保存至安全位置

### Hash 與完整性驗證

若已實現 T5（manifest 與 hashing）：

- [ ] 計算 manifest 中每個檔案的 SHA256：
  ```bash
  sha256sum report.md evidence.json execution_log.json
  ```
- [ ] 結果與 manifest 中的 hash 值一致
- [ ] manifest 本身的 hash 已記錄（用於驗證運行完整性）

### 後續優化建議（非必要，紀錄用）

- [ ] 觀察到的瓶頸（如某個 source adapter 總是最慢）
- [ ] 新聞全文抓取的白名單可擴充為（Coindesk / BeInCrypto / ...）
- [ ] 鏈上資料可增加（MEV / gas 成本 / ...）
- [ ] Critic 稽核的置信度規則可微調
- [ ] 回測框架可加入其他策略對比

---

## 第五階段：Demo 備援與演練

### 離線備援方案

- [ ] 已預先產出完整的離線分析結果（`outputs-offline-backup/`）
- [ ] 結果涵蓋三個題型（多源 / 假設 / 比較）
- [ ] 若 live 失敗，可在 < 30 秒內切換到備援展示

### Live 成功備份

若 live 分析成功：

- [ ] 已保存完整成功的輸出（`outputs-live-success/`）
- [ ] 記錄 provider / model / 耗時 / 配額變化
- [ ] 可重現該成功結果（相同題目、相同金鑰、相同 commit）

### 比較功能備援

若展示對比模式（如 SOL vs BNB）：

- [ ] 已預先產出對比分析（`outputs-comparison/`）
- [ ] 對比結果含流動性、風險敞口、市場關注度各項
- [ ] 若對比 live 失敗，有離線版本備援

### 演練與時間控制

- [ ] 完整演示已過排練一次（計時 ≤ 5 分鐘）
- [ ] 各分頁切換已排練（無卡頓）
- [ ] 故障處置已排練（如何快速切備援、說詞）
- [ ] 已準備備選題目（若主題目失敗，可快速用備選題切換）

---

## 第六階段：最終提交

### 代碼与狀態

- [ ] Git status 清潔（所有變更 committed 或在 `.gitignore` 中）
- [ ] 最新 commit 是 T8 freeze commit，message 清晰
- [ ] Tag 已建立：`competition-demo-ready`
  ```bash
  git tag competition-demo-ready
  git log --oneline -1  # 確認 commit hash
  ```

### 文件完整

- [ ] `docs/DEMO_RUNBOOK.md` 完整，所有命令已驗證可執行
- [ ] `docs/COMPETITION_CHECKLIST.md` 完整（本檔案），所有項目已逐項確認
- [ ] `README.md` 含運行方式與已知限制
- [ ] `SETUP.md` 若存在，已更新與驗證

### 已知限制文件

- [ ] `docs/COMPETITION_BLOCKERS.md` 已更新，記錄所有已知的邊界例外或待解決項
- [ ] T2、T3、T4、T5 尚未完成的任務已在文件中明確標示
- [ ] 三個尚未產出的提交物（research_plan.json / claims.json / manifest.json）已在清單中標示「待 Tn 完成」

### 提交檢查

- [ ] README 中明確指出支援幣種、時間窗口、資料來源
- [ ] 無 hardcode 的 API key、AWS key、競賽 access code
- [ ] 所有測試命令與預期結果已在文件中記錄
- [ ] 若有環境變數需設置，已在 `.env.example` 中列舉（不含實際值）

### 終極檢查（正式提交前 15 分鐘）

- [ ] `python3 -m unittest discover -s tests` → 267 passed ✓
- [ ] `python3 -c "from pathlib import Path; from src.orchestrator import run; run('ETH', 'final check', Path('outputs-final-check'), live=False, use_llm=False)"` → 三檔產出 ✓
- [ ] `python3 -m src.app` → 首頁加載 ✓
- [ ] 五幣均可選 ✓
- [ ] 論文與幻燈片已準備，無對代碼實現的虛假描述 ✓
- [ ] 所有團隊成員已了解 demo 流程與應急措施 ✓

---

## 補充說明

### 已知的邊界例外

以下項目已記錄於 `docs/COMPETITION_BLOCKERS.md`，不阻止展示但需誠實披露：

1. **T0.5 例外**：`src/app.py` 的 `_summarize_sources()` 仍直接呼叫 Gemini/OpenAI
   - 改用 Bedrock 時會回「未支援」並退回標題清單
   - 待 T6 與 LLMClient 改接後修復

2. **T0.6 例外**：Evidence 新增 11 個欄位
   - 既有 key 值與 report.md 內容經驗證無變
   - evidence.json 格式因此多出 11 個 key

3. **環境限制**：Python 3.9.6（低於 3.10+ 要求）
   - 目前可用，但正式環境應升至 3.10+

### 三項核心提交物的格式

當前實現的三項提交物：

```text
report.md:
  # <幣種> Market Research
  ## Question
  <研究問題>
  ## Stance
  <方向>（信心分）
  ## Market Judgment
  ...
  ## Facts
  - EV-001: ...
  ...
  ## Inferences
  ...
  ## Counter Evidence
  ...
  ## Confidence Limiters
  ...
  ## Execution & Critic Review
  ...
  ## Sources
  [1] <來源>
  ...

evidence.json:
  [
    {
      "evidence_id": "EV-001",
      "source": "CoinGecko",
      "source_url": "https://...",
      "fetched_at": "2026-08-01T...",
      "data_type": "market_price",
      "coin": "ETH",
      "time_range": "14_days",
      "content": "...",
      "reliability_score": 0.95,
      ...
    },
    ...
  ]

execution_log.json:
  {
    "run_id": "...",
    "coin": "ETH",
    "question": "...",
    "started_at": "...",
    "ended_at": "...",
    "stages": [
      {
        "name": "collection",
        "budget_seconds": 420,
        "actual_seconds": 2.1,
        "status": "completed"
      },
      ...
    ],
    "providers_used": ["gemini", "offline_fallback"],
    "evidence_count": 9,
    "report_confidence": 0.55,
    ...
  }
```

### 尚未產出的三項提交物

待 T2、T4、T5 完成後新增：

```text
research_plan.json:
  {
    "question": "...",
    "hypotheses": [ {...}, {...} ],
    "task_mode": "market_judgment" 或其他七種模式,
    "time_window": {...}
  }

claims.json:
  {
    "claims": [
      {
        "claim_id": "...",
        "text": "...",
        "claim_type": "fact" / "inference" / "forecast",
        "confidence": 0.75,
        "supporting_evidence": ["EV-001", "EV-003"],
        "contradicting_evidence": ["EV-005"],
        ...
      },
      ...
    ]
  }

manifest.json:
  {
    "run_id": "...",
    "execution_time": 15,
    "files": [
      {
        "filename": "report.md",
        "size_bytes": 4700,
        "sha256": "abc123..."
      },
      ...
    ],
    "total_hash": "xyz789..."
  }
```

---

## 最後確認

- [ ] 我已逐項檢查以上清單
- [ ] 所有 MUST-HAVE 項（測試、文件、備援）已完成
- [ ] 所有已知限制已誠實披露
- [ ] 我已準備好現場演示與應急方案

**簽署（如適用）**：

```text
Date: _______________
Presenter: _______________
Reviewer: _______________
```
