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

from src.schemas import (CONFIDENCE_COMPONENT_KEYS, CREDIBILITY_WEIGHTS, GATE_STATUS_FAIL,
                         GATE_STATUS_PASS, GATE_STATUS_PASS_WITH_WARNINGS,
                         VERDICT_INSUFFICIENT_EVIDENCE, VERDICTS)

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


# ======================================================================================
# T5 接線層：正式提交用的 report.md
# ======================================================================================
#
# 上面的 `render_report()` 是 Track B4 的獨立函式庫（形狀由它自己定義，測試在
# tests/test_report_renderer.py）。下面這一段是**接進既有管線**的版本：段落順序同樣固定、
# 同樣是純函式，但保留 repo 既有的段落標題（`## Stance`、`## Facts`、`## Evidence Sources` …），
# 因為那些標題已經是既有測試、Demo 腳本與讀者習慣的一部分，換掉不會讓報告更可稽核。
#
# 三條規則不變：
#
# 1. 段落順序由這裡的程式決定，LLM 不得改變報告骨架；模型只能提供段落裡的文字。
# 2. 相同輸入產生逐字相同的 markdown：不讀時間、不用隨機數、不依賴不穩定的字典順序。
# 3. 缺欄位一律降級成 `N/A` 或「本次無資料」，不拋例外 —— 報告不能因為少一塊資料就消失。

_STANCE_FALLBACK_LABEL = "資料不足"

_CRITIC_VERDICT_LABELS = {
    "pass": "通過", "concerns": "有需注意之處", "fail": "結論不被證據支撐",
}

_GATE_STATUS_LABELS = {
    GATE_STATUS_PASS: "PASS（全部引用可追溯）",
    GATE_STATUS_PASS_WITH_WARNINGS: "PASS_WITH_WARNINGS（可發佈，但有需揭露的警告）",
    GATE_STATUS_FAIL: "FAIL（存在不可追溯的引用）",
}

_SEMANTIC_CATEGORY_LABELS = {
    "over_claim": "結論強度超過證據",
    "unsupported": "主張缺少對應證據",
    "ignored_counter": "反方證據未被反映",
    "stale_or_weak": "引用了過舊或過弱的證據",
    "correlation_as_causation": "把同時性寫成因果",
    "official_statement_as_outcome": "官方公告被當成商業成果",
    "onchain_transfer_as_intent": "鏈上轉帳被當成意圖",
    "confidence_too_high": "信心與證據品質不相稱",
    "other": "其他",
}

_FRESHNESS_BASIS_LABELS = {
    "event_time": "事件時間", "published_at": "發布時間", "fetched_at": "取得時間（無事件時間）",
}

_VERIFICATION_STATUS_LABELS = {
    "unverified": "未交叉驗證", "partially_confirmed": "部分佐證", "verified": "已驗證",
    "rejected": "已排除", "unavailable": "取不到（降級）", "fallback": "離線 fixture",
}

_COMPETITION_DISCLAIMER = "_This is research support, not investment advice._"


def evidence_source_line(record: dict, number: int | None = None) -> str:
    """一筆 Evidence 的來源行：顯示它的分數**怎麼來的**，不只顯示數字。

    一句 `reliability: 0.6` 是不可稽核的 —— 讀者無法分辨「本來就一般的來源」與「不錯但被上限
    壓住的來源」。原始分數、生效的上限、新鮮度依據與五項分量才讓這個數字可以被質疑。
    T5 另外補上 `取得時間`、`內容依據` 與 `狀態`，因為提交物要求 Evidence 摘要能單獨重現。
    """
    record = _dict(record)
    breakdown = _dict(record.get("score_breakdown"))
    status = record.get("verification_status") or "unverified"
    parts = [
        "類別 %s" % _text(record.get("source_type"), "unknown"),
        "狀態 %s" % _VERIFICATION_STATUS_LABELS.get(status, status),
        "取得 %s" % _text(record.get("fetched_at")),
    ]
    if breakdown:
        components = _dict(breakdown.get("components"))
        parts.append("原始 %s" % _text(_round(breakdown.get("raw_score"))))
        limiters = [str(item) for item in _list(breakdown.get("score_limiters")) if str(item).strip()]
        if limiters:
            parts.append("上限 %s（%s）" % (_text(_round(breakdown.get("hard_cap"))), "、".join(limiters)))
        basis = _text(breakdown.get("freshness_basis"), "")
        if basis and basis != _NOT_AVAILABLE:
            parts.append("新鮮度依 %s（%s 天前）" % (
                _FRESHNESS_BASIS_LABELS.get(basis, basis), _text(_round(breakdown.get("age_days"), 2))))
        parts.append("分量 " + "／".join(
            "%s=%s" % (key, _text(_round(components.get(key)))) for key in CREDIBILITY_WEIGHTS))
    locator = _dict(record.get("content_reference"))
    if locator:
        parts.append("內容依據 %s" % "、".join(sorted(str(key) for key in locator)))
    claim_ids = [str(item) for item in _list(record.get("related_claim_ids")) if str(item).strip()]
    parts.append("使用於 %s" % ("、".join(claim_ids) if claim_ids else "（無 Claim 引用）"))

    prefix = "- [#%d] " % number if number else "- "
    return (prefix + "%s: [%s](%s) (reliability: %s) ｜ " % (
        _text(record.get("evidence_id")), _text(record.get("source")),
        _text(record.get("source_url")), _text(_round(record.get("reliability_score")))
    ) + "｜".join(parts))


def render_claims_section(graph: dict, confidence_weights) -> str:
    """`## Claims`：每個 Claim 印出三層、正反證據，以及把分數壓住的上限。

    信心分數若沒有可見的理由，讀者就無法稽核它，所以 limiters 一定跟著分數一起出現。
    """
    graph = _dict(graph)
    blocks = []
    for claim in _list(graph.get("claims")):
        claim = _dict(claim)
        confidence = _dict(claim.get("confidence"))
        lines = [
            "### %s｜%s" % (_text(claim.get("claim_id")), _text(claim.get("statement"))),
            "- 判定：**%s**　信心 %s（%s／%s，類型 %s）" % (
                _text(claim.get("verdict")), _text(confidence.get("score")),
                _text(confidence.get("level")), _text(confidence.get("type")),
                _text(claim.get("claim_type"))),
        ]
        for fact in _list(claim.get("facts")):
            fact = _dict(fact)
            lines.append("- 事實：%s（%s）" % (
                _text(fact.get("statement")),
                ", ".join(str(item) for item in _list(fact.get("evidence_ids")))))
        lines.append("- 推論：%s" % _text(claim.get("inference"), _NO_DATA))
        lines.append("- 結論：%s" % _text(claim.get("conclusion"), _NO_DATA))
        lines.append("- 支持證據：" + (", ".join(
            str(item) for item in _list(claim.get("supporting_evidence_ids"))) or "無"))
        lines.append("- 反方證據：" + (", ".join(
            str(item) for item in _list(claim.get("contradicting_evidence_ids"))) or "無"))
        components = _dict(confidence.get("components"))
        lines.append("- 信心分量：" + "／".join(
            "%s=%s" % (key, _text(components.get(key))) for key in confidence_weights))
        limiters = [str(item) for item in _list(confidence.get("limiters")) if str(item).strip()]
        if limiters:
            lines.append("- 生效上限：" + "、".join(limiters))
        for label, key in (("限制", "limitations"), ("推翻條件", "invalidation_conditions"),
                           ("觀察重點", "watchpoints")):
            for entry in _list(claim.get(key)):
                lines.append("- %s：%s" % (label, entry))
        blocks.append("\n".join(lines))

    note = ("_Claim 的 verdict 與信心由 deterministic Python 計算（%s），LLM 只提供敘述；"
            "信心是 heuristic evidence score，不是市場正確機率。_"
            % _text(graph.get("scoring_version"), "unknown"))
    body = "\n\n".join(blocks) if blocks else _NO_DATA
    return "\n## Claims\n" + body + "\n\n" + note + "\n"


def _render_scope(run_info: dict, plan_info: dict, evidence_window: dict) -> str:
    """資料截止與分析區間：報告不能只說結論是什麼，還要說它是「什麼時候、看多久」得出的。"""
    window = _dict(plan_info.get("time_window"))
    lines = [
        "## 資料截止與分析區間",
        "- Run ID：%s" % _text(run_info.get("run_id")),
        "- 執行開始：%s" % _text(run_info.get("started_at")),
        "- 執行完成：%s" % _text(run_info.get("completed_at")),
        "- 資料截止（as-of）：%s" % _text(run_info.get("as_of")),
        "- 研究時間窗：%s 天（來源：%s）" % (
            _text(window.get("days")), _text(window.get("source"))),
        "- 證據取得時間範圍：%s → %s" % (
            _text(_dict(evidence_window).get("earliest_fetched_at")),
            _text(_dict(evidence_window).get("latest_fetched_at"))),
        "- 執行模式：%s（live=%s、use_llm=%s）" % (
            _text(run_info.get("mode")), _text(run_info.get("live")), _text(run_info.get("use_llm"))),
        "- 推理提供者：%s／%s" % (_text(run_info.get("provider")), _text(run_info.get("model"))),
        # T7：執行性質與結束狀態。讀者必須能一眼看出這份報告是正式執行還是測試，以及它是照計畫
        # 跑完（COMPLETED）還是有來源／階段降級（COMPLETED_DEGRADED）。
        "- 執行性質：%s／狀態：%s" % (
            _text(run_info.get("run_mode")), _text(run_info.get("status"))),
    ]
    degradations = [str(reason) for reason in _list(run_info.get("degradation_reasons"))
                    if str(reason).strip()]
    if degradations:
        lines.append("- 本次降級項目：%s" % "、".join(degradations))
    rerun_of = _text(run_info.get("rerun_of"), "")
    if rerun_of:
        lines.append("- 重跑血緣：本 run 重跑自 %s（理由：%s；授權：%s）" % (
            rerun_of, _text(run_info.get("rerun_reason")),
            _text(run_info.get("authorized_rerun"))))
    return "\n".join(lines)


def _render_key_basis(stance: dict, consistency: dict) -> str:
    """關鍵依據：把立場最主要的推力列出來，每一項都帶 evidence ID。"""
    lines = ["## 關鍵依據"]
    drivers = _list(_dict(stance).get("drivers"))
    if drivers:
        for driver in drivers:
            driver = _dict(driver)
            lines.append("- %s（%s，權重 %s）" % (
                _text(driver.get("text")), _text(driver.get("evidence_id")),
                _text(driver.get("weight"))))
    else:
        lines.append("- 本次沒有形成方向的主要推力（方向性訊號不足或多空互相抵銷）。")
    consistency = _dict(consistency)
    if consistency:
        lines.append("- 跨來源一致程度：%s（%s）" % (
            _text(consistency.get("label"), _STANCE_FALLBACK_LABEL),
            _text(consistency.get("basis"))))
    return "\n".join(lines)


def _render_consistency(consistency: dict) -> str:
    """跨來源一致程度：一個方向由幾個領域同時支持，比「有幾筆證據」更誠實。"""
    consistency = _dict(consistency)
    lines = ["## 跨來源一致程度"]
    if not consistency:
        lines.append(_NO_DATA)
        return "\n".join(lines)
    lines.append("- 判定：**%s**" % _text(consistency.get("label"), _STANCE_FALLBACK_LABEL))
    lines.append("- 依據：%s" % _text(consistency.get("basis")))
    lines.append("- 一致度：%s%%（%s 個領域有方向，共 %s 個領域有證據）" % (
        _text(consistency.get("agreement_pct")), _text(consistency.get("directional_domain_count")),
        _text(consistency.get("domain_count"))))
    for domain, side in sorted(_dict(consistency.get("domain_sides")).items()):
        lines.append("- %s：%s" % (domain, side))
    conflicting = [str(item) for item in _list(consistency.get("conflicting_domains"))]
    if conflicting:
        lines.append("- 內部互相矛盾的領域：%s" % "、".join(conflicting))
    return "\n".join(lines)


def _render_competition_critique(critique, critic_status, semantic_findings) -> str:
    """稽核段：語意稽核可能不存在（離線、逾時、無配額），此時必須明說並改用保守呈現。"""
    lines = ["## Critic Review"]
    critique = _dict(critique)
    if critique:
        verdict = _text(critique.get("verdict"), "")
        lines.append("**%s**　信心 %s → %s" % (
            _CRITIC_VERDICT_LABELS.get(verdict, verdict),
            _text(_round(critique.get("original_confidence"))),
            _text(_round(critique.get("adjusted_confidence")))))
        lines.append("")
        lines.append(_text(critique.get("summary"), _NO_DATA))
        lines.append("")
        findings = _list(critique.get("findings"))
        if findings:
            for finding in findings:
                finding = _dict(finding)
                lines.append("- [%s／%s] %s（針對：%s｜證據：%s）" % (
                    _text(finding.get("severity"), "").upper(), _text(finding.get("category")),
                    _text(finding.get("issue")), _text(finding.get("claim"), "")[:60],
                    _text(finding.get("evidence_id"))))
        else:
            lines.append("- 稽核未發現實質問題")
    else:
        lines.append("**語意稽核未執行**　狀態：%s" % _text(critic_status))
        lines.append("")
        lines.append("本次沒有可用的語意稽核結果，報告改以 structural citation gate 與 "
                     "deterministic 語意風險偵測作為唯一檢核，結論維持保守呈現。")

    lines.append("")
    lines.append("### 語意風險（deterministic 偵測 + 稽核標記）")
    findings = _list(semantic_findings)
    if not findings:
        lines.append("- 本次未偵測到已知的過度推論樣式")
    else:
        for finding in findings:
            finding = _dict(finding)
            category = _text(finding.get("category"), "other")
            lines.append("- [%s] %s%s（來源：%s）" % (
                category, _SEMANTIC_CATEGORY_LABELS.get(category, category),
                "：%s" % _text(finding.get("issue")) if finding.get("issue") else "",
                _text(finding.get("detected_by"))))
    return "\n".join(lines)


def _render_citation_gate(gate: dict) -> str:
    """Citation Gate 的結果本身就是提交物的一部分：讀者要能看到哪一條規則被檢查過。"""
    gate = _dict(gate)
    lines = ["## Citation Gate"]
    if not gate:
        lines.append(_NO_DATA)
        return "\n".join(lines)
    status = _text(gate.get("status"), "")
    lines.append("- 結果：**%s**" % _GATE_STATUS_LABELS.get(status, status))
    lines.append("- Gate 版本：%s" % _text(gate.get("gate_version")))
    lines.append("- 檢查範圍：%s 個 Claim、%s 筆 Evidence（其中 %s 筆被引用）" % (
        _text(gate.get("claim_count")), _text(gate.get("evidence_count")),
        _text(gate.get("cited_evidence_count"))))
    for name, outcome in sorted(_dict(gate.get("checks")).items()):
        lines.append("- %s：%s" % (name, outcome))
    for message in _list(gate.get("errors")):
        lines.append("- ❌ %s" % message)
    for message in _list(gate.get("warnings")):
        lines.append("- ⚠ %s" % message)
    return "\n".join(lines)


def _render_invalidation(graph: dict) -> str:
    """可能推翻結論的條件：彙整所有 Claim 的推翻條件，讓讀者知道要盯什麼才會改變結論。"""
    lines = ["## 可能推翻結論的條件"]
    entries = []
    for claim in _list(_dict(graph).get("claims")):
        claim = _dict(claim)
        claim_id = _text(claim.get("claim_id"), "")
        for condition in _list(claim.get("invalidation_conditions")):
            text = str(condition).strip()
            if text:
                entries.append("%s（%s）" % (text, claim_id) if claim_id else text)
    lines.append(_bullets(entries, "本次沒有列出推翻條件（資料不足或未產生方向性結論）。"))
    return "\n".join(lines)


def render_competition_report(data: dict) -> str:
    """把一次執行的全部結果組成固定結構的 `report.md`。

    純函式：不寫檔案、不呼叫 LLM、不連網路、不讀系統時間。段落順序由本函式決定，
    模型只能影響段落內的文字。缺任何一塊資料都只會降級顯示，不會讓報告產不出來。
    """
    data = _dict(data)
    run_info = _dict(data.get("run"))
    plan_info = _dict(data.get("plan"))
    stance = _dict(data.get("stance"))
    reasoning = _dict(data.get("reasoning"))
    graph = _dict(data.get("claim_graph"))
    evidence = [item for item in _list(data.get("evidence")) if isinstance(item, dict)]
    gate = _dict(data.get("citation_gate"))
    confidence_weights = data.get("confidence_weights") or CONFIDENCE_COMPONENT_KEYS

    coins = [str(coin) for coin in _list(run_info.get("coins")) if str(coin).strip()]
    coin_label = " vs ".join(coins) if coins else "未指定標的"
    question = _text(run_info.get("question") or plan_info.get("primary_question"))

    sections = [
        "# %s Market Research" % coin_label,
        "## Question\n%s" % question,
        "\n".join([
            "## 分析標的與題目",
            "- 分析標的：%s" % coin_label,
            "- 研究問題：%s" % question,
            "- Task modes：%s" % ("、".join(
                str(mode) for mode in _list(plan_info.get("task_modes"))) or _NO_DATA),
        ]),
        _render_scope(run_info, plan_info, _dict(data.get("evidence_window"))),
        "\n".join([
            "## Stance",
            "**%s（%s）**　信心 %s" % (
                _text(stance.get("label"), _STANCE_FALLBACK_LABEL),
                _text(stance.get("label_en"), "Unknown"),
                _text(_round(reasoning.get("confidence")))),
            "",
            "- 依據：%s" % _text(stance.get("basis")),
            "- 訊號權重：多方 %s / 空方 %s（共 %s 項訊號）" % (
                _text(stance.get("bull_weight")), _text(stance.get("bear_weight")),
                _text(stance.get("signal_count"))),
        ] + ["- 主要推力：%s（%s）" % (_dict(driver).get("text"), _dict(driver).get("evidence_id"))
             for driver in _list(stance.get("drivers"))]),
        "## Market Judgment\n%s" % _text(reasoning.get("market_judgment"), _NO_DATA),
    ]

    history = _dict(data.get("history"))
    if history:
        windows = _dict(_dict(history.get("content")).get("windows"))
        rows = ["## Long-horizon Context"]
        for label, window in windows.items():
            window = _dict(window)
            rows.append("- %s（%s → %s）: 報酬 %s%%／年化波動 %s%%／最大回撤 %s%%"
                        "／價格位於區間 %s%% 分位" % (
                            label, _text(window.get("date_start")), _text(window.get("date_end")),
                            _text(window.get("return_pct")),
                            _text(window.get("volatility_annualised_pct")),
                            _text(window.get("max_drawdown_pct")),
                            _text(window.get("percentile_in_range"))))
        rows.append("- 資料來源：%s（%s 個交易日）" % (
            _text(history.get("evidence_id")),
            _text(_dict(history.get("content")).get("rows"))))
        sections.append("\n".join(rows))

    sections.extend([
        _render_key_basis(stance, data.get("consistency")),
        "## Facts\n%s" % _bullets(reasoning.get("facts")),
        "## Inferences\n%s" % _bullets(reasoning.get("inferences")),
        "## Conclusion\n%s" % _text(reasoning.get("conclusion"), _NO_DATA),
        render_claims_section(graph, confidence_weights).strip("\n"),
        _render_consistency(data.get("consistency")),
        _render_competition_critique(data.get("critique"), data.get("critic_status"),
                                     gate.get("semantic_findings")),
        _render_citation_gate(gate),
        "## Confidence\n%s" % _text(_round(reasoning.get("confidence"))),
        "## Indicators\n%s" % _render_indicators(data.get("indicators")),
        "## Counter Evidence\n%s" % _bullets(reasoning.get("counter_evidence")),
        "## Evidence Sources\n%s" % _render_competition_sources(evidence),
        "## Next Observations\n%s" % _bullets(reasoning.get("observation_points")),
        "## Risks and Limitations\n%s" % _bullets(data.get("risk_factors")),
        _render_invalidation(graph),
        _COMPETITION_DISCLAIMER,
    ])
    return "\n\n".join(section for section in sections if section) + "\n"


def _render_indicators(indicators) -> str:
    """指標段。長數列（prices／volumes／dates）由呼叫端先移除，這裡只忠實列出剩下的。"""
    indicators = _dict(indicators)
    if not indicators:
        return _NO_DATA
    lines = []
    for key, value in indicators.items():
        if isinstance(value, dict):
            lines.append("- %s: %s" % (key, ", ".join(
                "%s=%s" % (sub_key, sub_value) for sub_key, sub_value in value.items())))
        else:
            lines.append("- %s: %s" % (key, value))
    return "\n".join(lines) if lines else _NO_DATA


def _render_competition_sources(evidence: list) -> str:
    """Evidence 摘要。rejected 的證據不列入引用編號，但仍在段末列出，供稽核者看見它被排除。"""
    active, rejected = [], []
    for record in evidence:
        (rejected if record.get("verification_status") == _REJECTED_STATUS else active).append(record)
    lines = [evidence_source_line(record, number)
             for number, record in enumerate(active, start=1)]
    if not lines:
        lines = [_NO_DATA]
    if rejected:
        lines.append("")
        lines.append("### 已排除的證據（rejected，不得作為結論依據）")
        lines.extend(evidence_source_line(record) for record in rejected)
    return "\n".join(lines)
