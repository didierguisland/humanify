# Humanify: constitution (N0)

> English translation of `cerebro/CONSTITUCION.md` (the Spanish original is the file the system actually loads). Lightly sanitized: the project name is "Project A" and the install path is `$HUMANIFY_HOME`. The original also contains a closing `@import` line pointing to `cerebro/REGLAS.md`.

Humanify is the author's brain for all of his projects: do not learn the same correction twice. It lives in `$HUMANIFY_HOME`. Only the author changes this file.

## Watchdog
At startup, the hooks write a line `Humanify activo - session <id> - project <root>`. If you do not see it in the context, tell the author in your first reply: "the Humanify hooks did not run". It is not your error: it is a warning.

## Levels
- **N0** this file: author only.
- **N1** rules with evidence (`cerebro/REGLAS.md`, `MODELOS.md`, contract limits): change with data and his yes.
- **N2** references (tastes, methods, project learnings): a comment from him is enough.
- **N3** what the author says right now: always wins and is not saved.

Typical entry: `- [N1] conclusion - scope: ... - ev: x3 (dates, source) - against: ... - conf: YYYY-MM-DD`. If you are about to rely on an N1 rule more than 60 days unconfirmed (or an N2 more than 90), say so and mark it "review". If something today contradicts a rule, review it for this task and note the `against:`.

## Files
Evidence is added, what is current is rewritten, and what is replaced is archived. `ESTADO.md` (state) and `APRENDIZAJES.md` (learnings) are rewritten as soon as something is validated, editing the entry, not the whole file. Logs grow with `herramientas/anota.sh bitacora`. A rule lives in one place; the rest point to it.

## Destination policy
- Each Humanify process writes only to its own destination.
- **Project A:** its own cycle and its factory rule. The Project A chat rewrites its own files (handoffs, learnings, corrections book) as its CLAUDE.md asks. Only Humanify's automatic processes (rescues, weekly review) are limited to `Project A/Bases/HUMANIFY-CANDIDATOS.md`.
- No client names or data, amounts or accounts go up to the center (`cerebro/`).
- What serves all projects is marked `up-center` in the project. It goes up to the center only with the author's yes.
- What is rescued (from rooms, transcripts or after compaction) enters as a candidate "to validate", never directly as a rule.

## Closing cycle (every session with real work)
1. `Bases/ESTADO.md` up to date. In projects with the old format, the handoff and pending list as always.
2. Validated learnings, rewritten in their entry; mark `up-center` on what serves everyone.
3. Narrative in `Bases/SESIONES.md` only if it adds something.
4. Delegations: the scoreboard records and grades them by itself; if the author disagrees with one, `herramientas/marcador.sh corregir N 0|1|2 "reason"`.
5. **Always:** `$HUMANIFY_HOME/herramientas/anota.sh cierre <id> "<one line>"`, with the id from the startup line. "Nothing to save" is valid.
6. Memory up to date if something changed that matters between chats.

## Contracts (the router suggests them by word; load them yourself if they apply even if not named)
- `cerebro/contratos/diseno.md`: interfaces, panels, logos, sketches, anything visual.
- `cerebro/contratos/video.md`: videos; in Project A its factory rules.
- `cerebro/MODELOS.md`: before delegating to another model (subagent, Codex or Gemini).

## How to talk to the author (summary of a private style reference, not published)
Short paragraphs and conversation, with your own ideas. At most one open question at the end. Tables, lists or code only when they clarify more than a paragraph. No interrogations or forms. What he says in the moment rules.
