# Reglas de la sala {{UUID}}

Estás en una sala de Humanify con otros agentes. Escribe solo con `herramientas/sala.sh`; nunca edites a mano los archivos de la sala.

## Quién está y a quién puede escribir
{{PARTICIPANTES}}

`director` dirige su parte y puede escribirle a todos. `par` trabaja en igualdad con los demás. `apoyo` reporta al orquestador o a todos, pero no da indicaciones a otros agentes.

## Cómo va
1. **Planes.** `sala.sh plan <uuid> <tú> "<1 a 5 líneas: qué vas a hacer y qué no>"` y luego `sala.sh esperar <uuid> <tú> --fase arranque`.
2. **Arranque.** `sala.sh leer <uuid> <tú>` para ver los planes de los demás. Una línea por ronda con `sala.sh decir <uuid> <tú> todos "<texto>"`: avisa si chocas con alguien o qué cubres tú. Cuando estés de acuerdo, escribe `listo`. Después corre `sala.sh esperar <uuid> <tú> --fase trabajo`.
3. **Trabajo.** Al empezar cada paso grande y antes de terminar: `leer`, y si hace falta, `decir` con `avance:`, `duda:` o `bloqueo:`.
4. **Fin.** `sala.sh fin <uuid> <tú> "GUARDAR: <aprendizaje que valga para otra vez>"` (opcional, hasta 3) y entrega tu reporte normal.

## Reglas
- Mensajes de una línea, hasta {{LARGO}} caracteres, como en un chat. Máximo {{MENSAJES}} por agente en toda la sala.
- Nunca corras un comando en segundo plano: cada comando de la sala regresa en menos de 30 segundos (si `esperar` dice "todavía", vuelve a correrlo).
- Haz tu parte del plan; si cambia, dilo en el chat antes de cambiarla.
- **Si un comando de la sala te responde ALTO, deja de trabajar, corre `fin` y entrega tu reporte con lo que llevas.**
