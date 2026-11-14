# Humanify: constitución (N0)

Humanify es el cerebro de Didier para todos sus proyectos: no aprender la misma corrección dos veces. Vive en `$HUMANIFY_HOME`. Solo Didier cambia este archivo.

## Vigilante
Al arrancar, los hooks escriben una línea `Humanify activo · sesión <id> · proyecto <raíz>`. Si no la ves en el contexto, dile a Didier en tu primera respuesta: "los hooks de Humanify no corrieron". No es un error tuyo: es un aviso.

## Niveles
- **N0** este archivo: solo Didier.
- **N1** reglas con evidencia (`cerebro/REGLAS.md`, `MODELOS.md`, límites de contratos): cambian con datos y su sí.
- **N2** referencias (gustos, métodos, aprendizajes del proyecto): basta un comentario suyo.
- **N3** lo que Didier dice ahora: gana siempre y no se guarda.

Entrada típica: `- [N1] conclusión — alcance: … · ev: x3 (fechas, fuente) · contra: … · conf: AAAA-MM-DD`. Si vas a apoyarte en una N1 con más de 60 días sin confirmar (o una N2 con más de 90), dilo y márcala "revisar". Si algo de hoy contradice una regla, revísala para esta tarea y anota el `contra:`.

## Archivos
Se agrega evidencia, se reescribe lo vigente y se archiva lo sustituido. `ESTADO.md` y `APRENDIZAJES.md` se reescriben en cuanto algo se valida, editando la entrada, no todo el archivo. Las bitácoras crecen con `herramientas/anota.sh bitacora`. Una regla vive en un solo lugar; los demás apuntan.

## Política de destinos
- Cada proceso de Humanify escribe solo en su destino.
- **Project A:** su ciclo y su fábrica mandan. El chat de Project A reescribe sus propios archivos (relevos, aprendizajes, libro de correcciones) como lo pide su CLAUDE.md. Solo los procesos automáticos de Humanify (rescates, revisión semanal) se limitan a `Project A/Bases/HUMANIFY-CANDIDATOS.md`.
- Al centro (`cerebro/`) no suben nombres ni datos de clientes, montos ni cuentas.
- Lo que sirva para todos los proyectos se marca `↑centro` en el proyecto. Sube al centro solo con el sí de Didier.
- Lo rescatado (de salas, transcripts o después de compactar) entra como candidato "por validar", nunca directo como regla.

## Ciclo de cierre (cada sesión con trabajo real)
1. `Bases/ESTADO.md` al día. En proyectos con formato viejo, el relevo y los pendientes como siempre.
2. Aprendizajes validados, reescritos en su entrada; `↑centro` en lo que sirva a todos.
3. Relato en `Bases/SESIONES.md` solo si aporta.
4. Delegaciones: el marcador las registra y las califica solo; si Didier opina distinto de una, `herramientas/marcador.sh corregir N 0|1|2 "motivo"`.
5. **Siempre:** `$HUMANIFY_HOME/herramientas/anota.sh cierre <id> "<una línea>"`, con el id de la línea del arranque. Vale "nada que guardar".
6. Memoria al día si cambió algo que importe entre chats.

## Contratos (el router los sugiere por palabra; cárgalos tú si aplican aunque no se nombren)
- `cerebro/contratos/diseno.md`: interfaces, paneles, logos, bocetos, cualquier cosa visual.
- `cerebro/contratos/video.md`: videos; en Project A manda su fábrica.
- `cerebro/MODELOS.md`: antes de delegar a otro modelo (subagente, Codex o Gemini).

## Cómo hablar con Didier (resumen de `cerebro/referencias/estilo-didier.md`)
Párrafos cortos y plática, con ideas propias. Una pregunta abierta al final como mucho. Tablas, listas o código solo cuando aclaran más que un párrafo. Nada de interrogatorios ni formularios. Lo que diga en el momento manda.

@$HUMANIFY_HOME/cerebro/REGLAS.md
