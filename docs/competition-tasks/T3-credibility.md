# T3 — Evidence Schema、Credibility Engine 與 Source Lineage

## Metadata

```yaml
task_id: T3
depends_on: [T2]
target_minutes: 90
target_commit: "feat(T3): add explainable evidence credibility engine"
```

## Goal

把目前固定或不透明的 `reliability_score` 升級成 deterministic、可展開、具有 hard caps 與來源 lineage 的 Evidence credibility。

## New Files

```text
src/credibility.py
config/source_registry.json
tests/test_credibility.py
```

## Local Modifications

- Evidence dataclass／schema 所在檔案
- Collector 建立 Evidence 的最小 metadata 增補
- `src/validation.py`
- `src/orchestrator.py`

## Compatibility Constraint

若既有 Evidence 使用 positional constructor：

- 不得刪除既有欄位。
- 不得重新排序既有欄位。
- 新欄位全部加在尾端。
- 每個新欄位都有 default。

## New Evidence Fields

```python
source_type: str = "unknown"
published_at: str | None = None
event_time: str | None = None
verification_status: str = "unverified"
source_lineage_id: str = ""
claim_relevance: float = 0.0
independence_factor: float = 1.0
score_breakdown: dict = field(default_factory=dict)
score_limiters: list[str] = field(default_factory=list)
related_claim_ids: list[str] = field(default_factory=list)
```

## Source Registry Categories

至少：

```text
market_api
derivatives_api
blockchain_raw
official_announcement
major_media
secondary_media
social_public
macro_api
local_csv
fallback_fixture
unknown
```

每類可配置：

```text
source_quality
traceability
method_transparency
freshness policy
known limitations
```

## Deterministic Score

```text
base_score =
  0.30 × source_quality
+ 0.25 × traceability
+ 0.20 × freshness
+ 0.15 × method_transparency
+ 0.10 × independence
```

輸出：

```text
raw_score
final_score
components
hard_cap
score_limiters
verification_status
scoring_version
```

## Hard Caps

```yaml
missing_source_locator: 0.30
missing_fetched_at: rejected
anonymous_or_low_trace_social: 0.35
single_secondary_news_source: 0.60
fallback_fixture: 0.20
unverifiable_entity_attribution: 0.60
unverifiable_intent_attribution: 0.55
```

Hard cap 必須用 `min(raw_score, cap)` 或等效 deterministic 邏輯。

## Source Locator

可接受：

- URL。
- API endpoint + parameters。
- local file path。
- transaction hash。
- chain/address/query name。
- dashboard query + range。

若現有 schema 不適合增加獨立 locator 欄位，可先存入 `content_reference`，但 validator 必須能判斷是否足以重現。

## Time Semantics

分開保存：

```text
event_time
published_at
fetched_at
```

Freshness 優先使用 event/published time；不能只因剛抓取舊新聞就得到高 freshness。

## Semantic Rules

- Blockchain transfer：高可信證明轉帳，不直接證明 owner 或 intent。
- Official announcement：高可信證明發布，不直接證明成果。
- Social：可描述討論/熱度/情緒，不單獨確認外部事件。
- Technical indicator：計算可重現，不等於預測必然正確。
- Fallback fixture：可保留流程，但不能當主要實證。

## Source Lineage

簡化判斷：

- canonical URL
- normalized title
- source domain
- original-source reference
- quote/content hash
- transaction hash
- event ID

不要刪除 duplicates；保存相同 lineage 並降低 independence。

## Orchestrator Integration

在分析前：

```text
raw Evidence
→ metadata validation
→ enrich and score
→ lineage
→ allowed/rejected set
```

LLM 不可寫 final score。

## Required Tests

- [ ] 舊 positional constructor 相容。
- [ ] 無 source locator → cap 0.30。
- [ ] 無 fetched_at → rejected。
- [ ] fallback → cap 0.20。
- [ ] 單一 secondary news → cap 0.60。
- [ ] 20 篇同源轉載 → one main lineage。
- [ ] official statement semantics。
- [ ] on-chain transfer vs intent。
- [ ] social factual limit。
- [ ] stale news freshness。
- [ ] same input → exactly same score。
- [ ] score components 和 limiter 可序列化。
- [ ] full suite no regression。

## Acceptance Criteria

- [ ] Evidence List 能解釋分數。
- [ ] hard caps 一定生效。
- [ ] fallback 不會高分。
- [ ] 同源轉載不會灌高獨立性。
- [ ] Raw Evidence 未被 LLM 改寫。
- [ ] validator 能拒絕缺少核心 metadata 的 Evidence。
- [ ] commit hash 已回報。

## Stop Condition

完成後停止。不得開始 T4。
