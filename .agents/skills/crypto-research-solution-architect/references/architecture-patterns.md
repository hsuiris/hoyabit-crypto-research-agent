# Reference Patterns

## Canonical zones

External Sources | Evidence Adapter Layer | Strategy / Indicator Engine | Validation | LLM Reasoning (+ Offline Fallback) | Orchestration | Reporting / Web UI | Control Plane | Cross-Cutting Resilience

## Canonical paths

- Market data: exchange/price API (e.g. CoinGecko, Binance klines) -> adapter normalization -> strategy/indicator engine (e.g. Vegas Channel, RSI, volume-spike detection) -> evidence bundle -> reasoning input.
- Evidence/signal metadata: news RSS, social APIs (Reddit/Bluesky/Hacker News), on-chain RPC, derivatives/funding-rate, whale-wallet, long/short-ratio sources -> adapter normalization -> standardized evidence record (id, source, reliability score, content, citation) -> validation -> reasoning input.
- Control/configuration: environment variables (LLM provider, API keys, live/mock toggle, supported-coin whitelist) -> orchestration entrypoint -> adapter and reasoning-layer behavior.
- Report/output: validated evidence + reasoning -> report artifacts (Markdown report, evidence log, execution log) -> HTTP report server -> browser render (charts, citation links, evidence table).

## Common decisions to surface

Per-source fallback strategy (offline fixture vs. hard failure), evidence reliability scoring, citation/traceability enforcement (every claim must resolve to a real evidence ID), LLM provider selection and failure fallback, rate limits and timeouts on free public APIs, historical-window requirements for multi-period indicators (e.g. a Fibonacci EMA ribbon needing ~1000 bars to seed), timeframe-alignment rules between a trend timeframe and an execution timeframe, and coin/asset support scope (which chains or symbols are actually wired up vs. explicitly unsupported).
