# Bedrock Token Monitoring

## Concepts

**Input tokens** — everything sent to the model: system prompt + full conversation history + current message. Grows with each turn in a conversation. You don't set a limit; the model's context window is the hard ceiling (Claude 3.5 Sonnet: 200k tokens).

**Output tokens** — what the model generates back. Capped by `maxTokens` in `inferenceConfig`. The model stops at this limit and returns whatever it has, even mid-sentence.

**stopReason** — tells you why the model stopped:
- `end_turn` — finished naturally
- `max_tokens` — hit the cap and was cut off

Both input and output tokens are billed separately. Output is typically more expensive per token than input.

---

## Why Per-Request Monitoring is Noisy

Token counts vary naturally per request:
- "yes or no?" → ~5 output tokens
- "explain this concept" → ~600 output tokens

Alerting on individual requests produces constant false positives. One large response is normal. The signal is in the aggregate.

---

## What to Monitor Instead

### 1. Hourly token volume (CloudWatch)

Use metric math to sum both token types per hour:

```
SUM(InputTokenCount) + SUM(OutputTokenCount) per hour
```

Alert if this exceeds 2x your normal hourly average. A sudden spike means unusual usage volume or someone hammering the API.

CloudWatch metrics to use:
- Namespace: `AWS/Bedrock`
- Metrics: `InputTokenCount`, `OutputTokenCount`
- Dimension: `ModelId`

### 2. Sustained max_tokens stop reason

If many responses are hitting `stopReason: max_tokens`, users are getting truncated answers. This means either:
- `maxTokens` is set too low for your use case
- users are asking for unusually long outputs

Log `stopReason` per response and alert if the rate of `max_tokens` exceeds ~10% of requests in an hour.

### 3. Input token growth per session

Input grows with conversation history since the full message array is sent each turn. Abnormally fast growth could indicate prompt injection — someone stuffing the context with junk.

Alert if a single session's input tokens exceed a threshold (e.g. 50k tokens) within a short window.

### 4. Cost (most actionable)

Set an AWS Budget with SNS notification at 80% of your monthly spend cap. This is the real thing you care about — token counts are a proxy for cost.

```
AWS Budgets → Cost budget → Alert at 80% → SNS topic → email/slack
```

---

## Alert Summary

| What | Signal | Action |
|---|---|---|
| Hourly token sum spikes 2x | Unusual usage volume | Investigate, check for abuse |
| High `max_tokens` stop rate | Responses being truncated | Raise `maxTokens` or add response length guidance to system prompt |
| Single session input > 50k tokens | Possible prompt injection | Review session, consider input length guard |
| Monthly cost hits 80% budget | Spend approaching limit | Review usage, consider rate limiting |

---

## Current Config (learn-lovable-llm-chat)

```python
inferenceConfig={"maxTokens": 2048, "temperature": 0.7}
```

- Max output per response: ~1500 words
- Typical chat response: 100–500 tokens
- Responses consistently hitting 2048 = truncation risk
