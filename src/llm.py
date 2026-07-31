"""Provider-neutral LLM reasoning adapters for Gemini and OpenAI."""

from __future__ import annotations

import json
import os
from urllib.parse import quote
from urllib.request import Request, urlopen


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
    "required": ["market_judgment", "confidence", "facts", "inferences", "conclusion", "counter_evidence", "observation_points", "cited_evidence_ids"],
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


def _parse_critic_output(output_text: str | None) -> dict:
    if not output_text:
        raise ValueError("Critic response contained no structured output")
    result = json.loads(output_text)
    missing = set(CRITIC_SCHEMA["required"]) - set(result)
    if missing:
        raise ValueError(f"Critic response missing required fields: {sorted(missing)}")
    if result["verdict"] not in {"pass", "concerns", "fail"}:
        raise ValueError(f"Invalid critic verdict: {result['verdict']}")
    adjustment = result.get("confidence_adjustment")
    if not isinstance(adjustment, (int, float)) or not -1 <= adjustment <= 0:
        raise ValueError("Critic confidence_adjustment must be between -1 and 0")
    return result


def critique_with_llm(coin: str, question: str, result: dict, evidence: list[dict], timeout: int = 60) -> dict:
    """Second-opinion pass: audit the analysis against its own evidence.

    Deliberately a separate call rather than another field on the analysis schema -- a model asked
    to produce a conclusion and critique it in one breath rates its own work generously.
    """
    provider = configured_provider()
    prompt = build_critic_prompt(coin, question, result, evidence)
    if provider == "gemini":
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured")
        model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json",
                                 "responseJsonSchema": CRITIC_SCHEMA, "temperature": 0.1},
        }
        endpoint = ("https://generativelanguage.googleapis.com/v1beta/models/"
                    f"{quote(model, safe='')}:generateContent")
        request = Request(endpoint, data=json.dumps(payload).encode(), method="POST", headers={
            "x-goog-api-key": api_key, "Content-Type": "application/json",
            "User-Agent": "hoyabit-market-research-agent/1.0"})
        with urlopen(request, timeout=timeout) as response:
            raw = json.loads(response.read().decode())
        candidates = raw.get("candidates", [])
        if not candidates:
            reason = raw.get("promptFeedback", {}).get("blockReason", "no candidates")
            raise ValueError(f"Gemini critic response blocked or empty: {reason}")
        parts = candidates[0].get("content", {}).get("parts", [])
        return _parse_critic_output("".join(part.get("text", "") for part in parts))
    if provider == "openai":
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")
        payload = {
            "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            "input": prompt,
            "text": {"format": {"type": "json_schema", "name": "research_critique",
                                "strict": True, "schema": CRITIC_SCHEMA}},
            "store": False,
        }
        request = Request("https://api.openai.com/v1/responses", data=json.dumps(payload).encode(),
                          method="POST", headers={"Authorization": f"Bearer {api_key}",
                                                  "Content-Type": "application/json"})
        with urlopen(request, timeout=timeout) as response:
            raw = json.loads(response.read().decode())
        output_text = raw.get("output_text")
        if not output_text:
            for item in raw.get("output", []):
                for content in item.get("content", []):
                    if content.get("type") in {"output_text", "text"}:
                        output_text = content.get("text")
                        break
        return _parse_critic_output(output_text)
    raise RuntimeError(f"No LLM provider configured for critique (provider={provider})")


def configured_provider() -> str:
    """Return the explicitly selected provider, or infer one from available keys."""
    provider = os.getenv("LLM_PROVIDER", "").strip().lower()
    if not provider:
        if os.getenv("GEMINI_API_KEY"):
            return "gemini"
        if os.getenv("OPENAI_API_KEY"):
            return "openai"
        return "none"
    if provider not in {"gemini", "openai", "none"}:
        raise ValueError(f"Unsupported LLM_PROVIDER: {provider}")
    return provider


def llm_is_configured() -> bool:
    provider = configured_provider()
    return (
        provider == "gemini" and bool(os.getenv("GEMINI_API_KEY"))
    ) or (
        provider == "openai" and bool(os.getenv("OPENAI_API_KEY"))
    )


def llm_runtime_info() -> dict:
    provider = configured_provider()
    model = (
        os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
        if provider == "gemini"
        else os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        if provider == "openai"
        else None
    )
    return {"provider": provider, "model": model}


def _parse_json_output(output_text: str | None) -> dict:
    if not output_text:
        raise ValueError("LLM response contained no structured output")
    result = json.loads(output_text)
    missing = set(ANALYSIS_SCHEMA["required"]) - set(result)
    if missing:
        raise ValueError(f"LLM response missing required fields: {sorted(missing)}")
    confidence = result.get("confidence")
    if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        raise ValueError("LLM confidence must be between 0 and 1")
    return result


def _analyze_openai(coin: str, question: str, evidence: list[dict]) -> dict:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    payload = {
        "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        "input": build_prompt(coin, question, evidence),
        "text": {"format": {"type": "json_schema", "name": "market_analysis", "strict": True, "schema": ANALYSIS_SCHEMA}},
        "store": False,
    }
    request = Request("https://api.openai.com/v1/responses", data=json.dumps(payload).encode(), headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=60) as response:
        raw = json.loads(response.read().decode())
    output_text = raw.get("output_text")
    if not output_text:
        for item in raw.get("output", []):
            for content in item.get("content", []):
                if content.get("type") in {"output_text", "text"}:
                    output_text = content.get("text")
                    break
    return _parse_json_output(output_text)


def _analyze_gemini(coin: str, question: str, evidence: list[dict]) -> dict:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")
    model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": build_prompt(coin, question, evidence)}],
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseJsonSchema": ANALYSIS_SCHEMA,
            "temperature": 0.2,
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
    with urlopen(request, timeout=60) as response:
        raw = json.loads(response.read().decode())
    candidates = raw.get("candidates", [])
    if not candidates:
        reason = raw.get("promptFeedback", {}).get("blockReason", "no candidates")
        raise ValueError(f"Gemini response blocked or empty: {reason}")
    parts = candidates[0].get("content", {}).get("parts", [])
    output_text = "".join(part.get("text", "") for part in parts)
    return _parse_json_output(output_text)


def analyze_with_llm(coin: str, question: str, evidence: list[dict]) -> dict:
    provider = configured_provider()
    if provider == "gemini":
        return _analyze_gemini(coin, question, evidence)
    if provider == "openai":
        return _analyze_openai(coin, question, evidence)
    raise RuntimeError("No LLM provider is configured")
