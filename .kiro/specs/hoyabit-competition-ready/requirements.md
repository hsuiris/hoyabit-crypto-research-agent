# Requirements Document — HoyaBIT Competition-Ready Upgrade

## Introduction

本規格將既有 `hoyabit-crypto-research-agent` MVP 升級成競賽可執行版本。系統需在最多 15 分鐘內，針對 BTC、ETH、SOL、BNB、XRP 與現場指定題目，整合多來源資料，產生具證據支撐、可回溯、能說明不確定性的市場分析報告。

本升級必須沿用既有 Collector、Orchestrator、Web Demo、比較模式與 offline fallback，採最小侵入修改，不重建整個專案。

---

## Requirement 1 — Preserve the Existing MVP

**User Story:**  
As the hackathon team, I want the current working MVP preserved, so that competition upgrades do not consume time rebuilding already functional capabilities.

### Acceptance Criteria

1.1 WHEN an upgrade task is implemented, THE SYSTEM SHALL preserve existing Collector behavior unless the task explicitly requires a local compatibility change.

1.2 WHEN the Orchestrator is extended, THE SYSTEM SHALL preserve deterministic offline reasoning and existing failure fallback.

1.3 WHEN the Web Demo is modified, THE SYSTEM SHALL retain the current framework and existing routes.

1.4 WHEN a new component is needed, THE SYSTEM SHALL prefer a small new module and minimal integration changes over a large refactor.

1.5 IF an out-of-scope architecture change is proposed, THEN THE DEVELOPMENT AGENT SHALL reject it and continue with the smallest competition-ready implementation.

---

## Requirement 2 — Establish a Reversible Baseline

**User Story:**  
As the development coordinator, I want a verified baseline and branch, so that the team can recover immediately if an upgrade breaks the MVP.

### Acceptance Criteria

2.1 WHEN Task T0 starts, THE DEVELOPMENT AGENT SHALL record the branch, commit, Python version, test commands, test results, offline smoke result, and Web smoke result.

2.2 WHEN the baseline is valid, THE DEVELOPMENT AGENT SHALL work on branch `hackathon/competition-ready`.

2.3 IF baseline tests fail before any competition change, THEN THE DEVELOPMENT AGENT SHALL distinguish existing failures from regressions.

2.4 WHEN a Task completes, THE DEVELOPMENT AGENT SHALL create exactly one Task-specific commit and record its hash.

---

## Requirement 3 — Support Amazon Bedrock

**User Story:**  
As the competition operator, I want Amazon Bedrock to be the supported competition LLM provider, so that the application complies with the allowed AWS environment.

### Acceptance Criteria

3.1 WHEN `LLM_PROVIDER=bedrock`, THE SYSTEM SHALL use the AWS SDK credential chain and `bedrock-runtime`.

3.2 WHEN Bedrock is selected, THE SYSTEM SHALL require a model ID and region but SHALL NOT require Gemini or OpenAI API keys.

3.3 WHEN Bedrock returns structured JSON, THE SYSTEM SHALL validate and parse it using the existing application schema.

3.4 IF Bedrock returns invalid JSON or times out, THEN THE SYSTEM SHALL retry or repair at most once and then use deterministic fallback.

3.5 WHEN running unit tests, THE SYSTEM SHALL mock Bedrock and SHALL NOT require a real AWS call.

3.6 WHEN deployed with the provided AWS template, THE SYSTEM SHALL receive least-privilege Bedrock inference permission without embedded credentials.

---

## Requirement 4 — Plan Research from the Question

**User Story:**  
As a judge or user, I want the system to understand the question before collecting and analyzing evidence, so that the report addresses the actual task rather than a fixed template.

### Acceptance Criteria

4.1 WHEN a question is submitted, THE SYSTEM SHALL create a `ResearchPlan`.

4.2 THE ResearchPlan SHALL identify coins, task modes, primary question, time window, hypotheses, required domains, comparison dimensions, assumptions, and stop conditions.

4.3 THE allowed task modes SHALL include:
- `describe_market_state`
- `test_hypothesis`
- `compare_assets`
- `explain_driver`
- `assess_consistency`
- `identify_risks`
- `identify_attention_conditions`

4.4 WHEN a hypothesis-validation question is submitted, THE SYSTEM SHALL create support questions, contradiction questions, and falsification conditions.

4.5 WHEN a comparison question is submitted, THE SYSTEM SHALL use a shared time window and shared comparison dimensions for both assets.

4.6 IF the Planner fails or returns invalid output, THEN THE SYSTEM SHALL use a deterministic keyword-based fallback.

4.7 WHEN a run completes, THE SYSTEM SHALL save `research_plan.json`.

---

## Requirement 5 — Maintain Traceable Evidence

**User Story:**  
As a judge, I want every important conclusion traceable to its underlying data, so that I can audit source authenticity and relevance.

### Acceptance Criteria

5.1 EVERY Evidence item SHALL include source, fetched time, content reference, source type, verification status, and related Claim IDs.

5.2 WHEN Evidence originates from a webpage or announcement, THE SYSTEM SHALL retain source URL and a bounded quotation or section reference.

5.3 WHEN Evidence originates from an API, CSV, on-chain query, or dashboard, THE SYSTEM SHALL retain sufficient reproducibility metadata such as endpoint, parameters, time range, pair, address, transaction hash, query name, or raw file.

5.4 WHEN raw source content is saved, THE SYSTEM SHALL retain a SHA-256 hash or equivalent integrity reference.

5.5 IF an Evidence item lacks `fetched_at`, THEN THE SYSTEM SHALL reject it.

5.6 IF an Evidence item lacks a reproducible source locator, THEN THE SYSTEM SHALL cap its credibility at 0.30.

---

## Requirement 6 — Score Evidence Deterministically

**User Story:**  
As an analyst, I want evidence credibility to be explainable and reproducible, so that an LLM cannot arbitrarily decide which source is trustworthy.

### Acceptance Criteria

6.1 WHEN Evidence is processed, THE SYSTEM SHALL compute credibility using deterministic Python rules.

6.2 THE score SHALL expose source quality, traceability, freshness, method transparency, independence, raw score, final score, hard cap, and limiters.

6.3 THE SYSTEM SHALL apply hard caps for missing locator, anonymous or low-trace social content, single secondary-news sources, fallback fixtures, unverifiable entity attribution, and unverifiable intent attribution.

6.4 THE LLM SHALL NOT set the final Evidence credibility score.

6.5 GIVEN the same Evidence and configuration, THE SYSTEM SHALL produce exactly the same score.

6.6 WHEN Evidence is a fallback fixture, THE SYSTEM SHALL keep it in the Evidence List but SHALL NOT treat it as primary confirmation.

---

## Requirement 7 — Detect Source Lineage and Semantic Limits

**User Story:**  
As a judge, I want repeated reporting and source semantics handled correctly, so that copied stories or promotional claims do not inflate confidence.

### Acceptance Criteria

7.1 WHEN multiple articles derive from the same original source, THE SYSTEM SHALL assign them to one source lineage and reduce their independence weight.

7.2 WHEN an official organization publishes a statement, THE SYSTEM MAY treat publication authenticity as high confidence but SHALL NOT automatically treat the claimed outcome as verified.

7.3 WHEN an on-chain transfer is observed, THE SYSTEM MAY treat the transfer as verified but SHALL NOT automatically infer owner identity or transaction intent.

7.4 WHEN social data is used, THE SYSTEM SHALL limit it to discussion, attention, or sentiment claims unless external facts are independently confirmed.

7.5 WHEN event time, publication time, and fetch time differ, THE SYSTEM SHALL retain them separately.

---

## Requirement 8 — Build a Claim–Evidence Graph

**User Story:**  
As a report reader, I want facts, inferences, and conclusions separated, so that I can understand how the final judgment was formed.

### Acceptance Criteria

8.1 THE SYSTEM SHALL represent every major conclusion as a Claim with a stable Claim ID.

8.2 EACH Claim SHALL contain facts, inference, conclusion, supporting Evidence IDs, contradicting Evidence IDs, verdict, confidence, limitations, invalidation conditions, and watchpoints.

8.3 WHEN a Claim lacks acceptable supporting Evidence, THE SYSTEM SHALL set the verdict to `insufficient_evidence`.

8.4 WHEN hypothesis evidence is mixed, THE SYSTEM SHALL separately compute support strength and contradiction strength.

8.5 WHEN comparing two assets, THE SYSTEM SHALL preserve missing values as missing and SHALL NOT convert them to zero.

8.6 WHEN a run completes, THE SYSTEM SHALL save `claims.json`.

---

## Requirement 9 — Compute Claim Confidence Deterministically

**User Story:**  
As a judge, I want confidence to reflect evidence quality, coverage, diversity, and conflicts, so that the report does not pretend to be more certain than the data permits.

### Acceptance Criteria

9.1 THE SYSTEM SHALL calculate Claim confidence from weighted evidence quality, domain coverage, source diversity, signal consistency, and counter-evidence coverage.

9.2 IF only one domain supports a Claim, THEN THE SYSTEM SHALL cap confidence at 0.60.

9.3 IF high-quality supporting and contradicting evidence are both strong, THEN THE SYSTEM SHALL cap confidence at 0.70.

9.4 IF domain coverage is below 0.40, THEN THE SYSTEM SHALL use `insufficient_evidence`.

9.5 IF primary support consists only of fallback Evidence, THEN THE SYSTEM SHALL use `insufficient_evidence`.

9.6 THE Critic MAY reduce Claim confidence but SHALL NOT increase it.

9.7 THE report SHALL label confidence as a heuristic evidence score rather than a calibrated probability.

---

## Requirement 10 — Enforce Citation Gates

**User Story:**  
As a judge, I want unsupported or overclaimed statements blocked before publication, so that every report remains auditable.

### Acceptance Criteria

10.1 BEFORE publication, THE SYSTEM SHALL verify that every cited Evidence ID exists and belongs to the current run.

10.2 THE SYSTEM SHALL verify that each Evidence item includes source, fetched time, content reference, and the relevant Claim ID.

10.3 THE SYSTEM SHALL block rejected Evidence from supporting a primary Claim.

10.4 THE SYSTEM SHALL prevent fallback Evidence from being the sole support of a primary Claim.

10.5 THE SYSTEM SHALL detect claims that convert correlation into causation, official statements into achieved outcomes, or transfers into intent.

10.6 IF semantic Critic execution fails, THEN THE SYSTEM SHALL still publish a conservative report that has passed the structural gate.

10.7 THE Critic SHALL NOT create Evidence or raise confidence.

---

## Requirement 11 — Produce Competition Submission Artifacts

**User Story:**  
As the competition team, I want all required deliverables generated from one run, so that submission is complete and internally consistent.

### Acceptance Criteria

11.1 WHEN a run completes, THE SYSTEM SHALL generate:
- `report.md`
- `evidence.json`
- `execution_log.json`
- `research_plan.json`
- `claims.json`
- `manifest.json`

11.2 THE report SHALL include market judgment, key evidence, sources and times, fact-to-inference-to-conclusion reasoning, positive and negative evidence, confidence and limitations, invalidation conditions, and watchpoints.

11.3 THE manifest SHALL include run metadata, provider/model, code commit, scoring/config versions, file list, file hashes, and validation status.

11.4 THE Execution Log SHALL include stage timestamps, durations, tools or collectors, source/query summaries, statuses, fallback reasons, created Evidence IDs, and LLM runtime metadata.

11.5 ALL artifacts SHALL refer to the same run ID.

---

## Requirement 12 — Expose the Analysis in the Existing Web Demo

**User Story:**  
As a presenter, I want to demonstrate the plan, evidence, claims, confidence, and logs in the existing UI, so that judges can inspect the full reasoning chain.

### Acceptance Criteria

12.1 WHEN a completed run is opened, THE Web Demo SHALL display the Research Plan.

12.2 THE Web Demo SHALL display Claim cards with facts, inference, conclusion, supporting and contradicting Evidence, limitations, and invalidation conditions.

12.3 THE Web Demo SHALL display confidence components and hard-cap limiters.

12.4 THE Web Demo SHALL display traceable Evidence metadata and Execution Log entries.

12.5 IF old or partial data lacks new fields, THEN THE Web Demo SHALL show `N/A` instead of crashing.

12.6 IF the Web Demo cannot be used, THEN the generated report and JSON artifacts SHALL remain independently readable.

---

## Requirement 13 — Manage Formal Runs and Deadlines

**User Story:**  
As the official operator, I want each formal attempt uniquely recorded and completed before the time limit, so that no result is overwritten and the team always retains a submission.

### Acceptance Criteria

13.1 THE SYSTEM SHALL support `test` and `formal` run modes.

13.2 EACH run SHALL use a unique run ID and output directory.

13.3 WHEN a formal run with the same question hash already exists, THE SYSTEM SHALL reject an accidental duplicate unless an authorized rerun is declared.

13.4 WHEN a rerun is authorized, THE SYSTEM SHALL preserve `rerun_of`, `rerun_reason`, and the first attempt.

13.5 THE official hard deadline SHALL be no more than 900 seconds.

13.6 THE internal finalization deadline SHALL be no later than 840 seconds.

13.7 WHEN the finalization deadline approaches, THE SYSTEM SHALL stop optional LLM Critic or follow-up work and prioritize saving all artifacts.

13.8 A partially degraded but auditable result SHALL be preferred over an unfinished ideal result.

---

## Requirement 14 — Remain Fault Tolerant

**User Story:**  
As the competition team, I want the system to finish even when external services fail, so that one network or model failure does not consume the single formal attempt.

### Acceptance Criteria

14.1 IF one Collector times out or fails, THEN the remaining Collectors SHALL continue and the failed domain SHALL be marked missing or unavailable.

14.2 IF Bedrock fails, THEN deterministic offline analysis SHALL remain available.

14.3 IF the Planner fails, THEN deterministic planning fallback SHALL be used.

14.4 IF the Report Agent fails, THEN a deterministic report renderer SHALL generate the required report.

14.5 IF an optional domain is unavailable, THEN the report SHALL disclose the gap and reduce confidence.

14.6 ALREADY collected Evidence SHALL remain saved after later-stage failure.

---

## Requirement 15 — Verify and Freeze the Demo

**User Story:**  
As the team lead, I want structured competition testing and a frozen backup demo, so that the final presentation remains reliable.

### Acceptance Criteria

15.1 THE SYSTEM SHALL pass smoke tests for all five allowed coins.

15.2 THE SYSTEM SHALL pass representative multi-source, hypothesis-validation, comparison, risk, and ambiguous-question tests.

15.3 THE test suite SHALL cover Bedrock timeout, invalid JSON, source outage, stale data, duplicated lineage, unknown Evidence ID, fallback sole support, high-quality conflict, and approaching deadline.

15.4 BEFORE feature freeze, THE team SHALL save a successful live run and offline backup fixtures.

15.5 AFTER Demo Freeze, THE DEVELOPMENT AGENT SHALL NOT add new product functionality.

15.6 THE final runbook SHALL document install, environment, offline run, live run, Web start, formal run, authorized rerun, artifacts, and backup-demo procedure.
