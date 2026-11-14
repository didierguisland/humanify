---
name: humanify-estado
description: Revisar si Humanify está instalado y corriendo (hooks, línea @import, errores pendientes, sesiones sin cerrar). Úsalo cuando Didier pida el estado de Humanify, cuando el arranque avise de errores de hooks o cuando no aparezca la línea "Humanify activo".
---

# Estado de Humanify

1. Corre `$HUMANIFY_HOME/herramientas/estado.sh` y cuéntale a Didier el resultado en dos o tres líneas de plática.
2. Si hay errores de hooks, lee solo las líneas de error que muestra, explica la causa probable en una línea y propón el arreglo. No toques `~/.claude/settings.json` ni `~/.claude/CLAUDE.md` sin su sí.
3. Cuando Didier diga que ya están atendidos (o ya se arreglaron), corre `estado.sh --atendidos` para que el arranque deje de avisar.
4. Si hay sesiones sin cerrar, pregúntale si se rescatan o se descartan. Para rescatar, un Sonnet lee el transcript y escribe cada candidato mediante `herramientas/anota.sh candidato <destino.md> "<texto>" --base hecho --cambia "<qué cambia, al menos 4 palabras>" --fuente "rescate <id>" --transcript <ruta del transcript>`; el destino es APRENDIZAJES o, en Project A, `Bases/HUMANIFY-CANDIDATOS.md`. Usa `--base inferencia` cuando sea una inferencia. Si atribuye algo a Didier, usa `--base didier --cita "<sus palabras exactas>"`, siempre con `--transcript`. No pegues candidatos a mano; corrige los rechazados según el motivo. Al resolver cada sesión: `herramientas/anota.sh resuelto <id> rescatada|descartada`.
