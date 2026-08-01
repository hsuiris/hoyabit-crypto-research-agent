"""Provider-neutral LLM reasoning adapters for Gemini, OpenAI, and Amazon Bedrock."""

from __future__ import annotations

import json
import os
from urllib.parse import quote
from urllib.request import Request, urlopen

try:
    import boto3
    from botocore.config import Config as BotoConfig
except ImportError:  # Local offline use remains possible without the AWS SDK.
    boto3 = None
    BotoConfig = None


ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "market_judgment": {"type": "string"},
        "confidence": {"type": "number"},
        "facts": {"type": "array", "items": {"type": "string"}},
        "inferences": {"type": "array", "items": {"type": "string"}},
        "conclusion": {"type": "string"},
        "counter_evidence": {"type": "array", "items": {"type": "string"}},
        "observation_points": {"type": "array", "items": {"type": "string"}},
        "cited_evidence_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "market_judgment", "confidence", "facts", "inferences", "conclusion",
        "counter_evidence", "observation_points", "cited_evidence_ids",
    ],
    "additionalProperties": False,
}


def build_prompt(coin: str, question: str, evidence: list[dict]) -> str:
    return ("You are a crypto market research analyst. Use only the supplied evidence. "
            "Separate facts from inferences and conclusion. Do not provide buy/sell advice. "
            "Cite evidence IDs for every material claim and lower confidence when signals conflict.\n\n"
            + json.dumps({"coin": coin, "question": question, "evidence": evidence}, ensure_ascii=False))


CRITIC_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "concerns", "fail"]},
        "summary": {"type": "string"},
        "confidence_adjustment": {"type": "number"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {"type": "string", "enum": ["high", "medium", "low"]},
                    "category": {"type": "string"},
                    "claim": {"type": "string"},
                    "issue": {"type": "string"},
                    "evidence_id": {"type": "string"},
                },
                "required": ["severity", "category", "claim", "issue", "evidence_id"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["verdict", "summary", "confidence_adjustment", "findings"],
    "additionalProperties": False,
}

_CRITIC_PROMPT = (
    "你是研究稽核員，任務是**挑戰**下面這份分析，不是複述或讚美它。請用繁體中文回覆。\n\n"
    "逐項檢查並列出問題（findings）：\n"
    "1. over_claim：某句結論的強度超過其引用證據能支撐的程度\n"
    "2. unsupported：某項主張沒有對應的證據，或引用的證據其實不談這件事\n"
    "3. ignored_counter：反方證據明明存在，卻沒有反映在結論或信心分數上\n"
    "4. stale_or_weak：引用了可靠度偏低（<0.5）或降級（reliability 0.20）的證據卻當成確證\n"
    "5. confidence：信心分數與證據衝突程度不相稱\n\n"
    "每個 finding 要指出具體是哪一句（claim）、問題是什麼（issue）、以及相關的 evidence_id"
    "（若無對應證據就填 'N/A'）。severity 只有在會改變讀者判斷時才用 high。\n"
    "confidence_adjustment 是你建議的信心調整值，範圍 -0.3 到 0，找不到問題就填 0。\n"
    "verdict：pass（沒有實質問題）、concerns（有需注意之處但結論仍成立）、"
    "fail（結論不被證據支撐）。\n"
    "若分析確實嚴謹，回 pass 並在 summary 說明你檢查了什麼 —— 不要為了交差而編造問題。\n\n"
)


def build_critic_prompt(coin: str, question: str, result: dict, evidence: list[dict]) -> str:
    reasoning = result.get("reasoning", {})
    stance = result.get("stance", {})
    payload = {
        "coin": coin,
        "question": question,
        "stance": {key: stance.get(key) for key in ("stance", "label", "bull_weight", "bear_weight", "basis")},
        "analysis": {
            "market_judgment": reasoning.get("market_judgment"),
            "conclusion": reasoning.get("conclusion"),
            "confidence": reasoning.get("confidence"),
            "facts": reasoning.get("facts"),
            "inferences": reasoning.get("inferences"),
            "counter_evidence": reasoning.get("counter_evidence"),
        },
        "risk_factors": result.get("risk_factors"),
        "evidence": [
            {"evidence_id": item.get("evidence_id"), "source": item.get("source"),
             "data_type": item.get("data_type"), "reliability_score": item.get("reliability_score"),
             "content": item.get("content")}
            for item in evidence
        ],
    }
    return _CRITIC_PROMPT + json.dumps(payload, ensure_ascii=False)


def configured_provider() -> str:
    """Return the explicitly selected provider, or infer one from configured credentials."""
    provider = os.getenv("LLM_PROVIDER", "").strip().lower()
    if not provider:
        if os.getenv("GEMINI_API_KEY"):
            return "gemini"
        if os.getenv("OPENAI_API_KEY"):
            return "openai"
        if os.getenv("BEDROCK_MODEL_ID"):
            return "bedrock"
        return "none"
    if provider not in {"bedrock", "gemini", "openai", "none"}:
        raise ValueError(f"Unsupported LLM_PROVIDER: {provider}")
    return provider


def _bedrock_region() -> str | None:
    """Prefer an explicit deployment setting, then Lambda/AWS standard region variables."""
    return (os.getenv("BEDROCK_REGION") or os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION"))


def _bedrock_model_id() -> str:
    model_id = os.getenv("BEDROCK_MODEL_ID", "").strip()
    if not model_id:
        raise RuntimeError("BEDROCK_MODEL_ID is not configured")
    return model_id


def _environment_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def _environment_temperature(name: str, default: float) -> float:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be a number") from error
    if not 0 <= value <= 1:
        raise ValueError(f"{name} must be between 0 and 1")
    return value


def llm_is_configured() -> bool:
    provider = configured_provider()
    return (
        provider == "gemini" and bool(os.getenv("GEMINI_API_KEY"))
    ) or (
        provider == "openai" and bool(os.getenv("OPENAI_API_KEY"))
    ) or (
        provider == "bedrock" and bool(os.getenv("BEDROCK_MODEL_ID", "").strip())
    )


def llm_runtime_info() -> dict:
    provider = configured_provider()
    model = (
        os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
        if provider == "gemini"
        else os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        if provider == "openai"
        else os.getenv("BEDROCK_MODEL_ID")
        if provider == "bedrock"
        else None
    )
    return {"provider": provider, "model": model, "region": _bedrock_region() if provider == "bedrock" else None}


def _validate_required_fields(result: object, schema: dict, schema_name: str) -> dict:
    if not isinstance(result, dict):
        raise ValueError(f"{schema_name} response must be a JSON object")
    missing = set(schema["required"]) - set(result)
    if missing:
        raise ValueError(f"{schema_name} response missing required fields: {sorted(missing)}")
    return result


def _parse_json_object(output_text: str | None, schema: dict, schema_name: str) -> dict:
    if not output_text:
        raise ValueError(f"{schema_name} response contained no structured output")
    return _validate_required_fields(json.loads(output_text), schema, schema_name)


def _openai_output_text(raw: dict) -> str | None:
    output_text = raw.get("output_text")
    if output_text:
        return output_text
    for item in raw.get("output", []):
        for content in item.get("content", []):
            if content.get("type") in {"output_text", "text"}:
                return content.get("text")
    return None


def _gemini_output_text(raw: dict, schema_name: str) -> str:
    candidates = raw.get("candidates", [])
    if not candidates:
        reason = raw.get("promptFeedback", {}).get("blockReason", "no candidates")
        raise ValueError(f"Gemini {schema_name} response blocked or empty: {reason}")
    parts = candidates[0].get("content", {}).get("parts", [])
    return "".join(part.get("text", "") for part in parts)


def _bedrock_output_text(raw: dict) -> str:
    content = raw.get("output", {}).get("message", {}).get("content", [])
    texts = [item.get("text", "") for item in content if isinstance(item, dict) and item.get("text")]
    if not texts:
        raise ValueError("Bedrock response contained no text content")
    return "".join(texts)


def _generate_gemini(prompt: str, schema: dict, timeout_seconds: int) -> str:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")
    model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseJsonSchema": schema,
            "temperature": 0.1 if schema is CRITIC_SCHEMA else 0.2,
        },
    }
    endpoint = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{quote(model, safe='')}:generateContent"
    )
    request = Request(
        endpoint,
        data=json.dumps(payload).encode(),
        headers={
            "x-goog-api-key": api_key,
            "Content-Type": "application/json",
            "User-Agent": "hoyabit-market-research-agent/1.0",
        },
        method="POST",
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        return _gemini_output_text(json.loads(response.read().decode()), "structured")


def _generate_openai(prompt: str, schema: dict, schema_name: str, timeout_seconds: int) -> str | None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    payload = {
        "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        "input": prompt,
        "text": {"format": {"type": "json_schema", "name": schema_name, "strict": True, "schema": schema}},
        "store": False,
    }
    request = Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        return _openai_output_text(json.loads(response.read().decode()))


def _bedrock_retry_prompt(prompt: str, schema: dict) -> str:
    return (
        f"{prompt}\n\nYour previous response was invalid. Return only one valid JSON object that "
        f"includes every required field and conforms to this JSON Schema:\n{json.dumps(schema, ensure_ascii=False)}"
    )


def _generate_bedrock(prompt: str, schema: dict, schema_name: str, timeout_seconds: int) -> dict:
    """Use Converse and retry once only for malformed or incomplete structured output."""
    model_id = _bedrock_model_id()
    if boto3 is None:
        raise RuntimeError("Amazon Bedrock requires boto3; install it locally or run in AWS Lambda")
    region = _bedrock_region()
    client_kwargs = {"region_name": region} if region else {}
    if BotoConfig is not None:
        client_kwargs["config"] = BotoConfig(
            connect_timeout=timeout_seconds,
            read_timeout=timeout_seconds,
            retries={"max_attempts": 0},
        )
    client = boto3.client("bedrock-runtime", **client_kwargs)
    request_prompt = prompt + (
        "\n\nReturn only one valid JSON object. It must conform to this JSON Schema:\n"
        + json.dumps(schema, ensure_ascii=False)
    )
    for attempt in range(2):
        response = client.converse(
            modelId=model_id,
            messages=[{"role": "user", "content": [{"text": request_prompt}]}],
            inferenceConfig={
                "maxTokens": _environment_int("BEDROCK_MAX_TOKENS", 2048),
                "temperature": _environment_temperature("BEDROCK_TEMPERATURE", 0.2),
            },
        )
        try:
            return _parse_json_object(_bedrock_output_text(response), schema, schema_name)
        except (json.JSONDecodeError, ValueError) as error:
            if attempt:
                raise ValueError(f"Bedrock {schema_name} response was invalid after one retry: {error}") from error
            request_prompt = _bedrock_retry_prompt(prompt, schema)
    raise AssertionError("Bedrock JSON retry loop exited unexpectedly")


def generate_json_with_llm(prompt: str, schema: dict, schema_name: str, timeout_seconds: int) -> dict:
    """Generate and minimally validate a schema-shaped JSON object from the selected provider."""
    provider = configured_provider()
    if provider == "gemini":
        return _parse_json_object(_generate_gemini(prompt, schema, timeout_seconds), schema, schema_name)
    if provider == "openai":
        return _parse_json_object(_generate_openai(prompt, schema, schema_name, timeout_seconds), schema, schema_name)
    if provider == "bedrock":
        return _generate_bedrock(prompt, schema, schema_name, timeout_seconds)
    raise RuntimeError(f"No LLM provider is configured (provider={provider})")


def _validate_critic_result(result: dict) -> dict:
    if result["verdict"] not in {"pass", "concerns", "fail"}:
        raise ValueError(f"Invalid critic verdict: {result['verdict']}")
    adjustment = result.get("confidence_adjustment")
    if not isinstance(adjustment, (int, float)) or not -1 <= adjustment <= 0:
        raise ValueError("Critic confidence_adjustment must be between -1 and 0")
    return result


def _parse_critic_output(output_text: str | None) -> dict:
    return _validate_critic_result(_parse_json_object(output_text, CRITIC_SCHEMA, "Critic"))


def critique_with_llm(coin: str, question: str, result: dict, evidence: list[dict], timeout: int = 60) -> dict:
    """Audit an analysis independently; the returned critique may only lower confidence."""
    prompt = build_critic_prompt(coin, question, result, evidence)
    return _validate_critic_result(
        generate_json_with_llm(prompt, CRITIC_SCHEMA, "research_critique", timeout)
    )


def _validate_analysis_result(result: dict) -> dict:
    confidence = result.get("confidence")
    if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        raise ValueError("LLM confidence must be between 0 and 1")
    return result


def _parse_json_output(output_text: str | None) -> dict:
    return _validate_analysis_result(_parse_json_object(output_text, ANALYSIS_SCHEMA, "LLM"))


def analyze_with_llm(coin: str, question: str, evidence: list[dict]) -> dict:
    return _validate_analysis_result(
        generate_json_with_llm(build_prompt(coin, question, evidence), ANALYSIS_SCHEMA, "market_analysis", 60)
    )
