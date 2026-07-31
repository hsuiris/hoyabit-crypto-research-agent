# 正式 Demo 錄影 Runbook

## 錄影前

1. 將五種主辦方 CSV 放入 `data/BTC.csv`、`ETH.csv`、`SOL.csv`、`BNB.csv`、`XRP.csv`。
2. 設定 `OPENAI_API_KEY`；若未設定，畫面需明確說明為離線推理 fallback。
3. 執行 `python -m unittest discover -s tests -v`。
4. 執行 `scripts/run_demo.ps1`。

## 三分鐘錄影順序

1. 顯示測試全部通過。
2. 開啟 `http://127.0.0.1:8000`。
3. 輸入指定幣種與臨時題目。
4. 顯示 Market Judgment、Facts、Inferences、Conclusion、Confidence。
5. 顯示 Evidence URL、fetched_at、content_reference、related_claim。
6. 顯示 Execution Log 的資料來源成功／fallback 與耗時。
7. 顯示 `report.md`、`evidence.json`、`execution_log.json`。

Windows 可用 `Win+Alt+R` 開始或停止 Game Bar 錄影。錄影完成後檢查聲音、游標、輸入與最終輸出都清楚可見。
