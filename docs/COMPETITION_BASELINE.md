# 競賽升級前基線

- **任務**：T0 — 凍結競賽升級前基線
- **建立日期**：2026-08-01
- **Branch**：`hackathon/competition-ready`
- **基線 commit（建立 branch 時）**：`ea2bc3c064c7a5c4c5d15bb8870ab0fa5b6b366e`
- **基線 commit message**：`（預計）chore(T0): freeze competition upgrade baseline`

## 執行環境

- 作業系統：macOS
- `python`：不可用（shell 回報 `zsh: command not found: python`）
- 實際執行命令：`python3`
- Python 版本：`Python 3.9.6`
- 專案要求：Python 3.10 以上
- 第三方相依：未安裝；本次測試使用標準函式庫完成

## 完整測試

文件指定命令為：

```bash
python -m unittest discover -s tests
```

由於環境沒有 `python` 指令，實際使用等價的可用命令：

```bash
python3 -m unittest discover -s tests -v
```

結果：

- `Ran 117 tests in 3.992s`
- **117 passed / 0 failed / 0 errors**
- 結果：`OK`

## Offline smoke test

實際執行命令：

```bash
python3 -c "from pathlib import Path; from src.orchestrator import run; run('ETH', '離線基線 smoke test', Path('outputs-t0-baseline'), live=False, use_llm=False)"
```

結果：成功完成，且確認以下檔案存在：

- `outputs-t0-baseline/report.md`
- `outputs-t0-baseline/evidence.json`
- `outputs-t0-baseline/execution_log.json`

此路徑使用 deterministic offline fallback，未呼叫外部 API 或 LLM。

## Web Demo smoke test

啟動命令：

```bash
python3 -m src.app
```

服務位址：`http://127.0.0.1:8000`

實際驗證命令：

```bash
python3 -c "from urllib.request import urlopen; response=urlopen('http://127.0.0.1:8000/', timeout=10); body=response.read(); assert response.status == 200; assert b'HOYA BIT' in body; print('HTTP', response.status, 'bytes', len(body), 'homepage=ok')"
```

結果：`HTTP 200`、回應 `20410` bytes，首頁可載入並包含 `HOYA BIT`。

## 現有輸出檔案

本次 T0 smoke test 產出的輸出檔案位於 `outputs-t0-baseline/`：

- `report.md`
- `evidence.json`
- `execution_log.json`

該目錄符合 `.gitignore` 的 `outputs-*/` 規則，屬於可重建的執行產物，不納入本次 commit。專案既有的固定離線 Demo fixture 仍位於 `demo-fixtures/`，未修改。

## 已知失敗與限制

1. 環境沒有 `python` 命令；改用 `python3` 後測試與 smoke test 均成功。這是環境命令差異，不是產品程式故障。
2. 目前 `python3` 為 3.9.6，低於專案要求的 Python 3.10+；本次 117 項測試仍全部通過，但後續競賽環境應升級至 Python 3.10 以上。
3. Web server 為長駐程序；本次已成功啟動並完成首頁 GET 驗證。完成驗證後應停止本地 server，避免佔用 port 8000。
4. 本次未執行 live API、真實 LLM 或 AWS 部署驗證；T0 僅凍結目前離線可執行基線。
5. 測試與 smoke test 未修改產品程式碼；僅產生被忽略的 `outputs-t0-baseline/` 執行產物與本基線文件。

## 基線用途

本 branch 與本文件用於在競賽升級前保留可回復參考點。T0 完成後停止，不在此基線 commit 中開始 T1 功能開發。
