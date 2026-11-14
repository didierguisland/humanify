#!/usr/bin/python3
"""Builds examples/scoreboard-sample.db: ~10 fictional tasks and runs, created with the real schema
code in hooks/_marcador.py (conexion() creates tables, views and the price table). Nothing here is real data.
Usage: python3 examples/make_scoreboard_sample.py"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'examples' / 'scoreboard-sample.db'
for suffix in ('', '-wal', '-shm'):
    if Path(str(OUT) + suffix).exists():
        Path(str(OUT) + suffix).unlink()
os.environ['MARCADOR_DB'] = str(OUT)
os.environ['MARCADOR_COLA'] = str(ROOT / 'examples' / '.cola-ignored.jsonl')
sys.path.insert(0, str(ROOT / 'hooks'))
import _marcador as M  # noqa: E402

TASKS = [  # (type, description, risk, agent, effort, in, out, cache_read, cache_write, level, retry)
    ('construir', 'add --largo option to rooms', 'normal', 'claude-opus-5-5', 'medium', 900, 42000, 3100000, 210000, 2, False),
    ('revisar', 'review hook timeouts', 'normal', 'claude-sonnet-5-5', 'medium', 600, 18000, 1200000, 90000, 2, False),
    ('bug', 'fix lock release on crash', 'normal', 'claude-sonnet-5-5', 'low', 300, 9000, 400000, 30000, 1, False),
    ('creativo', 'three sketches for a status panel', 'normal', 'gemini-4-argon-medium', None, 52000, 9000, 0, 0, 1, False),
    ('arquitectura', 'review of design doc v1', 'caro', 'gpt-6.1-sol', 'high', 120000, 6000, 800000, 0, 2, False),
    ('resumir', 'summarize 12 reports', 'normal', 'claude-sonnet-5-5', 'low', 200, 5000, 150000, 10000, 2, False),
    ('construir', 'scoreboard export command', 'normal', 'claude-opus-5-5', 'medium', 700, 30000, 2200000, 150000, 1, True),
    ('investigar', 'compare lock strategies', 'normal', 'claude-opus-5-5', 'low', 500, 12000, 900000, 60000, None, False),
    ('datos', 'aggregate costs per agent', 'normal', 'claude-sonnet-5-5', 'medium', 400, 7000, 500000, 20000, 2, False),
    ('video', 'storyboard for a 30 s sketch', 'normal', 'gemini-3.8-flash-low', None, 30000, 2500, 0, 0, 0, False),
]
with M.conexion() as db:
    for i, (tipo, desc, riesgo, agente, esfuerzo, ti, to, cl, ce, nivel, retry) in enumerate(TASKS, 1):
        fecha = '2026-11-{:02d}T10:00:00+00:00'.format(i)
        db.execute('INSERT INTO tareas(id,uuid,fecha,proyecto,sesion,tipo,descripcion,riesgo) VALUES (?,?,?,?,?,?,?,?)',
                   (i, 'sample-task-{}'.format(i), fecha, 'Project A', 'sample-session', tipo, desc, riesgo))
        cuenta = 'google' if agente.startswith('gemini') else 'chatgpt' if agente.startswith('gpt') else 'claude'
        db.execute('INSERT INTO corridas(tarea,origen,id_externo,rol,vuelta,agente,esfuerzo,cuenta,tokens_entrada,tokens_salida,'
                   'tokens_cache_leida,tokens_cache_escrita,segundos,exacto,pruebas,fecha) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (i, 'sample', 'run-{}-a'.format(i), 'ejecucion', 1, agente, esfuerzo, cuenta, ti, to, cl, ce, 60.0 + i * 20, 1,
                    'paso' if tipo in ('construir', 'bug') else 'na', fecha))
        if retry:
            db.execute('INSERT INTO corridas(tarea,origen,id_externo,rol,vuelta,agente,esfuerzo,cuenta,tokens_entrada,tokens_salida,'
                       'tokens_cache_leida,tokens_cache_escrita,segundos,exacto,pruebas,fecha) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (i, 'sample', 'run-{}-b'.format(i), 'retrabajo', 2, agente, esfuerzo, cuenta, ti // 2, to // 2, cl // 2, ce // 2, 90.0, 1, 'paso', fecha))
        db.execute('INSERT INTO corridas(tarea,origen,id_externo,rol,vuelta,agente,esfuerzo,cuenta,tokens_entrada,tokens_salida,'
                   'tokens_cache_leida,tokens_cache_escrita,segundos,exacto,pruebas,fecha) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (i, 'sample', 'run-{}-g'.format(i), 'calificador', 1, 'claude-opus-5-5', 'low', 'claude', 150, 800, 20000, 3000, 12.0, 1, 'na',
                    '2026-11-{:02d}T10:05:00+00:00'.format(i)))
        db.execute('INSERT INTO veredictos(tarea,juez,nivel,motivo,huella_evidencia,fecha) VALUES (?,?,?,?,?,?)',
                   (i, 'calificador', nivel, 'sample verdict' if nivel is not None else 'not enough evidence', 'sample-{}'.format(i),
                    '2026-11-{:02d}T10:06:00+00:00'.format(i)))
    db.commit()
import sqlite3  # noqa: E402
_c = sqlite3.connect(str(OUT))
_c.execute('PRAGMA journal_mode=DELETE')
_c.close()
print('wrote', OUT)
