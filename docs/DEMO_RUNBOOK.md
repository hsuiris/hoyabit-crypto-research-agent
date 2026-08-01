# Demo Runbook — HoyaBIT 加密貨幣市場研究 Agent

> 本文件用於現場快速啟動、驗證與展示。指令均已在 macOS 環境（python3 3.9.6）實際執行驗證。

## 環境準備

### 依賴版本

```text
Python:    python3（3.9.6；專案要求 3.10+）
Branch:    hackathon/competition-ready
Commit:    最新；見 git log
AWS:       （若使用 Bedrock）指定 region 與 model ID
LLM:       Gemini（预设）、OpenAI、Bedrock、offline fallback
```

### 金鑰設置（若使用 LLM）

複製 `.env.example` 為 `.env`（**絕不要 commit**）：

```bash
cp .env.example .env
```

編輯 `.env`，設置以下任一組合：

```text
# Gemini（預設；免費方案每日 20 次呼叫，每次分析消耗 3 次）
LLM_PROVIDER=gemini
GEMINI_API_KEY=your_key_here
GEMINI_MODEL=gemini-3.5-flash

# 或 OpenAI
LLM_PROVIDER=openai
OPENAI_API_KEY=your_key_here
OPENAI_MODEL=gpt-4o-mini

# 或 AWS Bedrock
LLM_PROVIDER=bedrock
AWS_REGION=us-east-1
BEDROCK_MODEL_ID=anthropic.claude-3-5-sonnet-20241022-v2:0
```

若無金鑰或金鑰配額用盡，系統會自動切換到 **offline fallback** 模式（確定性推理，無 LLM 呼叫）。

## 完整測試套件

確認基線未回歸：

```bash
python3 -m unittest discover -s tests -v
```

預期結果：**534 tests, 534 passed, 0 failed, 0 errors**

> 數量隨 Task 增加：T0 基線 117、T0.6 後 267、T5 後 488、T6 後 495、T7 後 534。以 `docs/COMPETITION_TASK_STATUS.yaml`
> 的 `full_suite_latest` 為準。

## 離線 Smoke 測試

驗證完整工作流程（不依賴外部 API 或 LLM）：

```bash
python3 -c "
from pathlib import Path
from src.orchestrator import run
output_dir = Path('outputs-demo')
output_dir.mkdir(exist_ok=True)
run('ETH', '離線 smoke test', output_dir, live=False, use_llm=False)
"
```

預期產出（T5 後為完整六項提交物）：

- `outputs-demo/report.md`
- `outputs-demo/evidence.json`
- `outputs-demo/execution_log.json`
- `outputs-demo/research_plan.json`
- `outputs-demo/claims.json`
- `outputs-demo/manifest.json`

驗證 manifest 的 SHA-256 與實際檔案相符：

```bash
python3 -c "
import hashlib, json
from pathlib import Path
out = Path('outputs-demo')
manifest = json.loads((out / 'manifest.json').read_text(encoding='utf-8'))
print('run_id:', manifest['run_id'], '| gate:', manifest['validation']['citation_gate_status'])
for entry in manifest['files']:
    actual = hashlib.sha256((out / entry['path']).read_bytes()).hexdigest()
    print(entry['path'], 'OK' if actual == entry['sha256'] else 'MISMATCH')
"
```

## 網頁啟動

```bash
python3 -m src.app
```

開啟瀏覽器：

```text
http://127.0.0.1:8000
```

首頁功能：

- **研究首頁**：輸入幣種（BTC / ETH / SOL / BNB / XRP）與研究問題，啟動分析
- **研究摘要分頁**：展示立場、事實、推論、反方證據、信心、資料來源、獨立稽核
- **互動圖表分頁**：5 年長期價格脈絡、消息面重點、鏈上指標（TVL）
- **執行流程分頁**：三階段時間預算、平行 Agent 進度、來源降級狀態
- **回測頁面** (`/backtest?coin=ETH`)：5 年日線 walk-forward 回測，Vegas 通道 + RSI 策略

支援幣種與切換：

- 每個分頁頂部可直接選擇 BTC / ETH / SOL / BNB / XRP
- 回測頁可用 `?coin=` 參數切換

## Live 研究執行

若要執行真實 API 與 LLM 分析（需配額）：

```bash
python3 -c "
from pathlib import Path
from src.orchestrator import run
output_dir = Path('outputs-live')
output_dir.mkdir(exist_ok=True)
run('ETH', '市場認為 ETH 短期將維持盤整，請蒐集支持與反對證據', output_dir, live=True, use_llm=True)
"
```

預期耗時：6–60 秒（實測大多 < 10 秒；平行 Agent 設計）。

### LLM 配額警告

- 免費方案（Gemini）：每日 20 次呼叫
- 每次完整分析：消耗 **3 次** 呼叫（結構化推理 + 消息面摘要 + Critic 稽核）
- 因此每天約只能執行 **6 次完整分析**

**測試 UI 改動時，請使用 `live=False, use_llm=False`** 避免消耗配額。

## 多幣種測試

支援所有五個幣種的 smoke 與 live 執行：

```bash
for coin in BTC ETH SOL BNB XRP; do
  python3 -c "
from pathlib import Path
from src.orchestrator import run
output_dir = Path('outputs-${coin}')
output_dir.mkdir(exist_ok=True)
run('${coin}', '市場狀態簡析', output_dir, live=False, use_llm=False)
  "
done
```

## 五分鐘展示流程

1. **問題輸入**（0:00–0:30）
   - 首頁輸入「ETH」與「市場認為 ETH 短期將維持盤整，請蒐集支持與反對證據」
   - 點擊「開始研究」

2. **立場與信心**（0:30–1:20）
   - 展示「研究摘要」分頁的立場判定（偏多／中性／偏空）
   - 解釋立場不是模型自由生成，而是由證據訊號**確定性加權計算**
   - 展開「這個立場是怎麼算出來的？」，顯示推力與 evidence ID
   - 强調：同一批證據永遠得到同一個立場，offline 模式也一樣

3. **支持與反方證據**（1:20–2:20）
   - 滾動展示「事實」區間（7–9 項）
   - 在「反方證據」區間停留，解釋這是系統自動挑出的反向訊號
   - 點擊一個證據 ID（如 EV-006），展示來源 URL 與時間戳

4. **互動圖表**（2:20–3:20）
   - 切換至「互動圖表」分頁
   - 滑鼠移動到長期價格圖，顯示 5 年脈絡
   - 指出近 14 天與近 1 年的對比（如「14 天 +8%，但 1 年 -30%」）
   - 顯示消息面摘要（中文重點整理）與 TVL 圖

5. **可靠性與降級**（3:20–4:00）
   - 切換至「執行流程」分頁
   - 展示「三階段時間預算」表（上限 vs 實際）
   - 展示「平行 Agent 進度」（6 個 agent、11 個來源、耗時對比）
   - **演示 fallback**：
     - 切換到 `demo-fixtures/report.md`，說明 API 全掛或 LLM 配額用盡時
     - 系統退到確定性推理，報告照樣產出、證據照樣可追溯
     - Execution Log 標記為 `offline_fallback`

6. **回測與限制**（4:00–5:00）
   - 開啟 `/backtest?coin=ETH`
   - 展示 5 年日線 walk-forward 回測，Vegas + RSI 策略
   - **重點講誠實**：
     - 樣本數只有 1–3 筆交易，**不足以證明策略有效**
     - 任何勝率都只是觀察
     - 我們展示的是「流程正確性」，不是「預測能力」
   - 切換其他幣種看規則一致性

## 備援與失敗應對

### Live 失敗切換備援

若 live 分析超時或失敗：

1. **停止等待**（約 30–60 秒後）
2. **判斷失敗類型**：
   - 若 Execution Log 寫 `offline_fallback`，報告已自動降級 → 繼續展示該報告
   - 若網頁卡住或無任何輸出 → 手動切換
3. **切換到固定備援**：
   ```bash
   # 開啟另一個終端
   cp -r demo-fixtures/* outputs-live/
   # 然後在首頁用「已保存的結果」方式展示
   ```
4. **在 Execution Log 中說明**：「此次 live 呼叫超時，已使用離線模式產出」

### 常見問題

| 問題 | 診斷 | 解決 |
|---|---|---|
| 網頁卡住／無回應 | 查 terminal：是否有錯誤訊息 | 停止 server、查環境變數、重啟 |
| 「找不到報告」 | outputs 目錄是否存在且不為空 | 重跑 smoke 或 live |
| LLM 呼叫失敗 | Execution Log 顯示 `offline_fallback` | 正常情況，系統自動降級 |
| 金鑰無效 | 網頁顯示「LLM provider 錯誤」 | 檢查 `.env` 中的 API KEY 與模型 ID |
| 社群數據空白 | Evidence 中 social sentiment 無內容 | 正常（mock 或來源暫不可用） |

## 已知限制

### 功能邊界

1. **五幣支援**：僅 BTC、ETH、SOL、BNB、XRP；其他輸入會被拒絕
2. **全文抓取**：僅涵蓋 Cointelegraph、Decrypt；其餘新聞來源為導言或標題層級
3. **鏈上資料**：大戶追蹤（Whale）僅支援 BTC / ETH / BNB
4. **時間窗口**：固定 14 天；回測採用 5 年日線（與線上運行 4H/1H 不同）

### 環境限制

5. **Python 版本**：本機 3.9.6（低於專案要求的 3.10+）
   - 使用 `python3`；`python` 命令不存在
   - 正式競賽環境應改用 3.10 以上

6. **LLM 配額**：
   - Gemini 免費方案每日 20 次呼叫，每次分析消耗 3 次 → 約 6 次分析／天
   - Bedrock 需 AWS 帳戶與配額設定

### 功能開發狀態

> 六項提交物自 T5 起全部產生（T2 接 `research_plan.json`、T4 接 `claims.json`、
> T5 接 `manifest.json` 與 citation gate）。目前仍在進行的邊界：

7. **`claims.json` 不含 run_id**：刻意如此，用來證明相同輸入產生逐字相同的 Claim；
   run 與檔案的綁定在 `manifest.json` 與 `evidence.json` 的 `run_id`
8. **語意稽核在離線模式為 deterministic 詞表偵測**：八個類別都有覆蓋，但只產生 warning，
   不改分數；LLM Critic 只在 `use_llm=True` 且有配額時執行
9. **Web UI 尚未顯示 Claim／Citation Gate**：待 T6 接線；目前需開 `claims.json`
   與 `report.md` 的「## Citation Gate」段查看

### 外部服務限制

10. **新聞來源**：Google News robots.txt 全站禁止爬取，系統遵守此限制，因此 Google News 結果只能取得標題或 snippet
11. **社群情緒**：Twitter/X、Discord、Reddit 資料需通過 API；若配額不足或服務異常，會顯示 `reliability_score 0.20`（降級証據）

### 既有邊界例外（已記錄，待後續任務處理）

12. **Web UI 中的 `_summarize_sources()` 函式**（`src/app.py`）
    - 目前仍直接呼叫 Gemini / OpenAI，不經過 Bedrock adapter
    - 這是呈現層的來源摘要功能，與核心分析三角色（Planner / Analyst / Critic）分開
    - Bedrock 無此支援時會回「未支援的 provider」並退回純標題清單
    - 待 T6 処理時改為經由 LLMClient adapter

13. **Lambda 產物落點**（`lambda_handler.py`）
    - T7 起改為每次請求一個唯一 run 目錄（`<ARTIFACT_ROOT 或容器暫存目錄>/runs/<run_id>/`），
      同一個容器連續處理兩個請求時不會互相覆寫
    - 仍寫在容器本機、尚未上傳 S3；改用 `S3ArtifactStore` 留給後續 S3 任務

## 操作檢查清單

展示前 30 分鐘：

- [ ] 確認 `python3 -m unittest discover -s tests` 通過（534 tests）
- [ ] 驗證 `data/` 目錄下有五個 `.csv` 檔（BTC / ETH / SOL / BNB / XRP）
- [ ] 若使用 LLM：檢查 `.env` 金鑰與模型 ID 有效
- [ ] 預先跑一次離線 smoke，確認輸出目錄有六個提交物，且 manifest hash 全部相符
- [ ] 確認 `report.md` 的「## Citation Gate」段為 PASS 或 PASS_WITH_WARNINGS
- [ ] 預先開啟 `/backtest?coin=ETH` 讓結果進快取（避免現場等待）

展示中：

- [ ] 首頁能正常載入，五幣選擇器工作
- [ ] 輸入研究問題後，「開始研究」按鈕回應
- [ ] 結果分頁能切換（研究摘要 / 互動圖表 / 執行流程）
- [ ] Evidence ID 點擊能開新分頁到來源 URL
- [ ] 回測頁能加載與切換幣種

展示後：

- [ ] 保存完整輸出目錄（`outputs-*/` 子目錄）
- [ ] 記錄 live / offline 成敗與耗時
- [ ] 若失敗，保存 Execution Log（用於診斷）

## 故障排查

### 常見錯誤訊息

**「目前僅支援 BTC、ETH、SOL、BNB、XRP」**
→ 幣種名稱拼寫或非支援幣種

**「研究問題不可為空白」**
→ 清空了 textarea

**「找不到資料檔」**
→ `data/BTC.csv` 等檔案遺失；需要 clone 整個 repo

**「LLM provider 錯誤」** / **「金鑰無效」**
→ 檢查 `.env`；若無金鑰，系統自動用 offline 模式

**「timeout」** / **「連線失敗」**
→ 正常，執行記錄會標 `offline_fallback`，報告仍會產出

### 日誌查看

若出現問題，檢查以下日誌：

```bash
# 離線運行的詳細日誌
python3 -c "
import logging
logging.basicConfig(level=logging.DEBUG)
from pathlib import Path
from src.orchestrator import run
run('ETH', 'test', Path('outputs-debug'), live=False, use_llm=False)
" 2>&1 | tail -100

# 網頁伺服器日誌
# 直接在啟動的 terminal 中觀察（Ctrl+C 停止）
```

## 競賽時的正式執行

正式競賽會使用 `run_formal()` 或 Lambda 入口點；該流程：

1. 經由 `RunContext` 設置內部 deadline（900 秒）
2. 產出六項提交物（T5 起全部齊備）
3. 生成 manifest 檔案與 SHA256 hash（T5 已完成）
4. 記錄完整 lineage 與降級狀態（rerun lineage 待 T7）

當前 MVP 在本文所述的 CLI 與網頁介面中已完整演示；正式競賽流程在 T7 完成後會進一步加固 deadline 管理與格式驗證。
