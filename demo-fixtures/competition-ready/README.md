# T8 固定展示產物

此目錄是已凍結的展示備援，不是執行時輸出目錄。報告一律繁體中文。

**各份的程式基準不同，這一點對照時很重要：**

| 目錄 | 產生於 | 社群面 | 證據筆數（live） |
|---|---|---|---|
| `offline-backup/`、`comparison-backup/`、上層 `demo-fixtures/` | T8.6 之後 | 三個獨立平台 | 13 |
| `live-success/` | `f94acfb`（T8.4，**早於社群面修正**） | 單一 Hacker News | 11 |

`live-success` 是實際雲端執行的紀錄，不會為了看起來一致而改寫。它產生時 Function URL 上的
程式還是 T8.4，社群面仍是「第一個成功就停」的備援鏈，因此只有一筆 Hacker News 證據。
下次部署後重跑即可更新（屆時應為 13 筆、三筆社群證據）。

- `offline-backup/`：ETH 假設驗證題的六項離線產物（`live=False, use_llm=False`）。
  不需網路或憑證，Citation Gate `PASS`。所有 Claim 均誠實標為 `insufficient_evidence`——
  離線模式全部證據都是可靠度 0.20 的 fixture，本來就不足以支撐方向性結論。
- `comparison-backup/`：SOL vs BNB 比較題的 pair bundle；兩腳各自有六項產物，
  頂層保留 `comparison.md`、`comparison.json`、共用研究計畫與 manifest。
- `live-success/`：真正以 AWS Bedrock 完成的 live 執行（`RUN-20260801T142730Z-BTC-52f95e69`，
  us-west-2，`amazon.nova-lite-v1:0`，analyst 與 critic 皆 success）。細節與降級原因見該目錄的
  `README.md`。

上層 `demo-fixtures/` 另有一組較早的三檔 MVP 展示集（`report.md`／`evidence.json`／
`execution_log.json`），為本機 live 採集、11 個來源全部成功、零降級。

## 驗證任何 bundle 的檔案 hash

```bash
python3 -c "import hashlib,json; from pathlib import Path; out=Path('demo-fixtures/competition-ready/offline-backup'); m=json.loads((out/'manifest.json').read_text()); [print(x['path'], hashlib.sha256((out/x['path']).read_bytes()).hexdigest()==x['sha256']) for x in m['files']]"
```

把路徑換成 `live-success`、`comparison-backup`、`comparison-backup/SOL` 或
`comparison-backup/BNB` 即可驗證其他 bundle；五個目錄實測皆全部相符。
