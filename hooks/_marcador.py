#!/usr/bin/python3
"""Marcador de Humanify. Biblioteca estándar; los productores solo escriben JSONL."""
import argparse
import contextlib
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import random
import re
import signal
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from _humanify import append, identifier, lock

ROOT = Path(os.environ.get('HUMANIFY_DIR', str(Path(__file__).resolve().parent.parent)))
DB = Path(os.environ.get('MARCADOR_DB', str(ROOT / 'registro/marcador.db')))
COLA = Path(os.environ.get('MARCADOR_COLA', str(ROOT / 'registro/cola-marcador.jsonl')))
REG = ROOT / 'registro'
TIPOS = {
    'diseno': 'Forma e interacción de una interfaz; excluye implementación y arquitectura.',
    'construir': 'Implementar una solución definida; excluye reparar fallos existentes.',
    'bug': 'Diagnosticar y reparar un fallo existente; excluye funciones nuevas.',
    'revisar': 'Evaluar un trabajo existente; excluye construirlo o definir su arquitectura.',
    'arquitectura': 'Definir componentes y sus contratos; excluye diseño visual e implementación.',
    'investigar': 'Buscar y contrastar fuentes; excluye analizar conjuntos de datos.',
    'resumir': 'Condensar contenido existente; excluye redactar contenido nuevo.',
    'redactar': 'Crear texto nuevo; excluye resúmenes y conceptos creativos.',
    'datos': 'Analizar o transformar datos estructurados; excluye investigación documental.',
    'creativo': 'Proponer conceptos y narrativas; excluye producción final de video.',
    'video': 'Producir o editar material audiovisual; excluye ideación sin producción.',
    'sin-tipo': 'Trabajo aún sin clasificar; excluido de recomendaciones y eficiencia.',
}
TOKENS = ('tokens_entrada', 'tokens_salida', 'tokens_razonamiento',
          'tokens_cache_leida', 'tokens_cache_escrita')
ROLES = ('ejecucion', 'apoyo', 'retrabajo', 'calificador', 'vigilante', 'auditor')
TRABAJOS = ('idear', 'decidir', 'especificar', 'construir', 'verificar', 'juzgar')
# USD por millón de tokens (tarifas oficiales; fuente: skill claude-api, 3-nov-2026). La caché escrita es
# 1.25 × entrada. Gemini y Codex van por cuota de suscripción: no tienen fila y su costo en USD queda NULL.
PRECIOS = [
    ('opus', 4.0, 20.0, 0.20, 5.0), ('claude-opus-5-5', 4.0, 20.0, 0.20, 5.0),
    ('sonnet', 2.0, 10.0, 0.20, 2.5), ('claude-sonnet-5-5', 2.0, 10.0, 0.20, 2.5),
    ('haiku', 0.10, 0.50, 0.01, 0.125), ('claude-haiku-5-5', 0.10, 0.50, 0.01, 0.125),
]
PRECIOS_DESDE = '2026-11-01'
VERSION_ESQUEMA = 3


def ahora():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='microseconds')


SCHEMA = """
CREATE TABLE IF NOT EXISTS tipos(clave TEXT PRIMARY KEY, descripcion TEXT);
CREATE TABLE IF NOT EXISTS tareas(
 id INTEGER PRIMARY KEY, uuid TEXT UNIQUE, fecha TEXT, proyecto TEXT, sesion TEXT,
 tipo TEXT REFERENCES tipos(clave), descripcion TEXT CHECK(length(descripcion)<=120),
 riesgo TEXT CHECK(riesgo IN ('normal','caro')) DEFAULT 'normal', sala TEXT,
 compartida INTEGER DEFAULT 0, criterio TEXT, evidencia TEXT, tipo_sugerido TEXT);
CREATE TABLE IF NOT EXISTS corridas(
 id INTEGER PRIMARY KEY, tarea INTEGER REFERENCES tareas(id), origen TEXT, id_externo TEXT,
 rol TEXT CHECK(rol IN ('ejecucion','apoyo','retrabajo','calificador','vigilante','auditor')),
 vuelta INTEGER, agente TEXT, esfuerzo TEXT, cuenta TEXT,
 tokens_entrada INTEGER, tokens_salida INTEGER, tokens_razonamiento INTEGER,
 tokens_cache_leida INTEGER, tokens_cache_escrita INTEGER, segundos REAL, exacto INTEGER,
 pruebas TEXT CHECK(pruebas IN ('paso','fallo','na')) DEFAULT 'na', fecha TEXT,
 etapa TEXT, trabajo TEXT, turnos INTEGER,
 UNIQUE(origen,id_externo));
CREATE TABLE IF NOT EXISTS veredictos(
 id INTEGER PRIMARY KEY, tarea INTEGER REFERENCES tareas(id),
 juez TEXT CHECK(juez IN ('calificador','didier','auditor')),
 nivel INTEGER CHECK(nivel IN (0,1,2) OR nivel IS NULL), motivo TEXT,
 huella_evidencia TEXT, ciego INTEGER DEFAULT 0, fecha TEXT);
CREATE TABLE IF NOT EXISTS pesos(agente TEXT, esfuerzo TEXT, peso REAL, desde TEXT,
 PRIMARY KEY(agente,esfuerzo,desde));
CREATE TABLE IF NOT EXISTS salas(uuid TEXT PRIMARY KEY, tarea INTEGER REFERENCES tareas(id),
 agentes TEXT, mensajes INTEGER, rechazos INTEGER, banderas INTEGER, altos INTEGER,
 llamadas_vigilante INTEGER, forzada INTEGER DEFAULT 0, cerrada_en TEXT);
CREATE TABLE IF NOT EXISTS trabajos(id INTEGER PRIMARY KEY,
 clase TEXT CHECK(clase IN ('calificar','auditar')), tarea INTEGER,
 estado TEXT CHECK(estado IN ('pendiente','lanzado','hecho')), intentos INTEGER DEFAULT 0,
 fecha TEXT, UNIQUE(clase,tarea,fecha));
CREATE TABLE IF NOT EXISTS alertas(id INTEGER PRIMARY KEY, corrida INTEGER REFERENCES corridas(id),
 tarea INTEGER REFERENCES tareas(id), motivo TEXT, fecha TEXT, atendida INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS precios(modelo TEXT, desde TEXT, entrada REAL, salida REAL,
 cache_leida REAL, cache_escrita REAL, PRIMARY KEY(modelo,desde));
-- Una fila por etapa de una ruta: con esto el elenco decide quién hace cada tipo de trabajo.
CREATE TABLE IF NOT EXISTS etapas(id INTEGER PRIMARY KEY, tarea INTEGER REFERENCES tareas(id), ruta TEXT,
 oficio TEXT, etapa TEXT, trabajo TEXT, papel TEXT, agente TEXT, modelo TEXT, esfuerzo TEXT, motivo TEXT,
 vueltas INTEGER DEFAULT 0, ab_gano INTEGER, aprobada INTEGER, fecha TEXT, UNIQUE(tarea,etapa));
-- Estado interno: la marca y cada reintento se confirman junto con los datos.
CREATE TABLE IF NOT EXISTS _colas(nombre TEXT PRIMARY KEY, marca INTEGER);
CREATE TABLE IF NOT EXISTS _fallos(nombre TEXT, posicion INTEGER, linea TEXT,
 PRIMARY KEY(nombre,posicion));
CREATE TABLE IF NOT EXISTS _recibos(huella TEXT PRIMARY KEY);
CREATE VIEW IF NOT EXISTS _costos AS
 SELECT c.*, (coalesce(tokens_entrada,0)+coalesce(tokens_salida,0)*5+
 coalesce(tokens_cache_leida,0)*0.1+coalesce(tokens_cache_escrita,0)*1.25)*
 coalesce((SELECT peso FROM pesos p WHERE p.agente=c.agente AND p.esfuerzo=c.esfuerzo
 AND date(p.desde)<=date(c.fecha) ORDER BY p.desde DESC LIMIT 1),1.0) AS costo
 FROM corridas c;
CREATE VIEW IF NOT EXISTS _costos_usd AS
 SELECT c.*, CASE WHEN p.modelo IS NULL THEN NULL ELSE
 (coalesce(c.tokens_entrada,0)*p.entrada+coalesce(c.tokens_salida,0)*p.salida+
 coalesce(c.tokens_cache_leida,0)*p.cache_leida+coalesce(c.tokens_cache_escrita,0)*p.cache_escrita)/1e6 END AS usd,
 (p.modelo IS NULL AND c.cuenta IN ('google','chatgpt')) AS cuota
 FROM corridas c LEFT JOIN precios p ON p.rowid=(SELECT pp.rowid FROM precios pp WHERE pp.modelo=c.agente
 AND date(pp.desde)<=date(c.fecha) ORDER BY pp.desde DESC LIMIT 1);
CREATE VIEW IF NOT EXISTS etapas_usd AS
 SELECT e.*, coalesce((SELECT sum(u.usd) FROM _costos_usd u WHERE u.tarea=e.tarea AND u.etapa=e.etapa),0) AS usd,
 (SELECT sum(u.turnos) FROM _costos_usd u WHERE u.tarea=e.tarea AND u.etapa=e.etapa) AS turnos
 FROM etapas e;
CREATE VIEW IF NOT EXISTS _resultados AS
 SELECT t.*, v.juez, v.nivel AS nivel_original,
 CASE WHEN v.nivel=2 AND EXISTS(SELECT 1 FROM corridas c WHERE c.tarea=t.id
 AND c.rol='retrabajo') THEN 1 ELSE v.nivel END AS calidad,
 EXISTS(SELECT 1 FROM corridas c WHERE c.tarea=t.id AND c.rol='retrabajo') AS rehecha,
 coalesce((SELECT sum(costo) FROM _costos c WHERE c.tarea=t.id
 AND c.rol IN ('ejecucion','apoyo','retrabajo')),0) AS costo,
 coalesce((SELECT sum(costo) FROM _costos c WHERE c.tarea=t.id
 AND c.rol IN ('calificador','vigilante','auditor')),0) AS costo_medir
 FROM tareas t LEFT JOIN veredictos v ON v.id=(SELECT vv.id FROM veredictos vv
 WHERE vv.tarea=t.id AND vv.fecha>=coalesce(
 (SELECT max(c.fecha) FROM corridas c WHERE c.tarea=t.id
 AND c.rol IN ('ejecucion','retrabajo')),'')
 AND vv.huella_evidencia IS (SELECT reciente.huella_evidencia FROM veredictos reciente
 WHERE reciente.tarea=t.id ORDER BY reciente.fecha DESC,reciente.id DESC LIMIT 1)
 ORDER BY CASE vv.juez WHEN 'didier' THEN 3 WHEN 'auditor' THEN 2 ELSE 1 END DESC,
 vv.fecha DESC, vv.id DESC LIMIT 1);
CREATE VIEW IF NOT EXISTS eficiencia AS
 SELECT r.tipo,c.agente,c.esfuerzo,count(*) AS casos,count(r.calidad) AS evaluables,
 sum(r.calidad IS NULL) AS no_evaluables,
 100.0*sum(r.calidad=2)/nullif(count(r.calidad),0) AS pct_aceptadas,
 100.0*sum(r.calidad=0 OR r.rehecha)/count(*) AS pct_rehechas,
 avg(r.costo) AS costo_medio,
 sum(r.costo)/nullif(sum(r.calidad=2),0) AS costo_por_aceptada
 FROM _resultados r JOIN corridas c ON c.id=(SELECT cc.id FROM corridas cc
 WHERE cc.tarea=r.id AND cc.rol='ejecucion' ORDER BY cc.id LIMIT 1)
 WHERE r.compartida=0 AND r.tipo!='sin-tipo' GROUP BY r.tipo,c.agente,c.esfuerzo;
CREATE VIEW IF NOT EXISTS salas_metricas AS
 SELECT tipo,(sala IS NOT NULL) AS con_sala,count(*) AS casos,count(calidad) AS evaluables,
 sum(calidad IS NULL) AS no_evaluables,
 100.0*sum(calidad=2)/nullif(count(calidad),0) AS pct_aceptadas,
 100.0*sum(calidad=0 OR rehecha)/count(*) AS pct_rehechas,avg(costo) AS costo_medio,
 sum(costo)/nullif(sum(calidad=2),0) AS costo_por_aceptada
 FROM _resultados GROUP BY tipo,con_sala;
CREATE VIEW IF NOT EXISTS sin_veredicto AS
 SELECT * FROM _resultados WHERE juez IS NULL AND julianday(fecha)<julianday('now','-30 minutes');
CREATE VIEW IF NOT EXISTS calibracion AS
 SELECT t.id AS tarea,
 (SELECT nivel FROM veredictos WHERE tarea=t.id AND juez='calificador' ORDER BY fecha DESC,id DESC LIMIT 1) AS calificador,
 (SELECT nivel FROM veredictos WHERE tarea=t.id AND juez='auditor' ORDER BY fecha DESC,id DESC LIMIT 1) AS auditor,
 (SELECT nivel FROM veredictos WHERE tarea=t.id AND juez='didier' ORDER BY fecha DESC,id DESC LIMIT 1) AS didier,
 (SELECT count(nivel)=count(*) AND count(DISTINCT nivel)=1 FROM veredictos v WHERE v.id IN
 (SELECT ultimo.id FROM veredictos ultimo WHERE ultimo.tarea=t.id AND ultimo.id=
 (SELECT vv.id FROM veredictos vv WHERE vv.tarea=t.id AND vv.juez=ultimo.juez
 ORDER BY vv.fecha DESC,vv.id DESC LIMIT 1))) AS acuerdo
 FROM tareas t WHERE (SELECT count(DISTINCT juez) FROM veredictos WHERE tarea=t.id)>1;
"""


@contextlib.contextmanager
def conexion():
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(DB), timeout=2, isolation_level=None)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA busy_timeout=2000')
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA foreign_keys=ON')
        migrar(db)
        # executescript contiene su BEGIN: también el primer uso queda serializado.
        db.executescript('BEGIN IMMEDIATE;\n' + SCHEMA)
        db.executemany('INSERT OR IGNORE INTO tipos VALUES (?,?)', TIPOS.items())
        db.executemany('INSERT OR IGNORE INTO precios VALUES (?,?,?,?,?,?)',
                       [(m, PRECIOS_DESDE, e, s, cl, ce) for m, e, s, cl, ce in PRECIOS])
        if not db.execute('SELECT 1 FROM pesos LIMIT 1').fetchone():
            db.executemany('INSERT INTO pesos VALUES (?,?,1.0,?)',
                           [(a, e, dt.date.today().isoformat()) for a in
                            ('opus', 'sonnet', 'haiku', 'sol', 'terra', 'codex', 'argon', 'flash')
                            for e in ('low', 'medium', 'high', 'xhigh')])
        db.commit()
        yield db
    finally:
        if db.in_transaction:
            db.rollback()
        db.close()


def migrar(db):
    """Esquema 2: etapa, trabajo y turnos en cada corrida. Antes de alterar una base con datos se respalda
    entera; las columnas nuevas solo se agregan (nada se borra)."""
    version = db.execute('PRAGMA user_version').fetchone()[0]
    if version >= VERSION_ESQUEMA:
        return
    if version == 2:
        # Esquema 3: el papel (animar, retocar…) afina el tipo de trabajo en el elenco. Solo se agrega una columna.
        if {r[1] for r in db.execute('PRAGMA table_info(etapas)')} and 'papel' not in {
                r[1] for r in db.execute('PRAGMA table_info(etapas)')}:
            db.execute('BEGIN IMMEDIATE')
            db.execute('ALTER TABLE etapas ADD COLUMN papel TEXT')
            db.execute('PRAGMA user_version={}'.format(VERSION_ESQUEMA))
            db.commit()
        else:
            db.execute('PRAGMA user_version={}'.format(VERSION_ESQUEMA))
        return
    columnas = {r[1] for r in db.execute('PRAGMA table_info(corridas)')}
    if columnas:
        faltan = [c for c in ('etapa', 'trabajo', 'turnos') if c not in columnas]
        if faltan and db.execute('SELECT 1 FROM corridas LIMIT 1').fetchone():
            respaldo = REG / 'respaldos' / 'marcador-v1-{}.db'.format(dt.datetime.now().strftime('%Y%m%d-%H%M%S'))
            respaldo.parent.mkdir(parents=True, exist_ok=True)
            destino = sqlite3.connect(str(respaldo))
            try:
                db.backup(destino)
            finally:
                destino.close()
        db.execute('BEGIN IMMEDIATE')
        for columna in faltan:
            db.execute('ALTER TABLE corridas ADD COLUMN {} {}'.format(columna, 'INTEGER' if columna == 'turnos' else 'TEXT'))
        db.execute('PRAGMA user_version={}'.format(VERSION_ESQUEMA))
        db.commit()
    else:
        db.execute('PRAGMA user_version={}'.format(VERSION_ESQUEMA))


def insertar(db, tabla, campos):
    permitidos = {r['name'] for r in db.execute('PRAGMA table_info(' + tabla + ')')}
    if set(campos) - permitidos:
        raise ValueError('campos desconocidos en ' + tabla)
    nombres = list(campos)
    return db.execute('INSERT INTO ' + tabla + '(' + ','.join(nombres) + ') VALUES (' +
                      ','.join('?' for _ in nombres) + ')', [campos[k] for k in nombres])


def tarea_id(db, referencia):
    r = db.execute('SELECT id FROM tareas WHERE uuid=? OR id=?',
                   (str(referencia), referencia)).fetchone()
    if r is None:
        raise ValueError('tarea desconocida: ' + str(referencia))
    return r[0]


def aplicar(db, evento):
    item = dict(evento)
    clase = item.pop('evento')
    if clase != 'sala':
        fecha = dt.datetime.fromisoformat((item.get('fecha') or ahora()).replace('Z', '+00:00'))
        if fecha.tzinfo is None:
            fecha = fecha.replace(tzinfo=dt.timezone.utc)
        item['fecha'] = fecha.astimezone(dt.timezone.utc).isoformat(timespec='microseconds')
    if clase == 'tarea':
        item.setdefault('uuid', str(uuid.uuid4()))
        existente = db.execute('SELECT id FROM tareas WHERE uuid=?', (item['uuid'],)).fetchone()
        if existente:
            return existente[0]
        item.setdefault('tipo', 'sin-tipo')
        item.setdefault('fecha', ahora())
        item.setdefault('descripcion', '')
        return insertar(db, 'tareas', item).lastrowid
    if clase == 'sala':
        item.pop('id_externo', None)  # contrato de la cola; PK real: uuid
        item['tarea'] = tarea_id(db, item['tarea'])
        if db.execute('SELECT 1 FROM salas WHERE uuid=?', (item['uuid'],)).fetchone():
            return
        if isinstance(item.get('agentes'), (list, dict)):
            item['agentes'] = json.dumps(item['agentes'], ensure_ascii=False)
        item.setdefault('cerrada_en', ahora())
        insertar(db, 'salas', item)
        db.execute('UPDATE tareas SET sala=? WHERE id=?', (item['uuid'], item['tarea']))
        db.execute("INSERT OR IGNORE INTO trabajos(clase,tarea,estado,fecha) VALUES ('calificar',?,'pendiente',?)",
                   (item['tarea'], item['cerrada_en']))
        return
    if clase == 'etapa':
        item['tarea'] = tarea_id(db, item['tarea'])
        item.pop('id_externo', None)
        if item.get('trabajo') not in TRABAJOS:
            raise ValueError('trabajo desconocido: {}'.format(item.get('trabajo')))
        db.execute('DELETE FROM etapas WHERE tarea=? AND etapa=?', (item['tarea'], item['etapa']))
        insertar(db, 'etapas', item)
        return
    if clase not in ('corrida', 'veredicto'):
        raise ValueError('evento desconocido')
    item['tarea'] = tarea_id(db, item['tarea'])
    item.setdefault('fecha', ahora())
    if clase == 'veredicto':
        if item.get('nivel') == 'nulo':
            item['nivel'] = None
        insertar(db, 'veredictos', item)
        db.execute("UPDATE trabajos SET estado='hecho' WHERE tarea=? AND clase=? AND fecha<=?",
                   (item['tarea'], 'auditar' if item['juez'] == 'auditor' else 'calificar', item['fecha']))
        return
    if not item.get('origen') or not item.get('id_externo'):
        raise ValueError('corrida sin origen/id_externo')
    if db.execute('SELECT 1 FROM corridas WHERE origen=? AND id_externo=?',
                  (item['origen'], item['id_externo'])).fetchone():
        return
    vuelta = db.execute('SELECT coalesce(max(vuelta),1) FROM corridas WHERE tarea=?', (item['tarea'],)).fetchone()[0]
    item['vuelta'] = vuelta + (item['rol'] == 'retrabajo')
    for campo in TOKENS:
        item.setdefault(campo, None)
        if item[campo] is not None and (not isinstance(item[campo], int) or item[campo] < 0):
            raise ValueError('tokens inválidos')
    item['exacto'] = int(bool(item.get('exacto', 1)) and all(item[k] is not None for k in
                         ('tokens_entrada', 'tokens_salida', 'tokens_cache_leida', 'tokens_cache_escrita')))
    corrida = insertar(db, 'corridas', item).lastrowid
    alertar_gasto(db, corrida, item)
    if item['rol'] == 'apoyo' or (item['rol'] in ('ejecucion', 'retrabajo') and
            db.execute("SELECT count(DISTINCT coalesce(agente,'?')) FROM corridas WHERE tarea=? AND rol IN ('ejecucion','retrabajo')", (item['tarea'],)).fetchone()[0] > 1):
        db.execute('UPDATE tareas SET compartida=1 WHERE id=?', (item['tarea'],))
    if item['rol'] in ('ejecucion', 'retrabajo') and db.execute(
            'SELECT sala IS NULL FROM tareas WHERE id=?', (item['tarea'],)).fetchone()[0]:
        db.execute("INSERT OR IGNORE INTO trabajos(clase,tarea,estado,fecha) VALUES ('calificar',?,'pendiente',?)",
                   (item['tarea'], item['fecha']))


def alertar_gasto(db, corrida, item):
    total_sql = ('coalesce(tokens_entrada,0)+coalesce(tokens_cache_leida,0)+'
                 'coalesce(tokens_cache_escrita,0)+coalesce(tokens_salida,0)')
    total = sum(item[k] or 0 for k in
                ('tokens_entrada', 'tokens_cache_leida', 'tokens_cache_escrita', 'tokens_salida'))
    motivo = None
    if item['rol'] in ('calificador', 'vigilante', 'auditor') and total > 8000:
        motivo = '{}: {} tokens (>8000)'.format(item['rol'], total)
    elif item['rol'] == 'ejecucion':
        previos = [r[0] for r in db.execute(
            'SELECT ' + total_sql + ' FROM corridas c JOIN tareas t ON t.id=c.tarea '
            "WHERE c.id<? AND c.rol='ejecucion' AND c.agente=? "
            'AND t.tipo=(SELECT tipo FROM tareas WHERE id=?)',
            (corrida, item.get('agente'), item['tarea']))]
        if len(previos) >= 5:
            mediana = statistics.median(previos)
            if total > 3 * mediana:
                motivo = '{}: {} tokens (>3 × mediana {:g})'.format(item.get('agente'), total, mediana)
    if motivo:
        insertar(db, 'alertas', dict(corrida=corrida, tarea=item['tarea'], motivo=motivo, fecha=item['fecha']))


def encolar(evento):
    if not isinstance(evento, dict) or evento.get('evento') not in ('tarea', 'corrida', 'veredicto', 'sala', 'etapa'):
        raise ValueError('se espera un evento tarea, corrida, veredicto, sala o etapa')
    if evento['evento'] == 'corrida':
        # Dentro de una ruta, el conductor marca la etapa y el tipo de trabajo; cada corrida hereda la marca.
        for campo, variable in (('etapa', 'MARCADOR_ETAPA'), ('trabajo', 'MARCADOR_TRABAJO')):
            if not evento.get(campo) and os.environ.get(variable):
                evento = dict(evento, **{campo: os.environ[variable]})
    append(COLA, json.dumps(evento, ensure_ascii=False, separators=(',', ':')))


def segundo_plano():
    subprocess.Popen(['/usr/bin/python3', str(Path(__file__).resolve()), 'procesar'],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


def lanzar_trabajos(db):
    guion = ROOT / 'herramientas/califica.sh'
    if not guion.is_file():
        return
    filas = db.execute("SELECT * FROM trabajos WHERE clase='calificar' AND intentos<3 AND "
                       "(estado='pendiente' OR (estado='lanzado' AND julianday(fecha)<julianday('now','-15 minutes'))) ORDER BY id").fetchall()
    for fila in filas:
        db.execute('BEGIN IMMEDIATE')
        db.execute("UPDATE trabajos SET estado='lanzado',intentos=intentos+1,fecha=? WHERE id=?", (ahora(), fila['id']))
        db.commit()
        try:
            subprocess.Popen(['/bin/bash', str(guion), str(fila['tarea'])],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE trabajos SET estado='pendiente' WHERE id=?", (fila['id'],))
            db.commit()


def procesar():
    DB.parent.mkdir(parents=True, exist_ok=True)
    with candado_procesador():
        with conexion() as db:
            nombre = str(COLA.resolve())
            db.execute('BEGIN IMMEDIATE')
            fila = db.execute('SELECT marca FROM _colas WHERE nombre=?', (nombre,)).fetchone()
            marca = fila[0] if fila else 0
            lineas = list(db.execute('SELECT posicion,linea FROM _fallos WHERE nombre=? ORDER BY posicion', (nombre,)))
            if COLA.exists():
                with COLA.open('rb') as stream:
                    stream.seek(marca)
                    while True:
                        posicion = stream.tell()
                        linea = stream.readline()
                        if not linea or not linea.endswith(b'\n'):
                            break
                        marca = stream.tell()
                        lineas.append((posicion, linea.decode('utf-8', errors='replace')))
            errores = 0
            for posicion, linea in lineas:
                db.execute('SAVEPOINT evento')
                try:
                    huella = hashlib.sha256(linea.strip().encode()).hexdigest()
                    if not db.execute('SELECT 1 FROM _recibos WHERE huella=?', (huella,)).fetchone():
                        aplicar(db, json.loads(linea))
                        db.execute('INSERT INTO _recibos VALUES (?)', (huella,))
                    db.execute('DELETE FROM _fallos WHERE nombre=? AND posicion=?', (nombre, posicion))
                except (ValueError, KeyError, TypeError, sqlite3.IntegrityError):
                    db.execute('ROLLBACK TO evento')
                    db.execute('INSERT OR REPLACE INTO _fallos VALUES (?,?,?)', (nombre, posicion, linea))
                    errores += 1
                finally:
                    db.execute('RELEASE evento')
            db.execute('INSERT OR REPLACE INTO _colas VALUES (?,?)', (nombre, marca))
            db.commit()
            lanzar_trabajos(db)
    return 'Cola procesada; pendientes de reintento: ' + str(errores)


@contextlib.contextmanager
def candado_procesador():
    try:
        with lock(Path(str(DB) + '.procesar')):
            yield
    except ValueError as error:
        if 'no se pudo tomar el candado' in str(error):
            raise TimeoutError(str(error)) from error
        raise


def etiqueta(descripcion):
    m = re.search(r'\[([a-z-]+)(!|[@#+]\d+)?\]', descripcion or '')
    tipo, modificador = m.groups() if m else ('sin-tipo', '')
    modificador = modificador or ''
    return {'tipo': tipo if tipo in TIPOS else 'sin-tipo',
            'riesgo': 'caro' if modificador == '!' else 'normal',
            'rol': {'#': 'retrabajo', '+': 'apoyo'}.get(modificador[:1], 'ejecucion'),
            'tarea': int(modificador[1:]) if modificador[:1] in ('#', '+', '@') else None}


def leer_jsonl(ruta):
    with Path(ruta).open(encoding='utf-8') as stream:
        for linea in stream:
            try:
                yield json.loads(linea)
            except ValueError:
                continue


def de_subagente(payload_post, ruta_transcript):
    entrada = payload_post.get('tool_input') or {}
    respuesta = payload_post.get('tool_response') or {}
    if isinstance(respuesta, str):
        try:
            respuesta = json.loads(respuesta)
        except ValueError:
            respuesta = {}
    desc = entrada.get('description', payload_post.get('description', respuesta.get('description', '')))
    datos = etiqueta(desc)
    datos.update(descripcion=desc[:120], agente=respuesta.get('resolvedModel') or entrada.get('model'),
                 esfuerzo=entrada.get('effort'), cuenta='claude', origen='claude',
                 id_externo='agent:' + str(respuesta.get('agentId') or payload_post.get('agent_id') or 'desconocido'))
    campos = dict(zip(TOKENS, ('input_tokens', 'output_tokens', 'reasoning_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens')))
    usages = []
    vistos = set()
    if ruta_transcript and Path(ruta_transcript).is_file():
        for fila in leer_jsonl(ruta_transcript):
            mensaje = fila.get('message') or {}
            usage = mensaje.get('usage') or fila.get('usage')
            # Un mensaje puede aparecer repetido como actualizaciones del streaming.
            identidad = (mensaje.get('id'), mensaje.get('model'), json.dumps(usage, sort_keys=True))
            if usage and (not mensaje.get('id') or identidad not in vistos):
                usages.append(usage)
                vistos.add(identidad)
                datos['agente'] = datos.get('agente') or mensaje.get('model')
    for destino, origen in campos.items():
        valores = [u.get(origen) for u in usages]
        datos[destino] = sum(v for v in valores if v is not None) if any(v is not None for v in valores) else None
    datos['exacto'] = int(bool(usages) and all(all(k in u for k in
                            ('input_tokens', 'output_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens')) for u in usages))
    return datos


def de_codex(archivo_sesion, ultimo_total=None):
    total = None
    for fila in leer_jsonl(archivo_sesion):
        payload = fila.get('payload') or {}
        if payload.get('type') == 'token_count':
            total = (payload.get('info') or {}).get('total_token_usage') or total
    total = total or {}
    anterior = ultimo_total or {}
    def diferencia(clave):
        valor = total.get(clave)
        if valor is None or (ultimo_total is not None and clave not in anterior):
            return None
        resta = valor - anterior.get(clave, 0)
        return resta if resta >= 0 else None
    entrada, cache = diferencia('input_tokens'), diferencia('cached_input_tokens')
    return {'tokens_entrada': entrada - cache if entrada is not None and cache is not None and entrada >= cache else None,
            'tokens_cache_leida': cache, 'tokens_cache_escrita': 0 if total else None,
            'tokens_salida': diferencia('output_tokens'),
            'tokens_razonamiento': diferencia('reasoning_output_tokens'),
            'exacto': int(bool(total) and all(diferencia(k) is not None for k in
                          ('input_tokens', 'cached_input_tokens', 'output_tokens')))}


def de_agy(json_salida):
    datos = json.loads(json_salida) if isinstance(json_salida, str) else json_salida
    usage = datos.get('usage') or {}
    return {'tokens_entrada': usage.get('input_tokens'), 'tokens_salida': usage.get('output_tokens'),
            'tokens_razonamiento': usage.get('thinking_tokens'), 'tokens_cache_leida': usage.get('cache_read_tokens'),
            'tokens_cache_escrita': 0 if usage else None, 'segundos': datos.get('duration_seconds'),
            'id_externo': 'argon:' + str(datos['conversation_id']) if datos.get('conversation_id') else None,
            'exacto': int(all(k in usage for k in ('input_tokens', 'output_tokens', 'cache_read_tokens')))}


def agy_en_vivo(comando, salida):
    """Corre agy con stream-json y deja en `salida` un JSON como el de --output-format json. Si agy no llega
    al evento final (lo matan o se cuelga), el JSON lleva la suma de lo gastado hasta ahí y `cortado: true`."""
    suma = {k: 0 for k in ('input_tokens', 'output_tokens', 'thinking_tokens', 'cache_read_tokens')}
    estado = {'conversation_id': None, 'final': None, 'texto': [], 'inicio': time.time()}
    proceso = subprocess.Popen(comando, stdout=subprocess.PIPE)

    def terminar(senal, _marco):
        # Lo mataron por grupo (ALTO de la sala): agy ya recibió la señal; aquí solo se guarda lo que haya.
        if proceso.poll() is None:
            proceso.terminate()
    anterior = signal.signal(signal.SIGTERM, terminar)
    try:
        for crudo in proceso.stdout:
            try:
                evento = json.loads(crudo)
            except ValueError:
                continue
            if 'event' not in evento:  # formato json de una sola pieza
                estado['final'] = evento
                continue
            if evento['event'] == 'init':
                estado['conversation_id'] = evento.get('conversation_id')
            elif evento['event'] == 'result':
                estado['final'] = evento.get('result') or {}
            elif evento['event'] == 'step_update':
                paso = evento.get('step_update') or {}
                if paso.get('step_type') == 'agent_response' and paso.get('text_delta'):
                    estado['texto'].append(paso['text_delta'])
                if paso.get('state') == 'DONE' and paso.get('usage'):
                    for k in suma:
                        suma[k] += paso['usage'].get(k) or 0
    finally:
        codigo = proceso.wait()
        signal.signal(signal.SIGTERM, anterior)
    final = estado['final']
    if not final or not final.get('usage'):
        final = dict(final or {}, conversation_id=(final or {}).get('conversation_id') or estado['conversation_id'],
                     response=(final or {}).get('response') or ''.join(estado['texto']),
                     duration_seconds=time.time() - estado['inicio'], usage=suma, cortado=True)
    salida.write_text(json.dumps(final, ensure_ascii=False), encoding='utf-8')
    return codigo


def hook(clase, payload):
    respuesta = payload.get('tool_response') or {}
    if isinstance(respuesta, str):
        try:
            respuesta = json.loads(respuesta)
        except ValueError:
            respuesta = {}
    agente = payload.get('agent_id') or respuesta.get('agentId')
    ruta = payload.get('agent_transcript_path') or payload.get('transcript_path')
    if not agente and ruta:
        nombre = Path(ruta).name
        if nombre.startswith('agent-') and nombre.endswith('.jsonl'):
            agente = nombre[6:-6]
    carpeta = REG / 'marcador-payloads'
    carpeta.mkdir(parents=True, exist_ok=True)
    llave = hashlib.sha256(str(agente or ruta).encode()).hexdigest()
    estado = carpeta / (llave + '.json')
    if clase == 'hook-delegacion':
        if not agente:
            (carpeta / (uuid.uuid4().hex + '.json')).write_text(json.dumps(payload), encoding='utf-8')
            return
        datos = de_subagente(payload, None)
        referencia = datos['tarea'] or 'agent:' + str(agente)
        if datos['tarea'] is None:
            encolar({'evento': 'tarea', 'uuid': referencia, 'tipo': datos['tipo'],
                     'riesgo': datos['riesgo'], 'descripcion': datos['descripcion'],
                     'sesion': payload.get('session_id'), 'proyecto': payload.get('cwd')})
        estado.write_text(json.dumps({'post': payload, 'tarea': referencia, 'inicio': time.time()}), encoding='utf-8')
    else:
        if not agente and not ruta:
            (carpeta / (uuid.uuid4().hex + '.json')).write_text(json.dumps(payload), encoding='utf-8')
            return
        previo = json.loads(estado.read_text(encoding='utf-8')) if estado.exists() else {}
        if not ruta and agente:
            # transcript_path del padre se conoce desde PostToolUse.
            padre = (previo.get('post') or {}).get('transcript_path')
            if padre:
                ruta = str(Path(padre).with_suffix('') / 'subagents' / ('agent-' + str(agente) + '.jsonl'))
        post = previo.get('post') or payload
        datos = de_subagente(post, ruta)
        referencia = previo.get('tarea') or datos['tarea'] or 'agent:' + str(agente or llave)
        if not previo and datos['tarea'] is None:
            encolar({'evento': 'tarea', 'uuid': referencia, 'tipo': datos['tipo'], 'descripcion': datos['descripcion']})
        for campo in ('tipo', 'riesgo', 'descripcion', 'tarea'):
            datos.pop(campo, None)
        datos.update(evento='corrida', tarea=referencia,
                     id_externo='agent:' + str(agente or llave),
                     segundos=time.time() - previo['inicio'] if previo.get('inicio') else None)
        encolar(datos)
    segundo_plano()


def resumen(db, dias):
    filas = db.execute("SELECT * FROM _resultados WHERE julianday(fecha)>=julianday('now',?)", ('-' + str(dias) + ' days',)).fetchall()
    ids = [r['id'] for r in filas]
    aceptadas = sum(r['calidad'] == 2 for r in filas)
    evaluables = sum(r['calidad'] is not None for r in filas)
    lineas = ['Últimos %s días: %s tareas; %s/%s aceptadas; %s no evaluables.' %
              (dias, len(filas), aceptadas, evaluables, len(filas)-evaluables),
              'Costo equivalente: %.1f ejecución; %.1f medición.' %
              (sum(r['costo'] for r in filas), sum(r['costo_medir'] for r in filas))]
    # Agrupar el mismo intervalo, sin mezclar la vista histórica con el resumen semanal.
    grupos = {}
    for r in filas:
        if r['compartida'] or r['tipo'] == 'sin-tipo':
            continue
        c = db.execute("SELECT agente,esfuerzo FROM corridas WHERE tarea=? AND rol='ejecucion' ORDER BY id LIMIT 1", (r['id'],)).fetchone()
        if c:
            grupos.setdefault((r['tipo'], c[0], c[1]), []).append(r)
    for (tipo, agente, esfuerzo), casos in sorted(grupos.items(), key=lambda kv: -len(kv[1]))[:4]:
        lineas.append('%s · %s/%s: %s casos, %s aceptadas, costo medio %.1f.' %
                      (tipo, agente, esfuerzo or '?', len(casos), sum(r['calidad'] == 2 for r in casos), sum(r['costo'] for r in casos)/len(casos)))
    for sala in (True, False):
        casos = [r for r in filas if bool(r['sala']) == sala]
        lineas.append('%s sala: %s tareas, %s aceptadas, %s rehechas.' %
                      ('Con' if sala else 'Sin', len(casos), sum(r['calidad'] == 2 for r in casos), sum(r['rehecha'] or r['calidad'] == 0 for r in casos)))
    calibracion = [r for r in db.execute('SELECT * FROM calibracion') if r['tarea'] in ids]
    lineas.append('Calibración: %s tareas, %s acuerdos evaluables.' % (len(calibracion), sum(r['acuerdo'] == 1 and sum(r[k] is not None for k in ('calificador','auditor','didier')) > 1 for r in calibracion)))
    return '\n'.join(lineas)


def sugerir(db, tipo, riesgo):
    filas = db.execute('SELECT * FROM eficiencia WHERE tipo=? AND evaluables>=5 ORDER BY pct_aceptadas DESC,costo_medio', (tipo,)).fetchall()
    if not filas:
        return 'pocos datos: se necesitan al menos 5 casos evaluables por combinación.'
    mejor = filas[0]
    texto = '%s/%s: %s casos, %.0f%% aceptadas, costo medio %.1f. No demuestra equivalencia.' % (mejor['agente'], mejor['esfuerzo'] or '?', mejor['casos'], mejor['pct_aceptadas'] or 0, mejor['costo_medio'])
    n = db.execute('SELECT count(*) FROM tareas WHERE tipo=? AND riesgo=\'normal\'', (tipo,)).fetchone()[0]
    if riesgo == 'normal' and (n + 1) % 5 == 0:
        texto += '\nExplorar un escalón más barato (1 de 5); decide el orquestador, Didier gana.'
    return texto


def sugerir_trabajo(db, trabajo, oficio, papel=None):
    """Elenco con evidencia: con 5 o más etapas juzgadas de una combinación, la que más se aprueba sin vueltas
    y luego la más barata. Devuelve (dict o None, n): None = no hay evidencia suficiente."""
    filas = db.execute(
        'SELECT agente, modelo, esfuerzo, count(*) AS casos, avg(aprobada=1 AND vueltas=0) AS limpias, '
        'avg(usd) AS usd FROM etapas_usd WHERE trabajo=? AND oficio=? AND coalesce(papel,\'\')=? AND aprobada IS NOT NULL '
        'GROUP BY agente, modelo, esfuerzo HAVING count(*)>=5 ORDER BY limpias DESC, usd', (trabajo, oficio, papel or '')).fetchall()
    n = db.execute('SELECT count(*) FROM etapas WHERE trabajo=? AND oficio=? AND coalesce(papel,\'\')=?',
                   (trabajo, oficio, papel or '')).fetchone()[0]
    if not filas:
        return None, n
    mejor = filas[0]
    return {'agente': mejor['agente'], 'modelo': mejor['modelo'], 'esfuerzo': mejor['esfuerzo'],
            'motivo': 'evidencia x{}: {:.0%} limpias, {:.2f} USD por etapa'.format(
                mejor['casos'], mejor['limpias'] or 0, mejor['usd'] or 0)}, n


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='comando', required=True)
    t = sub.add_parser('tarea')
    t.add_argument('--tipo', choices=TIPOS, required=True)
    t.add_argument('--desc', required=True)
    t.add_argument('--riesgo', choices=('normal', 'caro'), default='normal')
    for campo in ('criterio', 'proyecto', 'sesion', 'sala', 'evidencia'):
        t.add_argument('--' + campo)
    e = sub.add_parser('encolar')
    e.add_argument('json')
    sub.add_parser('procesar')
    a = sub.add_parser('alertas')
    a.add_argument('--atendidas', action='store_true')
    v = sub.add_parser('veredicto')
    v.add_argument('--tarea', type=int, required=True)
    v.add_argument('--juez', choices=('didier', 'auditor', 'calificador'), required=True)
    v.add_argument('--nivel', choices=('0', '1', '2', 'nulo'), required=True)
    v.add_argument('--motivo', required=True)
    v.add_argument('--huella', required=True)
    v.add_argument('--ciego', action='store_true')
    c = sub.add_parser('corregir')
    c.add_argument('tarea', type=int)
    c.add_argument('nivel', choices=('0', '1', '2'))
    c.add_argument('motivo')
    r = sub.add_parser('resumen')
    r.add_argument('--dias', type=int, default=7)
    s = sub.add_parser('sugerir')
    s.add_argument('--tipo', choices=TIPOS, required=True)
    s.add_argument('--riesgo', choices=('normal', 'caro'), default='normal')
    for comando in ('pendientes', 'auditar', 'hook-delegacion', 'hook-subagente'):
        sub.add_parser(comando)
    return p


def interfaz(args):
    if args.comando == 'encolar':
        encolar(json.loads(args.json))
        return ''
    if args.comando == 'procesar':
        return procesar()
    if args.comando.startswith('hook-'):
        hook(args.comando, json.load(sys.stdin))
        return ''
    with conexion() as db:
        if args.comando in ('tarea', 'veredicto', 'corregir', 'auditar', 'alertas'):
            db.execute('BEGIN IMMEDIATE')
        if args.comando == 'tarea':
            datos = {k: v for k, v in vars(args).items() if k != 'comando' and v is not None}
            datos['descripcion'] = datos.pop('desc')
            resultado = str(aplicar(db, dict(evento='tarea', **datos)))
        elif args.comando in ('veredicto', 'corregir'):
            aplicar(db, {'evento': 'veredicto', 'tarea': args.tarea,
                         'juez': getattr(args, 'juez', 'didier'),
                         'nivel': None if args.nivel == 'nulo' else int(args.nivel),
                         'motivo': args.motivo, 'huella_evidencia': getattr(args, 'huella', ''),
                         'ciego': int(getattr(args, 'ciego', False))})
            resultado = 'Veredicto registrado.'
        elif args.comando == 'resumen':
            if args.dias < 1:
                raise ValueError('--dias debe ser positivo')
            resultado = resumen(db, args.dias)
        elif args.comando == 'sugerir':
            resultado = sugerir(db, args.tipo, args.riesgo)
        elif args.comando == 'pendientes':
            filas = db.execute('SELECT id,tipo,descripcion FROM sin_veredicto ORDER BY fecha').fetchall()
            resultado = '\n'.join(['Pendientes sin veredicto (>30 min): ' + str(len(filas))] +
                                  ['%s · %s · %s' % tuple(r) for r in filas[:8]])
        elif args.comando == 'alertas':
            if args.atendidas:
                db.execute('UPDATE alertas SET atendida=1 WHERE atendida=0')
                resultado = 'Alertas marcadas como atendidas.'
            else:
                total = db.execute('SELECT count(*) FROM alertas WHERE atendida=0').fetchone()[0]
                filas = db.execute('SELECT id,tarea,motivo FROM alertas WHERE atendida=0 '
                                   'ORDER BY fecha DESC,id DESC LIMIT 9').fetchall()
                resultado = '\n'.join(['Alertas de gasto sin atender: ' + str(total)] +
                                      ['{} · tarea {} · {}'.format(*r) for r in filas])
        elif args.comando == 'auditar':
            filas = db.execute("SELECT DISTINCT t.id FROM tareas t JOIN veredictos v ON v.tarea=t.id WHERE v.juez='calificador' AND julianday(t.fecha)>=julianday('now','-7 days')").fetchall()
            elegidos = random.sample([r[0] for r in filas], min(2, len(filas)))
            for tarea in elegidos:
                db.execute("INSERT INTO trabajos(clase,tarea,estado,fecha) VALUES ('auditar',?,'pendiente',?)", (tarea, ahora()))
            resultado = 'Auditar (a ciegas): ' + (', '.join(map(str, elegidos)) or 'sin candidatas')
        else:
            raise ValueError('comando desconocido')
        if db.in_transaction:
            db.commit()
        return resultado


def opciones_envoltorio(argv):
    if '--' not in argv:
        raise ValueError('separa los argumentos del motor con --')
    corte = argv.index('--')
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument('--tipo', choices=TIPOS, default='sin-tipo')
    p.add_argument('--riesgo', choices=('normal', 'caro'), default='normal')
    relacion = p.add_mutually_exclusive_group()
    relacion.add_argument('--tarea', type=int)
    relacion.add_argument('--retrabajo', type=int)
    relacion.add_argument('--apoyo', type=int)
    p.add_argument('--rol', choices=ROLES, default='ejecucion')
    p.add_argument('--criterio')
    p.add_argument('-o', '--output')
    p.add_argument('--pruebas', help='comando de pruebas de Codex (tope: 10 minutos)')
    args = p.parse_args(argv[:corte])
    if not argv[corte+1:]:
        raise ValueError('faltan argumentos del motor')
    return args, argv[corte+1:]


def preparar_envoltorio(args, argumentos):
    referencia = args.retrabajo or args.apoyo or args.tarea or os.environ.get('MARCADOR_TAREA')
    rol = 'retrabajo' if args.retrabajo else 'apoyo' if args.apoyo else args.rol
    if referencia is None:
        referencia = str(uuid.uuid4())
        encolar({'evento': 'tarea', 'uuid': referencia, 'tipo': args.tipo, 'riesgo': args.riesgo,
                 'descripcion': ' '.join(argumentos)[:120], 'proyecto': os.getcwd(), 'criterio': args.criterio})
    return referencia, rol


def sesiones_codex(cwd=None):
    carpeta = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'sessions'
    cwd = Path(cwd or os.getcwd()).resolve()
    resultado = {}
    for ruta in carpeta.glob('**/*.jsonl'):
        try:
            filas = leer_jsonl(ruta)
            meta = next(filas, {}).get('payload', {})
            if Path(meta.get('cwd', '/')).resolve() != cwd:
                continue
            total, conteo, modelo, esfuerzo = None, 0, None, None
            for fila in filas:
                payload = fila.get('payload') or {}
                if payload.get('type') == 'token_count':
                    total = (payload.get('info') or {}).get('total_token_usage') or total
                    conteo += 1
                if fila.get('type') == 'turn_context':
                    modelo = payload.get('model') or modelo
                    esfuerzo = payload.get('effort') or (payload.get('collaboration_mode') or {}).get('settings', {}).get('reasoning_effort') or esfuerzo
            resultado[str(ruta)] = dict(id=meta.get('id'), total=total, conteo=conteo,
                                         modelo=modelo, esfuerzo=esfuerzo, mtime=ruta.stat().st_mtime)
        except (OSError, ValueError):
            continue
    return resultado


def opcion(argumentos, *nombres):
    for i, valor in enumerate(argumentos):
        if valor in nombres and i+1 < len(argumentos):
            return argumentos[i+1]
        for nombre in nombres:
            if valor.startswith(nombre + '='):
                return valor.split('=', 1)[1]
    return None


def guardar_evidencia_codex(cwd, sesion, comando_pruebas):
    partes, pruebas = [], 'na'
    if comando_pruebas is not None:
        vencio = False
        with tempfile.TemporaryFile() as salida:
            proceso = subprocess.Popen(comando_pruebas, shell=True, cwd=cwd, stdout=salida,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            try:
                codigo = proceso.wait(timeout=600)
            except subprocess.TimeoutExpired:
                vencio = True
                try:
                    os.killpg(proceso.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                codigo = proceso.wait()
            pruebas = 'paso' if codigo == 0 and not vencio else 'fallo'
            salida.seek(0, os.SEEK_END)
            salida.seek(max(0, salida.tell() - 16384))
            cola = '\n'.join(salida.read().decode('utf-8', errors='replace').splitlines()[-40:])[-2000:]
        partes.append('PRUEBAS: {} (salida {}; {})\n$ {}\n{}'.format(
            pruebas, codigo, 'tope de 10 min agotado' if vencio else 'últimas 40 líneas',
            comando_pruebas[:300], cola))
    try:
        repo = subprocess.run(['git', 'rev-parse', '--is-inside-work-tree'], cwd=cwd,
                              capture_output=True, text=True, timeout=10)
        if repo.returncode == 0 and repo.stdout.strip() == 'true':
            for argumentos, tope in ((['status', '--short'], 600), (['diff', '--stat'], 1000)):
                r = subprocess.run(['git'] + argumentos, cwd=cwd, capture_output=True,
                                   text=True, errors='replace', timeout=10)
                partes.append('$ git {}\n{}'.format(' '.join(argumentos),
                              (r.stdout if r.returncode == 0 else r.stderr)[:tope]))
    except (OSError, subprocess.TimeoutExpired) as exc:
        partes.append('Git no disponible: ' + str(exc)[:200])
    if partes:
        archivo = REG / 'marcador-respuestas' / ('codex-' + identifier(sesion) + '.txt')
        archivo.parent.mkdir(parents=True, exist_ok=True)
        archivo.write_text('\n\n'.join(partes), encoding='utf-8')
    return pruebas


def envolver(motor, argv):
    args, argumentos = opciones_envoltorio(argv)
    if args.pruebas is not None and motor != 'codex':
        raise ValueError('--pruebas solo está disponible para codex')
    referencia, rol = preparar_envoltorio(args, argumentos)
    inicio = time.time()
    datos = {}
    if motor == 'codex':
        cwd = opcion(argumentos, '--cd', '-C') or os.getcwd()
        previas = sesiones_codex(cwd)
        capturado = []
        proceso = subprocess.Popen(['codex', 'exec'] + argumentos, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        def copiar(origen, destino):
            while True:
                bloque = origen.read1(8192)
                if not bloque:
                    break
                # El encabezado contiene el id; no retener una respuesta enorme.
                if sum(map(len, capturado)) < 2097152:
                    capturado.append(bloque.decode('utf-8', errors='replace'))
                destino.buffer.write(bloque)
                destino.buffer.flush()
        hilos = [threading.Thread(target=copiar, args=par) for par in
                 ((proceso.stdout, sys.stdout), (proceso.stderr, sys.stderr))]
        for hilo in hilos:
            hilo.start()
        codigo = proceso.wait()
        for hilo in hilos:
            hilo.join()
        posteriores = sesiones_codex(cwd)
        texto = ''.join(capturado)
        identificadores = set(re.findall(r'(?:session id:|"thread_id"\s*:)\s*["\s]*([a-zA-Z0-9_-]+)', texto, re.I))
        candidatas = [(p, m) for p, m in posteriores.items() if m['id'] in identificadores]
        if not candidatas and not identificadores:
            candidatas = [(p, m) for p, m in posteriores.items() if p not in previas and m['mtime'] >= inicio]
        if len(candidatas) == 1:
            ruta, meta = candidatas[0]
            datos = de_codex(ruta, previas.get(ruta, {}).get('total'))
            datos['id_externo'] = 'codex:%s:%s' % (meta['id'], meta['conteo'])
            datos['agente'] = meta['modelo']
            datos['esfuerzo'] = meta['esfuerzo']
        else:
            print('Marcador: sesión no identificada inequívocamente; tokens desconocidos.', file=sys.stderr)
        datos['agente'] = datos.get('agente') or opcion(argumentos, '--model', '-m') or 'codex'
        datos.setdefault('esfuerzo', None)
        datos.setdefault('id_externo', 'codex:' + uuid.uuid4().hex)
        datos['pruebas'] = guardar_evidencia_codex(cwd, datos['id_externo'].split(':')[1], args.pruebas)
    else:
        salida = Path(args.output) if args.output else REG / 'marcador-respuestas' / (uuid.uuid4().hex + '.json')
        salida = salida.absolute()
        salida.parent.mkdir(parents=True, exist_ok=True)
        # El archivo siempre es JSON, incluso si el llamador traía otro formato.
        limpios = []
        omitir = False
        for valor in argumentos:
            if omitir:
                omitir = False
                continue
            if valor == '--output-format':
                omitir = True
                continue
            if not valor.startswith('--output-format='):
                limpios.append(valor)
        comando = ['agy'] + (limpios if '-p' in limpios else ['-p'] + limpios)
        if os.environ.get('SALA_UUID'):
            # En una sala el proceso puede acabar cortado (ALTO, cuelgue): se va sumando el gasto paso a paso.
            codigo = agy_en_vivo(comando + ['--output-format', 'stream-json'], salida)
        else:
            with salida.open('wb') as stream:
                codigo = subprocess.call(comando + ['--output-format', 'json'], stdout=stream)
        try:
            texto_salida = salida.read_text(encoding='utf-8')
            datos = de_agy(texto_salida)
            respuesta = json.loads(texto_salida)
            conversacion = identifier(respuesta['conversation_id'])
            copia = REG / 'marcador-respuestas' / ('argon-' + conversacion + '.json')
            copia.parent.mkdir(parents=True, exist_ok=True)
            copia.write_text(json.dumps({'prompt': opcion(comando, '-p'),
                                         'response': respuesta.get('response', '')}, ensure_ascii=False), encoding='utf-8')
            if respuesta.get('cortado'):
                datos['exacto'] = 0
        except (ValueError, KeyError):
            datos = {}
        datos['agente'] = opcion(argumentos, '--model', '-m') or 'argon'
        datos['esfuerzo'] = None
        print(salida)
        print('Gasto: entrada %s; salida %s; caché %s; %.2f s.' %
              (datos.get('tokens_entrada'), datos.get('tokens_salida'), datos.get('tokens_cache_leida'), time.time()-inicio))
    datos.update(evento='corrida', tarea=referencia, origen=motor, rol=rol,
                 cuenta='chatgpt' if motor == 'codex' else 'google',
                 segundos=datos.get('segundos') if datos.get('segundos') is not None else time.time()-inicio,
                 fecha=ahora())
    datos['id_externo'] = datos.get('id_externo') or motor + ':' + uuid.uuid4().hex
    try:
        encolar(datos)
        segundo_plano()
    except (OSError, ValueError) as error:
        print('Marcador: no se pudo registrar: ' + str(error).replace('\n', ' ')[:180], file=sys.stderr)
    return codigo if codigo >= 0 else 128 - codigo


def main():
    try:
        if len(sys.argv) > 1 and sys.argv[1] in ('envolver-codex', 'envolver-argon'):
            return envolver(sys.argv[1].split('-')[1], sys.argv[2:])
        resultado = interfaz(parser().parse_args())
        if resultado:
            print(resultado)
        return 0
    except (sqlite3.OperationalError, TimeoutError) as error:
        ocupado = isinstance(error, TimeoutError) or 'locked' in str(error) or 'busy' in str(error)
        print('Base ocupada; reintentar.' if ocupado else 'Error: ' + str(error), file=sys.stderr)
        return 3 if ocupado else 2
    except (ValueError, KeyError, TypeError, OSError, sqlite3.IntegrityError) as error:
        print('Error: ' + str(error).replace('\n', ' ')[:240], file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
