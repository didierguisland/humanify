---
name: arranque
description: Ronda de arranque de Humanify para un proyecto sin estructura (o con estructura vieja) en Projects: inventario, propuesta de qué archivar y armado de CLAUDE.md + Bases/ desde la plantilla. Úsalo cuando el arranque diga "Proyecto sin estructura" y Didier acepte, o cuando pida "arranca este proyecto con Humanify".
---

# Ronda de arranque

Es la receta que se usó con Humanify el 2-nov-2026. Antes de esto no se trabaja en el proyecto, salvo que Didier diga otra cosa.

1. **Inventario** (Sonnet si la carpeta es grande; tú si es chica): qué hay, qué es viejo, qué pesa, qué se regenera (node_modules, builds, cachés), dónde hay git. Resumen de ≤15 líneas.
2. **Platica con Didier** qué es el proyecto, en qué etapa va y qué quiere de él. Pocas preguntas y abiertas, no un formulario.
3. **Propón qué archivar** a `_archivo/` (o `<proyecto>-v0/` si es una etapa entera). No se mueve ni se borra nada sin su sí. Lo regenerable se puede borrar solo con su sí.
4. **Arma la estructura** desde `$HUMANIFY_HOME/plantilla/`: copia `CLAUDE.md` y `Bases/`, cambia `{{PROYECTO}}` y `{{FECHA}}` y llena con lo platicado:
   - qué es y la etapa;
   - qué leer y qué no;
   - `ESTADO.md` con la situación y los pendientes;
   - `BASES.md` con lo decidido;
   - `APRENDIZAJES.md` con lo que ya se sepa.
   Corto: lo que no se va a usar esta semana no se escribe.
5. **Si el proyecto tenía un CLAUDE.md o relevos**, no los tires: lo vigente pasa a `ESTADO.md` y `APRENDIZAJES.md`, y los originales van a `Bases/_archivo/`.
6. **Primera foto de memoria:** `$HUMANIFY_HOME/herramientas/anota.sh foto <Proyecto>`.
7. **Cierra la sesión** con el ciclo normal (comprobante incluido) y una entrada en `SESIONES.md`: cómo fue la ronda y qué se archivó. Lo que la ronda enseñe sobre la ronda misma va con `↑centro`.
