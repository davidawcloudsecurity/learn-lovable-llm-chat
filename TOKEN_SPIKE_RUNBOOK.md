# Token Spike Runbook — Operations Guide

## Why This Matters (Business Pain)

Bedrock bills per token. A spike in input or output tokens means a direct cost spike on the AWS bill. Three real problems this causes:

**1. Unexpected cost**
If the product is offered free or flat-rate, the company absorbs every token. Without monitoring, you don't know until the bill arrives at end of month — by then the damage is done.

**2. Abuse / misuse**
Someone could be using the app as a free AI tool — running scripts, scraping responses, or doing things outside intended use. A token spike with no corresponding user growth is a red flag. Catching it early means you can rate-limit or block before it costs thousands.

**3. Throttling and outages**
Bedrock has service quotas (tokens per minute per model). If usage spikes hard enough, Bedrock returns `ThrottlingException` — real users start seeing errors. Monitoring lets you see you're approaching the limit before it becomes an outage.

---

## Your Role as Operations

You are not the decision maker. Your job is to detect, investigate, and escalate with context.

When an alert fires:
1. Look at the logs to understand what's happening
2. Answer the diagnostic questions below
3. Escalate to the right person with a clear summary

---

## Diagnostic Questions to Answer Before Escalating

When a token spike alert fires, work through these:

**Is it input or output driving the spike?**
- Input spike only → long conversations, large messages, or possible prompt injection
- Output spike only → model generating verbose responses, or `maxTokens` too high
- Both spiking → traffic volume increase (more users or more requests)

**Is it one user or many?**
- One user/IP → likely abuse or a script hammering the API
- Many users → legitimate traffic growth or a bug causing duplicate requests

**Is it a burst or sustained?**
- Short burst (minutes) → likely a script or automated tool
- Sustained over hours → traffic growth or a runaway process

**Is `stopReason: max_tokens` firing frequently?**
- Yes → users are getting truncated responses, `maxTokens` may need to be raised
- No → responses are completing normally

---

## Who to Escalate To

| Situation | Escalate to | What to say |
|---|---|---|
| Spike looks like abuse/script | Product owner + developer | "One source is generating X tokens/hour, looks like abuse. Need decision on rate limiting." |
| Spike is legitimate traffic growth | Stakeholder / product owner | "Usage grew 3x this hour. At this rate monthly cost will be $X. Need budget decision." |
| Spike looks like a bug (duplicate requests) | Developer | "Seeing repeated identical requests from same session. Looks like a client-side retry loop." |
| Approaching Bedrock quota | Developer + AWS account owner | "ThrottlingException rate increasing. Need quota increase request before users see errors." |
| Monthly spend at 80% budget | Finance + stakeholder | "AWS spend at 80% of monthly cap with X days left. Need approval to continue or throttle." |

---

## What the Logs Tell You

Every request logs this line:

```
stopReason=end_turn inputTokens=312 outputTokens=487 duration=2.1s
```

From the logs you can answer:
- Which requests are large? (high `inputTokens`)
- Are responses being cut off? (`stopReason=max_tokens`)
- Is one session generating all the traffic? (same session appearing repeatedly)
- Is latency normal? (`duration` field)

To find spikes in Vercel logs, filter for `inputTokens` values significantly above your normal baseline, or filter for `stopReason=max_tokens`.

---

## Escalation Message Template

> **Token spike detected — [date/time]**
>
> Input tokens this hour: [X] (normal: ~[Y])
> Output tokens this hour: [X] (normal: ~[Y])
>
> Likely cause: [abuse / traffic growth / bug / unknown]
> Evidence: [one IP, many users, duplicate requests, etc.]
>
> Estimated cost impact: [$ if known]
> Recommended action: [rate limit / quota increase / investigate / no action]
>
> Logs available at: [Vercel log link]
