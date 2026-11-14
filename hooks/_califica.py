#!/usr/bin/python3
"""Calificador de Humanify (diseño v2.1, sección B): Opus low aislado, solo con evidencia corta."""
import glob
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _humanify as H  # noqa: E402
import _sala as S  # noqa: E402

TOPE_EVIDENCIA = 6000
TOPE_RESPUESTA = 3000
TOPE_TEAMS = 16000  # una plática se juzga entera: cortarla fue la FALLA 21
DIRIGE = ('didier', 'orquestador')
ESQUEMA = {
    'type': 'object',
    'properties': {'nivel': {'enum': [0, 1, 2, None]}, 'tipo': {'type': 'string'}, 'motivo': {'type': 'string'}},
    'required': ['nivel', 'motivo'],
}


def base_datos():
    return Path(os.environ.get('MARCADOR_DB', str(H.REG / 'marcador.db')))


def leer_tarea(numero):
    with sqlite3.connect('file:{}?mode=ro'.format(base_datos()), uri=True, timeout=2) as con:
        con.row_factory = sqlite3.Row
        tarea = con.execute('SELECT * FROM tareas WHERE id = ?', (numero,)).fetchone()
        if tarea is None:
            raise ValueError('no existe la tarea {}'.format(numero))
        corridas = con.execute('SELECT * FROM corridas WHERE tarea = ? ORDER BY id', (numero,)).fetchall()
        huellas = {fila[0] for fila in con.execute(
            "SELECT huella_evidencia FROM veredictos WHERE tarea = ? AND juez = 'calificador'", (numero,))}
    return dict(tarea), [dict(c) for c in corridas], huellas


def texto_de(contenido):
    if isinstance(contenido, str):
        return contenido
    if isinstance(contenido, list):
        return '\n'.join(p.get('text', '') for p in contenido if isinstance(p, dict) and p.get('type') == 'text')
    return ''


def de_subagente(agent_id):
    rutas = glob.glob(str(Path.home() / '.claude/projects/*/*/subagents/agent-{}.jsonl'.format(agent_id)))
    if not rutas:
        return '', ''
    encargo, respuesta = '', ''
    for linea in open(rutas[0], encoding='utf-8'):
        try:
            d = json.loads(linea)
        except ValueError:
            continue
        mensaje = d.get('message') or {}
        texto = texto_de(mensaje.get('content'))
        if d.get('type') == 'user' and not encargo and texto:
            encargo = texto
        elif d.get('type') == 'assistant':
            # El reporte final del subagente llega como llamada a SubagentHandback, no como texto.
            for parte in mensaje.get('content') or []:
                if isinstance(parte, dict) and parte.get('type') == 'tool_use' and parte.get('name') == 'SubagentHandback':
                    texto = str((parte.get('input') or {}).get('message') or '') or texto
            if texto:
                respuesta = texto
    return encargo, respuesta


def de_codex(sesion):
    rutas = glob.glob(str(Path.home() / '.codex/sessions/*/*/*/*{}*.jsonl'.format(sesion)))
    if not rutas:
        return '', ''
    encargo, respuesta = '', ''
    for linea in open(rutas[0], encoding='utf-8'):
        try:
            d = json.loads(linea)
        except ValueError:
            continue
        carga = d.get('payload') or {}
        if carga.get('type') == 'message' and carga.get('role') == 'user':
            # Los primeros mensajes de usuario son AGENTS.md y el contexto del entorno; el encargo es el último que no lo es.
            texto = '\n'.join(p.get('text', '') for p in carga.get('content') or [] if isinstance(p, dict))
            if texto and not texto.lstrip().startswith(('# AGENTS.md', '<')):
                encargo = texto
        elif carga.get('type') == 'task_complete' and carga.get('last_agent_message'):
            respuesta = carga['last_agent_message']
    return encargo, respuesta


def evidencia_teams(tarea):
    """Plática del Teams: el pedido y las decisiones de quien dirige son el criterio; el chat va entero."""
    guardada = H.REG / 'marcador-evidencia' / 'tarea-{}'.format(tarea['id'])
    chat = guardada / 'CHAT.md'
    lineas = chat.read_text(encoding='utf-8', errors='replace').splitlines() if chat.is_file() else []
    pedido, criterio = '', []
    for linea in lineas:
        _, _, cuerpo = linea.partition(' ')
        quien, _, texto = cuerpo.partition('→')
        texto = texto.partition(': ')[2]
        if quien not in DIRIGE or not texto or texto.startswith('/'):
            continue
        if not pedido:
            pedido = texto.partition('Brief: ')[2] or texto
        else:
            criterio.append('- {}: {}'.format(quien, texto))
    partes = ['PLÁTICA DEL TEAMS ({}): varios agentes conversan y construyen; se juzga el resultado del equipo.'.format(
        tarea.get('descripcion') or '')]
    if pedido:
        partes.append('ENCARGO:\n' + pedido[:1500])
    if tarea.get('criterio'):
        partes.append('CRITERIO DE "QUEDÓ BIEN": ' + tarea['criterio'])
    if criterio:
        partes.append('CRITERIO (aprobaciones y correcciones de quien dirige, en orden):\n' + '\n'.join(criterio)[:3000])
    rubrica = guardada / 'RUBRICA.md'
    if rubrica.is_file():
        partes.append('RÚBRICA DEL OFICIO (criterios de "quedó bien" de este tipo de trabajo):\n' +
                      rubrica.read_text(encoding='utf-8', errors='replace')[:1500])
    entregables = guardada / 'entregables'
    if entregables.is_dir():
        for archivo in sorted(entregables.iterdir()):
            if not archivo.is_file():
                continue
            if archivo.suffix == '.html':
                # El HTML es código de animación: cortado no se puede juzgar; el chat dice cómo quedó.
                partes.append('--- entregable: {} ({} KB, código de animación) ---'.format(
                    archivo.name, archivo.stat().st_size // 1024))
            else:
                with archivo.open(encoding='utf-8', errors='replace') as stream:
                    partes.append('--- entregable: {} ---\n{}'.format(archivo.name, stream.read(1500)))
    cabeza = '\n\n'.join(partes)
    titulo = '\n\nCHAT COMPLETO:\n'
    texto_chat = '\n'.join(lineas)
    disponible = max(0, TOPE_TEAMS - len(cabeza) - len(titulo))
    if len(texto_chat) > disponible:
        cabeza = cabeza[:max(0, TOPE_TEAMS - len(titulo) - min(len(texto_chat), TOPE_TEAMS * 2 // 3))]
        disponible = TOPE_TEAMS - len(cabeza) - len(titulo)
        texto_chat = '…\n' + texto_chat[-(disponible - 2):]
    return (cabeza + titulo + texto_chat)[:TOPE_TEAMS]


def evidencia(tarea, corridas):
    if (tarea.get('descripcion') or '').startswith('teams:'):
        return evidencia_teams(tarea)
    partes = []
    if tarea.get('criterio'):
        partes.append('CRITERIO DE "QUEDÓ BIEN": ' + tarea['criterio'])
    partes.append('TAREA: {} (tipo {})'.format(tarea.get('descripcion') or '', tarea.get('tipo')))
    pruebas = [c['pruebas'] for c in corridas if c.get('pruebas') and c['pruebas'] != 'na']
    if pruebas:
        partes.append('PRUEBAS: ' + ', '.join(pruebas))
    vueltas = sum(1 for c in corridas if c.get('rol') == 'retrabajo')
    if vueltas:
        partes.append('RETRABAJOS: {}'.format(vueltas))
    # Prioridad dentro del tope: criterio, entregables verificables, reportes y chat.
    partes = ['\n\n'.join(partes)[:1000]]
    def agregar(texto):
        disponible = max(0, TOPE_EVIDENCIA - len('\n\n'.join(partes)) - 2)
        if texto and disponible:
            partes.append(texto[:disponible])

    carpeta = tarea.get('evidencia')
    texto_chat = ''
    if tarea.get('sala'):
        guardada = H.REG / 'marcador-evidencia' / 'tarea-{}'.format(tarea['id'])
        entregables = guardada / 'entregables'
        if entregables.is_dir():
            for archivo in sorted(entregables.iterdir()):
                if archivo.is_file():
                    with archivo.open(encoding='utf-8', errors='replace') as stream:
                        agregar('--- entregable: {} ---\n{}'.format(archivo.name, stream.read(1500)))
        omitidos = guardada / 'entregables-omitidos.txt'
        if omitidos.is_file():
            agregar(omitidos.read_text(encoding='utf-8')[:1000])
        chat = guardada / 'CHAT.md'
        if chat.is_file():
            lineas = chat.read_text(encoding='utf-8', errors='replace').splitlines()[-40:]
            texto_chat = '\n'.join(lineas)
    elif carpeta and Path(carpeta).is_dir():
        archivos = []
        for archivo in sorted(Path(carpeta).iterdir()):
            if archivo.suffix in ('.md', '.txt', '.log', '.json') and archivo.is_file():
                archivos.append('--- {} ---\n{}'.format(archivo.name, archivo.read_text(encoding='utf-8', errors='replace')[:TOPE_RESPUESTA]))
        agregar('\n\n'.join(archivos)[:2000])
    reportes, encargo = [], ''
    for c in corridas:
        if c.get('rol') not in ('ejecucion', 'retrabajo'):
            continue
        origen, _, resto = (c.get('id_externo') or '').partition(':')
        prompt, respuesta = '', ''
        if origen == 'agent':
            prompt, respuesta = de_subagente(resto)
        elif origen == 'codex':
            sesion = resto.split(':')[0]
            prompt, respuesta = de_codex(sesion)
            archivo = H.REG / 'marcador-respuestas' / ('codex-' + H.identifier(sesion) + '.txt')
            if archivo.is_file():
                with archivo.open(encoding='utf-8', errors='replace') as stream:
                    agregar('--- evidencia Codex: {} ---\n{}'.format(sesion, stream.read(4000)))
        elif origen == 'argon':
            archivo = H.REG / 'marcador-respuestas' / ('argon-' + H.identifier(resto) + '.json')
            if archivo.is_file():
                datos = json.loads(archivo.read_text(encoding='utf-8'))
                prompt, respuesta = datos.get('prompt') or '', datos.get('response') or ''
        encargo = encargo or prompt
        reportes.append(('--- {} ---\n'.format(c.get('agente') or origen), respuesta or '(sin reporte disponible)'))
    if reportes:
        usado = len('\n\n'.join(partes)) + sum(len(titulo) + 2 for titulo, _ in reportes)
        por_reporte = 700 if tarea.get('sala') else TOPE_RESPUESTA
        if sum(min(por_reporte, len(respuesta)) for _, respuesta in reportes) > TOPE_EVIDENCIA - usado:
            por_reporte = min(por_reporte, max(0, (TOPE_EVIDENCIA - usado) // len(reportes)))
        for titulo, respuesta in reportes:
            agregar(titulo + respuesta[-por_reporte:] if por_reporte else titulo)
    if encargo:
        agregar('ENCARGO:\n' + encargo[:1000])
    if texto_chat:
        titulo_chat = 'CHAT (últimas 40 líneas):\n'
        disponible = max(0, TOPE_EVIDENCIA - len('\n\n'.join(partes)) - len(titulo_chat) - 2)
        if disponible:
            agregar(titulo_chat + texto_chat[-disponible:])
    return '\n\n'.join(partes)[:TOPE_EVIDENCIA]


def marcador(*argumentos):
    herramienta = H.HOME_DIR / 'herramientas/marcador.sh'
    return subprocess.run(['/bin/bash', str(herramienta)] + list(argumentos), capture_output=True, text=True, timeout=30)


def calificar(numero):
    tarea, corridas, ya = leer_tarea(int(numero))
    texto = evidencia(tarea, corridas)
    huella = hashlib.sha256(texto.encode('utf-8')).hexdigest()[:16]
    if huella in ya:
        return 'tarea {}: ya calificada con esta evidencia'.format(numero)
    rubrica = (H.HOME_DIR / 'cerebro/contratos/calificador.md').read_text(encoding='utf-8')
    inicio = time.time()
    resultado, usage = S.claude_json(rubrica, texto, ESQUEMA, 'low')
    nivel = 'nulo' if resultado.get('nivel') is None else str(resultado['nivel'])
    motivo = ' '.join(str(resultado.get('motivo', '')).split())[:200]
    if tarea.get('tipo') == 'sin-tipo' and resultado.get('tipo'):
        motivo = '{} (tipo sugerido: {})'.format(motivo, resultado['tipo'])[:200]
    r = marcador('veredicto', '--tarea', str(numero), '--juez', 'calificador', '--nivel', nivel,
                 '--motivo', motivo, '--huella', huella)
    if r.returncode:
        raise ValueError('marcador veredicto: ' + (r.stderr or r.stdout).strip()[:160])
    evento = {'evento': 'corrida', 'tarea': int(numero), 'origen': 'calificador',
              'id_externo': 'calificador:{}:{}'.format(numero, huella[:8]), 'rol': 'calificador',
              'agente': 'opus', 'esfuerzo': 'low', 'cuenta': 'claude',
              'tokens_entrada': usage.get('input_tokens'), 'tokens_salida': usage.get('output_tokens'),
              'tokens_cache_leida': usage.get('cache_read_input_tokens'),
              'tokens_cache_escrita': usage.get('cache_creation_input_tokens'),
              'segundos': round(time.time() - inicio, 1), 'exacto': 1}
    marcador('encolar', json.dumps(evento, ensure_ascii=False))
    marcador('procesar')
    return 'tarea {}: nivel {} · {}'.format(numero, nivel, motivo)


if __name__ == '__main__':
    try:
        if len(sys.argv) != 2 or not sys.argv[1].isdigit():
            raise ValueError('uso: califica.sh <tarea>')
        print(calificar(sys.argv[1]))
        sys.exit(0)
    except Exception as exc:
        try:
            H.log_event('califica', sys.argv[1] if len(sys.argv) > 1 else '', 'error', 0, str(exc))
        except Exception:
            pass
        print('califica: ' + str(exc), file=sys.stderr)
        sys.exit(1)
