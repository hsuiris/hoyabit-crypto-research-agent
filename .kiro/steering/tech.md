# 技術約束

## 最重要的一條：零第三方相依

**只使用 Python 標準函式庫。** HTTP 用 `urllib`、XML 用 `xml.etree`、網頁用 `http.server`、
平行化用 `concurrent.futures`、指標與 EMA 全部自己實作。

不要引入 requests、pandas、numpy、pandas-ta、TA-Lib、LangChain、FastAPI 或任何套件。
這不是風格偏好，而是部署約束：Lambda 打包沒有相依衝突、沒有 C 擴充編譯問題、冷啟動快，
任何人 clone 下來就能 `python -m src.app` 直接跑。新增功能時若覺得需要套件，請先找標準庫的做法。

需要 Python 3.10 以上。

## 常用指令

```bash
python -m unittest discover -s tests    # 全部測試（應為 117 passed）
python -m src.app                       # 啟動網頁，http://127.0.0.1:8000
```

回測頁在 `/backtest?coin=ETH`。

## LLM 設定

複製 `.env.example` 為 `.env`（**絕不要提交**）：

```
LLM_PROVIDER=gemini
GEMINI_API_KEY=your_key_here
GEMINI_MODEL=gemini-3.6-flash
```

未設定金鑰時系統會使用確定性離線推理，功能完整、報告照樣產出，只是執行記錄標為 `offline_fallback`。

**配額警告**：免費方案每日 20 次呼叫，每次分析消耗 **3 次**（結構化推理、消息面摘要、Critic 稽核），
因此每天約只能跑 6 次完整分析。測試前端改動時請用離線模式（`live=False, use_llm=False`），
把真實呼叫留給最終驗證。

## 架構速覽

三階段管線，各有獨立時間上限（合計 720 秒，低於比賽的 15 分鐘）：

| 階段 | 上限 | 內容 |
|---|---|---|
| ① 蒐集 | 7 分鐘 | 6 個領域 Agent 平行採集 11 個來源，新聞面爬取全文 |
| ② 整理 | 3 分鐘 | LLM 結構化推理、指標計算、報告產出 |
| ③ 稽核 | 2 分鐘 | Critic 獨立稽核分析與其證據 |

**上限是天花板不是目標**：每階段做完立刻進入下一階段，實測整趟約 6 秒。

## 修改時的注意事項

- **證據順序不可隨意更動**：腳註編號依證據在清單中的位置決定，平行採集後會重新排回
  `_SOURCE_LOADERS` 的固定順序。若順序浮動，讀者看到的引用編號會在每次執行間跳動。
- **Critic 只能標註與下調信心**：`confidence_adjustment` 僅接受 -1 到 0，正值會被拒絕。
  不要讓稽核改寫結論 —— 會改寫的稽核等於第二個分析師。
- **送給 LLM 的證據會移除長數列**（`_llm_evidence_payload()`），文字內容完整保留。
  這讓 prompt 從 62k 降到 21k 字元。新增含長序列的來源時記得比照處理。
- **爬取前必須檢查 robots.txt**，且僅限 `FULLTEXT_DOMAINS` 白名單。

## T0 基線驗證與升級規則

競賽升級從 `hackathon/competition-ready` 的 T0 baseline commit `b38c43369efbf27aeff841b4ca0e15fd9db22aa0` 開始。基線完整測試為 117 passed、0 failed、0 errors；offline smoke test 必須繼續使用 `live=False, use_llm=False`，並產出三個標準檔案。基線機器的 `python3` 是 3.9.6，低於本專案 3.10+ 要求；新驗證環境應使用 Python 3.10 以上。