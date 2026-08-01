# HoyaBIT Competition-Ready Tasks T0–T8

本文件是索引與快速瀏覽版。Kiro 實際執行時，應使用：

```text
.kiro/specs/hoyabit-competition-ready/tasks.md
```

並閱讀各自的詳細檔案：

| Task | Detail |
|---|---|
| T0 | `docs/competition-tasks/T0-baseline.md` |
| T1 | `docs/competition-tasks/T1-bedrock.md` |
| T2 | `docs/competition-tasks/T2-planner.md` |
| T3 | `docs/competition-tasks/T3-credibility.md` |
| T4 | `docs/competition-tasks/T4-claim-graph.md` |
| T5 | `docs/competition-tasks/T5-citation-output.md` |
| T6 | `docs/competition-tasks/T6-web-demo.md` |
| T7 | `docs/competition-tasks/T7-formal-run.md` |
| T8 | `docs/competition-tasks/T8-demo-freeze.md` |

## Critical Path

```text
T0 → T1 → T2 → T3 → T4 → T5 → T6 → T7 → T8
```

## Minimum Competition-Ready Line

```text
T5 PASS
```

## Full Demo-Ready Line

```text
T8 PASS + tag competition-demo-ready
```

## Time Budget

| Task | Target |
|---|---:|
| T0 | 30 min |
| T1 | 75 min |
| T2 | 60 min |
| T3 | 90 min |
| T4 | 90 min |
| T5 | 75 min |
| T6 | 60 min |
| T7 | 45 min |
| T8 | 90 min |

## Non-Negotiable Rules

- Existing MVP first.
- Offline fallback stays.
- LLM does not approve itself.
- Deterministic scores and caps.
- Every Claim traces to Evidence.
- One Task at a time.
- Tests and real commit required.
- Stop after each Task.
