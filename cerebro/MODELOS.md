# Router de modelos (N1)

Lo lees antes de delegar. Lo que diga Didier en el momento ("esto es simple, sin subagentes", "audita con Sonnet") gana siempre.

## Cuatro preguntas
1. **¿Cuánto juicio pide?** Mucho → Opus. Si es creativo → Argon (Gemini), siempre aterrizado por Opus.
2. **¿Cuánto hay que leer o hacer?** Mucho y mecánico → Sonnet. La lluvia de ideas barata → Flash.
3. **¿Cuánto cuesta equivocarse?** Caro (pagos, datos de clientes, Project A en producción) → se verifica: Sol audita el detalle y Terra verifica con fuentes.
4. **¿Se notaría el error?** Si saldría convincente y escondido (resumir correcciones, sacar reglas, rescatar aprendizajes), no se empieza barato. Si se empieza barato, se contrasta una muestra contra la fuente.

## Escalado y esfuerzo
- Lo mecánico cuyo error se nota empieza barato y sube si sale pobre. Lo de juicio empieza bien, porque rehacer cuesta más (el speech de Sonnet salió plano).
- **low**: ejecutar algo clarísimo. **medium**: el día a día del orquestador y del constructor con buen ojo. **high**: decisiones difíciles y caras de errar.
- Lo que se mide es el costo total. Si algo barato se rehízo, cuentan las dos vueltas.

## Constructores (no hay uno fijo)
Construye quien lo haga mejor en cada pieza: Opus, Sonnet, Sol, Terra o Codex. Se le dice a Didier en una línea.

## Codex (Sol, Terra)
El protocolo vive en `~/.claude/CLAUDE.md` (preguntar antes de delegar, solo lectura por defecto, verificar lo que cita). Fuera de un repo de git: `codex exec --skip-git-repo-check …`.

## Evidencia
- [N1] Para bocetos de diseño, Opus medium dirigiendo a Argon rindió más que Sonnet high solo (115 mil contra 144 y 172 mil tokens, y menos rehechos). — alcance: bocetos de interfaz · ev: x2 (2-nov sala 1 y sala 2) · contra: — · conf: 2026-11-02
- [N1] Revisión de arquitectura: Sol high, en paralelo y con ángulos distintos, dio hallazgos reales casi sin repetirse. — alcance: revisiones de diseño de sistemas · ev: x1 (2-nov, 4 corridas) · contra: — · conf: 2026-11-02

## Marcador (desde el 2-nov; detalle en `Bases/DISENO-MARCADOR-SALA.md`)
Antes de delegar algo que se repite, `herramientas/marcador.sh sugerir --tipo T [--riesgo caro]` (≤10 líneas). Etiqueta cada subagente en su `description`: `[tipo]`, `[tipo!]` riesgo caro, `[tipo#N]` retrabajo de N, `[tipo+N]` apoyo a N, `[tipo@N]` dentro de la tarea N. Codex por `herramientas/codex.sh` y Argon por `herramientas/argon.sh` (`agy`; el viejo `gemini` ya no funciona), para que midan solos. Si Didier dice que algo salió peor o mejor: `marcador.sh corregir N 0|1|2 "motivo"`.
- [N1] Con menos de 5 casos de una combinación no se cambia de agente por números; con 5 o más se miran, pero la equivalencia solo la da una prueba a ciegas emparejada. — alcance: elegir agente y esfuerzo · ev: x0, diseño revisado por 3 Sol (2-nov) · contra: — · conf: 2026-11-02
- [N1] Una de cada 5 tareas de un tipo con riesgo normal prueba un escalón más barato; con riesgo caro, nunca. — alcance: ídem · ev: x0, diseño (2-nov) · contra: — · conf: 2026-11-02
- [N1] Califica Opus low aislado con `cerebro/contratos/calificador.md`; no se cambia de juez sin 8 auditorías de Sol con buen acuerdo. — alcance: marcador · ev: x2 (2-nov: DETENTE de Sol y prueba Haiku, ambos bien juzgados) · contra: — · conf: 2026-11-02

## A/B a ciegas en video animado (ruta `video-animado`)
- Evidencia x1 (3-nov, microondas, tarea 38): cuadros fijos HTML, Sonnet medium contra Opus medium, juez Argon sin saber cuál era cuál. Ganó Opus por puesta en escena, jerarquía y continuidad. Costo con tarifas oficiales: cuadros de Opus 0.65 USD contra 0.24 de Sonnet; el taller de Opus completo (cuadros, corrección, animación y corrección de render) 2.88 USD, el de Sonnet (storyboard, su corrección y cuadros) 0.64. Con un solo caso no se cambia nada (regla de 5 casos); seguir corriendo el A/B.
- Evidencia x2 (3-nov, dedos arrugados, tarea 44): mismo A/B, **ganó Sonnet** (surcos legibles a tamaño de teléfono, encuadre sin cortes); también animó (0.69 USD). Va 1-1: el elenco sigue con su a priori hasta 5 casos. La tabla `etapas` del marcador ya guarda cada caso (ganador y perdedor) para que esto se decida solo.
- Evidencia x3 (3-nov, pelo 3D, tarea 46): A/B del **boceto de estilo en 3D (Three.js)**, papel=estilo. **Ganó Opus** claro (hebra translúcida, escamas, cepillo creíble; Sonnet hizo «vasos apilados»). Costo: Opus 1.38 USD en 6.6 min contra Sonnet 0.33 en 1.6 min. En 3D el boceto ya es casi todo el código (la animación lo reutilizó y Opus animó sin perder nada, 1.38 USD, cero vueltas). Va Opus 2, Sonnet 1; sigue sin cambiarse nada (regla de 5).
- Desde el 3-nov (v3.1) el A/B se hace en el **boceto de estilo** (un cuadro por escena), no con todos los cuadros: los casos nuevos van con `papel=estilo`. Animar: Opus a priori; retocar tras el render: Sonnet a priori (práctica de Didier, sin evidencia; los decide el marcador por papel con 5 casos). Opinión de Claude: con el estilo fijado, animar se vuelve más mecánico y Sonnet podría alcanzar a Opus; Opus suele ser más consistente en código de animación largo.
