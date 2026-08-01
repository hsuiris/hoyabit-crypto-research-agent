"""Day 2 data adapters with safe offline fallbacks."""

from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from html import unescape
from urllib.parse import quote, urlparse
from urllib.robotparser import RobotFileParser
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from .credibility import lineage_id_for, parse_timestamp
from .schemas import VERIFICATION_STATUS_UNAVAILABLE
from .day1_mvp import Evidence, mock_evidence
from .vegas_strategy import analyze_vegas_channel, check_timeframe_alignment


COIN_IDS = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "BNB": "binancecoin", "XRP": "ripple"}
FUTURES_SYMBOLS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT", "BNB": "BNBUSDT", "XRP": "XRPUSDT"}

# Curated large accounts, each cross-checked against a live balance before being added here. This is
# not a whale-discovery feed: it re-reads a fixed address list, so it can show a known holder moving
# size but cannot find a new one.
#
# BTC/ETH/BNB entries carry exchange labels because those addresses are among the most widely
# documented on-chain (Etherscan/BscScan public tags, cross-checked 2026-07-28).
#
# SOL/XRP entries deliberately do NOT name an owner. Balances were verified on-chain 2026-08-01, but
# the ownership attribution could not be confirmed against a first-party source -- and a balance
# proves only that an account holds size, never who controls it (see the Observable Fact vs
# Source Statement split in .kiro/steering/evidence-confidence-standards.md). Naming a likely
# exchange here would put an unverifiable claim into the evidence table, so the label states what is
# actually known. The credibility engine's `unverifiable_entity_attribution` cap applies regardless.
KNOWN_WHALE_ADDRESSES = {
    "BTC": [
        {"label": "Binance Cold Wallet 1", "address": "34xp4vRoCGJym3xR7yCVPFHoCNxv4Twseo"},
        {"label": "Binance Cold Wallet 2", "address": "3M219KR5vEneNb47ewrPfWyb5jQ2DjxRP6"},
    ],
    "ETH": [
        {"label": "Binance 7", "address": "0xbe0eb53f46cd790cd13851d5eff43d12404d33e8"},
        {"label": "Binance Hot Wallet 20", "address": "0xf977814e90da44bfa03b6295a0616a897441acec"},
    ],
    "BNB": [
        {"label": "Binance 7 (BSC)", "address": "0xbe0eb53f46cd790cd13851d5eff43d12404d33e8"},
        {"label": "Binance Hot Wallet 20 (BSC)", "address": "0xf977814e90da44bfa03b6295a0616a897441acec"},
    ],
    "SOL": [
        # 10,755,443 SOL 與 693,532 SOL（實測 2026-08-01）。
        {"label": "SOL 大額帳戶 1（持有者未經第一方確認）",
         "address": "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"},
        {"label": "SOL 大額帳戶 2（持有者未經第一方確認）",
         "address": "5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9"},
    ],
    "XRP": [
        # 10,605,986 XRP 與 9,258,993 XRP（實測 2026-08-01）。
        {"label": "XRP 大額帳戶 1（持有者未經第一方確認）",
         "address": "rLW9gnQo7BQhU6igk5keqYnH3TVrCxGRzm"},
        {"label": "XRP 大額帳戶 2（持有者未經第一方確認）",
         "address": "rDsbeomae4FXwgQTJp9Rs64Qg9vDiTCdBv"},
    ],
}

WHALE_EXPLORER_URL = {
    "BTC": "https://www.blockchain.com/explorer/addresses/btc/{address}",
    "ETH": "https://etherscan.io/address/{address}",
    "BNB": "https://bscscan.com/address/{address}",
    "SOL": "https://solscan.io/account/{address}",
    "XRP": "https://livenet.xrpl.org/accounts/{address}",
}

# Official project feeds, probed 2026-07-31 and re-probed 2026-08-01. ETH/BTC/SOL publish a real
# first-party feed on their own domain.
#
# Ripple and BNB Chain still expose no working blog RSS/Atom endpoint (ripple.com/insights/feed is
# 404, xrpl.org has no feed and no autodiscovery tag, bnbchain.org/en/blog/feed serves HTML, and the
# BNB Chain Medium account at medium.com/feed/@bnbchain is a real feed but has not published since
# 2021-05). What both projects *do* publish first-hand is their reference-client release feed on
# GitHub, which is genuinely first-party and independently verifiable.
#
# That feed is narrower than a general announcement channel -- it carries protocol/client releases,
# not partnerships or listings -- so the evidence description says so rather than implying the feed
# covers every official announcement. The domain-restricted Google News feed stays as the second
# entry: if a release feed is quiet, syndication still yields something, and it is scored lower and
# labelled as non-first-party so the distinction stays visible in the evidence table.
OFFICIAL_FEEDS = {
    "ETH": [("Ethereum Foundation Blog", "https://blog.ethereum.org/en/feed.xml", True)],
    "BTC": [("Bitcoin Optech Newsletter", "https://bitcoinops.org/feed.xml", True)],
    "SOL": [("Solana Official News", "https://solana.com/news/rss.xml", True)],
    "XRP": [("XRP Ledger Foundation rippled releases",
             "https://github.com/XRPLF/rippled/releases.atom", True),
            ("Google News (restricted to ripple.com / xrpl.org)",
             "https://news.google.com/rss/search?q=" + quote("site:ripple.com OR site:xrpl.org"), False)],
    "BNB": [("BNB Chain bsc client releases",
             "https://github.com/bnb-chain/bsc/releases.atom", True),
            ("Google News (restricted to bnbchain.org / binance.com announcements)",
             "https://news.google.com/rss/search?q=" + quote("site:bnbchain.org OR site:binance.com/en/support/announcement"), False)],
}

# 只有這兩幣的第一方來源是「參考客戶端發布」而非一般公告頻道；描述必須說清楚，否則讀者會
# 以為看到的是完整官方公告（違反 Source Statement 不得被放大成 Fact 的分層規則）。
RELEASE_FEED_COINS = frozenset({"XRP", "BNB"})

ATOM_NS = "{http://www.w3.org/2005/Atom}"


# ---------------------------------------------------------------------------------------------
# T3: credibility metadata
#
# The data layer supplies the *metadata* the credibility engine needs and nothing else: which
# registry category a source belongs to, when the data itself is from (as opposed to when we
# fetched it), and which news items trace back to the same story. No scoring happens here --
# `src/credibility.py` owns the arithmetic and `src/orchestrator.py` owns writing scores back, so
# an adapter can never talk its own reliability up.
# ---------------------------------------------------------------------------------------------

# data_type -> config/source_registry.json category. Chosen by *how the data is obtained*, which is
# what the registry's quality/traceability/freshness baselines actually describe:
#   whale/onchain    both read chain state from a public node or block explorer API
#   vegas_channel    an indicator computed from Binance spot klines, i.e. market data
#   tvl              DefiLlama's public market-data API (documented endpoint, snapshot semantics)
#   news             Google News + publisher feeds are syndication, not first-party statements
#   price_history    the repository's own daily CSV
DATA_TYPE_SOURCE_TYPES = {
    "market": "market_api",
    "price_history": "local_csv",
    "news": "secondary_media",
    "announcement": "official_announcement",
    "macro": "macro_api",
    "onchain": "blockchain_raw",
    "whale": "blockchain_raw",
    "social": "social_public",
    "derivatives": "derivatives_api",
    "long_short_ratio": "derivatives_api",
    "vegas_channel": "market_api",
    "tvl": "market_api",
}

# A degraded record keeps its place in the evidence list so the reader can see what was missing, but
# its status has to say so. "unavailable" = we expected this source and could not get it (here);
# "fallback" = it was a fixture from the start (set in `src/day1_mvp.py`).


def _epoch_or_timestamp(value):
    """Parse a feed/API timestamp, accepting epoch seconds as well as date strings."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    return parse_timestamp(value)


def _newest(values) -> str | None:
    """The most recent parseable timestamp in `values`, as ISO-8601, or None."""
    parsed = [item for item in (_epoch_or_timestamp(value) for value in values) if item is not None]
    return max(parsed).isoformat() if parsed else None


def _item_times(items, keys) -> str | None:
    return _newest([item.get(key) for item in items if isinstance(item, dict) for key in keys])


def _annotate_news_lineage(items: list) -> dict:
    """Group news items that trace back to the same story, in place.

    Twenty outlets running the same wire copy is one confirmation, not twenty. Each item gets the
    lineage ID the credibility engine would give it, plus a flag when an earlier item already
    covered that story; the returned summary is what the report and the claim graph should count.
    """
    groups: dict[str, list[str]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        lineage = lineage_id_for(item)
        item["lineage_id"] = lineage
        item["duplicate_of_lineage"] = lineage in groups
        groups.setdefault(lineage, []).append(item.get("url") or item.get("title") or "")
    return {
        "lineage_groups": dict(groups),
        "independent_story_count": len(groups),
        "duplicate_item_count": sum(len(value) - 1 for value in groups.values()),
    }


def _time_semantics(evidence: Evidence) -> tuple[str | None, str | None]:
    """(published_at, event_time) for one record: when the data itself is from.

    Freshness must not be inferred from `fetched_at` for anything published: a two-year-old article
    downloaded a second ago is not fresh. Snapshot-style sources (market, derivatives, chain state)
    legitimately treat the fetch time as the observation time and are declared as such in the
    registry, so they only need an explicit time when the payload carries one.
    """
    content = evidence.content if isinstance(evidence.content, dict) else {}
    data_type = evidence.data_type
    items = content.get("items") if isinstance(content.get("items"), list) else []
    posts = content.get("posts") if isinstance(content.get("posts"), list) else []
    history = content.get("history") if isinstance(content.get("history"), list) else []

    if data_type in {"news", "announcement"}:
        return _item_times(items, ("published", "updated", "published_at")), None
    if data_type == "social":
        return None, _item_times(posts, ("created_utc", "indexed_at", "created_at"))
    if data_type == "macro":
        macro_history = content.get("fear_greed_history")
        latest = macro_history[-1].get("time") if isinstance(macro_history, list) and macro_history else None
        published = _item_times(content.get("fed_releases") or [], ("published",))
        return published, _newest([latest])
    if data_type == "tvl":
        return None, _newest([history[-1].get("time")]) if history else None
    if data_type == "price_history":
        return None, _newest([content.get("date_end")])
    if data_type == "market":
        dates = content.get("dates")
        latest = dates[-1] if isinstance(dates, list) and dates else None
        return None, _newest([latest])
    return None, None


def stamp_credibility_metadata(evidence: Evidence) -> Evidence:
    """Fill in the T3 metadata for one record, in place. Idempotent; never assigns a score.

    An explicitly set `source_type` wins: a fixture stays a fixture even when it stands in for a
    live source, and the orchestrator's own CSV-backed records classify themselves.
    """
    if evidence.source_type in (None, "", "unknown"):
        evidence.source_type = DATA_TYPE_SOURCE_TYPES.get(evidence.data_type, "unknown")
    if evidence.data_type == "announcement" and evidence.source_type == "official_announcement":
        content = evidence.content if isinstance(evidence.content, dict) else {}
        if not content.get("first_party"):
            # A Google-News feed restricted to official domains is still syndication (see
            # OFFICIAL_FEEDS): scoring it as a first-party announcement would overstate it.
            evidence.source_type = "secondary_media"

    published_at, event_time = _time_semantics(evidence)
    if published_at and not evidence.published_at:
        evidence.published_at = published_at
    if event_time and not evidence.event_time:
        evidence.event_time = event_time

    if evidence.data_type in {"news", "announcement"} and isinstance(evidence.content, dict):
        items = evidence.content.get("items")
        if isinstance(items, list) and items:
            evidence.content.update(_annotate_news_lineage(items))

    if not evidence.source_lineage_id:
        evidence.source_lineage_id = lineage_id_for(evidence)
    return evidence


def _get_json(url: str, timeout: int = 8) -> dict:
    request = Request(url, headers={"User-Agent": "agent-team-mvp/0.1"})
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_text(url: str, timeout: int = 8) -> str:
    request = Request(url, headers={"User-Agent": "agent-team-mvp/0.1"})
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8").strip()


def _post_json(url: str, payload: dict, timeout: int = 8) -> dict:
    request = Request(url, data=json.dumps(payload).encode(), headers={"User-Agent": "agent-team-mvp/0.1", "Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_coingecko(coin: str = "ETH", days: int = 14) -> Evidence:
    """Fetch market data; raise on network failure so caller can fallback."""
    coin = coin.upper()
    coin_id = COIN_IDS[coin]
    url = f"https://api.coingecko.com/api/v3/coins/{quote(coin_id)}/market_chart?vs_currency=usd&days={days}"
    payload = _get_json(url)
    prices = payload.get("prices", [])
    if not prices:
        raise ValueError("CoinGecko returned no prices")
    first, last = prices[0][1], prices[-1][1]
    volumes = [point[1] for point in payload.get("total_volumes", [])]
    content = {
        "start_price": first, "end_price": last, "return_pct": round((last / first - 1) * 100, 2),
        "prices": [point[1] for point in prices],
        "dates": [datetime.fromtimestamp(point[0] / 1000, tz=timezone.utc).strftime("%Y-%m-%d") for point in prices],
    }
    if len(volumes) == len(prices):
        content["volumes"] = volumes
    return Evidence(
        "EV-MARKET-001", "CoinGecko", url, datetime.now(timezone.utc).isoformat(),
        "market", coin, f"{days}d", content, 0.90,
        {"endpoint": url, "query": {"vs_currency": "usd", "days": days}, "points": len(prices)}, f"{coin} {days}-day market return"
    )


# Funding-rate providers, in preference order. Binance stays first so nothing changes where it is
# reachable; the other two exist because Binance geo-blocks AWS us-west-2 (see aws/README.md) and the
# derivatives *domain* only has two collectors -- funding rate and long/short ratio -- both of which
# used to be Binance-only. One geo-block therefore erased an entire research domain from the report.
# Kraken Futures is a US-regulated venue and dYdX v4 is decentralised, so both answer from US IPs.
#
# Settlement intervals differ and MUST be normalised: Binance settles every 8 hours, Kraken and dYdX
# every hour. The +/-0.01% `bias` thresholds below are defined on an 8-hour basis, so feeding an
# hourly rate in unchanged would move the same market state a whole bias level just by switching
# provider. Every provider therefore returns an 8-hour-equivalent percentage.
# Cross-checked 2026-08-01 after normalisation: ETH read -0.01825% via Kraken and -0.01784% via dYdX.
KRAKEN_FUTURES_SYMBOLS = {"BTC": "PF_XBTUSD", "ETH": "PF_ETHUSD", "SOL": "PF_SOLUSD",
                          "BNB": "PF_BNBUSD", "XRP": "PF_XRPUSD"}
DYDX_MARKETS = {"BTC": "BTC-USD", "ETH": "ETH-USD", "SOL": "SOL-USD",
                "BNB": "BNB-USD", "XRP": "XRP-USD"}
# Binance quotes one rate per 8h window; Kraken and dYdX quote hourly.
FUNDING_SETTLEMENT_HOURS = 8


def _funding_from_binance(coin: str) -> dict:
    symbol = FUTURES_SYMBOLS[coin]
    url = f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={quote(symbol)}"
    payload = _get_json(url)
    rate = payload.get("lastFundingRate")
    if rate is None:
        raise ValueError("Binance returned no funding rate")
    # Already an 8-hour rate: no conversion, which is why this stays the preferred provider.
    return {"symbol": symbol, "url": url, "pct_8h": float(rate) * 100,
            "native_interval_hours": 8, "mark_price": payload.get("markPrice"),
            "next_funding_time": payload.get("nextFundingTime"), "raw_funding_rate": rate}


def _funding_from_kraken(coin: str) -> dict:
    symbol = KRAKEN_FUTURES_SYMBOLS[coin]
    url = "https://futures.kraken.com/derivatives/api/v3/tickers"
    payload = _get_json(url)
    ticker = next((item for item in payload.get("tickers") or []
                   if item.get("symbol") == symbol), None)
    if ticker is None:
        raise ValueError(f"Kraken Futures has no ticker for {symbol}")
    rate, mark_price = ticker.get("fundingRate"), ticker.get("markPrice")
    if rate is None or not mark_price:
        raise ValueError(f"Kraken Futures returned no funding rate for {symbol}")
    # Kraken quotes funding in quote currency per contract, so the comparable relative rate is
    # `fundingRate / markPrice` -- an absolute figure would scale with the coin's price level.
    hourly = float(rate) / float(mark_price)
    return {"symbol": symbol, "url": url, "pct_8h": hourly * FUNDING_SETTLEMENT_HOURS * 100,
            "native_interval_hours": 1, "mark_price": mark_price,
            "next_funding_time": None, "raw_funding_rate": rate}


def _funding_from_dydx(coin: str) -> dict:
    market = DYDX_MARKETS[coin]
    url = f"https://indexer.dydx.trade/v4/perpetualMarkets?ticker={quote(market)}"
    payload = _get_json(url)
    entry = (payload.get("markets") or {}).get(market)
    if not entry:
        raise ValueError(f"dYdX has no market for {market}")
    rate = entry.get("nextFundingRate")
    if rate is None:
        raise ValueError(f"dYdX returned no funding rate for {market}")
    return {"symbol": market, "url": url,
            "pct_8h": float(rate) * FUNDING_SETTLEMENT_HOURS * 100,
            "native_interval_hours": 1, "mark_price": entry.get("oraclePrice"),
            "next_funding_time": None, "raw_funding_rate": rate}


FUNDING_RATE_PROVIDERS = (
    ("Binance Futures", _funding_from_binance),
    ("Kraken Futures", _funding_from_kraken),
    ("dYdX v4", _funding_from_dydx),
)


def fetch_funding_rate(coin: str = "ETH") -> Evidence:
    """Fetch the current perpetual-futures funding rate, trying each provider in turn.

    A positive rate means longs pay shorts (long side is crowded / paying a premium to stay open);
    a negative rate means shorts pay longs (short side is crowded). Magnitude, not just sign, matters:
    most of the time funding sits within roughly +/-0.01% per 8h window.

    Falling through to a later provider is **not** a degradation: the data is still live and
    first-hand, just from another venue. The evidence records which venue answered and what the
    venue's native settlement interval was, so a reader can reproduce the number.
    """
    coin = coin.upper()
    errors = []
    for provider_name, provider in FUNDING_RATE_PROVIDERS:
        try:
            quote_data = provider(coin)
        except Exception as error:  # 換下一個交易所，全部失敗才讓 collector 降級
            errors.append(f"{provider_name}: {type(error).__name__}")
            continue

        funding_rate_pct = round(quote_data["pct_8h"], 4)
        if funding_rate_pct > 0.01:
            bias = "long_crowded"
        elif funding_rate_pct < -0.01:
            bias = "short_crowded"
        else:
            bias = "balanced"
        url = quote_data["url"]
        return Evidence(
            f"EV-DERIV-{coin}-001", provider_name, url, datetime.now(timezone.utc).isoformat(),
            "derivatives", coin, "current", {
                "symbol": quote_data["symbol"], "funding_rate_pct": funding_rate_pct, "bias": bias,
                "mark_price": quote_data["mark_price"],
                "next_funding_time": quote_data["next_funding_time"],
                "provider": provider_name,
                # 讀者要能判斷這個百分比的基準，否則無法與其他來源比較。
                "funding_interval_hours": FUNDING_SETTLEMENT_HOURS,
                "provider_native_interval_hours": quote_data["native_interval_hours"],
                "providers_tried": errors or None,
                # 資金費率是「該交易所的」市場狀態，不是全市場常數。實測 2026-08-01 ETH 在
                # Binance 為 +0.00568%（balanced）、在 Kraken 為 -0.01824%（short_crowded）——
                # 換了交易所 bias 可能不同。因此本欄位必須連同 provider 一起解讀。
                "scope_note": f"本費率為 {provider_name} 單一交易所的永續合約報價，"
                              "不同交易所的費率與擁擠方向可能相反，不可視為全市場共識",
            }, 0.75,
            {"endpoint": url, "symbol": quote_data["symbol"],
             "raw_funding_rate": quote_data["raw_funding_rate"],
             "provider": provider_name,
             "normalised_to_hours": FUNDING_SETTLEMENT_HOURS,
             "provider_native_interval_hours": quote_data["native_interval_hours"]},
            f"{coin} perpetual-futures funding rate and long/short crowding bias"
        )
    raise ValueError("All funding-rate providers failed: " + "; ".join(errors))


def fetch_whale_wallets(coin: str = "ETH") -> Evidence:
    """Re-check balances for a small curated list of publicly known large exchange wallets.

    This is a free/no-key proxy for whale tracking, not a live whale-discovery feed: it does not
    find new large holders, it re-queries a fixed, source-cited address list against free public
    balance endpoints (blockchain.info for BTC, public JSON-RPC for ETH/BNB/SOL/XRP).

    Each chain reports balances in its own base unit, so the divisor differs per chain: satoshi
    (1e8), wei (1e18), lamports (1e9) and drops (1e6). Sharing one divisor would silently misreport
    balances by orders of magnitude.
    """
    coin = coin.upper()
    addresses = KNOWN_WHALE_ADDRESSES.get(coin)
    if not addresses:
        raise NotImplementedError(f"Whale wallet tracking is not yet supported for {coin}")

    wallets = []
    if coin == "BTC":
        source_url = "https://blockchain.info/balance?active=" + "|".join(item["address"] for item in addresses)
        payload = _get_json(source_url)
        for item in addresses:
            balance = payload.get(item["address"], {}).get("final_balance")
            if balance is None:
                raise ValueError(f"blockchain.info returned no balance for {item['address']}")
            wallets.append({**item, "balance": round(balance / 1e8, 4), "explorer_url": WHALE_EXPLORER_URL["BTC"].format(address=item["address"])})
    elif coin == "SOL":
        source_url = "https://api.mainnet-beta.solana.com"
        for item in addresses:
            result = _post_json(source_url, {"jsonrpc": "2.0", "id": 1, "method": "getBalance",
                                             "params": [item["address"]]})
            lamports = (result.get("result") or {}).get("value")
            if lamports is None:
                raise ValueError(f"{source_url} returned no balance for {item['address']}")
            wallets.append({**item, "balance": round(int(lamports) / 1e9, 4),
                            "explorer_url": WHALE_EXPLORER_URL["SOL"].format(address=item["address"])})
    elif coin == "XRP":
        source_url = "https://xrplcluster.com/"
        for item in addresses:
            result = _post_json(source_url, {"method": "account_info", "params": [
                {"account": item["address"], "ledger_index": "validated"}]})
            drops = ((result.get("result") or {}).get("account_data") or {}).get("Balance")
            if drops is None:
                raise ValueError(f"{source_url} returned no balance for {item['address']}")
            wallets.append({**item, "balance": round(int(drops) / 1e6, 4),
                            "explorer_url": WHALE_EXPLORER_URL["XRP"].format(address=item["address"])})
    else:
        source_url = "https://ethereum.publicnode.com" if coin == "ETH" else "https://bsc-dataseed.binance.org/"
        for item in addresses:
            result = _post_json(source_url, {"jsonrpc": "2.0", "id": 1, "method": "eth_getBalance", "params": [item["address"], "latest"]})
            hex_balance = result.get("result")
            if not hex_balance:
                raise ValueError(f"{source_url} returned no balance for {item['address']}")
            wallets.append({**item, "balance": round(int(hex_balance, 16) / 1e18, 4), "explorer_url": WHALE_EXPLORER_URL[coin].format(address=item["address"])})

    # SOL/XRP 的持有者未經確認，note 不能沿用「已知大型交易所/機構地址」的說法。
    attributed = coin not in {"SOL", "XRP"}
    note = ("僅追蹤已知大型交易所/機構地址的即時餘額，非全網即時巨鯨偵測" if attributed else
            "僅追蹤固定的大額帳戶清單，餘額為鏈上實測；持有者歸屬未經第一方確認，"
            "不得據此推論交易所動向或交易意圖")
    return Evidence(
        f"EV-WHALE-{coin}-001", "Public balance check (curated known addresses)", source_url, datetime.now(timezone.utc).isoformat(),
        "whale", coin, "current", {"wallets": wallets, "note": note,
                                   "owner_attribution_verified": attributed}, 0.65,
        {"addresses": [item["address"] for item in addresses]}, f"Known large-wallet balance snapshot for {coin}"
    )


def fetch_klines(symbol: str, interval: str, limit: int = 1000) -> list[dict]:
    """Fetch OHLCV candles from Binance's public spot klines endpoint (free, no key required)."""
    url = f"https://api.binance.com/api/v3/klines?symbol={quote(symbol)}&interval={interval}&limit={limit}"
    raw = _get_json(url)
    if not raw:
        raise ValueError(f"Binance klines returned no data for {symbol} {interval}")
    return [{"close": float(row[4]), "high": float(row[2]), "low": float(row[3]), "volume": float(row[5])} for row in raw]


def fetch_vegas_signal(coin: str = "ETH") -> Evidence:
    """Vegas Channel (EMA144-987) + RSI(6) + volume-confirmed entry/exit, per the user-supplied
    Pine Script. Runs on two timeframes: 4h for trend context, 1h for the execution trigger --
    this needs ~1000 bars per timeframe to seed EMA987, which is why it fetches Binance klines
    directly instead of reusing the project's usual 14-day evidence window.
    """
    coin = coin.upper()
    symbol = FUTURES_SYMBOLS[coin]
    timeframes = {}
    for timeframe in ("4h", "1h"):
        candles = fetch_klines(symbol, timeframe, limit=1000)
        timeframes[timeframe] = analyze_vegas_channel(candles)
    alignment = check_timeframe_alignment("4h", timeframes["4h"]["trend"], "1h", timeframes["1h"]["trend"])

    return Evidence(
        f"EV-VEGAS-{coin}-001", "Binance Klines (Vegas Channel + RSI strategy)",
        f"https://api.binance.com/api/v3/klines?symbol={symbol}", datetime.now(timezone.utc).isoformat(),
        "vegas_channel", coin, "4h trend / 1h execution", {
            "symbol": symbol, "trend_timeframe": "4h", "execution_timeframe": "1h",
            "4h": timeframes["4h"], "1h": timeframes["1h"], "alignment": alignment,
        }, 0.70,
        {"symbol": symbol, "bars_per_timeframe": 1000}, f"{coin} Vegas Channel + RSI(6) entry/exit state on 4h trend and 1h execution timeframes"
    )


def fetch_long_short_ratio(coin: str = "ETH", period: str = "1h", limit: int = 24) -> Evidence:
    """Top-trader long/short POSITION ratio (position size, not account headcount) from Binance's
    free public futures-data endpoint. Includes recent history for charting, plus a "feasibility"
    percentage: how much of the recent window agrees with the current long/short bias.
    """
    coin = coin.upper()
    symbol = FUTURES_SYMBOLS[coin]
    url = f"https://fapi.binance.com/futures/data/topLongShortPositionRatio?symbol={quote(symbol)}&period={period}&limit={limit}"
    rows = _get_json(url)
    if not rows:
        raise ValueError(f"Binance returned no long/short ratio data for {symbol}")

    history = [{
        "time": datetime.fromtimestamp(int(row["timestamp"]) / 1000, tz=timezone.utc).isoformat(),
        "long_pct": round(float(row["longAccount"]) * 100, 2),
        "short_pct": round(float(row["shortAccount"]) * 100, 2),
        "ratio": round(float(row["longShortRatio"]), 4),
    } for row in rows]

    current = history[-1]
    bias = "long_dominant" if current["ratio"] > 1.02 else "short_dominant" if current["ratio"] < 0.98 else "balanced"
    current_is_long = current["ratio"] > 1.0
    same_side = sum(1 for point in history if (point["ratio"] > 1.0) == current_is_long)
    consistency_pct = round(same_side / len(history) * 100, 1)

    return Evidence(
        f"EV-LSRATIO-{coin}-001", "Binance Futures Data (top-trader position ratio)", url, datetime.now(timezone.utc).isoformat(),
        "long_short_ratio", coin, f"last {limit}x{period}", {
            "symbol": symbol, "period": period, "history": history, "current": current,
            "bias": bias, "consistency_pct": consistency_pct,
        }, 0.70,
        {"endpoint": url, "points": len(history)}, f"{coin} top-trader long/short position ratio and bias consistency"
    )


# DefiLlama addresses chains by their own slug rather than by ticker.
DEFILLAMA_CHAINS = {"BTC": "Bitcoin", "ETH": "Ethereum", "BNB": "BSC", "SOL": "Solana", "XRP": "XRPL"}


def fetch_defillama_tvl(coin: str = "ETH", days: int = 90) -> Evidence:
    """Total value locked on the coin's own chain, from DefiLlama's free no-key API.

    TVL is the closest free proxy for on-chain economic activity that is comparable across chains:
    unlike price it is not a market expectation, so a price move that TVL does not confirm is a
    genuine divergence worth flagging. The daily history also gives the report a fundamentals
    series to chart next to price.
    """
    coin = coin.upper()
    chain = DEFILLAMA_CHAINS.get(coin)
    if chain is None:
        raise NotImplementedError(f"DefiLlama chain mapping not available for {coin}")
    url = f"https://api.llama.fi/v2/historicalChainTvl/{quote(chain)}"
    rows = _get_json(url)
    if not isinstance(rows, list) or len(rows) < 2:
        raise ValueError(f"DefiLlama returned no TVL history for {chain}")

    history = [{
        "time": datetime.fromtimestamp(int(row["date"]), tz=timezone.utc).date().isoformat(),
        "tvl_usd": round(float(row["tvl"]), 2),
    } for row in rows if row.get("tvl") is not None][-days:]
    if len(history) < 2:
        raise ValueError(f"DefiLlama TVL history for {chain} is too short to trend")

    current, earliest = history[-1]["tvl_usd"], history[0]["tvl_usd"]
    change_pct = round((current - earliest) / earliest * 100, 2) if earliest else None
    # 30-day slope is what separates "high TVL" from "TVL currently growing"; both matter and they
    # frequently disagree, so the report carries the shorter window as well as the full one.
    month = history[-31:] if len(history) >= 31 else history
    change_30d_pct = round((current - month[0]["tvl_usd"]) / month[0]["tvl_usd"] * 100, 2) if month[0]["tvl_usd"] else None
    direction = (
        "expanding" if change_30d_pct is not None and change_30d_pct > 3
        else "contracting" if change_30d_pct is not None and change_30d_pct < -3
        else "flat" if change_30d_pct is not None else "unknown"
    )

    return Evidence(
        f"EV-TVL-{coin}-001", f"DefiLlama chain TVL ({chain})", url, datetime.now(timezone.utc).isoformat(),
        "tvl", coin, f"last {len(history)}d", {
            "chain": chain, "history": history, "tvl_usd": current,
            "change_pct": change_pct, "change_30d_pct": change_30d_pct, "direction": direction,
        }, 0.75,
        {"endpoint": url, "points": len(history)}, f"{coin} chain TVL level and trend as an on-chain fundamentals signal"
    )


# Google News search gives per-coin coverage but its <description> is only a repeat of the headline
# as a link, so it can never yield more than title-level information. These publisher feeds carry a
# real 1-3 sentence lede in <description>, which is what lifts the summary above headline level.
NEWS_LEDE_FEEDS = (
    ("Cointelegraph", "https://cointelegraph.com/rss"),
    ("Decrypt", "https://decrypt.co/feed"),
)
_NEWS_LEDE_MAX_CHARS = 420


def _coin_aliases(coin: str) -> tuple[str, ...]:
    names = {"BTC": ("bitcoin",), "ETH": ("ethereum", "ether"), "SOL": ("solana",),
             "BNB": ("binance coin", "bnb chain"), "XRP": ("ripple",)}
    return (coin.lower(),) + names.get(coin.upper(), ())


def _mentions_coin(text: str, coin: str) -> bool:
    lowered = text.lower()
    return any(alias in lowered for alias in _coin_aliases(coin))


# Publishers whose article pages are fetchable without JS and whose robots.txt permits it (verified
# 2026-07-31). Google News links are deliberately absent: they are JS redirect pages that yield no
# text, and news.google.com/robots.txt disallows everything for *.
FULLTEXT_DOMAINS = {"cointelegraph.com", "decrypt.co"}
_FULLTEXT_MAX_CHARS = 4000
_ROBOTS_CACHE: dict[str, RobotFileParser | None] = {}


def _robots_allows(url: str, user_agent: str = "hoyabit-research-agent") -> bool:
    """Check robots.txt before fetching an article. Cached per host; unreadable means do not crawl.

    robots.txt is fetched with our own User-Agent rather than via `RobotFileParser.read()`: that
    helper uses the bare `Python-urllib` agent, which these publishers reject with 403, and the
    parser then interprets the 403 as `disallow_all` -- refusing pages the site actually permits.
    """
    host = urlparse(url).netloc
    if host not in _ROBOTS_CACHE:
        parser = RobotFileParser()
        try:
            body = _get_bytes(f"https://{host}/robots.txt", timeout=8).decode("utf-8", errors="replace")
            parser.parse(body.splitlines())
        except Exception:
            parser = None  # unreadable robots.txt is treated as "do not crawl"
        _ROBOTS_CACHE[host] = parser
    parser = _ROBOTS_CACHE[host]
    return bool(parser and parser.can_fetch(user_agent, url))


def _extract_article_text(html: str) -> str:
    """Pull the article body out of a page with regex only (no third-party parser available).

    Paragraphs are filtered by a price-token density test because crypto publishers wrap articles in
    live ticker widgets; those render as <p> blocks full of symbols and percentages and would
    otherwise dominate the extracted text.
    """
    body = re.sub(r"(?is)<(script|style|nav|header|footer|aside|form)[^>]*>.*?</\1>", " ", html)
    kept = []
    for raw in re.findall(r"(?is)<p[^>]*>(.*?)</p>", body):
        text = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", raw))).strip()
        if len(text) < 80:
            continue
        tokens = text.split()
        ticker_like = sum(1 for token in tokens if token.startswith("$") or token.endswith("%"))
        if tokens and ticker_like / len(tokens) > 0.2:
            continue
        kept.append(text)
    return "\n".join(kept)[:_FULLTEXT_MAX_CHARS]


def enrich_with_fulltext(items: list[dict], deadline: float | None = None, timeout: int = 8,
                         max_articles: int = 8) -> list[dict]:
    """Fetch article bodies for whitelisted domains, stopping at the deadline.

    Every article is independent: a timeout, a 403 or a robots.txt refusal downgrades that one item
    back to lede level and never affects the rest. `deadline` is an absolute time.monotonic() value
    so the crawl phase can be capped without the caller tracking elapsed time.
    """
    fetched = 0
    for item in items:
        if fetched >= max_articles:
            break
        if deadline is not None and time.monotonic() >= deadline:
            item["fulltext_status"] = "skipped:deadline"
            continue
        url = item.get("url") or ""
        if urlparse(url).netloc.removeprefix("www.") not in FULLTEXT_DOMAINS:
            item["fulltext_status"] = "skipped:not_whitelisted"
            continue
        if not _robots_allows(url):
            item["fulltext_status"] = "skipped:robots"
            continue
        try:
            html = _get_bytes(url, timeout=timeout).decode("utf-8", errors="replace")
            text = _extract_article_text(html)
        except Exception as error:
            item["fulltext_status"] = f"failed:{type(error).__name__}"
            continue
        fetched += 1
        if text:
            item["fulltext"] = text
            item["fulltext_chars"] = len(text)
            item["fulltext_status"] = "ok"
        else:
            item["fulltext_status"] = "empty"
    return items


def fetch_news_rss(coin: str = "ETH", feed_url: str | None = None, limit: int = 5,
                   fulltext: bool = False, deadline: float | None = None) -> Evidence:
    """Recent news, preferring articles that ship a lede over headline-only search results.

    Publisher feeds are best-effort enrichment: each one is tried independently and a failure just
    means fewer summarised articles, never a failed news fetch, because Google News still provides
    the coin-specific coverage that the general publisher feeds cannot guarantee.
    """
    coin = coin.upper()
    feed_url = feed_url or f"https://news.google.com/rss/search?q={quote(coin)}%20crypto"

    items: list[dict] = []
    seen_titles: set[str] = set()
    for publisher, url in NEWS_LEDE_FEEDS:
        try:
            for entry in _parse_feed_entries(_get_bytes(url, timeout=6), limit=25):
                text = f"{entry['title']} {entry.get('summary', '')}"
                if not entry["title"] or not _mentions_coin(text, coin):
                    continue
                if entry["title"].lower() in seen_titles:
                    continue
                seen_titles.add(entry["title"].lower())
                items.append({**entry, "publisher": publisher, "has_summary": bool(entry.get("summary"))})
        except Exception:
            continue

    request = Request(feed_url, headers={"User-Agent": "agent-team-mvp/0.1"})
    with urlopen(request, timeout=8) as response:
        root = ElementTree.fromstring(response.read())
    search_items = []
    for item in root.findall(".//item")[:limit]:
        title = item.findtext("title", "")
        if title.lower() in seen_titles:
            continue
        search_items.append({
            "title": title, "url": item.findtext("link", ""), "published": item.findtext("pubDate", ""),
            "summary": "", "publisher": item.findtext("source", "") or "Google News", "has_summary": False,
        })
    # Lede-carrying articles lead; the search feed tops the list up to `limit` for coverage.
    items = (items[:limit] + search_items)[:limit]
    if not items:
        raise ValueError("RSS returned no articles")

    if fulltext:
        enrich_with_fulltext(items, deadline=deadline)

    with_summary = sum(1 for item in items if item["has_summary"])
    with_fulltext = sum(1 for item in items if item.get("fulltext"))
    return Evidence(
        "EV-NEWS-001", "Google News RSS + publisher feeds", feed_url, datetime.now(timezone.utc).isoformat(),
        "news", coin, "14d",
        {"items": items, "summarised_count": with_summary, "fulltext_count": with_fulltext}, 0.60,
        {"feed_url": feed_url, "article_count": len(items), "with_lede": with_summary,
         "with_fulltext": with_fulltext, "items": items},
        f"Recent news context for {coin}"
    )


def _get_bytes(url: str, timeout: int = 8) -> bytes:
    request = Request(url, headers={"User-Agent": "agent-team-mvp/0.1"})
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def _strip_html(raw: str, max_chars: int = 420) -> str:
    """Reduce a feed summary to plain text: feeds wrap ledes in markup and entity escapes."""
    text = re.sub(r"<[^>]+>", " ", raw or "")
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_chars:
        return text
    # Prefer cutting at a sentence end so the lede does not stop mid-clause.
    cut = text[:max_chars]
    boundary = max(cut.rfind("。"), cut.rfind("! "), cut.rfind("? "), cut.rfind(". "))
    return (cut[: boundary + 1] if boundary > max_chars * 0.5 else cut).strip() + "…"


def _parse_feed_entries(raw: bytes, limit: int = 5) -> list[dict]:
    """Parse either RSS 2.0 (<item>) or Atom (<entry>) into a common shape.

    Both are needed: Ethereum/Solana publish RSS while Bitcoin Optech publishes Atom. `summary` is
    the article lede when the feed provides one and an empty string when it does not, so callers
    can tell headline-only sources from ones carrying real content.
    """
    root = ElementTree.fromstring(raw)
    entries = [{
        "title": (item.findtext("title") or "").strip(),
        "url": (item.findtext("link") or "").strip(),
        "published": (item.findtext("pubDate") or "").strip(),
        "summary": _strip_html(item.findtext("description") or ""),
    } for item in root.findall(".//item")[:limit]]
    if entries:
        return entries
    for entry in root.findall(f".//{ATOM_NS}entry")[:limit]:
        link = entry.find(f"{ATOM_NS}link")
        entries.append({
            "title": (entry.findtext(f"{ATOM_NS}title") or "").strip(),
            "url": (link.get("href") if link is not None else "") or "",
            "published": (entry.findtext(f"{ATOM_NS}updated") or entry.findtext(f"{ATOM_NS}published") or "").strip(),
            "summary": _strip_html(
                entry.findtext(f"{ATOM_NS}summary") or entry.findtext(f"{ATOM_NS}content") or ""
            ),
        })
    return entries


def fetch_macro(coin: str = "ETH") -> Evidence:
    """Market-wide macro backdrop: the Crypto Fear & Greed Index plus the latest Federal Reserve
    monetary-policy press releases. Both are free and key-less.

    This is deliberately *not* coin-specific -- it is the risk-appetite and rates backdrop that all
    five supported assets share, which is exactly what a single-coin technical read cannot supply.
    The Fed feed is best-effort: Fear & Greed is the required part, so a Fed outage degrades the
    evidence rather than dropping the whole macro signal.
    """
    coin = coin.upper()
    fng_url = "https://api.alternative.me/fng/?limit=14"
    payload = _get_json(fng_url)
    points = payload.get("data") or []
    if not points:
        raise ValueError("alternative.me returned no Fear & Greed data")

    # alternative.me returns newest-first; flip to chronological so charts read left-to-right.
    history = [{
        "time": datetime.fromtimestamp(int(point["timestamp"]), tz=timezone.utc).strftime("%Y-%m-%d"),
        "value": int(point["value"]),
        "classification": point.get("value_classification", ""),
    } for point in reversed(points)]
    current = history[-1]
    previous = history[0]
    delta = current["value"] - previous["value"]
    direction = "risk_appetite_improving" if delta > 5 else "risk_appetite_deteriorating" if delta < -5 else "risk_appetite_stable"

    content = {
        "fear_greed_value": current["value"],
        "fear_greed_classification": current["classification"],
        "fear_greed_history": history,
        "fear_greed_change_14d": delta,
        "risk_appetite_direction": direction,
        "scope": "crypto market-wide (not coin-specific)",
    }

    fed_url = "https://www.federalreserve.gov/feeds/press_monetary.xml"
    try:
        releases = _parse_feed_entries(_get_bytes(fed_url), limit=5)
        content["fed_releases"] = releases
        fomc = next((entry for entry in releases if "FOMC" in entry["title"].upper()), None)
        content["latest_fomc_release"] = fomc
        content["fed_status"] = "available"
    except Exception as error:
        content["fed_releases"] = []
        content["latest_fomc_release"] = None
        content["fed_status"] = f"unavailable:{type(error).__name__}"

    return Evidence(
        f"EV-MACRO-{coin}-001", "Alternative.me Fear & Greed + Federal Reserve press releases", fng_url,
        datetime.now(timezone.utc).isoformat(), "macro", coin, "14d", content, 0.80,
        {"fear_greed_endpoint": fng_url, "fed_feed": fed_url, "points": len(history), "fed_status": content["fed_status"]},
        f"Market-wide risk appetite and monetary-policy backdrop applied to {coin}"
    )


def fetch_official_announcements(coin: str = "ETH") -> Evidence:
    """Fetch the coin's official project announcements (see OFFICIAL_FEEDS for the first-party
    vs domain-restricted-syndication distinction, which is reflected in the reliability score)."""
    coin = coin.upper()
    feeds = OFFICIAL_FEEDS.get(coin)
    if not feeds:
        raise NotImplementedError(f"No official announcement feed configured for {coin}")
    last_error: Exception = ValueError(f"No official feed produced entries for {coin}")
    for source_name, feed_url, first_party in feeds:
        try:
            entries = _parse_feed_entries(_get_bytes(feed_url), limit=5)
            if not entries:
                raise ValueError(f"{source_name} returned no entries")
            release_feed = first_party and coin in RELEASE_FEED_COINS
            if release_feed:
                note = "第一方發布，但範圍僅限參考客戶端／協議版本發布，不含合作、上架等一般公告"
            elif first_party:
                note = "第一方官方發布"
            else:
                note = "官方網域限定的新聞聚合（非第一方 feed）"
            return Evidence(
                f"EV-ANNOUNCE-{coin}-001", source_name, feed_url, datetime.now(timezone.utc).isoformat(),
                "announcement", coin, "recent", {
                    "items": entries,
                    "first_party": first_party,
                    "scope": "client_releases" if release_feed else "general",
                    "note": note,
                }, 0.85 if first_party else 0.55,
                {"feed_url": feed_url, "entry_count": len(entries), "first_party": first_party},
                (f"{coin} official reference-client release announcements" if release_feed
                 else f"Official project announcements for {coin}")
            )
        except Exception as error:
            last_error = error
    raise last_error


def fetch_onchain(coin: str = "ETH") -> Evidence:
    """Fetch a public chain-health observation for the requested asset."""
    coin = coin.upper()
    chain_config = {
        "ETH": "Ethereum",
        "BNB": "BNB Smart Chain",
        "SOL": "Solana",
        "XRP": "XRP Ledger",
        "BTC": "Bitcoin",
    }
    endpoints = {
        "ETH": "https://ethereum.publicnode.com",
        "BNB": "https://bsc-dataseed.binance.org/",
        "SOL": "https://api.mainnet-beta.solana.com",
        "XRP": "https://xrplcluster.com/",
        "BTC": "https://blockchain.info/q/getblockcount",
    }
    chain_name, endpoint = chain_config[coin], endpoints[coin]
    if coin in {"ETH", "BNB"}:
        url = endpoint
        payload = _post_json(url, {"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []})
        value = payload.get("result")
        if not value or "error" in payload:
            raise ValueError(f"{chain_name} RPC returned no block number")
        content = {"chain": chain_name, "latest_block_hex": value}
    elif coin == "BTC":
        url = endpoint
        content = {"chain": chain_name, "latest_block_height": _get_text(url)}
    elif coin == "SOL":
        url = endpoint
        payload = _post_json(url, {"jsonrpc": "2.0", "id": 1, "method": "getEpochInfo"})
        value = payload.get("result")
        if not value:
            raise ValueError("Solana RPC returned no epoch info")
        content = {"chain": chain_name, "epoch_info": value}
    elif coin == "XRP":
        url = endpoint
        payload = _post_json(url, {"method": "ledger_current", "params": [{}]})
        value = payload.get("result", {}).get("ledger_current_index")
        if not value:
            raise ValueError("XRPL RPC returned no current ledger")
        content = {"chain": chain_name, "ledger_current_index": value}
    return Evidence(
        f"EV-ONCHAIN-{coin}-001", chain_name, url, datetime.now(timezone.utc).isoformat(),
        "onchain", coin, "current", content, 0.70,
        {"endpoint": url, "chain": chain_name}, f"Current {chain_name} chain health observation for {coin}"
    )


def fetch_social_reddit(coin: str = "ETH") -> Evidence:
    """Fetch recent public Reddit discussions and derive a transparent title-based signal."""
    coin = coin.upper()
    url = f"https://www.reddit.com/search.json?q={quote(coin + ' crypto')}&sort=new&t=week&limit=25"
    payload = _get_json(url)
    posts = []
    positive_words = {"bull", "bullish", "gain", "gains", "surge", "breakout", "adoption", "upgrade"}
    negative_words = {"bear", "bearish", "loss", "losses", "drop", "crash", "hack", "exploit"}
    positive = negative = 0
    for child in payload.get("data", {}).get("children", []):
        data = child.get("data", {})
        title = data.get("title", "")
        words = {word.strip(".,!?():[]\"").lower() for word in title.split()}
        matched_positive, matched_negative = sorted(words & positive_words), sorted(words & negative_words)
        positive += len(matched_positive)
        negative += len(matched_negative)
        posts.append({
            "title": title,
            "url": "https://www.reddit.com" + data.get("permalink", ""),
            "created_utc": data.get("created_utc"),
            "score": data.get("score", 0),
            "comments": data.get("num_comments", 0),
            "matched_positive": matched_positive,
            "matched_negative": matched_negative,
        })
    if not posts:
        raise ValueError("Reddit returned no public posts")
    sentiment = "positive" if positive > negative else "negative" if negative > positive else "mixed"
    return Evidence(
        f"EV-SOCIAL-{coin}-001", "Reddit public search", url, datetime.now(timezone.utc).isoformat(),
        "social", coin, "7d", {"sentiment": sentiment, "positive_terms": positive, "negative_terms": negative, "posts": posts}, 0.45,
        {"endpoint": url, "query": f"{coin} crypto", "post_count": len(posts), "posts": posts}, f"Public discussion tone and attention for {coin}"
    )


def fetch_social_bluesky(coin: str = "ETH") -> Evidence:
    """Fetch public Bluesky posts without an API key."""
    coin = coin.upper()
    url = f"https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts?q={quote(coin + ' crypto')}&sort=latest&limit=25"
    payload = _get_json(url)
    posts = []
    positive_words = {"bull", "bullish", "gain", "gains", "surge", "breakout", "adoption", "upgrade"}
    negative_words = {"bear", "bearish", "loss", "losses", "drop", "crash", "hack", "exploit"}
    positive = negative = 0
    for item in payload.get("posts", []):
        text = item.get("record", {}).get("text", "")
        words = {word.strip(".,!?():[]\"").lower() for word in text.split()}
        matched_positive, matched_negative = sorted(words & positive_words), sorted(words & negative_words)
        positive += len(matched_positive)
        negative += len(matched_negative)
        handle = item.get("author", {}).get("handle", "")
        rkey = item.get("uri", "").rsplit("/", 1)[-1]
        posts.append({
            "text": text[:500],
            "url": f"https://bsky.app/profile/{handle}/post/{rkey}" if handle and rkey else "",
            "indexed_at": item.get("indexedAt"),
            "likes": item.get("likeCount", 0),
            "replies": item.get("replyCount", 0),
            "reposts": item.get("repostCount", 0),
            "matched_positive": matched_positive,
            "matched_negative": matched_negative,
        })
    if not posts:
        raise ValueError("Bluesky returned no public posts")
    sentiment = "positive" if positive > negative else "negative" if negative > positive else "mixed"
    return Evidence(
        f"EV-SOCIAL-{coin}-BSKY-001", "Bluesky public search", url, datetime.now(timezone.utc).isoformat(),
        "social", coin, "recent", {"sentiment": sentiment, "positive_terms": positive, "negative_terms": negative, "posts": posts}, 0.45,
        {"endpoint": url, "query": f"{coin} crypto", "post_count": len(posts), "posts": posts}, f"Public discussion tone and attention for {coin}"
    )


def fetch_social_hackernews(coin: str = "ETH") -> Evidence:
    """Fetch recent Hacker News stories/comments as a public discussion signal."""
    coin = coin.upper()
    url = f"https://hn.algolia.com/api/v1/search_by_date?query={quote(coin + ' crypto')}&tags=(story,comment)&hitsPerPage=25"
    payload = _get_json(url)
    posts = []
    positive_words = {"bull", "bullish", "gain", "gains", "surge", "breakout", "adoption", "upgrade"}
    negative_words = {"bear", "bearish", "loss", "losses", "drop", "crash", "hack", "exploit"}
    positive = negative = 0
    for hit in payload.get("hits", []):
        text = hit.get("title") or hit.get("story_title") or hit.get("comment_text") or ""
        words = {word.strip(".,!?():[]\"").lower() for word in text.split()}
        matched_positive, matched_negative = sorted(words & positive_words), sorted(words & negative_words)
        positive += len(matched_positive)
        negative += len(matched_negative)
        object_id = hit.get("objectID", "")
        posts.append({
            "text": text[:500],
            "url": hit.get("url") or f"https://news.ycombinator.com/item?id={object_id}",
            "created_at": hit.get("created_at"),
            "points": hit.get("points") or 0,
            "comments": hit.get("num_comments") or 0,
            "matched_positive": matched_positive,
            "matched_negative": matched_negative,
        })
    if not posts:
        raise ValueError("Hacker News returned no public discussions")
    sentiment = "positive" if positive > negative else "negative" if negative > positive else "mixed"
    return Evidence(
        f"EV-SOCIAL-{coin}-HN-001", "Hacker News Algolia search", url, datetime.now(timezone.utc).isoformat(),
        "social", coin, "recent", {"sentiment": sentiment, "positive_terms": positive, "negative_terms": negative, "posts": posts}, 0.50,
        {"endpoint": url, "query": f"{coin} crypto", "post_count": len(posts), "posts": posts}, f"Public technical-community discussion tone for {coin}"
    )


def fetch_social(coin: str = "ETH") -> Evidence:
    """Try independent public social providers before allowing the orchestrator to fallback."""
    try:
        return fetch_social_reddit(coin)
    except Exception:
        try:
            return fetch_social_bluesky(coin)
        except Exception:
            return fetch_social_hackernews(coin)


# Explicit degraded-evidence fixtures, keyed by label: (id_prefix, source, url, time_range, extra_content).
# These carry reliability 0.20 and a "do not treat as live evidence" claim so a failed fetch can never
# be mistaken for a real observation. Where a label has an entry here it takes precedence over the
# Day-1 mock fixture, which is presentation-grade and would otherwise overstate reliability.
_FALLBACK_SPECS = {
    "onchain": ("EV-ONCHAIN", "Offline chain fixture", "https://example.com/onchain", "current", {}),
    "derivatives": ("EV-DERIV", "Offline funding-rate fixture", "https://example.com/derivatives", "current", {"bias": "unknown"}),
    "whale": ("EV-WHALE", "Offline whale-wallet fixture", "https://example.com/whale", "current", {"wallets": []}),
    "vegas_channel": ("EV-VEGAS", "Offline Vegas Channel fixture", "https://example.com/vegas", "4h trend / 1h execution", {"4h": None, "1h": None, "alignment": None}),
    "long_short_ratio": ("EV-LSRATIO", "Offline long/short ratio fixture", "https://example.com/long_short_ratio", "current", {"history": [], "current": None, "bias": "unknown", "consistency_pct": None}),
    "macro": ("EV-MACRO", "Offline macro fixture", "https://example.com/macro", "14d", {"fear_greed_value": None, "fear_greed_history": [], "risk_appetite_direction": "unknown", "fed_releases": []}),
    "announcement": ("EV-ANNOUNCE", "Offline announcement fixture", "https://example.com/announcement", "recent", {"items": [], "first_party": False}),
    "tvl": ("EV-TVL", "Offline chain TVL fixture", "https://example.com/tvl", "90d", {"history": [], "tvl_usd": None, "change_pct": None, "change_30d_pct": None, "direction": "unknown"}),
}

_SOURCE_LOADERS = (
    ("market", fetch_coingecko),
    ("news", fetch_news_rss),
    ("macro", fetch_macro),
    ("announcement", fetch_official_announcements),
    ("onchain", fetch_onchain),
    ("social", fetch_social),
    ("derivatives", fetch_funding_rate),
    ("whale", fetch_whale_wallets),
    ("vegas_channel", fetch_vegas_signal),
    ("long_short_ratio", fetch_long_short_ratio),
    ("tvl", fetch_defillama_tvl),
)


def _fallback_evidence(label: str, coin: str, error: Exception) -> Evidence | None:
    spec = _FALLBACK_SPECS.get(label)
    if spec is None:
        # market/news/social keep the Day-1 fixture's shape so the UI still renders a complete
        # report offline, but the record is re-stamped: fixture reliability (0.80 for market) must
        # never survive onto evidence that stands in for a failed live fetch.
        mock = next((item for item in mock_evidence(coin) if item.data_type == label), None)
        if mock is None:
            return None
        status = "unsupported" if isinstance(error, NotImplementedError) else "unavailable"
        return stamp_credibility_metadata(replace(
            mock,
            evidence_id=f"{mock.evidence_id}-{coin}-FALLBACK",
            fetched_at=datetime.now(timezone.utc).isoformat(),
            content={**mock.content, "status": status, "reason": type(error).__name__},
            reliability_score=0.20,
            content_reference={"fallback": True, "error_type": type(error).__name__, "fixture": mock.source},
            related_claim=f"{label} signal unavailable for {coin}; do not treat fallback as live evidence",
            source_type="fallback_fixture",
            verification_status=VERIFICATION_STATUS_UNAVAILABLE,
        ))
    id_prefix, source, url, time_range, extra = spec
    status = "unsupported" if isinstance(error, NotImplementedError) else "unavailable"
    return stamp_credibility_metadata(Evidence(
        f"{id_prefix}-{coin}-FALLBACK", source, url, datetime.now(timezone.utc).isoformat(),
        label, coin, time_range, {"status": status, "reason": type(error).__name__, **extra}, 0.20,
        {"fallback": True, "error_type": type(error).__name__},
        f"{label} signal unavailable for {coin}; do not treat fallback as live evidence",
        source_type="fallback_fixture",
        verification_status=VERIFICATION_STATUS_UNAVAILABLE,
    ))


# Sources grouped into domain agents. Each agent owns one research aspect and runs independently,
# so wall-clock becomes the slowest agent instead of the sum of all sources. Grouping (rather than
# one thread per source) keeps related calls to the same host sequential -- hammering Binance with
# three simultaneous requests is how free endpoints start rate-limiting.
COLLECTION_AGENTS = (
    ("news_agent", "新聞面", ("news",)),
    ("social_agent", "社群面", ("social",)),
    ("market_agent", "市場價格", ("market",)),
    ("technical_agent", "技術與衍生品", ("vegas_channel", "long_short_ratio", "derivatives")),
    ("onchain_agent", "鏈上面", ("onchain", "whale", "tvl")),
    ("macro_agent", "總經與官方", ("macro", "announcement")),
)


def _run_source(label: str, loader, coin: str, deadline: float | None,
                loader_kwargs: dict | None = None) -> tuple[Evidence | None, str]:
    """Run one source with the deadline check and fallback policy, returning (evidence, log entry).

    `deadline` is the watchdog for this call; `loader_kwargs` is what the adapter itself needs --
    kept separate because the news adapter takes its own `deadline` for the crawl phase.
    """
    if deadline is not None and time.monotonic() >= deadline:
        skipped = _fallback_evidence(label, coin, TimeoutError("collection deadline exceeded"))
        return skipped, f"{label}:skipped:deadline"
    try:
        return loader(coin, **(loader_kwargs or {})), f"{label}:success"
    except Exception as error:
        fallback = _fallback_evidence(label, coin, error)
        if fallback is None:
            raise
        return fallback, f"{label}:fallback:{type(error).__name__}"


def collect_evidence_detailed(coin: str = "ETH", live: bool = False, deadline: float | None = None,
                              fulltext: bool = False, parallel: bool = True
                              ) -> tuple[list[Evidence], list[str], list[dict]]:
    """Collect evidence with domain agents running concurrently.

    Returns (evidence, log, agent_report). Evidence is re-sorted into the canonical source order
    afterwards so that concurrency never changes the report: evidence numbering drives the
    footnotes, and footnotes that move between runs would be worse than a slower pipeline.
    """
    if not live:
        return [stamp_credibility_metadata(item) for item in mock_evidence(coin)], ["mock_mode"], []

    loaders = dict(_SOURCE_LOADERS)
    canonical_order = [label for label, _ in _SOURCE_LOADERS]

    def run_agent(name: str, zh_label: str, labels: tuple[str, ...]) -> dict:
        started = time.monotonic()
        collected: list[tuple[str, Evidence]] = []
        entries: list[str] = []
        for label in labels:
            kwargs = {"fulltext": fulltext, "deadline": deadline} if label == "news" else None
            evidence, entry = _run_source(label, loaders[label], coin, deadline, kwargs)
            entries.append(entry)
            if evidence is not None:
                collected.append((label, evidence))
        return {
            "agent": name, "label": zh_label, "sources": list(labels), "collected": collected,
            "log": entries, "duration_ms": round((time.monotonic() - started) * 1000, 1),
        }

    if parallel:
        with ThreadPoolExecutor(max_workers=len(COLLECTION_AGENTS), thread_name_prefix="agent") as pool:
            futures = [pool.submit(run_agent, *agent) for agent in COLLECTION_AGENTS]
            reports = [future.result() for future in futures]
    else:
        reports = [run_agent(*agent) for agent in COLLECTION_AGENTS]

    by_label = {label: evidence for report in reports for label, evidence in report["collected"]}
    evidence = [stamp_credibility_metadata(by_label[label]) for label in canonical_order if label in by_label]
    log = [entry for label in canonical_order for report in reports
           for entry in report["log"] if entry.startswith(f"{label}:")]
    agent_report = [
        {key: value for key, value in report.items() if key != "collected"} for report in reports
    ]
    return evidence, log, agent_report


def collect_evidence(coin: str = "ETH", live: bool = False, deadline: float | None = None,
                     fulltext: bool = False) -> tuple[list[Evidence], list[str]]:
    """Collect live data when requested, otherwise use deterministic fallback.

    Thin wrapper over `collect_evidence_detailed` for callers that do not need the per-agent report.

    `deadline` is an absolute time.monotonic() value acting as a collection watchdog: it is checked
    before each source, so the worst-case overshoot is one source timeout rather than the full
    remaining source list. Sources skipped this way are logged as `<label>:skipped:deadline`, which
    is deliberately distinct from `:fallback:` so a demo run can tell a slow network from a dead API.
    """
    evidence, log, _ = collect_evidence_detailed(coin, live=live, deadline=deadline, fulltext=fulltext)
    return evidence, log
