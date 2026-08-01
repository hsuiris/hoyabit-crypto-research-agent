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


# --------------------------------------------------------------------------------------
# 提交物下載
# --------------------------------------------------------------------------------------

# Function URL 的路由在這裡，不在 `src/app.py` 的 `Handler.do_GET`。兩邊各自分派，因此在
# stdlib server 加的路由**不會**自動出現在雲端 —— 下載按鈕本來只在本機有效，而 Demo 跑在
# 雲端，按了沒反應。這一段就是把 `/download` 與 `/artifact` 補到 Lambda 這一側。
#
# 產物寫在容器的 `/tmp`，所以只有處理過該次執行的容器讀得到。這是 Lambda 的固有限制，
# 不是 bug：冷啟動或被路由到別的容器時就沒有可下載的 run，此時回 404 並說明原因，
# 而不是回一個空的 ZIP 讓人以為執行沒有產物。
_LATEST_RUN_DIR: Path | None = None


def _query(event: dict) -> dict:
    return event.get("queryStringParameters") or {}


def _resolve_run_dir(run_id: str = "") -> Path | None:
    if run_id:
        # 只接受單一路徑片段，帶分隔符或 `..` 一律拒絕，不做任何拼接嘗試。
        if "/" in run_id or "\\" in run_id or run_id in {".", ".."}:
            return None
        runs_root = artifact_root(DEFAULT_ARTIFACT_ROOT) / "runs"
        candidate = (runs_root / run_id).resolve()
        if candidate.is_dir() and candidate.is_relative_to(runs_root.resolve()):
            return candidate
        return None
    return _LATEST_RUN_DIR


def _no_run_response() -> dict:
    return _html(404, "<meta charset='utf-8'><h1>沒有可下載的執行結果</h1>"
                      "<p>提交物寫在處理該次執行的容器暫存目錄，只有那個容器讀得到。"
                      "請先在本頁執行一次分析，再回來下載。</p><p><a href='/'>返回首頁</a></p>")


def _download_bundle(event: dict) -> dict:
    """把提交物打包成 ZIP 回傳。scope=required 為命題要求的三份，scope=all 為全部產物。"""
    from src.app import DOWNLOAD_SCOPES, build_artifact_zip

    params = _query(event)
    scope = (params.get("scope") or "required").strip()
    if scope not in DOWNLOAD_SCOPES:
        return _html(400, f"<meta charset='utf-8'><h1>不支援的下載範圍</h1><p>{escape(scope)}</p>")
    run_dir = _resolve_run_dir((params.get("run") or "").strip())
    if run_dir is None:
        return _no_run_response()
    payload, names = build_artifact_zip(run_dir, scope)
    if not names:
        return _no_run_response()
    # Function URL 傳二進位必須 base64 編碼並標示，否則會被當成文字而損毀。
    return {
        "statusCode": 200,
        "headers": {
            "content-type": "application/zip",
            "content-disposition": f'attachment; filename="hoyabit-artifacts-{run_dir.name}.zip"',
        },
        "body": base64.b64encode(payload).decode("ascii"),
        "isBase64Encoded": True,
    }


def _single_artifact(event: dict) -> dict:
    """回傳單一提交物；`download=1` 時加上 Content-Disposition 讓瀏覽器直接存檔。"""
    from src.app import DOWNLOADABLE_NAMES

    params = _query(event)
    name = (params.get("path") or "").strip()
    if name not in DOWNLOADABLE_NAMES:
        return _html(404, "<meta charset='utf-8'><h1>找不到指定輸出檔案</h1>")
    run_dir = _resolve_run_dir((params.get("run") or "").strip())
    if run_dir is None:
        return _no_run_response()
    target = run_dir / name
    if not target.is_file():
        return _no_run_response()
    headers = {"content-type": "application/json; charset=utf-8" if name.endswith(".json")
               else "text/plain; charset=utf-8"}
    if params.get("download") == "1":
        headers["content-disposition"] = f'attachment; filename="{name}"'
    return {"statusCode": 200, "headers": headers, "body": target.read_text(encoding="utf-8")}


def log_run_summary(event_name: str, payload: dict) -> None:
    """輸出一行可搜尋的執行摘要到 CloudWatch。

    沒有這行的話，log 裡只有 Lambda 自己的 START／END／REPORT 與冷啟動的 `[sdk]`，
    無法從 CloudWatch 回溯到特定 `run_id`，也無法在不取得回應內容的情況下判斷模型路徑
    是否降級——而降級的報告看起來與正常的一模一樣。

    刻意**不記錄題目原文**，只記 `question_hash`：log 的保留期比單次請求長得多，
    不該把使用者輸入寫進去。同樣地也不記錄任何 Evidence 內容或憑證。
    """
    print(f"[{event_name}] {json.dumps(payload, ensure_ascii=False)}")


def _run_summary(record, manifest: dict, log: dict) -> dict:
    """組出摘要欄位。只取判斷「這次執行是否可信」所需的最小集合。"""
    stages = {
        name: (info.get("status") or info.get("provider"))
        for name, info in (manifest.get("stage_providers") or {}).items()
    }
    return {
        "run_id": record.run_id,
        "mode": record.mode,
        "status": record.status,
        "question_hash": manifest.get("question_hash"),
        "duration_ms": manifest.get("duration_ms"),
        "citation_gate": (manifest.get("validation") or {}).get("citation_gate_status"),
        "degradation_reasons": log.get("degradation_reasons") or [],
        "stages": stages,
        "converse_available": SDK_CAPABILITY["converse_available"],
    }


def handler(event, context):
    global _LATEST_RUN_DIR
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    path = (event.get("rawPath")
            or event.get("requestContext", {}).get("http", {}).get("path", "/") or "/")
    if method == "GET" and path.rstrip("/").endswith("/download"):
        return _download_bundle(event)
    if method == "GET" and path.rstrip("/").endswith("/artifact"):
        return _single_artifact(event)
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
        # 正式執行被 lock 擋下是預期行為，但仍要留痕：否則現場只會看到 409 而無從得知
        # 是哪一題被擋、以及原本那次正式執行是哪一個 run。
        log_run_summary("run-rejected", {"reason": "formal_run_already_exists", "detail": str(error)})
        return _html(409, f"<meta charset='utf-8'><h1>正式執行已存在</h1><p>{error}</p>")
    csv_path = Path(__file__).parent / "data" / f"{coin.upper()}.csv"
    try:
        result = run(coin, question, output_dir, live=True, use_llm=_load_llm_credentials(), ohlcv_path=csv_path if csv_path.exists() else None, run_record=record)
    except AgentInputError as error:
        return _html(400, f"<meta charset='utf-8'><h1>輸入錯誤</h1><p>{error}</p>")
    # 讓 `/download` 與 `/artifact` 能找到這次的產物（同一個容器內有效）。
    _LATEST_RUN_DIR = output_dir
    artifacts = {
        key: json.loads((output_dir / name).read_text(encoding="utf-8")) if name.endswith(".json")
        else (output_dir / name).read_text(encoding="utf-8")
        for key, name in ARTIFACT_FILENAMES.items()
    }
    report, log = artifacts["report"], artifacts["execution_log"]
    log_run_summary("run", _run_summary(record, artifacts["manifest"], log))
    if "application/json" in content_type:
        # 六項提交物一次回傳，呼叫端不需要再回頭讀容器裡的檔案。
        # sdk_capability 讓呼叫端能程式化區分「模型成功」與「SDK 太舊而靜默降級」，
        # 不必回頭撈 CloudWatch Logs。
        return {"statusCode": 200, "headers": {"content-type": "application/json"}, "body": json.dumps({"result": result, "run_id": record.run_id, "run_mode": record.mode, "run_status": record.status, "artifact_directory": str(output_dir), "sdk_capability": SDK_CAPABILITY, **artifacts}, ensure_ascii=False)}
    # 下載連結帶上 run_id：容器若同時服務多次執行，不帶 run_id 只會拿到最後一次的產物。
    downloads = (
        f"<h2>下載提交物</h2><p>"
        f"<a href='/download?scope=required&run={escape(record.run_id)}'>下載三份提交物（ZIP）</a>"
        f"　｜　"
        f"<a href='/download?scope=all&run={escape(record.run_id)}'>下載全部產出（ZIP）</a></p><p>"
        + "　".join(
            f"<a href='/artifact?path={escape(name)}&run={escape(record.run_id)}&download=1'>"
            f"{escape(name)}</a>"
            for name in ARTIFACT_FILENAMES.values())
        + "</p>"
    )
    return _html(200, f"<meta charset='utf-8'><h1>{result['coin']} 分析完成</h1><p>Run {record.run_id}（{record.mode}／{record.status}）</p>{downloads}<pre style='white-space:pre-wrap'>{report}</pre><h2>執行紀錄（Execution Log）</h2><pre>{json.dumps(log, ensure_ascii=False, indent=2)}</pre>")
