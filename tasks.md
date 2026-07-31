# MVP 任務看板

狀態：`Todo`、`Doing`、`Blocked`、`Review`、`Done`

| ID | 任務 | Owner | 狀態 | 驗收條件 |
|---|---|---|---|---|
| T1 | 建立資料 schema 與 API fallback | data-engineer | Review | CoinGecko／RSS adapter 與 fallback 已完成 |
| T2 | 建立 Orchestrator 與 Prompt | agent-engineer | Review | 初版 Orchestrator 已完成；LLM Prompt 後續加強 |
| T3 | 完成報酬率、RSI、波動率 | research-analyst | Review | 指標與單元測試已完成 |
| T4 | 建立輸入／結果頁面與 Demo | product-qa | Review | 標準庫 Web Demo 已完成 |
| T5 | 端到端整合與報告輸出 | pm-orchestrator | Review | Day 4 已串接指標、Evidence、報告 |

## 每日更新格式

```text
[日期] [Agent]
- Done:
- Next:
- Blocked:
- 需要 PM 決策：
```

## Day 1 驗收紀錄

- 狀態：通過
- 測試：`1 test` passed
- 已驗證：輸入 ETH 與研究問題後，成功產出 `report.md`、`evidence.json`、`execution_log.json`
- 已驗證：三筆 evidence 均有來源、時間、資料類型與可靠度欄位
- 已知限制：目前使用 mock data；真實 API 與 LLM Prompt 排入 Day 2

## Day 2 驗收紀錄

- 狀態：通過（離線驗收）
- 已完成：CoinGecko adapter、Google News RSS adapter、offline fallback、Orchestrator 初版
- 驗收指令：`python -m unittest discover -s tests -v`
- 測試結果：2 tests passed
- 注意：真實 API 連線需在有網路環境執行 `collect_evidence(live=True)` 再做一次人工驗證

## Day 3 驗收紀錄

- 狀態：通過
- 已完成：RSI、報酬率、波動率、Evidence 欄位／引用驗證、分步 Execution Log 與耗時
- 測試結果：4 tests passed；端到端輸出驗證成功

## Day 4 驗收紀錄

- 狀態：通過
- 已完成：資料、指標、Evidence、報告與 Web Demo 串接
- 測試結果：5 tests passed；完整 pipeline 輸出驗證成功
- Demo：`python -m src.app`，開啟 `http://127.0.0.1:8000`

## Day 5 驗收紀錄

- 狀態：通過
- 已完成：輸入驗證、可理解錯誤訊息、來源連結、風險限制、異常情境測試
- 測試結果：7 tests passed

## Day 6／7 驗收紀錄

- 狀態：通過
- 文件：README、安裝、API Key、Demo、架構圖、限制、5 分鐘講稿
- 備援：`demo-fixtures/` 已準備離線 Demo 輸出
- 彩排：依 `docs/day7-checklist.md` 執行；功能凍結後不再新增需求

## Competition-readiness upgrade

- 已完成：五種競賽幣種輸入、OHLCV CSV loader、CoinGecko 真實價格 adapter、Etherscan/BTC 鏈上 adapter、完整 Evidence Schema、結構化 LLM reasoning adapter
- 已驗收：10 tests passed
- 真實模式煙霧測試：流程成功，但本機網路政策使三個外部來源均 fallback；Execution Log 已明確記錄 fallback 原因
- 待現場設定：主辦方 OHLCV CSV、`OPENAI_API_KEY`、可用的鏈上／新聞網路環境

## Production-readiness execution

- 真實外部 API：CoinGecko、Google News RSS、Ethereum chain、Hacker News Algolia 均已 live 驗證成功
- 真實 OHLCV：BTC、ETH、SOL、BNB、XRP 各 1,826 筆；來源為 Binance public market-data，明確標記為非主辦方資料包
- 社群：Reddit／Bluesky 被 HTTP 層阻擋時，自動切換 Hacker News Algolia；live 驗證 25 筆成功
- AWS：Lambda + Function URL + CloudFormation + Secrets Manager 設計與 216KB deployment package 已完成；部署被未設定 AWS credentials 阻擋
- LLM：Responses API adapter 與失敗 fallback 已完成；真實呼叫被未設定 `OPENAI_API_KEY` 阻擋
- Demo：新版 Web UI 已用瀏覽器執行真實四來源流程，完成首頁與結果備援截圖；正式影片仍需使用本機 Game Bar 錄製
