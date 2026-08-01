# Kiro Development Runbook — T0 to T8

## 1. Use the Spec

Kiro IDE：

1. Open Specs.
2. Select `hoyabit-competition-ready`.
3. Review requirements/design/tasks.
4. Start one Task only.

Kiro CLI：

```text
/spec hoyabit-competition-ready
```

Then:

```text
Implement only task T0.
Read docs/competition-tasks/T0-baseline.md first.
Do not start T1.
Update docs/COMPETITION_TASK_STATUS.yaml and stop after the Task report.
```

Repeat by replacing Task ID.

Do not use `/spec run hoyabit-competition-ready` unless you explicitly accept losing manual phase gates.

## 2. Task Lifecycle

```text
READY
→ IN_PROGRESS
→ REVIEW
→ PASS / FAIL / BLOCKED / PARTIAL
```

Only PASS unlocks the next Task.

## 3. Required Agent Report

```text
TASK:
STATUS:
FILES_CHANGED:
TEST_COMMANDS:
TEST_RESULTS:
COMMIT:
KNOWN_LIMITATIONS:
NEXT_TASK_READY:
```

## 4. Reviewer Questions

After every Task ask:

1. Did the Agent implement only this Task?
2. Were targeted tests actually executed?
3. Was the full suite actually executed?
4. Did offline fallback still pass?
5. Are there unstated failures or warnings?
6. Was a real commit created?
7. Does the commit contain unrelated changes?
8. Does status YAML match reality?

## 5. Execution Order

```text
T0 Baseline
T1 Bedrock
T2 Planner
T3 Credibility
T4 Claim Graph
T5 Citation + Outputs
T6 UI
T7 Formal Run
T8 Freeze
```

## 6. Time Cutoffs

- T0 should finish within 30 minutes.
- A single blocker gets 20 minutes.
- T5 is the minimum core.
- T5 before UI polish.
- Final 90 minutes: no new feature.

If time is critical:

```text
Finish T5
→ minimum T7 deadline/output protection
→ T8 critical tests + backup
→ skip nonessential T6 polish
```

## 7. Emergency Fallback Decisions

| Problem | Decision |
|---|---|
| Bedrock access unavailable | Keep adapter/mock; use offline; retry live at T8 |
| Planner invalid | deterministic fallback |
| One Collector broken | mark missing; continue |
| On-chain unavailable | disclose; lower coverage |
| Semantic Critic broken | structural gate + conservative report |
| UI broken | use report + JSON |
| Deadline near | stop optional work and save artifacts |
| Comparison incomplete | preserve missing; no zero-fill |

## 8. Branch and Commit

Branch:

```text
hackathon/competition-ready
```

One Task, one commit. Do not rebase or force-reset without explicit instruction.

## 9. Kiro Prompt Templates

### Start a Task

```text
#spec:hoyabit-competition-ready

Implement only task T3.
Read docs/competition-tasks/T3-credibility.md and all always-included steering.
Confirm T2 is PASS.
Do not start T4.
Run targeted tests and the full suite, update task status, create the specified commit,
then stop and report the exact results.
```

### Review a Task

```text
#spec:hoyabit-competition-ready

Review task T3 against its acceptance criteria.
Inspect the actual diff and run the specified tests.
Return PASS, FAIL, BLOCKED, or PARTIAL.
Do not implement T4.
```

### Repair a Failed Task

```text
#spec:hoyabit-competition-ready

Repair only the failures found in task T3 review.
Do not expand scope and do not start T4.
Re-run targeted and full tests, update status, amend only when safe, then stop.
```

## 10. Demo Freeze

T8 PASS 後：

- Tag `competition-demo-ready`.
- Preserve successful live output.
- Preserve offline backup.
- Preserve comparison backup.
- Do not add features.
