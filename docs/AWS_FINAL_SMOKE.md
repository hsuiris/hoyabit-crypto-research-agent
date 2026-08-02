# AWS 最終部署與 live smoke 存證（E4）

本文件只記錄非敏感結果。完整 Function URL、AWS account ID、ARN 與任何憑證都不寫入此檔或版控。

## 部署

| 項目 | 值 |
|---|---|
| candidate SHA | `9926aab7ff84d44067925b7736ca587f8342fbcc` |
| 部署 ZIP | 30 檔／444 KB |
| stack | `hoyabit-agent-mvp`，region `us-west-2` |
| stack 狀態 | `UPDATE_COMPLETE`（非 CREATE：既有 stack 更新） |
| 部署完成（UTC） | 2026-08-02T03:17:07 |
| Lambda | python3.12／1024 MB／timeout 900 s |
| Function URL | `<redacted>`（host sha256 前 12：`bbaffc4176dd`） |

部署參數與線上實際值逐項核對相符：

| 參數 | 值 | 線上實測 |
|---|---|---|
| `CodeCommit` | `9926aab7ff…` | 相同，且不含 `-dirty` |
| `BedrockModelId` | `amazon.nova-lite-v1:0` | 相同 |
| `BedrockMaxTokens` | 5000 | Lambda `BEDROCK_MAX_TOKENS=5000` |
| `FunctionUrlAuthType` | `NONE` | `NONE` |
| `ReservedConcurrency` | 5 | 5 |
| `LogRetentionDays` | 7 | 7 |

`BedrockMaxTokens` 是 E2.6 新增的參數。在此之前 Lambda 環境完全沒有 `BEDROCK_MAX_TOKENS`，
因此雲端只能吃程式內建預設值，沒有任何調整途徑。這次部署是它第一次生效。

## 唯一一次 live smoke

恰好送出一次 `POST`，`Content-Type: application/json`，`mode=test`。未重送。

| 項目 | 值 |
|---|---|
| run_id | `RUN-20260802T032130Z-ETH-5d5716b3` |
| 幣種／題目類型 | ETH／多源整合（價格、鏈上、新聞、社群 + 一致程度） |
| 送出（UTC） | 2026-08-02T03:21:30 |
| 完成（UTC） | 2026-08-02T03:22:24 |
| HTTP | 200 |
| 端到端耗時 | **55.18 秒**（命題上限 900 秒） |
| 回應大小 | 201 KB（Function URL buffered 上限 6 MB） |
| run_mode | `test` |
| `execution_flags` | `live=True`、`use_llm=True` |
| run_status | `COMPLETED_DEGRADED`（原因見下方「已知限制」） |

### 模型路徑

四個階段全部走 Bedrock（`amazon.nova-lite-v1:0`，us-west-2）：

| 階段 | provider | status | 耗時 |
|---|---|---|---:|
| `plan_research` | bedrock | — | 2,969 ms |
| `llm_reasoning`（analyst） | bedrock | **success** | 29,123 ms |
| `critic_review` | bedrock | **success** | 15,763 ms |
| `build_claims` | bedrock | — | 4,713 ms |

模型呼叫合計 52.6 秒，佔總耗時 97%；所有確定性運算（計分、指標、驗證、gate、報告渲染）
合計約 1.4 秒。`collect_evidence` 只花 1,350 ms —— 13 個 collector 是平行的。

### 提交物與可追溯性

- 六項提交物齊備：`report.md`、`evidence.json`、`execution_log.json`、`research_plan.json`、
  `claims.json`、`manifest.json`。
- `manifest` 所列五個檔案的 SHA-256 **全部相符**；透過 `/download?scope=all` 取回 ZIP 後
  重新計算仍全部相符（ZIP 6 檔、CRC 全對、`Content-Type: application/zip`）。
- Citation Gate：`PASS_WITH_WARNINGS`，**0 error**、1 warning（`cited_item_locator`）、
  6 個語意 finding。其餘 10 條結構檢查全部 `pass`。
- `GET /report?run=<run_id>` 回 200／144 KB：`lang="zh-Hant-TW"`、三報告區塊齊備、
  **站內 anchor 零斷鏈**、每個被 Claim 引用的 Evidence 都有 anchor target、
  66 個外部來源超連結、無可執行 `<script>`。該端點只讀既有產物，不重跑分析。
- `report.md`：20 個段落、無英文開頭標題；「## 主張（Claim）」段有 **28 個可點擊連結**，
  其中 18 個是 source-item 層級連結。

### 資料採集

11／13 個 collector 取得真實資料。

| 領域 | 結果 |
|---|---|
| market | 成功 |
| news | 成功 |
| macro／announcement | 成功 |
| social（Reddit／Bluesky／Hacker News） | **三條獨立來源鏈全部成功** |
| derivatives | 成功，由 **Kraken Futures** 提供，可靠度 0.8375 |
| onchain／whale／tvl | 成功 |
| `vegas_channel`、`long_short_ratio` | 降級（Binance 地理封鎖，見下方） |

### 逐輪修正在雲端的實際效果

| Task | 驗證結果 |
|---|---|
| T8.1 | `required_domains` 為 canonical `[market, onchain, news, social]`，`domain_coverage = 1.0`，`CL-001` verdict `supported`、信心 0.7911 (high)，而非 `insufficient_evidence` |
| T8.4 | Kraken Futures 備援鏈從 us-west-2 連通，`derivatives` 領域未消失 |
| T8.6 | 13 筆證據；三個社群 collector 各自獨立成功，Reddit 失敗不再拖垮整個社群面 |
| E1 | 首頁無 `formal` 可選值，僅 `<input type='hidden' name='mode' value='test'>`，並明示「公開雲端展示僅提供 test mode」 |
| E2.6 | `BEDROCK_MAX_TOKENS=5000` 生效；執行紀錄**零截斷跡象**，analyst 完整回傳（輸出變長使該階段由 T8.6 時期的 20.3 秒增為 29.1 秒） |
| E3 | `/report` 三報告 UI 與逐 Claim 溯源在真實 Function URL 上驗證通過 |
| E3.1 | Claim 段 28 個可點擊引用（部署前為 0） |
| E3.2 | 三個社群來源 `author_coverage` 皆 1.0、不同作者數 4／8／16、最大單一帳號佔比 ≤ 0.27、皆有 profile locator → cap 不生效，可靠度 0.4589–0.4875（修復前一律被壓在 0.35） |

## 已知限制

1. **`vegas_channel` 與 `long_short_ratio` 仍是 Binance 單一來源**，而 Binance 封鎖美國 IP，
   us-west-2 是美國 region，因此這兩個 collector 在雲端一律降級。`run_status` 因此是
   `COMPLETED_DEGRADED`。這是 region 固有限制，改程式碼無法修復；`derivatives` 領域已由
   T8.4 的 Kraken 備援救回，`market` 領域由 CoinGecko 撐著。Citation Gate 仍 0 error，
   降級證據不構成任何主要 Claim 的唯一支持。

2. **E2 的模型評估被自身驗證擋掉。** 13 筆 `semantic_assessment` 全部是
   `deterministic_fallback`（`llm=0`）。原因記錄在執行紀錄：

   ```
   model assessment rejected: EV-NEWS-001: source_item_ids may name at most 3 items;
   EV-ANNOUNCE-ETH-001: ...; EV-SOCIAL-ETH-001: ...
   ```

   **這不是 token 上限問題** —— E2.6 已生效，零截斷跡象，模型完整回傳了評估。問題是
   E2 的驗證是全批作廢制（設計上刻意「不可部分接受」），而模型對三筆聚合證據
   （news 5 則、social 17–22 則貼文）各列了超過 `MAX_ASSESSMENT_SOURCE_ITEM_IDS = 3`
   個子項。後果是「模型判定問題相關性」這個能力在雲端未實際啟用；報告品質未受影響
   （deterministic 標籤給出 direct 2／indirect 4／context 1／unclear 6），但不應宣稱
   相關性評估來自模型。

3. **產物只存在容器 `/tmp`。** `/report` 與 `/download` 只有處理過該次執行的容器讀得到；
   冷啟動或被路由到其他容器時回 404 並說明此限制。未實作 S3 產物持久化。

4. **首頁沒有 `<html>` 元素**（是最小化片段 `<!doctype html><meta charset='utf-8'><title>…`），
   因此沒有 `lang` 屬性。結果頁與 `/report` 都有 `lang="zh-Hant-TW"`。

5. **公開端點 `AuthType=NONE`。** 任何取得該 URL 的人都能觸發完整 test 執行並消耗 Bedrock
   配額。收斂手段：E1 的 test-only 護欄（formal 與授權重跑一律拒絕）、reserved concurrency 5、
   log 保留 7 天。可用 `--auth-type AWS_IAM` 重新部署改為需憑證。

6. **測試環境 Python 為 3.9.6**，低於 runbook 要求的 3.10+。這是自 T0 記錄的既有偏離；
   Lambda runtime 是 python3.12，部署目標環境不受影響。928 項測試全數通過、0 skip。

7. 未測試雲端雙幣比較，未執行 formal 模式（公開端點已由 E1 禁止）。

## 帳號生命週期

由主辦方直接取消帳號權限。本專案不規劃也不執行 teardown。若需提前收斂，可執行
`aws cloudformation delete-stack --region us-west-2 --stack-name hoyabit-agent-mvp`；
兩個 S3 bucket 不是 stack 資源，需另行清空刪除。
