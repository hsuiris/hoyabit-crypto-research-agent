# Day 7 功能凍結與彩排清單

## 上午：凍結前驗收

- [ ] 修復所有阻斷 Demo 的 Bug
- [ ] 執行 `python -m unittest discover -s tests -v`
- [ ] 確認所有測試通過
- [ ] 確認輸出 `report.md`、`evidence.json`、`execution_log.json`
- [ ] 確認至少兩個資料來源或 fallback 記錄
- [ ] 確認每個結論有 Evidence ID／URL
- [ ] 準備 `demo-fixtures/` 固定輸出

## 下午：功能凍結後彩排

- [ ] 不再新增功能
- [ ] 依 `demo-script.md` 完成 5 分鐘彩排
- [ ] 使用固定輸出完成 API 失敗備援 Demo
- [ ] 每位成員用 30 秒說明自己的模組
- [ ] PM 能說明三個風險與 fallback 策略

## 三層 Demo

1. Live：真實 API + 真實 LLM（若環境可用）。
2. Stable：真實資料 + 固定分析輸出。
3. Offline：完整預先產生的報告與 JSON 檔案。

