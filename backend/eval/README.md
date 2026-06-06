# LLM explanation eval

A task-specific harness for the explanation layer. Generic model leaderboards
don't tell you whether a model invents facts about *your* recommendations — this
does, against frozen fixtures of your own computed outputs.

## Golden fixtures

`golden/*.json` — each is `{request, recommendations}` captured from a real run.
Regenerate / extend them with:

```bash
cd backend
.venv/bin/python -m eval.make_golden
```

Aim for 20–50 covering sunny/rainy, near/far, family/dog, varied place types,
missing-data cases, and EN + RU. Add an adversarial slice (omit weather, mark
traffic unknown) to test that a model says "unavailable" instead of inventing.

## Running

```bash
.venv/bin/python -m eval.run                 # template baseline (no-op, offline)

LLM_PROVIDER=llamacpp LLM_BASE_URL=http://localhost:8080/v1 \
  .venv/bin/python -m eval.run --live        # grade a local llama.cpp model

LLM_PROVIDER=deepseek LLM_MODEL=deepseek-chat LLM_API_KEY=sk-... \
  .venv/bin/python -m eval.run --live        # grade a cheap hosted model
```

## Metrics & model selection

The headline is **grounded %** — the share of explanations that introduce no
number absent from the computed facts (the same `is_grounded` guard used at
runtime). Treat it as a **gate, not a tradeoff**: drop any model below the
grounding/format threshold no matter how fluent it sounds, then pick the
cheapest/fastest survivor. Clarity (1–5) is a human/LLM-judge add-on; latency is
printed here. The runtime guard means a model that occasionally slips still
degrades safely to templates.
