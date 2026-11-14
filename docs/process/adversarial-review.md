# Adversarial review of an architecture (room log)

> Edited excerpt, translated from the Spanish room log `Bases/revision-sol/SALA.md` (2 Nov 2026), lightly sanitized. The closing "FIN: Opus" line and the heading wording were shortened. Model names: Sol = a GPT model run through Codex; Opus = the orchestrating Claude model.

**Setup.** Three reviewers (Sol-1 failure modes, Sol-2 rigidity, Sol-3 file lifecycle), all high effort and read-only, reviewed architecture v1. Opus decided. Every citation was checked against the v1 document and found accurate (including one where a handoff note asked for the architecture while the pending list already marked it done: "the latest handoff" had already lied once).

## Where they converged (two or three said it independently)
- A file's date does not prove that a session closed (Sol-1, Sol-3).
- "Only append at the end" does not solve two chats writing at once, nor keep knowledge current (Sol-1, Sol-3, and the author).
- Closing a session must not close other chats' rooms (Sol-1, Sol-3).
- Rescuing is not learning: a `GUARDAR:` line or a recollection after context compaction is a candidate, not a rule (Sol-1, Sol-3).
- Line limits must be guides, never cuts (Sol-1, Sol-2).
- A keyword router gets the word right and the intent wrong (Sol-1, Sol-2).

## Decisions (Opus)
| # | Finding | Decision |
|---|---|---|
| 1 | A log that only grows; knowledge stale all week (Sol-3 HIGH, author) | **Accepted.** New rule: add evidence, rewrite what is current at the moment it is validated, archive what is replaced. The weekly pass no longer maintains, it only reviews. |
| 2 | "The latest handoff" is not the project state (Sol-3 HIGH, Sol-1) | **Accepted, and simplifies.** Numbered handoffs plus the pending list merge into one live `ESTADO.md`; each session's entry goes to `SESIONES.md`. Fewer files, no numbering that collides. |
| 3 | Date as proof of closing; `SessionEnd` may not run (Sol-1 HIGH, MEDIUM) | **Accepted.** Startup issues a session id; closing writes its entry with that id ("nothing to save" is valid). A session left open and old with no entry gets a rescue *suggested*, once. |
| 4 | Concurrency: `>>` is not enough (Sol-1 HIGH, Sol-3) | **Accepted, cheaply.** One `anota.sh` with a short lock (lock directory, 5 s, self-releasing at 30 s). The lock is never held while a model thinks. Conflicting meaning: both are kept and flagged pending. |
| 5 | Rooms: foreign close, deleting between copy and delete (Sol-1 HIGH) | **Accepted.** A room has an owner (session id). `cerrar` turns `GUARDAR:` lines into candidates with their source and **archives** the room; repeating it does not duplicate. Session close only marks its own rooms. |
| 6 | Blind watchdog if the dispatcher fails (Sol-1 HIGH) | **Accepted, with a cheaper defense.** The constitution (loaded without hooks) says: if you do not see "Humanify activo" at startup, tell the author in your first line. Plus `/humanify-estado` to test the install. No extra launcher. |
| 7 | Project A: subfolders and destinations (Sol-1 HIGH) | **Accepted.** Canonical root = first level under `Proyectos/`. One destination policy: in Project A every Humanify process writes only to `HUMANIFY-CANDIDATOS.md`. Ambiguous root: warn and do not write. |
| 8 | Contracts that forbid exploring (Sol-2 HIGH) | **Accepted.** "Nobody explores" is removed. Directed reading when there is a concrete doubt. Contract split into *limits* (mandatory) and *default method* (can be set aside with a short reason). Report: at most 10 lines plus expandable detail; FEASIBILITY only if there is material doubt. |
| 9 | Cheap models fail without looking poor (Sol-2 HIGH) | **Accepted.** Fourth router question: would the error be noticed? If not, do not start cheap (or check a sample). Total cost is measured including redo. |
| 10 | Limits that hide the decisive part (Sol-2 HIGH, Sol-1) | **Accepted.** Limits are budgets. The hook never cuts: it delivers whole or gives a pointer. |
| 11 | Router: word is not intent (Sol-1, Sol-2 MEDIUM) | **Accepted.** The router *suggests*; the orchestrator confirms, combines or discards. A one-line contract index in the constitution loads contracts that were not named. It remembers file+version and resets on compaction. |
| 12 | x3 and expiry by calendar (Sol-2 MEDIUM) | **Accepted.** x3 is normal; decisive evidence may propose earlier. Each rule has scope and counterexamples. Contradiction: review it now for that task. Expiry means review on reuse, not invalidation by date. |
| 13 | Grounding kills ideas (Sol-2 MEDIUM) | **Accepted.** Separate verified limit from assumption; if an assumption decides the discard, run a minimal test first. Keep the intent and an alternative. |
| 14 | Seven proposals, archive by silence (Sol-2, Sol-3) | **Accepted.** Seven is the visible window; the rest wait, recoverable. Silence is not rejection: after 30 days they move to "deferred". |
| 15 | `APRENDIZAJES-CENTRO` duplicates (Sol-3 REDUNDANT) | **Accepted.** Removed: cross-project items go straight to `REGLAS.md` (N1) or `referencias/` (N2). |
| 16 | Version the project memory (Sol-3) | **Accepted.** Local git (no remote) in all of Humanify and in each project from the startup round. It gives "archive what is replaced" for free and removes the Codex-outside-git stumble. |
| 17 | Switching off is not reverting (Sol-1 MEDIUM) | **Accepted as a clarification.** Removing the line switches off new chats; open ones must restart. Undoing writes = git. |
| 18 | Slow or chatty hooks (Sol-1 MEDIUM) | **Accepted.** Explicit `timeout` in settings (5 s), logs rotated monthly. |
| 19 | Rescue after compaction invents (Sol-1 MEDIUM) | **Accepted.** Only what is supported and sourced; "nothing" is valid; never promoted directly to a rule. |
| - | Per-line `GUARDAR:` identifiers, atomic replacement of handoffs, per-ID review on every change, independent launcher | **Rejected.** Solved more cheaply by 2, 3, 5 and 6. Adding them would repeat v0. |

## Round 2: Sol-4 verified v1.1 (report 4)
It found 4 real gaps and all 4 were fixed in v1.1: (1) in Project A, closing forced writing into its files, so the close receipt now lives in the center (`registro/cierres`); (2) `git init` saved nothing by itself, so a "shadow" memory git holds only `.md` files, with a snapshot at each close, without touching anyone's code git; (3) rooms closed before everyone finished, so there is now a `FIN:` line and a candidate count before archiving; (4) a chat resumed with the same id hid its new work behind the old close, so the receipt counts only if it is later than the last resume. Also: a narrative in `SESIONES.md` only if it adds something. Sol-4's verdict: ready for phase 1 once (1), (2) and (4) are resolved; they are.

## Notes
- Three reviewers with different angles and a shared `_COMUN.md` gave findings that barely overlapped and cross-checked well. Reuse the format for architecture reviews.
- Codex outside a git repo needs `--skip-git-repo-check`; with local git in each project it is no longer needed.
