# Model router (N1)

> English translation of `cerebro/MODELOS.md`. Sanitized ("Project A"). The Spanish original is what the system loads. Agent names: Opus and Sonnet (Claude), Sol and Terra (GPT models through Codex), Argon and Flash (Gemini-based).

You read this before delegating. What the author says in the moment ("this is simple, no subagents", "audit with Sonnet") always wins.

## Four questions
1. **How much judgment does it need?** A lot: Opus. If creative: Argon (Gemini), always grounded by Opus.
2. **How much must be read or done?** A lot and mechanical: Sonnet. Cheap brainstorming: Flash.
3. **How costly is a mistake?** Expensive (payments, client data, Project A in production): verify. Sol audits the detail and Terra checks against sources.
4. **Would the error be noticed?** If it would come out convincing and hidden (summarizing corrections, extracting rules, rescuing learnings), do not start cheap. If you start cheap, check a sample against the source.

## Escalation and effort
- Mechanical work whose error is noticed starts cheap and goes up if the result is poor. Judgment work starts good, because redoing costs more (Sonnet's speech came out flat).
- **low**: execute something very clear. **medium**: the orchestrator's and builder's day-to-day with a good eye. **high**: hard decisions that are expensive to get wrong.
- What is measured is total cost. If something cheap was redone, both rounds count.

## Builders (there is no fixed one)
Whoever does a piece best builds it: Opus, Sonnet, Sol, Terra or Codex. The author is told in one line.

## Codex (Sol, Terra)
The protocol lives in the author's personal global instructions (ask before delegating, read-only by default, verify what it cites). Outside a git repo: `codex exec --skip-git-repo-check ...`.

## Evidence
- [N1] For interface sketches, Opus medium directing Argon did better than Sonnet high alone (115 thousand tokens against 144 and 172 thousand, and fewer redos). - scope: interface sketches - ev: x2 (2 Nov rooms 1 and 2) - against: none - conf: 2026-11-02
- [N1] Architecture review: Sol high, in parallel with different angles, gave real findings that barely repeated. - scope: reviews of system design - ev: x1 (2 Nov, 4 runs) - against: none - conf: 2026-11-02

## Scoreboard (since 2 Nov; detail in `docs/scoreboard-and-rooms.md`)
Before delegating something that repeats, `herramientas/marcador.sh sugerir --tipo T [--riesgo caro]` (at most 10 lines). Label each subagent in its `description`: `[type]`, `[type!]` expensive risk, `[type#N]` redo of N, `[type+N]` support for N, `[type@N]` inside task N. Codex through `herramientas/codex.sh` and Argon through `herramientas/argon.sh` (`agy`; the old `gemini` no longer works), so they measure themselves. If the author says something came out worse or better: `marcador.sh corregir N 0|1|2 "reason"`.
- [N1] With fewer than 5 cases of a combination, do not change agent based on numbers; with 5 or more, look at them, but equivalence is only established by a paired blind test. - scope: choosing agent and effort - ev: x0, design reviewed by 3 Sol (2 Nov) - against: none - conf: 2026-11-02
- [N1] One in every 5 tasks of a normal-risk type tries a cheaper step; with expensive risk, never. - scope: same - ev: x0, design (2 Nov) - against: none - conf: 2026-11-02
- [N1] An isolated Opus low grades with `cerebro/contratos/calificador.md`; the judge is not changed without 8 Sol audits with good agreement. - scope: scoreboard - ev: x2 (2 Nov: a Sol STOP and a Haiku test, both judged well) - against: none - conf: 2026-11-02

## Blind A/B in animated video (route `video-animado`)
Summary of the original section (it lists three cases, each n = 1; nothing was changed because of the 5-case rule):
- Case 1 (3 Nov, microwave): fixed HTML frames, Sonnet medium against Opus medium, judged blind by Argon. Opus won on staging, hierarchy and continuity. Frames cost 0.65 USD (Opus) against 0.24 (Sonnet) at official prices; the whole Opus workshop 2.88 USD against 0.64 for Sonnet.
- Case 2 (3 Nov, wrinkled fingers): same A/B, Sonnet won (legible grooves at phone size, uncut framing); 0.69 USD to animate.
- Case 3 (3 Nov, 3D hair): A/B of the 3D style sketch (Three.js); Opus won clearly. Opus 1.38 USD in 6.6 min against Sonnet 0.33 in 1.6 min. Standing: Opus 2, Sonnet 1, nothing changed.
- From 3 Nov the A/B is done on the style sketch (one frame per scene). The author's note: once the style is fixed, animating becomes more mechanical and Sonnet might catch up; Opus tends to be more consistent in long animation code.
