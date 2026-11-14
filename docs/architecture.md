# Humanify Architecture v1.1 (2 Nov 2026, Opus 5.5 High)

> English translation of the original Spanish design document (Bases/ARQUITECTURA.md), lightly sanitized for publication.

Source of intent: `BASES-HUMANIFY.md`. This is the specification to build from. Nothing is built yet.
v1.1 = v1 + the review by three Sol reviewers (`revision-sol/SALA.md` holds each finding and what was decided; v1 is kept in `revision-sol/ARQUITECTURA-v1.md`).

## The idea in one line

Humanify is a folder of `.md` files and a few AI-free scripts that hook into the Claude app: the `.md` files store what has been learned, by level of rigidity, and the scripts decide **what** gets loaded **when** and warn if something was left unclosed. The learning is done by the chat (as in Project A); the software only ensures it is not forgotten and does not cost more than necessary.

**File principle:** evidence is appended, what is current is rewritten, what is superseded is archived. A new chat reads only what is current; the history is there for when it is needed.

## 1. Where everything lives

```
Humanify/                  (local git, no remote)
  CLAUDE.md, Bases/        ← Humanify as a project, like any other
  cerebro/                 ← what gets installed for all projects ("cerebro" = brain)
    CONSTITUCION.md        N0: always loaded (target ~60 lines); imports REGLAS.md
    REGLAS.md              N1: always loaded (target ~40 lines), each rule with evidence and scope
    MODELOS.md             N1: model router, read when delegating (single source of the allocation)
    ROUTER.md              word → contract table, read by the router script
    contratos/             one per type of work: diseno, video, auditoria, codigo, escritura, investigacion
    referencias/           N2: Didier's style, formats, design tastes
    PROPUESTAS.md          what awaits his approval: 7 visible, the rest on hold
  hooks/humanify.sh        a single dispatcher; each event calls its own script
  herramientas/            anota.sh (locked writes), sala.sh, estado.sh
  plantilla/               what a new project receives (template)
  registro/                events (rotated by month) and state of sessions and rooms
Proyectos/<proyecto>/      own CLAUDE.md + Bases/ (snapshotted by the memory git)
Proyectos/Tareas/          loose tasks: 5-line CLAUDE.md, no memory unless requested
```

**A project's files** (they come from the template; Project A keeps its own, see section 10):

| File | Type | Contents | Who and when |
|---|---|---|---|
| `CLAUDE.md` | live | who we are, map, what to read and what not; points, does not copy | the chat, when the structure or scope changes |
| `Bases/ESTADO.md` (state) | live | situation in 3-5 lines + pending items with id, owner and blocker + open questions for Didier | the chat, as work advances, not only at close. Replaces `RELEVO-N` and `PENDIENTES` |
| `Bases/APRENDIZAJES.md` (learnings) | live | current learnings, by topic: conclusion, scope, level, source | the chat, as soon as it validates something: rewrites the entry, merges repeated ones, changes what has been superseded |
| `Bases/SESIONES.md` (sessions) | log | one entry per session with its id (what happened, what worked / did not, where it ended) **only if it adds value**; a minor session writes no narrative | the chat at close, with `anota.sh`; rotated monthly to `Bases/_archivo/` |
| `Bases/BASES.md` (foundations) | live | project decisions with a date | the chat, when Didier decides something |

On startup, a chat reads `CLAUDE.md`, `ESTADO.md` and `APRENDIZAJES.md`. `SESIONES.md` and `_archivo/` only if needed.

**Installation:** one line in `~/.claude/CLAUDE.md` (`@$HUMANIFY_HOME/cerebro/CONSTITUCION.md`) and a few entries in `~/.claude/settings.json` that call `hooks/humanify.sh <event>` with a 5 s `timeout`, alongside those of Notchy and `bloqueo-terceros.sh` (a third-party blocking hook; these are not touched).

**Switch-off with one line:** the dispatcher checks whether the `@import` line is still in `~/.claude/CLAUDE.md`; if Didier deletes it, the brain stops loading and the hooks exit without doing anything. This applies to new chats; open ones must be restarted. Undoing what has already been written is a different matter: that is what git is for.

**Memory git (snapshots of the `.md` files).** Humanify has its own regular git. For projects, a single "shadow" repo at `Humanify/registro/memoria.git` keeps a snapshot of `CLAUDE.md` and `Bases/**/*.md` of each project (`git --git-dir=… --work-tree=<project>`, only those files). It does not touch the code git a project may already have and adds nothing to it. `anota.sh cierre` (close) takes the snapshot at the end of each session, and the weekly review takes one before rewriting. This way each rewrite of a live file can be inspected and undone, and in Project A it acts as an automatic backup (reading in order to snapshot is not editing). No remote: nothing leaves the Mac. For Codex outside a repo, the `--skip-git-repo-check` option goes in `MODELOS.md`.

## 2. Levels of rigidity

| Level | What it is | Who changes it | Where |
|---|---|---|---|
| N0 Constitution | what is never broken: security, not touching without his approval, privacy, close cycle, destination policy | only Didier, in the chat | `CONSTITUCION.md` |
| N1 Rule with evidence | what is proven: model router, repeated corrections, contract limits | proposal with data + his approval | `REGLAS.md`, `MODELOS.md`, limits in `contratos/` |
| N2 Reference | tastes, styles, default methods, what depends on the case | a comment from him is enough | `referencias/`, method in `contratos/`, the project's `APRENDIZAJES.md` |
| N3 The moment | what Didier says now ("this is simple, no subagents") | always wins, is not stored | nowhere |

**Format of each entry (one line, searchable with grep):**
`- [N1] For interface sketches, Opus medium performs better than Sonnet high. — scope: interface sketches · ev: x2 (2-nov sala-1, sala-2) · contra: — · conf: 2026-11-02`

(`contra:` = counter-evidence; `conf:` = date last confirmed.)

**Moving up and down:**
- An N3 that Didier repeats or approves → N2.
- An N2 with x3 in at least two sessions → proposal to N1. Decisive evidence (a clear test, an expensive error) may be proposed earlier, stating why.
- Each rule keeps its scope and counterexamples: three cases from the same context are not generalized to all.
- If something today contradicts a rule, it is checked at that moment whether it applies to this task (no need to wait for the weekly review), and the contradiction is recorded in `contra:`. Two contradictions → a proposal to demote it.
- Promotion to N0 only if Didier says so.
- **Expiry = review on reuse.** If an N1 has gone more than 60 days without confirmation (or an N2, more than 90), when the chat is about to use it, it marks it "review" and says it is relying on something old. Nothing is invalidated or deleted by calendar.

**One rule, one place.** If something applies in two places, it lives in one and the other points to it. The model allocation lives only in `MODELOS.md`; contracts point there.

## 3. The hooks (four, no AI, none blocks)

All go through `humanify.sh`. They have a 5 s time limit, write a line in `registro/hooks-AAAA-MM.log` (event, ok/error, ms) and, if they fail, exit without getting in Claude's way and leave the error in the log.

**Project root:** always the first level under `Proyectos/` (opening `Project A/Videos/` is Project A). Outside `Proyectos/` it is a loose task. If the root cannot be determined, the hook warns and writes nothing.

**1. On startup (`SessionStart`: startup and resume).** Writes the context. The ~25 lines are a target, not a cut-off: if something does not fit, it gives the path instead of truncating.
- First line always: `Humanify activo · sesión <short id> · proyecto <root>` ("Humanify active · session · project"). The id is used for the close. The hook stores `registro/sesiones/<id>` with the time and the root.
- *Project with brain:* the contents of `ESTADO.md` (or, in projects with the old format, the latest `RELEVO-*`, warning that it may not be the full state). Also, how many proposals are waiting.
- *Previous session not closed:* a rescue is *suggested* (see hook 4); the chat or Didier may dismiss it.
- *Project without structure:* "propose the startup round before working; do not do it without his approval".
- *Loose task:* only the first line.
- *Project A:* "protected zone: Project A's cycle rules; Humanify only reads and adds candidates".
- If more than 7 days have passed since the last weekly review, a line reminding of it.
- If there were hook errors since the last startup, a line. An error is considered handled only when `/humanify-estado` marks it.

**Watchdog without a hook:** the constitution, which loads even if the hooks fail, says: "if you do not see the `Humanify activo` line in the startup context, tell Didier in your first response that the hooks did not run". This way a broken dispatcher cannot go for days without anyone noticing.

**2. After compaction (`SessionStart`: compact).** Gives `ESTADO.md` again, resets the router's memory for this session and leaves an instruction: "if something with support was learned before compacting, record it in `APRENDIZAJES.md` with its origin; if there was nothing, carry on". What is recalled after compaction is a candidate, never a direct rule.

**3. On receiving a message (`UserPromptSubmit`): the router.** Strips accents, lowercases and looks for whole words from `ROUTER.md`. What it finds is a **suggestion**: it prints `Contracts suggested by keyword: diseno. Use them if they apply to what Didier wants`, followed by the full contract. If there are several, it gives the highest-priority one in full and the path of the others. It never cuts a contract in half. It remembers per session which file and which version it already gave, to avoid repeating. If it finds nothing, it prints nothing.
The orchestrator decides based on intent: it confirms, combines or discards what is suggested ("do not make a video" does not call for the video contract). It also loads on its own what was not named, because the constitution carries a one-line-per-contract index of contracts ("make it clearer" may be design).
Format of `ROUTER.md`: `diseno|boceto|panel|interfaz|logo -> contratos/diseno.md (prioridad 2)`.

**Close receipt (same for all projects, Project A included):** when finishing its cycle, the chat runs `anota.sh cierre <id> "<one line>"`. That writes to `Humanify/registro/cierres-AAAA-MM.jsonl` (id, project, time and the line, which may be "nothing to save") and takes the memory snapshot. The receipt lives at the center and not in the project, so Project A receives no writes from Humanify: its narrative remains its own handoff.
When a chat is resumed (`resume`), startup rewrites the start mark with the new time. A receipt only counts if it is **later than the last resume**: yesterday's close does not cover today's work.

**4. On close (`SessionEnd`).** Looks for a receipt with this session's id and a time later than its start mark. If there is one, it deletes `registro/sesiones/<id>` and that is it. If there is not, and the chat had more than a few messages, it leaves it marked "unclosed" together with the transcript path. It also marks as "pending review" the rooms of **this** session, without closing or touching those of others.
**If `SessionEnd` does not get to run** (the app closes abruptly; unverified), the start mark stays alive. The next startup in that project finds a session more than 12 hours old with no entry and treats it as unclosed. Each rescue is offered only once: it is either rescued (a Sonnet reads the transcript and proposes entries) or dismissed, and in both cases it is resolved.

**Deliberately discarded:** `Stop` (runs every turn; using it to force the cycle would cost and get in the way), `prompt`-type hooks (use AI on every event), a hook that blocks edits in protected zones (Project A needs to edit its factory; protection is for Humanify's processes and lives in the constitution) and a separate watchdog launcher (covered by the constitution's rule).

**Optional in phase 2:** `PostToolUse` with an `Agent` matcher for the delegation log (see 5).

## 4. Contracts and the router

One **contract** per type of work (target ~30 lines), in two parts:
- **Limits (N1, mandatory):** what truly cannot or must not be done (technical, security, Didier's), each marked `comprobado` (verified) or `supuesto` (assumed).
- **Default method (N2):** who does it, format of the assignment (3 to 15 lines as a guide), of the report, and how it is verified in proportion to the risk. The orchestrator may depart from it with a one-line reason inside the assignment; that changes nothing stored and asks for no permission.

**Reports:** a summary of ≤10 lines on top (the only thing the orchestrator reads at first), detail and evidence below, with `DUDAS PARA OPUS` ("questions for Opus") at the end. `FACTIBILIDAD` (feasibility) is included only if there is a real doubt about whether something can be done.

**Reading to understand is allowed** when there is a concrete doubt: reading directed at what is needed, not "explore the project". The contract saves routine exploration; it does not deprive anyone of the information they need.

**Creative work gets grounded** (the Argon case), but without killing ideas: the grounding separates verified limits from assumptions. If an assumption is the only thing ruling out an idea, a minimal test is done first. The creative intent is kept, plus an alternative in case the limit turns out to be real. Grounding happens before building at scale, not before thinking. The first contract comes from `Diseno/CONTRATO-TERMINAL.md`.

The **response format** (paragraphs, conversational, a table only if needed) is N2 in `referencias/estilo-didier.md`, with a 5-line version inside the constitution.

## 5. Model router

Lives in `MODELOS.md` (N1). The orchestrator decides with four questions and the specialty:

- **How much judgment does it require?** A lot → Opus (or Argon if creative, always grounded by Opus).
- **How much has to be read or done?** A lot and mechanical → Sonnet; Flash for cheap bulk work.
- **How costly is a mistake?** Expensive (payments, client data, Project A in production) → it is verified; Sol audits the detail and Terra verifies against sources.
- **Would the error be noticed?** If an error would come out convincing and hidden (summarizing corrections, extracting rules, rescuing learnings), one does not start cheap. If one starts cheap, a sample is checked against the source.
- **Escalation:** mechanical work whose error would be noticed starts cheap and moves up if the result is poor; judgment work starts well, because redoing costs more.
- **Effort:** low executes something perfectly clear; medium is the daily level of the orchestrator and of the builder with good judgment; high for hard decisions that are expensive to get wrong.
- **Codex:** the global rule (ask before delegating, what is passed, read-only by default) remains in force; it moves to `MODELOS.md` unchanged.
- What Didier says in the moment wins (N3).

> **Replaced on 2 Nov by the SQLite marker** (`Bases/DISENO-MARCADOR-SALA.md` v2.1, sections A and B). What follows is kept as history.

**Delegation log** (`registro/delegaciones-AAAA-MM.jsonl`, one line per assignment: date, project, type, model, effort, tokens, time, verdict). It fills itself where possible: the `PostToolUse` hook on `Agent` for subagents and a line in `llamar.sh` for Codex and Gemini. The **verdict** (accepted, adjusted or redone) is set by the chat at close. What is measured is the **total cost**: if something cheap was redone, the spend of both rounds counts.

**Blind tests:** not periodic. The weekly review proposes at most one, only if the log leaves a real doubt, with its estimated cost.

## 6. Writing without stepping on each other (two chats at once)

- **Logs** (`SESIONES.md`, `registro/`): always with `anota.sh`. It takes a short lock (a `.candado` directory that waits up to 5 s and releases itself after 30 s if someone died), writes the full entry and releases it. The lock is never held while a model is thinking: the text is prepared first and written afterwards.
- **Live files** (`ESTADO.md`, `APRENDIZAJES.md`, rules): the chat changes them with the editing tool, which should reject the change if the file changed since it was read (**to be verified**; if it does not, `anota.sh` offers a "replace if unchanged" mode). They are edited by entry and not by rewriting the whole file from a stale read.
- **Clash of meaning** (two chats reach opposite conclusions): both are kept with their source and it becomes a pending item in `ESTADO.md`. Nobody chooses silently.

## 7. Rooms

> **Replaced on 2 Nov by temporary rooms with phases and a watchdog** (`Bases/DISENO-MARCADOR-SALA.md` v2.1, section C; `herramientas/sala.sh`). What follows is kept as history.

For parallel work where agents touch each other's output. In a chain, the report is enough. Never for simple tasks.

- `sala.sh abrir <name>` (open) creates `<project>/salas/<name>/SALA.md` with a header and an owner (the id of the session that opens it).
- Rules inside: short notes with the agent's name and time, nobody deletes another's content, `GUARDAR:` ("SAVE:") at the start of anything worth rescuing.
- Each participant ends with a line `FIN: <agent>`. `sala.sh cerrar` (close) refuses while anyone who wrote lacks their `FIN:`, unless `--forzar` (for example, if an agent died), and says so.
- `sala.sh cerrar <name>` (owner only): converts the `GUARDAR:` items into **candidates** with a source in `APRENDIZAJES.md` (marked "to validate"; the chat validates or discards them right there). In Project A they go to `HUMANIFY-CANDIDATOS.md`. Before archiving, it checks that as many candidates were written as there are `GUARDAR:` items in the room; if the numbers do not match, it does not archive and warns. Then it moves the room to `_archivo/salas/` and does not delete it. If run twice, it duplicates nothing.
- If the room is large and has no `GUARDAR:` at all, the orchestrator sends a Sonnet to rescue. Otherwise grep is enough.
- Archived rooms are deleted in the weekly review, once it has been seen that the rescue held.

## 8. Close cycle and where each learning is recorded

**During the session** (new in v1.1): when something is validated, its entry in `APRENDIZAJES.md` or `ESTADO.md` is rewritten at that moment. This way tomorrow's chat does not read something stale.

**On close** (lives in the constitution): `ESTADO.md` up to date; entry in `SESIONES.md` only if it adds value; receipt with `anota.sh cierre` (mandatory and one line); verdict on each delegation; `↑centro` ("up to the center") on whatever serves everyone; memory up to date.

- **Project-level** (its business, its flow, its corrections) → the project's `Bases/`.
- **Everyone's** (models, how Didier works, the architecture) → marked `↑centro` in the project; the weekly review proposes it for `REGLAS.md` (N1) or `referencias/` (N2). It enters only with his approval.
- **Destination policy (N0):** each Humanify process writes only to its own destination; in Project A, only to `HUMANIFY-CANDIDATOS.md`. No client names or data, amounts or accounts at the center.
- Projects with their own production (Project A) keep their `LIBRO-CORRECCIONES` (corrections book); the others use `APRENDIZAJES.md` with a counter.

## 9. Weekly review (no longer maintains, only reviews)

Since what is current is maintained at the moment, the weekly review only processes **what is new since the last review**. It is the `/semana` command (a skill): a Sonnet medium reads the new sessions, the `↑centro` items, contradictions, what is pending confirmation and the delegation log, and writes a summary of ≤40 lines in `PROPUESTAS.md`:
- what is repeated x3 or has decisive evidence, proposed as a rule; rules that are duplicated or clash;
- `↑centro` items ready to move up;
- three numbers from the log: spend by type of work, redone items and the model that performed best;
- rotation of `SESIONES.md` and the log, and archived rooms to delete;
- **seven visible proposals** at most, ordered by impact. The rest wait without being lost, grouped if they repeat. After 30 days without a response they move to "deferred": silence is not rejection, and they return if their topic reappears.

Opus reads the summary and, if it flags a disagreement, opens that specific source. Didier approves in one sitting. The reminder appears at startup, which costs nothing, instead of a scheduled task.

## 10. Projects, loose tasks and startup round

- **Project with brain:** the files in the table of section 1, with progressive loading.
- **Loose task:** `Proyectos/Tareas/`. It reads no state and writes no memory. If Didier says "save it", it is proposed for the center or proposed to turn it into a project.
- **Startup round** (`/arranque`, a skill): inventory of the folder, enrollment in the memory git, proposal of what to archive (to `_archivo/`; nothing is deleted without his approval), reading of the essentials and assembly of `CLAUDE.md` + `Bases/` from `plantilla/`.
- **Projects with the old format** (`RELEVO-N` + `PENDIENTES`, like Humanify itself today): they keep working. Startup reads them and warns. They move to the new format when Didier wants, in a short round.

## 11. Migration of Project A (protected zone)

1. Backup of all of Project A's `.md` files before installing: first snapshot in the memory git, plus a dated tar in `Humanify/registro/respaldos/` just in case.
2. Protected zone: all of `Videos/fabrica/`, `LIBRO-CORRECCIONES`, paths, team, library and the `/video` skill. Project A keeps its files and its cycle (handoffs included). Humanify's processes (weekly review, rescues, rooms, compaction) only read and write to `Project A/Bases/HUMANIFY-CANDIDATOS.md`. Project A continues editing its factory as always.
3. Startup in Project A says that Humanify is active and that Project A's cycle rules. Nothing is duplicated: Humanify only adds the session id, the close receipt (stored at the center), the memory snapshot and the candidates.
4. Smoke test: open a new chat and reopen an old one of Project A. Both must say "Humanify activo" and the factory must run a complete video exactly as before. Also, two Project A chats open at once and one is closed: the other must notice nothing.
5. Rolling back: remove the `@import` line and restart the open chats.

What is in `~/.claude/CLAUDE.md` (style, protocol with Codex, model allocation) moves to the brain in parts, each part with his approval. The global file keeps only the `@import` line and what is not Humanify's (the third-party hook rule stays where it is).

## 12. Efficiency

Fixed cost per chat: the constitution and the rules (~100 lines) plus startup (~25) and, in projects, current `CLAUDE.md` + `ESTADO.md` + `APRENDIZAJES.md`. About 150 to 200 lines in total, which no longer grow over the months because history is archived. Everything else is paid for only if used: a contract by keyword or by the orchestrator's decision, `MODELOS.md` only when delegating. The hooks use no AI. Rescues go by grep before going by model. The orchestrator reads summaries and opens the source only if something clashes. The spend of each task is measured as total cost (with redone work), and that decides what is worth it.

The line limits in this whole document are **budgets**: if something important does not fit, the path is given or that part is expanded, never cut.

## 13. Risks and how they stand

| Risk | Response |
|---|---|
| The engine overwrites too much | N0 destination policy; rules change only with his approval; memory git |
| Two chats write at once | `anota.sh` with a lock for logs; per-entry editing in live files; clashes become a pending item |
| Old knowledge or an unchecked log | what is current is rewritten when validated; history rotated by month; the weekly review sees only what is new |
| The "latest handoff" lies | replaced by a live `ESTADO.md` |
| A session is cut off and what was learned is lost | session id + start mark + rescue suggested once |
| Hooks failing silently | log, startup warning and, if the dispatcher dies, the constitution rule that notices without hooks |
| Cost or slowness of the hooks | no AI, 5 s `timeout`, they print little or nothing, rotated logs |
| False lessons | rescues and `GUARDAR:` enter as candidates; scope and counterexamples; x3 or decisive evidence with his approval |
| Rules that confine | limits vs default method; router that suggests; directed reading allowed; limits as budget |
| Client data reaching the center | N0 destination policy + weekly review filter |
| Two accounts share `~/.claude` | Humanify lives in files and does not depend on the account; the log records tokens, not quota |
| Breaking Project A | backup, protected zone, two-chat smoke test, one-line switch-off |

## 14. Build order

- **Phase 1 (this week):** Humanify git and memory git; `cerebro/` with the constitution (includes watchdog, contract index, destination policy and close cycle), rules, router and the first two contracts (design and video); `humanify.sh` with hooks 1 to 4 and `anota.sh`; template with the new format; `Proyectos/Tareas/`; `/humanify-estado`; installation; test on Humanify and Tareas; backup and smoke test in Project A.
- **Phase 2 (after a week of use):** delegation log, `sala.sh`, `/semana`, `/arranque`, the remaining contracts and moving Humanify itself to the new format.
- **Phase 3:** a Humanify tab in Neo (state, proposals with one-click approve, learned today, cost per task, hook errors). Not designed until phases 1 and 2 are actually in use.

**To verify when building (before installing):**
- that the `@import` with an absolute path works from the global CLAUDE.md;
- whether `SessionEnd` runs when the app closes abruptly;
- that `SessionStart` with `compact` injects correctly;
- which session id reaches the hooks and whether it is kept on resume or with the other account;
- what token data the `PostToolUse` of `Agent` carries (Neo already sees model and time; perhaps it is reused);
- whether Claude's editing tool rejects a change when the file changed since it was read.

The tests include: double execution of each hook, an interrupted write by `anota.sh`, and two chats in the same project.

## What changed relative to BASES (one line each)

- **Room rescue by grepping `GUARDAR:`** instead of a Sonnet: it costs nothing and almost always suffices. What is rescued enters as a candidate.
- **Unclosed sessions (new):** session id, start mark and a rescue suggested once. It was Project A's most serious failure.
- **"Before compacting" is done afterwards:** in `SessionStart` with `compact`, which can indeed ask the chat to record.
- **Automatic delegation log:** the chat only sets the verdict.
- **Blind tests only when there is real doubt:** each one costs double.
- **No hook blocking the protected zone:** protection is for Humanify's processes.
- **Weekly reminder at startup:** it costs nothing.
- **Real one-line switch-off:** the dispatcher checks the `@import`.
- **The panel moves to phase 3:** practical before pretty.
- **(v1.1) `RELEVO` + `PENDIENTES` → live `ESTADO.md` + `SESIONES.md` log:** the latest handoff had already lied. What is current is rewritten and history is archived, as Didier asked.
- **(v1.1) The weekly review no longer maintains, only reviews:** knowledge is updated at the moment.
- **(v1.1) `APRENDIZAJES-CENTRO.md` removed:** it duplicated what `REGLAS.md` and `referencias/` already do.
- **(v1.1) Memory git (shadow repo of `.md` files only):** archives what is superseded without getting into any project's code git.
- **(v1.1, after Sol-4's verification) Close receipt at the center, valid only if later than the last resume;** narrative in `SESIONES.md` only if it adds value; rooms with `FIN:` and rescue verified before archiving.
