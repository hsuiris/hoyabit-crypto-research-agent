# T8 — 競賽驗收、故障注入與 Demo Freeze

## Metadata

```yaml
task_id: T8
depends_on: [T7]
target_minutes: 90
target_commit: "test(T8): freeze competition-ready demo"
target_tag: competition-demo-ready
```

## Goal

停止新增產品功能，只做競賽驗收、故障注入、文件、成功備份與最終凍結。

## Allowed Work

- 測試。
- 修復 blocking regressions。
- 文件。
- Demo fixture。
- 一次 live smoke。
- 備援與演練。
- 最終 tag。

## Forbidden Work

- 新資料來源。
- 新 Agent。
- 新模型。
- 新 UI framework。
- Collector refactor。
- 回測／策略擴充。
- 新資料庫。
- 非 blocker 美化。

## New / Updated Files

- `tests/test_competition_readiness.py`
- `README.md`
- `SETUP.md`
- `docs/DEMO_RUNBOOK.md`
- `docs/COMPETITION_CHECKLIST.md`
- `demo-fixtures/competition-ready/`

## Required Question Tests

### 1. BTC 多源

```text
分析 BTC 過去兩週市場表現，整合價格、鏈上、主要新聞與討論熱度，說明訊號一致程度。
```

### 2. ETH 假設

```text
市場認為 ETH 短期將維持盤整，請蒐集支持與反對證據。
```

### 3. SOL vs BNB

```text
比較 SOL 與 BNB 的流動性、市場關注度及風險敞口。
```

### 4. XRP 模糊題

```text
分析 XRP 當前市場狀態、主要風險與後續觀察條件。
```

### 5. 五幣 Smoke

BTC、ETH、SOL、BNB、XRP 都至少完成一次 test/offline run 或合法降級。

## Required Failure Injection

- Bedrock timeout。
- Bedrock invalid JSON。
- Planner failure。
- News source failure。
- On-chain unavailable。
- Social empty。
- Single secondary news。
- 20 same-lineage reposts。
- Unknown Evidence ID。
- Fallback sole support。
- Strong high-quality conflict。
- Semantic Critic timeout。
- Approaching finalization deadline。
- UI old fixture missing fields。

## Required Metrics

- 關鍵 Claim 有合法 Evidence ID：100%。
- Evidence source 完整：100%。
- Evidence fetched_at 完整：100%。
- Evidence content_reference 完整：100%。
- 同源不重複加滿權重。
- 高品質衝突降低 confidence。
- 單一 Collector failure 仍有 report。
- Bedrock failure 仍有 offline report。
- Comparison 共用 window。
- 六項 artifacts 存在。
- Manifest hashes 正確。
- Runtime 在內部 deadline 內。

## Live Smoke

只做一次 BTC 或 ETH：

- 使用現場允許 Bedrock model。
- 使用最少外部 API 呼叫。
- 保存完整成功 run。
- 記錄 provider/model/region/runtime。
- Live failure 不得破壞 offline backup。

## Backup Fixtures

```text
demo-fixtures/competition-ready/
├── live-success/
├── offline-backup/
└── comparison-backup/
```

每個 backup 應包含可獨立展示的六項 artifacts。

## Runbook Requirements

`docs/DEMO_RUNBOOK.md`：

1. 安裝。
2. AWS credential/region。
3. Bedrock model ID。
4. Offline smoke。
5. Live smoke。
6. Web start。
7. Formal run。
8. Authorized rerun。
9. Artifacts。
10. Live failure 切 backup。
11. 五分鐘展示順序。
12. 已知限制。

`docs/COMPETITION_CHECKLIST.md`：

- 啟動前。
- 正式執行前。
- 執行中。
- 執行後。
- Demo 備援。
- 提交檔案。

## Final Commands

依 T0 確認的實際命令執行：

```text
targeted competition tests
full test suite
offline end-to-end
one live smoke
Web smoke
comparison smoke
```

## Acceptance Criteria

- [ ] 五幣 smoke。
- [ ] 三大核心題型。
- [ ] 故障注入。
- [ ] 完整 artifacts。
- [ ] manifest hashes。
- [ ] deadline。
- [ ] live or precisely documented environment blocker。
- [ ] offline backup。
- [ ] comparison backup。
- [ ] runbook/checklist。
- [ ] full suite no regression。
- [ ] commit hash。
- [ ] tag `competition-demo-ready`。

## Freeze Rule

T8 PASS 後：

- 不再新增產品功能。
- 只允許 presentation blocker 修復。
- 所有修改必須重跑最小 competition suite。
