"""Day 1: mock-data end-to-end MVP flow.

This deliberately avoids external APIs and LLM calls so the team can validate
contracts and integration on the first day.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class Evidence:
    evidence_id: str
    source: str
    source_url: str
    fetched_at: str
    data_type: str
    coin: str
    time_range: str
    content: dict
    reliability_score: float
    content_reference: dict = field(default_factory=dict)
    related_claim: str = ""

    def __post_init__(self):
        if not self.content_reference:
            self.content_reference = {"summary": self.content, "time_range": self.time_range}
        if not self.related_claim:
            self.related_claim = f"{self.coin} {self.data_type} evidence used in the market analysis"


# Per-coin scale factors so an offline demo still produces a differentiated comparison instead of a
# table of identical numbers. These are fixtures, not observations -- every record below is labelled
# "Mock*" and carries an example.com source_url so it can never be mistaken for live evidence.
_MOCK_SCALE = {"BTC": 6.0, "ETH": 3.0, "SOL": 1.4, "BNB": 1.1, "XRP": 0.8}


def mock_evidence(coin: str = "ETH") -> list[Evidence]:
    now = datetime.now(timezone.utc).isoformat()
    scale = _MOCK_SCALE.get(coin.upper(), 1.0)
    prices = [round(100 * scale * (1 + 0.006 * index), 2) for index in range(14)]
    volumes = [round(1_000_000_000 * scale * (1 + 0.02 * (index % 5)), 2) for index in range(14)]
    dates = [f"2026-01-{index + 1:02d}" for index in range(14)]
    return [
        Evidence(
            "EV-001", "MockMarket", "https://example.com/market", now,
            "market", coin, "14d",
            {"return_pct": 8.2, "rsi": 61.4, "start_price": prices[0], "end_price": prices[-1],
             "prices": prices, "volumes": volumes, "dates": dates}, 0.80
        ),
        Evidence(
            "EV-002", "MockNews", "https://example.com/news", now,
            "news", coin, "14d", {"headline": "Network activity remains elevated", "sentiment": "positive",
             "items": [{"title": f"{coin} network activity remains elevated", "url": "https://example.com/news/1", "published": ""},
                       {"title": f"{coin} developer activity steady week over week", "url": "https://example.com/news/2", "published": ""}]}, 0.60
        ),
        Evidence(
            "EV-003", "MockSocial", "https://example.com/social", now,
            "social", coin, "14d", {"sentiment": "mixed", "confidence": 0.45,
             "positive_terms": 3, "negative_terms": 3,
             "posts": [{"title": f"{coin} adoption thread", "url": "https://example.com/social/1", "score": 12,
                        "comments": 4, "matched_positive": ["adoption"], "matched_negative": []},
                       {"title": f"{coin} drop discussion", "url": "https://example.com/social/2", "score": 7,
                        "comments": 2, "matched_positive": [], "matched_negative": ["drop"]}]}, 0.35
        ),
        Evidence(
            "EV-004", "MockDerivatives", "https://example.com/derivatives", now,
            "derivatives", coin, "current", {"symbol": f"{coin}USDT", "funding_rate_pct": 0.008, "bias": "balanced"}, 0.75
        ),
        Evidence(
            "EV-005", "MockWhaleWatch", "https://example.com/whale", now,
            "whale", coin, "current", {"wallets": [
                {"label": "Demo Exchange Wallet", "address": "0xDEMO0000000000000000000000000000000000", "balance": 500000, "explorer_url": "https://example.com/whale"},
            ], "note": "Demo fixture only"}, 0.65
        ),
        Evidence(
            "EV-006", "MockVegasChannel", "https://example.com/vegas", now,
            "vegas_channel", coin, "4h trend / 1h execution", {
                "symbol": f"{coin}USDT", "trend_timeframe": "4h", "execution_timeframe": "1h",
                "4h": {"trend": "bullish_aligned", "tunnel_top": None, "tunnel_bottom": None, "in_tunnel": True, "rsi": 55.0, "volume_spike": False, "last_signal": None, "bars_since_signal": None},
                "1h": {"trend": "mixed", "tunnel_top": None, "tunnel_bottom": None, "in_tunnel": False, "rsi": 48.0, "volume_spike": False, "last_signal": None, "bars_since_signal": None},
                "alignment": {"passed": False, "4h_direction": "long", "1h_direction": "neutral", "note": "4h為多頭，1h為訊號不一(mixed)，方向不一致"},
            }, 0.70
        ),
        Evidence(
            "EV-007", "MockLongShortRatio", "https://example.com/long_short_ratio", now,
            "long_short_ratio", coin, "last 24x1h", {
                "symbol": f"{coin}USDT", "period": "1h",
                "history": [{"time": now, "long_pct": 55.0, "short_pct": 45.0, "ratio": 1.2222}],
                "current": {"time": now, "long_pct": 55.0, "short_pct": 45.0, "ratio": 1.2222},
                "bias": "long_dominant", "consistency_pct": 80.0,
            }, 0.70
        ),
        Evidence(
            "EV-008", "MockMacro", "https://example.com/macro", now,
            "macro", coin, "14d", {
                "fear_greed_value": 52, "fear_greed_classification": "Neutral",
                "fear_greed_history": [{"time": "2026-01-01", "value": 47, "classification": "Neutral"},
                                       {"time": "2026-01-14", "value": 52, "classification": "Neutral"}],
                "fear_greed_change_14d": 5, "risk_appetite_direction": "risk_appetite_stable",
                "scope": "crypto market-wide (not coin-specific)",
                "fed_releases": [{"title": "Federal Reserve issues FOMC statement", "url": "https://example.com/fomc", "published": ""}],
                "latest_fomc_release": {"title": "Federal Reserve issues FOMC statement", "url": "https://example.com/fomc", "published": ""},
                "fed_status": "available",
            }, 0.80
        ),
        Evidence(
            "EV-009", "MockOfficialAnnouncements", "https://example.com/announcement", now,
            "announcement", coin, "recent", {
                "items": [{"title": f"{coin} protocol update published", "url": "https://example.com/announcement/1", "published": ""}],
                "first_party": True, "note": "Demo fixture only",
            }, 0.85
        ),
    ]


def run(coin: str, question: str, output_dir: Path) -> dict:
    evidence = mock_evidence(coin)
    report = {
        "coin": coin,
        "question": question,
        "summary": f"{coin} shows a moderately positive 14-day market signal, with mixed social sentiment.",
        "signals": {"market": "positive", "news": "positive", "social": "mixed", "consistency": "partially_consistent"},
        "risk_factors": ["Mock data only", "Social sentiment has low reliability"],
        "evidence_ids": [item.evidence_id for item in evidence],
        "disclaimer": "For research demonstration only; not investment advice.",
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.md").write_text(
        f"# {coin} Market Research\n\n## Question\n{question}\n\n## Summary\n{report['summary']}\n\n## Signals\n"
        + "\n".join(f"- {key}: {value}" for key, value in report["signals"].items())
        + "\n\n## Risks\n" + "\n".join(f"- {risk}" for risk in report["risk_factors"])
        + f"\n\n## Evidence\n{', '.join(report['evidence_ids'])}\n\n_{report['disclaimer']}_\n",
        encoding="utf-8",
    )
    (output_dir / "evidence.json").write_text(json.dumps([asdict(item) for item in evidence], indent=2), encoding="utf-8")
    (output_dir / "execution_log.json").write_text(json.dumps({"status": "success", "steps": ["parse_input", "load_mock_evidence", "generate_report"]}, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    run("ETH", "近期上漲的主要原因是什麼？", Path(__file__).parents[1] / "outputs")
    print("Day 1 MVP completed")
