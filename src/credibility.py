"""T3：Evidence credibility 的 deterministic 計分引擎。

本模組只做一件事：吃一筆（或一批）Evidence 的 metadata，回傳**可展開、可序列化、可重現**的
可信度分數。設計約束來自 ``.kiro/steering/evidence-confidence-standards.md`` 與
``docs/competition-tasks/T3-credibility.md``：

1. **純函式。** 不讀寫全域狀態、不呼叫網路、不呼叫 LLM。相同輸入必得相同輸出（時間也要從
   ``now`` 參數傳入，測試才能固定）。
2. **權重與 hard cap 一律沿用 ``src/schemas.py``。** 這裡不重新定義權重字面值，也不新增
   cap 名稱；``missing_fetched_at`` 依 schema 語意是「命中即 reject」而不是降到 0.0 的分數。
3. **LLM 不得寫最終分數。** 呼叫端可以用 LLM 補 metadata（例如判斷 source_type），但
   ``final_score`` 只能由本模組算出來。
4. **同源轉載不得灌高獨立性。** 20 篇引用同一原始消息只算一條主要來源鏈，independence 隨
   lineage 群組大小遞減。
5. **舊新聞不會因為剛抓下來就變新鮮。** freshness 優先使用 ``event_time``／``published_at``；
   只有在來源類別明確標記「fetched_at 就是觀測時間」（例如 API 快照）時，才允許用
   ``fetched_at`` 主張新鮮度。

公開介面（Track A 接線時只需要這幾個）::

    load_source_registry(path=None) -> dict
    registry_entry(registry, source_type) -> dict
    lineage_id_for(evidence) -> str
    group_by_lineage(evidence_items) -> dict          # lineage_id -> [evidence_id, ...]
    score_evidence(evidence, registry=None, *, now=None, ...) -> dict
    score_evidence_batch(evidence_items, registry=None, *, now=None) -> list[dict]

``score_evidence`` 的輸出（全部可 ``json.dumps``）::

    raw_score            五項加權後的原始分數（未套 cap）
    final_score          min(raw_score, hard_cap)；被 reject 時為 0.0
    components           CREDIBILITY_COMPONENT_KEYS 的五個 0-1 分量
    hard_cap             實際生效的最小 cap（沒有 cap 時為 None）
    score_limiters       生效的 cap 名稱（HARD_CAPS 的鍵，依 HARD_CAPS 順序）
    rejected             是否命中 REJECTING_HARD_CAP_KEYS
    verification_status  被 reject 時為 "rejected"，否則沿用輸入（非法值正規化為 unverified）
    scoring_version      SCORING_VERSION
    notes                非 cap 的降分原因說明（給人看，不參與計算）
    以及 evidence_id / source_type / source_lineage_id / lineage_size /
    independence_factor / freshness_basis / age_days 供報告與 Web UI 展示。

claim 層的 ``high_quality_conflict_claim_cap`` 屬於 T4（claim confidence），本模組不套用。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit

from src.schemas import (
    CREDIBILITY_COMPONENT_KEYS,
    CREDIBILITY_WEIGHTS,
    HARD_CAPS,
    REJECTING_HARD_CAP_KEYS,
    SCORING_VERSION,
    SOURCE_LINEAGE_KEYS,
    SOURCE_TYPE_FALLBACK_FIXTURE,
    SOURCE_TYPE_UNKNOWN,
    SOURCE_TYPES,
    VERIFICATION_STATUS_REJECTED,
    VERIFICATION_STATUS_UNVERIFIED,
    VERIFICATION_STATUSES,
)

DEFAULT_REGISTRY_PATH = Path(__file__).parents[1] / "config" / "source_registry.json"

# registry 缺該來源時的保守預設。刻意寫死在程式裡：即使 config 檔案不存在或被改壞，
# 計分仍要能跑完（單一來源失敗不得中斷流程），只是分數偏保守。
CONSERVATIVE_ENTRY = {
    "label": "保守預設（registry 未涵蓋）",
    "source_quality": 0.35,
    "traceability": 0.35,
    "method_transparency": 0.30,
    "independence_base": 0.50,
    "first_party": False,
    "is_news": False,
    "is_secondary_news": False,
    "is_social": False,
    "is_fallback": False,
    "fetched_at_is_observation": False,
    "freshness_policy": {"fresh_within_days": 1, "stale_after_days": 7},
    "known_limitations": ["來源類別未登錄，一律以保守基準計分"],
}

# 分量的下限與調整幅度。全部是常數，沒有任何隨機或時間相依成分。
FRESHNESS_FLOOR = 0.10
INDEPENDENCE_FLOOR = 0.05
MIN_TRACEABILITY_WITHOUT_LOCATOR = 0.10
# 來源類別的 fetched_at 不等於觀測時間，又沒有 event/published time 時，freshness 的上限。
FRESHNESS_CAP_WITHOUT_EVENT_TIME = 0.50
METHOD_TRANSPARENCY_BONUS = 0.10
TRACEABILITY_BONUS_VERIFIED = 0.05

_ROUND = 4

# --------------------------------------------------------------------------------------
# metadata 訊號的鍵名集合
# --------------------------------------------------------------------------------------

# 足以重現一筆資料的 locator 訊號（T3「Source Locator」）。
# 刻意不含 time_range／interval／period：時間窗說明的是「何時」而不是「在哪裡取得」，
# 單靠它無法重現同一筆資料。這些鍵留在 _METHOD_KEYS 供 method_transparency 使用。
_LOCATOR_KEYS = frozenset({
    "url", "source_url", "canonical_url", "original_source_url", "permalink", "explorer_url",
    "endpoint", "api_endpoint", "request_url", "query", "query_name", "query_parameters",
    "query_params", "params", "symbol", "trading_pair", "pair", "chain", "address",
    "transaction_hash", "tx_hash", "block", "block_number", "dashboard_query", "dataset",
    "raw_file", "file", "file_path", "path",
})

# 方法可重現性的訊號（指標怎麼算出來的）。
_METHOD_KEYS = frozenset({
    "method", "methodology", "calculation", "formula", "indicator", "indicator_params",
    "parameters", "params", "interval", "period", "window", "timeframe", "sample_size",
    "trend_timeframe", "execution_timeframe", "scoring", "aggregation",
})

# 主體歸屬（這個地址／帳號屬於誰）的訊號。
_ENTITY_ATTRIBUTION_KEYS = frozenset({
    "label", "wallet_label", "entity", "entity_name", "owner", "attributed_entity",
    "attributed_to", "exchange", "whale_label", "account_owner",
})

# 意圖歸屬（他打算做什麼）的訊號。
_INTENT_ATTRIBUTION_KEYS = frozenset({
    "intent", "intention", "purpose", "motive", "implied_action", "expected_action",
    "planned_action", "interpretation", "reason",
})

# 社群來源的可追溯身分訊號。
_AUTHOR_KEYS = frozenset({"author", "account", "handle", "username", "user", "profile_url", "author_url"})

_UNUSABLE_LOCATOR_VALUES = frozenset({"", "-", "n/a", "na", "none", "null", "unknown", "tbd"})

_TRACKING_QUERY_PREFIXES = ("utm_", "fbclid", "gclid", "ref", "ref_src", "mc_cid", "mc_eid")


# --------------------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------------------


def load_source_registry(path=None) -> dict:
    """讀取 ``config/source_registry.json``。

    檔案不存在或內容壞掉時回傳只含保守預設的 registry，而不是拋例外 —— 計分不該成為
    整條研究流程的單點失敗。
    """
    target = Path(path) if path is not None else DEFAULT_REGISTRY_PATH
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    source_types = raw.get("source_types")
    if not isinstance(source_types, dict):
        source_types = {}
    default_entry = raw.get("default")
    if not isinstance(default_entry, dict):
        default_entry = {}
    return {
        "registry_version": str(raw.get("registry_version", "unavailable")),
        "default": dict(default_entry),
        "source_types": {str(key): dict(value) for key, value in source_types.items() if isinstance(value, dict)},
    }


def registry_entry(registry, source_type: str) -> dict:
    """取得某個 ``source_type`` 的基準值，缺項一律以保守預設補齊。"""
    entry = dict(CONSERVATIVE_ENTRY)
    entry["freshness_policy"] = dict(CONSERVATIVE_ENTRY["freshness_policy"])
    entry["known_limitations"] = list(CONSERVATIVE_ENTRY["known_limitations"])

    registry = registry if isinstance(registry, dict) else {}
    layers = []
    default_layer = registry.get("default")
    if isinstance(default_layer, dict):
        layers.append(default_layer)
    configured = registry.get("source_types")
    if isinstance(configured, dict):
        candidate = configured.get(_normalise_source_type(source_type))
        if isinstance(candidate, dict):
            layers.append(candidate)

    for layer in layers:
        for key, value in layer.items():
            if key == "freshness_policy" and isinstance(value, dict):
                policy = dict(entry["freshness_policy"])
                policy.update({str(pkey): pvalue for pkey, pvalue in value.items()})
                entry["freshness_policy"] = policy
            else:
                entry[key] = value
    entry["source_type"] = _normalise_source_type(source_type)
    entry["registry_hit"] = bool(
        isinstance(registry.get("source_types"), dict)
        and _normalise_source_type(source_type) in registry["source_types"]
    )
    return entry


def _normalise_source_type(source_type) -> str:
    value = str(source_type or "").strip().lower()
    if value in SOURCE_TYPES:
        return value
    return SOURCE_TYPE_UNKNOWN


# --------------------------------------------------------------------------------------
# Evidence 讀取工具
# --------------------------------------------------------------------------------------


def evidence_as_dict(evidence) -> dict:
    """把 Evidence dataclass 或 mapping 統一成 plain dict（不修改輸入）。"""
    if is_dataclass(evidence) and not isinstance(evidence, type):
        return asdict(evidence)
    if isinstance(evidence, dict):
        return dict(evidence)
    raise TypeError("evidence 必須是 dataclass 實例或 mapping")


def _metadata_scopes(data: dict) -> list:
    """回傳要掃描 metadata 訊號的 mapping 清單（content_reference 與 content 各展開一層）。"""
    scopes = [data]
    for key in ("content_reference", "content"):
        value = data.get(key)
        if isinstance(value, dict):
            scopes.append(value)
            for nested in value.values():
                if isinstance(nested, dict):
                    scopes.append(nested)
                elif isinstance(nested, list):
                    for item in nested[:20]:
                        if isinstance(item, dict):
                            scopes.append(item)
    return scopes


def _find_key(data: dict, keys) -> str:
    """在 metadata 各層找第一個有值的鍵，回傳鍵名（找不到回空字串）。"""
    for scope in _metadata_scopes(data):
        for key, value in scope.items():
            if str(key).lower() in keys and _has_value(value):
                return str(key).lower()
    return ""


def _lookup(data: dict, key: str):
    for scope in _metadata_scopes(data):
        for candidate, value in scope.items():
            if str(candidate).lower() == key and _has_value(value):
                return value
    return None


def _has_value(value) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in _UNUSABLE_LOCATOR_VALUES
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) > 0
    return True


def _flag(data: dict, key: str) -> bool:
    value = _lookup(data, key)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1"}
    return False


# --------------------------------------------------------------------------------------
# 時間語意
# --------------------------------------------------------------------------------------


def parse_timestamp(value):
    """盡量寬鬆地解析時間字串，失敗回 ``None``。只用標準函式庫。"""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    candidate = text.replace("Z", "+00:00") if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        parsed = None
    if parsed is None:
        for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d"):
            try:
                parsed = datetime.strptime(text, pattern)
                break
            except ValueError:
                continue
    if parsed is None:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError):
            parsed = None
    if parsed is None:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _resolve_now(now):
    if now is None:
        return datetime.now(timezone.utc)
    parsed = parse_timestamp(now)
    if parsed is None:
        raise ValueError("now 無法解析為時間")
    return parsed


def _reference_time(data: dict, entry: dict):
    """freshness 的參考時間：事件時間 > 發布時間 > 抓取時間。"""
    for key in ("event_time", "published_at"):
        parsed = parse_timestamp(data.get(key))
        if parsed is not None:
            return parsed, key
    # 有些 collector 只把時間放進 content_reference／content。
    for key in ("event_time", "published_at", "published"):
        parsed = parse_timestamp(_lookup(data, key))
        if parsed is not None:
            return parsed, "published_at" if key != "event_time" else "event_time"
    parsed = parse_timestamp(data.get("fetched_at"))
    if parsed is not None:
        return parsed, "fetched_at"
    return None, ""


# --------------------------------------------------------------------------------------
# Source lineage
# --------------------------------------------------------------------------------------


def _normalise_url(value: str) -> str:
    try:
        parts = urlsplit(str(value).strip())
    except ValueError:
        return str(value).strip().lower()
    host = (parts.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = (parts.path or "").rstrip("/").lower()
    query = "&".join(
        sorted(
            item for item in (parts.query or "").split("&")
            if item and not str(item).lower().startswith(_TRACKING_QUERY_PREFIXES)
        )
    )
    if not host and not path:
        return str(value).strip().lower()
    return "{}{}{}".format(host, path, ("?" + query) if query else "")


def _normalise_title(value: str) -> str:
    text = re.sub(r"[\W_]+", " ", str(value or "").lower(), flags=re.UNICODE)
    return " ".join(text.split())


def source_domain(data: dict) -> str:
    """從 ``source_url`` 取出正規化 domain（取不到時退回 ``source`` 名稱）。"""
    explicit = _lookup(data, "source_domain")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip().lower()
    try:
        host = (urlsplit(str(data.get("source_url") or "").strip()).netloc or "").lower()
    except ValueError:
        host = ""
    if host.startswith("www."):
        host = host[4:]
    if host:
        return host
    return str(data.get("source") or "").strip().lower()


def lineage_signature(evidence) -> tuple:
    """回傳 ``(signal_name, normalised_value)``：判斷兩筆 Evidence 是否屬於同一來源鏈。

    優先序刻意由「最能證明同一則原始消息」往下排：canonical / original source URL →
    交易或事件識別 → 內容雜湊 → 正規化標題 → domain。全部落空時退回 evidence_id
    （代表無從判斷，視為自己一條 lineage，不與他人合併）。
    """
    data = evidence_as_dict(evidence)

    explicit = str(data.get("source_lineage_id") or "").strip()
    if explicit:
        return ("explicit", explicit)

    for key in ("canonical_url", "original_source_url"):
        value = _lookup(data, key)
        if isinstance(value, str) and value.strip():
            return (key, _normalise_url(value))

    for key in ("transaction_hash", "tx_hash"):
        value = _lookup(data, key)
        if isinstance(value, str) and value.strip():
            return ("transaction_hash", value.strip().lower())

    event_id = _lookup(data, "event_id")
    if isinstance(event_id, str) and event_id.strip():
        return ("event_id", event_id.strip().lower())

    for key in ("content_hash", "quote_hash"):
        value = _lookup(data, key)
        if isinstance(value, str) and value.strip():
            return (key, value.strip().lower())

    for key in ("normalized_title", "title", "headline"):
        value = _lookup(data, key)
        if isinstance(value, str) and value.strip():
            normalised = _normalise_title(value)
            if normalised:
                return ("normalized_title", normalised)

    domain = source_domain(data)
    if domain:
        return ("source_domain", "{}|{}".format(domain, str(data.get("data_type") or "").lower()))

    return ("evidence_id", str(data.get("evidence_id") or "").lower())


def lineage_id_for(evidence) -> str:
    """把 lineage signature 壓成穩定短 ID（``lin:<signal>:<sha1-12>``）。"""
    signal, value = lineage_signature(evidence)
    if signal == "explicit":
        return value
    digest = hashlib.sha1("{}|{}".format(signal, value).encode("utf-8")).hexdigest()[:12]
    return "lin:{}:{}".format(signal, digest)


def group_by_lineage(evidence_items) -> dict:
    """把一批 Evidence 依來源鏈分群：``{lineage_id: [evidence_id, ...]}``（保留輸入順序）。

    不刪除 duplicates —— 全部保留，只是同群成員的 independence 會被稀釋。
    """
    groups = {}
    for item in evidence_items:
        data = evidence_as_dict(item)
        key = lineage_id_for(data)
        groups.setdefault(key, []).append(str(data.get("evidence_id") or ""))
    return groups


# --------------------------------------------------------------------------------------
# 五項分量
# --------------------------------------------------------------------------------------


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _has_source_locator(data: dict) -> bool:
    url = str(data.get("source_url") or "").strip()
    if url.lower() not in _UNUSABLE_LOCATOR_VALUES and ("://" in url or url.startswith("/")):
        return True
    return bool(_find_key(data, _LOCATOR_KEYS))


def _freshness(data: dict, entry: dict, now):
    reference, basis = _reference_time(data, entry)
    if reference is None:
        return 0.0, "", None, ["缺少任何可用時間，freshness 記為 0"]

    age_days = max((now - reference).total_seconds() / 86400.0, 0.0)
    policy = entry.get("freshness_policy") or {}
    try:
        fresh_within = float(policy.get("fresh_within_days", 1))
        stale_after = float(policy.get("stale_after_days", 7))
    except (TypeError, ValueError):
        fresh_within, stale_after = 1.0, 7.0
    if stale_after <= fresh_within:
        stale_after = fresh_within + 1.0

    if age_days <= fresh_within:
        score = 1.0
    elif age_days >= stale_after:
        score = FRESHNESS_FLOOR
    else:
        ratio = (age_days - fresh_within) / (stale_after - fresh_within)
        score = 1.0 - ratio * (1.0 - FRESHNESS_FLOOR)

    notes = []
    if basis == "fetched_at" and not bool(entry.get("fetched_at_is_observation")):
        # 這類來源的 fetched_at 只是「我什麼時候抓的」，不能用來主張事件新鮮。
        if score > FRESHNESS_CAP_WITHOUT_EVENT_TIME:
            notes.append("缺少 event_time／published_at，freshness 以 fetched_at 推估並設上限")
        score = min(score, FRESHNESS_CAP_WITHOUT_EVENT_TIME)

    return _clamp(score), basis, round(age_days, _ROUND), notes


def _components(data: dict, entry: dict, now, lineage_size: int):
    notes = []

    source_quality = _clamp(entry.get("source_quality", CONSERVATIVE_ENTRY["source_quality"]))
    if not entry.get("registry_hit"):
        notes.append("registry 未登錄此 source_type，改用保守基準")

    has_locator = _has_source_locator(data)
    traceability = _clamp(entry.get("traceability", CONSERVATIVE_ENTRY["traceability"]))
    if not has_locator:
        traceability = MIN_TRACEABILITY_WITHOUT_LOCATOR
        notes.append("找不到可重現的 source locator")
    elif str(data.get("verification_status") or "") == "verified":
        traceability = _clamp(traceability + TRACEABILITY_BONUS_VERIFIED)

    freshness, basis, age_days, freshness_notes = _freshness(data, entry, now)
    notes.extend(freshness_notes)

    method_transparency = _clamp(entry.get("method_transparency", CONSERVATIVE_ENTRY["method_transparency"]))
    if _find_key(data, _METHOD_KEYS):
        method_transparency = _clamp(method_transparency + METHOD_TRANSPARENCY_BONUS)
    else:
        notes.append("未提供計算方法或參數，method_transparency 未加分")

    size = max(int(lineage_size or 1), 1)
    independence_base = _clamp(entry.get("independence_base", CONSERVATIVE_ENTRY["independence_base"]))
    independence = max(round(independence_base / size, _ROUND), INDEPENDENCE_FLOOR)
    if size > 1:
        notes.append("與其他 {} 筆證據屬於同一來源鏈，independence 已稀釋".format(size - 1))

    components = {
        "source_quality": round(source_quality, _ROUND),
        "traceability": round(traceability, _ROUND),
        "freshness": round(freshness, _ROUND),
        "method_transparency": round(method_transparency, _ROUND),
        "independence": round(independence, _ROUND),
    }
    meta = {
        "has_source_locator": has_locator,
        "freshness_basis": basis,
        "age_days": age_days,
        "independence_factor": round(1.0 / size, _ROUND),
        "lineage_size": size,
    }
    return components, meta, notes


# --------------------------------------------------------------------------------------
# Hard caps
# --------------------------------------------------------------------------------------


def _hard_caps(data: dict, entry: dict, meta: dict, corroborating_lineage_count: int):
    """回傳生效的 cap 名稱清單（依 HARD_CAPS 的宣告順序）。"""
    hits = set()

    if parse_timestamp(data.get("fetched_at")) is None:
        hits.add("missing_fetched_at")

    if not meta.get("has_source_locator"):
        hits.add("missing_source_locator")

    source_type = entry.get("source_type", SOURCE_TYPE_UNKNOWN)
    if (
        source_type == SOURCE_TYPE_FALLBACK_FIXTURE
        or bool(entry.get("is_fallback"))
        or _flag(data, "fallback")
        or _flag(data, "is_fallback")
    ):
        hits.add("fallback_fixture")

    if bool(entry.get("is_social")):
        author = _find_key(data, _AUTHOR_KEYS)
        if not author or _flag(data, "anonymous"):
            hits.add("anonymous_or_low_trace_social")

    if bool(entry.get("is_secondary_news")) and int(corroborating_lineage_count or 1) <= 1:
        hits.add("single_secondary_news_source")

    verified = str(data.get("verification_status") or "") == "verified"
    if _find_key(data, _ENTITY_ATTRIBUTION_KEYS) and not verified:
        hits.add("unverifiable_entity_attribution")
    if _find_key(data, _INTENT_ATTRIBUTION_KEYS) and not verified:
        hits.add("unverifiable_intent_attribution")

    return [key for key in HARD_CAPS if key in hits]


# --------------------------------------------------------------------------------------
# 對外計分
# --------------------------------------------------------------------------------------


def score_evidence(
    evidence,
    registry=None,
    *,
    now=None,
    lineage_size: int = 1,
    lineage_id=None,
    corroborating_lineage_count: int = 1,
) -> dict:
    """為單筆 Evidence 計分。純函式：不修改 ``evidence``，也不寫任何全域狀態。

    參數
    ----
    evidence
        Evidence dataclass 實例或其 ``asdict()``。
    registry
        ``load_source_registry()`` 的結果；``None`` 時讀預設路徑。
    now
        計算 freshness 的當下時間（ISO 字串或 datetime）。測試必須傳入以固定結果。
    lineage_size
        本筆所屬來源鏈的成員數（含自己）。由 ``score_evidence_batch`` 自動算出；
        單筆呼叫時預設 1。
    corroborating_lineage_count
        支持同一則消息的**獨立來源鏈**數量。次級媒體只有一條鏈時會套 0.60 cap。
    """
    data = evidence_as_dict(evidence)
    resolved_now = _resolve_now(now)
    active_registry = registry if registry is not None else load_source_registry()
    entry = registry_entry(active_registry, data.get("source_type"))

    components, meta, notes = _components(data, entry, resolved_now, lineage_size)
    raw_score = round(
        sum(CREDIBILITY_WEIGHTS[key] * components[key] for key in CREDIBILITY_COMPONENT_KEYS),
        _ROUND,
    )

    limiters = _hard_caps(data, entry, meta, corroborating_lineage_count)
    rejected = any(key in REJECTING_HARD_CAP_KEYS for key in limiters)
    cap = min((HARD_CAPS[key] for key in limiters), default=None)
    final_score = raw_score if cap is None else round(min(raw_score, cap), _ROUND)
    if rejected:
        final_score = 0.0

    status = str(data.get("verification_status") or "").strip() or VERIFICATION_STATUS_UNVERIFIED
    if status not in VERIFICATION_STATUSES:
        notes.append("verification_status 非合法值，正規化為 unverified")
        status = VERIFICATION_STATUS_UNVERIFIED
    if rejected:
        status = VERIFICATION_STATUS_REJECTED

    return {
        "evidence_id": str(data.get("evidence_id") or ""),
        "source_type": entry.get("source_type", SOURCE_TYPE_UNKNOWN),
        "source_lineage_id": str(lineage_id) if lineage_id else lineage_id_for(data),
        "lineage_size": meta["lineage_size"],
        "independence_factor": meta["independence_factor"],
        "raw_score": raw_score,
        "final_score": final_score,
        "components": components,
        "hard_cap": cap,
        "score_limiters": limiters,
        "rejected": rejected,
        "verification_status": status,
        "freshness_basis": meta["freshness_basis"],
        "age_days": meta["age_days"],
        "known_limitations": list(entry.get("known_limitations") or []),
        "scoring_version": SCORING_VERSION,
        "notes": notes,
    }


def score_evidence_batch(evidence_items, registry=None, *, now=None) -> list:
    """為一批 Evidence 計分，並在計分前先做 lineage 分群。

    分群有兩個作用：同源轉載共用一條主要來源鏈（independence 稀釋），以及判斷次級媒體
    是否有獨立來源鏈佐證（沒有就套 ``single_secondary_news_source``）。

    回傳與輸入順序相同的結果清單 —— 本專案的腳註編號依證據順序決定，順序不能浮動。
    """
    items = [evidence_as_dict(item) for item in evidence_items]
    active_registry = registry if registry is not None else load_source_registry()
    resolved_now = _resolve_now(now)

    lineage_ids = [lineage_id_for(item) for item in items]
    sizes = {}
    for key in lineage_ids:
        sizes[key] = sizes.get(key, 0) + 1

    # 新聞類（含官方公告與主要媒體）的獨立來源鏈數量：判斷次級媒體是否被佐證。
    news_lineages = set()
    for item, key in zip(items, lineage_ids):
        entry = registry_entry(active_registry, item.get("source_type"))
        if bool(entry.get("is_news")):
            news_lineages.add(key)
    corroborating = max(len(news_lineages), 1)

    return [
        score_evidence(
            item,
            active_registry,
            now=resolved_now,
            lineage_size=sizes[key],
            lineage_id=key,
            corroborating_lineage_count=corroborating,
        )
        for item, key in zip(items, lineage_ids)
    ]
