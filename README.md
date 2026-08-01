# HoyaBIT 加密貨幣市場研究 Agent

Evidence-first 的加密貨幣市場研究工具。輸入 BTC、ETH、SOL、BNB 或 XRP 與研究題目後，系統在內部 deadline 內整合市場、鏈上、新聞與社群證據，輸出可回溯且具 Citation Gate 的研究報告；**不提供投資建議或自動交易訊號**。

## 主要能力

- 以規則決定市場立場；LLM 僅能撰寫敘事與稽核，不能決定信心分數或捏造 Evidence。
- 每次單幣執行產生六項提交物：`report.md`、`evidence.json`、`execution_log.json`、`research_plan.json`、`claims.json`、`manifest.json`。
- 任一 collector、Planner、Bedrock 或 Critic 失敗時，保留確定性 offline fallback 與完整產物。
- 正式執行使用不可覆寫 run 目錄、formal lock、授權重跑 lineage 與 900 秒以內 deadline。
- 比較題的兩腳共用研究計畫與時間窗，避免不同幣種使用不同條件比較。

## 快速開始

需求：Python 3.10 以上；專案只使用標準函式庫，不需要 `pip install`。

```bash
python3 -m unittest discover -s tests
python3 -m src.app
```

開啟 <http://127.0.0.1:8000>。未設定外部服務時，Web 及 CLI 都可使用 offline fallback。

執行單幣離線 smoke：

```bash
python3 -c "from pathlib import Path; from src.orchestrator import run; run('ETH', '市場認為 ETH 短期將維持盤整，請蒐集支持與反對證據。', Path('outputs-offline-smoke'), live=False, use_llm=False)"
```

詳細的 AWS／Bedrock、formal run、授權重跑、live smoke、manifest 驗證與現場故障切換，請見 [SETUP.md](SETUP.md) 與 [docs/DEMO_RUNBOOK.md](docs/DEMO_RUNBOOK.md)。

## 固定展示備援

- `demo-fixtures/competition-ready/offline-backup/`：ETH 假設題完整六項產物。
- `demo-fixtures/competition-ready/comparison-backup/`：SOL vs BNB 比較題與兩腳完整產物。
- `demo-fixtures/competition-ready/live-success/`：僅在成功完成一次 live smoke 後保存其完整產物；若現場服務不可用，改展示 offline backup，不得將 fallback 宣稱為 live。

## 已知限制

- 僅支援 BTC、ETH、SOL、BNB、XRP；預設研究窗為 14 天。
- 離線 fixture 是流程備援，所有 Claim 會誠實標為 `insufficient_evidence`，不可視為市場方向。
- Bedrock live 使用 AWS credential chain 與已獲准的 `BEDROCK_MODEL_ID`；外部網路或配額失敗時仍可輸出 offline 報告。
- 現行執行環境的 `python3` 為 3.9.6，僅用於驗收；正式展示環境應使用 Python 3.10 以上。
