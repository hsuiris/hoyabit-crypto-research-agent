"""AWS Lambda Function URL entry point for the HOYA BIT MVP."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from urllib.parse import parse_qs

from src.errors import AgentInputError
from src.llm import llm_is_configured
from src.orchestrator import run


def _load_llm_credentials() -> bool:
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
        return _html(200, """<!doctype html><meta charset='utf-8'><title>HOYA BIT Research Agent</title>
        <h1>加密市場分析 AI Agent</h1><form method='post'>
        <label>幣種 <select name='coin'><option>BTC</option><option selected>ETH</option><option>SOL</option><option>BNB</option><option>XRP</option></select></label><br>
        <label>研究問題 <input name='question' size='70' value='分析近期市場狀況、主要驅動因素與風險'></label><br>
        <button>開始分析</button></form>""")
    raw_body = event.get("body", "")
    if event.get("isBase64Encoded"):
        raw_body = base64.b64decode(raw_body).decode("utf-8")
    content_type = event.get("headers", {}).get("content-type", "")
    if "application/json" in content_type:
        values = json.loads(raw_body or "{}")
        coin, question = values.get("coin", "ETH"), values.get("question", "分析近期市場狀況")
    else:
        values = parse_qs(raw_body)
        coin, question = values.get("coin", ["ETH"])[0], values.get("question", ["分析近期市場狀況"])[0]
    output_dir = Path("/tmp/outputs")
    csv_path = Path(__file__).parent / "data" / f"{coin.upper()}.csv"
    try:
        result = run(coin, question, output_dir, live=True, use_llm=_load_llm_credentials(), ohlcv_path=csv_path if csv_path.exists() else None)
    except AgentInputError as error:
        return _html(400, f"<meta charset='utf-8'><h1>輸入錯誤</h1><p>{error}</p>")
    report = (output_dir / "report.md").read_text(encoding="utf-8")
    evidence = json.loads((output_dir / "evidence.json").read_text(encoding="utf-8"))
    log = json.loads((output_dir / "execution_log.json").read_text(encoding="utf-8"))
    if "application/json" in content_type:
        return {"statusCode": 200, "headers": {"content-type": "application/json"}, "body": json.dumps({"result": result, "report": report, "evidence": evidence, "execution_log": log}, ensure_ascii=False)}
    return _html(200, f"<meta charset='utf-8'><h1>{result['coin']} 分析完成</h1><pre style='white-space:pre-wrap'>{report}</pre><h2>Execution Log</h2><pre>{json.dumps(log, ensure_ascii=False, indent=2)}</pre>")
