# 競賽升級前基線（T0）

本文件記錄競賽升級前的可執行基線。T0 已完成；後續工作從 `hackathon/competition-ready` 分支開始，不能把本基線當成 T1 功能需求。

## Git 基線

- Branch：`hackathon/competition-ready`
- 建立 branch 時的原始 commit：`ea2bc3c064c7a5c4c5d15bb8870ab0fa5b6b366e`
- T0 baseline commit：`b38c43369efbf27aeff841b4ca0e15fd9db22aa0`
- Commit message：`chore(T0): freeze competition upgrade baseline`
- 基線文件：`docs/COMPETITION_BASELINE.md`

## 可重現驗證

專案只使用 Python 標準函式庫；環境要求 Python 3.10 以上。基線驗證環境為 macOS，實際可用命令是 `python3`，版本為 `Python 3.9.6`；`python` 命令不存在。後續競賽環境應先使用 Python 3.10+，不應把目前 3.9.6 視為支援版本。

完整測試：

```bash
python3 -m unittest discover -s tests -v
```

結果：117 tests、117 passed、0 failed、0 errors。

Offline smoke test：

```bash
python3 -c "from pathlib import Path; from src.orchestrator import run; run('ETH', '離線基線 smoke test', Path('outputs-t0-baseline'), live=False, use_llm=False)"
```

成功產生：

- `outputs-t0-baseline/report.md`
- `outputs-t0-baseline/evidence.json`
- `outputs-t0-baseline/execution_log.json`

Web Demo：

```bash
python3 -m src.app
```

服務位址：`http://127.0.0.1:8000`。基線驗證已確認首頁回應 HTTP 200 且可載入 `HOYA BIT`。

## T0 限制

- 未執行 live API、真實 LLM 或 AWS 部署驗證。
- `outputs-t0-baseline/` 是可重建的產物，受 `.gitignore` 的 `outputs-*/` 規則忽略；固定 Demo fixture 位於 `demo-fixtures/`。
- T0 不修改產品功能；若後續測試遇到環境問題，只能先修復最小啟動 blocker，避免在基線上進行功能重構。
- 完整驗收細節以 `docs/COMPETITION_BASELINE.md` 為準。
