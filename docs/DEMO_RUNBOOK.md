# HoyaBIT Demo Runbook（T8 Freeze）

本文件是正式展示的唯一操作流程。核心原則：**先完成離線備援驗證；live 只嘗試一次；任何外部服務失敗都切換到可稽核的 offline bundle。**

## 1. 安裝與前置檢查

```bash
python3 --version                 # 正式環境必須是 Python 3.10+
python3 -m unittest discover -s tests
```

專案只使用標準函式庫。確認 `data/` 包含 BTC、ETH、SOL、BNB、XRP CSV，且工作樹沒有 `.env` 或任何憑證被加入 Git。

## 2. AWS credentials、region 與 Bedrock model

使用正常 AWS credential chain（SSO、profile 或執行角色）；不要在檔案中寫 AWS access key。

```bash
cp .env.example .env
```

在 `.env` 指定現場實際獲准的值：

```text
LLM_PROVIDER=bedrock
AWS_REGION=ap-northeast-1
BEDROCK_MODEL_ID=amazon.nova-lite-v1:0
```

Web 入口會讀取 `.env`。CLI live smoke 請在 shell 匯出同樣變數。開始前確認 model ID 在該 region 可用；不要以多次探測消耗呼叫額度。

## 3. 離線 smoke（必做）

```bash
python3 -c "from pathlib import Path; from src.orchestrator import run; run('ETH', '市場認為 ETH 短期將維持盤整，請蒐集支持與反對證據。', Path('outputs-demo-offline'), live=False, use_llm=False)"
```

必須存在：`report.md`、`evidence.json`、`execution_log.json`、`research_plan.json`、`claims.json`、`manifest.json`。離線結果是刻意的 `COMPLETED_DEGRADED`；Claim 必須標示 `insufficient_evidence`，不可把 fixture 說成 live 市場結論。

## 4. 唯一一次 live smoke（選用）

僅在 credentials、region 和 model ID 都已確認後，執行一次 BTC 或 ETH；此命令將成功結果直接寫到 live backup：

```bash
LLM_PROVIDER=bedrock AWS_REGION=ap-northeast-1 BEDROCK_MODEL_ID='現場允許的模型 ID' python3 -c "from pathlib import Path; from src.orchestrator import run; run('BTC', '分析 BTC 過去兩週市場表現，整合價格、鏈上、主要新聞與討論熱度，說明訊號一致程度。', Path('demo-fixtures/competition-ready/live-success'), live=True, use_llm=True)"
```

完成後檢查 `execution_log.json` 的 provider/model/region、`duration_ms`、collector 狀態及 `manifest.json` hash。若任何模型或來源 fallback，保留該輸出做診斷，但不稱為 live success；不重試大量 API，直接使用下一節備援。

## 5. Web 啟動與 smoke

```bash
python3 -m src.app
```

開啟 <http://127.0.0.1:8000>。確認首頁載入、幣種選單只有 BTC／ETH／SOL／BNB／XRP、正式／測試模式選單存在，並用 offline 模式送出一題。產物頁可經 `/artifact?path=...` 檢視。

## 6. Formal run

正式 run 不可覆寫，且同一題目的第二次正式執行會被 formal lock 擋下：

```bash
python3 -c "from pathlib import Path; from src.run_manager import RunManager, RUN_MODE_FORMAL; from src.orchestrator import run; q='分析 XRP 當前市場狀態、主要風險與後續觀察條件。'; m=RunManager(Path('outputs-formal')); r=m.create_run(q,['XRP'],mode=RUN_MODE_FORMAL); run('XRP',q,m.run_directory(r),live=False,use_llm=False,run_record=r); print(r.run_id,r.status)"
```

輸出位置為 `outputs-formal/runs/<run_id>/`，checkpoint 位於同一目錄的 `_checkpoint.json`。

## 7. Authorized rerun

只有已記錄原因且經授權的重跑可略過 formal lock；保留前一 run 的目錄與 lock history：

```python
rerun = manager.create_run(
    question, ['XRP'], mode=RUN_MODE_FORMAL,
    rerun_of=previous_run_id,
    rerun_reason='第一次正式執行因已記錄的外部服務問題失敗',
    authorized_rerun=True,
)
```

## 8. Demo 故障切換

1. live 超過合理等待時間、出現 timeout／非法 JSON／collector failure，或 Web 沒有產物時，停止等待。
2. 說明系統保留 Evidence-first fallback，不捏造 live 結果。
3. 開啟 `demo-fixtures/competition-ready/offline-backup/report.md` 與 `execution_log.json`；如展示比較題，開啟 `comparison-backup/comparison.md` 與兩個幣種目錄。
4. 以 manifest 驗證五個提交檔 hash：

```bash
python3 -c "import hashlib,json; from pathlib import Path; out=Path('demo-fixtures/competition-ready/offline-backup'); m=json.loads((out/'manifest.json').read_text()); [print(x['path'], hashlib.sha256((out/x['path']).read_bytes()).hexdigest()==x['sha256']) for x in m['files']]"
```

## 9. 五分鐘展示順序

1. 0:00–0:40：輸入 ETH 假設題，說明只支援五幣與不是投資建議。
2. 0:40–1:40：展示 Fact → Inference → Conclusion、Evidence ID、來源 URL、時間戳。
3. 1:40–2:30：展示 Claim 的支持與反方 Evidence、heuristic confidence、限制與推翻條件。
4. 2:30–3:20：展示 Citation Gate、manifest hash、單一來源失敗的 degradation reason。
5. 3:20–4:20：開啟 SOL vs BNB comparison backup，強調共用時間窗。
6. 4:20–5:00：示範故障切換，明說 offline fixture 的 `insufficient_evidence` 限制。

## 10. 已知限制

- 離線 fixture 只保證可展示流程，不能作為單一主要 Claim 的充分支持。
- live 外部來源、Bedrock 模型存取與網路品質取決於現場帳戶及 region；失敗不會停用 fallback。
- formal lock 是本機檔案，跨多機部署需要共享儲存。
- Python 3.9.6 可跑本機驗收但不屬正式支援；正式環境使用 3.10+。
