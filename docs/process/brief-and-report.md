# Example: a brief and its report

> English translation of a real brief (Codex brief 6) and its report from the project history, lightly sanitized. It shows the method: the architect writes a precise brief, the builder works inside a fixed zone, and the brief names the conditions under which the builder must stop and report instead of improvising.

## Brief: per-room character limit

**Goal:** each room chooses its own maximum characters per message when it is opened. Today it is the constant `LARGO = 200` in `hooks/_sala.py`.

**Zone:** `hooks/_sala.py`, `instalacion/plantillas/sala-REGLAS.md`, `tests/test_sala.py`. Nothing else.

**Steps:**
1. `sala.sh abrir ... --largo N`: optional integer, default 200, valid from 80 to 2000; out of range or non-numeric means rejection with a clear reason.
2. Store it in the room's `estado.json`. Every length validation (`plan` and `decir`) uses the room's limit; an old room without the field uses 200 (a room is running right now in the old format: it must not break).
3. The rules template states the room's real limit (today it says a fixed "up to 200 characters"). Rejection messages and `USO` (usage text) too.
4. `estado <uuid>` shows the limit (for example `- largo 600`).
5. New tests in `tests/test_sala.py`: default 200; a room with 600 accepts 550 and rejects 650; plan uses the room's limit; invalid value rejected; old room without the field stays at 200.

**STOP and report ("DETENTE y repórtalo")** if it turns out you need to touch the scoreboard, the hooks of other events, or change the chat format.
**Do not touch** anything else; do not improve things in passing.

**Done when:** the whole suite passes (`/usr/bin/python3 -m unittest discover -s tests`, or however it runs today) and the new tests cover the 5 cases.

**Report:** at most 10 lines in `Bases/REPORTE-CODEX-6.md`: what changed, how many new tests and what they cover, and the suite result.

## Report (as delivered)

Implemented `--largo` from 80 to 2000, default 200, backward compatible with old rooms. Rules, validations and status show the corresponding limit.

Checks: 100 tests OK, including 7 new ones; review by the builder's own subagent found nothing.
Not tested manually in the active room.

## Why this format

- The brief fits on one screen; context lives in each project's `AGENTS.md`, not in the brief.
- The zone is a list of files, so a diff outside it is visible immediately.
- The "STOP and report" clause turns a risky guess (touching shared code) into a question for the architect. The grader treats a justified stop as a good outcome (`cerebro/contratos/calificador.md`).
- "Done when" names a verifiable check, which is also what the automatic grader looks for.
