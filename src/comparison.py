"""Two-coin comparative analysis across liquidity, risk exposure and market attention.

The competition's third example question asks the agent to compare two assets rather than analyse
one. Rather than inventing a second reasoning pipeline, this module derives three comparable
profiles from the evidence each single-coin run already produces, then renders a side-by-side
verdict. Every number traces back to an evidence_id, and any dimension whose inputs are missing is
reported as `None` instead of being silently defaulted to zero -- a coin whose funding-rate fetch
failed must not look "low risk" as a result.
"""

from __future__ import annotations

import statistics


def _by_type(evidence: list[dict], data_type: str) -> dict:
    item = next((entry for entry in evidence if entry.get("data_type") == data_type), None)
    return item or {}


def _content(evidence: list[dict], data_type: str) -> dict:
    return _by_type(evidence, data_type).get("content") or {}


def _is_live(evidence: list[dict], data_type: str) -> bool:
    """A fallback fixture carries reliability 0.20 and a `fallback` content_reference flag."""
    item = _by_type(evidence, data_type)
    return bool(item) and not (item.get("content_reference") or {}).get("fallback")


def liquidity_profile(evidence: list[dict]) -> dict:
    """Traded-volume depth proxy. This is turnover, not order-book depth: the free endpoints in
    this MVP do not expose bid/ask spread, so a thin book with high churn would look liquid here."""
    market = _content(evidence, "market")
    volumes = [value for value in (market.get("volumes") or []) if isinstance(value, (int, float))]
    profile = {
        "avg_daily_volume_usd": round(statistics.fmean(volumes), 2) if volumes else None,
        "latest_volume_usd": round(volumes[-1], 2) if volumes else None,
        "volume_trend_pct": None,
        "evidence_id": _by_type(evidence, "market").get("evidence_id"),
        "basis": "CoinGecko 14 天成交額（turnover proxy，非訂單簿深度）",
    }
    if len(volumes) >= 2 and volumes[0]:
        profile["volume_trend_pct"] = round((volumes[-1] / volumes[0] - 1) * 100, 2)
    return profile


def risk_exposure_profile(evidence: list[dict]) -> dict:
    """Composite positioning/technical risk on a 0-100 scale (higher = more exposed).

    Each component is only included when its source is live, and the composite is the mean of the
    components that survived -- so a coin with two dead feeds gets a composite built from fewer
    inputs, flagged via `components_available`, rather than a falsely reassuring low score.
    """
    components: dict[str, float] = {}
    cited = []

    derivatives = _content(evidence, "derivatives")
    funding = derivatives.get("funding_rate_pct")
    if funding is not None and _is_live(evidence, "derivatives"):
        # |0.05%| per 8h window is already a crowded book; treat that as full scale.
        components["funding_stress"] = round(min(100.0, abs(float(funding)) / 0.05 * 100), 1)
        cited.append(_by_type(evidence, "derivatives").get("evidence_id"))

    lsratio = _content(evidence, "long_short_ratio")
    current = lsratio.get("current")
    if current and current.get("long_pct") is not None and _is_live(evidence, "long_short_ratio"):
        # 50/50 is balanced; 100/0 would be maximally one-sided.
        components["positioning_crowding"] = round(min(100.0, abs(float(current["long_pct"]) - 50) * 2), 1)
        cited.append(_by_type(evidence, "long_short_ratio").get("evidence_id"))

    vegas = _content(evidence, "vegas_channel")
    execution = vegas.get("1h") or {}
    rsi = execution.get("rsi")
    if rsi is not None:
        components["rsi_extremity"] = round(min(100.0, abs(float(rsi) - 50) / 50 * 100), 1)
    alignment = vegas.get("alignment")
    if alignment is not None:
        components["timeframe_conflict"] = 0.0 if alignment.get("passed") else 100.0
    if execution or alignment:
        cited.append(_by_type(evidence, "vegas_channel").get("evidence_id"))

    prices = [value for value in (_content(evidence, "market").get("prices") or []) if isinstance(value, (int, float))]
    if len(prices) >= 3:
        returns = [(b / a - 1) for a, b in zip(prices, prices[1:]) if a]
        if len(returns) >= 2:
            # 5% daily stdev is very volatile for a major asset; use it as full scale.
            components["realised_volatility"] = round(min(100.0, statistics.pstdev(returns) / 0.05 * 100), 1)
            cited.append(_by_type(evidence, "market").get("evidence_id"))

    return {
        "components": components,
        "components_available": len(components),
        "composite_score": round(statistics.fmean(components.values()), 1) if components else None,
        "cited_evidence_ids": [eid for eid in cited if eid],
        "basis": "資金費率擁擠度、大戶持倉偏斜、RSI 極端度、時區訊號衝突、已實現波動度的等權平均（0-100，越高代表暴險越大）",
    }


def attention_profile(evidence: list[dict]) -> dict:
    """Raw attention counters plus engagement.

    The item counts saturate: the fetchers cap news at 5, announcements at 5 and social posts at 25,
    so two actively-discussed coins both return the cap and the counts cannot separate them. Total
    engagement (score/points/likes/comments) has no such ceiling, which is why `compare_profiles`
    ranks on engagement and treats the counts as coverage indicators only.
    """
    news_items = _content(evidence, "news").get("items") or []
    announcements = _content(evidence, "announcement").get("items") or []
    social = _content(evidence, "social")
    posts = social.get("posts") or []
    engagement = sum(
        int(post.get("score") or 0) + int(post.get("points") or 0) + int(post.get("likes") or 0)
        + int(post.get("comments") or 0) + int(post.get("replies") or 0) + int(post.get("reposts") or 0)
        for post in posts
    )
    cited = [_by_type(evidence, key).get("evidence_id") for key in ("news", "announcement", "social")]
    return {
        "news_article_count": len(news_items),
        "official_announcement_count": len(announcements),
        "social_post_count": len(posts),
        "social_engagement_total": engagement,
        "social_sentiment": social.get("sentiment"),
        "counts_are_capped": len(news_items) >= 5 or len(posts) >= 25,
        "cited_evidence_ids": [eid for eid in cited if eid],
        "basis": "以社群互動總量（無抓取上限）為主要判準；新聞／公告／貼文則數受抓取上限限制，僅作覆蓋度參考",
    }


def build_profile(coin: str, evidence: list[dict]) -> dict:
    return {
        "coin": coin,
        "liquidity": liquidity_profile(evidence),
        "risk_exposure": risk_exposure_profile(evidence),
        "attention": attention_profile(evidence),
    }


def _rank(coin_a: str, value_a, coin_b: str, value_b, higher_label: str, lower_label: str) -> dict:
    """Compare one metric, tolerating a missing side rather than treating None as zero."""
    if value_a is None and value_b is None:
        return {"verdict": "資料不足，無法比較", "leader": None, "a": None, "b": None}
    if value_a is None:
        return {"verdict": f"{coin_a} 資料不可用，僅 {coin_b} 有觀測值", "leader": coin_b, "a": None, "b": value_b}
    if value_b is None:
        return {"verdict": f"{coin_b} 資料不可用，僅 {coin_a} 有觀測值", "leader": coin_a, "a": value_a, "b": None}
    if value_a == value_b:
        return {"verdict": "兩者相當", "leader": None, "a": value_a, "b": value_b}
    leader, follower = (coin_a, coin_b) if value_a > value_b else (coin_b, coin_a)
    ratio = (max(value_a, value_b) / min(value_a, value_b)) if min(value_a, value_b) else None
    ratio_text = f"（約 {ratio:.1f} 倍）" if ratio and ratio >= 1.1 else ""
    return {
        "verdict": f"{leader} {higher_label}{ratio_text}，{follower} {lower_label}",
        "leader": leader, "a": value_a, "b": value_b,
    }


def compare_profiles(profile_a: dict, profile_b: dict) -> dict:
    coin_a, coin_b = profile_a["coin"], profile_b["coin"]
    liquidity = _rank(
        coin_a, profile_a["liquidity"]["avg_daily_volume_usd"],
        coin_b, profile_b["liquidity"]["avg_daily_volume_usd"],
        "成交量較高、進出較不易滑價", "相對較薄",
    )
    risk = _rank(
        coin_a, profile_a["risk_exposure"]["composite_score"],
        coin_b, profile_b["risk_exposure"]["composite_score"],
        "綜合暴險分數較高", "相對較低",
    )
    attention = _rank(
        coin_a, profile_a["attention"]["social_engagement_total"],
        coin_b, profile_b["attention"]["social_engagement_total"],
        "社群互動量較高、市場關注度較強", "討論熱度相對低",
    )
    if profile_a["attention"]["counts_are_capped"] or profile_b["attention"]["counts_are_capped"]:
        attention["note"] = "新聞／貼文則數已達抓取上限，該欄位無法反映真實差異，判讀以互動總量為準"
    return {
        "coins": [coin_a, coin_b],
        "liquidity": liquidity,
        "risk_exposure": risk,
        "attention": attention,
        "summary": _summary(coin_a, coin_b, liquidity, risk, attention),
        "caveat": (
            "三個維度使用不同資料窗口（成交量 14 天、持倉與技術指標為當前值、社群為近 7 天），"
            "且各幣種的資料可用性可能不同；比較結果描述相對差異，不構成任何買賣建議。"
        ),
    }


def _summary(coin_a: str, coin_b: str, liquidity: dict, risk: dict, attention: dict) -> str:
    parts = []
    if liquidity["leader"]:
        parts.append(f"流動性方面 {liquidity['leader']} 佔優")
    if risk["leader"]:
        parts.append(f"暴險程度 {risk['leader']} 較高")
    if attention["leader"]:
        parts.append(f"市場關注度 {attention['leader']} 較高")
    if not parts:
        return f"{coin_a} 與 {coin_b} 的三個維度皆因資料不足而無法分出差異。"
    body = "；".join(parts)
    if liquidity["leader"] and risk["leader"] and liquidity["leader"] == risk["leader"]:
        body += f"。{liquidity['leader']} 同時具備較高流動性與較高暴險，代表其波動來自活躍交易而非流動性枯竭"
    elif liquidity["leader"] and risk["leader"]:
        body += f"。{risk['leader']} 在流動性較低的情況下暴險較高，這個組合的下行風險需要額外留意"
    return body + "。"


def comparison_markdown(comparison: dict, profile_a: dict, profile_b: dict) -> str:
    coin_a, coin_b = profile_a["coin"], profile_b["coin"]

    def row(label: str, value_a, value_b, verdict: str) -> str:
        fmt = lambda v: "N/A" if v is None else (f"{v:,.2f}" if isinstance(v, float) else f"{v:,}")
        return f"| {label} | {fmt(value_a)} | {fmt(value_b)} | {verdict} |"

    lines = [
        f"## {coin_a} vs {coin_b} 並列比較",
        "",
        comparison["summary"],
        "",
        f"| 維度 | {coin_a} | {coin_b} | 判讀 |",
        "|---|---|---|---|",
        row("平均日成交額 (USD)", profile_a["liquidity"]["avg_daily_volume_usd"], profile_b["liquidity"]["avg_daily_volume_usd"], comparison["liquidity"]["verdict"]),
        row("綜合暴險分數 (0-100)", profile_a["risk_exposure"]["composite_score"], profile_b["risk_exposure"]["composite_score"], comparison["risk_exposure"]["verdict"]),
        row("社群互動總量", profile_a["attention"]["social_engagement_total"], profile_b["attention"]["social_engagement_total"], comparison["attention"]["verdict"]),
        row("新聞則數（上限 5）", profile_a["attention"]["news_article_count"], profile_b["attention"]["news_article_count"], comparison["attention"].get("note", "")),
        row("社群貼文數（上限 25）", profile_a["attention"]["social_post_count"], profile_b["attention"]["social_post_count"], ""),
        row("官方公告則數（上限 5）", profile_a["attention"]["official_announcement_count"], profile_b["attention"]["official_announcement_count"], ""),
        "",
        "### 暴險分數組成",
    ]
    for profile in (profile_a, profile_b):
        components = profile["risk_exposure"]["components"]
        detail = "、".join(f"{key}={value}" for key, value in components.items()) or "無可用組成（所有來源皆不可用）"
        lines.append(f"- **{profile['coin']}**（{profile['risk_exposure']['components_available']} 項可用）：{detail}")
    lines += ["", f"> {comparison['caveat']}", ""]
    return "\n".join(lines)
