# Router: keyword to contract

> English translation of `cerebro/ROUTER.md`. The keyword lists are kept in Spanish, as they are what the hook matches; the contract files they point to are not all included in this public copy.

`hooks/router.sh` reads it on every message from the author. Format: `words separated by | -> path (priority N)`; 1 is the highest. Words are compared whole, without accents or capitals. It is a suggestion: the orchestrator decides whether it applies.

```
diseno|diseño|boceto|bocetos|interfaz|panel|logo|pantalla|maqueta|mockup|ui|estilo visual -> contratos/diseno.md (prioridad 2)
video|videos|reel|reels|guion|speech|fabrica -> contratos/video.md (prioridad 2)
delega|delegar|subagente|subagentes|codex|sonnet|gemini|argon|audita|auditoria|auditar -> MODELOS.md (prioridad 3)
ensayo|ensayos|articulo|articulos|escribir|escribe|redactar|redacta|post|paper|columna|publicacion|prosa -> contratos/escritura.md (prioridad 2)
```
