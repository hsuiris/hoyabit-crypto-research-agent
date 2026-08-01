"""Day 2 initial Orchestrator: collect evidence, analyse, and write outputs."""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path

from .comparison import build_profile, compare_profiles, comparison_markdown
from .day2_sources import collect_evidence_detailed
from .validation import validate_evidence
from .errors import AgentInputError, validate_request
from .llm import (analyze_with_llm, critique_with_llm, default_llm_client, llm_is_configured,
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
# Planning runs before collection and draws on the same total deadline instead of getting a phase
# ceiling of its own, so adding a planner cannot push the run past the competition limit. Below this
# remaining margin the planner is not attempted at all -- the deterministic keyword plan is instant.
_PLANNER_MIN_SECONDS = 5.0

# Where the plan came from, as recorded in the Execution Log. "shared" means the plan was built once
# by a comparison run and handed to both legs, so both coins are judged over the same time window.
_PLANNING_PATH_SHARED = "shared"


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

    observation_points = []
    if opposing:
        observation_points.append(f"優先追蹤反向訊號是否強化：{opposing[0]['text']}（{opposing[0]['evidence_id']}）。")
    if net_side != "neutral":
        observation_points.append(f"確認下一個 24 小時價格與成交量是否延續{'偏多' if net_side == 'bull' else '偏空'}方向，否則視為假突破。")
    macro_item = next((item for item in evidence if item.data_type == "macro"), None)
    if macro_item and macro_item.content.get("fear_greed_value") is not None:
        observation_points.append(f"追蹤全市場恐懼貪婪指數是否脫離目前的 {macro_item.content.get('fear_greed_value')} 區間，總體風險偏好轉向會同步改變個別幣種的訊號解讀。")
    observation_points.append("追蹤官方公告與鏈上活躍度是否與價格方向同步，若背離則優先相信鏈上與官方事件。")

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
    )


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


def run(coin: str, question: str, output_dir: Path, live: bool = False, use_llm: bool = False,
        ohlcv_path: Path | None = None, time_budget_seconds: float = DEFAULT_TIME_BUDGET_SECONDS,
        deadline: float | None = None, history_path: Path | None = None,
        fulltext: bool = False, research_plan=None, planner_client: object | None = None) -> dict:
    coin, question = validate_request(coin, question)
    started = time.perf_counter()
    if deadline is None:
        deadline = time.monotonic() + time_budget_seconds
    # Plan before collecting. A comparison run passes its shared plan in so both legs are planned
    # once, over one time window and one set of comparison dimensions.
    if research_plan is None:
        plan, planning_log = _plan_research(
            question, [coin], use_llm=use_llm, client=planner_client, deadline=deadline,
        )
    else:
        plan, planning_log = research_plan, {
            "path": _PLANNING_PATH_SHARED, "fallback_used": False,
            "fallback_reason": None, "duration_seconds": 0.0,
        }
    phase_started = time.monotonic()
    collection_deadline = min(deadline, phase_started + COLLECTION_PHASE_SECONDS)
    evidence, collection_log, agent_report = collect_evidence_detailed(
        coin, live=live, deadline=collection_deadline if live else None, fulltext=fulltext,
    )
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
            from datetime import datetime, timezone
            market = Evidence("EV-OHLCV-001", "Competition OHLCV CSV", str(ohlcv_path), datetime.now(timezone.utc).isoformat(), "market", coin, f"{len(rows)}d", {}, 0.95, {"file": str(ohlcv_path), "rows": len(rows), "date_start": rows[0]["date"], "date_end": rows[-1]["date"]}, f"Competition OHLCV close prices for {coin}")
            evidence.insert(0, market)
        market.content["prices"] = prices
        market.content["volumes"] = [row["volume"] for row in rows]
        market.content["dates"] = [row["date"] for row in rows]
    if history_path:
        evidence.append(_price_history_evidence(coin, history_path))
    plan_dict = plan_to_dict(plan)
    result = {
        "coin": coin.upper(), "question": question,
        "research_plan": plan_dict, "planning": planning_log,
        "summary": f"{coin.upper()} has a positive market signal; news, on-chain and social evidence require further validation.",
        "signals": {item.data_type: item.content.get("sentiment", "available") for item in evidence},
        "indicators": market.content if market is not None else {},
        "risk_factors": [],
        "evidence_ids": [item.evidence_id for item in evidence],
        "disclaimer": "For research demonstration only; not investment advice.",
        "collection_log": collection_log,
    }
    signals = _signal_inventory(result, evidence)
    result["stance"] = _market_stance(signals)
    result["risk_factors"] = _dynamic_risk_factors(result, evidence, signals)
    llm_status = "offline_fallback"
    llm_info = llm_runtime_info()
    llm_seconds_remaining = round(reasoning_deadline - time.monotonic(), 1)
    if use_llm and llm_seconds_remaining < _LLM_MIN_SECONDS:
        # Skip rather than start: a call begun with 3 seconds left burns the budget and still fails.
        llm_status = "fallback:TimeBudgetExceeded"
        result["reasoning"] = _offline_reasoning(
            result, evidence,
            f"時間預算僅剩 {llm_seconds_remaining} 秒（低於 LLM 最低需求 {_LLM_MIN_SECONDS} 秒），watchdog 強制切換至離線推理",
        )
    elif use_llm:
        try:
            llm_result = analyze_with_llm(coin, question, _llm_evidence_payload(evidence))
            unknown = set(llm_result.get("cited_evidence_ids", [])) - set(result["evidence_ids"])
            if unknown:
                raise ValueError(f"LLM cited unknown evidence IDs: {sorted(unknown)}")
            result["reasoning"] = llm_result
            llm_status = "success"
        except Exception as error:
            llm_status = f"fallback:{type(error).__name__}"
            result["reasoning"] = _offline_reasoning(result, evidence, f"LLM failed: {type(error).__name__}")
    else:
        result["reasoning"] = _offline_reasoning(result, evidence)
    phase_timings["reasoning_ms"] = round((time.monotonic() - reasoning_started) * 1000, 1)

    # Phase 3: audit. The critic sees the finished analysis and its evidence, and may only annotate
    # and lower confidence -- it never rewrites the conclusion, because a critic that edits the work
    # is just a second analyst and the reader loses the independent check.
    critic_started = time.monotonic()
    critic_deadline = min(deadline, critic_started + CRITIC_PHASE_SECONDS)
    critic_status, critique = "skipped:disabled", None
    if use_llm:
        critic_seconds_remaining = round(critic_deadline - time.monotonic(), 1)
        if critic_seconds_remaining < _CRITIC_MIN_SECONDS:
            critic_status = "skipped:TimeBudgetExceeded"
        else:
            try:
                # Validate before committing to `critique`: a critique that cites evidence which
                # does not exist is itself a hallucination, and accepting it would let it lower
                # confidence and display invented findings.
                candidate = critique_with_llm(coin, question, result, _llm_evidence_payload(evidence))
                unknown = {
                    finding.get("evidence_id") for finding in candidate.get("findings", [])
                } - set(result["evidence_ids"]) - {"N/A", "", None}
                if unknown:
                    raise ValueError(f"Critic cited unknown evidence IDs: {sorted(unknown)}")
                critique = candidate
                critic_status = "success"
            except Exception as error:
                critic_status = f"fallback:{type(error).__name__}"
    if critique:
        adjustment = float(critique.get("confidence_adjustment") or 0)
        original = float(result["reasoning"].get("confidence", 0) or 0)
        adjusted = max(0.0, min(1.0, original + adjustment))
        critique["original_confidence"] = round(original, 4)
        critique["adjusted_confidence"] = round(adjusted, 4)
        result["reasoning"]["confidence"] = round(adjusted, 4)
    result["critique"] = critique
    phase_timings["critic_ms"] = round((time.monotonic() - critic_started) * 1000, 1)

    validation_errors = validate_evidence(evidence, result["evidence_ids"])
    if validation_errors:
        raise ValueError("Evidence validation failed: " + "; ".join(validation_errors))
    output_dir.mkdir(parents=True, exist_ok=True)
    sources = "\n".join(f"- {item.evidence_id}: [{item.source}]({item.source_url}) (reliability: {item.reliability_score})" for item in evidence)
    reasoning = result["reasoning"]
    # Series belong in the charts, not the prose report: dumping the raw `dates`/`prices` arrays
    # here buried the readable indicators under hundreds of lines.
    display_indicators = {key: value for key, value in result["indicators"].items() if key not in ("prices", "volumes", "dates") and value is not None}
    indicator_lines = "\n".join(
        f"- {key}: " + ", ".join(f"{sub_key}={sub_value}" for sub_key, sub_value in value.items()) if isinstance(value, dict) else f"- {key}: {value}"
        for key, value in display_indicators.items()
    )
    stance = result["stance"]
    stance_block = (
        f"**{stance['label']}（{stance['label_en']}）**　信心 {reasoning['confidence']}\n\n"
        f"- 依據：{stance['basis']}\n"
        f"- 訊號權重：多方 {stance['bull_weight']} / 空方 {stance['bear_weight']}（共 {stance['signal_count']} 項訊號）\n"
        + "".join(f"- 主要推力：{driver['text']}（{driver['evidence_id']}）\n" for driver in stance["drivers"])
    )
    critique_block = ""
    if critique:
        verdict_labels = {"pass": "通過", "concerns": "有需注意之處", "fail": "結論不被證據支撐"}
        findings = "\n".join(
            f"- [{finding['severity'].upper()}／{finding['category']}] {finding['issue']}"
            f"（針對：{finding['claim'][:60]}｜證據：{finding['evidence_id']}）"
            for finding in critique.get("findings", [])
        ) or "- 稽核未發現實質問題"
        critique_block = (
            f"\n## Critic Review\n**{verdict_labels.get(critique['verdict'], critique['verdict'])}**"
            f"　信心 {critique['original_confidence']} → {critique['adjusted_confidence']}\n\n"
            f"{critique['summary']}\n\n{findings}\n"
        )
    history = next((item for item in evidence if item.data_type == "price_history"), None)
    history_block = ""
    if history is not None:
        history_block = "\n## Long-horizon Context\n" + "\n".join(
            f"- {label}（{window['date_start']} → {window['date_end']}）: 報酬 {window['return_pct']}%"
            f"／年化波動 {window['volatility_annualised_pct']}%"
            f"／最大回撤 {window['max_drawdown_pct']}%"
            f"／價格位於區間 {window['percentile_in_range']}% 分位"
            for label, window in history.content["windows"].items()
        ) + f"\n- 資料來源：{history.evidence_id}（{history.content['rows']} 個交易日）\n"
    (output_dir / "report.md").write_text(f"# {result['coin']} Market Research\n\n## Question\n{question}\n\n## Stance\n{stance_block}\n## Market Judgment\n{reasoning['market_judgment']}\n{history_block}\n## Facts\n" + "\n".join(f"- {x}" for x in reasoning["facts"]) + "\n\n## Inferences\n" + "\n".join(f"- {x}" for x in reasoning["inferences"]) + f"\n\n## Conclusion\n{reasoning['conclusion']}\n{critique_block}\n## Confidence\n{reasoning['confidence']}\n\n## Indicators\n" + indicator_lines + "\n\n## Counter Evidence\n" + "\n".join(f"- {x}" for x in reasoning["counter_evidence"]) + "\n\n## Evidence Sources\n" + sources + "\n\n## Next Observations\n" + "\n".join(f"- {x}" for x in reasoning["observation_points"]) + "\n\n## Risks and Limitations\n" + "\n".join(f"- {x}" for x in result["risk_factors"]) + "\n\n_This is research support, not investment advice._\n", encoding="utf-8")
    (output_dir / "evidence.json").write_text(json.dumps([asdict(item) for item in evidence], indent=2), encoding="utf-8")
    (output_dir / "research_plan.json").write_text(
        json.dumps(plan_dict, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    skipped = [entry for entry in collection_log if "skipped" in entry]
    (output_dir / "execution_log.json").write_text(json.dumps({
        "status": "success",
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
        },
        "collection_agents": agent_report,
        "steps": [
            {"name": "parse_input", "status": "success"},
            _planner_step(plan, planning_log, llm_info),
            {"name": "collect_evidence", "status": "degraded" if skipped else "success", "details": collection_log},
            {"name": "calculate_indicators", "status": "success"},
            {"name": "validate_evidence", "status": "success", "error_count": 0},
            {"name": "llm_reasoning", "status": llm_status, **llm_info},
            {"name": "critic_review", "status": critic_status, **llm_info},
            {"name": "generate_report", "status": "success"},
        ],
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
    }, indent=2), encoding="utf-8")
    return result


def run_comparison(coin_a: str, coin_b: str, question: str, output_dir: Path, live: bool = False,
                   use_llm: bool = False, time_budget_seconds: float = DEFAULT_TIME_BUDGET_SECONDS,
                   planner_client: object | None = None) -> dict:
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
    deadline = time.monotonic() + time_budget_seconds
    output_dir.mkdir(parents=True, exist_ok=True)

    # One plan for the pair, not one per leg: a comparison is only fair if both coins are read over
    # the same time window, the same as-of and the same comparison dimensions.
    shared_plan, shared_planning_log = _plan_research(
        question, [coin_a, coin_b], use_llm=use_llm, client=planner_client, deadline=deadline,
    )
    shared_plan_dict = plan_to_dict(shared_plan)
    (output_dir / "research_plan.json").write_text(
        json.dumps(shared_plan_dict, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    results, profiles = {}, {}
    for coin in (coin_a, coin_b):
        leg_dir = output_dir / coin
        results[coin] = run(coin, question, leg_dir, live=live, use_llm=use_llm,
                            time_budget_seconds=time_budget_seconds, deadline=deadline,
                            research_plan=shared_plan)
        evidence = json.loads((leg_dir / "evidence.json").read_text(encoding="utf-8"))
        profiles[coin] = build_profile(coin, evidence)

    comparison = compare_profiles(profiles[coin_a], profiles[coin_b])
    markdown = comparison_markdown(comparison, profiles[coin_a], profiles[coin_b])
    (output_dir / "comparison.md").write_text(
        f"# {coin_a} vs {coin_b} 比較研究\n\n## Question\n{question}\n\n{markdown}\n"
        f"## 個別幣種完整報告\n- {coin_a}: `{coin_a}/report.md`\n- {coin_b}: `{coin_b}/report.md`\n\n"
        "_This is research support, not investment advice._\n",
        encoding="utf-8",
    )
    payload = {
        "mode": "comparison", "coins": [coin_a, coin_b], "question": question,
        "research_plan": shared_plan_dict, "planning": shared_planning_log,
        "results": results, "profiles": profiles, "comparison": comparison,
        "markdown": markdown,
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
    }
    (output_dir / "comparison.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload
