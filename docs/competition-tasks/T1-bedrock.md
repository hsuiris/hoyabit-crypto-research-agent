# T1 — 新增 Amazon Bedrock LLM Provider

## Metadata

```yaml
task_id: T1
depends_on: [T0]
target_minutes: 75
target_commit: "feat(T1): add Amazon Bedrock LLM adapter"
```

## Goal

在保留 Gemini、OpenAI、`none` 與 deterministic offline fallback 的前提下，新增 Amazon Bedrock 作為競賽 LLM provider。

## Primary Files

- `src/llm.py`
- `lambda_handler.py`
- `.env.example`
- `aws/template.yaml`
- 對應測試

## In Scope

- Bedrock provider selection。
- Bedrock Runtime client。
- JSON generation wrapper。
- Invalid JSON／timeout handling。
- Lambda environment handling。
- IAM inference permission。
- Mocked tests。
- Offline fallback regression test。

## Out of Scope

- Collector 資料邏輯。
- Planner 功能。
- Credibility score。
- UI 排版。
- 比較演算法。
- 回測與 Vegas Strategy。

## Implementation Requirements

### Provider Configuration

支援：

```text
LLM_PROVIDER=bedrock
BEDROCK_MODEL_ID
AWS_REGION or AWS_DEFAULT_REGION
BEDROCK_MAX_TOKENS
BEDROCK_TEMPERATURE
```

`configured_provider()` 必須支援：

```text
bedrock
gemini
openai
none
```

Bedrock 模式：

- 不要求 Gemini/OpenAI key。
- 至少要求 model ID。
- Region 可使用正常 AWS fallback。
- 不在 `llm_is_configured()` 每次呼叫時測試網路。

### Bedrock Client

使用：

```python
boto3.client("bedrock-runtime")
```

優先透過 `converse()` 實作一致介面。

不得：

- 硬編 Access Key。
- 把密碼或 token 寫入 log。
- 在 unit tests 真實呼叫 AWS。

### Shared Structured JSON Helper

新增或重構成共用介面：

```python
generate_json_with_llm(
    prompt,
    schema,
    schema_name,
    timeout_seconds,
)
```

行為：

1. 發送 system/user message。
2. 擷取模型文字輸出。
3. `json.loads`。
4. 驗證 required fields。
5. 非法 JSON 最多修復或重試一次。
6. 仍失敗時丟出明確 typed exception。
7. Orchestrator 使用 offline fallback。

### Existing Functions

使下列既有功能支援 Bedrock：

- `analyze_with_llm()`
- `critique_with_llm()`
- `llm_runtime_info()`

Runtime info 至少包含：

```text
provider
model
region
```

### Lambda and AWS Template

`lambda_handler.py`：

- Bedrock provider 不得要求 Secrets Manager API key。
- Legacy provider secret loading 可保留。
- 不得內嵌 AWS credentials。

`aws/template.yaml`：

- 加入最小 Bedrock inference 權限。
- 以 environment variable 傳 model/region。
- 不得加入 AdministratorAccess。

### Environment Example

`.env.example` 加入不含秘密的範例：

```text
LLM_PROVIDER=bedrock
BEDROCK_MODEL_ID=
AWS_REGION=us-east-1
BEDROCK_MAX_TOKENS=...
BEDROCK_TEMPERATURE=...
```

## Required Tests

- [ ] `configured_provider()` 選到 bedrock。
- [ ] Bedrock 無 model ID 時未 configured。
- [ ] 正常 JSON 回應。
- [ ] 非法 JSON。
- [ ] timeout／SDK exception。
- [ ] repair/retry 最多一次。
- [ ] `analyze_with_llm()` Bedrock path。
- [ ] `critique_with_llm()` Bedrock path。
- [ ] Bedrock failure → offline fallback。
- [ ] Gemini/OpenAI/none 既有行為未破壞。
- [ ] Lambda Bedrock path 不要求外部 API secret。
- [ ] IAM template syntax 可驗證。

## Acceptance Criteria

- [ ] `LLM_PROVIDER=bedrock` 可載入。
- [ ] 無 Gemini/OpenAI key 仍可配置。
- [ ] 所有 unit tests 使用 mock。
- [ ] Bedrock failure 不會失去報告。
- [ ] offline mode 仍正常。
- [ ] 完整測試套件沒有新增 regression。
- [ ] commit hash 已回報。

## Gate

T1 只有在「adapter + tests + fallback」都完成時才 PASS。真實 Bedrock 權限若暫時不可取得，可以記錄為現場設定，但 mock path 與 fallback 必須完整。

## Blocker Rule

真實 AWS 權限／model access 超過 30 分鐘仍無法解除：

- 不得停在此處重構。
- 保留 adapter。
- 記錄 `docs/COMPETITION_BLOCKERS.md`。
- 以 mock + offline path 驗收程式層。
- T8 再做一次 live smoke。

## Stop Condition

完成後停止。不得開始 T2。
