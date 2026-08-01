# 安裝與操作設定

## 需求與安裝

- Python 3.10 以上（本機驗收若只有 `python3` 3.9.6，僅作相容性檢查，不是正式支援版本）。
- 僅使用 Python 標準函式庫，**不需要 `pip install`**。
- clone 後於專案根目錄執行：

```bash
python3 -m unittest discover -s tests
python3 -m src.app
```

Web Demo 位於 <http://127.0.0.1:8000>。

## AWS Bedrock（選用）

系統不讀取或保存 access key；請先使用正常 AWS credential chain（例如 AWS SSO、環境角色或 `~/.aws/credentials`）登入。確認競賽帳戶已在目標 region 取得模型存取權。

```bash
cp .env.example .env
```

在 `.env` 設定，不要提交此檔：

```text
LLM_PROVIDER=bedrock
AWS_REGION=ap-northeast-1
BEDROCK_MODEL_ID=amazon.nova-lite-v1:0
BEDROCK_MAX_TOKENS=2048
BEDROCK_TEMPERATURE=0.2
```

`BEDROCK_MODEL_ID` 必須替換為現場帳戶／region 實際允許的 model ID。CLI 指令不會自動載入 `.env`；先在 shell 匯出相同變數，或使用 Web 入口（它會載入根目錄 `.env`）。

## 離線 smoke

```bash
python3 -c "from pathlib import Path; from src.orchestrator import run; run('ETH', '市場認為 ETH 短期將維持盤整，請蒐集支持與反對證據。', Path('outputs-offline-smoke'), live=False, use_llm=False)"
```

確認六項輸出與 manifest：

```bash
python3 -c "import hashlib,json; from pathlib import Path; out=Path('outputs-offline-smoke'); m=json.loads((out/'manifest.json').read_text()); [print(x['path'], hashlib.sha256((out/x['path']).read_bytes()).hexdigest()==x['sha256']) for x in m['files']]"
```

## 一次 live smoke

僅在 AWS credentials、region 與 model ID 都已確認時執行一次：

```bash
LLM_PROVIDER=bedrock AWS_REGION=ap-northeast-1 BEDROCK_MODEL_ID='你的現場模型 ID' python3 -c "from pathlib import Path; from src.orchestrator import run; run('BTC', '分析 BTC 過去兩週市場表現，整合價格、鏈上、主要新聞與討論熱度，說明訊號一致程度。', Path('demo-fixtures/competition-ready/live-success'), live=True, use_llm=True)"
```

若結果的 `execution_log.json` 出現 fallback，保留該診斷輸出，但不要把它當作 live-success；改以 `offline-backup` 展示。

## Formal run 與授權重跑

```bash
python3 -c "from pathlib import Path; from src.run_manager import RunManager, RUN_MODE_FORMAL; from src.orchestrator import run; m=RunManager(Path('outputs-formal')); r=m.create_run('分析 XRP 當前市場狀態、主要風險與後續觀察條件。',['XRP'],mode=RUN_MODE_FORMAL); run('XRP','分析 XRP 當前市場狀態、主要風險與後續觀察條件。',m.run_directory(r),live=False,use_llm=False,run_record=r); print(r.run_id,r.status)"
```

同題正式重跑必須明確帶上前一個 `run_id`、理由及授權：

```python
rerun = manager.create_run(question, ['XRP'], mode=RUN_MODE_FORMAL,
    rerun_of=previous_run_id, rerun_reason='已授權的原因', authorized_rerun=True)
```

完整現場步驟與 fallback 展示方式見 `docs/DEMO_RUNBOOK.md`。
