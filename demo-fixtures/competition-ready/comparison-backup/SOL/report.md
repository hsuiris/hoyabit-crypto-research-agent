# SOL 市場研究報告

## 研究問題
比較 SOL 與 BNB 在當前宏觀環境下的市場位置與風險特徵，說明流動性、市場關注度與風險敞口的主要差異。

## 分析標的與題目
- 分析標的：SOL
- 研究問題：比較 SOL 與 BNB 在當前宏觀環境下的市場位置與風險特徵，說明流動性、市場關注度與風險敞口的主要差異。
- 任務類型：describe_market_state、compare_assets、identify_risks

## 資料截止與分析區間
- Run ID：RUN-20260801T154304Z-SOL-8c894c19
- 執行開始：2026-08-01T15:43:04.448560+00:00
- 執行完成：2026-08-01T15:43:04.459309+00:00
- 資料截止（as-of）：2026-08-01T15:43:04.448560+00:00
- 研究時間窗：14 天（來源：default）
- 證據取得時間範圍：2026-08-01T15:43:04.448711+00:00 → 2026-08-01T15:43:04.448711+00:00
- 執行模式：offline（live=False、use_llm=False）
- 推理提供者：deterministic／N/A
- 執行性質：N/A／狀態：COMPLETED_DEGRADED
- 本次降級項目：all_evidence_is_fallback_fixture

## 立場
**偏多（Bullish）**　信心 0.55

- 依據：多方權重 2.2 領先空方 0.9，淨優勢 42%
- 訊號權重：多方 2.2 / 空方 0.9（共 7 項訊號）
- 主要推力：4H Vegas 通道為多頭排列，中期趨勢向上（EV-006）
- 主要推力：期間報酬 +8.2%，價格動能為正（EV-001）

## 市場判斷
SOL 整體訊號偏多（多方權重 2.2 / 空方 0.9）；技術面大小時區訊號不一致，RSI 位於中性區間，1H 成交量無異常爆量。支持該方向的獨立訊號 2 項，反向訊號 1 項。

## 關鍵依據
- 4H Vegas 通道為多頭排列，中期趨勢向上（EV-006，權重 1.2）
- 期間報酬 +8.2%，價格動能為正（EV-001，權重 1.0）
- 跨來源一致程度：矛盾（偏多領域 1 個、偏空領域 1 個，跨來源訊號互相矛盾。）

## 事實（Fact）
- EV-001: 已取得市場價格資料（期間報酬率 8.2%）。
- EV-002: 近期新聞標題包含 『SOL network activity remains elevated』；『SOL developer activity steady week over week』。
- EV-003: 公開討論情緒為 mixed（依據：正向關鍵字：adoption；負向關鍵字：drop）。
- EV-004: 資金費率 0.008%，多空接近平衡。
- EV-005: 已知大戶錢包餘額（Demo Exchange Wallet 持有 500,000）。
- EV-006: Vegas 通道 4H 趨勢為多頭排列，1H RSI(6) 48.0，1H 成交量無異常爆量，最近訊號為無明確訊號（None 根K棒前），4h為多頭，1h為訊號不一(mixed)，方向不一致。
- EV-007: 大戶多空比 55.0% 多 / 45.0% 空（多方持倉佔優），近期一致性 80.0%。
- EV-008: 全市場恐懼貪婪指數 52（Neutral），14 天變化 +5，聯準會最新貨幣政策發布為『Federal Reserve issues FOMC statement』。
- EV-009: 官方公告（第一方官方 feed）近期 1 則，包含 『SOL protocol update published』。

## 推論（Inference）
- 期間報酬 +8.2%，價格動能為正，故此項支持偏多判讀（EV-001）。
- 4H Vegas 通道為多頭排列，中期趨勢向上，故此項支持偏多判讀（EV-006）。
- 4H 與 1H 方向不一致，趨勢訊號本身互相矛盾（EV-006），此項不提供方向性資訊，僅調整信心水準。
- 資金費率 0.008%，多空成本接近平衡，衍生品未顯示明顯偏斜（EV-004），此項不提供方向性資訊，僅調整信心水準。
- 由於同時存在 1 項反向訊號，上述方向僅為權重加總後的淨結果，而非一致性結論；信心分數已因此下修至 0.55。

## 結論（Conclusion）
SOL 整體訊號偏多（多方權重 2.2 / 空方 0.9）；技術面大小時區訊號不一致，RSI 位於中性區間，1H 成交量無異常爆量。支持該方向的獨立訊號 2 項，反向訊號 1 項。

## 主張（Claim）
### CL-001｜SOL 目前訊號偏多（支持側權重 2.20，反向側 0.90）
- 判定：**insufficient_evidence**　信心 0.35（low／heuristic，類型 market_judgment）
- 事實：期間報酬 +8.2%，價格動能為正（EV-001）
- 事實：4H Vegas 通道為多頭排列，中期趨勢向上（EV-006）
- 推論：支持側與反向側的權重差距構成本次方向判讀；權重來自既有訊號盤點，非模型自由生成。
- 結論：針對「比較 SOL 與 BNB 在當前宏觀環境下的市場位置與風險特徵，說明流動性、市場關注度與風險敞口的主要差異。」，本次證據偏多，但仍保留反向側證據供讀者檢視。
- 支持證據：EV-001, EV-006
- 反方證據：EV-007
- 信心分量：weighted_evidence_quality=0.1905／domain_coverage=0.6667／source_diversity=0.6667／signal_consistency=0.6552／counter_evidence_coverage=1.0
- 生效上限：single_supporting_domain、fallback_only_primary_support
- 限制：本 Claim 由 deterministic fallback 產生，未經模型敘事分層。
- 限制：立場判定依據：多方權重 2.2 領先空方 0.9，淨優勢 42%
- 推翻條件：反向側權重超過支持側時，本判斷即被推翻。
- 觀察重點：大戶持倉 55.0% 偏多（近期一致性 80.0%），持倉過度集中於單邊時反向風險上升

_Claim 的 verdict 與信心由 deterministic Python 計算（claim-confidence-v1），LLM 只提供敘述；信心是 heuristic evidence score，不是市場正確機率。_

## 跨來源一致程度
- 判定：**矛盾**
- 依據：偏多領域 1 個、偏空領域 1 個，跨來源訊號互相矛盾。
- 一致度：50.0%（2 個領域有方向，共 4 個領域有證據）
- derivatives：偏空（多 0.0 / 空 0.9）
- macro：無方向（多 0.0 / 空 0.0）
- market：偏多（多 2.2 / 空 0.0）
- news：無方向（多 0.0 / 空 0.0）

## 稽核（Critic）
**語意稽核未執行**　狀態：skipped:disabled

本次沒有可用的語意稽核結果，報告改以 structural citation gate 與 deterministic 語意風險偵測作為唯一檢核，結論維持保守呈現。

### 語意風險（deterministic 偵測 + 稽核標記）
- 本次未偵測到已知的過度推論樣式

## 引用檢核（Citation Gate）
- 結果：**PASS（全部引用可追溯）**
- Gate 版本：citation-gate-v1
- 檢查範圍：1 個 Claim、9 筆 Evidence（其中 9 筆被引用）
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
- return_pct: 8.2
- rsi: 61.4
- start_price: 140.0
- end_price: 150.92

## 反方證據
- 反方訊號｜大戶持倉 55.0% 偏多（近期一致性 80.0%），持倉過度集中於單邊時反向風險上升（EV-007）
- 未定訊號｜4H 與 1H 方向不一致，趨勢訊號本身互相矛盾（EV-006）
- 未定訊號｜資金費率 0.008%，多空成本接近平衡，衍生品未顯示明顯偏斜（EV-004）
- 未定訊號｜全市場恐懼貪婪指數 52，情緒位於中性區間（EV-008）
- 推理模式｜LLM disabled，本結論由規則式訊號加總產生，未經 LLM 語意推理交叉檢驗。

## 證據來源
- [#1] EV-001: [MockMarket](https://example.com/market) (reliability: 0.18) ｜ 類別 fallback_fixture｜狀態 離線 fixture｜取得 2026-08-01T15:43:04.448711+00:00｜原始 0.18｜上限 0.2（fallback_fixture）｜新鮮度依 事件時間（199.65 天前）｜分量 source_quality=0.2／traceability=0.2／freshness=0.1／method_transparency=0.2／independence=0.2｜內容依據 summary、time_range｜使用於 CL-001
- [#2] EV-002: [MockNews](https://example.com/news) (reliability: 0.2) ｜ 類別 fallback_fixture｜狀態 離線 fixture｜取得 2026-08-01T15:43:04.448711+00:00｜原始 0.36｜上限 0.2（fallback_fixture）｜新鮮度依 取得時間（無事件時間）（0.0 天前）｜分量 source_quality=0.2／traceability=0.2／freshness=1.0／method_transparency=0.2／independence=0.2｜內容依據 summary、time_range｜使用於 （無 Claim 引用）
- [#3] EV-003: [MockSocial](https://example.com/social) (reliability: 0.2) ｜ 類別 fallback_fixture｜狀態 離線 fixture｜取得 2026-08-01T15:43:04.448711+00:00｜原始 0.36｜上限 0.2（fallback_fixture）｜新鮮度依 取得時間（無事件時間）（0.0 天前）｜分量 source_quality=0.2／traceability=0.2／freshness=1.0／method_transparency=0.2／independence=0.2｜內容依據 summary、time_range｜使用於 （無 Claim 引用）
- [#4] EV-004: [MockDerivatives](https://example.com/derivatives) (reliability: 0.2) ｜ 類別 fallback_fixture｜狀態 離線 fixture｜取得 2026-08-01T15:43:04.448711+00:00｜原始 0.36｜上限 0.2（fallback_fixture）｜新鮮度依 取得時間（無事件時間）（0.0 天前）｜分量 source_quality=0.2／traceability=0.2／freshness=1.0／method_transparency=0.2／independence=0.2｜內容依據 summary、time_range｜使用於 （無 Claim 引用）
- [#5] EV-005: [MockWhaleWatch](https://example.com/whale) (reliability: 0.2) ｜ 類別 fallback_fixture｜狀態 離線 fixture｜取得 2026-08-01T15:43:04.448711+00:00｜原始 0.36｜上限 0.2（fallback_fixture、unverifiable_entity_attribution）｜新鮮度依 取得時間（無事件時間）（0.0 天前）｜分量 source_quality=0.2／traceability=0.2／freshness=1.0／method_transparency=0.2／independence=0.2｜內容依據 summary、time_range｜使用於 （無 Claim 引用）
- [#6] EV-006: [MockVegasChannel](https://example.com/vegas) (reliability: 0.2) ｜ 類別 fallback_fixture｜狀態 離線 fixture｜取得 2026-08-01T15:43:04.448711+00:00｜原始 0.375｜上限 0.2（fallback_fixture）｜新鮮度依 取得時間（無事件時間）（0.0 天前）｜分量 source_quality=0.2／traceability=0.2／freshness=1.0／method_transparency=0.3／independence=0.2｜內容依據 summary、time_range｜使用於 CL-001
- [#7] EV-007: [MockLongShortRatio](https://example.com/long_short_ratio) (reliability: 0.2) ｜ 類別 fallback_fixture｜狀態 離線 fixture｜取得 2026-08-01T15:43:04.448711+00:00｜原始 0.375｜上限 0.2（fallback_fixture）｜新鮮度依 取得時間（無事件時間）（0.0 天前）｜分量 source_quality=0.2／traceability=0.2／freshness=1.0／method_transparency=0.3／independence=0.2｜內容依據 summary、time_range｜使用於 CL-001
- [#8] EV-008: [MockMacro](https://example.com/macro) (reliability: 0.18) ｜ 類別 fallback_fixture｜狀態 離線 fixture｜取得 2026-08-01T15:43:04.448711+00:00｜原始 0.18｜上限 0.2（fallback_fixture）｜新鮮度依 事件時間（199.65 天前）｜分量 source_quality=0.2／traceability=0.2／freshness=0.1／method_transparency=0.2／independence=0.2｜內容依據 summary、time_range｜使用於 （無 Claim 引用）
- [#9] EV-009: [MockOfficialAnnouncements](https://example.com/announcement) (reliability: 0.2) ｜ 類別 fallback_fixture｜狀態 離線 fixture｜取得 2026-08-01T15:43:04.448711+00:00｜原始 0.36｜上限 0.2（fallback_fixture）｜新鮮度依 取得時間（無事件時間）（0.0 天前）｜分量 source_quality=0.2／traceability=0.2／freshness=1.0／method_transparency=0.2／independence=0.2｜內容依據 summary、time_range｜使用於 （無 Claim 引用）

## 後續觀察重點
- 優先追蹤反向訊號是否強化：大戶持倉 55.0% 偏多（近期一致性 80.0%），持倉過度集中於單邊時反向風險上升（EV-007）。
- 確認下一個 24 小時價格與成交量是否延續偏多方向，否則視為假突破。
- 追蹤全市場恐懼貪婪指數是否脫離目前的 52 區間，總體風險偏好轉向會同步改變個別幣種的訊號解讀。
- 追蹤官方公告與鏈上活躍度是否與價格方向同步，若背離則優先相信鏈上與官方事件。

## 風險與限制
- 4H 與 1H 時區方向不一致，策略層面屬於不進場條件，任何方向性結論的可信度都應下修。
- 本 MVP 僅建立訊號的同時性關聯，不足以建立因果關係。
- 社群與新聞來源的可靠度低於市場與鏈上資料，權重已相應調低。
- 本次全部 9 筆證據皆為離線 fixture 或降級來源（可信度上限 0.2），只足以示範流程，不足以支撐方向性結論。
- 以下可信度上限已生效，相關敘述不得超過證據能證明的範圍：unverifiable_entity_attribution×1。

## 可能推翻結論的條件
- 反向側權重超過支持側時，本判斷即被推翻。（CL-001）

_本報告為研究支援，非投資建議；不構成買賣訊號，也不對未來市場表現做出保證。_
