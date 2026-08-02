"""E3 — 雲端三報告的純渲染層（Final Report／Evidence List／Execution Log）。

為什麼需要這一個模組：Function URL 的結果頁原本只是把 `report.md` 與 execution log 塞進兩個
`<pre>`。那份 markdown 讀得懂，但 `evidence.json` 在畫面上完全不存在，於是「每個結論都能點回
原始來源」這件事在雲端只能靠下載 JSON 再人工比對 Evidence ID —— 而那正是主辦方抽查引用真實性
時會做的事。本機 `src/app.py` 早就有完整介面，雲端沒有，這個模組補的是這個落差。

三條設計邊界：

1. **純函式。** 不讀檔、不連網、不寫檔、不呼叫 LLM。輸入是已經載入的六份提交物，輸出是一段
   HTML 字串。因此可以在不啟動 Lambda、不跑分析管線的情況下逐項測試。
2. **UI 是同一份 artifact 的可讀投影，不是第二份真相來源。** 這裡不重新計算任何分數、不改寫
   verdict、不補值。畫面上出現的每個數字都必須能在 JSON 裡找到同一個值。
3. **所有外部內容一律 escape。** Evidence 的 title／author／URL 來自新聞 RSS 與社群貼文，是
   不可信輸入；而公開端點的 AuthType 是 NONE。未 escape 的插值等於把外部來源的字串當成程式碼。

只用標準函式庫：`html.escape` 與字串組裝，沒有樣板引擎、沒有 markdown 套件、沒有 CDN 資源。
"""

from __future__ import annotations

import json
from html import escape

# 來源沒有提供某個欄位時的統一顯示。刻意寫明「來源未提供」而不是留空或填 0：
# 空白會被讀成「系統漏了」，0 會被讀成「量測到 0」，兩者都是錯的。
NOT_PROVIDED = "N/A（來源未提供）"

# 只有這兩種 scheme 可以成為可點連結。`javascript:`、`data:`、`vbscript:` 一律降級成純文字。
_SAFE_URL_SCHEMES = ("https://", "http://")

# 未知或已 rejected 的 Evidence ID 不得渲染成看起來可用的站內連結：那會讓一個不存在的引用
# 看起來可追溯。改成純文字加上明確標記，並由 Citation Gate 負責讓整份輸出 FAIL。
UNKNOWN_EVIDENCE_MARK = "（未知引用）"

_RELEVANCE_LABELS_ZH = {
    "direct": "直接相關",
    "indirect": "間接相關",
    "context": "背景脈絡",
    "irrelevant": "不相關",
    "unclear": "無法判斷",
}
_RELATIONSHIP_ZH = {
    "support": "支持題目方向",
    "contradict": "反對題目方向",
    "context": "僅提供背景",
    "irrelevant": "不相關",
    "unclear": "無法判斷",
}
_IMPACT_ZH = {
    "bullish": "偏多", "bearish": "偏空", "neutral": "中性",
    "mixed": "多空混合", "not_applicable": "不適用", "unclear": "無法判斷",
}
_HORIZON_ZH = {
    "immediate": "即時", "short_term": "短期", "medium_term": "中期",
    "long_term": "長期", "unclear": "無法判斷",
}
_VERDICT_ZH = {
    "supported": "證據支持",
    "partially_supported": "部分支持",
    "contradicted": "證據反對",
    "insufficient_evidence": "證據不足",
}
_CONFIDENCE_COMPONENT_ZH = {
    "weighted_evidence_quality": "證據品質（加權）",
    "domain_coverage": "領域覆蓋度",
    "source_diversity": "來源多元性",
    "signal_consistency": "訊號一致性",
    "counter_evidence_coverage": "反方證據覆蓋度",
}
_SCORE_COMPONENT_ZH = {
    "source_quality": "來源品質",
    "traceability": "可追溯性",
    "freshness": "新鮮度",
    "method_transparency": "方法透明度",
    "independence": "獨立性",
}

CSS = """
:root{--ink:#1b1f24;--muted:#5b6570;--line:#dfe3e8;--bg:#f6f7f9;--card:#fff;
--pos:#0f7b52;--neg:#a4343a;--warn:#8a5a00}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:16px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans TC","PingFang TC",sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:24px 18px 64px}
h1{font-size:1.55rem;margin:0 0 6px}
h2{font-size:1.2rem;margin:0 0 12px}
h3{font-size:1.02rem;margin:0 0 8px}
a{color:#12507f}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:18px;margin:0 0 18px}
.meta{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0 0;padding:0;list-style:none}
.meta li{background:#eef1f4;border-radius:999px;padding:3px 10px;font-size:.82rem;color:var(--muted)}
nav.tabs{display:flex;flex-wrap:wrap;gap:10px;margin:0 0 18px}
nav.tabs a{background:var(--card);border:1px solid var(--line);border-radius:8px;
padding:9px 14px;text-decoration:none;font-weight:600}
nav.tabs a:focus{outline:3px solid #12507f;outline-offset:2px}
table{width:100%;border-collapse:collapse;font-size:.9rem}
.tablewrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
th,td{border-bottom:1px solid var(--line);padding:8px 10px;text-align:left;vertical-align:top}
th{background:#f0f2f5;font-weight:600;white-space:nowrap}
td.wrap-any{word-break:break-all}
.muted{color:var(--muted)}
.small{font-size:.85rem}
pre{background:#f0f2f5;border:1px solid var(--line);border-radius:8px;padding:12px;
overflow-x:auto;font-size:.82rem;white-space:pre-wrap;word-break:break-word}
details{border:1px solid var(--line);border-radius:8px;padding:10px 12px;margin:8px 0;background:#fbfcfd}
summary{cursor:pointer;font-weight:600}
.tag{display:inline-block;border-radius:4px;padding:1px 7px;font-size:.78rem;
border:1px solid var(--line);background:#eef1f4;color:var(--muted)}
.tag.pos{color:var(--pos);border-color:#b7dfcc;background:#eef8f3}
.tag.neg{color:var(--neg);border-color:#e8c4c6;background:#fdf1f1}
.tag.warn{color:var(--warn);border-color:#e6d3a8;background:#fdf7e8}
ul.plain{margin:6px 0 0;padding-left:20px}
.claim{border-left:4px solid #c9d2db;padding-left:14px;margin:0 0 20px}
.kv{margin:6px 0 0}
.kv b{font-weight:600}
.side{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media (max-width:720px){.side{grid-template-columns:1fr}.wrap{padding:16px 12px 48px}}
.notice{border-left:4px solid var(--warn);background:#fdf7e8;padding:10px 12px;border-radius:6px}
"""


# --------------------------------------------------------------------------------------
# 基本安全工具
# --------------------------------------------------------------------------------------


def _text(value) -> str:
    """任意值 → 已 escape 的純文字。None／空字串一律走 `NOT_PROVIDED`。"""
    if value is None:
        return escape(NOT_PROVIDED)
    if isinstance(value, bool):
        return escape("是" if value else "否")
    raw = str(value).strip()
    return escape(raw) if raw else escape(NOT_PROVIDED)


def _plain(value) -> str:
    """已 escape 的純文字，但空值回空字串（用於不需要 N/A 佔位的位置）。"""
    return escape(str(value).strip()) if value is not None and str(value).strip() else ""


def safe_url(value) -> str:
    """只放行 http/https；其餘（含 `javascript:`、`data:`）回空字串。"""
    raw = str(value or "").strip()
    if not raw:
        return ""
    lowered = raw.lower()
    if any(lowered.startswith(scheme) for scheme in _SAFE_URL_SCHEMES):
        # 換行與引號會破壞屬性邊界；URL 本身不該含這些字元，出現就視為不可信。
        if any(ch in raw for ch in ('"', "'", "<", ">", "\n", "\r", " ")):
            return ""
        return raw
    return ""


def link(value, label=None) -> str:
    """外部連結。scheme 不安全時降級成純文字，絕不產生可點的 `javascript:`。"""
    url = safe_url(value)
    shown = label if label is not None else value
    if not url:
        return _text(shown)
    return (f"<a href='{escape(url)}' target='_blank' rel='noopener noreferrer'>"
            f"{_text(shown)}</a>")


def _num(value, digits: int = 4) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return escape(f"{round(float(value), digits):g}")
    return _text(value)


def _list_items(values) -> str:
    items = [v for v in (values or []) if str(v).strip()]
    if not items:
        return "<p class='muted small'>無</p>"
    return "<ul class='plain'>" + "".join(f"<li>{_text(v)}</li>" for v in items) + "</ul>"


def _evidence_anchor_id(evidence_id) -> str:
    """站內錨點 ID。只保留英數與 `-_`，避免 ID 帶進屬性分隔字元。"""
    raw = str(evidence_id or "")
    return "evidence-" + "".join(ch if (ch.isalnum() or ch in "-_") else "-" for ch in raw)


def _item_anchor_id(item_id) -> str:
    raw = str(item_id or "")
    return "item-" + "".join(ch if (ch.isalnum() or ch in "-_") else "-" for ch in raw)


def evidence_ref(evidence_id, known_ids) -> str:
    """Claim 引用的 Evidence ID → 站內連結。

    ID 不在本次 run 的有效 Evidence 裡時**不產生連結**，改成純文字加標記。理由：一個可點但
    指向不存在錨點的連結，看起來與可追溯的引用完全一樣，等於用 UI 掩蓋 Citation Gate 該抓到
    的問題。
    """
    raw = str(evidence_id or "").strip()
    if not raw:
        return ""
    if raw in known_ids:
        return f"<a href='#{escape(_evidence_anchor_id(raw))}'>{escape(raw)}</a>"
    return f"<span class='tag neg'>{escape(raw)}{escape(UNKNOWN_EVIDENCE_MARK)}</span>"


def _evidence_refs(ids, known_ids) -> str:
    refs = [evidence_ref(i, known_ids) for i in (ids or []) if str(i).strip()]
    return "、".join(refs) if refs else "<span class='muted'>無</span>"


def _claim_refs(claim_ids) -> str:
    """Evidence 反向指回 Claim 的站內連結（Claim 卡片的 id 是 `claim-<id>`）。"""
    refs = [f"<a href='#claim-{escape(str(c))}'>{escape(str(c))}</a>"
            for c in (claim_ids or []) if str(c).strip()]
    return "、".join(refs) if refs else "<span class='muted'>無</span>"


def _item_ref(item_id, known_item_ids) -> str:
    raw = str(item_id or "").strip()
    if not raw:
        return ""
    if raw in known_item_ids:
        return f"<a href='#{escape(_item_anchor_id(raw))}'>{escape(raw)}</a>"
    return f"<span class='tag neg'>{escape(raw)}{escape(UNKNOWN_EVIDENCE_MARK)}</span>"


# --------------------------------------------------------------------------------------
# 區塊：頁首
# --------------------------------------------------------------------------------------


def _gate_tag(status) -> str:
    raw = str(status or "").upper()
    css = "pos" if raw == "PASS" else "warn" if raw in {"WARN", "PARTIAL"} else "neg" if raw else ""
    return f"<span class='tag {css}'>Citation Gate：{_text(raw or None)}</span>"


def _header(run_id, mode, status, log, manifest, headline) -> str:
    lifecycle = log.get("run_lifecycle") or {}
    degradations = [d for d in (log.get("degradation_reasons") or []) if str(d).strip()]
    degrade_html = (
        "<div class='notice small'><b>本次降級項目：</b>"
        + "、".join(_text(d) for d in degradations) + "</div>"
        if degradations else
        "<p class='small muted'>本次沒有降級項目。</p>"
    )
    providers = log.get("stage_providers") or {}
    provider_bits = "、".join(
        f"{_text(name)}：{_text(info.get('provider'))}"
        + (f"／{_text(info.get('status'))}" if info.get("status") else "")
        for name, info in providers.items()
    ) or "<span class='muted'>無紀錄</span>"
    return f"""<section class='card'>
    <h1>{_text(headline)}</h1>
    <p class='small muted'>公開雲端展示僅提供 test mode；正式（formal）執行只透過受控管道進行。</p>
    <ul class='meta'>
      <li>Run ID：{_text(run_id)}</li>
      <li>執行性質：{_text(mode)}</li>
      <li>狀態：{_text(status)}</li>
      <li>開始（UTC）：{_text(log.get('started_at') or lifecycle.get('started_at'))}</li>
      <li>完成（UTC）：{_text(log.get('completed_at') or lifecycle.get('completed_at'))}</li>
      <li>資料截止（as-of）：{_text(manifest.get('as_of') or lifecycle.get('started_at'))}</li>
      <li>{_gate_tag((log.get('citation_gate') or {}).get('status'))}</li>
    </ul>
    <p class='kv small'><b>各階段推理提供者：</b>{provider_bits}</p>
    {degrade_html}
    </section>"""


def _nav() -> str:
    return """<nav class='tabs' aria-label='報告區段'>
    <a href='#final-report'>① 分析報告（Final Report）</a>
    <a href='#evidence-list'>② 證據清單（Evidence List）</a>
    <a href='#execution-log'>③ 執行紀錄（Execution Log）</a>
    </nav>"""


def _downloads(run_id, artifact_filenames) -> str:
    run = escape(str(run_id or ""))
    links = "　".join(
        f"<a href='/artifact?path={escape(name)}&run={run}&download=1'>{escape(name)}</a>"
        for name in artifact_filenames
    )
    return f"""<section class='card'>
    <h2>下載提交物</h2>
    <p><a href='/download?scope=required&run={run}'>下載三份提交物（ZIP）</a>
    　｜　<a href='/download?scope=all&run={run}'>下載全部產出（ZIP）</a></p>
    <p class='small'>{links}</p>
    <p class='small muted'>提交物寫在處理該次執行的容器暫存目錄。冷啟動或被路由到其他容器時
    連結會回 404，這是 Lambda 的固有限制，不是執行失敗。</p>
    </section>"""


# --------------------------------------------------------------------------------------
# 區塊一：Final Report
# --------------------------------------------------------------------------------------


def _stance_block(result) -> str:
    stance = (result or {}).get("stance") or {}
    if not stance:
        return ""
    label = stance.get("label") or stance.get("stance")
    css = {"bullish": "pos", "bearish": "neg"}.get(str(stance.get("stance") or ""), "")
    drivers = stance.get("drivers") or []
    driver_rows = "".join(
        f"<tr><td>{_text(d.get('text'))}</td><td>{_text(d.get('evidence_id'))}</td>"
        f"<td>{_num(d.get('weight'), 3)}</td>"
        f"<td>{_num(d.get('base_weight'), 3) if d.get('base_weight') is not None else '<span class=muted>—</span>'}</td></tr>"
        for d in drivers
    ) or "<tr><td colspan='4' class='muted'>可信度過低的訊號已被排除，本次沒有可列示的主要推力。</td></tr>"
    return f"""<div class='card'>
    <h3>立場判定（規則計算，非模型生成）</h3>
    <p><span class='tag {css}'>{_text(label)}</span>
    　多方權重 {_num(stance.get('bull_weight'), 3)}／空方權重 {_num(stance.get('bear_weight'), 3)}
    　共 {_text(stance.get('signal_count'))} 項訊號</p>
    <p class='small'><b>依據：</b>{_text(stance.get('basis'))}</p>
    <div class='tablewrap'><table><thead><tr>
      <th>主要推力</th><th>Evidence</th><th>有效權重</th><th>原始權重</th>
    </tr></thead><tbody>{driver_rows}</tbody></table></div>
    <p class='small muted'>有效權重 = 原始權重 × min(1, 來源可信度 ÷ 0.70)。可信度低的證據無法
    用字面權重推動方向判讀；兩欄並列是為了讓下調過程可稽核。此處為訊號層的立場權重，與證據層的
    「問題相關性有效權重」是不同的量。</p>
    </div>"""


def _confidence_block(confidence) -> str:
    conf = confidence or {}
    components = conf.get("components") or {}
    limiters = conf.get("limiters") or []
    comp = "／".join(
        f"{_text(_CONFIDENCE_COMPONENT_ZH.get(name, name))}={_num(value, 4)}"
        for name, value in components.items()
    ) or "<span class='muted'>無</span>"
    limiter_html = ("、".join(f"<span class='tag warn'>{_text(l)}</span>" for l in limiters)
                    if limiters else "<span class='muted'>無</span>")
    return f"""<details><summary>信心分量與生效上限（由 deterministic Python 計算）</summary>
    <p class='small kv'><b>分量：</b>{comp}</p>
    <p class='small kv'><b>生效的上限（limiters）：</b>{limiter_html}</p>
    <p class='small muted'>信心是 heuristic evidence score，代表證據品質與一致程度，
    不等同於「有相同百分比機率預測正確」。LLM 不得寫入或提高這個數字。</p>
    </details>"""


def _claim_card(claim, known_ids) -> str:
    conf = claim.get("confidence") or {}
    verdict = str(claim.get("verdict") or "")
    css = {"supported": "pos", "contradicted": "neg"}.get(verdict, "warn")
    facts = claim.get("facts") or []
    fact_rows = "".join(
        f"<li>{_text(f.get('statement'))}"
        f"　<span class='small'>{_evidence_refs(f.get('evidence_ids'), known_ids)}</span></li>"
        for f in facts if isinstance(f, dict)
    ) or "<li class='muted'>無</li>"
    return f"""<article class='claim' id='claim-{escape(str(claim.get('claim_id') or ''))}'>
    <h3>{_text(claim.get('claim_id'))}｜{_text(claim.get('statement'))}</h3>
    <p><span class='tag {css}'>判定：{_text(_VERDICT_ZH.get(verdict, verdict or None))}</span>
    　信心 {_num(conf.get('score'), 3)}
    　<span class='tag'>{_text(conf.get('level'))}</span>
    　<span class='tag'>類型 {_text(claim.get('claim_type'))}</span></p>
    <p class='kv'><b>事實（Fact）：</b></p><ul class='plain'>{fact_rows}</ul>
    <p class='kv'><b>推論（Inference）：</b>{_text(claim.get('inference'))}</p>
    <p class='kv'><b>結論（Conclusion）：</b>{_text(claim.get('conclusion'))}</p>
    <div class='side'>
      <div><p class='kv'><b>支持證據</b></p>
        <p class='small'>{_evidence_refs(claim.get('supporting_evidence_ids'), known_ids)}</p>
        <p class='kv'><b>限制</b></p>{_list_items(claim.get('limitations'))}</div>
      <div><p class='kv'><b>反方證據</b></p>
        <p class='small'>{_evidence_refs(claim.get('contradicting_evidence_ids'), known_ids)}</p>
        <p class='kv'><b>推翻條件</b></p>{_list_items(claim.get('invalidation_conditions'))}</div>
    </div>
    <p class='kv'><b>後續觀察重點</b></p>{_list_items(claim.get('watchpoints'))}
    {_confidence_block(conf)}
    </article>"""


def _final_report(result, report, claims, known_ids) -> str:
    payload = claims if isinstance(claims, dict) else {}
    claim_list = payload.get("claims") or (claims if isinstance(claims, list) else [])
    cards = "".join(_claim_card(c, known_ids) for c in claim_list if isinstance(c, dict))
    if not cards:
        cards = ("<p class='muted'>本次沒有可展示的 Claim；仍可從證據清單與 report.md 追溯。</p>")
    question = (result or {}).get("question") or (result or {}).get("research_question")
    fallback = payload.get("fallback_reason")
    fallback_html = (f"<p class='small notice'>Claim 由 deterministic fallback 產生：{_text(fallback)}</p>"
                     if fallback else "")
    return f"""<section class='card' id='final-report'>
    <h2>① 分析報告（Final Report）</h2>
    <p class='kv'><b>研究問題：</b>{_text(question)}</p>
    <p class='small muted'>Claim 來源：{_text(payload.get('claim_source'))}
    ／計分版本 {_text(payload.get('scoring_version'))}</p>
    {fallback_html}
    {_stance_block(result)}
    {cards}
    <details><summary>查看 report.md 原文</summary><pre>{escape(str(report or ''))}</pre></details>
    <p class='small muted'>本報告僅供研究與展示用途，不構成投資建議。</p>
    </section>"""


# --------------------------------------------------------------------------------------
# 區塊二：Evidence List
# --------------------------------------------------------------------------------------


def _assessment_block(assessment) -> str:
    data = assessment or {}
    if not data:
        return "<p class='small muted'>本筆沒有問題導向的語意評估紀錄。</p>"
    label = str(data.get("relevance_label") or "")
    relationship = str(data.get("relationship_to_question") or "")
    impact = str(data.get("impact_direction") or "")
    horizon = str(data.get("impact_horizon") or "")
    excluded = data.get("excluded_reason")
    return f"""<div class='tablewrap'><table><tbody>
    <tr><th>問題相關性</th><td>{_text(_RELEVANCE_LABELS_ZH.get(label, label or None))}
      （relevance_score {_num(data.get('relevance_score'), 3)}）</td></tr>
    <tr><th>相對題目的立場</th><td>{_text(_RELATIONSHIP_ZH.get(relationship, relationship or None))}</td></tr>
    <tr><th>影響方向</th><td>{_text(_IMPACT_ZH.get(impact, impact or None))}</td></tr>
    <tr><th>影響時間範圍</th><td>{_text(_HORIZON_ZH.get(horizon, horizon or None))}</td></tr>
    <tr><th>有效權重</th><td>{_num(data.get('effective_weight'), 4)}
      <span class='small muted'>（可信度 × 問題相關性 × 獨立性）</span></td></tr>
    <tr><th>判斷理由</th><td>{_text(data.get('rationale'))}</td></tr>
    <tr><th>評估來源</th><td>{_text(data.get('assessment_source'))}
      <span class='small muted'>（標籤可由模型提出，分數一律由 Python 固定映射）</span></td></tr>
    {f"<tr><th>排除原因</th><td>{_text(excluded)}</td></tr>" if str(excluded or '').strip() else ''}
    </tbody></table></div>"""


def _score_block(breakdown) -> str:
    data = breakdown or {}
    components = data.get("components") or {}
    weights = data.get("weights") or {}
    rows = "".join(
        f"<tr><td>{_text(_SCORE_COMPONENT_ZH.get(name, name))}</td>"
        f"<td>{_num(value, 4)}</td><td>{_num(weights.get(name), 3)}</td></tr>"
        for name, value in components.items()
    ) or "<tr><td colspan='3' class='muted'>無分量紀錄</td></tr>"
    limiters = data.get("score_limiters") or []
    limiter_html = ("、".join(f"<span class='tag warn'>{_text(l)}</span>" for l in limiters)
                    if limiters else "<span class='muted'>無</span>")
    return f"""<div class='tablewrap'><table><thead><tr><th>分量</th><th>分數</th><th>權重</th></tr></thead>
    <tbody>{rows}</tbody></table></div>
    <p class='small kv'><b>原始加權分：</b>{_num(data.get('raw_score'), 4)}
    　<b>最終分：</b>{_num(data.get('final_score'), 4)}
    　<b>生效上限：</b>{_num(data.get('hard_cap'), 4) if data.get('hard_cap') is not None else '無'}</p>
    <p class='small kv'><b>生效的上限（limiters）：</b>{limiter_html}</p>
    <p class='small kv'><b>新鮮度基準：</b>{_text(data.get('freshness_basis'))}
    　<b>資料年齡（天）：</b>{_num(data.get('age_days'), 3)}
    　<b>來源鏈規模：</b>{_text(data.get('lineage_size'))}</p>
    {("<p class='small kv'><b>來源已知限制：</b></p>" + _list_items(data.get('known_limitations')))
     if data.get('known_limitations') else ''}"""


def _source_items_block(items) -> str:
    rows = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        item_id = item.get("source_item_id")
        rows.append(
            f"<tr id='{escape(_item_anchor_id(item_id))}'>"
            f"<td class='wrap-any small'>{_text(item_id)}</td>"
            f"<td>{_text(item.get('title') or item.get('text'))}</td>"
            f"<td class='wrap-any small'>{link(item.get('url'))}</td>"
            f"<td class='small'>{_text(item.get('publisher') or item.get('platform'))}</td>"
            f"<td class='small'>{_text(item.get('author') or item.get('author_handle'))}</td>"
            f"<td class='small'>{_text(item.get('published_at') or item.get('created_at') or item.get('indexed_at'))}</td>"
            f"</tr>")
    if not rows:
        return ("<p class='small muted'>本筆沒有子項 locator（單一觀測值的來源，或該來源未提供逐則資料）。</p>")
    return ("<div class='tablewrap'><table><thead><tr><th>子項 ID</th><th>標題／內容</th>"
            "<th>原始連結</th><th>出版商／平台</th><th>作者</th><th>來源發布時間</th>"
            "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>")


def _evidence_card(item, known_ids, known_item_ids) -> str:
    evidence_id = item.get("evidence_id")
    assessment = item.get("semantic_assessment") or {}
    item_ids = assessment.get("source_item_ids") or []
    cited_items = "、".join(_item_ref(i, known_item_ids) for i in item_ids if str(i).strip())
    return f"""<details id='{escape(_evidence_anchor_id(evidence_id))}'>
    <summary>{_text(evidence_id)}　·　{_text(item.get('source'))}
    　<span class='tag'>可信度 {_num(item.get('reliability_score'), 4)}</span></summary>
    <div class='tablewrap'><table><tbody>
      <tr><th>資料類型</th><td>{_text(item.get('data_type'))}
        ／來源類別 {_text(item.get('source_type'))}</td></tr>
      <tr><th>來源位址</th><td class='wrap-any'>{link(item.get('source_url'))}</td></tr>
      <tr><th>系統擷取時間（fetched_at）</th><td>{_text(item.get('fetched_at'))}</td></tr>
      <tr><th>來源發布時間（published_at）</th><td>{_text(item.get('published_at'))}</td></tr>
      <tr><th>事件時間（event_time）</th><td>{_text(item.get('event_time'))}</td></tr>
      <tr><th>驗證狀態</th><td>{_text(item.get('verification_status'))}</td></tr>
      <tr><th>來源鏈 ID</th><td class='wrap-any small'>{_text(item.get('source_lineage_id'))}</td></tr>
      <tr><th>對應主張</th><td>{_claim_refs(item.get('related_claim_ids'))}</td></tr>
      <tr><th>內容依據（content_reference）</th>
        <td class='wrap-any small'><pre>{escape(json.dumps(item.get('content_reference'), ensure_ascii=False)[:1200])}</pre></td></tr>
    </tbody></table></div>
    <h4 class='small'>問題導向語意評估（E2）</h4>
    {_assessment_block(assessment)}
    {f"<p class='small kv'><b>本筆實際引用的子項：</b>{cited_items}</p>" if cited_items else ''}
    <h4 class='small'>可信度分量（E3／T3 deterministic 計分）</h4>
    {_score_block(item.get('score_breakdown'))}
    <h4 class='small'>子項來源（source items）</h4>
    {_source_items_block(item.get('source_items'))}
    </details>"""


def _evidence_list(evidence, known_ids, known_item_ids) -> str:
    rows = "".join(
        f"<tr><td><a href='#{escape(_evidence_anchor_id(e.get('evidence_id')))}'>"
        f"{_text(e.get('evidence_id'))}</a></td>"
        f"<td>{_text(e.get('source'))}</td>"
        f"<td class='small'>{_text(e.get('source_type'))}</td>"
        f"<td class='small'>{_text(e.get('fetched_at'))}</td>"
        f"<td class='small'>{_text(e.get('published_at'))}</td>"
        f"<td>{_num(e.get('reliability_score'), 4)}</td>"
        f"<td>{_num((e.get('semantic_assessment') or {}).get('relevance_score'), 3)}</td>"
        f"<td>{_num((e.get('semantic_assessment') or {}).get('effective_weight'), 4)}</td></tr>"
        for e in evidence if isinstance(e, dict)
    ) or "<tr><td colspan='8' class='muted'>本次沒有有效證據。</td></tr>"
    cards = "".join(_evidence_card(e, known_ids, known_item_ids)
                    for e in evidence if isinstance(e, dict))
    return f"""<section class='card' id='evidence-list'>
    <h2>② 證據清單（Evidence List）　<span class='small muted'>{len(evidence)} 筆</span></h2>
    <div class='tablewrap'><table><thead><tr>
      <th>Evidence ID</th><th>資料來源</th><th>來源類別</th>
      <th>系統擷取時間</th><th>來源發布時間</th><th>可信度</th>
      <th>問題相關性</th><th>有效權重</th>
    </tr></thead><tbody>{rows}</tbody></table></div>
    <p class='small muted'>「系統擷取時間」是本系統抓到資料的時間，「來源發布時間」是來源自己宣稱
    的發布時間。兩者不互相代填；來源沒有提供時顯示「{escape(NOT_PROVIDED)}」。</p>
    {cards}
    </section>"""


# --------------------------------------------------------------------------------------
# 區塊三：Execution Log
# --------------------------------------------------------------------------------------


def _step_rows(steps) -> str:
    rows = []
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        status = str(step.get("status") or "")
        css = "pos" if status in {"ok", "success", "completed"} else \
              "warn" if any(k in status for k in ("fallback", "skip", "degrad", "partial")) else \
              "neg" if any(k in status for k in ("fail", "error")) else ""
        evidence_ids = step.get("evidence_ids") or step.get("produced_evidence_ids") or []
        rows.append(
            f"<tr><td>{_text(step.get('name'))}</td>"
            f"<td><span class='tag {css}'>{_text(status or None)}</span></td>"
            f"<td class='small'>{_text(step.get('tool') or step.get('provider'))}</td>"
            f"<td class='small'>{_text(step.get('started_at'))}</td>"
            f"<td class='small'>{_text(step.get('completed_at'))}</td>"
            f"<td>{_num(step.get('duration_ms'), 2)}</td>"
            f"<td class='small wrap-any'>{'、'.join(_text(i) for i in evidence_ids) if evidence_ids else '<span class=muted>—</span>'}</td>"
            f"<td class='small'>{_text(step.get('fallback_reason') or step.get('detail')) if (step.get('fallback_reason') or step.get('detail')) else '<span class=muted>—</span>'}</td>"
            f"</tr>")
    return "".join(rows) or "<tr><td colspan='8' class='muted'>沒有步驟紀錄。</td></tr>"


def _collection_rows(log) -> str:
    rows = []
    for agent in log.get("collection_agents") or []:
        if not isinstance(agent, dict):
            continue
        rows.append(
            f"<tr><td>{_text(agent.get('agent') or agent.get('name'))}</td>"
            f"<td class='small'>{'、'.join(_text(s) for s in (agent.get('sources') or [])) or _text(None)}</td>"
            f"<td>{_num(agent.get('duration_ms'), 2)}</td>"
            f"<td class='small'>{_text(agent.get('status') or agent.get('result'))}</td></tr>")
    for entry in log.get("collection") or []:
        if isinstance(entry, str):
            rows.append(f"<tr><td colspan='4' class='small'>{_text(entry)}</td></tr>")
    return "".join(rows) or "<tr><td colspan='4' class='muted'>沒有採集紀錄。</td></tr>"


def _budget_rows(log) -> str:
    budget = log.get("time_budget") or {}
    ceilings = budget.get("phase_ceilings_seconds") or {}
    actual = budget.get("phase_actual_ms") or {}
    rows = []
    for phase, ceiling in ceilings.items():
        used_ms = actual.get(f"{phase}_ms")
        rows.append(
            f"<tr><td>{_text(phase)}</td><td>{_num(ceiling, 1)} 秒</td>"
            f"<td>{_num(used_ms, 2)} ms</td></tr>")
    return "".join(rows) or "<tr><td colspan='3' class='muted'>沒有時間預算紀錄。</td></tr>"


def _execution_log(log) -> str:
    budget = log.get("time_budget") or {}
    gate = log.get("citation_gate") or {}
    checks = gate.get("checks") or {}
    check_rows = "".join(
        f"<tr><td>{_text(name)}</td><td>{_text(value)}</td></tr>"
        for name, value in checks.items()
    ) or "<tr><td colspan='2' class='muted'>沒有 gate 檢查紀錄。</td></tr>"
    return f"""<section class='card' id='execution-log'>
    <h2>③ 執行紀錄（Execution Log）</h2>
    <p class='small'><b>總耗時：</b>{_num(log.get('duration_ms'), 2)} ms
    　<b>時間預算：</b>{_num(budget.get('budget_seconds'), 1)} 秒
    　<b>競賽硬上限：</b>{_num((budget.get('deadline') or {}).get('competition_hard_ceiling_seconds'), 1)} 秒</p>
    <h3>步驟</h3>
    <div class='tablewrap'><table><thead><tr>
      <th>階段</th><th>狀態</th><th>工具／提供者</th><th>開始</th><th>結束</th>
      <th>耗時 (ms)</th><th>產生的 Evidence</th><th>降級原因</th>
    </tr></thead><tbody>{_step_rows(log.get('steps'))}</tbody></table></div>
    <h3>採集</h3>
    <div class='tablewrap'><table><thead><tr>
      <th>採集 Agent</th><th>負責來源</th><th>耗時 (ms)</th><th>結果</th>
    </tr></thead><tbody>{_collection_rows(log)}</tbody></table></div>
    <h3>三階段時間預算</h3>
    <div class='tablewrap'><table><thead><tr><th>階段</th><th>上限</th><th>實際</th></tr></thead>
    <tbody>{_budget_rows(log)}</tbody></table></div>
    <h3>Citation Gate</h3>
    <p class='small'>{_gate_tag(gate.get('status'))}
    　Claim {_text(gate.get('claim_count'))} 個／Evidence {_text(gate.get('evidence_count'))} 筆
    ／被引用 {_text(gate.get('cited_evidence_count'))} 筆</p>
    <div class='tablewrap'><table><thead><tr><th>檢查項</th><th>結果</th></tr></thead>
    <tbody>{check_rows}</tbody></table></div>
    <details><summary>查看 execution_log.json 原文</summary>
    <pre>{escape(json.dumps(log, ensure_ascii=False, indent=2))}</pre></details>
    </section>"""


# --------------------------------------------------------------------------------------
# 對外入口
# --------------------------------------------------------------------------------------


def collect_known_ids(evidence) -> tuple:
    """回傳 `(evidence_ids, source_item_ids)`。

    Claim 的引用只能連到這兩個集合裡的 ID；不在集合裡的一律不產生連結。
    """
    evidence_ids, item_ids = set(), set()
    for item in evidence or []:
        if not isinstance(item, dict):
            continue
        raw = str(item.get("evidence_id") or "").strip()
        if raw:
            evidence_ids.add(raw)
        for sub in item.get("source_items") or []:
            if isinstance(sub, dict):
                sub_id = str(sub.get("source_item_id") or "").strip()
                if sub_id:
                    item_ids.add(sub_id)
    return evidence_ids, item_ids


def render_report_page(*, run_id, mode, status, result, report, evidence, claims,
                       execution_log, manifest=None, artifact_filenames=(),
                       footer_note="") -> str:
    """把六份提交物渲染成一頁三段式報告。純函式，無 I/O。"""
    evidence_items = evidence if isinstance(evidence, list) else []
    log = execution_log if isinstance(execution_log, dict) else {}
    known_ids, known_item_ids = collect_known_ids(evidence_items)
    coin = (result or {}).get("coin") or ""
    # 標題保留「分析完成」這個既有的成功標記（E1 的 release guardrail 測試以它判斷成功路徑），
    # 再接上這一頁真正的內容名稱。
    headline = f"{coin} 分析完成｜加密市場研究報告（Final Report）" if coin \
        else "分析完成｜加密市場研究報告（Final Report）"
    title = f"{coin} 研究報告" if coin else "研究報告"
    return f"""<!doctype html><html lang='zh-Hant-TW'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>{_text(title)}｜HOYA BIT</title><style>{CSS}</style></head><body>
<main class='wrap'>
{_header(run_id, mode, status, log, manifest or {}, headline)}
{_nav()}
{_downloads(run_id, artifact_filenames)}
{_final_report(result, report, claims, known_ids)}
{_evidence_list(evidence_items, known_ids, known_item_ids)}
{_execution_log(log)}
{f"<p class='small muted'>{_text(footer_note)}</p>" if footer_note else ''}
</main></body></html>"""


# --------------------------------------------------------------------------------------
# E7 — 雙幣比較頁（cloud dual-coin comparison）
# --------------------------------------------------------------------------------------
#
# 為什麼不直接重用 `render_report_page`：一次比較執行有**兩份**完整的 Evidence 清單，而兩腳
# 都從 `EV-001` 開始編號。把兩份清單套進單幣頁會在同一份 HTML 產生兩個 `id='evidence-EV-001'`，
# 瀏覽器只會跳到第一個 —— 引用看起來可追溯，實際上有一半指錯了那一腳。因此比較頁的錨點一律
# 帶幣種前綴（`evidence-ETH-EV-001`），而且連結與錨點由同一組函式產生，不會各寫一套而分岔。
#
# 這裡同樣是純函式：不讀檔、不連網、不重新計算任何分數。畫面上的每個數字都必須能在
# `comparison.json` 或某一腳的 `evidence.json` 找到同一個值。

# 只補比較頁需要的版面（數值靠右、標題列並排），其餘一律沿用上面那份 `CSS`。
_COMPARISON_CSS = """
td.num,th.num{text-align:right;white-space:nowrap}
.pairgrid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media (max-width:720px){.pairgrid{grid-template-columns:1fr}}
"""

_RISK_COMPONENT_ZH = {
    "funding_stress": "資金費率擁擠度",
    "positioning_crowding": "大戶持倉偏斜",
    "rsi_extremity": "RSI 極端度",
    "timeframe_conflict": "時區訊號衝突",
    "realised_volatility": "已實現波動度",
}


def _amount(value) -> str:
    """並列表格的數值。None 一律顯示「來源未提供」而不是 0：一個採集失敗的來源不該看起來
    像量到了 0。"""
    if value is None or isinstance(value, bool):
        return _text(value if isinstance(value, bool) else None)
    if isinstance(value, float):
        return escape(f"{value:,.2f}")
    if isinstance(value, int):
        return escape(f"{value:,}")
    return _text(value)


def pair_evidence_anchor(coin, evidence_id) -> str:
    """比較頁的 Evidence 錨點。帶幣種前綴，因為兩腳的 ID 會重號。"""
    return _evidence_anchor_id(f"{coin}-{evidence_id}")


def pair_evidence_ref(coin, evidence_id, known_ids) -> str:
    """某一腳引用的 Evidence ID → 站內連結。

    ID 不在那一腳的有效 Evidence 裡時**不產生連結**，理由與單幣頁相同：一個可點但指向不存在
    錨點的連結，看起來與可追溯的引用一模一樣。
    """
    raw = str(evidence_id or "").strip()
    if not raw:
        return ""
    if raw in (known_ids or set()):
        return (f"<a href='#{escape(pair_evidence_anchor(coin, raw))}'>"
                f"{escape(str(coin))}／{escape(raw)}</a>")
    return f"<span class='tag neg'>{escape(raw)}{escape(UNKNOWN_EVIDENCE_MARK)}</span>"


def _pair_refs(coin, ids, known_ids) -> str:
    refs = [pair_evidence_ref(coin, i, known_ids) for i in (ids or []) if str(i).strip()]
    return "、".join(refs) if refs else "<span class='muted'>無</span>"


def _dimension_block(*, anchor, title, tag, verdict, note, basis, rows,
                     coin_a, coin_b, cited_a, cited_b, known_a, known_b) -> str:
    body = "".join(
        f"<tr><td>{_text(label)}</td><td class='num'>{_amount(value_a)}</td>"
        f"<td class='num'>{_amount(value_b)}</td></tr>"
        for label, value_a, value_b in rows
    ) or "<tr><td colspan='3' class='muted'>沒有可列示的指標。</td></tr>"
    note_html = f"<p class='small notice'>{_text(note)}</p>" if str(note or "").strip() else ""
    return f"""<article class='card' id='{escape(str(anchor))}'>
    <h3>{_text(title)}　<span class='tag'>{_text(tag)}</span></h3>
    <p><b>判讀：</b>{_text(verdict)}</p>
    {note_html}
    <div class='tablewrap'><table><thead><tr><th>指標</th>
      <th class='num'>{_text(coin_a)}</th><th class='num'>{_text(coin_b)}</th>
    </tr></thead><tbody>{body}</tbody></table></div>
    <div class='pairgrid'>
      <p class='small kv'><b>{_text(coin_a)} 引用的 Evidence：</b>
      {_pair_refs(coin_a, cited_a, known_a)}</p>
      <p class='small kv'><b>{_text(coin_b)} 引用的 Evidence：</b>
      {_pair_refs(coin_b, cited_b, known_b)}</p>
    </div>
    <p class='small muted'>判讀依據：{_text(basis)}</p>
    </article>"""


def _pair_evidence_rows(coin, evidence) -> str:
    rows = []
    for item in evidence or []:
        if not isinstance(item, dict):
            continue
        rows.append(
            f"<tr id='{escape(pair_evidence_anchor(coin, item.get('evidence_id')))}'>"
            f"<td class='small'>{_text(item.get('evidence_id'))}</td>"
            f"<td>{_text(item.get('source'))}</td>"
            f"<td class='small'>{_text(item.get('data_type'))}</td>"
            f"<td class='small'>{_text(item.get('fetched_at'))}</td>"
            f"<td class='num'>{_num(item.get('reliability_score'), 4)}</td>"
            f"<td class='wrap-any small'>{link(item.get('source_url'))}</td></tr>")
    return "".join(rows) or "<tr><td colspan='6' class='muted'>這一腳沒有有效證據。</td></tr>"


def _pair_source_item_rows(coin, evidence) -> str:
    """子項 locator。title／author／url 全部來自外部來源，一律走 `_text`／`link`。"""
    rows = []
    for item in evidence or []:
        if not isinstance(item, dict):
            continue
        for sub in item.get("source_items") or []:
            if not isinstance(sub, dict):
                continue
            anchor = _item_anchor_id(f"{coin}-{sub.get('source_item_id') or ''}")
            rows.append(
                f"<tr id='{escape(anchor)}'>"
                f"<td class='wrap-any small'>{_text(sub.get('source_item_id'))}</td>"
                f"<td>{_text(sub.get('title') or sub.get('text'))}</td>"
                f"<td class='small'>{_text(sub.get('author') or sub.get('author_handle'))}</td>"
                f"<td class='small'>{_text(sub.get('publisher') or sub.get('platform'))}</td>"
                f"<td class='wrap-any small'>{link(sub.get('url'))}</td></tr>")
    return "".join(rows)


def _leg_section(coin, leg_result, evidence, gate_status) -> str:
    result = leg_result if isinstance(leg_result, dict) else {}
    reasoning = result.get("reasoning") or {}
    item_rows = _pair_source_item_rows(coin, evidence)
    items_html = (f"""<details><summary>{_text(coin)} 的來源子項（標題／作者／原始連結）</summary>
    <div class='tablewrap'><table><thead><tr><th>子項 ID</th><th>標題／內容</th><th>作者</th>
    <th>出版商／平台</th><th>原始連結</th></tr></thead><tbody>{item_rows}</tbody></table></div>
    </details>""" if item_rows else "")
    return f"""<section class='card'>
    <h3>{_text(coin)} 這一腳的證據與結論</h3>
    <p class='small'><b>Run ID：</b>{_text(result.get('run_id'))}
    　<b>狀態：</b>{_text(result.get('run_status'))}
    　{_gate_tag(gate_status)}
    　<b>信心：</b>{_num(reasoning.get('confidence'), 3)}</p>
    <p>{_text(reasoning.get('market_judgment'))}</p>
    <div class='tablewrap'><table><thead><tr><th>Evidence ID</th><th>資料來源</th><th>資料類型</th>
      <th>系統擷取時間</th><th class='num'>可信度</th><th>原始連結</th>
    </tr></thead><tbody>{_pair_evidence_rows(coin, evidence)}</tbody></table></div>
    {items_html}
    <p class='small muted'>這一腳的完整報告在 <code>{_text(coin)}/report.md</code>，
    證據清單在 <code>{_text(coin)}/evidence.json</code>（含於「全部產出」ZIP）。</p>
    </section>"""


def _comparison_header(run_id, mode, status, question, coin_a, coin_b, manifest,
                       gate_by_coin) -> str:
    data = manifest if isinstance(manifest, dict) else {}
    lifecycle = data.get("run_lifecycle") or {}
    degradations = [d for d in (data.get("degradation_reasons") or []) if str(d).strip()]
    degrade_html = (
        "<div class='notice small'><b>本次降級項目（兩腳合併）：</b>"
        + "、".join(_text(d) for d in degradations) + "</div>"
        if degradations else "<p class='small muted'>本次沒有降級項目。</p>"
    )
    gates = "　".join(
        f"{_text(coin)}：{_gate_tag((gate_by_coin or {}).get(coin))}"
        for coin in (coin_a, coin_b)
    )
    window = data.get("shared_time_window") or {}
    return f"""<section class='card'>
    <h1>{_text(coin_a)} vs {_text(coin_b)} 比較分析完成｜雙幣並列研究報告</h1>
    <p class='small muted'>公開雲端展示僅提供 test mode；正式（formal）執行只透過受控管道進行。</p>
    <p class='kv'><b>研究問題：</b>{_text(question)}</p>
    <ul class='meta'>
      <li>Run ID：{_text(run_id)}</li>
      <li>執行性質：{_text(mode)}</li>
      <li>狀態：{_text(status)}</li>
      <li>比較標的：{_text(coin_a)}　vs　{_text(coin_b)}</li>
      <li>共用時間窗：{_text(window.get('days'))} 天（{_text(window.get('source'))}）</li>
      <li>資料截止（as-of）：{_text(data.get('as_of') or lifecycle.get('started_at'))}</li>
    </ul>
    <p class='small kv'><b>各腳引用檢核：</b>{gates}</p>
    {degrade_html}
    </section>"""


def _comparison_nav() -> str:
    return """<nav class='tabs' aria-label='比較報告區段'>
    <a href='#comparison-summary'>① 比較結論（Comparison）</a>
    <a href='#comparison-dimensions'>② 三維度並列（Dimensions）</a>
    <a href='#comparison-evidence'>③ 兩腳證據（Evidence）</a>
    </nav>"""


def _comparison_downloads(run_id, coin_a, coin_b, artifact_filenames) -> str:
    run = escape(str(run_id or ""))
    links = "　".join(
        f"<a href='/artifact?path={escape(str(name))}&run={run}&download=1'>{escape(str(name))}</a>"
        for name in (artifact_filenames or ())
    ) or "<span class='muted'>無</span>"
    return f"""<section class='card' id='comparison-downloads'>
    <h2>下載提交物</h2>
    <p><a href='/download?scope=all&run={run}'>下載全部產出（ZIP，含兩腳各自的六份提交物）</a>
    　｜　<a href='/download?scope=required&run={run}'>下載三份提交物（ZIP）</a></p>
    <p class='small'>{links}</p>
    <p class='small muted'>比較執行的 report.md／evidence.json／execution_log.json 分別寫在
    <code>{_text(coin_a)}/</code> 與 <code>{_text(coin_b)}/</code> 子目錄，
    因此「三份提交物」ZIP 會依幣種分資料夾收錄，兩腳各三份共六個項目；
    比較檔（comparison.md／comparison.json）與 manifest 請用「全部產出」。</p>
    <p class='small muted'>提交物寫在處理該次執行的容器暫存目錄。冷啟動或被路由到其他容器時
    連結會回 404，這是 Lambda 的固有限制，不是執行失敗。</p>
    </section>"""


def render_comparison_page(*, run_id, mode, status, payload, evidence_by_coin,
                           artifact_filenames=(), footer_note="") -> str:
    """把一次比較執行渲染成一頁並列報告。純函式，無 I/O。

    `payload` 是 `src.orchestrator.run_comparison()` 的回傳值；`evidence_by_coin` 是
    `{coin: evidence list}`，由呼叫端從各腳的 `evidence.json` 載入。
    """
    data = payload if isinstance(payload, dict) else {}
    coins = [str(c) for c in (data.get("coins") or [])]
    coin_a = coins[0] if coins else ""
    coin_b = coins[1] if len(coins) > 1 else ""
    profiles = data.get("profiles") or {}
    profile_a = profiles.get(coin_a) or {}
    profile_b = profiles.get(coin_b) or {}
    comparison = data.get("comparison") or {}
    manifest = data.get("manifest") or {}
    results = data.get("results") or {}
    evidence_map = evidence_by_coin if isinstance(evidence_by_coin, dict) else {}
    evidence_a = evidence_map.get(coin_a) or []
    evidence_b = evidence_map.get(coin_b) or []
    known_a, _ = collect_known_ids(evidence_a)
    known_b, _ = collect_known_ids(evidence_b)
    gate_by_coin = (manifest.get("validation") or {}).get("citation_gate_status_by_coin") or {}

    liquidity_a = profile_a.get("liquidity") or {}
    liquidity_b = profile_b.get("liquidity") or {}
    risk_a = profile_a.get("risk_exposure") or {}
    risk_b = profile_b.get("risk_exposure") or {}
    attention_a = profile_a.get("attention") or {}
    attention_b = profile_b.get("attention") or {}

    risk_rows = [
        ("綜合暴險分數（0-100）", risk_a.get("composite_score"), risk_b.get("composite_score")),
        ("可用組成項目數", risk_a.get("components_available"), risk_b.get("components_available")),
    ]
    for key, label in _RISK_COMPONENT_ZH.items():
        risk_rows.append((label, (risk_a.get("components") or {}).get(key),
                          (risk_b.get("components") or {}).get(key)))

    dimensions = "".join([
        _dimension_block(
            anchor="dimension-liquidity", title="流動性", tag="Liquidity",
            verdict=(comparison.get("liquidity") or {}).get("verdict"), note="",
            basis=liquidity_a.get("basis") or liquidity_b.get("basis"),
            rows=[("平均日成交額（USD）", liquidity_a.get("avg_daily_volume_usd"),
                   liquidity_b.get("avg_daily_volume_usd")),
                  ("最新成交額（USD）", liquidity_a.get("latest_volume_usd"),
                   liquidity_b.get("latest_volume_usd")),
                  ("成交量變化（%）", liquidity_a.get("volume_trend_pct"),
                   liquidity_b.get("volume_trend_pct"))],
            coin_a=coin_a, coin_b=coin_b,
            cited_a=[liquidity_a.get("evidence_id")], cited_b=[liquidity_b.get("evidence_id")],
            known_a=known_a, known_b=known_b),
        _dimension_block(
            anchor="dimension-risk", title="風險敞口", tag="Risk exposure",
            verdict=(comparison.get("risk_exposure") or {}).get("verdict"), note="",
            basis=risk_a.get("basis") or risk_b.get("basis"), rows=risk_rows,
            coin_a=coin_a, coin_b=coin_b,
            cited_a=risk_a.get("cited_evidence_ids"), cited_b=risk_b.get("cited_evidence_ids"),
            known_a=known_a, known_b=known_b),
        _dimension_block(
            anchor="dimension-attention", title="市場關注度", tag="Market attention",
            verdict=(comparison.get("attention") or {}).get("verdict"),
            note=(comparison.get("attention") or {}).get("note"),
            basis=attention_a.get("basis") or attention_b.get("basis"),
            rows=[("社群互動總量（無抓取上限）", attention_a.get("social_engagement_total"),
                   attention_b.get("social_engagement_total")),
                  ("新聞則數（抓取上限 5）", attention_a.get("news_article_count"),
                   attention_b.get("news_article_count")),
                  ("官方公告則數（抓取上限 5）", attention_a.get("official_announcement_count"),
                   attention_b.get("official_announcement_count")),
                  ("社群貼文數（抓取上限 25）", attention_a.get("social_post_count"),
                   attention_b.get("social_post_count")),
                  ("則數已達抓取上限", attention_a.get("counts_are_capped"),
                   attention_b.get("counts_are_capped"))],
            coin_a=coin_a, coin_b=coin_b,
            cited_a=attention_a.get("cited_evidence_ids"),
            cited_b=attention_b.get("cited_evidence_ids"),
            known_a=known_a, known_b=known_b),
    ])

    plan = data.get("research_plan") if isinstance(data.get("research_plan"), dict) else {}
    return f"""<!doctype html><html lang='zh-Hant-TW'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>{_text(coin_a)} vs {_text(coin_b)} 比較研究｜HOYA BIT</title>
<style>{CSS}{_COMPARISON_CSS}</style></head><body>
<main class='wrap'>
{_comparison_header(run_id, mode, status, data.get('question'), coin_a, coin_b,
                    manifest, gate_by_coin)}
{_comparison_nav()}
{_comparison_downloads(run_id, coin_a, coin_b, artifact_filenames)}
<section class='card' id='comparison-summary'>
  <h2>① 比較結論（Comparison）</h2>
  <p>{_text(comparison.get('summary'))}</p>
  <p class='small kv'><b>共用研究計畫：</b>比較維度
  {_text('、'.join(str(d) for d in (plan.get('comparison_dimensions') or [])) or None)}
  ／必要資料領域
  {_text('、'.join(str(d) for d in (plan.get('required_domains') or [])) or None)}</p>
  <div class='notice small'><b>比較限制：</b>{_text(comparison.get('caveat'))}</div>
  <p class='small muted'>兩腳共用同一組時間窗與同一個時間預算，因此比較是同一個時間切面的相對
  差異，不是預測。本頁僅供研究與展示用途，不構成投資建議。</p>
</section>
<section class='card' id='comparison-dimensions'>
  <h2>② 三維度並列（Dimensions）</h2>
  <p class='small muted'>每個維度的數值都來自各腳自己的 Evidence；右側列出那一腳實際引用的
  Evidence ID，點擊可跳到下一段的該筆證據。來源不可用時顯示「{escape(NOT_PROVIDED)}」，
  不以 0 代填。</p>
  {dimensions}
</section>
<section class='card' id='comparison-evidence'>
  <h2>③ 兩腳證據（Evidence）</h2>
  <p class='small muted'>兩腳的 Evidence ID 各自從 EV-001 起編號，因此本頁的錨點帶幣種前綴
  （例如 <code>evidence-{_text(coin_a)}-EV-001</code>）以避免兩腳互相指錯。</p>
  {_leg_section(coin_a, results.get(coin_a), evidence_a, gate_by_coin.get(coin_a))}
  {_leg_section(coin_b, results.get(coin_b), evidence_b, gate_by_coin.get(coin_b))}
</section>
<section class='card'>
  <h2>原始比較檔</h2>
  <details><summary>查看 comparison.md 原文</summary>
  <pre>{escape(str(data.get('markdown') or ''))}</pre></details>
  <p class='small muted'>本比較僅供研究與展示用途，不構成投資建議，也不產生任何可執行的
  買賣訊號。</p>
</section>
{f"<p class='small muted'>{_text(footer_note)}</p>" if footer_note else ''}
</main></body></html>"""
