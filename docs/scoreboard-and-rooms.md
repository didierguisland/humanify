# Design: scoreboard, grader and rooms (phase 2, part 1) — v2

> English translation of the original Spanish design document (Bases/DISENO-MARCADOR-SALA.md), lightly sanitized for publication.

2 Nov 2026. v2 = v1 + the review by two Sol high runs (`revision-fase2/SINTESIS.md`, with the reasoning behind each change), plus the 11 adjustments from the verifier (`REPORTE-3-verificacion.md`; marked v2.1). Replaces §5 and §7 of `ARQUITECTURA.md` once installed. Plan approved by Didier on 2 Nov.

**Boundary:** numbers go to SQLite (`registro/marcador.db`); judgment, rules and explanations go to `.md` files.
**Criterion:** detect, do not harden. The agents cooperate; the risk is that they drift.
**Style:** Python 3.9 standard library (`/usr/bin/python3`), nothing to install; bash 3.2 wrappers like `herramientas/anota.sh`. Reused from `hooks/_humanify.py`: `lock()`, `append()`, `reg_path()`, `project()`, `log_event()`. Hooks are cut off at 3 s (`dispatch`): inside a hook only millisecond-scale writes are done; everything else runs in the background (`start_new_session=True`).

Glossary of Spanish terms used below: *marcador* = scoreboard; *sala* = room (a shared workspace for agents); *tareas / corridas / veredictos* = tasks / runs / verdicts; *calificador* = grader; *vigilante* = watcher; *auditor* = auditor; *retrabajo* = rework; *encargo* = work order / assignment.

---

## A. Scoreboard (marcador)

### Schema (SQLite, WAL, `busy_timeout` 2 s, writes with `BEGIN IMMEDIATE`)

```sql
tipos(clave TEXT PRIMARY KEY, descripcion TEXT)
  -- diseno, construir, bug, revisar, arquitectura, investigar, resumir, redactar, datos, creativo, video, sin-tipo
  -- each with a one-line description saying where it ends (so they do not overlap)

tareas(id INTEGER PRIMARY KEY, uuid TEXT UNIQUE, fecha TEXT, proyecto TEXT, sesion TEXT,
       tipo TEXT REFERENCES tipos, descripcion TEXT,                 -- ≤ 120 characters
       riesgo TEXT CHECK(riesgo IN ('normal','caro')) DEFAULT 'normal',
       sala TEXT, compartida INTEGER DEFAULT 0,                      -- 1 if there was more than one executor
       criterio TEXT,                                                -- "done well" criterion, if given
       evidencia TEXT)                                               -- path to the evidence folder

corridas(id INTEGER PRIMARY KEY, tarea INTEGER REFERENCES tareas,
         origen TEXT, id_externo TEXT,                               -- agent:<agentId> | codex:<session>:<n> | argon:<conversation_id> | calificador:<task>:<n> | vigilante:<room>:<n> | manual:<uuid>
         rol TEXT CHECK(rol IN ('ejecucion','apoyo','retrabajo','calificador','vigilante','auditor')),
         vuelta INTEGER,                                             -- only increases with rol=retrabajo
         agente TEXT, esfuerzo TEXT, cuenta TEXT,                    -- claude | chatgpt | google
         tokens_entrada INTEGER, tokens_salida INTEGER, tokens_razonamiento INTEGER,
         tokens_cache_leida INTEGER, tokens_cache_escrita INTEGER,   -- NULL = unknown, never 0
         segundos REAL, exacto INTEGER,
         pruebas TEXT CHECK(pruebas IN ('paso','fallo','na')) DEFAULT 'na',
         fecha TEXT,
         UNIQUE(origen, id_externo))

veredictos(id INTEGER PRIMARY KEY, tarea INTEGER REFERENCES tareas,
           juez TEXT CHECK(juez IN ('calificador','didier','auditor')),
           nivel INTEGER CHECK(nivel IN (0,1,2) OR nivel IS NULL),   -- NULL = not evaluable
           motivo TEXT, huella_evidencia TEXT, ciego INTEGER DEFAULT 0, fecha TEXT)

pesos(agente TEXT, esfuerzo TEXT, peso REAL, desde TEXT, PRIMARY KEY(agente, esfuerzo, desde))

-- v2.1
salas(uuid TEXT PRIMARY KEY, tarea INTEGER REFERENCES tareas, agentes TEXT,
      mensajes INTEGER, rechazos INTEGER, banderas INTEGER, altos INTEGER, llamadas_vigilante INTEGER,
      forzada INTEGER DEFAULT 0, cerrada_en TEXT)            -- written once (idempotent by uuid); it is the receipt of closing
trabajos(id INTEGER PRIMARY KEY, clase TEXT CHECK(clase IN ('calificar','auditar')), tarea INTEGER,
         estado TEXT CHECK(estado IN ('pendiente','lanzado','hecho')), intentos INTEGER DEFAULT 0, fecha TEXT,
         UNIQUE(clase, tarea, fecha))
-- tareas.tipo_sugerido TEXT: what the grader proposes when the task arrived as sin-tipo (does not change tipo)
```

**Data rules**
- **Mutually exclusive categories (v2.1):** `tokens_entrada` = **non-cached** input; `tokens_cache_leida` and `tokens_cache_escrita` are separate; `tokens_salida` includes reasoning; `tokens_razonamiento` is informational only (a subset of output, not added). Per adapter: Claude → `input_tokens` already excludes the cache. Codex → input = `input_tokens − cached_input_tokens`, cache read = `cached_input_tokens`, output = `output_tokens` (includes `reasoning_output_tokens`). agy → `input_tokens` and `cache_read_tokens` are separate, and `output_tokens` includes `thinking_tokens` (checked: total = input + output).
- **Equivalent tokens** = input + output×5 + cache read×0.1 + cache write×1.25. If a component is missing, it is computed with the known ones and the run is left with `exacto=0`. The cache is huge (one Sonnet subagent read 4.8 M of cache against 117 thousand of output); without these factors it would dominate everything.
- **Cost** of a task = Σ equivalent tokens × the weight in force on the date of the run, over all its runs. `calificador` and `vigilante` runs are summed separately, as the cost of measuring.
- **Type:** set when the task is created. If it arrived as `sin-tipo`, the grader only fills `tipo_sugerido`; `sugerir` and `eficiencia` ignore `sin-tipo` tasks until the chat or Didier sets the type (v2.1).
- **Quality** = the latest verdict from `didier`; if none, the one from `auditor`; if none, the one from `calificador`. If any `retrabajo` run exists, the cap is 1.
- A verdict is valid only for the evidence fingerprint it saw. If the evidence changed (for example, because of a new rework round), the grader runs again.
- Nothing is deleted.

### Views
- `eficiencia` (efficiency): by type × agent × effort, **non-shared tasks only** → cases, % accepted (level 2), % redone (0 or with rework), mean cost and cost per accepted task.
- `salas_metricas`: tasks with a room versus without a room, by type → the same metrics. (v2.1: it is not called `salas` because that is the table's name; Sol caught this in work order 2.)
- `sin_veredicto`: tasks without a verdict after more than 30 min.
- `calibracion`: tasks with more than one judge → agreement among grader, auditor and Didier.

### Capture: fast queue + idempotent processor
Hooks and wrappers **do not touch the database**. They append one JSON line to `registro/cola-marcador.jsonl` (with `append()`, in milliseconds) and launch `marcador.sh procesar` in the background. The processor takes its own lock, reads new lines from its mark and, **in the same transaction**, inserts (duplicates are ignored through `UNIQUE`), creates the pending grading `trabajo` if needed, and advances the mark. Afterwards it launches the pending jobs. A `lanzado` (launched) job more than 15 min old with no verdict is relaunched, up to a maximum of 3 attempts (v2.1). If anything fails, the line stays and is retried on the next `procesar`.

| Source | Event | Tokens |
|---|---|---|
| Claude subagent | `PostToolUse` matcher `Agent` (event `delegacion`): arrives at **launch** (`status: async_launched`, `agentId`, `resolvedModel`, `description`; verified 2 Nov) → creates the task. `SubagentStop` (event `subagente`) → records the run. | Exact: sum of the `usage` of each message in `<session>/subagents/agent-<agentId>.jsonl`. |
| Codex (Sol, Terra) | `herramientas/codex.sh` wraps `codex exec`: records the start, captures the session id that Codex prints and, when done, enqueues the run. | Exact: last `token_count.total_token_usage` in **that** session's file; on resume, the difference against the last recorded total. |
| Argon / Flash | `herramientas/argon.sh` over `agy -p … --model M --output-format json` → saves the response and enqueues. | Exact: `usage.{input,output,thinking,cache_read}_tokens`, `duration_seconds`, `conversation_id` (verified 2 Nov). |

**Type, round and risk in the subagent's `description`:** `[tipo]`, `[tipo!]` (expensive risk), `[tipo@N]` (execution inside already-created task N, for example that of a room), `[tipo#N]` (rework of task N) and `[tipo+N]` (support for task N). In `codex.sh` and `argon.sh` they are options (`--tipo`, `--riesgo`, `--tarea N`, `--retrabajo N`, `--apoyo N`, `--rol auditor`, `--criterio "…"`); `--tarea` is also taken from the `MARCADOR_TAREA` variable, which `sala.sh lanzar` sets (v2.1). No tag → `sin-tipo` and the grader infers it.

### `herramientas/marcador.sh` interface (contract with the other pieces)
Output ≤ 10 lines. Exit codes: 0 ok, 2 usage error, 3 database busy (retry).

```
marcador.sh tarea --tipo T --desc "…" [--riesgo caro] [--criterio "…"] [--proyecto P] [--sesion S] [--sala ID] [--evidencia DIR]  → id
marcador.sh encolar '<json>'                       → what hooks and wrappers use
marcador.sh procesar                               → idempotent; launched by hooks, wrappers and startup
marcador.sh veredicto --tarea N --juez J --nivel 0|1|2|nulo --motivo "…" --huella H [--ciego]
marcador.sh corregir N 0|1|2 "motivo"              → Didier's shortcut (juez=didier)
marcador.sh resumen [--dias 7]                     → efficiency, rooms and calibration
marcador.sh sugerir --tipo T [--riesgo caro]       → best combination with data, or "pocos datos" (too little data); notice to explore
marcador.sh pendientes                             → sin_veredicto
marcador.sh auditar                                → picks 2 random tasks from the week for the auditor
```

### Decision rules (`sugerir`; to `MODELOS.md` as a proposed N1 rule)
- Fewer than 5 cases → "pocos datos". With 5 or more the figures are shown, but `sugerir` **never declares equivalence**; only a paired blind test gives that (the same task with two options and a judge who does not know who did what). At most one per week.
- One in every 5 tasks of a type with normal risk, `sugerir` proposes exploring one cheaper step. With expensive risk, never.
- `sugerir` advises; the orchestrator decides; Didier wins.

---

## B. Grader and auditor

- **Grader (calificador):** Opus low, in the background, launched by `procesar` when an execution or rework run closes. Command (**verified 2 Nov** with the OAuth account): `claude -p --safe-mode --model opus --effort low --output-format json --no-session-persistence --tools "" --system-prompt "<rubric>" --json-schema <schema>`, from an empty folder inside `registro/`. `--safe-mode` turns off CLAUDE.md, hooks and plugins. `--tools ""` and `--system-prompt` remove the factory instructions: the call dropped from 30.6 thousand to 1.4 thousand tokens (from 0.25 to 0.012 USD nominal). The response carries `structured_output` and `usage`, which are enqueued as a `rol=calificador` run.
- **Evidence** (the task's folder, trimmed to ~6000 characters): the work order or prompt; the criterion if it exists; the final report or response (≤3000 characters); **anything verifiable when it exists**: test output, `git diff --stat`, files touched against the requested zone. Never the full code.
- **Rubric:** 2 = met the criterion, with evidence, without leaving the zone. 1 = useful with adjustments or minor gaps. 0 = did not meet it, broke something, or must be redone. `nulo` = the evidence is not enough to judge (for example, the agent only says "done"). Responds `{nivel, tipo, motivo}`. The agent's self-report without evidence is not enough for a 2.
- **Didier** only intervenes when he disagrees: the chat runs `marcador.sh corregir`. Startup warns with one line if there are pending or `nulo` tasks.
- **Auditor:** every week, `marcador.sh auditar` picks 2 random tasks and Sol high re-grades them with the same rubric, without seeing the previous verdict (`codex.sh --rol auditor --tarea N`, read-only, ChatGPT quota). Its spend is a `rol=auditor` run (cost of measuring, never a round) and its JSON response is recorded as a `juez=auditor` verdict. This feeds `calibracion`. The grader is not changed (for example, to Sonnet) without at least 8 audits with good agreement.

---

## C. Rooms (salas)

For work where two or more agents touch each other's output. In a chain, the report is enough. Never for simple tasks.

### Where each thing lives
- `Humanify/registro/salas/<uuid>/` (authoritative, outside the project): `estado.json` (phase, version, participants, processes with PID + start time, per-agent read mark, watcher budget, scoreboard task), `REGLAS.md`, `CHAT.md` and `huellas` (hash of each accepted line).
- `<project>/salas/<name>/` (working copy, where agents can write): `plan-<agent>.md` and a read-only copy of the chat that `sala.sh` regenerates.
- Agents write only through `sala.sh`. If a line without a fingerprint or a changed `REGLAS.md` appears → flag.

### `herramientas/sala.sh` commands
```
abrir <name> --agentes a,b[,c] --tipo T [--riesgo caro] [--criterio "…"]   → uuid; creates the task in the scoreboard
lanzar <uuid> <agent> -- <command…>       → starts the process in **its own group** (setsid), with MARCADOR_TAREA, and stores the leader's PID + start
unir <uuid> <agent> --subagente <agentId> → for Claude subagents (no PID: never sent a signal)
plan <uuid> <agent> "<≤5 lines>"
esperar <uuid> <agent> --fase F           → returns at once if the phase has already passed; cap 10 min
leer <uuid> <agent>                       → only what is new for that agent, ≤ 20 lines
decir <uuid> <agent> <to> "<text>"
fin <uuid> <agent> ["GUARDAR: …"]…
alto <uuid> "<reason>"                    → watcher or orchestrator
estado <uuid>                             → one line
cerrar <uuid> [--forzar]                  → owner or sweep
```
Participants have status `activo` (active), `terminado` (finished) or `ausente` (absent): `fin` marks finished; for subagents, the `SubagentStop` hook also does (v2.1). Every write to `estado.json` and `CHAT.md` goes under `lock()`, which is taken only to write and never while waiting or calling a model. Each change bumps `estado.version`.

### Phases (advanced by the script)
1. **planes (plans):** each agent runs `plan` and then `esperar --fase arranque`. Advances when all plans are in or after 10 min; whoever is missing is flagged.
2. **arranque (kickoff):** at most 2 rounds; in each round, one line per agent, in any order. The round closes when everyone has written or 2.5 min pass (whoever did not write counts as absent for that round). Advances when everyone says `listo` (ready) or the rounds run out. No strict turns (v2.1). The watcher runs at this transition, and the **trabajo** phase waits for its OK.
3. **trabajo (work):** `leer` and `decir … avance|duda|bloqueo: …` (progress | doubt | blocker) at the start of each large step and before finishing. It is turn-based, not live.
4. **fin (end):** `fin` with optional `GUARDAR:` ("save") lines.

### Hard rules for `decir` (no model)
More than 200 characters, a recipient not allowed by `REGLAS.md` (for example, Sonnet does not give instructions to Argon; it can only speak to `orquestador` or `todos`), out of phase, a second message in the same kickoff round, or the cap of 12 messages per agent → rejected with the reason. Room `detenida` (halted) → "ALTO: <reason>. Finish and report". For Claude subagents, in addition, a `PreToolUse` denies `Write` or `Edit` on `registro/salas/**` and `*/salas/*/CHAT*`.

### Watcher (vigilante)
1. **Script flags (free):** a message nearly identical to an earlier one, a question unanswered after 2 messages, 3 rejections of the same agent, absence when a phase expires, a line without a fingerprint. Clear violations are resolved here, without a model.
2. **Opus medium on events:** the kickoff → work transition (mandatory), every 8 messages and grouped flags. **One active review at a time** and a **budget of 4 calls per room**. When it runs out, the next flag triggers an automatic ALTO so the orchestrator decides. Command: same as the grader (`--safe-mode --tools "" --system-prompt`), with `--effort medium`, and with `REGLAS.md`, the plans and the last 20 lines. Responds `{veredicto: OK|ALTO, motivo, a_quien, version}`. If `version` is no longer the current one, the result only counts as a flag. Deadline of 3 min. If the mandatory review times out or fails, the room advances with the flag `sin vigilancia` (unwatched). If no budget is left and a review is needed, the room goes to ALTO for budget (v2.1). Its spend is enqueued as a `rol=vigilante` run.
3. **ALTO (halt):** phase `detenida` (persistent), a line in the chat, and 2 min of margin for each agent to report. Afterwards, SIGTERM to the **group** of each launched process (`killpg`), only if the leader's start time matches what was recorded; this also takes down the child CLI. After 30 s, SIGKILL to the group if still alive (v2.1). Claude subagents are stopped only by the response of their commands.

The orchestrator finds out when its agents return, through `sala.sh estado` and through the startup of any chat of the project, which reports halted rooms.

### Launching the agents
`abrir` prints the fixed header for each assignment (template `instalacion/plantillas/sala-REGLAS.md`): role, uuid, commands, and the line "if a room command answers ALTO, finish and report".
- **Codex:** `sala.sh lanzar <uuid> sol -- herramientas/codex.sh … --add-dir <Humanify>/registro/salas/<uuid>`, with the room's working folder inside its area.
- **Argon:** `sala.sh lanzar <uuid> argon -- herramientas/argon.sh …` with `--settings '{"permissions":{"allow":["command(<path>/sala.sh)"]}}'` (without `*`: that way it matches any argument; with `*` it denies). Never `--dangerously-skip-permissions`. **Verified 2 Nov:** it denies other commands, `;`, `$( )` and writing files, and any denial cuts off the whole response.
- **Claude subagent:** `Agent` in the background and, right after, `sala.sh unir … --subagente <agentId>`.

### Closing and sweeping (a single procedure)
`cerrar <uuid>`:
1. Takes the room lock. If it was already `cerrada` (closed), it ends doing nothing (idempotent).
2. Revalidates: exits without changing anything if there is a live PID with a matching start, an `activo` participant without `fin` (including subagents, which have no PID), or activity in the last 10 min. The owner's `--forzar` skips this and is noted.
2b. Saves the previous phase and marks `cerrando` (closing): from then on, every agent command is rejected with "sala cerrándose" (room closing). If a later step fails, the previous phase is restored (v2.1).
3. Writes each `GUARDAR:` as a candidate "por validar" (to be validated) with id `<uuid>:<n>` into `Bases/APRENDIZAJES.md` (in Project A, `Bases/HUMANIFY-CANDIDATOS.md`) with `anota.sh`. If the id is already written, it skips it.
4. Enqueues the `salas` row (external id = uuid) and runs `procesar`. The confirmation is that this row exists.
5. Only if every id has a receipt at its destination and the scoreboard confirmed, deletes the two folders and leaves one line in `registro/salas-cerradas-AAAA-MM.jsonl`. On any failure it keeps everything and notes it in the hooks log, which startup already checks.

**23:30 sweep** (`launchd`, `herramientas/barrido.sh`, no model): calls `cerrar` for each room idle for more than 3 h with no live PID. If a room of more than 40 lines has no `GUARDAR:` at all, it first launches a rescue with Opus low (`--safe-mode`), which proposes up to 3 `GUARDAR:` lines. It never touches an active room.

---

## To verify while building (minimal test, before continuing)
1. `SubagentStop` payload: whether it carries `agent_id` or the transcript path, and whether it also arrives in the foreground.
2. ~~`claude -p --safe-mode` with OAuth~~ verified 2 Nov.
3. ~~`agy --settings` with a specific command permission~~ verified 2 Nov (see "Launching the agents").
4. ~~Per engine: join, wait, advance, receive ALTO and finish~~ verified 2 Nov with Codex Sol low (`--add-dir`, sandbox workspace-write), Argon low, Flash low and a Sonnet subagent, with two rooms at once; ALTO during `esperar` in all three engines and `killpg` with SIGKILL on a process that ignores SIGTERM.

## Out of scope
`/semana` (except `marcador.sh auditar`), a panel in Neo, new contracts, a new Humanify format, automatic blind tests.
