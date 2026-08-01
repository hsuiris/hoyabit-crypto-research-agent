# Live smoke 保存位置

此目錄只保留真正以 AWS Bedrock 完成的 live 執行六項產物；絕不放入模擬或離線輸出。

## 實際結果（2026-08-01，第二次更換）

**模型路徑全程成功：analyst 與 critic 都是 Bedrock，Claim 由模型提案。**

| 項目 | 值 |
|---|---|
| run_id | `RUN-20260801T142730Z-BTC-52f95e69` |
| 執行性質 | `test`（見下方「為什麼不是 formal」） |
| 題目 | 分析 BTC 過去兩週市場表現，整合價格、鏈上、主要新聞與討論熱度，說明訊號一致程度。 |
| 執行環境 | AWS Lambda，us-west-2，Function URL |
| 線上程式 | `code_commit=f94acfb734e770a5ab44a662042c6b94ef3f0c69`（部署時由 `deploy.sh` 注入） |
| 模型 | `amazon.nova-lite-v1:0`，Converse 可用 |
| 耗時 | 46.9 秒（命題上限 900 秒） |
| planner | `provider=bedrock` |
| analyst | `provider=bedrock`，**status=success** |
| critic | `provider=bedrock`，**status=success** |
| claims | `claim_source=llm`（模型提案，verdict 與信心仍由 deterministic 程式計算） |
| Citation Gate | `PASS_WITH_WARNINGS`（0 error、0 warning） |
| CL-001 | `current_state`／**supported**／信心 0.5907（medium） |
| manifest | 五個 SHA-256 全部相符（落地後重新從磁碟計算仍相符） |
| run_status | `COMPLETED_DEGRADED`（原因見下方，非模型失敗） |

## 與前一份 fixture 的差異

前一份是 `RUN-20260801T095907Z-BTC-5c13147c`。換掉的原因不是它失敗，而是它反映的是三個
已修好的缺陷：

| | 舊 fixture | 本 fixture |
|---|---|---|
| 報告語言 | Fact／Inference／Conclusion 為英文，段落標題中英混雜 | 全篇繁體中文（20 個段落） |
| `domain_coverage` | 0.0 | 0.4 |
| CL-001 verdict | `insufficient_evidence`（信心 0.35） | `supported`（信心 0.5907） |
| Claim 是否貼題 | 「BTC 價格將在未來兩週內繼續上漲」——題目沒有要求的價格預測 | `claim_type=current_state`，直接回答題目 |
| 後續觀察重點 | 把 Facts 的主語複製一遍 | 前瞻追蹤項目 |
| 降級 collector | 3 個（derivatives／vegas_channel／long_short_ratio） | 2 個（vegas_channel／long_short_ratio） |
| 衍生品資料 | 無（可靠度 0.20 的離線 fixture） | **Kraken Futures**，可靠度 0.8375 |

`domain_coverage` 從 0.0 變成 0.4 是最關鍵的一項：舊版每個 Claim 都被判成資料不足，但那不是
證據不足，是 Planner 產出的 `required_domains`（`market_data`、`news_events`…）與 claim graph
的 domain 詞彙表（`market`、`news`…）對不起來，交集是空集合。修法見 commit `d640e7b`。

## 為什麼 run_status 是 COMPLETED_DEGRADED

**不是模型失敗，是兩個 collector 仍只有 Binance 一個來源。**

Binance 封鎖美國 IP，而部署 region 是 `us-west-2`：

- `vegas_channel`（Binance klines）→ fallback。屬 `market` 領域，CoinGecko 仍提供該領域資料。
- `long_short_ratio`（Binance 大戶多空比）→ fallback。屬 `derivatives` 領域，但該領域已由
  資金費率補回。

`derivatives`（資金費率）不再降級：commit `f94acfb` 加入 `Binance → Kraken Futures → dYdX v4`
的備援鏈，本次由 **Kraken Futures** 回答（可靠度 0.8375）。因此有真實證據的研究領域由
5 個回到 6 個。

大戶多空比在 Kraken Futures 與 dYdX 都沒有等價端點，Kraken 現貨 OHLC 上限 720 根也不足以
支撐 Vegas 通道的 EMA676 暖身，因此這兩個維持單一來源，並如實標示降級。

> 不同交易所的資金費率是各自市場的狀態。實測同一時點 ETH 在 Binance 為 `balanced`、在
> Kraken 為 `short_crowded`。證據的 `scope_note` 已標明這是單一交易所報價，不可視為全市場
> 共識；本機（Binance）與雲端（Kraken）的衍生品訊號可能因此不一致。

## 為什麼不是 formal

同一題目的 formal 執行確實跑過，run_id 是 `RUN-20260801T142232Z-BTC-bab828fd`，但它的產物
取不回來：Lambda 把產物寫在容器的 `/tmp`，而當時 Function URL 還沒有 `/download` 路由
（該路由原本只加在本機的 stdlib server，沒有加到 `lambda_handler.py`）。

再次以 formal 送出同一題目時，RunManager 如預期回 409 並附上完整 lineage：

```
formal run already exists for question_hash='00232f98...'
(latest run_id='RUN-20260801T142232Z-BTC-bab828fd', status='COMPLETED_DEGRADED');
pass authorized_rerun=True with rerun_of='RUN-20260801T142232Z-BTC-bab828fd'
```

因此本 fixture 取自同題目的 `test` 執行。兩者走完全相同的管線，差別只在 run 生命週期標記。
`/download` 路由已補進 `lambda_handler.py`，下次部署後即可直接從雲端取回六項產物。

> Lambda 的 formal lock 寫在容器 `/tmp`，不跨冷啟動保存。它能防止同一容器內的重複正式執行，
> 但不是持久性保證；正式比賽的一次性執行紀錄以本目錄與 `manifest.json` 為準。

## 驗證 manifest hash

```bash
python3 -c "import hashlib,json; from pathlib import Path; out=Path('demo-fixtures/competition-ready/live-success'); m=json.loads((out/'manifest.json').read_text()); [print(x['path'], hashlib.sha256((out/x['path']).read_bytes()).hexdigest()==x['sha256']) for x in m['files']]"
```

## 這份 fixture 早於社群面修正（T8.6）

本次執行時線上程式是 `f94acfb`，社群面還是「Reddit → Bluesky → HackerNews 第一個成功就停」
的備援鏈，而 Reddit 與 Bluesky 當時都回 403，因此只有一筆 Hacker News 證據，且它的查詢字串
是 `"{代號} crypto"` —— 那會匹配到 `Ethernet` 與 `cryptography`。本目錄
`evidence.json` 裡的社群貼文因此包含與加密貨幣無關的科技討論。

T8.6 已修正：三個平台各自成為獨立 collector、查詢改用幣種全名、並加上完整詞相關性過濾。
**這份 fixture 沒有重新產生**，因為它是實際雲端執行的紀錄，不會為了看起來一致而改寫。
下次部署後重跑即可更新（屆時應為 13 筆證據，其中三筆社群）。

其餘三份備援（`../offline-backup/`、`../comparison-backup/`、上層 `demo-fixtures/`）已包含
社群面修正。

## 展示用途

現場可直接展示本目錄，或使用仍然保留的備案：

- `../offline-backup/`：ETH 假設驗證題，完全離線，不需網路或憑證。
- `../comparison-backup/`：SOL vs BNB 比較題。

Workshop Studio 帳號是臨時環境，活動結束後 Function URL 會失效，但本目錄的六項產物是靜態
檔案，可獨立驗證 manifest hash。
