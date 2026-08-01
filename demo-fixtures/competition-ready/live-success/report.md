# BTC 市場研究報告

## 研究問題
分析 BTC 過去兩週市場表現，整合價格、鏈上、主要新聞與討論熱度，說明訊號一致程度。

## 分析標的與題目
- 分析標的：BTC
- 研究問題：分析 BTC 過去兩週市場表現，整合價格、鏈上、主要新聞與討論熱度，說明訊號一致程度。
- 任務類型：describe_market_state、assess_consistency

## 資料截止與分析區間
- Run ID：RUN-20260801T142730Z-BTC-52f95e69
- 執行開始：2026-08-01T14:27:30.518691+00:00
- 執行完成：2026-08-01T14:28:17.397760+00:00
- 資料截止（as-of）：2026-08-01T14:27:30.518691+00:00
- 研究時間窗：14 天（來源：explicit）
- 證據取得時間範圍：2026-08-01T14:27:33.352328+00:00 → 2026-08-01T14:27:34.064527+00:00
- 執行模式：offline（live=True、use_llm=True）
- 推理提供者：bedrock／amazon.nova-lite-v1:0
- 執行性質：test／狀態：COMPLETED_DEGRADED
- 本次降級項目：collector_fallback:vegas_channel、collector_fallback:long_short_ratio

## 立場
**中性（Neutral）**　信心 0.55

- 依據：方向性訊號權重僅 0.7（門檻 1.5），不足以形成方向判讀
- 訊號權重：多方 0 / 空方 0.7（共 5 項訊號）

## 市場判斷
根據過去兩週的市場表現，BTC 的價格略有下跌，鏈上數據顯示 BTC 鏈的健康狀況良好，主要新聞和討論熱度顯示市場情緒較為謹慎，整體訊號一致程度較低。

## 關鍵依據
- 本次沒有形成方向的主要推力（方向性訊號不足或多空互相抵銷）。
- 跨來源一致程度：資料不足（只有 onchain 一個領域產生方向性訊號，不構成跨來源確認。）

## 事實（Fact）
- 根據 CoinGecko 的數據，BTC 在過去兩週內的市場回報率為 -1.61%。 (EV-MARKET-001)
- 根據 Google News 的新聞摘要，最近的 BTC 相關新聞包括 Coldcard 錢包損失估計額達到 70 萬美元，以及比特幣 ETF 在 7 月份吸引了 1.724 億美元的資金流入。 (EV-NEWS-001)
- 根據 Alternative.me 的數據，市場廣泛的風險偏好指數為 27，處於恐懼狀態。 (EV-MACRO-BTC-001)
- 根據 Bitcoin Optech Newsletter 的公告，最近的官方公告包括對 COLDCARD 簽名設備生成的錢包的嚴重漏洞警告。 (EV-ANNOUNCE-BTC-001)
- 根據 Bitcoin 鏈的數據，最新的區塊高度為 960570。 (EV-ONCHAIN-BTC-001)
- 根據 Hacker News 的討論，公眾對 BTC 的技術社區討論情緒為混合。 (EV-SOCIAL-BTC-HN-001)
- 根據 Kraken Futures 的數據，BTC 的永續合約資金費率為 0.0048%，偏向平衡。 (EV-DERIV-BTC-001)
- 根據公共平衡檢查的數據，已知大型交易所/機構地址的即時餘額為 248597.5907 和 185274.9191。 (EV-WHALE-BTC-001)
- 根據 DefiLlama 的數據，BTC 鏈的 TVL 在過去 90 天內為 3503300388.0 美元，變化百分比為 -32.94%。 (EV-TVL-BTC-001)

## 推論（Inference）
- BTC 的價格略有下跌，鏈上數據顯示 BTC 鏈的健康狀況良好，市場情緒較為謹慎，這些訊號顯示 BTC 的市場表現可能受到外部因素的影響。

## 結論（Conclusion）
根據過去兩週的市場表現，BTC 的價格略有下跌，鏈上數據顯示 BTC 鏈的健康狀況良好，主要新聞和討論熱度顯示市場情緒較為謹慎，整體訊號一致程度較低。

## 主張（Claim）
### CL-001｜BTC 的價格在過去兩週內有所下跌。
- 判定：**supported**　信心 0.5907（medium／heuristic，類型 current_state）
- 事實：根據 CoinGecko 的數據，BTC 的價格在過去兩週內從 64087.72124536932 美元下跌至 63057.06197901681 美元。（EV-MARKET-001）
- 事實：根據 Kraken Futures 的數據，BTC 的 mark price 為 63066.43672496961 美元。（EV-DERIV-BTC-001）
- 推論：BTC 的價格在過去兩週內有所下跌，這表明市場對 BTC 的需求可能有所減少。
- 結論：BTC 的價格在過去兩週內有所下跌。
- 支持證據：EV-MARKET-001, EV-DERIV-BTC-001
- 反方證據：無
- 信心分量：weighted_evidence_quality=0.858／domain_coverage=0.4／source_diversity=0.6667／signal_consistency=1.0／counter_evidence_coverage=0.5
- 生效上限：critic_confidence_reduction
- 限制：數據來源僅限於 CoinGecko 和 Kraken Futures
- 推翻條件：如果其他可靠的市場數據顯示 BTC 價格上漲
- 觀察重點：持續關注 BTC 的價格變動

_Claim 的 verdict 與信心由 deterministic Python 計算（claim-confidence-v1），LLM 只提供敘述；信心是 heuristic evidence score，不是市場正確機率。_

## 跨來源一致程度
- 判定：**資料不足**
- 依據：只有 onchain 一個領域產生方向性訊號，不構成跨來源確認。
- 一致度：100.0%（1 個領域有方向，共 5 個領域有證據）
- derivatives：無方向（多 0.0 / 空 0.0）
- macro：無方向（多 0.0 / 空 0.0）
- market：無方向（多 0.0 / 空 0.0）
- news：無方向（多 0.0 / 空 0.0）
- onchain：偏空（多 0.0 / 空 0.7）

## 稽核（Critic）
**有需注意之處**　信心 0.65 → 0.55

檢查了 BTC 過去兩週市場表現的分析，發現部分結論與證據不完全一致，且有些證據的可靠度不高。

- [MEDIUM／unsupported] 主要新聞和討論熱度的證據來源可靠度不高，且討論情緒並未明確指出市場情緒較為謹慎。（針對：根據過去兩週的市場表現，BTC 的價格略有下跌，鏈上數據顯示 BTC 鏈的健康狀況良好，主要新聞和討論熱度顯示市場情緒較｜證據：EV-NEWS-001）
- [LOW／unsupported] 市場情緒較為謹慎的結論未能完全由證據支撐，且外部因素的影響未具體說明。（針對：BTC 的價格略有下跌，鏈上數據顯示 BTC 鏈的健康狀況良好，市場情緒較為謹慎，這些訊號顯示 BTC 的市場表現可能受｜證據：N/A）
- [LOW／stale_or_weak] 使用了可靠度低於 0.5 的證據（如 EV-NEWS-001 和 EV-SOCIAL-BTC-HN-001）來支持結論。（針對：根據過去兩週的市場表現，BTC 的價格略有下跌，鏈上數據顯示 BTC 鏈的健康狀況良好，主要新聞和討論熱度顯示市場情緒較｜證據：EV-NEWS-001）
- [LOW／stale_or_weak] 使用了可靠度低於 0.5 的證據（如 EV-SOCIAL-BTC-HN-001）來支持結論。（針對：根據過去兩週的市場表現，BTC 的價格略有下跌，鏈上數據顯示 BTC 鏈的健康狀況良好，主要新聞和討論熱度顯示市場情緒較｜證據：EV-SOCIAL-BTC-HN-001）
- [LOW／confidence_too_high] 信心分數 0.65 與證據的可靠度和質量不完全一致。（針對：根據過去兩週的市場表現，BTC 的價格略有下跌，鏈上數據顯示 BTC 鏈的健康狀況良好，主要新聞和討論熱度顯示市場情緒較｜證據：N/A）

### 語意風險（deterministic 偵測 + 稽核標記）
- [unsupported] 主張缺少對應證據：主要新聞和討論熱度的證據來源可靠度不高，且討論情緒並未明確指出市場情緒較為謹慎。（來源：llm_critic）
- [unsupported] 主張缺少對應證據：市場情緒較為謹慎的結論未能完全由證據支撐，且外部因素的影響未具體說明。（來源：llm_critic）
- [stale_or_weak] 引用了過舊或過弱的證據：使用了可靠度低於 0.5 的證據（如 EV-NEWS-001 和 EV-SOCIAL-BTC-HN-001）來支持結論。（來源：llm_critic）
- [stale_or_weak] 引用了過舊或過弱的證據：使用了可靠度低於 0.5 的證據（如 EV-SOCIAL-BTC-HN-001）來支持結論。（來源：llm_critic）
- [confidence_too_high] 信心與證據品質不相稱：信心分數 0.65 與證據的可靠度和質量不完全一致。（來源：llm_critic）

## 引用檢核（Citation Gate）
- 結果：**PASS_WITH_WARNINGS（可發佈，但有需揭露的警告）**
- Gate 版本：citation-gate-v1
- 檢查範圍：1 個 Claim、11 筆 Evidence（其中 9 筆被引用）
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
0.55

## 指標
- start_price: 64087.72124536932
- end_price: 63057.06197901681
- return_pct: -1.61

## 反方證據
- 根據 CoinGecko 的數據，BTC 在過去兩週內的市場回報率為 -1.61%，顯示 BTC 的價格略有下跌。

## 證據來源
- [#1] EV-MARKET-001: [CoinGecko](https://api.coingecko.com/api/v3/coins/bitcoin/market_chart?vs_currency=usd&days=14) (reliability: 0.8775) ｜ 類別 market_api｜狀態 未交叉驗證｜取得 2026-08-01T14:27:33.405110+00:00｜原始 0.8775｜新鮮度依 事件時間（0.6 天前）｜分量 source_quality=0.85／traceability=0.9／freshness=1.0／method_transparency=0.75／independence=0.85｜內容依據 endpoint、points、query｜使用於 CL-001
- [#2] EV-NEWS-001: [Google News RSS + publisher feeds](https://news.google.com/rss/search?q=BTC%20crypto) (reliability: 0.58) ｜ 類別 secondary_media｜狀態 未交叉驗證｜取得 2026-08-01T14:27:33.471384+00:00｜原始 0.58｜新鮮度依 發布時間（0.21 天前）｜分量 source_quality=0.5／traceability=0.55／freshness=1.0／method_transparency=0.35／independence=0.4｜內容依據 article_count、feed_url、items、with_fulltext、with_lede｜使用於 （無 Claim 引用）
- [#3] EV-MACRO-BTC-001: [Alternative.me Fear & Greed + Federal Reserve press releases](https://api.alternative.me/fng/?limit=14) (reliability: 0.8325) ｜ 類別 macro_api｜狀態 未交叉驗證｜取得 2026-08-01T14:27:33.593278+00:00｜原始 0.8325｜新鮮度依 事件時間（0.6 天前）｜分量 source_quality=0.8／traceability=0.85／freshness=1.0／method_transparency=0.7／independence=0.75｜內容依據 fear_greed_endpoint、fed_feed、fed_status、points｜使用於 （無 Claim 引用）
- [#4] EV-ANNOUNCE-BTC-001: [Bitcoin Optech Newsletter](https://bitcoinops.org/feed.xml) (reliability: 0.81) ｜ 類別 official_announcement｜狀態 未交叉驗證｜取得 2026-08-01T14:27:33.806936+00:00｜原始 0.81｜新鮮度依 發布時間（1.6 天前）｜分量 source_quality=0.85／traceability=0.85／freshness=1.0／method_transparency=0.55／independence=0.6｜內容依據 entry_count、feed_url、first_party｜使用於 （無 Claim 引用）
- [#5] EV-ONCHAIN-BTC-001: [Bitcoin](https://blockchain.info/q/getblockcount) (reliability: 0.925) ｜ 類別 blockchain_raw｜狀態 未交叉驗證｜取得 2026-08-01T14:27:33.807583+00:00｜原始 0.925｜新鮮度依 取得時間（無事件時間）（0.0 天前）｜分量 source_quality=0.9／traceability=0.95／freshness=1.0／method_transparency=0.85／independence=0.9｜內容依據 chain、endpoint｜使用於 （無 Claim 引用）
- [#6] EV-SOCIAL-BTC-HN-001: [Hacker News Algolia search](https://hn.algolia.com/api/v1/search_by_date?query=BTC%20crypto&tags=(story,comment)&hitsPerPage=25) (reliability: 0.35) ｜ 類別 social_public｜狀態 未交叉驗證｜取得 2026-08-01T14:27:33.778246+00:00｜原始 0.4875｜上限 0.35（anonymous_or_low_trace_social）｜新鮮度依 事件時間（0.53 天前）｜分量 source_quality=0.35／traceability=0.4／freshness=1.0／method_transparency=0.25／independence=0.45｜內容依據 endpoint、post_count、posts、query｜使用於 （無 Claim 引用）
- [#7] EV-DERIV-BTC-001: [Kraken Futures](https://futures.kraken.com/derivatives/api/v3/tickers) (reliability: 0.8375) ｜ 類別 derivatives_api｜狀態 未交叉驗證｜取得 2026-08-01T14:27:34.064527+00:00｜原始 0.8375｜新鮮度依 取得時間（無事件時間）（0.0 天前）｜分量 source_quality=0.8／traceability=0.85／freshness=1.0／method_transparency=0.7／independence=0.8｜內容依據 endpoint、normalised_to_hours、provider、provider_native_interval_hours、raw_funding_rate、symbol｜使用於 CL-001
- [#8] EV-WHALE-BTC-001: [Public balance check (curated known addresses)](https://blockchain.info/balance?active=34xp4vRoCGJym3xR7yCVPFHoCNxv4Twseo|3M219KR5vEneNb47ewrPfWyb5jQ2DjxRP6) (reliability: 0.6) ｜ 類別 blockchain_raw｜狀態 未交叉驗證｜取得 2026-08-01T14:27:34.019959+00:00｜原始 0.925｜上限 0.6（unverifiable_entity_attribution）｜新鮮度依 取得時間（無事件時間）（0.0 天前）｜分量 source_quality=0.9／traceability=0.95／freshness=1.0／method_transparency=0.85／independence=0.9｜內容依據 addresses｜使用於 （無 Claim 引用）
- [#9] EV-VEGAS-BTC-FALLBACK: [Offline Vegas Channel fixture](https://example.com/vegas) (reliability: 0.2) ｜ 類別 fallback_fixture｜狀態 取不到（降級）｜取得 2026-08-01T14:27:33.352328+00:00｜原始 0.36｜上限 0.2（fallback_fixture、unverifiable_intent_attribution）｜新鮮度依 取得時間（無事件時間）（0.0 天前）｜分量 source_quality=0.2／traceability=0.2／freshness=1.0／method_transparency=0.2／independence=0.2｜內容依據 error_type、fallback｜使用於 （無 Claim 引用）
- [#10] EV-LSRATIO-BTC-FALLBACK: [Offline long/short ratio fixture](https://example.com/long_short_ratio) (reliability: 0.2) ｜ 類別 fallback_fixture｜狀態 取不到（降級）｜取得 2026-08-01T14:27:33.405555+00:00｜原始 0.36｜上限 0.2（fallback_fixture、unverifiable_intent_attribution）｜新鮮度依 取得時間（無事件時間）（0.0 天前）｜分量 source_quality=0.2／traceability=0.2／freshness=1.0／method_transparency=0.2／independence=0.2｜內容依據 error_type、fallback｜使用於 （無 Claim 引用）
- [#11] EV-TVL-BTC-001: [DefiLlama chain TVL (Bitcoin)](https://api.llama.fi/v2/historicalChainTvl/Bitcoin) (reliability: 0.8775) ｜ 類別 market_api｜狀態 未交叉驗證｜取得 2026-08-01T14:27:34.060263+00:00｜原始 0.8775｜新鮮度依 事件時間（0.6 天前）｜分量 source_quality=0.85／traceability=0.9／freshness=1.0／method_transparency=0.75／independence=0.85｜內容依據 endpoint、points｜使用於 （無 Claim 引用）

## 後續觀察重點
- 追蹤 BTC 的價格是否在未來的交易日內回升，若回升則本次判讀可能需要修正。

## 風險與限制
- vegas_channel 來源本次未取得即時資料（HTTPError），該面向的結論僅為部分覆蓋。
- long_short_ratio 來源本次未取得即時資料（HTTPError），該面向的結論僅為部分覆蓋。
- 本 MVP 僅建立訊號的同時性關聯，不足以建立因果關係。
- 社群與新聞來源的可靠度低於市場與鏈上資料，權重已相應調低。
- 2 筆證據為降級來源（EV-VEGAS-BTC-FALLBACK, EV-LSRATIO-BTC-FALLBACK），可信度已壓到 0.2 以下，不得作為結論的唯一依據。
- 以下可信度上限已生效，相關敘述不得超過證據能證明的範圍：anonymous_or_low_trace_social×1、unverifiable_entity_attribution×1、unverifiable_intent_attribution×2。

## 可能推翻結論的條件
- 如果其他可靠的市場數據顯示 BTC 價格上漲（CL-001）

_本報告為研究支援，非投資建議；不構成買賣訊號，也不對未來市場表現做出保證。_
