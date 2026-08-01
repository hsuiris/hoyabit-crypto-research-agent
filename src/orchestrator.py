"""Day 2 initial Orchestrator: collect evidence, analyse, and write outputs."""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .artifact_store import LocalArtifactStore
from .claim_graph import (CLAIM_SCORING_VERSION, CLAIM_TIMEOUT_SECONDS, DOMAIN_BY_DATA_TYPE,
                          GRAPH_SOURCE_LLM, apply_related_claim_ids, build_claim_graph,
                          claims_document)
from .comparison import build_profile, compare_profiles, comparison_markdown
from .credibility import load_source_registry, parse_timestamp, score_evidence_batch
from .day2_sources import (SOCIAL_DATA_TYPES, collect_evidence_detailed,
                           stamp_credibility_metadata)
from .report_renderer import render_claims_section, render_competition_report
from .run_context import RunContext
from .run_manager import (FINALIZATION_DEADLINE_SECONDS_MAX, HARD_DEADLINE_SECONDS_MAX,
                          STATUS_COMPLETED, STATUS_COMPLETED_DEGRADED, STATUS_CREATED,
                          STATUS_FAILED, STATUS_RUNNING, compute_question_hash, evaluate_deadline)
from .schemas import (ARTIFACT_FILENAMES, ASSESSMENT_SOURCE_FALLBACK, ASSESSMENT_SOURCE_LLM,
                      ASSESSMENT_VERSION, CITATION_GATE_VERSION, CONFIDENCE_WEIGHTS,
                      CREDIBILITY_WEIGHTS, GATE_STATUS_FAIL, HARD_CAPS,
                      IMPACT_DIRECTION_NOT_APPLICABLE, IMPACT_DIRECTION_UNCLEAR,
                      IMPACT_HORIZON_UNCLEAR, MANIFEST_VERSION,
                      MAX_ASSESSMENT_RATIONALE_CHARS, MAX_ASSESSMENT_SOURCE_ITEM_IDS,
                      QUESTION_RELATIONSHIP_CONTEXT, QUESTION_RELATIONSHIP_IRRELEVANT,
                      QUESTION_RELATIONSHIP_UNCLEAR, RELEVANCE_LABEL_CONTEXT,
                      RELEVANCE_LABEL_DIRECT, RELEVANCE_LABEL_INDIRECT,
                      RELEVANCE_LABEL_IRRELEVANT, RELEVANCE_LABEL_SCORES,
                      RELEVANCE_LABEL_UNCLEAR, SCHEMA_VERSION, SCORING_VERSION,
                      SOURCE_TYPE_FALLBACK_FIXTURE, VERDICT_INSUFFICIENT_EVIDENCE,
                      effective_weight, relevance_score_for)
from .validation import (run_citation_gate, validate_claims, validate_evidence,
                         validate_evidence_assessments)
from .errors import AgentInputError, validate_request
from .llm import (CLAIM_PROPOSAL_RESULT_FIELD, EVIDENCE_ASSESSMENT_RESULT_FIELD,
                  analyze_with_llm, critique_with_llm, default_llm_client, llm_is_configured,
                  llm_runtime_info)
from .ohlcv import downsample, load_ohlcv, price_windows
from .planner import (PLANNER_TIMEOUT_SECONDS, PLANNING_PATH_LLM, build_research_plan,
                      plan_to_dict)


_TREND_LABELS = {"bullish_aligned": "多頭排列", "bearish_aligned": "空頭排列", "mixed": "訊號不一"}
_SIGNAL_LABELS = {"bull_entry": "多頭進場", "bear_entry": "空頭進場", "take_profit_bull": "多頭停利", "take_profit_bear": "空頭停利"}

# Total wall-clock budget for one analysis. The competition allows 15 minutes; this sits below that
# so a slow venue network degrades to offline reasoning instead of running out the clock.
# Three phases with independent ceilings, sized to finish inside the competition's 15-minute limit
# with room to spare. Each phase ends as soon as its work is done -- the ceiling is a cap, not a
# target, and a typical live run finishes all three in well under a minute.
COLLECTION_PHASE_SECONDS = 420.0   # 7 min: gather evidence, including full-text crawling
REASONING_PHASE_SECONDS = 180.0    # 3 min: organise the evidence into a cited report
CRITIC_PHASE_SECONDS = 120.0       # 2 min: audit the report against its own evidence
DEFAULT_TIME_BUDGET_SECONDS = COLLECTION_PHASE_SECONDS + REASONING_PHASE_SECONDS + CRITIC_PHASE_SECONDS
_LLM_MIN_SECONDS = 20.0
_CRITIC_MIN_SECONDS = 15.0
# Claim building runs after the critic and, like the planner, draws on whatever is left of the total
# deadline instead of getting a phase ceiling of its own -- so adding it cannot push the run past the
# competition limit. Below this margin no model is called and the deterministic claim path is used.
_CLAIM_MIN_SECONDS = 15.0
# Planning runs before collection and draws on the same total deadline instead of getting a phase
# ceiling of its own, so adding a planner cannot push the run past the competition limit. Below this
# remaining margin the planner is not attempted at all -- the deterministic keyword plan is instant.
_PLANNER_MIN_SECONDS = 5.0

# Where the plan came from, as recorded in the Execution Log. "shared" means the plan was built once
# by a comparison run and handed to both legs, so both coins are judged over the same time window.
_PLANNING_PATH_SHARED = "shared"

# --------------------------------------------------------------------------------------
# T7 — finalization window
# --------------------------------------------------------------------------------------

# 收尾窗口佔 hard deadline 的比例，取自比賽的 840 / 900。套在既有 720 秒預算上得到 672 秒，
# 也就是最後 48 秒一律留給「把六項提交物寫出來」，不再開始任何新的模型呼叫或補充蒐集。
# 這個比例只會讓系統更早收尾，不會放寬既有的三階段預算。
FINALIZATION_DEADLINE_RATIO = FINALIZATION_DEADLINE_SECONDS_MAX / HARD_DEADLINE_SECONDS_MAX
DEFAULT_FINALIZATION_DEADLINE_SECONDS = DEFAULT_TIME_BUDGET_SECONDS * FINALIZATION_DEADLINE_RATIO

# 收尾原因的字面值；同時出現在 Execution Log 的階段狀態與 manifest 的降級清單裡。
_FINALIZATION_REASON = "finalization_window_reached"


def _deadline_bounds(time_budget_seconds: float,
                     finalization_deadline_seconds: float | None) -> tuple[float, float]:
    """把呼叫端給的預算收斂到比賽上限之內，並算出對應的收尾門檻。

    比賽的硬上限是 900 秒、收尾門檻是 840 秒；本專案預設仍用更保守的 720 / 672，
    而且這裡只會往下夾（`min`），不會因為「比賽允許 15 分鐘」就把既有預算放寬。
    """
    hard = min(float(time_budget_seconds), HARD_DEADLINE_SECONDS_MAX)
    if finalization_deadline_seconds is None:
        finalization = hard * FINALIZATION_DEADLINE_RATIO
    else:
        finalization = float(finalization_deadline_seconds)
    finalization = min(finalization, FINALIZATION_DEADLINE_SECONDS_MAX, hard)
    return hard, finalization


def _deadline_state(deadline: float, hard_seconds: float, finalization_seconds: float):
    """目前時間點的 deadline 決策，交給 `src/run_manager.evaluate_deadline` 這個純函式判斷。

    `deadline` 是 monotonic 絕對時點（比較題的兩腳共用同一個），所以已耗用時間由「預算減去剩餘」
    回推，而不是各自從自己的起點量 —— 否則第二腳會誤以為自己還有完整預算。
    """
    remaining = deadline - time.monotonic()
    elapsed = max(0.0, hard_seconds - remaining)
    return evaluate_deadline(elapsed, hard_deadline_seconds=hard_seconds,
                             finalization_deadline_seconds=finalization_seconds)


def _degradation_reasons(collection_log: list, credibility: dict, *, llm_status: str,
                         critic_status: str, claim_graph: dict, gate: dict,
                         claim_client_reason: str = "") -> list[str]:
    """本次執行實際發生的降級。空清單代表 `COMPLETED`，非空代表 `COMPLETED_DEGRADED`。

    刻意只計入「偏離原計畫」的事：使用者主動選擇離線模式（`use_llm=False`）不是降級，
    但某個 collector 失敗、watchdog 跳過來源、模型呼叫失敗、收尾窗口迫使跳過 Critic 都是。
    這條線是為了讓 `COMPLETED` 真的代表「照計畫跑完」，而不是變成人人有獎的標籤。
    """
    reasons = []
    for entry in collection_log or []:
        if ":skipped:" in str(entry):
            reasons.append(f"collector_skipped:{str(entry).split(':')[0]}")
        elif ":fallback:" in str(entry):
            reasons.append(f"collector_fallback:{str(entry).split(':')[0]}")
    if credibility.get("evidence_count") and not credibility.get("substantive_count"):
        reasons.append("all_evidence_is_fallback_fixture")
    if credibility.get("rejected_ids"):
        reasons.append("rejected_evidence:" + ",".join(credibility["rejected_ids"]))
    if str(llm_status).startswith("fallback:"):
        reasons.append(f"analysis_{llm_status}")
    critic_status = str(critic_status)
    # `skipped:disabled` 是「這次刻意不跑 Critic」，不是降級；因逾時或收尾窗口而跳過才是。
    if critic_status.startswith("fallback:") or (
            critic_status.startswith("skipped:") and critic_status != "skipped:disabled"):
        reasons.append(f"critic_{critic_status}")
    # 走 deterministic claim 路徑本身不是降級（沒開模型時那就是原本的計畫）；只有「模型提案被作廢
    # 後重建」或「因時間不足而不敢呼叫模型」才算偏離計畫。
    claim_fallback = str(claim_graph.get("fallback_reason") or "")
    if claim_fallback.startswith(("claim validation failed", "citation gate failed")):
        reasons.append(f"claims_rebuilt_deterministically:{claim_fallback[:80]}")
    if claim_client_reason == _FINALIZATION_REASON or str(claim_client_reason).startswith(
            "time_budget_remaining"):
        reasons.append(f"claims_model_skipped:{claim_client_reason}")
    # Citation gate 的 warning 不計入降級：那代表「可發佈，但必須把警告印在報告裡」，
    # 報告本身已經印了；只有 FAIL 會讓執行中止，走不到這裡。
    if gate.get("status") == GATE_STATUS_FAIL:  # pragma: no cover - gate FAIL 會先拋出例外
        reasons.append("citation_gate_failed")
    # 同一個原因只保留一次，順序維持首次出現的順序，讓 manifest 的清單穩定可比對。
    return list(dict.fromkeys(reasons))


def _signal_inventory(result: dict, evidence: list) -> list[dict]:
    """Turn the raw evidence into directional signals with an explicit side and weight.

    This is what lets the offline path argue rather than recite: the same inventory drives the
    judgment, the inferences and -- by taking whichever side loses -- the counter-evidence, so the
    bear case is always built from signals actually present in this run.
    """
    signals: list[dict] = []

    def add(side: str, text: str, evidence_id: str | None, weight: float = 1.0):
        signals.append({"side": side, "text": text, "evidence_id": evidence_id or "N/A", "weight": weight})

    def find(data_type: str):
        return next((item for item in evidence if item.data_type == data_type), None)

    market = find("market")
    if market:
        return_pct = market.content.get("return_pct")
        if isinstance(return_pct, (int, float)):
            if return_pct > 2:
                add("bull", f"期間報酬 +{return_pct}%，價格動能為正", market.evidence_id, 1.0)
            elif return_pct < -2:
                add("bear", f"期間報酬 {return_pct}%，價格動能為負", market.evidence_id, 1.0)
            else:
                add("neutral", f"期間報酬 {return_pct}%，價格接近持平", market.evidence_id, 0.5)

    vegas = find("vegas_channel")
    if vegas:
        tf_4h, tf_1h = vegas.content.get("4h"), vegas.content.get("1h")
        alignment = vegas.content.get("alignment")
        if tf_4h and tf_4h.get("trend") == "bullish_aligned":
            add("bull", "4H Vegas 通道為多頭排列，中期趨勢向上", vegas.evidence_id, 1.2)
        elif tf_4h and tf_4h.get("trend") == "bearish_aligned":
            add("bear", "4H Vegas 通道為空頭排列，中期趨勢向下", vegas.evidence_id, 1.2)
        if alignment and not alignment.get("passed"):
            add("neutral", "4H 與 1H 方向不一致，趨勢訊號本身互相矛盾", vegas.evidence_id, 1.0)
        rsi = tf_1h.get("rsi") if tf_1h else None
        if isinstance(rsi, (int, float)):
            if rsi > 80:
                add("bear", f"1H RSI(6) 為 {rsi}，處於過熱區，追高的回檔風險升高", vegas.evidence_id, 1.0)
            elif rsi < 20:
                add("bull", f"1H RSI(6) 為 {rsi}，處於超賣區，具備技術性反彈條件", vegas.evidence_id, 1.0)
        if tf_1h and tf_1h.get("volume_spike"):
            add("neutral", "1H 出現兩倍以上爆量，代表當前價格動作有量能背書但也常見於情緒高點", vegas.evidence_id, 0.8)

    derivatives = find("derivatives")
    if derivatives:
        bias, rate = derivatives.content.get("bias"), derivatives.content.get("funding_rate_pct")
        if bias == "long_crowded":
            add("bear", f"資金費率 {rate}%，多方付費過熱；擁擠的多單是逼空反轉的燃料", derivatives.evidence_id, 1.1)
        elif bias == "short_crowded":
            add("bull", f"資金費率 {rate}%，空方付費過熱；擁擠的空單容易造成軋空", derivatives.evidence_id, 1.1)
        elif bias == "balanced":
            add("neutral", f"資金費率 {rate}%，多空成本接近平衡，衍生品未顯示明顯偏斜", derivatives.evidence_id, 0.5)

    lsratio = find("long_short_ratio")
    if lsratio:
        current, consistency = lsratio.content.get("current"), lsratio.content.get("consistency_pct")
        bias = lsratio.content.get("bias")
        if current and bias == "long_dominant":
            add("bear", f"大戶持倉 {current.get('long_pct')}% 偏多（近期一致性 {consistency}%），持倉過度集中於單邊時反向風險上升", lsratio.evidence_id, 0.9)
        elif current and bias == "short_dominant":
            add("bull", f"大戶持倉 {current.get('short_pct')}% 偏空（近期一致性 {consistency}%），空方集中時的回補動能較強", lsratio.evidence_id, 0.9)

    social = find("social")
    if social:
        sentiment = social.content.get("sentiment")
        positive, negative = social.content.get("positive_terms"), social.content.get("negative_terms")
        if sentiment == "positive":
            add("bull", f"社群討論偏正向（正向詞 {positive} / 負向詞 {negative}），但社群為低可靠度來源", social.evidence_id, 0.4)
        elif sentiment == "negative":
            add("bear", f"社群討論偏負向（正向詞 {positive} / 負向詞 {negative}），但社群為低可靠度來源", social.evidence_id, 0.4)

    macro = find("macro")
    if macro:
        value = macro.content.get("fear_greed_value")
        direction = macro.content.get("risk_appetite_direction")
        if isinstance(value, (int, float)):
            if value <= 25:
                add("bull", f"全市場恐懼貪婪指數 {value}（極度恐懼），為典型的反向布局區間，但同時代表整體風險偏好低落", macro.evidence_id, 0.9)
            elif value >= 75:
                add("bear", f"全市場恐懼貪婪指數 {value}（極度貪婪），市場情緒過熱，追價的風險報酬比惡化", macro.evidence_id, 0.9)
            else:
                add("neutral", f"全市場恐懼貪婪指數 {value}，情緒位於中性區間", macro.evidence_id, 0.5)
        if direction == "risk_appetite_deteriorating":
            add("bear", "近兩週市場風險偏好轉弱，個別幣種的多頭訊號需要更強的證據支撐", macro.evidence_id, 0.8)
        elif direction == "risk_appetite_improving":
            add("bull", "近兩週市場風險偏好回升，對風險資產形成順風", macro.evidence_id, 0.8)

    announcement = find("announcement")
    if announcement:
        items = announcement.content.get("items") or []
        if items:
            add("neutral", f"官方管道近期有 {len(items)} 則發布，最新為『{items[0].get('title', '')[:60]}』，屬事件面背景而非方向性訊號", announcement.evidence_id, 0.4)

    tvl = find("tvl")
    if tvl:
        change_30d = tvl.content.get("change_30d_pct")
        direction = tvl.content.get("direction")
        chain = tvl.content.get("chain")
        if isinstance(change_30d, (int, float)):
            # TVL is a slow fundamentals series, so it earns less weight than price or positioning:
            # it corroborates or contradicts a move rather than timing one.
            if direction == "expanding":
                add("bull", f"{chain} 鏈上鎖倉量近 30 天成長 {change_30d}%，資金實際流入鏈上而非僅有價格波動", tvl.evidence_id, 0.7)
            elif direction == "contracting":
                add("bear", f"{chain} 鏈上鎖倉量近 30 天萎縮 {change_30d}%，鏈上資金外流，基本面未支撐價格", tvl.evidence_id, 0.7)
            else:
                add("neutral", f"{chain} 鏈上鎖倉量近 30 天變動 {change_30d}%，基本面大致持平", tvl.evidence_id, 0.4)

    return signals


# A directional call is only meaningful once enough independent weight is on the table, and only
# when one side clears the other by a real margin -- otherwise the honest answer is "neutral".
_STANCE_MIN_WEIGHT = 1.5
_STANCE_MARGIN = 0.25
_STANCE_LABELS = {
    "bullish": ("偏多", "Bullish"), "neutral": ("中性", "Neutral"), "bearish": ("偏空", "Bearish"),
}


def _market_stance(signals: list[dict]) -> dict:
    """Reduce the weighted signal inventory to one discrete Bullish / Neutral / Bearish call.

    Deterministic on purpose: the stance is derived from the same inventory that produces the
    inferences and counter-evidence, so it is reproducible, testable, identical on the offline
    path, and every label can be traced back to the evidence IDs that drove it -- none of which
    would hold if the stance were another free-text field asked of the model.
    """
    bull = round(sum(item["weight"] for item in signals if item["side"] == "bull"), 2)
    bear = round(sum(item["weight"] for item in signals if item["side"] == "bear"), 2)
    directional = bull + bear
    margin = ((bull - bear) / directional) if directional else 0.0

    if directional < _STANCE_MIN_WEIGHT:
        stance, basis = "neutral", f"方向性訊號權重僅 {directional:.1f}（門檻 {_STANCE_MIN_WEIGHT}），不足以形成方向判讀"
    elif margin >= _STANCE_MARGIN:
        stance, basis = "bullish", f"多方權重 {bull:.1f} 領先空方 {bear:.1f}，淨優勢 {margin * 100:.0f}%"
    elif margin <= -_STANCE_MARGIN:
        stance, basis = "bearish", f"空方權重 {bear:.1f} 領先多方 {bull:.1f}，淨優勢 {-margin * 100:.0f}%"
    else:
        stance, basis = "neutral", f"多方 {bull:.1f} / 空方 {bear:.1f}，淨優勢僅 {abs(margin) * 100:.0f}%（門檻 {_STANCE_MARGIN * 100:.0f}%），方向不明確"

    winning = "bull" if stance == "bullish" else "bear" if stance == "bearish" else None
    drivers = [
        {"text": item["text"], "evidence_id": item["evidence_id"], "weight": item["weight"]}
        for item in sorted(signals, key=lambda entry: -entry["weight"])
        if winning and item["side"] == winning
    ][:3]
    label_zh, label_en = _STANCE_LABELS[stance]
    return {
        "stance": stance, "label": label_zh, "label_en": label_en,
        "bull_weight": bull, "bear_weight": bear, "net_margin_pct": round(margin * 100, 1),
        "signal_count": len(signals), "basis": basis, "drivers": drivers,
    }


def _dynamic_risk_factors(result: dict, evidence: list, signals: list[dict]) -> list[str]:
    """Risks grounded in this run's actual data gaps and conflicts, not a fixed disclaimer list."""
    risks = []
    degraded = [entry for entry in result.get("collection_log", []) if "fallback" in entry or "skipped" in entry]
    for entry in degraded:
        label = entry.split(":")[0]
        cause = "逾時跳過" if "skipped" in entry else entry.split(":")[-1]
        risks.append(f"{label} 來源本次未取得即時資料（{cause}），該面向的結論僅為部分覆蓋。")

    bull = sum(item["weight"] for item in signals if item["side"] == "bull")
    bear = sum(item["weight"] for item in signals if item["side"] == "bear")
    if bull and bear and 0.6 <= bull / bear <= 1.67:
        risks.append(f"多空訊號強度接近（多方 {bull:.1f} / 空方 {bear:.1f}），方向判斷的解析度不足，不宜據此做單邊推論。")

    vegas = next((item for item in evidence if item.data_type == "vegas_channel"), None)
    alignment = vegas.content.get("alignment") if vegas else None
    if alignment and not alignment.get("passed"):
        risks.append("4H 與 1H 時區方向不一致，策略層面屬於不進場條件，任何方向性結論的可信度都應下修。")

    lsratio = next((item for item in evidence if item.data_type == "long_short_ratio"), None)
    consistency = lsratio.content.get("consistency_pct") if lsratio else None
    if isinstance(consistency, (int, float)) and consistency < 60:
        risks.append(f"大戶持倉方向在近期僅 {consistency}% 的時間維持一致，該訊號本身並不穩定。")

    macro = next((item for item in evidence if item.data_type == "macro"), None)
    if macro and (macro.content.get("fed_status") or "").startswith("unavailable"):
        risks.append("聯準會政策發布來源本次不可用，總體面僅涵蓋市場情緒指標，缺少利率政策事件。")

    risks.append("本 MVP 僅建立訊號的同時性關聯，不足以建立因果關係。")
    risks.append("社群與新聞來源的可靠度低於市場與鏈上資料，權重已相應調低。")
    return risks


def _observation_points(evidence: list, signals: list[dict]) -> list[str]:
    """後續觀察重點：每一條都要指向「接下來要看什麼、看到什麼算是改變判斷」。

    刻意不是把已知事實再列一次 —— 那對讀者沒有增量資訊。這裡產生的是前瞻性追蹤項目，
    同時也作為 LLM 路徑的替代來源（見 `_forward_looking_observations()`）。

    方向用的是與 `_offline_reasoning` 相同的原始權重比較（不是 `_market_stance()` 的
    門檻判定）：這裡只需要知道「淨訊號偏哪一側」來決定要追蹤什麼，不是在下立場結論。
    """
    bull_weight = sum(item["weight"] for item in signals if item["side"] == "bull")
    bear_weight = sum(item["weight"] for item in signals if item["side"] == "bear")
    if bull_weight > bear_weight:
        net_side = "bull"
    elif bear_weight > bull_weight:
        net_side = "bear"
    else:
        net_side = "neutral"
    opposing_side = "bear" if net_side == "bull" else "bull" if net_side == "bear" else None
    opposing = [item for item in signals if opposing_side and item["side"] == opposing_side]

    points = []
    if opposing:
        points.append(f"優先追蹤反向訊號是否強化：{opposing[0]['text']}（{opposing[0]['evidence_id']}）。")
    if net_side != "neutral":
        points.append(f"確認下一個 24 小時價格與成交量是否延續{'偏多' if net_side == 'bull' else '偏空'}方向，否則視為假突破。")
    macro_item = next((item for item in evidence if item.data_type == "macro"), None)
    if macro_item and macro_item.content.get("fear_greed_value") is not None:
        points.append(f"追蹤全市場恐懼貪婪指數是否脫離目前的 {macro_item.content.get('fear_greed_value')} 區間，總體風險偏好轉向會同步改變個別幣種的訊號解讀。")
    points.append("追蹤官方公告與鏈上活躍度是否與價格方向同步，若背離則優先相信鏈上與官方事件。")
    return points


# 觀察重點與某條 fact 的 2-gram 重疊比例達到這個門檻，就視為只是把該 fact 再說一次。
_OBSERVATION_RESTATEMENT_THRESHOLD = 0.90


def _normalise_for_overlap(text: str) -> str:
    """比對用的正規化：移除 Evidence ID、標點與空白，只留英數與中日韓文字。"""
    without_ids = re.sub(r"\bEV-[A-Z0-9-]+\b", "", str(text))
    return re.sub(r"[^0-9a-z\u3400-\u9fff]", "", without_ids.lower())


def _bigrams(text: str) -> set:
    return {text[index:index + 2] for index in range(len(text) - 1)}


def _restates_a_fact(point: str, facts: list[str]) -> bool:
    """這條觀察重點是不是只把某條 fact 再說一次。

    比 fact 更長的敘述一律不算複述 —— 多出來的字就是它補充的內容。只有「比 fact 短、
    而且幾乎全部字元都來自那條 fact」才會被判定為沒有增量資訊。
    """
    normalised = _normalise_for_overlap(point)
    if not normalised:
        return True
    grams = _bigrams(normalised)
    if not grams:
        return True
    for fact in facts:
        fact_normalised = _normalise_for_overlap(fact)
        if len(normalised) > len(fact_normalised):
            continue
        overlap = len(grams & _bigrams(fact_normalised)) / len(grams)
        if overlap >= _OBSERVATION_RESTATEMENT_THRESHOLD:
            return True
    return False


def _forward_looking_observations(reasoning: dict, fallback_points: list[str]) -> tuple[list[str], str]:
    """濾掉只是複述 fact 的觀察重點；全部被濾掉時改用 deterministic 版本。

    「後續觀察重點」的用途是告訴讀者接下來要看什麼。實測 nova-lite 會直接把 facts 的主語
    搬過來（「BTC's 14-day market return」對上 fact「BTC 14-day market return was -1.49%」），
    這種段落對讀者沒有任何增量資訊。過濾是 deterministic 的，不動模型的其他欄位。

    回傳 `(points, source)`；`source` 寫進 Execution Log，讓讀者知道這一段是模型寫的、
    被過濾過的，還是整段改用規則產生的。
    """
    facts = [str(item) for item in (reasoning.get("facts") or [])]
    points = [str(item).strip() for item in (reasoning.get("observation_points") or []) if str(item).strip()]
    kept = [point for point in points if not _restates_a_fact(point, facts)]
    if kept:
        return kept, "model" if len(kept) == len(points) else "model_filtered"
    return list(fallback_points), "deterministic_fallback"


def _offline_reasoning(result: dict, evidence: list, reason: str = "LLM disabled") -> dict:
    vegas = next((item for item in evidence if item.data_type == "vegas_channel"), None)
    tf_4h = vegas.content.get("4h") if vegas else None
    tf_1h = vegas.content.get("1h") if vegas else None
    alignment = vegas.content.get("alignment") if vegas else None

    if alignment and alignment.get("passed"):
        direction = "偏多" if alignment.get("4h_direction") == "long" else "偏空"
    else:
        direction = "大小時區訊號不一致"
    rsi_1h = tf_1h.get("rsi") if tf_1h else None
    rsi_context = (
        "RSI 顯示過熱風險" if rsi_1h is not None and rsi_1h > 80
        else "RSI 顯示超賣風險" if rsi_1h is not None and rsi_1h < 20
        else "RSI 位於中性區間" if rsi_1h is not None else "RSI 資料不可用"
    )
    volume_note = "1H 成交量出現爆量（超過均量兩倍）" if tf_1h and tf_1h.get("volume_spike") else "1H 成交量無異常爆量"

    facts = []
    for item in evidence:
        if item.data_type == "market":
            facts.append(f"{item.evidence_id}: 已取得市場價格資料（期間報酬率 {item.content.get('return_pct')}%）。")
        elif item.data_type == "news":
            titles = [entry.get("title", "") for entry in item.content.get("items", []) if entry.get("title")][:3]
            headline_text = "；".join(f"『{title}』" for title in titles) if titles else "無標題資料"
            facts.append(f"{item.evidence_id}: 近期新聞標題包含 {headline_text}。")
        elif item.data_type == "social":
            posts = item.content.get("posts", [])
            positive_words = sorted({word for post in posts for word in post.get("matched_positive", [])})
            negative_words = sorted({word for post in posts for word in post.get("matched_negative", [])})
            basis_parts = []
            if positive_words:
                basis_parts.append(f"正向關鍵字：{'、'.join(positive_words)}")
            if negative_words:
                basis_parts.append(f"負向關鍵字：{'、'.join(negative_words)}")
            basis_text = "；".join(basis_parts) if basis_parts else "未偵測到明顯正負向關鍵字"
            facts.append(f"{item.evidence_id}: 公開討論情緒為 {item.content.get('sentiment', 'unknown')}（依據：{basis_text}）。")
        elif item.data_type == "derivatives":
            bias_labels = {"long_crowded": "多方付費過熱（偏多擁擠）", "short_crowded": "空方付費過熱（偏空擁擠）", "balanced": "多空接近平衡", "unknown": "資料不可用"}
            funding_rate = item.content.get("funding_rate_pct")
            bias_text = bias_labels.get(item.content.get("bias"), "資料不可用")
            facts.append(f"{item.evidence_id}: 資金費率 {funding_rate}%，{bias_text}。" if funding_rate is not None else f"{item.evidence_id}: 資金費率資料不可用。")
        elif item.data_type == "whale":
            wallets = item.content.get("wallets") or []
            if wallets:
                summary = "、".join(f"{wallet['label']} 持有 {wallet['balance']:,}" for wallet in wallets)
                facts.append(f"{item.evidence_id}: 已知大戶錢包餘額（{summary}）。")
            else:
                facts.append(f"{item.evidence_id}: 此幣種巨鯨錢包資料尚未支援或暫時無法取得。")
        elif item.data_type == "vegas_channel":
            if tf_4h and tf_1h:
                signal_text = _SIGNAL_LABELS.get(tf_1h.get("last_signal"), "無明確訊號")
                alignment_text = alignment.get("note") if alignment else "方向一致性未知"
                facts.append(
                    f"{item.evidence_id}: Vegas 通道 4H 趨勢為{_TREND_LABELS.get(tf_4h.get('trend'), '不明')}，"
                    f"1H RSI(6) {rsi_1h}，{volume_note}，最近訊號為{signal_text}"
                    f"（{tf_1h.get('bars_since_signal')} 根K棒前），{alignment_text}。"
                )
            else:
                facts.append(f"{item.evidence_id}: Vegas 通道資料暫時無法取得。")
        elif item.data_type == "long_short_ratio":
            bias_labels = {"long_dominant": "多方持倉佔優", "short_dominant": "空方持倉佔優", "balanced": "多空接近平衡", "unknown": "資料不可用"}
            current = item.content.get("current")
            if current:
                facts.append(
                    f"{item.evidence_id}: 大戶多空比 {current.get('long_pct')}% 多 / {current.get('short_pct')}% 空"
                    f"（{bias_labels.get(item.content.get('bias'), '資料不可用')}），近期一致性 {item.content.get('consistency_pct')}%。"
                )
            else:
                facts.append(f"{item.evidence_id}: 大戶多空比資料暫時無法取得。")
        elif item.data_type == "macro":
            value = item.content.get("fear_greed_value")
            if value is None:
                facts.append(f"{item.evidence_id}: 總體情緒資料暫時無法取得。")
            else:
                fomc = item.content.get("latest_fomc_release") or {}
                fed_text = f"，聯準會最新貨幣政策發布為『{fomc.get('title')}』" if fomc.get("title") else "，聯準會政策發布本次不可用"
                facts.append(
                    f"{item.evidence_id}: 全市場恐懼貪婪指數 {value}（{item.content.get('fear_greed_classification')}），"
                    f"14 天變化 {item.content.get('fear_greed_change_14d'):+d}{fed_text}。"
                )
        elif item.data_type == "announcement":
            items = item.content.get("items") or []
            if items:
                channel = "第一方官方 feed" if item.content.get("first_party") else "官方網域限定的新聞聚合"
                titles = "；".join(f"『{entry.get('title', '')[:50]}』" for entry in items[:2])
                facts.append(f"{item.evidence_id}: 官方公告（{channel}）近期 {len(items)} 則，包含 {titles}。")
            else:
                facts.append(f"{item.evidence_id}: 官方公告來源暫時無法取得。")
        else:
            facts.append(f"{item.evidence_id}: 已取得 {item.data_type} 觀測值。")

    signals = _signal_inventory(result, evidence)
    bull_weight = sum(item["weight"] for item in signals if item["side"] == "bull")
    bear_weight = sum(item["weight"] for item in signals if item["side"] == "bear")
    directional = bull_weight + bear_weight
    if directional == 0:
        net_side, net_text = "neutral", "多空訊號皆不明確"
    elif bull_weight > bear_weight:
        net_side, net_text = "bull", f"整體訊號偏多（多方權重 {bull_weight:.1f} / 空方 {bear_weight:.1f}）"
    elif bear_weight > bull_weight:
        net_side, net_text = "bear", f"整體訊號偏空（空方權重 {bear_weight:.1f} / 多方 {bull_weight:.1f}）"
    else:
        net_side, net_text = "neutral", f"多空權重相等（各 {bull_weight:.1f}）"

    supporting = [item for item in signals if item["side"] == net_side and net_side != "neutral"]
    opposing_side = "bear" if net_side == "bull" else "bull" if net_side == "bear" else None
    opposing = [item for item in signals if opposing_side and item["side"] == opposing_side]
    unresolved = [item for item in signals if item["side"] == "neutral"]

    source_failures = sum(("fallback" in entry or "skipped" in entry) for entry in result.get("collection_log", []))
    agreement = (max(bull_weight, bear_weight) / directional) if directional else 0.0
    confidence = min(0.85, max(0.20, 0.30 + 0.35 * agreement - 0.06 * source_failures))

    judgment = (
        f"{result['coin']} {net_text}；技術面{direction}，{rsi_context}，{volume_note}。"
        f"支持該方向的獨立訊號 {len(supporting)} 項，反向訊號 {len(opposing)} 項。"
    )

    inferences = [f"{item['text']}，故此項支持{'偏多' if item['side'] == 'bull' else '偏空'}判讀（{item['evidence_id']}）。" for item in supporting[:4]]
    for item in unresolved[:2]:
        inferences.append(f"{item['text']}（{item['evidence_id']}），此項不提供方向性資訊，僅調整信心水準。")
    if not inferences:
        inferences.append("本次未取得足以形成方向性判讀的有效訊號，因此不提出方向推論。")
    if opposing:
        inferences.append(
            f"由於同時存在 {len(opposing)} 項反向訊號，上述方向僅為權重加總後的淨結果，"
            f"而非一致性結論；信心分數已因此下修至 {round(confidence, 2)}。"
        )

    counter_evidence = [f"反方訊號｜{item['text']}（{item['evidence_id']}）" for item in opposing]
    counter_evidence += [f"未定訊號｜{item['text']}（{item['evidence_id']}）" for item in unresolved[:3]]
    if not opposing and net_side != "neutral":
        counter_evidence.append(
            f"本次未偵測到明確的反向訊號，但訊號一致並不等於因果成立；"
            f"目前 {len(supporting)} 項支持訊號中，社群與新聞類來源的可靠度低於市場與衍生品資料，存在同源偏誤的可能。"
        )
    for entry in result.get("collection_log", []):
        if "fallback" in entry or "skipped" in entry:
            counter_evidence.append(f"資料品質｜{entry.split(':')[0]} 來源未取得即時資料（{entry}），該面向缺乏反證能力。")
    counter_evidence.append(f"推理模式｜{reason}，本結論由規則式訊號加總產生，未經 LLM 語意推理交叉檢驗。")

    observation_points = _observation_points(evidence, signals)

    return {
        "market_judgment": judgment,
        "confidence": round(confidence, 2),
        "facts": facts,
        "inferences": inferences,
        "conclusion": judgment,
        "counter_evidence": counter_evidence,
        "observation_points": observation_points,
        "cited_evidence_ids": result["evidence_ids"],
        "signal_inventory": signals,
    }


# Raw numeric series exist for the charts, not for the model: 337 individual closes tell it nothing
# the derived return/RSI/window stats do not, while costing a large share of the prompt. Textual
# lists (news items, social posts) are deliberately NOT stripped -- those carry the actual content.
_LLM_OMITTED_SERIES = ("prices", "volumes", "dates", "history", "fear_greed_history", "equity_curve")


def _llm_evidence_payload(evidence: list) -> list[dict]:
    """Evidence as sent to the model, with long numeric series replaced by a point count."""
    payload = []
    for item in evidence:
        record = asdict(item)
        content = dict(record.get("content") or {})
        for key in _LLM_OMITTED_SERIES:
            series = content.get(key)
            if isinstance(series, list) and len(series) > 12:
                content[key] = f"<{len(series)} points omitted from prompt; charted from evidence.json>"
        record["content"] = content
        record.pop("content_reference", None)  # duplicates content; doubles the cost of every record
        # 子項要留在 prompt 裡（模型必須能引用 source_item_id），但只留 locator 需要的欄位：
        # 完整子項與 content["items"] 幾乎重複一次，會讓每筆聚合證據的成本加倍。
        record["source_items"] = [
            {"source_item_id": entry.get("source_item_id"),
             "title": (entry.get("title") or entry.get("text") or "")[:160] or None,
             "url": entry.get("url"),
             "published_at": entry.get("published_at") or entry.get("created_at")
             or entry.get("indexed_at")}
            for entry in (record.get("source_items") or []) if isinstance(entry, dict)
        ]
        payload.append(record)
    return payload


def _price_history_evidence(coin: str, history_path: Path):
    """Multi-year daily closes as their own evidence record, separate from the 14-day live market.

    Kept additive rather than replacing the live market evidence: the short window stays the basis
    for the indicators every other source is aligned to, while this record answers the question a
    14-day chart cannot -- whether the current move is large or small by this asset's own standards.
    """
    from datetime import datetime, timezone

    from .day1_mvp import Evidence

    rows = load_ohlcv(history_path, coin, days=None)
    windows = price_windows(rows)
    dates, closes = downsample(rows)
    return Evidence(
        f"EV-HISTORY-{coin.upper()}-001", "Daily OHLCV history (5-year CSV)", str(history_path),
        datetime.now(timezone.utc).isoformat(), "price_history", coin.upper(),
        f"{rows[0]['date']} → {rows[-1]['date']}",
        {"windows": windows, "dates": dates, "prices": closes, "rows": len(rows),
         "date_start": rows[0]["date"], "date_end": rows[-1]["date"]},
        0.95,
        {"file": str(history_path), "rows": len(rows), "windows": sorted(windows)},
        f"{coin.upper()} multi-year price context: return, volatility, drawdown and range position",
        source_type="local_csv",
    )


def enrich_and_score_evidence(evidence: list, *, registry=None, now=None) -> list[dict]:
    """Score every record before anything reads it, and write the result back onto the evidence.

    This is the single point where `reliability_score` is decided. The adapters' hand-written
    numbers survive only as `legacy_reliability_hint` inside the breakdown: a source that is easy to
    reproduce and recent scores well because of what it is, not because whoever wrote the adapter
    felt good about it. Nothing here consults a model -- the arithmetic is in `src/credibility.py`
    and is a pure function of the metadata plus `now`.

    `now` is resolved once for the whole batch so every record's freshness is measured against the
    same instant; tests pass it in to get a fixed answer.
    """
    scoring_now = now or datetime.now(timezone.utc)
    active_registry = registry if registry is not None else load_source_registry()
    for item in evidence:
        stamp_credibility_metadata(item)
    records = score_evidence_batch(evidence, active_registry, now=scoring_now)
    for item, record in zip(evidence, records):
        item.source_lineage_id = record["source_lineage_id"]
        item.independence_factor = record["independence_factor"]
        item.verification_status = record["verification_status"]
        item.score_limiters = list(record["score_limiters"])
        item.scoring_version = record["scoring_version"]
        item.score_breakdown = {
            "raw_score": record["raw_score"],
            "final_score": record["final_score"],
            "components": dict(record["components"]),
            "weights": dict(CREDIBILITY_WEIGHTS),
            "hard_cap": record["hard_cap"],
            "score_limiters": list(record["score_limiters"]),
            "freshness_basis": record["freshness_basis"],
            "age_days": record["age_days"],
            "lineage_size": record["lineage_size"],
            "source_type": record["source_type"],
            "known_limitations": list(record["known_limitations"]),
            "notes": list(record["notes"]),
            # Kept for audit: what the adapter claimed before the engine recomputed it.
            "legacy_reliability_hint": item.reliability_score,
            "registry_version": active_registry.get("registry_version"),
            "scoring_version": record["scoring_version"],
            "scored_at": scoring_now.isoformat(),
        }
        item.reliability_score = record["final_score"]
    return records


# --------------------------------------------------------------------------------------
# E2 — 相對「這一題」的證據語意評估
#
# credibility（T3）回答「這個來源多可信」，那是來源屬性。它**不**回答「這筆證據對使用者問的
# 那個問題有多相關」—— 一則可信度 0.9 的總經新聞對「ETH 下行風險」可能完全不相關，而一筆
# 可信度 0.45 的社群貼文可能直接命中。兩者混用時，報告會用來源品質冒充問題相關性。
#
# 三條邊界：
#   1. 標籤（離散）可以由模型在**同一次**分析回應裡順便給出，因此不多花一次呼叫；
#      模型不得輸出任何數值。
#   2. 分數與 `effective_weight` 一律由 `src/schemas.py` 的固定映射與公式算出。
#   3. 模型提案只要有一處不合法就**整批**作廢，改走下面的 deterministic fallback ——
#      部分採用會讓報告出現「有些證據評估過、有些沒有」的混合狀態，而讀者分不出來。
#
# 立場（`_market_stance()` 的固定權重）與 Claim confidence 是兩條不同的訊號層。E2 只讓
# Claim／Evidence 層的問題相關性可稽核，刻意不動 stance 的權重公式。
# --------------------------------------------------------------------------------------

ASSESSMENT_PATH_LLM = "llm"
ASSESSMENT_PATH_FALLBACK = "deterministic_fallback"

# deterministic fallback 對各 data_type 的相關性標籤。
#
# 只有「結構化的市場／技術／衍生品資料」敢說 direct：它們是可計算的觀測，直接構成市場狀況。
# 鏈上與 TVL 是 indirect（它們描述基本面，不直接定價）。macro 明確標示為全市場範圍、非個別
# 幣種，因此是 context。
#
# news／announcement 一律 unclear：**沒有模型時本程式讀不懂新聞在講什麼**。給它 direct 等於
# 假裝知道；給它 irrelevant 等於假裝確定不相關。unclear（0.20）是唯一誠實的答案。
_FALLBACK_RELEVANCE_BY_DATA_TYPE = {
    "market": RELEVANCE_LABEL_DIRECT,
    "price_history": RELEVANCE_LABEL_DIRECT,
    "ohlcv": RELEVANCE_LABEL_DIRECT,
    "vegas_channel": RELEVANCE_LABEL_DIRECT,
    "derivatives": RELEVANCE_LABEL_DIRECT,
    "long_short_ratio": RELEVANCE_LABEL_DIRECT,
    "onchain": RELEVANCE_LABEL_INDIRECT,
    "whale": RELEVANCE_LABEL_INDIRECT,
    "tvl": RELEVANCE_LABEL_INDIRECT,
    "macro": RELEVANCE_LABEL_CONTEXT,
    "news": RELEVANCE_LABEL_UNCLEAR,
    "announcement": RELEVANCE_LABEL_UNCLEAR,
}

# 訊號盤點的方向 → impact_direction。同一筆證據同時有多空訊號時是 mixed。
_SIDE_TO_IMPACT = {"bull": "bullish", "bear": "bearish", "neutral": "neutral"}

# 各 data_type 的影響期間。依既有 collector 的觀測窗口設定，不由模型決定。
_FALLBACK_HORIZON_BY_DATA_TYPE = {
    "market": "short_term",
    "vegas_channel": "immediate",
    "derivatives": "immediate",
    "long_short_ratio": "immediate",
    "social": "immediate",
    "social_bluesky": "immediate",
    "social_hackernews": "immediate",
    "price_history": "long_term",
    "onchain": "medium_term",
    "whale": "medium_term",
    "tvl": "medium_term",
    "macro": "medium_term",
}


def _assessment_record(item, *, label: str, relationship: str, impact: str, horizon: str,
                       rationale: str, source_item_ids, path: str, excluded_reason: str = "") -> dict:
    """組出一筆 `semantic_assessment`。分數與權重在這裡算，呼叫端不得自帶數字。"""
    score = relevance_score_for(label)
    return {
        "relevance_label": label,
        "relevance_score": score,
        "relationship_to_question": relationship,
        "impact_direction": impact,
        "impact_horizon": horizon,
        "rationale": rationale[:MAX_ASSESSMENT_RATIONALE_CHARS],
        "source_item_ids": list(source_item_ids)[:MAX_ASSESSMENT_SOURCE_ITEM_IDS],
        "assessment_source": (ASSESSMENT_SOURCE_LLM if path == ASSESSMENT_PATH_LLM
                              else ASSESSMENT_SOURCE_FALLBACK),
        "effective_weight": effective_weight(item.reliability_score, score,
                                             item.independence_factor),
        "excluded_reason": excluded_reason if label == RELEVANCE_LABEL_IRRELEVANT else "",
        "assessment_version": ASSESSMENT_VERSION,
    }


def _citable_source_item_ids(item) -> list[str]:
    """挑出最多三個帶 http(s) locator 的子項 ID，供 deterministic 路徑引用。

    優先取有可解析 URL 的子項：Citation Gate 要能從評估回答「引用的是哪一則」，而沒有 URL
    的子項回答不了。順序沿用 `source_items` 的既有順序，因此同一份輸入永遠得到同一組 ID。
    """
    with_url, without_url = [], []
    for entry in item.source_items or []:
        if not isinstance(entry, dict):
            continue
        item_id = str(entry.get("source_item_id") or "")
        if not item_id:
            continue
        url = str(entry.get("url") or "").lower()
        (with_url if url.startswith(("http://", "https://")) else without_url).append(item_id)
    return (with_url or without_url)[:MAX_ASSESSMENT_SOURCE_ITEM_IDS]


def _window_start(plan_dict: dict, now: datetime):
    """plan 時間窗的起點。取不到天數就回 ``None``，代表「不判斷是否超出區間」。"""
    window = plan_dict.get("time_window") if isinstance(plan_dict, dict) else None
    try:
        days = int((window or {}).get("days"))
    except (TypeError, ValueError):
        return None
    return now - timedelta(days=days) if days > 0 else None


def _evidence_own_time(item):
    """證據自己的時間（事件時間優先於發布時間）。抓取時間**不算**，那是我們的時間。"""
    for value in (item.event_time, item.published_at):
        parsed = parse_timestamp(value)
        if parsed is not None:
            return parsed
    return None


def _deterministic_assessment(item, *, coin: str, sides: set, plan_dict: dict, now: datetime) -> dict:
    """不靠模型的評估。看不懂就說看不懂，絕不假裝知道方向。"""
    item_ids = _citable_source_item_ids(item)

    if coin and str(item.coin or "").upper() != coin.upper():
        return _assessment_record(
            item, label=RELEVANCE_LABEL_IRRELEVANT, relationship=QUESTION_RELATIONSHIP_IRRELEVANT,
            impact=IMPACT_DIRECTION_NOT_APPLICABLE, horizon=IMPACT_HORIZON_UNCLEAR,
            rationale="證據標的為 %s，與本次研究標的 %s 不符。" % (item.coin, coin.upper()),
            source_item_ids=item_ids, path=ASSESSMENT_PATH_FALLBACK,
            excluded_reason="標的不符：%s ≠ %s" % (item.coin, coin.upper()))

    horizon = _FALLBACK_HORIZON_BY_DATA_TYPE.get(item.data_type, IMPACT_HORIZON_UNCLEAR)

    # 降級來源沒有實際觀測內容，所以「它對題目多相關」本身就是未知，不是不相關。
    #
    # 這一項刻意排在時間窗檢查**之前**：fixture 裡的日期是佔位值，不是觀測時間。拿佔位日期
    # 去比對研究區間，等於把一個沒有意義的數字讀成「這筆資料太舊」。
    if item.source_type == SOURCE_TYPE_FALLBACK_FIXTURE:
        return _assessment_record(
            item, label=RELEVANCE_LABEL_UNCLEAR, relationship=QUESTION_RELATIONSHIP_UNCLEAR,
            impact=IMPACT_DIRECTION_UNCLEAR, horizon=IMPACT_HORIZON_UNCLEAR,
            rationale="本筆為降級／離線 fixture，沒有實際觀測內容可判斷與題目的關係。",
            source_item_ids=item_ids, path=ASSESSMENT_PATH_FALLBACK)

    start = _window_start(plan_dict, now)
    own_time = _evidence_own_time(item)
    if start is not None and own_time is not None and own_time < start:
        return _assessment_record(
            item, label=RELEVANCE_LABEL_IRRELEVANT, relationship=QUESTION_RELATIONSHIP_IRRELEVANT,
            impact=IMPACT_DIRECTION_NOT_APPLICABLE, horizon=IMPACT_HORIZON_UNCLEAR,
            rationale="證據時間 %s 早於研究區間起點 %s。" % (own_time.date(), start.date()),
            source_item_ids=item_ids, path=ASSESSMENT_PATH_FALLBACK,
            excluded_reason="超出 plan 時間窗：%s < %s" % (own_time.date(), start.date()))

    if item.data_type in SOCIAL_DATA_TYPES:
        content = item.content if isinstance(item.content, dict) else {}
        sentiment = content.get("sentiment")
        if content.get("post_count") and sentiment in ("positive", "negative"):
            return _assessment_record(
                item, label=RELEVANCE_LABEL_INDIRECT,
                relationship=QUESTION_RELATIONSHIP_CONTEXT,
                impact="bullish" if sentiment == "positive" else "bearish", horizon=horizon,
                rationale="%d 則提及本幣的貼文，固定詞表判為%s，屬間接情緒指標。"
                          % (content.get("post_count"), "偏正向" if sentiment == "positive" else "偏負向"),
                source_item_ids=item_ids, path=ASSESSMENT_PATH_FALLBACK)
        return _assessment_record(
            item, label=RELEVANCE_LABEL_UNCLEAR, relationship=QUESTION_RELATIONSHIP_UNCLEAR,
            impact=IMPACT_DIRECTION_UNCLEAR, horizon=horizon,
            rationale="社群樣本不足或正負向詞數相同，無法判斷與題目的關係。",
            source_item_ids=item_ids, path=ASSESSMENT_PATH_FALLBACK)

    label = _FALLBACK_RELEVANCE_BY_DATA_TYPE.get(item.data_type, RELEVANCE_LABEL_UNCLEAR)
    if label == RELEVANCE_LABEL_UNCLEAR:
        return _assessment_record(
            item, label=label, relationship=QUESTION_RELATIONSHIP_UNCLEAR,
            impact=IMPACT_DIRECTION_UNCLEAR, horizon=horizon,
            rationale="離線規則無法判讀此來源的文字語意，因此不宣稱方向，標為待判定。",
            source_item_ids=item_ids, path=ASSESSMENT_PATH_FALLBACK)

    # 結構化資料：方向沿用既有訊號盤點的分側，不新增第二套判斷邏輯。
    directional = sides & {"bull", "bear"}
    if len(directional) > 1:
        impact = "mixed"
    elif directional:
        impact = _SIDE_TO_IMPACT[next(iter(directional))]
    elif sides:
        impact = "neutral"
    else:
        impact = IMPACT_DIRECTION_UNCLEAR
    return _assessment_record(
        item, label=label,
        # relationship 只有讀懂題目才能說 support／contradict。離線規則做不到，因此一律 context：
        # 這筆資料為題目提供依據，但它站哪一邊要由能讀題的那一層來說。
        relationship=QUESTION_RELATIONSHIP_CONTEXT,
        impact=impact, horizon=horizon,
        rationale="結構化%s資料，方向沿用本次訊號盤點（%s）。"
                  % (item.data_type, impact),
        source_item_ids=item_ids, path=ASSESSMENT_PATH_FALLBACK)


def _model_assessment(item, proposal: dict) -> dict:
    """把驗證過的模型提案轉成最終評估。標籤照抄，數值一律重算。"""
    label = str(proposal.get("relevance_label"))
    rationale = str(proposal.get("rationale") or "").strip() or "模型未說明理由。"
    excluded = ""
    if label == RELEVANCE_LABEL_IRRELEVANT:
        excluded = "模型評估與研究問題無關：%s" % rationale
    # 模型沒指出子項時用 deterministic 選擇補上，而不是留空。
    #
    # 留空會讓聚合證據被引用時失去 item-level locator，Citation Gate 因此擋下整份報告 ——
    # 而模型漏填一個欄位不該讓正式執行沒有產出。子項的**選擇**一向是機械的（照既有順序取前
    # 幾個有 locator 的），補上它不等於替模型編造理由：標籤與 rationale 仍然完全是模型的。
    item_ids = [str(value) for value in (proposal.get("source_item_ids") or [])]
    return _assessment_record(
        item, label=label,
        relationship=str(proposal.get("relationship_to_question")),
        impact=str(proposal.get("impact_direction")),
        horizon=str(proposal.get("impact_horizon")),
        rationale=rationale,
        source_item_ids=item_ids or _citable_source_item_ids(item),
        path=ASSESSMENT_PATH_LLM,
        excluded_reason=excluded[:MAX_ASSESSMENT_RATIONALE_CHARS + 40])


def assess_evidence_relevance(evidence: list, *, coin: str, signals: list[dict],
                              plan_dict: dict, proposals=None, now=None) -> dict:
    """把每筆證據相對本次問題的評估寫回 Evidence，並回報這一步怎麼做的。

    回傳的 summary 直接進 Execution Log：走哪條路、為什麼降級、評了幾筆、哪些被判定不相關。
    **不含新聞全文**，只有 ID、標籤與計數。

    `claim_relevance` 在這裡被填上。在 E2 之前這個欄位在正式流程中沒有任何寫入者，永遠是
    預設的 0.0，而 `claim_graph._relevance()` 又把 0.0 當成缺值換成 0.50 —— 於是 Claim 信心
    裡的「相關性」其實是一個常數。
    """
    scoring_now = parse_timestamp(now) or datetime.now(timezone.utc)
    sides_by_id: dict[str, set] = {}
    for signal in signals or []:
        sides_by_id.setdefault(str(signal.get("evidence_id") or ""), set()).add(
            str(signal.get("side") or "neutral"))

    path, fallback_reason = ASSESSMENT_PATH_FALLBACK, ""
    accepted: dict[str, dict] = {}
    if proposals:
        errors = validate_evidence_assessments(proposals, evidence)
        if errors:
            # 全批作廢。部分採用會讓「已評估」與「未評估」混在同一份報告裡，而讀者無從分辨。
            fallback_reason = "model assessment rejected: " + "; ".join(errors[:3])
        else:
            path = ASSESSMENT_PATH_LLM
            accepted = {str(entry.get("evidence_id")): entry for entry in proposals}
    else:
        fallback_reason = "analysis response carried no evidence assessment"

    for item in evidence:
        proposal = accepted.get(item.evidence_id)
        if proposal is not None:
            item.semantic_assessment = _model_assessment(item, proposal)
        else:
            item.semantic_assessment = _deterministic_assessment(
                item, coin=coin, sides=sides_by_id.get(item.evidence_id, set()),
                plan_dict=plan_dict, now=scoring_now)
        # claim_graph 讀這個欄位算 strength；標籤與數值必須一致，否則兩處會各說一套。
        item.claim_relevance = item.semantic_assessment["relevance_score"]

    excluded = [item.evidence_id for item in evidence
                if item.semantic_assessment["relevance_label"] == RELEVANCE_LABEL_IRRELEVANT]
    label_counts: dict[str, int] = {}
    for item in evidence:
        label = item.semantic_assessment["relevance_label"]
        label_counts[label] = label_counts.get(label, 0) + 1
    weights = {item.evidence_id: item.semantic_assessment["effective_weight"] for item in evidence}
    return {
        "path": path,
        "provider": ASSESSMENT_SOURCE_LLM if path == ASSESSMENT_PATH_LLM else "deterministic",
        "fallback_reason": fallback_reason,
        "assessed_count": len(evidence),
        "assessment_version": ASSESSMENT_VERSION,
        "relevance_label_counts": dict(sorted(label_counts.items())),
        "excluded_evidence_ids": excluded,
        "excluded_reasons": {item.evidence_id: item.semantic_assessment["excluded_reason"]
                             for item in evidence if item.evidence_id in excluded},
        "effective_weights": dict(sorted(weights.items())),
        "relevance_label_scores": dict(RELEVANCE_LABEL_SCORES),
        "effective_weight_formula": "reliability_score × relevance_score × independence_factor",
    }


def _assessment_step(summary: dict, timing: dict) -> dict:
    """Execution Log 的 `assess_evidence` 條目。"""
    return {
        "name": "assess_evidence",
        "status": "success" if summary["path"] == ASSESSMENT_PATH_LLM else "fallback",
        # 標籤可以來自模型，分數永遠不會。這兩行是這一步最重要的紀錄。
        "labelled_by": "llm" if summary["path"] == ASSESSMENT_PATH_LLM else "deterministic_rules",
        "scored_by": "deterministic_rules",
        "path": summary["path"],
        "provider": summary["provider"],
        "fallback_reason": summary["fallback_reason"],
        "assessed_count": summary["assessed_count"],
        "relevance_label_counts": summary["relevance_label_counts"],
        "excluded_evidence_ids": summary["excluded_evidence_ids"],
        "excluded_reasons": summary["excluded_reasons"],
        "effective_weights": summary["effective_weights"],
        "effective_weight_formula": summary["effective_weight_formula"],
        "relevance_label_scores": summary["relevance_label_scores"],
        "assessment_version": summary["assessment_version"],
        # 「這一步沒有多打模型」是可稽核的事實：評估搭既有分析回應的便車。
        "extra_llm_calls": 0,
        **timing,
    }


def _credibility_risk_factors(credibility: dict) -> list[str]:
    """Turn the scoring outcome into limitations the reader can act on.

    An offline or heavily degraded run must say so in the report itself. A reader who only sees the
    conclusion has no way to know the evidence behind it was a fixture, and a stance built on
    fixtures reads exactly like one built on observations unless we print the difference.
    """
    factors = []
    fallback_count = len(credibility["fallback_ids"])
    if credibility["substantive_count"] == 0:
        factors.append(
            f"本次全部 {credibility['evidence_count']} 筆證據皆為離線 fixture 或降級來源"
            f"（可信度上限 {HARD_CAPS['fallback_fixture']}），只足以示範流程，不足以支撐方向性結論。"
        )
    elif fallback_count:
        factors.append(
            f"{fallback_count} 筆證據為降級來源（{', '.join(credibility['fallback_ids'])}），"
            f"可信度已壓到 {HARD_CAPS['fallback_fixture']} 以下，不得作為結論的唯一依據。"
        )
    if credibility["shared_lineages"]:
        groups = "；".join(", ".join(ids) for ids in credibility["shared_lineages"].values())
        factors.append(f"有證據來自同一來源鏈（{groups}），獨立性已稀釋，不視為多個獨立確認。")
    other_caps = {name: count for name, count in credibility["limiter_counts"].items()
                  if name != "fallback_fixture"}
    if other_caps:
        detail = "、".join(f"{name}×{count}" for name, count in sorted(other_caps.items()))
        factors.append(f"以下可信度上限已生效，相關敘述不得超過證據能證明的範圍：{detail}。")
    return factors


def _credibility_summary(records: list[dict], registry_version: str) -> dict:
    """The Execution Log / UI view of scoring: what was capped, what was rejected, what is left."""
    lineages: dict[str, list[str]] = {}
    for record in records:
        lineages.setdefault(record["source_lineage_id"], []).append(record["evidence_id"])
    substantive = [
        record for record in records
        if not record["rejected"] and record["source_type"] != "fallback_fixture"
    ]
    return {
        "scoring_version": records[0]["scoring_version"] if records else SCORING_VERSION,
        "registry_version": registry_version,
        "evidence_count": len(records),
        "substantive_count": len(substantive),
        "fallback_ids": [record["evidence_id"] for record in records
                         if record["source_type"] == "fallback_fixture"],
        "rejected_ids": [record["evidence_id"] for record in records if record["rejected"]],
        "capped_ids": [record["evidence_id"] for record in records if record["score_limiters"]],
        "limiter_counts": {
            name: sum(1 for record in records if name in record["score_limiters"])
            for name in sorted({name for record in records for name in record["score_limiters"]})
        },
        "source_type_counts": {
            name: sum(1 for record in records if record["source_type"] == name)
            for name in sorted({record["source_type"] for record in records})
        },
        "independent_lineage_count": len(lineages),
        "shared_lineages": {key: value for key, value in lineages.items() if len(value) > 1},
        "mean_final_score": round(sum(record["final_score"] for record in records) / len(records), 4) if records else 0.0,
    }


def _plan_research(question: str, coins: list[str], *, use_llm: bool, client: object | None,
                   deadline: float):
    """Turn the question into a `ResearchPlan` before any evidence is collected.

    The planner decides *what to look into*; it never produces a market conclusion and it is never
    allowed to stop the run. Model failure, malformed JSON or an unknown task mode all degrade to the
    deterministic keyword plan inside `src/planner.py`, so this call always returns a usable plan.
    """
    remaining = deadline - time.monotonic()
    if remaining < _PLANNER_MIN_SECONDS:
        # Same watchdog rule as the analyst phase: a call started with seconds left burns budget and
        # still fails, and the deterministic plan costs nothing.
        return build_research_plan(question, coins, client=None)
    if client is None and use_llm and llm_is_configured():
        client = default_llm_client()
    return build_research_plan(question, coins, client=client,
                               timeout_seconds=min(PLANNER_TIMEOUT_SECONDS, remaining))


def _planner_step(plan, planning_log: dict, llm_info: dict) -> dict:
    """The Execution Log entry for planning: which path ran, how long it took, what it assumed."""
    used_model = planning_log["path"] == PLANNING_PATH_LLM
    return {
        "name": "plan_research",
        "status": "fallback" if planning_log["fallback_used"] else "success",
        "path": planning_log["path"],
        # Only claim a provider when a model actually produced the plan; the deterministic path is
        # reported as such rather than borrowing the configured provider's name.
        "provider": llm_info["provider"] if used_model else "deterministic",
        "model": llm_info["model"] if used_model else None,
        "region": llm_info["region"] if used_model else None,
        "duration_ms": round(planning_log["duration_seconds"] * 1000, 1),
        "fallback_used": planning_log["fallback_used"],
        "fallback_reason": planning_log["fallback_reason"],
        "task_modes": list(plan.task_modes),
        "time_window": dict(plan.time_window),
        "time_window_assumptions": list(plan.assumptions),
        "hypothesis_count": len(plan.hypotheses),
        "required_domains": list(plan.required_domains),
    }


class _PreparedClaimsClient:
    """A one-shot `LLMClient` that replays Claim proposals already returned by the analysis call.

    The analysis response may carry a `claims` field (quarantined as `claim_proposals` by
    `src/llm.py`). Those proposals still have to clear exactly the same gate as a dedicated claim
    call -- every evidence ID must exist, facts must not smuggle in inference language, and the
    model's own confidence and verdict are discarded -- so instead of trusting them directly they
    are fed back through `src/claim_graph.py`'s validator via this adapter.

    The point is quota, not cleverness: a full analysis already costs three model calls, and asking
    the same model to restate its claims would make it four.
    """

    def __init__(self, proposals: list) -> None:
        self.proposals = list(proposals)
        self.calls = 0

    def generate_json(self, *, prompt: str, schema: dict, schema_name: str, timeout_seconds: float) -> dict:
        self.calls += 1
        return {"claims": self.proposals}


def _claim_client(reasoning: dict, *, use_llm: bool, client: object | None, deadline: float,
                  finalizing: bool = False):
    """Decide who proposes the Claim text: the analysis response, a fresh model call, or nobody.

    Returning `None` is a normal outcome, not an error: `build_claim_graph()` then builds
    conservative claims from the signal inventory that is already on the table.
    """
    if client is not None:
        return client, "injected_client"
    proposals = reasoning.get(CLAIM_PROPOSAL_RESULT_FIELD) or []
    if proposals:
        # Replaying proposals the analysis already returned costs no extra call, so it stays
        # available even inside the finalization window.
        return _PreparedClaimsClient(proposals), "analysis_response_claims"
    if finalizing:
        # No new model call once the finalization window opens: claims are built from the evidence
        # already on the table so the six artifacts still get written.
        return None, _FINALIZATION_REASON
    if not use_llm:
        return None, "llm_disabled"
    if not llm_is_configured():
        return None, "no_provider_configured"
    remaining = deadline - time.monotonic()
    if remaining < _CLAIM_MIN_SECONDS:
        return None, f"time_budget_remaining_{round(remaining, 1)}s"
    return default_llm_client(), "llm_client"


def _build_claims(result: dict, evidence: list, plan_dict: dict, signals: list[dict],
                  critique: dict | None, *, client: object | None, deadline: float) -> dict:
    """Build the Claim graph, and never let a model-authored graph through unvalidated.

    A graph that fails `validate_claims` is not repaired in place -- the whole batch is discarded and
    rebuilt deterministically, because a partially trusted graph is worse than an honest conservative
    one. If the deterministic graph also fails, that is a bug in this pipeline and the run stops.
    """
    timeout = max(1.0, min(CLAIM_TIMEOUT_SECONDS, deadline - time.monotonic()))

    def build(active_client):
        return build_claim_graph(
            result["coin"], result["question"], evidence,
            signals=signals, stance=result["stance"], plan=plan_dict,
            client=active_client, critique=critique, timeout_seconds=timeout,
        )

    graph = build(client)
    errors = validate_claims(graph["claims"], evidence)
    if errors and graph["source"] == GRAPH_SOURCE_LLM:
        graph = build(None)
        graph["fallback_reason"] = "claim validation failed: " + "; ".join(errors)
        errors = validate_claims(graph["claims"], evidence)
    if errors:
        raise ValueError("Claim validation failed: " + "; ".join(errors))
    return graph


def _claims_step(graph: dict, client_reason: str, llm_info: dict, duration_ms: float) -> dict:
    """The Execution Log entry for claim building: who proposed, who scored, what got capped."""
    used_model = graph["source"] == GRAPH_SOURCE_LLM
    claims = graph["claims"]
    return {
        "name": "build_claims",
        "status": "success" if used_model else "fallback",
        "claim_source": graph["source"],
        # Only the wording can come from a model; the verdict and the score never do.
        "proposed_by": "llm" if used_model else "deterministic_rules",
        "scored_by": "deterministic_rules",
        "provider": llm_info["provider"] if used_model else "deterministic",
        "model": llm_info["model"] if used_model else None,
        "client_selection": client_reason,
        "duration_ms": duration_ms,
        "fallback_reason": graph["fallback_reason"],
        "claim_count": len(claims),
        "verdicts": {claim["claim_id"]: claim["verdict"] for claim in claims},
        "confidence_scores": {claim["claim_id"]: claim["confidence"]["score"] for claim in claims},
        "confidence_limiters": {claim["claim_id"]: claim["confidence"]["limiters"]
                                for claim in claims if claim["confidence"]["limiters"]},
        "insufficient_evidence_claims": [claim["claim_id"] for claim in claims
                                         if claim["verdict"] == VERDICT_INSUFFICIENT_EVIDENCE],
        "hypothesis_assessments": graph["hypothesis_assessments"],
        "evidence_with_claim_links": len(graph["related_claim_ids"]),
        "rejected_evidence_ids": graph["rejected_evidence_ids"],
        "confidence_weights": dict(CONFIDENCE_WEIGHTS),
        "scoring_version": graph["scoring_version"],
    }


def _claims_markdown(graph: dict) -> str:
    """`## Claims` 段。實作在 `src/report_renderer.py`，這裡只保留既有呼叫點的名稱。

    報告的 markdown 結構自 T5 起一律由 deterministic renderer 決定，Orchestrator 不再自己拼字串。
    """
    return render_claims_section(graph, CONFIDENCE_WEIGHTS)


# --------------------------------------------------------------------------------------
# T5 — run 歸屬、階段時間軸、跨來源一致程度、Citation Gate、manifest
# --------------------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class _StageTimeline:
    """每個階段的 `started_at`／`completed_at`／`duration_ms`。

    Execution Log 的用途是讓別人能重建流程，所以每一步都要有真實的牆鐘時間，而不是只有一個
    總耗時。牆鐘時間用 `datetime` 取（可讀、可對照外部日誌），耗時用 `time.monotonic()` 量
    （不受系統時間調整影響）。
    """

    def __init__(self) -> None:
        self._stages: dict[str, dict] = {}
        self._order: list[str] = []

    def start(self, name: str) -> None:
        if name not in self._stages:
            self._order.append(name)
        self._stages[name] = {"started_at": _now_iso(), "_monotonic": time.monotonic()}

    def finish(self, name: str) -> dict:
        stage = self._stages.setdefault(name, {"started_at": _now_iso(), "_monotonic": time.monotonic()})
        if name not in self._order:
            self._order.append(name)
        stage["completed_at"] = _now_iso()
        stage["duration_ms"] = round((time.monotonic() - stage["_monotonic"]) * 1000, 1)
        return self.entry(name)

    def entry(self, name: str) -> dict:
        stage = self._stages.get(name)
        if not stage:
            return {"started_at": None, "completed_at": None, "duration_ms": None}
        return {"started_at": stage["started_at"],
                "completed_at": stage.get("completed_at"),
                "duration_ms": stage.get("duration_ms")}

    def mark(self, name: str) -> dict:
        """開始並立刻結束一個瞬時階段（例如純粹的輸入解析）。"""
        self.start(name)
        return self.finish(name)


def stamp_run_id(evidence: list, run_id: str) -> list:
    """把本次 run 的 ID 蓋在每一筆證據上。

    這是「Evidence 屬於本次 run」能被檢查的前提：沒有這個欄位，一筆從別次執行、快取或舊 fixture
    流進來的紀錄看起來會完全正常，而 citation gate 只能假設它是本次的。
    """
    for item in evidence or []:
        if hasattr(item, "run_id"):
            item.run_id = run_id
        elif isinstance(item, dict):
            item["run_id"] = run_id
    return evidence


def _evidence_window(evidence: list) -> dict:
    """證據取得時間的頭尾。報告要能回答「資料截止在哪」，不能只講執行時間。"""
    stamps = sorted(str(getattr(item, "fetched_at", "") or "") for item in evidence or [])
    stamps = [stamp for stamp in stamps if stamp]
    return {"earliest_fetched_at": stamps[0] if stamps else None,
            "latest_fetched_at": stamps[-1] if stamps else None}


_CONSISTENCY_LABELS = {
    "consistent": "一致", "partially_consistent": "部分一致",
    "conflicting": "矛盾", "insufficient": "資料不足",
}


def _source_consistency(signals: list[dict], evidence: list) -> dict:
    """跨來源一致程度：同一個方向由幾個**領域**支持，而不是由幾筆證據支持。

    這個區分是刻意的：同一則消息被五家轉載仍然只是一個領域的一次確認，而「價格、衍生品與鏈上
    同時指向同一方向」才是真正的跨來源一致。領域取自 `claim_graph.DOMAIN_BY_DATA_TYPE`，
    與 Claim 的 domain coverage 用同一套定義，避免報告兩處對「領域」的說法不一致。
    """
    domain_by_evidence = {
        str(getattr(item, "evidence_id", "")): DOMAIN_BY_DATA_TYPE.get(
            str(getattr(item, "data_type", "")), "other")
        for item in evidence or []
    }
    weights: dict[str, dict[str, float]] = {}
    for signal in signals or []:
        domain = domain_by_evidence.get(str(signal.get("evidence_id")), "other")
        bucket = weights.setdefault(domain, {"bull": 0.0, "bear": 0.0, "neutral": 0.0})
        bucket[signal.get("side", "neutral")] = bucket.get(signal.get("side", "neutral"), 0.0) + float(
            signal.get("weight") or 0.0)

    domain_sides, conflicting = {}, []
    for domain, bucket in weights.items():
        if bucket["bull"] > bucket["bear"]:
            side = "bull"
        elif bucket["bear"] > bucket["bull"]:
            side = "bear"
        else:
            side = "neutral"
        domain_sides[domain] = {
            "bull": "偏多", "bear": "偏空", "neutral": "無方向",
        }[side] + f"（多 {round(bucket['bull'], 2)} / 空 {round(bucket['bear'], 2)}）"
        if bucket["bull"] > 0 and bucket["bear"] > 0:
            conflicting.append(domain)
        weights[domain]["side"] = side

    directional = [domain for domain, bucket in weights.items() if bucket["side"] in ("bull", "bear")]
    if not directional:
        label, agreement = "insufficient", 0.0
        basis = "沒有任何領域產生方向性訊號，無法評估跨來源一致程度。"
    else:
        bull_domains = [d for d in directional if weights[d]["side"] == "bull"]
        bear_domains = [d for d in directional if weights[d]["side"] == "bear"]
        agreement = round(max(len(bull_domains), len(bear_domains)) / len(directional) * 100, 1)
        if len(directional) == 1:
            label = "insufficient"
            basis = f"只有 {directional[0]} 一個領域產生方向性訊號，不構成跨來源確認。"
        elif agreement >= 100.0:
            label = "consistent"
            basis = f"{len(directional)} 個領域全部指向同一方向。"
        elif agreement >= 60.0:
            label = "partially_consistent"
            basis = (f"{len(directional)} 個領域中有 {max(len(bull_domains), len(bear_domains))} 個"
                     f"指向同一方向，其餘相反。")
        else:
            label = "conflicting"
            basis = (f"偏多領域 {len(bull_domains)} 個、偏空領域 {len(bear_domains)} 個，"
                     "跨來源訊號互相矛盾。")

    return {
        "label": _CONSISTENCY_LABELS[label],
        "state": label,
        "agreement_pct": agreement,
        "domain_count": len(weights),
        "directional_domain_count": len(directional),
        "domain_sides": domain_sides,
        "conflicting_domains": sorted(conflicting),
        "basis": basis,
    }


def _link_claims_to_evidence(evidence: list, graph: dict) -> int:
    """把 Claim ID 寫回 Evidence，並先清掉舊值。

    清空是必要的：Claim 圖可能被重建（模型提案被作廢後改走 deterministic），若沿用上一版的
    連結，就會出現「證據說自己被 CL-002 使用，但 CL-002 沒引用它」這種對不起來的狀態 ——
    而那正是 citation gate 第 9 條要抓的問題。
    """
    for item in evidence or []:
        if hasattr(item, "related_claim_ids"):
            item.related_claim_ids = []
        elif isinstance(item, dict):
            item["related_claim_ids"] = []
    return apply_related_claim_ids(evidence, graph)


def _build_claims_with_gate(result: dict, evidence: list, plan_dict: dict, signals: list[dict],
                            critique: dict | None, *, client: object | None, deadline: float,
                            run_id: str, cited_evidence_ids, timeline=None) -> tuple:
    """建圖 → 寫回 Claim 連結 → 過 citation gate，FAIL 就降級重建，而不是照樣發佈。

    Gate FAIL 代表報告會包含追不回來源的判斷。這種情況下正確的處理是**丟掉整批模型提案**、
    改用 deterministic 圖重建（保守但可稽核），而不是逐條修補 —— 部分可信的圖比誠實的保守圖更糟。
    若 deterministic 圖也過不了 gate，那是本管線的 bug，必須讓執行明確失敗，
    不能輸出一份看起來正常但引用不成立的報告。
    """
    def build(active_client):
        graph = _build_claims(result, evidence, plan_dict, signals, critique,
                              client=active_client, deadline=deadline)
        linked = _link_claims_to_evidence(evidence, graph)
        # Timed on every attempt, so the Execution Log reports the gate run that actually decided
        # the outcome rather than the first one that failed.
        if timeline is not None:
            timeline.start("citation_gate")
        gate = run_citation_gate(run_id, evidence, graph["claims"],
                                 cited_evidence_ids=cited_evidence_ids, critique=critique)
        if timeline is not None:
            timeline.finish("citation_gate")
        return graph, gate, linked

    graph, gate, linked = build(client)
    if gate["status"] == GATE_STATUS_FAIL and graph["source"] == GRAPH_SOURCE_LLM:
        # 先把造成失敗的 errors 抄下來。`build(None)` 會重新綁定 `gate`，而重建後的
        # deterministic 圖通常會通過 gate（errors 是空的）—— 在那之後才讀 gate["errors"]
        # 等於用成功那次的結果覆蓋失敗原因，記錄只會剩下 "citation gate failed: "
        # 這個沒有任何資訊的字串，事後無法回答「模型的提案為什麼被擋掉」。
        rejected_errors = list(gate["errors"])
        graph, gate, linked = build(None)
        graph["fallback_reason"] = "citation gate failed: " + "; ".join(rejected_errors[:3])
    if gate["status"] == GATE_STATUS_FAIL:
        raise ValueError("Citation gate failed: " + "; ".join(gate["errors"]))
    return graph, gate, linked


def _collector_records(evidence: list) -> list[dict]:
    """每筆證據的採集紀錄：哪個 collector、可重現的 locator、狀態、以及產生的 Evidence ID。

    Execution Log 要能回答「這個數字是從哪個查詢來的」。只記錄一句 `collect_evidence: success`
    做不到這件事，因此這裡把每筆證據的 source locator 與查詢摘要一起留下。
    """
    records = []
    for item in evidence:
        reference = item.content_reference or {}
        query = {key: reference[key] for key in sorted(reference)
                 if key in ("endpoint", "query", "symbol", "interval", "chain", "address",
                            "transaction_hash", "feed_url", "file", "rows", "days", "query_name")}
        records.append({
            "collector": item.data_type,
            "evidence_id": item.evidence_id,
            "source": item.source,
            "source_locator": item.source_url,
            "query_summary": query or {"summary": "no structured locator recorded"},
            "source_type": item.source_type,
            "status": item.verification_status,
            "fetched_at": item.fetched_at,
            "reliability_score": item.reliability_score,
        })
    return records


def _manifest(context: RunContext, store: LocalArtifactStore, *, completed_at: str,
              duration_ms: float, live: bool, use_llm: bool, registry_version: str,
              gate: dict, evidence_error_count: int, providers: dict,
              rerun_of: str | None = None, question_hash: str = "",
              status: str = STATUS_COMPLETED, run_lifecycle: dict | None = None,
              degradation_reasons: list | None = None, deadline: dict | None = None) -> dict:
    """提交物清單：每個檔案的 SHA-256 加上重建這次執行所需的識別資訊。

    manifest 不把自己列進 `files`（無法對自己取雜湊），因此重複產生的結果是穩定的；
    `artifact_filenames` 仍列出全部六個檔名，讓稽核者知道應該收到幾個檔案。
    """
    return {
        "manifest_version": MANIFEST_VERSION,
        "run_id": context.run_id,
        "started_at": context.started_at,
        "completed_at": completed_at,
        "as_of": context.as_of,
        "duration_ms": duration_ms,
        "question": context.question,
        # 同一個問題與幣種組合的指紋。formal lock 用它判斷「這題是不是已經正式跑過」，
        # 稽核者也能用它把重跑與原始執行對上。
        "question_hash": question_hash,
        "coins": list(context.coins),
        # `mode` 是資料模式（offline／live／demo，T0.5 凍結）；`run_mode` 是 T7 的執行性質
        # （test／formal）。兩者刻意分開：離線的正式執行與連線的測試執行都必須能被正確描述。
        "mode": context.run_mode,
        "run_mode": (run_lifecycle or {}).get("run_mode"),
        "status": status,
        "degradation_reasons": list(degradation_reasons or []),
        "run_lifecycle": run_lifecycle,
        "deadline": dict(deadline or {}),
        "execution_flags": {"live": live, "use_llm": use_llm},
        "model": {"provider": context.model_provider, "model": context.model_id,
                  "region": context.region},
        # 每個階段實際用了哪個 provider／model。三者可能不同（例如分析走模型、Claim 走離線規則），
        # 所以分開記錄，不用單一 provider 欄位代表整趟執行。
        "stage_providers": providers,
        "code_commit": context.code_commit,
        "config_version": context.config_version,
        "versions": {
            "schema": SCHEMA_VERSION,
            "credibility_scoring": SCORING_VERSION,
            "claim_confidence_scoring": CLAIM_SCORING_VERSION,
            "citation_gate": CITATION_GATE_VERSION,
            "source_registry": registry_version,
        },
        "artifact_filenames": dict(ARTIFACT_FILENAMES),
        "artifact_root": str(store.base_path.resolve()),
        "files": store.entries,
        "manifest_file": ARTIFACT_FILENAMES["manifest"],
        "validation": {
            "evidence_error_count": evidence_error_count,
            "citation_gate_status": gate["status"],
            "citation_gate_error_count": gate["error_count"],
            "citation_gate_warning_count": gate["warning_count"],
            "citation_gate_checks": gate["checks"],
            "semantic_finding_count": gate["semantic_finding_count"],
        },
        # 重跑血緣：原始 run 的 ID、重跑理由與是否經過授權。第一次執行的紀錄不會因為重跑而消失，
        # 它仍留在自己的 run 目錄與 formal lock 歷史裡。
        "rerun_of": rerun_of or (run_lifecycle or {}).get("rerun_of"),
        "rerun_reason": (run_lifecycle or {}).get("rerun_reason"),
        "authorized_rerun": bool((run_lifecycle or {}).get("authorized_rerun")),
    }


def run(coin: str, question: str, output_dir: Path, live: bool = False, use_llm: bool = False,
        ohlcv_path: Path | None = None, time_budget_seconds: float = DEFAULT_TIME_BUDGET_SECONDS,
        deadline: float | None = None, history_path: Path | None = None,
        fulltext: bool = False, research_plan=None, planner_client: object | None = None,
        claim_client: object | None = None, analysis_client: object | None = None,
        critic_client: object | None = None, run_context: RunContext | None = None,
        run_record=None, finalization_deadline_seconds: float | None = None) -> dict:
    """執行一次分析。傳入 `run_record`（`src/run_manager.RunManager.create_run()` 產生）時，
    這次執行會納入正式 run 生命週期：狀態機、checkpoint 落地與 formal lock 同步。

    生命週期刻意包在管線外面，管線本身維持一條直線：任何階段丟出例外時，這裡負責把 run 標成
    `FAILED`、把已完成的 checkpoint 落地，然後把例外原封不動往上丟 —— 失敗紀錄不刪除，
    呼叫端也不會拿到一份看起來成功的結果。
    """
    if run_record is not None and run_record.status == STATUS_CREATED:
        run_record.mark(STATUS_RUNNING)
    try:
        result = _run_pipeline(
            coin, question, output_dir, live=live, use_llm=use_llm, ohlcv_path=ohlcv_path,
            time_budget_seconds=time_budget_seconds, deadline=deadline, history_path=history_path,
            fulltext=fulltext, research_plan=research_plan, planner_client=planner_client,
            claim_client=claim_client, analysis_client=analysis_client, critic_client=critic_client,
            run_context=run_context, run_record=run_record,
            finalization_deadline_seconds=finalization_deadline_seconds,
        )
    except BaseException:
        if run_record is not None:
            # 先標記失敗再落地，這樣 checkpoint 檔案本身就記著 FAILED；checkpoint 是「這次跑到哪、
            # 已經取得什麼」的唯一紀錄，不寫出來重跑時就沒有東西可以參照。
            if run_record.status == STATUS_RUNNING:
                run_record.mark(STATUS_FAILED)
            run_record.persist_checkpoints()
        raise
    if run_record is not None and run_record.status == STATUS_RUNNING:
        run_record.mark(result["run_status"])
    return result


def _run_pipeline(coin: str, question: str, output_dir: Path, live: bool = False,
                  use_llm: bool = False, ohlcv_path: Path | None = None,
                  time_budget_seconds: float = DEFAULT_TIME_BUDGET_SECONDS,
                  deadline: float | None = None, history_path: Path | None = None,
                  fulltext: bool = False, research_plan=None, planner_client: object | None = None,
                  claim_client: object | None = None, analysis_client: object | None = None,
                  critic_client: object | None = None, run_context: RunContext | None = None,
                  run_record=None,
                  finalization_deadline_seconds: float | None = None) -> dict:
    coin, question = validate_request(coin, question)
    started = time.perf_counter()
    timeline = _StageTimeline()
    # One RunContext per analysis: it supplies the run_id every artifact is stamped with, the code
    # commit, and the config version the manifest has to record. A caller (comparison leg, T7 formal
    # run) may pass its own so the whole bundle shares one identity.
    # A formal run brings its own context so the run_id in the lock, the output directory and the
    # manifest are the same string; otherwise one is created for this analysis.
    context = (run_record.context if run_record is not None else run_context) or RunContext.create(
        question, [coin], run_mode="live" if live else "offline")
    question_hash = compute_question_hash(question, [coin])
    timeline.mark("parse_input")
    hard_seconds, finalization_seconds = _deadline_bounds(time_budget_seconds,
                                                          finalization_deadline_seconds)
    if deadline is None:
        deadline = time.monotonic() + hard_seconds

    def deadline_now():
        return _deadline_state(deadline, hard_seconds, finalization_seconds)

    def checkpoint(stage: str, data: dict) -> None:
        """每個階段結束就保存一次，因此後面任何階段失敗都不會讓前面的成果消失。"""
        if run_record is not None:
            run_record.save_checkpoint(stage, data)
            run_record.persist_checkpoints()

    # Plan before collecting. A comparison run passes its shared plan in so both legs are planned
    # once, over one time window and one set of comparison dimensions.
    timeline.start("plan_research")
    if research_plan is None:
        plan, planning_log = _plan_research(
            question, [coin], use_llm=use_llm, client=planner_client, deadline=deadline,
        )
    else:
        plan, planning_log = research_plan, {
            "path": _PLANNING_PATH_SHARED, "fallback_used": False,
            "fallback_reason": None, "duration_seconds": 0.0,
        }
    timeline.finish("plan_research")
    checkpoint("plan", {"path": planning_log["path"], "task_modes": list(plan.task_modes),
                        "time_window": dict(plan.time_window),
                        "hypothesis_count": len(plan.hypotheses)})
    timeline.start("collect_evidence")
    phase_started = time.monotonic()
    collection_deadline = min(deadline, phase_started + COLLECTION_PHASE_SECONDS)
    # Inside the finalization window nothing optional starts: full-text crawling is the one
    # supplementary fetch this pipeline has, so it is the first thing dropped.
    collection_decision = deadline_now()
    fulltext_requested = fulltext
    if collection_decision.should_skip_follow_up_collection:
        fulltext = False
    evidence, collection_log, agent_report = collect_evidence_detailed(
        coin, live=live, deadline=collection_deadline if live else None, fulltext=fulltext,
    )
    if fulltext_requested and not fulltext:
        collection_log = list(collection_log) + [f"fulltext:skipped:{_FINALIZATION_REASON}"]
    # The crawl phase ends the moment it is done; the report phase starts immediately rather than
    # waiting out the remaining ceiling.
    phase_timings = {"collection_ms": round((time.monotonic() - phase_started) * 1000, 1)}
    reasoning_started = time.monotonic()
    reasoning_deadline = min(deadline, reasoning_started + REASONING_PHASE_SECONDS)
    market = next((item for item in evidence if item.data_type == "market"), None)
    if ohlcv_path:
        rows = load_ohlcv(ohlcv_path, coin, days=15)
        prices = [row["close"] for row in rows]
        if market is None:
            from .day1_mvp import Evidence
            market = Evidence("EV-OHLCV-001", "Competition OHLCV CSV", str(ohlcv_path), datetime.now(timezone.utc).isoformat(), "market", coin, f"{len(rows)}d", {}, 0.95, {"file": str(ohlcv_path), "rows": len(rows), "date_start": rows[0]["date"], "date_end": rows[-1]["date"]}, f"Competition OHLCV close prices for {coin}", source_type="local_csv")
            evidence.insert(0, market)
        market.content["prices"] = prices
        market.content["volumes"] = [row["volume"] for row in rows]
        market.content["dates"] = [row["date"] for row in rows]
    if history_path:
        evidence.append(_price_history_evidence(coin, history_path))
    # Stamp the run before scoring: from here on every record can be checked against this run, and
    # the citation gate can tell "this run's evidence" from "a record that came from somewhere else".
    stamp_run_id(evidence, context.run_id)
    timeline.finish("collect_evidence")
    checkpoint("collected_evidence", {"evidence_ids": [item.evidence_id for item in evidence],
                                      "collection_log": list(collection_log)})
    # Score before anything reads the evidence: the signal inventory, the prompt, the report and the
    # claim graph all quote `reliability_score`, so they must all see the engine's number rather
    # than the adapter's hint.
    timeline.start("score_evidence")
    registry = load_source_registry()
    credibility_records = enrich_and_score_evidence(evidence, registry=registry)
    credibility = _credibility_summary(credibility_records,
                                       registry.get("registry_version", "unavailable"))
    timeline.finish("score_evidence")
    checkpoint("scored_evidence", {"substantive_count": credibility["substantive_count"],
                                   "fallback_ids": list(credibility["fallback_ids"]),
                                   "mean_final_score": credibility["mean_final_score"]})
    plan_dict = plan_to_dict(plan)
    result = {
        "coin": coin.upper(), "question": question,
        "research_plan": plan_dict, "planning": planning_log,
        "credibility": credibility,
        "summary": f"{coin.upper()} has a positive market signal; news, on-chain and social evidence require further validation.",
        "signals": {item.data_type: item.content.get("sentiment", "available") for item in evidence},
        "indicators": market.content if market is not None else {},
        "risk_factors": [],
        "evidence_ids": [item.evidence_id for item in evidence],
        "disclaimer": "For research demonstration only; not investment advice.",
        "collection_log": collection_log,
    }
    timeline.mark("calculate_indicators")
    signals = _signal_inventory(result, evidence)
    result["stance"] = _market_stance(signals)
    result["consistency"] = _source_consistency(signals, evidence)
    result["risk_factors"] = _dynamic_risk_factors(result, evidence, signals) + _credibility_risk_factors(credibility)
    timeline.start("llm_reasoning")
    llm_status = "offline_fallback"
    # 離線推理的觀察重點本來就是規則產生的；只有走模型路徑時這個值才會變。
    observation_source = "deterministic_fallback"
    llm_info = llm_runtime_info()
    llm_seconds_remaining = round(reasoning_deadline - time.monotonic(), 1)
    reasoning_decision = deadline_now()
    if use_llm and llm_seconds_remaining < _LLM_MIN_SECONDS:
        # Skip rather than start: a call begun with 3 seconds left burns the budget and still fails.
        # Checked before the finalization window so the existing watchdog keeps its own label.
        llm_status = "fallback:TimeBudgetExceeded"
        result["reasoning"] = _offline_reasoning(
            result, evidence,
            f"時間預算僅剩 {llm_seconds_remaining} 秒（低於 LLM 最低需求 {_LLM_MIN_SECONDS} 秒），watchdog 強制切換至離線推理",
        )
    elif use_llm and reasoning_decision.should_finalize:
        # Inside the finalization window the remaining time belongs to the artifacts, so no model
        # call is started at all -- the deterministic reasoning path is instant and always available.
        llm_status = f"fallback:{_FINALIZATION_REASON}"
        result["reasoning"] = _offline_reasoning(
            result, evidence,
            f"已進入收尾窗口（已耗用 {reasoning_decision.elapsed_seconds:.1f} 秒，收尾門檻 "
            f"{finalization_seconds:.0f} 秒），優先保存提交物，改用離線推理",
        )
    elif use_llm:
        try:
            llm_result = analyze_with_llm(coin, question, _llm_evidence_payload(evidence),
                                          client=analysis_client)
            unknown = set(llm_result.get("cited_evidence_ids", [])) - set(result["evidence_ids"])
            if unknown:
                raise ValueError(f"LLM cited unknown evidence IDs: {sorted(unknown)}")
            # 「後續觀察重點」必須是前瞻項目。模型有時只把 facts 的主語搬過來，那一段就
            # 變成事實的重複清單；過濾是 deterministic 的，全部被濾掉才改用規則版本。
            llm_result["observation_points"], observation_source = _forward_looking_observations(
                llm_result, _observation_points(evidence, signals))
            result["reasoning"] = llm_result
            llm_status = "success"
        except Exception as error:
            llm_status = f"fallback:{type(error).__name__}"
            result["reasoning"] = _offline_reasoning(result, evidence, f"LLM failed: {type(error).__name__}")
    else:
        result["reasoning"] = _offline_reasoning(result, evidence)
    timeline.finish("llm_reasoning")

    # E2：問題相關性評估。刻意排在分析之後、Claim 之前 —— 標籤搭的是同一次分析回應的便車
    # （因此模型呼叫次數不變），而 Claim 的信心分量要用得到評估算出的 claim_relevance。
    timeline.start("assess_evidence")
    assessment_summary = assess_evidence_relevance(
        evidence, coin=result["coin"], signals=signals, plan_dict=plan_dict,
        proposals=result["reasoning"].get(EVIDENCE_ASSESSMENT_RESULT_FIELD) or None)
    result["evidence_assessment"] = assessment_summary
    timeline.finish("assess_evidence")
    phase_timings["reasoning_ms"] = round((time.monotonic() - reasoning_started) * 1000, 1)

    # Phase 3: audit. The critic sees the finished analysis and its evidence, and may only annotate
    # and lower confidence -- it never rewrites the conclusion, because a critic that edits the work
    # is just a second analyst and the reader loses the independent check.
    timeline.start("critic_review")
    critic_started = time.monotonic()
    critic_deadline = min(deadline, critic_started + CRITIC_PHASE_SECONDS)
    critic_status, critique = "skipped:disabled", None
    # 例外訊息與狀態分開存。`critic_status` 的字面格式被多處依賴（`== "success"`、
    # `startswith("fallback:")`、報告與 Execution Log 都直接顯示它），不能把訊息塞進去。
    critic_detail = ""
    critic_decision = deadline_now()
    if use_llm:
        critic_seconds_remaining = round(critic_deadline - time.monotonic(), 1)
        if critic_seconds_remaining < _CRITIC_MIN_SECONDS:
            # Existing watchdog first, so its label and behaviour are unchanged.
            critic_status = "skipped:TimeBudgetExceeded"
        elif critic_decision.should_skip_critic:
            # The semantic critic is optional by design; the report, claims and gate are not.
            critic_status = f"skipped:{_FINALIZATION_REASON}"
        else:
            try:
                # Validate before committing to `critique`: a critique that cites evidence which
                # does not exist is itself a hallucination, and accepting it would let it lower
                # confidence and display invented findings.
                evidence_snapshot = json.dumps([asdict(item) for item in evidence], sort_keys=True)
                candidate = critique_with_llm(coin, question, result, _llm_evidence_payload(evidence),
                                              client=critic_client)
                unknown = {
                    finding.get("evidence_id") for finding in candidate.get("findings", [])
                } - set(result["evidence_ids"]) - {"N/A", "", None}
                if unknown:
                    raise ValueError(f"Critic cited unknown evidence IDs: {sorted(unknown)}")
                # The critic may annotate and lower confidence; it may not touch the raw evidence.
                # Checked rather than assumed, because the critique is built from the same records.
                if json.dumps([asdict(item) for item in evidence], sort_keys=True) != evidence_snapshot:
                    raise ValueError("Critic modified raw evidence, which is not permitted")
                critique = candidate
                critic_status = "success"
            except Exception as error:
                critic_status = f"fallback:{type(error).__name__}"
                # 型別名不足以診斷：上面兩個 guard 都拋 ValueError，但「模型捏造 Evidence ID」
                # 是模型幻覺，「稽核改寫了原始證據」是完整性違規 —— 只看 fallback:ValueError
                # 無法分辨是哪一種，而這兩件事該有的反應完全不同。
                critic_detail = str(error)
    if critique:
        adjustment = float(critique.get("confidence_adjustment") or 0)
        original = float(result["reasoning"].get("confidence", 0) or 0)
        # Only downward: a positive adjustment is dropped rather than applied, so a critic that
        # tries to talk the confidence up leaves a trace instead of an effect.
        applied = min(0.0, adjustment)
        adjusted = max(0.0, min(1.0, original + applied))
        critique["original_confidence"] = round(original, 4)
        critique["adjusted_confidence"] = round(adjusted, 4)
        critique["applied_adjustment"] = round(applied, 4)
        critique["positive_adjustment_rejected"] = adjustment > 0
        result["reasoning"]["confidence"] = round(adjusted, 4)
    result["critique"] = critique
    result["critic_status"] = critic_status
    timeline.finish("critic_review")
    phase_timings["critic_ms"] = round((time.monotonic() - critic_started) * 1000, 1)

    # Claims are built last because they consume the critic's verdict: the critic may only lower a
    # claim's confidence, so it has to have spoken before the score is fixed.
    # Timed in its own Execution Log step rather than in `phase_actual_ms`: the three phase ceilings
    # are a frozen contract, and claim building has no ceiling of its own -- like planning, it runs
    # inside whatever the total deadline has left.
    timeline.start("build_claims")
    claims_started = time.monotonic()
    claims_decision = deadline_now()
    active_claim_client, claim_client_reason = _claim_client(
        result["reasoning"], use_llm=use_llm, client=claim_client, deadline=deadline,
        finalizing=claims_decision.should_finalize)
    # Build, link the claim IDs back onto the evidence (so `evidence.json` reads in both directions),
    # then run the citation gate. A gate failure discards the model's graph and rebuilds it
    # deterministically -- see `_build_claims_with_gate`.
    claim_graph, citation_gate, linked_evidence_count = _build_claims_with_gate(
        result, evidence, plan_dict, signals, critique,
        client=active_claim_client, deadline=deadline, run_id=context.run_id,
        cited_evidence_ids=result["reasoning"].get("cited_evidence_ids") or (),
        timeline=timeline,
    )
    # `claims.json` deliberately carries no run_id: two runs over the same evidence must produce a
    # byte-identical claims document, which is the cheapest possible proof that the scoring is
    # deterministic. The binding between run and artifacts lives in `manifest.json`, and every
    # evidence record carries its own run_id.
    claims_doc = claims_document(claim_graph)
    result["claims"] = claim_graph["claims"]
    result["citation_gate"] = citation_gate
    result["claim_graph"] = {
        "source": claim_graph["source"],
        "fallback_reason": claim_graph["fallback_reason"],
        "claim_count": len(claim_graph["claims"]),
        "verdicts": {claim["claim_id"]: claim["verdict"] for claim in claim_graph["claims"]},
        "hypothesis_assessments": claim_graph["hypothesis_assessments"],
        "linked_evidence_count": linked_evidence_count,
        "scoring_version": claim_graph["scoring_version"],
    }
    claims_duration_ms = round((time.monotonic() - claims_started) * 1000, 1)
    timeline.finish("build_claims")
    checkpoint("claims", {"claim_ids": [claim["claim_id"] for claim in claim_graph["claims"]],
                          "source": claim_graph["source"],
                          "citation_gate_status": citation_gate["status"]})

    timeline.start("validate_evidence")
    validation_errors = validate_evidence(evidence, result["evidence_ids"], require_scored=True)
    if validation_errors:
        raise ValueError("Evidence validation failed: " + "; ".join(validation_errors))
    timeline.finish("validate_evidence")

    # T7: the lifecycle status has to be decided before the artifacts are written, because both
    # `execution_log.json` and `manifest.json` record it. A run that lost a collector, fell back to
    # offline reasoning or had to skip the critic still produces a full report -- it is reported as
    # COMPLETED_DEGRADED rather than COMPLETED so nobody reads it as a clean run.
    degradation_reasons = _degradation_reasons(
        collection_log, credibility, llm_status=llm_status, critic_status=critic_status,
        claim_graph=claim_graph, gate=citation_gate, claim_client_reason=claim_client_reason)
    run_status = STATUS_COMPLETED_DEGRADED if degradation_reasons else STATUS_COMPLETED
    if run_record is not None:
        for reason in degradation_reasons:
            run_record.note_degradation(reason)
    # The record itself is only transitioned once every artifact is on disk (see `run()`), so the
    # status printed here is the decided outcome rather than the record's in-flight state.
    run_lifecycle = None if run_record is None else {
        **run_record.lifecycle_payload(), "status": run_status,
    }
    final_decision = deadline_now()
    deadline_report = {
        "hard_deadline_seconds": hard_seconds,
        "finalization_deadline_seconds": finalization_seconds,
        "competition_hard_ceiling_seconds": HARD_DEADLINE_SECONDS_MAX,
        "competition_finalization_ceiling_seconds": FINALIZATION_DEADLINE_SECONDS_MAX,
        "elapsed_seconds_at_finalization": round(final_decision.elapsed_seconds, 1),
        "remaining_seconds_at_finalization": round(final_decision.remaining_seconds, 1),
        "reason": final_decision.reason,
        "follow_up_collection_skipped": bool(fulltext_requested and not fulltext),
        "critic_skipped_for_deadline": critic_status == f"skipped:{_FINALIZATION_REASON}",
        "llm_skipped_for_deadline": llm_status == f"fallback:{_FINALIZATION_REASON}",
        "claim_model_skipped_for_deadline": claim_client_reason == _FINALIZATION_REASON,
    }

    timeline.start("generate_report")
    # Every artifact goes through the ArtifactStore, which records the byte count and SHA-256 of
    # each file as it is written -- that is what makes `manifest.json` verifiable rather than a
    # hand-maintained list. Prefix is empty so the paths stay exactly where callers expect them.
    store = LocalArtifactStore(output_dir)
    reasoning = result["reasoning"]
    # Series belong in the charts, not the prose report: dumping the raw `dates`/`prices` arrays
    # here buried the readable indicators under hundreds of lines.
    display_indicators = {key: value for key, value in result["indicators"].items() if key not in ("prices", "volumes", "dates") and value is not None}
    history = next((item for item in evidence if item.data_type == "price_history"), None)
    evidence_records = [asdict(item) for item in evidence]
    evidence_window = _evidence_window(evidence)
    completed_at = _now_iso()
    if run_lifecycle is not None:
        # 提交物與生命週期紀錄共用同一個完成時間，稽核時不會出現兩個對不起來的時間戳。
        run_lifecycle["completed_at"] = completed_at
    stage_providers = {
        "planner": {"provider": llm_info["provider"] if planning_log["path"] == PLANNING_PATH_LLM
                    else "deterministic",
                    "model": llm_info["model"] if planning_log["path"] == PLANNING_PATH_LLM else None},
        "analyst": {"provider": llm_info["provider"] if llm_status == "success" else "deterministic",
                    "model": llm_info["model"] if llm_status == "success" else None,
                    "status": llm_status},
        "critic": {"provider": llm_info["provider"] if critic_status == "success" else "deterministic",
                   "model": llm_info["model"] if critic_status == "success" else None,
                   "status": critic_status},
        "claims": {"provider": llm_info["provider"] if claim_graph["source"] == GRAPH_SOURCE_LLM
                   else "deterministic",
                   "model": llm_info["model"] if claim_graph["source"] == GRAPH_SOURCE_LLM else None,
                   "scored_by": "deterministic_rules"},
    }
    # Markdown structure is decided here, in Python, not by the model: `render_competition_report`
    # is a pure function with a fixed section order, so the report a judge reads has the same shape
    # whether the run was live, offline, or degraded halfway through.
    store.write_text(ARTIFACT_FILENAMES["report"], render_competition_report({
        "run": {
            "run_id": context.run_id, "started_at": context.started_at,
            "completed_at": completed_at, "as_of": context.as_of, "mode": context.run_mode,
            "live": live, "use_llm": use_llm, "question": question, "coins": [result["coin"]],
            "provider": stage_providers["analyst"]["provider"],
            "model": stage_providers["analyst"]["model"],
            # T7：正式執行資訊也印在報告裡，讀者不必打開 manifest 才知道這是正式執行或有降級。
            "run_mode": (run_lifecycle or {}).get("run_mode"),
            "status": run_status,
            "degradation_reasons": degradation_reasons,
            "rerun_of": (run_lifecycle or {}).get("rerun_of"),
            "rerun_reason": (run_lifecycle or {}).get("rerun_reason"),
            "authorized_rerun": (run_lifecycle or {}).get("authorized_rerun"),
        },
        "plan": plan_dict,
        "stance": result["stance"],
        "reasoning": reasoning,
        "indicators": display_indicators,
        "history": asdict(history) if history is not None else None,
        "claim_graph": claim_graph,
        "confidence_weights": CONFIDENCE_WEIGHTS,
        "consistency": result["consistency"],
        "critique": critique,
        "critic_status": critic_status,
        "citation_gate": citation_gate,
        "evidence": evidence_records,
        "evidence_window": evidence_window,
        "risk_factors": result["risk_factors"],
    }))
    store.write_json(ARTIFACT_FILENAMES["evidence"], evidence_records)
    store.write_json(ARTIFACT_FILENAMES["research_plan"], plan_dict)
    store.write_json(ARTIFACT_FILENAMES["claims"], claims_doc)
    timeline.finish("generate_report")

    skipped = [entry for entry in collection_log if "skipped" in entry]
    created_ids = [item.evidence_id for item in evidence]
    store.write_json(ARTIFACT_FILENAMES["execution_log"], {
        # `status` 是既有契約（「這次執行有沒有走完」），T7 的生命週期狀態另外放在 `run_status`，
        # 兩者不合併：一次 COMPLETED_DEGRADED 的執行仍然是走完的，六個檔案照樣齊全。
        "status": "success",
        "run_status": run_status,
        "run_mode": run_record.mode if run_record is not None else None,
        "question_hash": question_hash,
        "degradation_reasons": degradation_reasons,
        "run_lifecycle": run_lifecycle,
        "run_id": context.run_id,
        "started_at": context.started_at,
        "completed_at": completed_at,
        "mode": context.run_mode,
        "collection": collection_log,
        "time_budget": {
            "budget_seconds": time_budget_seconds,
            "llm_seconds_remaining_at_decision": llm_seconds_remaining,
            "sources_skipped_by_watchdog": skipped,
            "phase_ceilings_seconds": {
                "collection": COLLECTION_PHASE_SECONDS,
                "reasoning": REASONING_PHASE_SECONDS,
                "critic": CRITIC_PHASE_SECONDS,
            },
            "phase_actual_ms": phase_timings,
            "deadline": deadline_report,
        },
        "collection_agents": agent_report,
        "stage_providers": stage_providers,
        "citation_gate": citation_gate,
        "steps": [
            {"name": "parse_input", "status": "success", "tool": "src.errors.validate_request",
             **timeline.entry("parse_input")},
            {**_planner_step(plan, planning_log, llm_info), **timeline.entry("plan_research"),
             "tool": "src.planner.build_research_plan"},
            {"name": "collect_evidence",
             "status": "degraded" if skipped else "success",
             "tool": "src.day2_sources.collect_evidence_detailed",
             "details": collection_log,
             # Per-collector locators and the IDs each one produced: without these the log cannot
             # answer "which query produced this number", which is the whole point of keeping it.
             "collectors": _collector_records(evidence),
             "evidence_ids_created": created_ids,
             "fallback_reason": "; ".join(skipped) or None,
             **timeline.entry("collect_evidence")},
            {
                # Deterministic Python, never the model: the log records the engine version and
                # every cap that fired so a reviewer can recompute any score by hand.
                "name": "score_evidence",
                "status": "degraded" if credibility["rejected_ids"] or credibility["fallback_ids"] else "success",
                "scored_by": "deterministic_rules",
                "tool": "src.credibility.score_evidence_batch",
                **timeline.entry("score_evidence"),
                **credibility,
            },
            {"name": "calculate_indicators", "status": "success",
             "tool": "src.orchestrator._signal_inventory", **timeline.entry("calculate_indicators")},
            {"name": "validate_evidence", "status": "success", "error_count": 0,
             "tool": "src.validation.validate_evidence", **timeline.entry("validate_evidence")},
            {"name": "llm_reasoning", "status": llm_status, **llm_info,
             "tool": "src.llm.analyze_with_llm",
             "fallback_reason": None if llm_status == "success" else llm_status,
             # 「後續觀察重點」是模型寫的、被過濾過的，還是整段改用規則產生的。
             "observation_points_source": observation_source,
             **timeline.entry("llm_reasoning")},
            {**_assessment_step(assessment_summary, timeline.entry("assess_evidence")),
             "tool": "src.orchestrator.assess_evidence_relevance"},
            {"name": "critic_review", "status": critic_status, **llm_info,
             "tool": "src.llm.critique_with_llm",
             # `status` 維持既有字面值；具體原因附在 fallback_reason 之後，讓 Execution Log
             # 能回答「稽核為什麼沒跑」而不只是「稽核沒跑」。
             "fallback_reason": None if critic_status == "success" else
             "; ".join(part for part in (critic_status, critic_detail) if part),
             "semantic_categories": sorted({
                 finding["category"] for finding in citation_gate["semantic_findings"]}),
             **timeline.entry("critic_review")},
            {**_claims_step(claim_graph, claim_client_reason, llm_info, claims_duration_ms),
             **timeline.entry("build_claims"), "tool": "src.claim_graph.build_claim_graph"},
            {"name": "citation_gate", "status": citation_gate["status"],
             "tool": "src.validation.run_citation_gate",
             "gate_version": citation_gate["gate_version"],
             "checks": citation_gate["checks"],
             "error_count": citation_gate["error_count"],
             "warning_count": citation_gate["warning_count"],
             "errors": citation_gate["errors"],
             "warnings": citation_gate["warnings"],
             "semantic_finding_count": citation_gate["semantic_finding_count"],
             **timeline.entry("citation_gate")},
            {"name": "generate_report", "status": "success",
             "tool": "src.report_renderer.render_competition_report",
             "artifacts": sorted(ARTIFACT_FILENAMES.values()),
             **timeline.entry("generate_report")},
        ],
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
    })
    # Manifest last: it hashes the five artifacts written above, so it cannot include itself.
    manifest = _manifest(
        context, store, completed_at=completed_at,
        duration_ms=round((time.perf_counter() - started) * 1000, 2),
        live=live, use_llm=use_llm,
        registry_version=registry.get("registry_version", "unavailable"),
        gate=citation_gate, evidence_error_count=len(validation_errors),
        providers=stage_providers, question_hash=question_hash, status=run_status,
        run_lifecycle=run_lifecycle, degradation_reasons=degradation_reasons,
        deadline=deadline_report,
    )
    store.write_json(ARTIFACT_FILENAMES["manifest"], manifest)
    result["manifest"] = manifest
    result["run_id"] = context.run_id
    result["run_status"] = run_status
    result["question_hash"] = question_hash
    result["degradation_reasons"] = degradation_reasons
    result["deadline"] = deadline_report
    checkpoint("final_artifacts", {"artifacts": sorted(ARTIFACT_FILENAMES.values()),
                                   "run_status": run_status,
                                   "citation_gate_status": citation_gate["status"]})
    return result


def run_comparison(coin_a: str, coin_b: str, question: str, output_dir: Path, live: bool = False,
                   use_llm: bool = False, time_budget_seconds: float = DEFAULT_TIME_BUDGET_SECONDS,
                   planner_client: object | None = None, claim_client: object | None = None,
                   run_record=None) -> dict:
    """兩腳比較執行。傳入 `run_record` 時，整組比較納入同一個正式 run 生命週期。

    兩腳各自仍有自己的 run_id 與 manifest；`run_record` 描述的是「這一組比較」這次執行，
    因此狀態只在兩腳都跑完後才轉換，任一腳失敗即整組 `FAILED`。
    """
    if run_record is not None and run_record.status == STATUS_CREATED:
        run_record.mark(STATUS_RUNNING)
    try:
        payload = _run_comparison_pipeline(
            coin_a, coin_b, question, output_dir, live=live, use_llm=use_llm,
            time_budget_seconds=time_budget_seconds, planner_client=planner_client,
            claim_client=claim_client, run_record=run_record,
        )
    except BaseException:
        if run_record is not None:
            if run_record.status == STATUS_RUNNING:
                run_record.mark(STATUS_FAILED)
            run_record.persist_checkpoints()
        raise
    if run_record is not None and run_record.status == STATUS_RUNNING:
        run_record.mark(payload["manifest"]["status"])
    return payload


def _run_comparison_pipeline(coin_a: str, coin_b: str, question: str, output_dir: Path,
                             live: bool = False, use_llm: bool = False,
                             time_budget_seconds: float = DEFAULT_TIME_BUDGET_SECONDS,
                             planner_client: object | None = None,
                             claim_client: object | None = None, run_record=None) -> dict:
    """Run two single-coin analyses and render a side-by-side comparison.

    Both legs share one wall-clock deadline rather than getting a full budget each, so the pair
    still finishes inside the same envelope as a single analysis. Each leg keeps its own complete
    output directory, so the per-coin report and evidence stay independently auditable.
    """
    coin_a, question = validate_request(coin_a, question)
    coin_b, _ = validate_request(coin_b, question)
    if coin_a == coin_b:
        raise AgentInputError("比較分析需要兩個不同的幣種")

    started = time.perf_counter()
    # Both legs share one hard deadline, clamped to the competition ceiling the same way a single
    # analysis is; each leg derives its own finalization window from that shared deadline.
    hard_seconds, _ = _deadline_bounds(time_budget_seconds, None)
    deadline = time.monotonic() + hard_seconds
    output_dir.mkdir(parents=True, exist_ok=True)
    # The comparison bundle is its own run: it gets a run_id and a manifest so the shared plan and
    # the merged claims are verifiable too, while each leg keeps its own run_id and manifest.
    context = (run_record.context if run_record is not None else None) or RunContext.create(
        question, [coin_a, coin_b], run_mode="live" if live else "offline")
    store = LocalArtifactStore(output_dir)

    # One plan for the pair, not one per leg: a comparison is only fair if both coins are read over
    # the same time window, the same as-of and the same comparison dimensions.
    shared_plan, shared_planning_log = _plan_research(
        question, [coin_a, coin_b], use_llm=use_llm, client=planner_client, deadline=deadline,
    )
    shared_plan_dict = plan_to_dict(shared_plan)
    store.write_json(ARTIFACT_FILENAMES["research_plan"], shared_plan_dict)

    results, profiles, claims_by_coin = {}, {}, {}
    for coin in (coin_a, coin_b):
        leg_dir = output_dir / coin
        results[coin] = run(coin, question, leg_dir, live=live, use_llm=use_llm,
                            time_budget_seconds=time_budget_seconds, deadline=deadline,
                            research_plan=shared_plan, claim_client=claim_client)
        evidence = json.loads((leg_dir / "evidence.json").read_text(encoding="utf-8"))
        profiles[coin] = build_profile(coin, evidence)
        claims_by_coin[coin] = json.loads(
            (leg_dir / ARTIFACT_FILENAMES["claims"]).read_text(encoding="utf-8"))

    # One `claims.json` for the pair, keyed by coin. The legs are kept separate rather than merged
    # into one list: both legs number their claims from CL-001, and renumbering them would break the
    # link between a claim and the leg whose evidence it was scored against.
    store.write_json(ARTIFACT_FILENAMES["claims"], {
        "mode": "comparison",
        "coins": [coin_a, coin_b],
        # A comparison is only fair if both sides were read over the same window and the same
        # dimensions, so the shared plan's terms are recorded next to the claims they produced.
        "shared_time_window": dict(shared_plan.time_window),
        "comparison_dimensions": list(shared_plan.comparison_dimensions),
        "claims_by_coin": claims_by_coin,
    })

    comparison = compare_profiles(profiles[coin_a], profiles[coin_b])
    markdown = comparison_markdown(comparison, profiles[coin_a], profiles[coin_b])
    store.write_text("comparison.md",
        f"# {coin_a} vs {coin_b} 比較研究\n\n## 研究問題\n{question}\n\n{markdown}\n"
        f"## 個別幣種完整報告\n- {coin_a}: `{coin_a}/report.md`\n- {coin_b}: `{coin_b}/report.md`\n\n"
        "_This is research support, not investment advice._\n")
    payload = {
        "mode": "comparison", "coins": [coin_a, coin_b], "question": question,
        "run_id": context.run_id,
        "research_plan": shared_plan_dict, "planning": shared_planning_log,
        "results": results, "profiles": profiles, "comparison": comparison,
        "claims_by_coin": claims_by_coin,
        "markdown": markdown,
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
    }
    store.write_json("comparison.json", payload)
    # The pair's own manifest. Each leg is referenced by run_id rather than copied in: the leg
    # manifests already hash their own artifacts, and duplicating them here would create two
    # sources of truth for the same files.
    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    gate_statuses = {coin: results[coin]["citation_gate"]["status"] for coin in (coin_a, coin_b)}
    leg_statuses = {coin: results[coin]["run_status"] for coin in (coin_a, coin_b)}
    # 兩腳只要有一腳降級，整組比較就不能宣稱是乾淨的執行。
    pair_status = (STATUS_COMPLETED if set(leg_statuses.values()) == {STATUS_COMPLETED}
                   else STATUS_COMPLETED_DEGRADED)
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "run_id": context.run_id,
        "mode": "comparison",
        "status": pair_status,
        "question_hash": compute_question_hash(question, [coin_a, coin_b]),
        "degradation_reasons": sorted({reason for coin in (coin_a, coin_b)
                                       for reason in results[coin]["degradation_reasons"]}),
        "started_at": context.started_at,
        "completed_at": _now_iso(),
        "as_of": context.as_of,
        "duration_ms": duration_ms,
        "question": question,
        "coins": [coin_a, coin_b],
        "execution_flags": {"live": live, "use_llm": use_llm},
        "code_commit": context.code_commit,
        "config_version": context.config_version,
        "shared_time_window": dict(shared_plan.time_window),
        "run_mode": run_record.mode if run_record is not None else None,
        "run_lifecycle": None if run_record is None else {
            **run_record.lifecycle_payload(), "status": pair_status, "completed_at": _now_iso(),
        },
        "legs": {coin: {"run_id": results[coin]["run_id"],
                        "directory": coin,
                        "manifest": f"{coin}/{ARTIFACT_FILENAMES['manifest']}",
                        "status": leg_statuses[coin],
                        "citation_gate_status": gate_statuses[coin]}
                 for coin in (coin_a, coin_b)},
        "files": store.entries,
        "manifest_file": ARTIFACT_FILENAMES["manifest"],
        "validation": {"citation_gate_status_by_coin": gate_statuses,
                       "run_status_by_coin": leg_statuses},
    }
    store.write_json(ARTIFACT_FILENAMES["manifest"], manifest)
    payload["manifest"] = manifest
    return payload
