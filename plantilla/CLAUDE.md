# {{PROYECTO}}

Creado el {{FECHA}}. Este archivo apunta, no copia: las reglas generales viven en el cerebro de Humanify.

## Qué es
<!-- 2-3 líneas: qué es, para quién y qué busca. -->

## Etapa actual
<!-- Una línea. El detalle vivo está en Bases/ESTADO.md. -->

## Cómo trabajar aquí
Rigen la constitución, las reglas y los contratos de `$HUMANIFY_HOME/cerebro/`. Aquí solo va lo propio de {{PROYECTO}}; lo que sirva a todos se marca `↑centro` en `Bases/APRENDIZAJES.md`.

## Dónde está cada cosa
- `Bases/ESTADO.md`: situación, pendientes y preguntas abiertas. Vivo.
- `Bases/APRENDIZAJES.md`: lo aprendido vigente, por tema y con nivel. Vivo.
- `Bases/SESIONES.md`: bitácora por sesión, solo si aporta. Se rota cada mes a `Bases/_archivo/`.
- `Bases/BASES.md`: decisiones con fecha (Decidido / Idea).

## Qué leer al abrir un chat
`CLAUDE.md`, `Bases/ESTADO.md` y `Bases/APRENDIZAJES.md`. `SESIONES.md` y `_archivo/` solo si hace falta. No releer el proyecto entero ni la bitácora completa.

## Ciclo de cierre
1. `Bases/ESTADO.md` al día (lo terminado se borra).
2. Lo validado, reescrito en su entrada de `APRENDIZAJES.md`.
3. Relato en `SESIONES.md` solo si aporta.
4. Siempre el comprobante: `$HUMANIFY_HOME/herramientas/anota.sh cierre <id-de-sesión> "<una línea>"`. El id sale en la primera línea del arranque: "Humanify activo · sesión <id>".
