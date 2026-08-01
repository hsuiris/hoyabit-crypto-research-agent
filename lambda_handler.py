"""AWS Lambda Function URL entry point for the HOYA BIT MVP."""

from __future__ import annotations

import base64
import json
import os
from html import escape
from pathlib import Path
from urllib.parse import parse_qs

from src.errors import AgentInputError
from src.llm import configured_provider, llm_is_configured
from src.orchestrator import run
from src.run_manager import (RUN_MODE_TEST, VALID_RUN_MODES, FormalRunAlreadyExistsError,
                            RunManager, artifact_root)
from src.schemas import ARTIFACT_FILENAMES

# Lambda 的容器檔案系統只有暫存目錄可寫。輸出仍然一個 run 一個目錄
# （`<root>/runs/{run_id}/`），因此同一個容器連續處理兩個請求時，第二次不會覆寫第一次的產物。
DEFAULT_ARTIFACT_ROOT = "/tmp"


# --------------------------------------------------------------------------------------
# D4 — 執行環境的 AWS SDK 能力
# --------------------------------------------------------------------------------------

# Lambda 受管 runtime 內建的 boto3／botocore 版本常落後於 runtime 的發布時點。若它不支援
# `bedrock-runtime` 的 `Converse`，`src.llm` 會拋出例外、被 Orchestrator 攔下、降級成
# deterministic offline fallback —— 執行仍然成功、六項提交物仍然產出，只是模型路徑沒跑。
#
# 這正是 `docs/COMPETITION_BLOCKERS.md` 的 B2 在雲端的翻版，而且更難發現：本機至少會看到
# `RuntimeError: Amazon Bedrock requires boto3`，雲端卻只會看到一份「成功但已降級」的報告。
# 因此把 SDK 能力做成顯式欄位，而不是等它靜默失敗。
#
# 這個檢測**只回報事實，不改變任何控制流程**：provider 選擇仍由 `src.llm` 決定，降級決策
# 仍由 Orchestrator 負責。它存在的唯一理由是讓雲端的降級可以被區分出來。


def sdk_capability() -> dict:
    """回報執行環境是否具備 `bedrock-runtime` 的 `Converse` 操作。

    刻意只讀 service model，不建立 boto3 client：建 client 會嘗試解析憑證與 endpoint，
    因此本函式沒有網路行為、也不需要任何憑證，可以安全地在冷啟動時執行。

    任何失敗都回報為結構化結果而不拋出例外——這是可觀測性程式碼，不該成為新的失敗來源。
    """
    capability: dict = {"botocore_version": None, "converse_available": False, "detail": None}
    try:
        import botocore
        import botocore.session
    except ImportError as error:
        capability["detail"] = f"botocore unavailable: {error}"
        return capability
    capability["botocore_version"] = getattr(botocore, "__version__", None)
    try:
        model = botocore.session.get_session().get_service_model("bedrock-runtime")
    except Exception as error:  # UnknownServiceError 代表 SDK 太舊而不認得該服務
        capability["detail"] = f"{type(error).__name__}: {error}"
        return capability
    capability["converse_available"] = "Converse" in model.operation_names
    if not capability["converse_available"]:
        capability["detail"] = "bedrock-runtime service model has no Converse operation"
    return capability


SDK_CAPABILITY = sdk_capability()

# 只在 Lambda 執行環境輸出，避免污染本機測試與 CLI 的輸出。CloudWatch Logs 因此在每次
# 冷啟動都會有一行可搜尋的 `[sdk]`，不需要等到分析失敗才回頭查原因。
if os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
    print(f"[sdk] {json.dumps(SDK_CAPABILITY, ensure_ascii=False)}")


def _prepare_run(question: str, coins, mode: str):
    """建立這次請求的 run record 與唯一輸出目錄。"""
    manager = RunManager(artifact_root(DEFAULT_ARTIFACT_ROOT))
    record = manager.create_run(question, coins, mode=mode)
    return record, manager.run_directory(record)


def _run_mode(raw) -> str:
    mode = str(raw or RUN_MODE_TEST).strip().lower()
    return mode if mode in VALID_RUN_MODES else RUN_MODE_TEST


def _load_llm_credentials() -> bool:
    # Bedrock authenticates with the Lambda execution role / ambient AWS credential chain, never
    # with a provider API key stored in Secrets Manager.  Do not attempt a secret lookup here.
    if configured_provider() == "bedrock":
        return llm_is_configured()
    if llm_is_configured():
        return True
    secret_arn = os.getenv("LLM_SECRET_ARN") or os.getenv("OPENAI_SECRET_ARN")
    if not secret_arn:
        return False
    import boto3
    secret = boto3.client("secretsmanager").get_secret_value(SecretId=secret_arn)
    value = secret.get("SecretString", "")
    try:
        config = json.loads(value)
    except json.JSONDecodeError:
        config = {"OPENAI_API_KEY": value}
    if not isinstance(config, dict):
        return False
    for name in ("LLM_PROVIDER", "GEMINI_API_KEY", "GEMINI_MODEL", "OPENAI_API_KEY", "OPENAI_MODEL"):
        if config.get(name):
            os.environ[name] = str(config[name])
    return llm_is_configured()


def _html(status: int, body: str) -> dict:
    return {"statusCode": status, "headers": {"content-type": "text/html; charset=utf-8"}, "body": body}


def handler(event, context):
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    if method == "GET":
        # 把 SDK 能力放在首頁，讓部署驗收不必先跑一次完整分析就能看出模型路徑是否可用。
        # 只顯示版本與布林值，不顯示 detail（例外訊息），並仍經 escape 處理。
        sdk_note = (
            "<hr><p style='color:#666;font-size:0.85em'>執行環境："
            f"botocore {escape(str(SDK_CAPABILITY['botocore_version'] or '未知'))}"
            "／Bedrock Converse "
            f"{'可用' if SDK_CAPABILITY['converse_available'] else '<strong>不可用（模型路徑會降級為離線推理）</strong>'}"
            "</p>"
        )
        return _html(200, """<!doctype html><meta charset='utf-8'><title>HOYA BIT Research Agent</title>
        <h1>加密市場分析 AI Agent</h1><form method='post'>
        <label>幣種 <select name='coin'><option>BTC</option><option selected>ETH</option><option>SOL</option><option>BNB</option><option>XRP</option></select></label><br>
        <label>研究問題 <input name='question' size='70' value='分析近期市場狀況、主要驅動因素與風險'></label><br>
        <label>執行性質 <select name='mode'><option value='test' selected>Test（可重複）</option>
        <option value='formal'>Formal（正式，不可覆寫）</option></select></label><br>
        <button>開始分析</button></form>""" + sdk_note)
    raw_body = event.get("body", "")
    if event.get("isBase64Encoded"):
        raw_body = base64.b64decode(raw_body).decode("utf-8")
    content_type = event.get("headers", {}).get("content-type", "")
    if "application/json" in content_type:
        values = json.loads(raw_body or "{}")
        coin, question = values.get("coin", "ETH"), values.get("question", "分析近期市場狀況")
        mode = _run_mode(values.get("mode"))
    else:
        values = parse_qs(raw_body)
        coin, question = values.get("coin", ["ETH"])[0], values.get("question", ["分析近期市場狀況"])[0]
        mode = _run_mode(values.get("mode", [RUN_MODE_TEST])[0])
    try:
        record, output_dir = _prepare_run(question, [coin], mode)
    except FormalRunAlreadyExistsError as error:
        return _html(409, f"<meta charset='utf-8'><h1>正式執行已存在</h1><p>{error}</p>")
    csv_path = Path(__file__).parent / "data" / f"{coin.upper()}.csv"
    try:
        result = run(coin, question, output_dir, live=True, use_llm=_load_llm_credentials(), ohlcv_path=csv_path if csv_path.exists() else None, run_record=record)
    except AgentInputError as error:
        return _html(400, f"<meta charset='utf-8'><h1>輸入錯誤</h1><p>{error}</p>")
    artifacts = {
        key: json.loads((output_dir / name).read_text(encoding="utf-8")) if name.endswith(".json")
        else (output_dir / name).read_text(encoding="utf-8")
        for key, name in ARTIFACT_FILENAMES.items()
    }
    report, log = artifacts["report"], artifacts["execution_log"]
    if "application/json" in content_type:
        # 六項提交物一次回傳，呼叫端不需要再回頭讀容器裡的檔案。
        # sdk_capability 讓呼叫端能程式化區分「模型成功」與「SDK 太舊而靜默降級」，
        # 不必回頭撈 CloudWatch Logs。
        return {"statusCode": 200, "headers": {"content-type": "application/json"}, "body": json.dumps({"result": result, "run_id": record.run_id, "run_mode": record.mode, "run_status": record.status, "artifact_directory": str(output_dir), "sdk_capability": SDK_CAPABILITY, **artifacts}, ensure_ascii=False)}
    return _html(200, f"<meta charset='utf-8'><h1>{result['coin']} 分析完成</h1><p>Run {record.run_id}（{record.mode}／{record.status}）</p><pre style='white-space:pre-wrap'>{report}</pre><h2>Execution Log</h2><pre>{json.dumps(log, ensure_ascii=False, indent=2)}</pre>")
