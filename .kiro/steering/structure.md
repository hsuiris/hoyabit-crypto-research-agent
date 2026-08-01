# 專案結構

```
src/
  app.py             網頁伺服器與所有頁面渲染（首頁、研究報告、比較、回測）
  orchestrator.py    三階段管線、訊號盤點、立場判定、離線推理、報告產出
  day2_sources.py    11 個資料 adapter、6 個平行採集 Agent、全文爬取
  llm.py             Gemini／OpenAI 適配器：結構化推理 + Critic 稽核
  vegas_strategy.py  Vegas 通道 + RSI 策略引擎
  backtest.py        Walk-forward 回測
  comparison.py      雙幣比較（流動性／風險敞口／關注度）
  ohlcv.py           5 年日線 CSV 載入與多期間統計
  validation.py      證據結構與可追溯性驗證
  errors.py          輸入驗證
  day1_mvp.py        Evidence 資料類別與離線 fixture
tests/               117 個測試
data/                5 年日線 CSV（BTC/ETH/SOL/BNB/XRP，各 1826 天）
demo-fixtures/       離線 Demo 用的固定輸出（勿當成產生物刪除）
docs/                README、專案報告、Demo 腳本、AWS 架構
aws/                 CloudFormation 範本與部署腳本
```

## 各檔案的職責邊界

- **資料層**（`day2_sources.py`）只負責取得與正規化，不做判斷。
- **分析層**（`orchestrator.py`、`vegas_strategy.py`）做確定性計算，不呼叫 LLM 以外的外部服務。
- **推理層**（`llm.py`）只做敘事與稽核，不決定立場方向。
- **呈現層**（`app.py`）不改變任何資料，只負責渲染。

修改時請維持這個邊界。特別是：**不要把判斷邏輯寫進 `app.py`**，也不要讓 `day2_sources.py`
產生方向性結論。

## 資料契約（不要隨意更動）

`Evidence` 必要欄位：`evidence_id`、`source`、`source_url`、`fetched_at`、`data_type`、`coin`、
`time_range`、`content`、`content_reference`、`related_claim`、`reliability_score`。

`validation.py` 會強制檢查，缺欄位或可靠度超出 0–1 會直接中止流程。

## 測試慣例

使用標準庫 `unittest`。測試檔以 `test_dayN_*.py` 命名，對應開發階段。
新增功能時務必涵蓋：正常路徑、空資料、逾時／失敗降級三種情況。

## 競賽基線驗收邊界

T0 baseline commit：`b38c43369efbf27aeff841b4ca0e15fd9db22aa0`。基線驗證產物位於被忽略的 `outputs-t0-baseline/`，包含 `report.md`、`evidence.json`、`execution_log.json`；固定展示 fixture 位於 `demo-fixtures/`，不可誤刪。新增功能應維持既有目錄職責邊界，且先以 `python3 -m unittest discover -s tests -v` 確認基線沒有回歸。