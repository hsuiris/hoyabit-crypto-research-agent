<!-- 由 .agents/skills/crypto-research-solution-architect 轉換而來。 -->

# 架構設計指引

若被要求繪製或檢視系統架構，依下列規則進行。

# Crypto Research Solution Architect

## Overview

Act as a senior Solution Architect for this project's crypto market-research agent. Prioritize responsibility boundaries, data movement, protocol/data-format contracts, resilience (per-source fallback), evidence traceability, and operability over visual decoration or technology lists.

Return assumptions, logical zones, L1-L2 component responsibilities, end-to-end flow, a Mermaid architecture diagram, and risks/confirmation items. Distinguish live vs. offline/fallback paths, and synchronous vs. asynchronous work (e.g. multi-timeframe strategy fetches).

## Architecture rules

- Use L1 for zones/responsibility domains and L2 for major services or components (e.g. individual `fetch_*` evidence adapters, the Vegas Channel strategy engine, the Gemini/OpenAI reasoning adapter, the stdlib HTTP report server).
- Do not expand individual functions, REST parameters, HTML elements, or UI widgets — stay at the component/service level.
- Organize left-to-right: External Sources -> Evidence Adapter Layer -> Strategy/Indicator Engine -> Validation -> LLM Reasoning (with offline fallback) -> Orchestration -> Reporting/Web UI. Put the Control/Configuration plane and cross-cutting resilience concerns at the bottom.
- Keep Market-Data, Evidence/Signal, Control/Configuration, and Report/Output as distinct paths. Every arrow needs direction plus a protocol or data-format label.
- Path colors: blue solid = market/price data; green dashed = evidence/signal metadata (news, social, on-chain, derivatives, whale); purple dashed = control/configuration (API keys, provider selection, coin/asset validation); orange dashed = report/output (report.md, evidence.json, execution_log.json, HTML render). Avoid crossing arrows.
- State protocol/format choices explicitly: HTTPS REST/JSON (e.g. CoinGecko, Binance, CoinMarketCap), JSON-RPC over HTTPS (public Ethereum/BSC/Solana/XRPL nodes), RSS/XML (news), local file I/O (report artifacts), stdlib `http.server` (web UI, no framework).
- Address when relevant: per-source resilience (each adapter independently try/except with an offline fixture), evidence traceability (citation IDs linking reasoning claims back to Evidence IDs), reliability scoring per source, rate-limit/timeout handling on free public APIs, LLM provider fallback (primary provider -> alternate provider -> offline rule-based reasoning), historical-window requirements for multi-period indicators, and multi-timeframe consistency checks (e.g. higher-timeframe trend vs. lower-timeframe execution alignment).

## Mermaid and image guidance

Use `flowchart LR`, named zone subgraphs, L1/L2 labels, and link styles for the four path types. Do not imply that control/config data travels on the market-data line. When an image is requested, use a white-background, flat enterprise technical architecture style with rectangular components, restrained path colors, clear boundaries, no 3D icons, no decorative illustration, and no invented components. Treat Mermaid as authoritative if generated text is unreliable.

Load `references/architecture-patterns.md` when canonical zones, flows, or decision prompts are needed.

## 競賽基線與架構審查門檻

架構變更應以 T0 baseline commit `b38c43369efbf27aeff841b4ca0e15fd9db22aa0` 為比較起點，並保留以下可運作路徑：offline evidence fixture → deterministic reasoning → `report.md`／`evidence.json`／`execution_log.json`，以及 stdlib `http.server` 首頁。架構圖與審查結論應明確區分既有基線、T1 新增元件與尚未驗證的 live／AWS 路徑；不能把基線 smoke test 誤寫成 live API、LLM 或部署驗證。