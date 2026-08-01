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


# T4：分析回應允許額外帶 `claims` 提案。`ANALYSIS_SCHEMA` 本身不變（既有欄位與 required 清單
# 一字未改，`additionalProperties: False` 也保留），所以現有 provider 的行為完全相同；只有當上游
# 模型或注入的 client 主動回傳 `claims` 時，`normalise_analysis_claims()` 會把它挪到
# `claim_proposals`，明確標示這是「提案」而不是最終 Claim。
#
# 這樣做的理由是邊界：模型提出的 Claim 文字必須先經 `src/claim_graph.py` 驗證 Evidence ID、
# 檢查 Fact 是否混寫推論，最終 verdict 與 confidence 一律由 deterministic Python 計算。若直接把
# 模型的 `claims` 當成結果，模型就等於自己給了自己信心分數。
CLAIM_PROPOSAL_FIELD = "claims"
CLAIM_PROPOSAL_RESULT_FIELD = "claim_proposals"


def build_prompt(coin: str, question: str, evidence: list[dict]) -> str:
    return ("You are a crypto market research analyst. Use only the supplied evidence. "
            "Separate facts from inferences and conclusion. Do not provide buy/sell advice. "
            "Cite evidence IDs for every material claim and lower confidence when signals conflict.\n\n"
            "分層規則（T4）：facts 只寫資料直接顯示的觀察並附 evidence ID，不得出現「因此」「預期」"
            "「將會」等推論或預測用語；解釋寫在 inferences，對研究問題的回答寫在 conclusion。\n"
            "存在高品質反方證據時必須寫進 counter_evidence，不得隱藏。\n"
            "confidence 只是你的參考值：每個 Claim 的最終信心分數由程式依證據品質、領域覆蓋度、"
            "來源多元性、訊號一致性與反方覆蓋度計算，你的數字不會成為最終分數。\n\n"
            + json.dumps({"coin": coin, "question": question, "evidence": evidence}, ensure_ascii=False))


def normalise_analysis_claims(result: dict) -> dict:
    """把分析回應裡的 `claims` 改名成 `claim_proposals`，其餘欄位原樣保留。

    回傳的 dict 一定含有既有的 `market_judgment`／`facts`／`inferences`／`conclusion` 等欄位，
    因此對報告渲染與既有測試完全相容；差別只在模型的 Claim 文字被降級為待驗證的提案。
    """
    if CLAIM_PROPOSAL_FIELD not in result:
        return result
    normalised = dict(result)
    proposals = normalised.pop(CLAIM_PROPOSAL_FIELD)
    normalised[CLAIM_PROPOSAL_RESULT_FIELD] = proposals if isinstance(proposals, list) else []
    return normalised


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


class ExistingLLMClient:
    """`LLMClient` 實作：包裝本檔既有的 Gemini／OpenAI／Bedrock adapter。

    行為與直接呼叫 `generate_json_with_llm()` 完全相同（含 provider 選擇、JSON 驗證與
    Bedrock 的單次重試），存在的目的只是讓呼叫端依賴介面而非依賴模組函式。
    """

    def generate_json(self, *, prompt: str, schema: dict, schema_name: str, timeout_seconds: float) -> dict:
        return generate_json_with_llm(prompt, schema, schema_name, int(timeout_seconds))


class OfflineLLMClient:
    """`LLMClient` 實作：不呼叫任何模型，直接拋出 RuntimeError。

    供離線模式與測試明確注入使用。呼叫端（Orchestrator）本來就會攔下例外並改用
    deterministic offline reasoning，所以注入此 client 等同於強制走離線推理路徑。
    """

    def generate_json(self, *, prompt: str, schema: dict, schema_name: str, timeout_seconds: float) -> dict:
        raise RuntimeError(
            f"OfflineLLMClient does not call any model ({schema_name}); "
            "callers must fall back to deterministic offline reasoning"
        )


def default_llm_client() -> "ExistingLLMClient":
    """預設 client。維持現有行為：未設定 provider 時由 adapter 自行拋出可診斷的錯誤。"""
    return ExistingLLMClient()


def _validate_critic_result(result: dict) -> dict:
    if result["verdict"] not in {"pass", "concerns", "fail"}:
        raise ValueError(f"Invalid critic verdict: {result['verdict']}")
    adjustment = result.get("confidence_adjustment")
    if not isinstance(adjustment, (int, float)) or not -1 <= adjustment <= 0:
        raise ValueError("Critic confidence_adjustment must be between -1 and 0")
    return result


def _parse_critic_output(output_text: str | None) -> dict:
    return _validate_critic_result(_parse_json_object(output_text, CRITIC_SCHEMA, "Critic"))


def critique_with_llm(coin: str, question: str, result: dict, evidence: list[dict], timeout: int = 60,
                      client: object | None = None) -> dict:
    """Audit an analysis independently; the returned critique may only lower confidence.

    `client` 可注入任何 `LLMClient`（測試用 mock、離線 client、日後的 Bedrock client）；
    未提供時使用預設 client，行為與注入前完全相同。
    """
    prompt = build_critic_prompt(coin, question, result, evidence)
    raw = (client or default_llm_client()).generate_json(
        prompt=prompt, schema=CRITIC_SCHEMA, schema_name="research_critique", timeout_seconds=timeout,
    )
    return _validate_critic_result(_validate_required_fields(raw, CRITIC_SCHEMA, "Critic"))


def _validate_analysis_result(result: dict) -> dict:
    confidence = result.get("confidence")
    if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        raise ValueError("LLM confidence must be between 0 and 1")
    return result


def _parse_json_output(output_text: str | None) -> dict:
    return _validate_analysis_result(_parse_json_object(output_text, ANALYSIS_SCHEMA, "LLM"))


def analyze_with_llm(coin: str, question: str, evidence: list[dict], client: object | None = None) -> dict:
    """Produce the structured analysis. `client` 可注入 `LLMClient`；預設維持原有行為。"""
    raw = (client or default_llm_client()).generate_json(
        prompt=build_prompt(coin, question, evidence),
        schema=ANALYSIS_SCHEMA,
        schema_name="market_analysis",
        timeout_seconds=60,
    )
    validated = _validate_analysis_result(_validate_required_fields(raw, ANALYSIS_SCHEMA, "LLM"))
    return normalise_analysis_claims(validated)
