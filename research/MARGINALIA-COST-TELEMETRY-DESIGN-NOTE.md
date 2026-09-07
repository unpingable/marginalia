# Marginalia — cost telemetry (design note)

Date recorded: 2026-09-06

Status: **DESIGN NOTE / READY TO SCHEDULE**

Now tractable because the OpenRouter path returns real usage. Cost stops being
guesswork and becomes ordinary accounting.

---

## 1. Observed versus estimated

Keep the two separate and record both. The gap between them is the useful
signal:

```
request:
  estimated_prompt_tokens
  configured output reserve
  estimated maximum cost

response:
  provider_prompt_tokens
  completion_tokens
  reasoning_tokens
  actual cost
```

Reasoning tokens deserve their own field rather than being folded into
completion. One measured GLM 5.3 turn at ~19k input spent **5,310 of its 5,526
completion tokens on reasoning** — 96%. A model that folds them together cannot
explain its own bill.

## 2. Aggregation

```
session / project / model / provider / day / month
```

Author-facing, roughly:

> GLM 5.3 · 15,937 input · 5,526 completion (5,310 reasoning) · $0.047

> Doverton session: $0.38 · OpenRouter key: $19.62 remaining

The per-turn number matters more than it looks: GLM 5.3 full costs **$0.047** at
this session's size against **$0.0018** for Flash — 26×, and Orion is free. That
is a choice worth showing the author at the point she makes it, not a report she
reads afterwards.

## 3. The engineering payoff

Measurement lets Marginalia calibrate its budgeting model against reality. The
`provider_overhead_tokens = 16000` constant is subtracted from the input target
to produce the application budget and added back for telemetry, and nobody can
currently say what it represents. It has never been checked against a provider's
own reported `prompt_tokens`.

It now can be. One data point already exists: a request Marginalia sized at
~19,600 application tokens was reported by the provider as **15,937 prompt
tokens**. Collecting that comparison systematically turns backlog item 5 from a
redesign into a measurement.

## 4. Keep it separate from admission

Same token observations, different contracts:

| | question | contract |
| --- | --- | --- |
| **admission** | will this fit safely? | correctness and safety boundary |
| **billing telemetry** | what did this cost? | economics |

Do not let a cost signal influence admission, and do not let admission
arithmetic become the source of truth for billing. Sharing the measurement is
correct; sharing the contract is not. An admission budget that starts optimising
for cost is no longer a safety boundary.

## 5. Scope

- record observed usage on every response that reports it;
- record estimated cost on every request, including providers that report
  nothing back, marked as estimated;
- aggregate by the §2 dimensions;
- surface per-turn cost at model selection and per-session cost in the project
  view;
- expose the estimate-versus-observed delta for operator use, not author use.

## 6. Non-goals

Not billing enforcement. Not a budget that blocks generation. Not per-author
quota management. Not a replacement for the provider's own accounting, which
remains authoritative.

---

*Recorded 2026-09-06. Design note only: nothing implemented.*
