# ETH 市場研究報告

## 研究問題
分析 ETH 近兩週市場表現，整合價格、鏈上、主要新聞與討論熱度，說明訊號一致程度。

## 分析標的與題目
- 分析標的：ETH
- 研究問題：分析 ETH 近兩週市場表現，整合價格、鏈上、主要新聞與討論熱度，說明訊號一致程度。
- 任務類型：describe_market_state、assess_consistency

## 資料截止與分析區間
- Run ID：RUN-20260801T142914Z-ETH-eccf4dd5
- 執行開始：2026-08-01T14:29:14.356687+00:00
- 執行完成：2026-08-01T14:29:21.141308+00:00
- 資料截止（as-of）：2026-08-01T14:29:14.356687+00:00
- 研究時間窗：14 天（來源：explicit）
- 證據取得時間範圍：2026-08-01T14:29:14.655080+00:00 → 2026-08-01T14:29:21.094217+00:00
- 執行模式：live（live=True、use_llm=False）
- 推理提供者：deterministic／N/A
- 執行性質：N/A／狀態：COMPLETED

## 立場
**中性（Neutral）**　信心 0.5

- 依據：多方 0.7 / 空方 0.9，淨優勢僅 13%（門檻 25%），方向不明確
- 訊號權重：多方 0.7 / 空方 0.9（共 7 項訊號）

## 市場判斷
ETH 整體訊號偏空（空方權重 0.9 / 多方 0.7）；技術面大小時區訊號不一致，RSI 位於中性區間，1H 成交量無異常爆量。支持該方向的獨立訊號 1 項，反向訊號 1 項。

## 關鍵依據
- 本次沒有形成方向的主要推力（方向性訊號不足或多空互相抵銷）。
- 跨來源一致程度：矛盾（偏多領域 1 個、偏空領域 1 個，跨來源訊號互相矛盾。）

## 事實（Fact）
- EV-MARKET-001: 已取得市場價格資料（期間報酬率 1.41%）。
- EV-NEWS-001: 近期新聞標題包含 『Tether earns $1.5B in Q2 as US Treasury holdings fuel profits』；『New York sues Kalshi over alleged illegal gambling operation』；『US senators sent revised ethics rules to White House for CLARITY Act: Report』。
- EV-MACRO-ETH-001: 全市場恐懼貪婪指數 27（Fear），14 天變化 -1，聯準會最新貨幣政策發布為『Federal Reserve issues FOMC statement』。
- EV-ANNOUNCE-ETH-001: 官方公告（第一方官方 feed）近期 5 則，包含 『Ethereum Foundation Board Update』；『Devcon 8 Tickets Are Live: Find Your Path to Mumba』。
- EV-ONCHAIN-ETH-001: 已取得 onchain 觀測值。
- EV-SOCIAL-ETH-HN-001: 公開討論情緒為 mixed（依據：未偵測到明顯正負向關鍵字）。
- EV-DERIV-ETH-001: 資金費率 0.0067%，多空接近平衡。
- EV-WHALE-ETH-001: 已知大戶錢包餘額（Binance 7 持有 1,996,008.3858、Binance Hot Wallet 20 持有 739,595.9471）。
- EV-VEGAS-ETH-001: Vegas 通道 4H 趨勢為訊號不一，1H RSI(6) 72.737，1H 成交量無異常爆量，最近訊號為空頭停利（22 根K棒前），4h為訊號不一(mixed)，1h為多頭，方向不一致。
- EV-LSRATIO-ETH-001: 大戶多空比 57.6% 多 / 42.4% 空（多方持倉佔優），近期一致性 100.0%。
- EV-TVL-ETH-001: 已取得 tvl 觀測值。

## 推論（Inference）
- 大戶持倉 57.6% 偏多（近期一致性 100.0%），持倉過度集中於單邊時反向風險上升，故此項支持偏空判讀（EV-LSRATIO-ETH-001）。
- 期間報酬 1.41%，價格接近持平（EV-MARKET-001），此項不提供方向性資訊，僅調整信心水準。
- 4H 與 1H 方向不一致，趨勢訊號本身互相矛盾（EV-VEGAS-ETH-001），此項不提供方向性資訊，僅調整信心水準。
- 由於同時存在 1 項反向訊號，上述方向僅為權重加總後的淨結果，而非一致性結論；信心分數已因此下修至 0.5。

## 結論（Conclusion）
ETH 整體訊號偏空（空方權重 0.9 / 多方 0.7）；技術面大小時區訊號不一致，RSI 位於中性區間，1H 成交量無異常爆量。支持該方向的獨立訊號 1 項，反向訊號 1 項。

## 主張（Claim）
### CL-001｜ETH 目前訊號偏空（支持側權重 0.90，反向側 0.70）
- 判定：**mixed**　信心 0.6（medium／heuristic，類型 market_judgment）
- 事實：大戶持倉 57.6% 偏多（近期一致性 100.0%），持倉過度集中於單邊時反向風險上升（EV-LSRATIO-ETH-001）
- 推論：支持側與反向側的權重差距構成本次方向判讀；權重來自既有訊號盤點，非模型自由生成。
- 結論：針對「分析 ETH 近兩週市場表現，整合價格、鏈上、主要新聞與討論熱度，說明訊號一致程度。」，本次證據偏空，但仍保留反向側證據供讀者檢視。
- 支持證據：EV-LSRATIO-ETH-001
- 反方證據：EV-TVL-ETH-001
- 信心分量：weighted_evidence_quality=0.8525／domain_coverage=0.5／source_diversity=0.3333／signal_consistency=0.4928／counter_evidence_coverage=1.0
- 生效上限：single_supporting_domain、high_quality_conflict
- 限制：本 Claim 由 deterministic fallback 產生，未經模型敘事分層。
- 限制：立場判定依據：多方 0.7 / 空方 0.9，淨優勢僅 13%（門檻 25%），方向不明確
- 推翻條件：反向側權重超過支持側時，本判斷即被推翻。
- 觀察重點：Ethereum 鏈上鎖倉量近 30 天成長 9.93%，資金實際流入鏈上而非僅有價格波動

_Claim 的 verdict 與信心由 deterministic Python 計算（claim-confidence-v1），LLM 只提供敘述；信心是 heuristic evidence score，不是市場正確機率。_

## 跨來源一致程度
- 判定：**矛盾**
- 依據：偏多領域 1 個、偏空領域 1 個，跨來源訊號互相矛盾。
- 一致度：50.0%（2 個領域有方向，共 5 個領域有證據）
- derivatives：偏空（多 0.0 / 空 0.9）
- macro：無方向（多 0.0 / 空 0.0）
- market：無方向（多 0.0 / 空 0.0）
- news：無方向（多 0.0 / 空 0.0）
- onchain：偏多（多 0.7 / 空 0.0）

## 稽核（Critic）
**語意稽核未執行**　狀態：skipped:disabled

本次沒有可用的語意稽核結果，報告改以 structural citation gate 與 deterministic 語意風險偵測作為唯一檢核，結論維持保守呈現。

### 語意風險（deterministic 偵測 + 稽核標記）
- 本次未偵測到已知的過度推論樣式

## 引用檢核（Citation Gate）
- 結果：**PASS（全部引用可追溯）**
- Gate 版本：citation-gate-v1
- 檢查範圍：1 個 Claim、11 筆 Evidence（其中 11 筆被引用）
- claim_has_supporting_evidence：pass
- claim_structure：pass
- confidence_within_cap：pass
- evidence_belongs_to_run：pass
- evidence_id_exists：pass
- evidence_not_rejected：pass
- evidence_required_fields：pass
- fallback_not_sole_support：pass
- no_cross_run_citation：pass
- related_claim_ids_consistent：pass
- support_contradiction_exclusive：pass

## 信心
0.5

## 指標
- start_price: 1844.7415112609306
- end_price: 1870.697705010294
- return_pct: 1.41

## 反方證據
- 反方訊號｜Ethereum 鏈上鎖倉量近 30 天成長 9.93%，資金實際流入鏈上而非僅有價格波動（EV-TVL-ETH-001）
- 未定訊號｜期間報酬 1.41%，價格接近持平（EV-MARKET-001）
- 未定訊號｜4H 與 1H 方向不一致，趨勢訊號本身互相矛盾（EV-VEGAS-ETH-001）
- 未定訊號｜資金費率 0.0067%，多空成本接近平衡，衍生品未顯示明顯偏斜（EV-DERIV-ETH-001）
- 推理模式｜LLM disabled，本結論由規則式訊號加總產生，未經 LLM 語意推理交叉檢驗。

## 證據來源
- [#1] EV-MARKET-001: [CoinGecko](https://api.coingecko.com/api/v3/coins/ethereum/market_chart?vs_currency=usd&days=14) (reliability: 0.8775) ｜ 類別 market_api｜狀態 未交叉驗證｜取得 2026-08-01T14:29:17.881090+00:00｜原始 0.8775｜新鮮度依 事件時間（0.6 天前）｜分量 source_quality=0.85／traceability=0.9／freshness=1.0／method_transparency=0.75／independence=0.85｜內容依據 endpoint、points、query｜使用於 （無 Claim 引用）
- [#2] EV-NEWS-001: [Google News RSS + publisher feeds](https://news.google.com/rss/search?q=ETH%20crypto) (reliability: 0.58) ｜ 類別 secondary_media｜狀態 未交叉驗證｜取得 2026-08-01T14:29:20.995140+00:00｜原始 0.58｜新鮮度依 發布時間（0.89 天前）｜分量 source_quality=0.5／traceability=0.55／freshness=1.0／method_transparency=0.35／independence=0.4｜內容依據 article_count、feed_url、items、with_fulltext、with_lede｜使用於 （無 Claim 引用）
- [#3] EV-MACRO-ETH-001: [Alternative.me Fear & Greed + Federal Reserve press releases](https://api.alternative.me/fng/?limit=14) (reliability: 0.8325) ｜ 類別 macro_api｜狀態 未交叉驗證｜取得 2026-08-01T14:29:18.835158+00:00｜原始 0.8325｜新鮮度依 事件時間（0.6 天前）｜分量 source_quality=0.8／traceability=0.85／freshness=1.0／method_transparency=0.7／independence=0.75｜內容依據 fear_greed_endpoint、fed_feed、fed_status、points｜使用於 （無 Claim 引用）
- [#4] EV-ANNOUNCE-ETH-001: [Ethereum Foundation Blog](https://blog.ethereum.org/en/feed.xml) (reliability: 0.8081) ｜ 類別 official_announcement｜狀態 未交叉驗證｜取得 2026-08-01T14:29:21.094217+00:00｜原始 0.8081｜新鮮度依 發布時間（3.6 天前）｜分量 source_quality=0.85／traceability=0.85／freshness=0.9905／method_transparency=0.55／independence=0.6｜內容依據 entry_count、feed_url、first_party｜使用於 （無 Claim 引用）
- [#5] EV-ONCHAIN-ETH-001: [Ethereum](https://ethereum.publicnode.com) (reliability: 0.925) ｜ 類別 blockchain_raw｜狀態 未交叉驗證｜取得 2026-08-01T14:29:14.655080+00:00｜原始 0.925｜新鮮度依 取得時間（無事件時間）（0.0 天前）｜分量 source_quality=0.9／traceability=0.95／freshness=1.0／method_transparency=0.85／independence=0.9｜內容依據 chain、endpoint｜使用於 （無 Claim 引用）
- [#6] EV-SOCIAL-ETH-HN-001: [Hacker News Algolia search](https://hn.algolia.com/api/v1/search_by_date?query=ETH%20crypto&tags=(story,comment)&hitsPerPage=25) (reliability: 0.35) ｜ 類別 social_public｜狀態 未交叉驗證｜取得 2026-08-01T14:29:19.083239+00:00｜原始 0.4875｜上限 0.35（anonymous_or_low_trace_social）｜新鮮度依 事件時間（0.53 天前）｜分量 source_quality=0.35／traceability=0.4／freshness=1.0／method_transparency=0.25／independence=0.45｜內容依據 endpoint、post_count、posts、query｜使用於 （無 Claim 引用）
- [#7] EV-DERIV-ETH-001: [Binance Futures](https://fapi.binance.com/fapi/v1/premiumIndex?symbol=ETHUSDT) (reliability: 0.8375) ｜ 類別 derivatives_api｜狀態 未交叉驗證｜取得 2026-08-01T14:29:15.424190+00:00｜原始 0.8375｜新鮮度依 取得時間（無事件時間）（0.0 天前）｜分量 source_quality=0.8／traceability=0.85／freshness=1.0／method_transparency=0.7／independence=0.8｜內容依據 endpoint、normalised_to_hours、provider、provider_native_interval_hours、raw_funding_rate、symbol｜使用於 （無 Claim 引用）
- [#8] EV-WHALE-ETH-001: [Public balance check (curated known addresses)](https://ethereum.publicnode.com) (reliability: 0.6) ｜ 類別 blockchain_raw｜狀態 未交叉驗證｜取得 2026-08-01T14:29:14.975731+00:00｜原始 0.925｜上限 0.6（unverifiable_entity_attribution）｜新鮮度依 取得時間（無事件時間）（0.0 天前）｜分量 source_quality=0.9／traceability=0.95／freshness=1.0／method_transparency=0.85／independence=0.9｜內容依據 addresses｜使用於 （無 Claim 引用）
- [#9] EV-VEGAS-ETH-001: [Binance Klines (Vegas Channel + RSI strategy)](https://api.binance.com/api/v3/klines?symbol=ETHUSDT) (reliability: 0.8925) ｜ 類別 market_api｜狀態 未交叉驗證｜取得 2026-08-01T14:29:14.988787+00:00｜原始 0.8925｜新鮮度依 取得時間（無事件時間）（0.0 天前）｜分量 source_quality=0.85／traceability=0.9／freshness=1.0／method_transparency=0.85／independence=0.85｜內容依據 bars_per_timeframe、symbol｜使用於 （無 Claim 引用）
- [#10] EV-LSRATIO-ETH-001: [Binance Futures Data (top-trader position ratio)](https://fapi.binance.com/futures/data/topLongShortPositionRatio?symbol=ETHUSDT&period=1h&limit=24) (reliability: 0.8525) ｜ 類別 derivatives_api｜狀態 未交叉驗證｜取得 2026-08-01T14:29:15.230927+00:00｜原始 0.8525｜新鮮度依 取得時間（無事件時間）（0.0 天前）｜分量 source_quality=0.8／traceability=0.85／freshness=1.0／method_transparency=0.8／independence=0.8｜內容依據 endpoint、points｜使用於 CL-001
- [#11] EV-TVL-ETH-001: [DefiLlama chain TVL (Ethereum)](https://api.llama.fi/v2/historicalChainTvl/Ethereum) (reliability: 0.8775) ｜ 類別 market_api｜狀態 未交叉驗證｜取得 2026-08-01T14:29:16.514359+00:00｜原始 0.8775｜新鮮度依 事件時間（0.6 天前）｜分量 source_quality=0.85／traceability=0.9／freshness=1.0／method_transparency=0.75／independence=0.85｜內容依據 endpoint、points｜使用於 CL-001

## 後續觀察重點
- 優先追蹤反向訊號是否強化：Ethereum 鏈上鎖倉量近 30 天成長 9.93%，資金實際流入鏈上而非僅有價格波動（EV-TVL-ETH-001）。
- 確認下一個 24 小時價格與成交量是否延續偏空方向，否則視為假突破。
- 追蹤全市場恐懼貪婪指數是否脫離目前的 27 區間，總體風險偏好轉向會同步改變個別幣種的訊號解讀。
- 追蹤官方公告與鏈上活躍度是否與價格方向同步，若背離則優先相信鏈上與官方事件。

## 風險與限制
- 多空訊號強度接近（多方 0.7 / 空方 0.9），方向判斷的解析度不足，不宜據此做單邊推論。
- 4H 與 1H 時區方向不一致，策略層面屬於不進場條件，任何方向性結論的可信度都應下修。
- 本 MVP 僅建立訊號的同時性關聯，不足以建立因果關係。
- 社群與新聞來源的可靠度低於市場與鏈上資料，權重已相應調低。
- 以下可信度上限已生效，相關敘述不得超過證據能證明的範圍：anonymous_or_low_trace_social×1、unverifiable_entity_attribution×1。

## 可能推翻結論的條件
- 反向側權重超過支持側時，本判斷即被推翻。（CL-001）

_本報告為研究支援，非投資建議；不構成買賣訊號，也不對未來市場表現做出保證。_
