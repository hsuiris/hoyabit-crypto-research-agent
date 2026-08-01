---
inclusion: always
---

# Evidence and Confidence Standards

HoyaBIT 的核心不是新聞摘要，而是可回溯的證據管理與可解釋市場判斷。

## Evidence 最低契約

每筆 Evidence 至少必須具有：

```text
evidence_id
source
source_url 或其他可重現 source locator
fetched_at
content_reference
related_claim_ids
source_type
verification_status
reliability_score
```

API、CSV、鏈上或 Dashboard 類資料必須額外保留可重現條件，例如：

```text
endpoint
query parameters
symbol / trading pair
time range
interval
chain
address
transaction hash
query name
raw file
```

## 語意分層

不得混淆：

- Observable Fact：資料直接顯示的內容。
- Source Statement：某來源或官方聲稱的內容。
- Inference：由一筆或多筆 Evidence 推導的解釋。
- Forecast：對未來的判斷。

例：

```text
「官方發布合作公告」可視為高可信 Source Statement。
「合作已產生商業成果」需要額外 Evidence。
「幣價將因此上漲」是 Inference 或 Forecast。
```

鏈上轉帳可以高可信證明轉帳發生，不能單獨證明：

- 地址實際擁有者。
- 交易意圖。
- 即將買入或拋售。

## LLM 與程式責任

LLM 可以：

- 將內容拆成 atomic claims。
- 分類 Fact、Source Statement、Inference、Forecast。
- 判斷 Evidence 對 Claim 是 supports、contradicts、context 或 irrelevant。
- 提出替代解釋、限制、推翻條件與觀察點。

LLM 不可以：

- 決定最終 reliability score。
- 決定最終 claim confidence。
- 修改 Raw Evidence。
- 自行將 unverified Evidence 改為 verified。
- 引用不存在的 Evidence ID。
- 隱藏高品質反方 Evidence。

最終分數與 hard cap 必須由 deterministic Python 程式計算。

## Credibility 計算

最低公式：

```text
base_score =
  0.30 × source_quality
+ 0.25 × traceability
+ 0.20 × freshness
+ 0.15 × method_transparency
+ 0.10 × independence
```

每個分數必須保存：

```text
raw_score
final_score
components
hard_cap
score_limiters
scoring_version
```

## Hard Caps

至少實作：

```yaml
missing_source_locator: 0.30
missing_fetched_at: rejected
anonymous_or_low_trace_social: 0.35
single_secondary_news_source: 0.60
fallback_fixture: 0.20
unverifiable_entity_attribution: 0.60
unverifiable_intent_attribution: 0.55
high_quality_conflict_claim_cap: 0.70
```

Hard cap 是上限，不可被加權平均突破。

## Source Lineage

多篇文章引用同一原始消息，只能視為一個主要來源鏈。

Lineage 可使用：

- canonical URL
- original source URL
- normalized title
- article/content hash
- quote hash
- source domain
- event ID
- transaction hash

不得將 20 篇同源轉載當成 20 個獨立確認。

## Claim Confidence

最低公式：

```text
confidence =
  0.30 × weighted_evidence_quality
+ 0.25 × domain_coverage
+ 0.20 × source_diversity
+ 0.15 × signal_consistency
+ 0.10 × counter_evidence_coverage
```

再套用：

- 只有一個 domain 支持：`confidence <= 0.60`
- 高品質支持與反對同時強：`confidence <= 0.70`
- domain coverage `< 0.40`：`insufficient_evidence`
- 主要 Evidence 全為 fallback：`insufficient_evidence`
- Critic 只能降低 confidence，不能提高。

信心是 heuristic evidence score，不得宣稱為真實市場正確機率。

## 報告引用規則

- 每個主要 Claim 必須引用 Evidence ID。
- Fact 的語氣不可強於 Evidence。
- 同期發生不可自動寫成因果。
- rejected Evidence 不可進入主要報告。
- fallback Evidence 不可作為主要 Claim 的唯一支持。
- 有高品質反方 Evidence 時必須顯示。
- 資料不足時必須輸出 `insufficient_evidence`，不能硬給方向。
