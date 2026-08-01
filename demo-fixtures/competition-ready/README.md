# T8 固定展示產物

此目錄是已凍結的展示備援，不是執行時輸出目錄。

- `offline-backup/`：ETH 假設驗證題的六項離線產物。它是 `COMPLETED_DEGRADED`，所有 Claim 均會誠實標為 `insufficient_evidence`。
- `comparison-backup/`：SOL vs BNB 比較題的 pair bundle；兩腳各自有六項產物，頂層保留比較計畫與 manifest。
- `live-success/`：只接受真正以現場 Bedrock credentials 完成、且沒有 fallback 的單次 live smoke。若這次沒有成功 live run，目錄僅保留說明檔，現場一律切換至 `offline-backup/`。

驗證任何單幣 bundle 的檔案 hash：

```bash
python3 -c "import hashlib,json; from pathlib import Path; out=Path('demo-fixtures/competition-ready/offline-backup'); m=json.loads((out/'manifest.json').read_text()); [print(x['path'], hashlib.sha256((out/x['path']).read_bytes()).hexdigest()==x['sha256']) for x in m['files']]"
```
