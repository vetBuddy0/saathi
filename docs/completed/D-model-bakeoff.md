# D — Model bake-off

## What happened to the original brief

None of the three originally requested models
(`llama-3.3-70b-versatile`, `qwen/qwen3-32b`, `moonshotai/kimi-k2-instruct`)
exist on this Groq account — confirmed live against `GET /v1/models`,
including an explicit retry of Kimi K2 per instruction ("not listed" may
be a tier issue, not permanent — retried anyway; still absent). You
picked four real, available alternatives to compare instead:
`openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `qwen/qwen3.8-27b`,
`groq/compound`.

## What was run

The same four prompts (a greeting, a memory question, one needing a
tool call, one in Mandarin) through all four models, real calls, same
persona system prompt `cascade.py` actually uses. Full transcripts kept
in the session's scratch output.

## Findings

| | tool call | memory question | Mandarin | latency | cost/turn |
|---|---|---|---|---|---|
| **qwen/qwen3.8-27b** | correct | honestly says no memory | only model to admit it has no real weather data, instead of inventing some | fastest (0.26–0.49s) | tracks the visible reply exactly, no hidden overhead |
| gpt-oss-120b | never called it | honest | invented specific weather | 0.68–1.15s | 3–10x the visible reply length in completion_tokens — hidden reasoning-token cost |
| gpt-oss-20b | correct | **fabricated a fake memory** about a daughter visiting for tea | invented weather too | 0.60–1.13s | same hidden-reasoning problem, worse (501 tokens for one 2-sentence reply) |
| groq/compound | **doesn't support tool calling at all** (400 error) | honest | failed outright (413, request too large) | slowest (2.1–6.6s), one outright failure | no published price; 1000+ prompt tokens/turn from its own internal orchestration |

## Decision

`qwen/qwen3.8-27b` — the only model that got the tool call right *and*
refused to invent both the fake memory and the fake weather, fastest,
and its token usage matches what it actually says. `groq/compound` is
disqualified outright (no tool calling — a hard requirement for this
device). `gpt-oss-20b`'s fabricated memory is the single most concerning
result across the whole test, for exactly the population this device
serves — decided disqualifying, not just noted.

Set as `cascade.py`'s `_LLM_MODEL` default. Recorded in `DECISIONS.md`.

## Not verified / left open

- Only one trial per prompt per model — a larger sample would
  strengthen (or complicate) the ranking, not attempted here.
- No Pi-side latency numbers for any model (network-bound, so unlikely
  to differ much from laptop numbers, but not measured).
- Kimi K2 access: still genuinely worth retrying periodically if the
  Groq account's tier ever changes — the substitution here is a real
  decision made with real alternatives, not a placeholder waiting for
  the "real" model to become available.
