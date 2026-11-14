# Numbers

Aggregates only, taken from the author's own scoreboard database (`marcador.db`) in a single read-only snapshot. No prompts, responses or task text are included, and the database is not part of this repository. A synthetic sample with the same schema is in `examples/scoreboard-sample.db`.

Small samples: most comparisons between agents are n = 1 to 3 cases. These are operating numbers, not statistical conclusions.

## Period and volume

- First task: 2026-11-02 12:06 Mexico City time; last task: 2026-11-13 21:24 (stored in UTC, converted at UTC-6; Mexico City had no daylight saving in this period).
- First run: 2026-11-02 12:06; last run: 2026-11-13 21:24.
- Tasks: 425. Runs (one agent invocation each): 1113. Verdicts: 414, covering 394 distinct tasks (31 tasks have no verdict yet).
- The database is live: the counts above are from one snapshot and grow as work continues.

### Runs by role

| Role | Runs |
|---|---|
| ejecucion | 674 |
| calificador | 413 |
| apoyo | 12 |
| vigilante | 9 |
| retrabajo | 3 |
| auditor | 2 |

### Tasks by type

| Type | Tasks |
|---|---|
| sin-tipo | 273 |
| revisar | 38 |
| creativo | 35 |
| construir | 26 |
| video | 21 |
| investigar | 19 |
| diseno | 7 |
| resumir | 5 |
| datos | 1 |

Most tasks are `sin-tipo` (unclassified): the type label is optional and is written in the subagent description (`[type]`); unlabeled work stays `sin-tipo`.

## Verdicts

| Judge | Level | Count |
|---|---|---|
| calificador | 0 | 2 |
| calificador | 1 | 156 |
| calificador | 2 | 63 |
| calificador | null (not evaluable) | 192 |
| didier | 2 | 1 |

Levels: 2 = met the brief with verifiable evidence, 1 = usable with adjustments, 0 = failed or must be redone (see `cerebro/contratos/calificador.md`). The grader is an isolated model; the author's own verdicts are rare (one so far). A large share of grader verdicts are null because the evidence was not enough to judge.

## Estimated cost

- Estimated total in USD: **217.00**, over the 673 runs that have a price. The other 440 runs have no USD figure (see below).
- Of that, grading/monitoring/auditing runs (roles grader, watcher, auditor) account for 6.87 USD.

| Agent family | Runs | Input tokens | Output tokens | Cache read | Cache write | Est. USD | Runs without USD |
|---|---|---|---|---|---|---|---|
| Claude Opus | 558 | 3,434 | 3,045,119 | 170,713,104 | 12,117,409 | 155.65 | 0 |
| Gemini Argon | 155 | 2,643,387 | 493,334 | 1,765,193 | 0 | n/a | 155 |
| (no agent recorded) | 141 | 0 | 0 | 0 | 0 | n/a | 141 |
| Claude Sonnet | 114 | 2,532 | 1,413,739 | 117,337,190 | 9,495,408 | 61.35 | 0 |
| GPT Sol (Codex) | 56 | 1,267,940 | 181,986 | 16,950,272 | 0 | n/a | 56 |
| GPT Terra (Codex) | 46 | 2,088,776 | 51,261 | 10,349,184 | 0 | n/a | 46 |
| Gemini Flash | 37 | 700,874 | 17,563 | 0 | 0 | n/a | 37 |
| GPT other (Codex) | 4 | 74,394 | 2,205 | 507,136 | 0 | n/a | 4 |
| Claude Haiku | 1 | 2 | 196 | 0 | 55,497 | 0.01 | 0 |
| Claude Fable | 1 | 890 | 41,478 | 3,397,053 | 155,345 | n/a | 1 |

Per-model detail (Claude only, the models that have prices): see the `_costos_usd` view; the family rows above merge model aliases (for example `opus` and the full model id).

### How the cost is estimated

1. Each run stores token counts (input, output, cache read, cache write). The view `_costos_usd` multiplies them by a per-million-token price table (`precios`, seeded in `hooks/_marcador.py` from list prices; cache write is 1.25 x input) and divides by 1e6.
2. Only Claude models have a price row. Gemini and Codex (GPT) runs are paid through subscription quota, so their USD is NULL by design and they appear as `n/a`, not as zero. One Claude run (`Claude Fable`) has no price row either, so it is also `n/a`. The total therefore covers priced Claude models only.
3. Token counts are exact when the agent's own usage report was complete (`exacto` = 1) and approximate otherwise. For Claude runs, 'input tokens' are uncached input only; the cache columns are priced separately, which is why Claude input counts look small.

## Things that do not add up cleanly

- 149 runs have no token data at all (null input and output); 149 runs are flagged as not exact. The `(no agent recorded)` rows have an empty agent field, no tokens and no USD; their cause was not investigated here.
- The same model appears under several labels (for example `opus` and `claude-opus-5-5`; `argon` and `gemini-4-argon-medium`). Families above merge them by name matching; per-effort splits are not merged.
- Gemini and Codex figures are token counts only. Comparing them with Claude on cost is not possible from this data, because quota is not priced.
- An earlier informal summary of this database quoted slightly different counts and a different total; the database was still being written to, so only the snapshot above should be cited.
