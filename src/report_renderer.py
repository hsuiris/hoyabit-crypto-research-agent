"""Deterministic Report Renderer（T5 函式庫層，Track B4）。

把「run 資訊、research plan、claim graph、evidence、critique」這幾份 dict 組成一份固定結構的
`report.md`。這個模組刻意只做一件事：**把已經算好的資料轉成文字**，不做任何判斷、不算分、
不呼叫 LLM、不寫檔案、不連網路。信心分數、verdict、hard cap 全部由 T3／T4 的 deterministic
Python 程式算好後傳進來，本模組只負責忠實呈現。

四條邊界：

1. **段落順序固定**，不受輸入內容影響。呼叫端（未來由 Track A 接線）永遠會得到相同標題結構，
   LLM 不能改變報告骨架。
2. **相同輸入必須產生逐字相同的 markdown**：不使用目前時間、隨機數或不穩定的字典排序，
   任何順序都直接沿用輸入資料本身的順序（呼叫端已保證 Evidence／Claim 順序穩定）。
3. **缺欄位一律顯示 `N/A` 或「本次無資料」，不得拋例外**——包含缺 claims、缺 plan、缺 critique，
   以及缺少 T3 新欄位的舊 fixture。
4. **`insufficient_evidence` 的 Claim 不得渲染成有方向的結論**；`rejected` 的 Evidence
   不得出現在支持／反方證據或 Evidence Sources 段落。

輸入資料形狀（所有鍵皆可省略）：

    {
        "run": {"run_id", "started_at", "completed_at", "question", "coins", "mode",
                "provider", "model"},
        "plan": ResearchPlan 形狀的 dict（見 src/schemas.py），
        "claims_document": claim_graph.claims_document(graph) 的輸出，或直接一個 claim dict 清單，
        "evidence": Evidence dict 清單（asdict(Evidence) 或純 dict 皆可），
        "critique": Critic 輸出（形狀尚未凍結，本模組只做通用呈現，缺欄位一律顯示 N/A），
    }

對外只有一個函式：:func:`render_report`。
"""

from __future__ import annotations

from src.schemas import CONFIDENCE_COMPONENT_KEYS, VERDICT_INSUFFICIENT_EVIDENCE, VERDICTS

# --------------------------------------------------------------------------------------
# 常數
# --------------------------------------------------------------------------------------

_NOT_AVAILABLE = "N/A"
_NO_DATA = "本次無資料"

_VERDICT_LABELS = {
    "supported": "支持（supported）",
    "partially_supported": "部分支持（partially_supported）",
    "mixed": "訊號混合（mixed）",
    "contradicted": "反對（contradicted）",
    VERDICT_INSUFFICIENT_EVIDENCE: "證據不足（insufficient_evidence）",
}

_TASK_MODE_LABELS = {
    "describe_market_state": "描述市場現況",
    "test_hypothesis": "驗證假設",
    "compare_assets": "比較標的",
    "explain_driver": "解釋驅動因子",
    "assess_consistency": "評估訊號一致性",
    "identify_risks": "辨識風險",
    "identify_attention_conditions": "辨識後續關注條件",
}

_REJECTED_STATUS = "rejected"

_DISCLAIMER = (
    "本報告為研究支援，並非投資建議；不構成買賣訊號，也不對未來市場表現做出保證。\n"
    "信心分數（confidence）為 heuristic evidence score，反映本次證據的可稽核程度，"
    "不是校準過的市場正確機率。"
)

_INSUFFICIENT_INFERENCE_TEXT = "本判斷因證據不足，未產生方向性推論（insufficient_evidence）。"
_INSUFFICIENT_CONCLUSION_TEXT = "本判斷因證據不足，未產生方向性結論（insufficient_evidence）。"


# --------------------------------------------------------------------------------------
# 通用小工具：一律不拋例外，缺資料就回退成安全字串
# --------------------------------------------------------------------------------------

def _text(value, default: str = _NOT_AVAILABLE) -> str:
    """把任意輸入轉成顯示用字串；空字串／None／純空白都視為缺資料。"""
    if value is None:
        return default
    rendered = str(value).strip()
    return rendered if rendered else default


def _dict(value) -> dict:
    """把輸入正規化成 dict；非 dict（包含 None）一律回傳空 dict，不拋例外。"""
    return value if isinstance(value, dict) else {}


def _list(value) -> list:
    """把輸入正規化成 list；非 list（包含 None、單一字典）一律回傳空 list。"""
    return value if isinstance(value, list) else []


def _bullets(items, default: str = _NO_DATA) -> str:
    """把字串清單轉成 markdown bullet；清單為空或非清單時顯示預設訊息，不留空段落。"""
    cleaned = [str(item).strip() for item in _list(items) if str(item or "").strip()]
    if not cleaned:
        return default
    return "\n".join("- %s" % item for item in cleaned)


def _round(value, digits: int = 4):
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return value


# --------------------------------------------------------------------------------------
# Evidence 腳註：順序完全依照輸入清單的位置，rejected 的 Evidence 一律排除
# --------------------------------------------------------------------------------------

def _build_footnotes(evidence_list, rejected_ids) -> tuple:
    """回傳 (evidence_id -> 腳註編號, 依編號排序的 (編號, evidence dict) 清單)。

    編號完全由 ``evidence_list`` 中「非 rejected、未重複」項目第一次出現的位置決定，
    不做任何排序或去重以外的調整，確保相同輸入永遠得到相同腳註編號。
    """
    footnote_by_id: dict = {}
    ordered: list = []
    rejected = set(rejected_ids or ())
    for item in evidence_list:
        record = item if isinstance(item, dict) else {}
        evidence_id = _text(record.get("evidence_id"), "")
        if not evidence_id or evidence_id in footnote_by_id:
            continue
        if record.get("verification_status") == _REJECTED_STATUS or evidence_id in rejected:
            continue
        footnote_by_id[evidence_id] = len(ordered) + 1
        ordered.append((footnote_by_id[evidence_id], evidence_id, record))
    return footnote_by_id, ordered


def _cite(evidence_ids, footnote_by_id, rejected_ids) -> str:
    """把一組 evidence_id 轉成引用字串；unknown／rejected 的 ID 一律略過，不拋例外。"""
    rejected = set(rejected_ids or ())
    parts = []
    for raw_id in evidence_ids or []:
        evidence_id = _text(raw_id, "")
        if not evidence_id or evidence_id in rejected:
            continue
        number = footnote_by_id.get(evidence_id)
        parts.append("%s[#%d]" % (evidence_id, number) if number else evidence_id)
    return "、".join(parts) if parts else "（無）"


# --------------------------------------------------------------------------------------
# 段落：分析標的與題目 / 資料截止與時間範圍
# --------------------------------------------------------------------------------------

def _coin_label(run_info: dict, plan_info: dict) -> str:
    coins = _list(run_info.get("coins")) or _list(plan_info.get("coins"))
    labels = [_text(coin, "") for coin in coins]
    labels = [label for label in labels if label and label != _NOT_AVAILABLE]
    if not labels:
        return "未指定標的"
    return " vs ".join(labels) if len(labels) > 1 else labels[0]


def _render_header(run_info: dict, plan_info: dict) -> str:
    coin_label = _coin_label(run_info, plan_info)
    question = _text(run_info.get("question") or plan_info.get("primary_question"))
    lines = [
        "# %s Market Research" % coin_label,
        "",
        "## 分析標的與題目",
        "- 標的：%s" % coin_label,
        "- 研究問題：%s" % question,
        "- Run ID：%s" % _text(run_info.get("run_id")),
    ]
    mode = _text(run_info.get("mode"), "")
    if mode and mode != _NOT_AVAILABLE:
        lines.append("- 執行模式：%s" % mode)
    return "\n".join(lines)


def _render_time_scope(run_info: dict, plan_info: dict) -> str:
    time_window = _dict(plan_info.get("time_window"))
    lines = [
        "## 資料截止與時間範圍",
        "- 開始時間：%s" % _text(run_info.get("started_at")),
        "- 完成時間：%s" % _text(run_info.get("completed_at")),
    ]
    if time_window:
        days = _text(time_window.get("days"))
        source = _text(time_window.get("source"))
        lines.append("- 研究時間窗：%s 天（來源：%s）" % (days, source))
    else:
        lines.append("- 研究時間窗：%s" % _NOT_AVAILABLE)
    return "\n".join(lines)


def _render_plan_summary(plan_info: dict) -> str:
    lines = ["## 研究計畫摘要"]
    if not plan_info:
        lines.append(_NO_DATA)
        return "\n".join(lines)

    task_modes = _list(plan_info.get("task_modes"))
    mode_labels = [_TASK_MODE_LABELS.get(mode, mode) for mode in task_modes if str(mode or "").strip()]
    lines.append("- Task modes：%s" % ("、".join(mode_labels) if mode_labels else _NO_DATA))

    required_domains = [str(item) for item in _list(plan_info.get("required_domains")) if str(item or "").strip()]
    lines.append("- 必要資料領域：%s" % ("、".join(required_domains) if required_domains else _NO_DATA))

    comparison_dimensions = [
        str(item) for item in _list(plan_info.get("comparison_dimensions")) if str(item or "").strip()
    ]
    if comparison_dimensions:
        lines.append("- 比較維度：%s" % "、".join(comparison_dimensions))

    assumptions = [str(item) for item in _list(plan_info.get("assumptions")) if str(item or "").strip()]
    lines.append("")
    lines.append("### 假設前提")
    lines.append(_bullets(assumptions))

    hypotheses = _list(plan_info.get("hypotheses"))
    if hypotheses:
        lines.append("")
        lines.append("### 待驗證假設")
        for hypothesis in hypotheses:
            record = _dict(hypothesis)
            lines.append("- %s：%s" % (
                _text(record.get("hypothesis_id"), "H?"), _text(record.get("statement"))))
    return "\n".join(lines)


# --------------------------------------------------------------------------------------
# claims_document 正規化：支援「完整 document dict」或「裸 claim 清單」兩種輸入
# --------------------------------------------------------------------------------------

def _extract_claims_bundle(data: dict) -> dict:
    """把 ``claims_document`` 或裸 claim 清單正規化成統一形狀，缺資料一律給安全預設值。"""
    bundle = data.get("claims_document")
    if bundle is None:
        bundle = data.get("claims")

    if isinstance(bundle, dict) and isinstance(bundle.get("claims"), list):
        claims = _list(bundle.get("claims"))
        claim_source = bundle.get("claim_source", "")
        fallback_reason = bundle.get("fallback_reason", "")
        hypothesis_assessments = _list(bundle.get("hypothesis_assessments"))
        rejected_evidence_ids = _list(bundle.get("rejected_evidence_ids"))
    elif isinstance(bundle, list):
        claims = bundle
        claim_source, fallback_reason = "", ""
        hypothesis_assessments, rejected_evidence_ids = [], []
    else:
        claims = []
        claim_source, fallback_reason = "", ""
        hypothesis_assessments, rejected_evidence_ids = [], []

    # 允許呼叫端在頂層直接覆寫，方便未接線前的測試與未來整合彈性。
    claim_source = data.get("claim_source", claim_source)
    fallback_reason = data.get("fallback_reason", fallback_reason)
    rejected_evidence_ids = data.get("rejected_evidence_ids", rejected_evidence_ids)
    hypothesis_assessments = data.get("hypothesis_assessments", hypothesis_assessments)

    return {
        "claims": [c for c in claims if isinstance(c, dict)],
        "claim_source": claim_source,
        "fallback_reason": fallback_reason,
        "hypothesis_assessments": _list(hypothesis_assessments),
        "rejected_evidence_ids": [str(item) for item in _list(rejected_evidence_ids)],
    }


def _render_stance_overview(bundle: dict) -> str:
    lines = ["## 立場與判斷總覽"]
    claims = bundle["claims"]
    if not claims:
        lines.append("本次沒有可用的 Claim，資料不足，不提供方向性判斷（insufficient_evidence）。")
        return "\n".join(lines)

    source_label = "LLM 提案（經 deterministic 計分覆寫）" if bundle["claim_source"] == "llm" else "deterministic fallback"
    lines.append("- Claim 產生方式：%s" % source_label)
    if bundle["fallback_reason"]:
        lines.append("- Fallback 原因：%s" % _text(bundle["fallback_reason"]))
    lines.append("")
    for index, claim in enumerate(claims, start=1):
        verdict = _text(claim.get("verdict"), VERDICT_INSUFFICIENT_EVIDENCE)
        label = _VERDICT_LABELS.get(verdict, verdict)
        confidence = _dict(claim.get("confidence"))
        lines.append("- 判斷 %d（%s，信心 %s）：%s" % (
            index, label, _text(_round(confidence.get("score"))), _text(claim.get("statement"))))
    return "\n".join(lines)


# --------------------------------------------------------------------------------------
# 每個 Claim 的 Fact → Inference → Conclusion 與正反證據
# --------------------------------------------------------------------------------------

def _render_facts(facts, footnote_by_id, rejected_ids) -> str:
    fact_lines = []
    for fact in facts or []:
        record = _dict(fact)
        statement = _text(record.get("statement"), "")
        if not statement:
            continue
        citation = _cite(record.get("evidence_ids"), footnote_by_id, rejected_ids)
        fact_lines.append("%s（%s）" % (statement, citation))
    return _bullets(fact_lines)


def _render_confidence(confidence: dict) -> str:
    confidence = _dict(confidence)
    if not confidence:
        return "%s\n\n- Limiters：%s" % (_NOT_AVAILABLE, _NO_DATA)
    lines = [
        "- 分數：%s" % _text(_round(confidence.get("score"))),
        "- 等級：%s" % _text(confidence.get("level")),
        "- 類型：%s" % _text(confidence.get("type"), "heuristic"),
    ]
    components = _dict(confidence.get("components"))
    if components:
        lines.append("- Components：")
        keys = [key for key in CONFIDENCE_COMPONENT_KEYS if key in components]
        keys += [key for key in components if key not in keys]
        for key in keys:
            lines.append("  - %s: %s" % (key, _text(_round(components.get(key)))))
    else:
        lines.append("- Components：%s" % _NO_DATA)
    limiters = [str(item) for item in _list(confidence.get("limiters")) if str(item or "").strip()]
    lines.append("- Limiters：%s" % ("、".join(limiters) if limiters else "（無）"))
    return "\n".join(lines)


def _render_claim(index: int, claim: dict, footnote_by_id: dict, rejected_ids: list) -> str:
    verdict = _text(claim.get("verdict"), VERDICT_INSUFFICIENT_EVIDENCE)
    if verdict not in VERDICTS:
        verdict = VERDICT_INSUFFICIENT_EVIDENCE
    is_insufficient = verdict == VERDICT_INSUFFICIENT_EVIDENCE
    label = _VERDICT_LABELS.get(verdict, verdict)

    lines = [
        "## 判斷 %d：%s" % (index, _text(claim.get("statement"))),
        "",
        "- Verdict：%s" % label,
        "- Claim ID：%s" % _text(claim.get("claim_id")),
    ]
    if is_insufficient:
        lines.append("- ⚠ 本判斷資料不足，以下內容僅供追蹤，不構成方向性結論。")
    lines.append("")

    lines.append("### Fact")
    lines.append(_render_facts(claim.get("facts"), footnote_by_id, rejected_ids))
    lines.append("")

    lines.append("### Inference")
    lines.append(_INSUFFICIENT_INFERENCE_TEXT if is_insufficient else _text(claim.get("inference"), _NO_DATA))
    lines.append("")

    lines.append("### Conclusion")
    lines.append(_INSUFFICIENT_CONCLUSION_TEXT if is_insufficient else _text(claim.get("conclusion"), _NO_DATA))
    lines.append("")

    lines.append("### 支持證據")
    lines.append(_cite(claim.get("supporting_evidence_ids"), footnote_by_id, rejected_ids))
    lines.append("")

    lines.append("### 反方證據")
    lines.append(_cite(claim.get("contradicting_evidence_ids"), footnote_by_id, rejected_ids))
    lines.append("")

    lines.append("### 信心與 Limiters")
    lines.append(_render_confidence(claim.get("confidence")))
    lines.append("")

    lines.append("### 限制")
    lines.append(_bullets(claim.get("limitations")))
    lines.append("")

    lines.append("### 可能推翻結論的條件")
    lines.append(_bullets(claim.get("invalidation_conditions")))
    lines.append("")

    lines.append("### 後續觀察重點")
    lines.append(_bullets(claim.get("watchpoints")))

    return "\n".join(lines)


def _render_hypothesis_assessments(hypothesis_assessments: list) -> str:
    lines = ["## 假設檢驗"]
    if not hypothesis_assessments:
        lines.append(_NO_DATA)
        return "\n".join(lines)
    for item in hypothesis_assessments:
        record = _dict(item)
        lines.append("- %s（Claim %s，verdict %s，信心 %s）：支持強度 %s／反對強度 %s" % (
            _text(record.get("hypothesis_id"), "H?"),
            _text(record.get("claim_id")),
            _text(record.get("verdict")),
            _text(_round(record.get("confidence"))),
            _text(_round(record.get("support_strength"))),
            _text(_round(record.get("contradiction_strength"))),
        ))
    return "\n".join(lines)


# --------------------------------------------------------------------------------------
# 稽核（Critic）：形狀尚未凍結，一律以通用、防禦性方式呈現
# --------------------------------------------------------------------------------------

def _render_critique(critique) -> str:
    lines = ["## 稽核（Critic）"]
    critique = _dict(critique)
    if not critique:
        lines.append("%s（本次無稽核資料）" % _NOT_AVAILABLE)
        return "\n".join(lines)

    status = critique.get("status") or critique.get("gate_status")
    lines.append("- 狀態：%s" % _text(status))

    skipped = critique.get("skipped")
    degraded = critique.get("degraded")
    if skipped or degraded:
        lines.append("- 降級／略過原因：%s" % _text(critique.get("reason") or critique.get("fallback_reason")))

    flags = _list(critique.get("flags")) or _list(critique.get("warnings"))
    if flags:
        lines.append("- 稽核標記：")
        for flag in flags:
            record = _dict(flag)
            category = _text(record.get("category"), _text(flag, "N/A") if not isinstance(flag, dict) else "N/A")
            note = _text(record.get("note"), "")
            claim_id = _text(record.get("claim_id"), "")
            detail = " / ".join(part for part in (claim_id, note) if part and part != _NOT_AVAILABLE)
            lines.append("  - %s%s" % (category, "：%s" % detail if detail else ""))
    else:
        lines.append("- 稽核標記：%s" % _NO_DATA)

    adjustment = critique.get("confidence_adjustment")
    if adjustment is not None:
        lines.append("- Confidence adjustment：%s" % _text(_round(adjustment)))
    return "\n".join(lines)


# --------------------------------------------------------------------------------------
# Evidence Sources
# --------------------------------------------------------------------------------------

def _render_evidence_sources(ordered_evidence: list) -> str:
    lines = ["## Evidence Sources"]
    if not ordered_evidence:
        lines.append(_NO_DATA)
        return "\n".join(lines)
    for number, evidence_id, record in ordered_evidence:
        source = _text(record.get("source"))
        url = _text(record.get("source_url"))
        fetched_at = _text(record.get("fetched_at"))
        reliability = _text(_round(record.get("reliability_score")))
        lines.append("- [#%d] %s: %s（%s，取得時間 %s，可靠度 %s）" % (
            number, evidence_id, source, url, fetched_at, reliability))
    return "\n".join(lines)


# --------------------------------------------------------------------------------------
# 對外入口
# --------------------------------------------------------------------------------------

def render_report(data: dict) -> str:
    """把 run／plan／claims／evidence／critique 組成固定結構的 markdown 報告。

    純函式：不寫檔案、不呼叫 LLM、不連網路。缺任何一塊資料都不會拋例外，只會顯示
    ``N/A`` 或「本次無資料」。相同輸入永遠得到逐字相同的輸出。
    """
    data = _dict(data)
    run_info = _dict(data.get("run"))
    plan_info = _dict(data.get("plan"))
    evidence_list = [item for item in _list(data.get("evidence")) if isinstance(item, dict)]

    bundle = _extract_claims_bundle(data)
    footnote_by_id, ordered_evidence = _build_footnotes(evidence_list, bundle["rejected_evidence_ids"])

    sections = [
        _render_header(run_info, plan_info),
        _render_time_scope(run_info, plan_info),
        _render_plan_summary(plan_info),
        _render_stance_overview(bundle),
    ]

    for index, claim in enumerate(bundle["claims"], start=1):
        sections.append(_render_claim(index, claim, footnote_by_id, bundle["rejected_evidence_ids"]))

    sections.append(_render_hypothesis_assessments(bundle["hypothesis_assessments"]))
    sections.append(_render_critique(data.get("critique")))
    sections.append(_render_evidence_sources(ordered_evidence))
    sections.append("## 免責聲明\n\n%s" % _DISCLAIMER)

    return "\n\n".join(sections) + "\n"
