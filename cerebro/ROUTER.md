# Router: palabra → contrato

Lo lee `hooks/router.sh` en cada mensaje de Didier. Formato: `palabras separadas por | -> ruta (prioridad N)`; 1 es la más alta. Las palabras se comparan completas, sin acentos ni mayúsculas. Es una sugerencia: el orquestador decide si aplica.

diseno|diseño|boceto|bocetos|interfaz|panel|logo|pantalla|maqueta|mockup|ui|estilo visual -> contratos/diseno.md (prioridad 2)
video|videos|reel|reels|guion|speech|fabrica -> contratos/video.md (prioridad 2)
delega|delegar|subagente|subagentes|codex|sonnet|gemini|argon|audita|auditoria|auditar -> MODELOS.md (prioridad 3)
ensayo|ensayos|articulo|articulos|escribir|escribe|redactar|redacta|post|paper|columna|publicacion|prosa -> contratos/escritura.md (prioridad 2)
