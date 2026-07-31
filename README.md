# 加密貨幣市場研究 Agent 團隊

這是一個一週 MVP 專案：輸入幣種與臨時研究問題，15 分鐘內產出包含市場資料、新聞／社群訊號、證據清單與風險提醒的研究報告。

## 啟動方式

1. 先閱讀 `.agents/pm.md`，由 PM Agent 建立本週任務與每日 checkpoint。
2. 由 PM 將工作分派給 `.agents/agents/` 下的五個角色 Agent。
3. 所有 Agent 將進度更新到 `tasks.md`，完成後由 PM 進行整合驗收。

## 啟動 Day 4 Web Demo

在 `agent團隊` 目錄執行：`python -m src.app`，再開啟 `http://127.0.0.1:8000`。

## MVP 範圍

- 首週優先支援 ETH，必要時再加入 BTC。
- 最近 14 天資料。
- 市場價格、新聞、社群情緒；on-chain 可先使用 mock/fallback。
- 輸出 `report.md`、`evidence.json`、`execution_log.json`。

## 團隊角色

| Agent | 主要責任 |
|---|---|
| pm-orchestrator | 分派任務、追蹤進度、整合與驗收 |
| data-engineer | API、資料正規化、fallback |
| agent-engineer | Orchestrator、工具呼叫、Prompt、結構化輸出 |
| research-analyst | 指標、訊號一致性、風險分析 |
| product-qa | UI、測試、Demo、README |
