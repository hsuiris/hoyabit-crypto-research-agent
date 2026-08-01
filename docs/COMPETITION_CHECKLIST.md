# HoyaBIT 競賽最終檢查清單（T8 Freeze）

> 完成後停止新增功能。任何未通過項目必須記錄為 `FAIL`、`BLOCKED` 或 `PARTIAL`；不得以「看起來正確」代替驗證。

## 開始前

- [ ] 使用 Python 3.10+：`python3 --version`
- [ ] `.env` 未提交，沒有 API key、AWS key、密碼或競賽 access code。
- [ ] AWS credentials、`AWS_REGION`、現場 `BEDROCK_MODEL_ID` 已確認（若執行 live）。
- [ ] `data/` 有 BTC、ETH、SOL、BNB、XRP 的 CSV。
- [ ] `demo-fixtures/competition-ready/offline-backup/` 與 `comparison-backup/` 存在並可讀。

## 測試與離線驗收

- [ ] `python3 -m unittest tests.test_competition_readiness -v` 通過。
- [ ] `python3 -m unittest discover -s tests` 通過。
- [ ] BTC 多源題離線 run 通過。
- [ ] ETH 假設驗證離線 run 通過，計畫含支持與反對問題。
- [ ] SOL vs BNB 比較離線 run 通過，兩腳共用時間窗。
- [ ] XRP 一般未知題型離線 run 通過。
- [ ] 五幣（BTC／ETH／SOL／BNB／XRP）至少各有一次 offline/test run 或合法降級。
- [ ] Bedrock timeout、非法 JSON、Planner failure、News failure、On-chain unavailable、Social 空資料、Semantic Critic timeout 都仍產生報告。
- [ ] 單一二手新聞、20 篇同源轉載、未知 Evidence ID、fallback sole support、高品質衝突均由既有 citation／claim gate 測試覆蓋。
- [ ] 接近 deadline 時停止模型與後續蒐集，仍寫出完整產物。

## 每一個單幣 bundle 必查

- [ ] 六項產物都存在：`report.md`、`evidence.json`、`execution_log.json`、`research_plan.json`、`claims.json`、`manifest.json`。
- [ ] 所有主要 Claim 只引用合法 Evidence ID。
- [ ] `source`、`fetched_at`、`content_reference` 在 Evidence 中完整率均為 100%。
- [ ] Citation Gate 非 `FAIL`；rejected Evidence 未進入主要報告。
- [ ] 同源轉載不重複加滿權重；高品質正反衝突會降低 confidence。
- [ ] `manifest.json` 列出的五個 SHA-256 與檔案內容一致。
- [ ] runtime 在內部 hard deadline（最多 900 秒）內。

## Live smoke（最多一次）

- [ ] 只執行一個 BTC 或 ETH 題目。
- [ ] 記錄 provider、model、region、runtime、collector 及 fallback 狀態。
- [ ] 僅無 fallback 的完整結果才保存到 `demo-fixtures/competition-ready/live-success/`。
- [ ] live 失敗時不重複大量呼叫；保留診斷後切換 `offline-backup/`。

## Web 與正式執行

- [ ] `python3 -m src.app` 後首頁 HTTP 200。
- [ ] 可展示 Evidence Traceability、Claims、Citation Gate 與 artifact links。
- [ ] formal run 寫入 `runs/<run_id>/`，重複 formal run 被拒絕。
- [ ] authorized rerun 帶 `rerun_of`、理由與 `authorized_rerun=True`，並保留 lineage。
- [ ] 故障切換已演練：開啟 offline 或 comparison backup，誠實說明 `insufficient_evidence`。

## Freeze 與提交

- [ ] `docs/COMPETITION_TASK_STATUS.yaml` 的 T8 為 `PASS` 並記錄實際結果與限制。
- [ ] 已建立單一 commit：`test(T8): freeze competition-ready demo`。
- [ ] 已建立 tag：`competition-demo-ready`。
- [ ] `git status --short` 為空；完成後不開始 T9 或任何功能開發。
