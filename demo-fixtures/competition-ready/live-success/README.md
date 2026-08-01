# Live smoke 保存位置

此目錄只保留唯一一次成功的 Bedrock live smoke 六項產物；絕不放入模擬或離線輸出。

## 實際結果（2026-08-01）

**模型路徑成功，B2 已解除。**

| 項目 | 值 |
|---|---|
| run_id | `RUN-20260801T095907Z-BTC-5c13147c` |
| 執行性質 | `formal`（正式，不可覆寫） |
| 題目 | 評估 BTC 當前市場狀況、關鍵驅動因素與主要下行風險 |
| 執行環境 | AWS Lambda，us-west-2，Function URL |
| 模型 | `amazon.nova-lite-v1:0`（botocore 1.42.97，Converse 可用） |
| 耗時 | 17.4 秒 |
| analyst | `provider=bedrock`，**status=success** |
| critic | `provider=bedrock`，**status=success** |
| Citation Gate | `PASS_WITH_WARNINGS`（0 error、0 warning、4 個語意 finding） |
| credibility | `registry_version=source-registry-v1`，`mean_final_score=0.5866` |
| manifest | 五個 SHA-256 全部相符，落地後重新從磁碟計算仍相符 |
| run_status | `COMPLETED_DEGRADED`（原因見下方，非模型失敗） |

### 為什麼換過一次 fixture

D8 最初的 live run（`RUN-20260801T094240Z-BTC-f3d916b3`）模型路徑同樣成功，但它的
**證據可信度分數是用錯誤的基準算出來的**：當時的部署包漏了 `config/`，因此
`src/credibility.py` 的 `load_source_registry()` 找不到 `config/source_registry.json`，
靜默退回保守預設——所有 `source_type` 都變成 `source_quality=0.35`，高品質來源被大幅
低估（`blockchain_raw` 0.90 → 0.35），而 `fallback_fixture` 反被高估（0.20 → 0.35）。

| | 修復前 | 修復後 |
|---|---|---|
| `registry_version` | `unavailable` | `source-registry-v1` |
| `mean_final_score` | 0.3898 | 0.5866 |

證據筆數相同（11 筆，8 筆實質），差異純粹來自計分基準。由於
`weighted_evidence_quality` 佔 claim confidence 公式的 30%，這會傳導到最終信心分數，
因此舊 fixture 不適合作為展示基準，已由修復後的 run 取代。

修復內容：`aws/deploy.sh` 與 `aws/deploy.ps1` 加入 `config/` 的複製，並在打包階段就
驗證 `config/source_registry.json` 在位——不要等部署後才從 `registry_version=unavailable`
發現，那是靜默降級，很容易被當成正常。

## 為什麼 run_status 是 COMPLETED_DEGRADED

**不是模型失敗，是三個資料來源被地理封鎖。**

Binance 封鎖美國 IP，而部署 region 是 `us-west-2`，因此 `derivatives`、`vegas_channel`、
`long_short_ratio` 三個 collector 取不到資料，改用可靠度 0.20 的 fallback fixture。
11 個 collector 為 8 success + 3 fallback。

已實測對比：本機（台灣 IP）對 `api.binance.com` 與 `fapi.binance.com` 回 `HTTP 200`，
雲端 `us-west-2` 回 `HTTPError`。詳見 `aws/README.md`。

這個降級標示是誠實的，也剛好展示了 per-source fallback 的設計價值：單一來源失敗
不中斷流程，Citation Gate 仍 `PASS`，且 fallback 證據不會成為任何主要 Claim 的唯一支持。

## 更正：先前記載的 B2 根因已不成立

T8 當時把 B2 的根因記為「本機依零第三方相依限制未安裝 boto3」。**這個描述是錯的**，
不要照它去裝套件：

- 實測本機 `python3` 已有 `boto3`／`botocore` 1.42.97，且 `bedrock-runtime` 含
  `Converse` operation。
- 真正的阻塞是**缺少 AWS credentials 與模型存取權**，這在 D0–D2 取得 Workshop Studio
  憑證後即解除。
- 而雲端首次部署後模型仍然失敗，根因是**第三個、完全不同的問題**：
  `amazon.nova-lite-v1:0` 會把 JSON 包在 markdown code fence 裡（```json ... ```），
  `src/llm.py` 直接 `json.loads()` 因此拋
  `Expecting value: line 1 column 1 (char 0)`，且既有的單次重試無效——模型行為一致，
  重試只會拿到同一個 fence。已由 `src.llm.strip_json_fence()` 修復（commit `3269142`）。

完整記錄見 `.kiro/specs/hoyabit-aws-deployment/status.yaml` 的 D5 `scope_extension`。

## 展示用途

現場可直接展示本目錄，或使用仍然保留的備案：

- `../offline-backup/`：ETH 假設題，完全離線，不需網路或憑證。
- `../comparison-backup/`：SOL vs BNB 比較題。

Workshop Studio 帳號是臨時環境，活動結束後 Function URL 會失效，
但本目錄的六項產物是靜態檔案，可獨立驗證 manifest hash。
