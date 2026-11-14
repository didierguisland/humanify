#!/usr/bin/python3
"""Canales Teams sin sesiones de modelo. Solo biblioteca estándar."""
import argparse
import codecs
from collections import deque
import datetime as dt
import json
import os
from pathlib import Path
import queue
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import uuid
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _sala import COLORES, huella, marcador_sh
import _cazafrases

TOPE_TURNOS = 8
TOPE_RESPUESTA = 400
TOPE_CONTEXTO = 12000
ESPERA = 120
VIGENCIA = 3 * 3600  # una plática sin actividad por más de esto se cierra y la siguiente abre otra
TOPE_PROCESO = 6000
TOPE_TALLER = 4  # trabajos del taller por cada mensaje de Didier
TOPE_AVISO = 600
PEDIDO_TALLER = re.compile(r'/taller\s+@?([\w-]+)\s*:\s*(.+?)(?=\s/taller\s|$)')


def raiz():
    return Path(os.environ.get('HUMANIFY_DIR', str(Path(__file__).resolve().parent.parent)))


def proyectos():
    base = Path(os.environ.get('HUMANIFY_PROYECTOS', str(Path.home() / 'Projects'))).expanduser()
    return sorted((p for p in base.iterdir() if p.is_dir()), key=lambda p: p.name.casefold())


def proyecto_por_nombre(nombre):
    return next((p for p in proyectos() if p.name == nombre), None)


def equipo(base):
    miembros = {}
    for archivo in (base / 'equipo').glob('*/config.json'):
        config = json.loads(archivo.read_text(encoding='utf-8'))
        if config['motor'] not in ('claude', 'codex', 'agy'):
            raise ValueError('motor desconocido: ' + archivo.parent.name)
        miembros[archivo.parent.name.lower()] = config
    return dict(sorted(miembros.items(), key=lambda par: (par[1]['orden'], par[0])))


def plano(texto, tope=None):
    # Una respuesta siempre ocupa una línea; tampoco puede mandar escapes al visor.
    texto = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', str(texto))
    texto = ' '.join(''.join(c for c in texto if c.isprintable() or c.isspace()).split())
    if tope is None or len(texto) <= tope:
        return texto
    # Cortar en la última palabra completa, no a la mitad.
    corte = texto[:tope - 1].rsplit(' ', 1)[0] if ' ' in texto[:tope - 1] else texto[:tope - 1]
    return corte.rstrip(',;:') + '…'


# Lo que los motores dicen cuando se acaba la cuota (Claude: «You've hit your 5-hour limit · resets 11:20pm …», 429).
# Un 429 suelto o «limit reached» a secas no cuentan: una traza «archivo.js:429:10» o «context limit reached» no se arreglan esperando.
LIMITE = re.compile(r"(?:status|http|error|code|·)\D{0,3}429(?![\d:.])|\b429\b\s*(?:too many|client error)|usage[_ ]limit"
                    r"|hit your .{0,30}limit|rate.?limit(?!er)|too many requests|resource.?exhausted|quota (?:exceeded|exhausted)"
                    r"|(?:session|daily|weekly|monthly|hourly|hour|spend|credit|quota|plan|account)[ -]limit reached", re.I)
HORA_REINICIO = re.compile(r'resets?\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m?\b|resets?\s+(?:at\s+)?(\d{1,2}):(\d{2})\b', re.I)
EN_REINICIO = re.compile(r'(?:resets?|retry|try again)\s+(?:in|after)\s+(\d+(?:\.\d+)?)\s*(d|h|m|s)', re.I)
FECHA_REINICIO = re.compile(r'resets?\s+(?:on\s+)?(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})'
                            r'(?:\s*(?:,|at)\s*(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m?\b)?', re.I)
ZONA_REINICIO = re.compile(r'\(([A-Za-z_]+/[A-Za-z_+-]+)\)')
MESES = 'jan feb mar apr may jun jul aug sep oct nov dec'.split()


def limite_de_uso(texto, ahora=None):
    """Si el texto es un límite de cuota, {'reinicio': epoch o None}; si no, None. La hora del motor es la de la zona que
    nombre el mensaje («resets 11:20pm (America/Mexico_City)»); sin zona, la local."""
    if not texto or not LIMITE.search(str(texto)):
        return None
    texto = str(texto)
    ahora = ahora or dt.datetime.now()
    relativo = EN_REINICIO.search(texto)
    if relativo:
        segundos = float(relativo.group(1)) * {'d': 86400, 'h': 3600, 'm': 60, 's': 1}[relativo.group(2).lower()]
        return {'reinicio': (ahora + dt.timedelta(seconds=segundos)).timestamp()}
    zona = ZONA_REINICIO.search(texto)
    if zona:
        try:
            ahora = ahora.astimezone(ZoneInfo(zona.group(1)))
        except (KeyError, ValueError, OSError):
            pass  # zona que no conocemos: se queda la local
    fecha = FECHA_REINICIO.search(texto)
    if fecha:  # límite semanal: «resets Nov 7, 9am»
        h = (int(fecha.group(3)) % 12 + (12 if (fecha.group(5) or '').lower() == 'p' else 0)) if fecha.group(3) else 0
        reinicio = ahora.replace(month=MESES.index(fecha.group(1).lower()) + 1, day=int(fecha.group(2)), hour=h,
                                 minute=int(fecha.group(4) or 0), second=0, microsecond=0)
        if reinicio <= ahora:
            reinicio = reinicio.replace(year=reinicio.year + 1)
        return {'reinicio': reinicio.timestamp()}
    hora = HORA_REINICIO.search(texto)
    if not hora:
        return {'reinicio': None}
    if hora.group(1):
        h, m = int(hora.group(1)) % 12 + (12 if hora.group(3).lower() == 'p' else 0), int(hora.group(2) or 0)
    else:
        h, m = int(hora.group(4)), int(hora.group(5))
    reinicio = ahora.replace(hour=h, minute=m, second=0, microsecond=0)
    if reinicio <= ahora:
        reinicio += dt.timedelta(days=1)
    return {'reinicio': reinicio.timestamp()}


def detalle_error(codigo, errores, salida):
    """El error real de un motor, no solo su código: si sacó JSON, lo que el JSON dice."""
    try:
        datos = json.loads(salida)
        partes = [str(datos[k]) for k in ('result', 'error', 'api_error', 'api_error_status') if datos.get(k)]
    except (TypeError, ValueError, AttributeError):
        partes = []
    detalle = ' · '.join(partes) or ' '.join((errores or salida or '').split())[-600:]
    return ('salida {}: {}'.format(codigo, detalle)).rstrip(': ')


def ejecutar(comando, cwd, env):
    """El plazo incluye al wrapper y mata también al motor hijo si vence."""
    proceso = subprocess.Popen(comando, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               start_new_session=True)
    try:
        salida, errores = proceso.communicate(timeout=ESPERA)
    except BaseException:
        try:
            os.killpg(proceso.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proceso.communicate()
        raise
    if proceso.returncode:
        raise ValueError(detalle_error(proceso.returncode, errores, salida))
    return salida


class Canal:
    def __init__(self, proyecto):
        self.base = raiz()
        self.proyecto = Path(proyecto)
        self.miembros = equipo(self.base)
        self.carpeta = self.base / 'registro/canales' / self.proyecto.name
        self.carpeta.mkdir(parents=True, exist_ok=True)
        self.vacia = self.base / 'registro/vacia'
        self.vacia.mkdir(parents=True, exist_ok=True)
        self.candado = threading.RLock()
        self.cache = {}
        self.escribiendo = None
        self.avisos = queue.Queue()
        self.talleres = []
        self.pedidos_taller = 0
        self.espejos = {}  # PLATICA.md de cada trabajador del taller: lo que se dice mientras trabaja le llega
        for nombre in ('CHAT.md', 'huellas'):
            (self.carpeta / nombre).touch(exist_ok=True)
        self.estado = self.retomar()
        if self.estado is None:
            self.estado = {'uuid': uuid.uuid4().hex, 'esperando_didier': False, 'procesos': [],
                           'mensajes': {}, 'linea_inicio': len(self.lineas()), 'actividad': time.time()}
            self.estado['tarea'] = self.crear_tarea()
        self.guardar_estado()

    def retomar(self):
        """La plática abierta sigue si tuvo actividad reciente; si ya venció, se cierra antes de abrir otra."""
        archivo = self.carpeta / 'estado.json'
        try:
            previo = json.loads(archivo.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None
        if previo.get('cerrada') or 'tarea' not in previo:
            return None
        previo.setdefault('procesos', [])
        previo.setdefault('mensajes', {})
        previo.setdefault('linea_inicio', 0)
        if time.time() - previo.get('actividad', 0) < VIGENCIA or self.ruta_abierta(previo):
            return previo
        self.estado = previo
        self.cerrar()
        return None

    def ruta_abierta(self, previo):
        """Una ruta a medias no vence: esperar la voz o un 429 no es «sin actividad», y cerrar le quitaría el paquete a sus agentes."""
        try:
            ruta = json.loads((self.carpeta / 'ruta.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return False
        return ruta.get('fase') != 'terminada' and ruta.get('tarea') == previo.get('tarea')

    def cerrar(self):
        """Cierra la plática: deja su evidencia y avisa al marcador, que la califica una sola vez."""
        if self.estado.get('cerrada'):
            return
        tarea = self.estado['tarea']
        evidencia = self.base / 'registro/marcador-evidencia' / 'tarea-{}'.format(tarea)
        evidencia.mkdir(parents=True, exist_ok=True)
        (evidencia / 'CHAT.md').write_text(
            '\n'.join(self.lineas()[self.estado.get('linea_inicio', 0):]) + '\n', encoding='utf-8')
        trabajo = self.estado.get('carpeta')
        if trabajo and Path(trabajo).is_dir():
            entregables = evidencia / 'entregables'
            entregables.mkdir(exist_ok=True)
            for archivo in Path(trabajo).iterdir():
                if archivo.is_file() and archivo.suffix in ('.md', '.txt', '.html', '.srt', '.json'):
                    shutil.copy2(archivo, entregables / archivo.name)
        rubrica = self.estado.get('rubrica')
        if rubrica and Path(rubrica).is_file():
            shutil.copy2(rubrica, evidencia / 'RUBRICA.md')
        mensajes = self.estado.get('mensajes', {})
        evento = {'evento': 'sala', 'uuid': self.estado['uuid'], 'id_externo': 'sala:' + self.estado['uuid'],
                  'tarea': tarea, 'agentes': ','.join(sorted(mensajes)), 'mensajes': sum(mensajes.values()),
                  'rechazos': 0, 'banderas': 0, 'altos': 0, 'llamadas_vigilante': 0, 'forzada': 0}
        herramienta = marcador_sh()
        if herramienta:
            for paso in (['encolar', json.dumps(evento, ensure_ascii=False)], ['procesar']):
                ejecutar(['/bin/bash', str(herramienta)] + paso, self.vacia, os.environ.copy())
        try:
            if not self.estado.get('solo_paquete'):  # en la fábrica, lo de un video va al libro; no a la memoria del siguiente
                _cazafrases.correr(self.base, self.lineas()[self.estado.get('linea_inicio', 0):], self.miembros, tarea)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass  # la memoria es un extra: nunca impide cerrar la plática
        self.agregar('sistema', 'todos', 'plática cerrada (tarea {})'.format(tarea))
        self.estado['cerrada'] = True
        self.guardar_estado()

    def descartar(self):
        """Cierra la plática sin evidencia ni calificación: lo abandonado no cuenta como caso."""
        if self.estado.get('cerrada'):
            return
        self.agregar('sistema', 'todos', 'plática descartada (tarea {}, sin calificar)'.format(self.estado['tarea']))
        self.estado['cerrada'] = True
        self.guardar_estado()

    def crear_tarea(self):
        herramienta = marcador_sh()
        if herramienta is None:
            raise ValueError('no encontré marcador.sh')
        salida = ejecutar(['/bin/bash', str(herramienta), 'tarea', '--tipo', 'sin-tipo',
                           '--desc', 'teams:' + self.proyecto.name, '--proyecto', str(self.proyecto),
                           # Con sala, el marcador califica la plática una vez al cerrar, no turno por turno.
                           '--sala', self.estado['uuid']],
                          self.vacia, os.environ.copy())
        return int(salida.strip())

    def guardar_estado(self):
        with self.candado:
            temporal = self.carpeta / 'estado.tmp'
            temporal.write_text(json.dumps(self.estado, ensure_ascii=False), encoding='utf-8')
            temporal.replace(self.carpeta / 'estado.json')

    def lineas(self):
        with self.candado:
            return (self.carpeta / 'CHAT.md').read_text(encoding='utf-8').splitlines()

    def agregar(self, quien, para, texto):
        linea = '{} {}→{}: {}'.format(dt.datetime.now().strftime('%H:%M'), quien, para, plano(texto))
        with self.candado:
            for archivo, valor in (('CHAT.md', linea), ('huellas', huella(linea))):
                with (self.carpeta / archivo).open('a', encoding='utf-8') as stream:
                    stream.write(valor + '\n')
            for espejo in list(getattr(self, 'espejos', {}).values()):
                try:
                    with Path(espejo).open('a', encoding='utf-8') as stream:
                        stream.write(linea + '\n')
                except OSError:
                    pass  # el espejo es una ayuda para el trabajador, no detiene el canal
            estado = getattr(self, 'estado', None)
            if estado is not None:
                estado['actividad'] = time.time()
                if quien in self.miembros:
                    estado.setdefault('mensajes', {})[quien] = estado.get('mensajes', {}).get(quien, 0) + 1
        return linea

    def menciones(self, texto):
        return list(dict.fromkeys(n.lower() for n in re.findall(r'(?<![\w@])@([\w-]+)', texto)
                                  if n.lower() in self.miembros or n.lower() == 'didier'))

    def contexto(self):
        if self.estado.get('solo_paquete'):
            # Ruta de fábrica: los agentes reciben solo el paquete del video, no el CLAUDE.md ni el relevo del proyecto.
            return self.paquete()
        relevos = list((self.proyecto / 'Bases').glob('RELEVO-*.md'))
        ultimo = max(relevos, key=lambda p: (p.stat().st_mtime_ns, p.name)) if relevos else None
        partes = []
        for archivo in (self.proyecto / 'CLAUDE.md', ultimo):
            if archivo is None or not archivo.exists():
                continue
            fecha = archivo.stat().st_mtime_ns
            if archivo not in self.cache or self.cache[archivo][0] != fecha:
                with archivo.open(encoding='utf-8') as stream:
                    self.cache[archivo] = (fecha, stream.read(TOPE_CONTEXTO // 2))
            partes.append('{}:\n{}'.format(archivo.name, self.cache[archivo][1]))
        base = '\n\n'.join(partes)[:TOPE_CONTEXTO]
        extra = []
        for relativo in self.estado.get('procesos', []):
            archivo = self.proyecto / relativo
            if not archivo.is_file():
                continue
            fecha = archivo.stat().st_mtime_ns
            if archivo not in self.cache or self.cache[archivo][0] != fecha:
                with archivo.open(encoding='utf-8') as stream:
                    leido = stream.read(TOPE_PROCESO + 1)
                # Nada se corta en silencio (FALLA 37).
                aviso = ' …[cortado a {} caracteres]'.format(TOPE_PROCESO) if len(leido) > TOPE_PROCESO else ''
                self.cache[archivo] = (fecha, leido[:TOPE_PROCESO] + aviso)
            extra.append('Proceso del proyecto que aplica ({}):\n{}'.format(relativo, self.cache[archivo][1]))
        return '\n\n'.join([base] + extra) if extra else base

    def paquete(self):
        archivo = Path(self.estado.get('carpeta') or '.') / '00-PAQUETE.md'
        try:
            texto = archivo.read_text(encoding='utf-8')
        except OSError:
            return '(sin paquete: no hay {})'.format(archivo.name)
        return texto if len(texto) <= TOPE_CONTEXTO else texto[:TOPE_CONTEXTO] + ' …[cortado a {} caracteres]'.format(TOPE_CONTEXTO)

    def procesos(self):
        """equipo/PROCESOS.json: {"<proyecto>": [{"palabras": [...], "archivo": "<ruta relativa>"}]}."""
        try:
            datos = json.loads((self.base / 'equipo/PROCESOS.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return []
        return datos.get(self.proyecto.name, []) if isinstance(datos, dict) else []

    def detectar_procesos(self, mensaje):
        texto = mensaje.casefold()
        for proceso in self.procesos():
            archivo = proceso.get('archivo')
            if archivo and archivo not in self.estado['procesos'] and any(
                    re.search(r'\b' + re.escape(p.casefold()), texto) for p in proceso.get('palabras', [])):
                self.estado['procesos'].append(archivo)

    def prompt(self, nombre, motivo, tope=TOPE_RESPUESTA, quien_decide='didier'):
        carpeta = self.base / 'equipo' / nombre
        # Lo fijo (equipo, alma, memoria, proyecto) va en el prefijo para que la caché lo reaproveche
        # entre turnos; en el mensaje solo va lo que cambia (el canal y el motivo).
        memoria = (carpeta / 'MEMORIA.md').read_text(encoding='utf-8')
        if self.estado.get('solo_paquete'):
            # Cada video es suyo: los regaños y aprobaciones son de otro video («voltea ENERO»); solo viajan las lecciones de proceso.
            memoria = '\n'.join(l for l in memoria.splitlines() if not l.startswith('- ') or '| aprendizaje |' in l)
        sistema = '\n\n'.join([p.read_text(encoding='utf-8') for p in (self.base / 'equipo/EQUIPO.md', carpeta / 'ALMA.md')] +
                               [memoria, 'Proyecto: {}\n{}'.format(self.proyecto.name, self.contexto())])
        resto = ('Canal (últimas 40 líneas):\n{}\n\n'
                 'Eres {}: {}. Responde en texto plano, máximo {} caracteres, como en un chat, en español. '
                 'No uses herramientas. Menciona con @nombre solo si de verdad necesitas a alguien; '
                 '@{} si necesitas su respuesta.').format('\n'.join(self.lineas()[-40:]), nombre, motivo, tope,
                                                          quien_decide)
        return sistema, resto

    def codex_home(self):
        destino = self.base / 'registro/codex-equipo'
        destino.mkdir(parents=True, exist_ok=True)
        for nombre in ('auth.json', 'config.toml'):
            enlace = destino / nombre
            origen = Path.home() / '.codex' / nombre
            if not enlace.is_symlink() and not enlace.exists():
                enlace.symlink_to(origen)
        # Nunca se importa el AGENTS.md global al hogar del equipo.
        return destino

    def gasto_claude(self, nombre, config, datos, segundos):
        # Mismo protocolo de encolar_corrida; ese helper fija «opus», aquí el modelo es variable.
        uso = datos.get('usage') or {}
        evento = dict(evento='corrida', tarea=self.estado['tarea'], origen='teams',
                      id_externo=uuid.uuid4().hex, rol='ejecucion', agente=config['modelo'],
                      esfuerzo=config['esfuerzo'], cuenta='claude', segundos=segundos, exacto=1)
        for campo, clave in (('entrada', 'input_tokens'), ('salida', 'output_tokens'),
                             ('cache_leida', 'cache_read_input_tokens'),
                             ('cache_escrita', 'cache_creation_input_tokens')):
            evento['tokens_' + campo] = uso.get(clave)
        ejecutar(['/bin/bash', str(marcador_sh()), 'encolar', json.dumps(evento, ensure_ascii=False)],
                 self.vacia, os.environ.copy())

    def turno(self, nombre, motivo, tope=TOPE_RESPUESTA, quien_decide='didier'):
        config = self.miembros[nombre]
        sistema, resto = self.prompt(nombre, motivo, tope, quien_decide)
        env = {k: v for k, v in os.environ.items()
               if k != 'CLAUDECODE' and not k.startswith('CLAUDE_CODE_')}
        # No heredar la identidad de una sala o una tarea que abrió esta terminal.
        for clave in ('SALA_UUID', 'MARCADOR_TAREA'):
            env.pop(clave, None)
        motor, modelo, esfuerzo = config['motor'], config['modelo'], config['esfuerzo']
        inicio = time.monotonic()
        if motor == 'claude':
            comando = ['claude', '-p', '--safe-mode', '--no-session-persistence', '--tools', '',
                       '--model', modelo, '--effort', esfuerzo, '--output-format', 'json',
                       '--system-prompt', sistema, resto]
            datos = json.loads(ejecutar(comando, self.vacia, env))
            try:
                self.gasto_claude(nombre, config, datos, time.monotonic() - inicio)
            except (OSError, ValueError, subprocess.SubprocessError):
                self.agregar('sistema', 'todos', 'no se pudo anotar el gasto de ' + nombre)
            if datos.get('is_error'):
                raise ValueError(detalle_error('error del motor', '', json.dumps(datos)))
            respuesta = datos.get('result', '')
        else:
            with tempfile.TemporaryDirectory(prefix='teams-', dir=str(self.base / 'registro')) as temporal:
                archivo = Path(temporal) / 'respuesta'
                prompt = sistema + '\n\n' + resto
                comunes = ['--tarea', str(self.estado['tarea'])]
                if motor == 'codex':
                    env['CODEX_HOME'] = str(self.codex_home())
                    comando = ['/bin/bash', str(self.base / 'herramientas/codex.sh')] + comunes + [
                        '--', '--skip-git-repo-check', '-s', 'read-only', '-m', modelo,
                        '-c', 'model_reasoning_effort=' + json.dumps(esfuerzo), '-C', str(self.vacia),
                        '-o', str(archivo), prompt]
                else:
                    comando = ['/bin/bash', str(self.base / 'herramientas/argon.sh')] + comunes + [
                        '-o', str(archivo), '--', '-p', prompt, '--model', modelo]
                ejecutar(comando, self.vacia, env)
                respuesta = archivo.read_text(encoding='utf-8')
                if motor == 'agy':
                    datos = json.loads(respuesta)
                    if datos.get('is_error') or datos.get('error'):
                        raise ValueError(detalle_error('error del motor', '', json.dumps(datos)))
                    respuesta = datos.get('response', '')
        if not isinstance(respuesta, str) or not plano(respuesta):
            raise ValueError('respuesta vacía')
        # La ruta guarda los entregables con sus saltos de línea; el canal siempre recibe una sola línea.
        self.ultima_cruda = respuesta.strip()
        return plano(respuesta, tope)

    def recibir(self, mensaje):
        self.detectar_procesos(mensaje)
        self.pedidos_taller = 0
        carpeta = re.match(r'^/carpeta\s+(.+)$', mensaje.strip())
        if carpeta:
            self.estado['carpeta'] = str(Path(carpeta.group(1)).expanduser().resolve())
            self.guardar_estado()
        self.agregar('didier', ','.join(self.menciones(mensaje)) or 'todos', mensaje)

    def despachar(self, mensaje, taller=True, motivo='te mencionaron'):
        destinos = self.menciones(mensaje)
        pendientes = deque((n, motivo) for n in destinos if n != 'didier')
        todos = re.search(r'(?<![\w@])@todos\b', mensaje, re.I) is not None
        # Un mensaje que solo pide trabajo al taller no abre ronda de opinión.
        comando = re.match(r'^/carpeta\s', mensaje.strip()) is not None
        ronda = todos or not (destinos or comando or (taller and PEDIDO_TALLER.search(mensaje)))
        if ronda:
            # La ronda corta deja fuera a quien no suma en la opinión inicial; entra si alguien lo menciona.
            pendientes.extend((n, 'te toca opinar') for n, c in self.miembros.items()
                              if c.get('activo') and (todos or c.get('ronda', True)) and n not in destinos)
        self.estado['esperando_didier'] = False
        self.guardar_estado()
        if taller:
            self.pedir_taller('didier', mensaje)
        try:
            for _ in range(TOPE_TURNOS):
                if not pendientes:
                    break
                nombre, motivo = pendientes.popleft()
                self.escribiendo = nombre
                try:
                    respuesta = self.turno(nombre, motivo)
                    mencionados = self.menciones(respuesta)
                    self.agregar(nombre, ','.join(mencionados) or 'todos', respuesta)
                    self.pedir_taller(nombre, respuesta)
                    if 'didier' in mencionados:
                        self.estado['esperando_didier'] = True
                        # En una ronda de opinión, la pregunta a Didier espera a que todos opinen.
                        if not ronda:
                            break
                        mencionados = [m for m in mencionados if m != 'didier']
                    # Una mención pasa al frente; si ya esperaba su opinión, se convierte en respuesta.
                    for destino in reversed(mencionados):
                        pendientes = deque(p for p in pendientes if p[0] != destino)
                        pendientes.appendleft((destino, 'te mencionaron'))
                except Exception as error:
                    motivo = 'más de 120 s' if isinstance(error, subprocess.TimeoutExpired) else (
                        str(error) if isinstance(error, ValueError) else type(error).__name__)
                    self.agregar('sistema', 'todos', '{} no contestó ({})'.format(nombre, plano(motivo, 100)))
            else:
                if pendientes:
                    self.agregar('sistema', 'todos', 'tope de 8 turnos; esperando a Didier')
                    self.estado['esperando_didier'] = True
        finally:
            self.escribiendo = None
            self.guardar_estado()
            # Los turnos de Claude solo se encolan; si nadie procesa, el gasto no aparece hasta la siguiente corrida.
            herramienta = marcador_sh()
            if herramienta:
                subprocess.Popen(['/bin/bash', str(herramienta), 'procesar'], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


    # --- taller: trabajo con herramientas en segundo plano --------------------------------------

    def carpeta_trabajo(self):
        if not self.estado.get('carpeta'):
            self.estado['carpeta'] = str(self.carpeta / 'trabajo' / self.estado['uuid'][:8])
            self.guardar_estado()
        return Path(self.estado['carpeta'])

    def pedir_taller(self, quien, texto):
        for agente, instruccion in PEDIDO_TALLER.findall(texto):
            agente = agente.lower()
            if agente not in self.miembros:
                self.agregar('sistema', 'todos', 'taller: no conozco a ' + agente)
                continue
            if self.pedidos_taller >= TOPE_TALLER:
                self.agregar('sistema', 'todos', 'taller: tope de {} trabajos por mensaje de Didier'.format(TOPE_TALLER))
                return
            self.pedidos_taller += 1
            self.lanzar_taller(quien, agente, instruccion.strip())

    def lanzar_taller(self, quien, agente, instruccion):
        import _taller
        sistema, _ = self.prompt(agente, 'trabajas en el taller')
        trabajo = {'agente': agente, 'config': self.miembros[agente], 'sistema': sistema,
                   'contexto': self.contexto(), 'instruccion': instruccion,
                   'chat': '\n'.join(self.lineas()[self.estado.get('linea_inicio', 0):]),
                   'carpeta': self.carpeta_trabajo(), 'proyecto': self.proyecto,
                   'tarea': self.estado['tarea'], 'base': self.base, 'canal': self.carpeta / 'CHAT.md'}
        self.agregar('sistema', 'todos', '{} entra al taller ({}): {}'.format(agente, self.estado['carpeta'],
                                                                            plano(instruccion, 160)))
        self.espejos[agente] = Path(trabajo['carpeta']) / 'PLATICA.md'
        self.talleres.append(_taller.lanzar(trabajo, lambda texto: self.avisos.put((quien, agente, texto))))

    def taller_pendiente(self):
        self.talleres = [h for h in self.talleres if h.is_alive()]
        return bool(self.talleres) or not self.avisos.empty()

    def procesar_aviso(self, aviso):
        """El trabajo terminó: una línea al canal y se despierta quien lo pidió."""
        quien, agente, texto = aviso
        self.espejos.pop(agente, None)
        texto = plano(texto, TOPE_AVISO) or 'no terminó (respuesta vacía)'
        mencionados = self.menciones(texto)
        if texto.startswith('no terminó'):
            self.agregar('sistema', 'todos', '{} {}'.format(agente, texto))
        else:
            self.agregar(agente, ','.join(mencionados) or 'todos', '[taller] ' + texto)
        if 'didier' in mencionados or quien not in self.miembros:
            self.estado['esperando_didier'] = self.estado['esperando_didier'] or 'didier' in mencionados
            self.guardar_estado()
            return
        self.despachar('@' + quien, taller=False,
                       motivo='terminó el trabajo que le pediste a {} en el taller (lo ves en el canal); decide qué sigue'.format(agente))

    def esperar_taller(self):
        while self.taller_pendiente():
            try:
                self.procesar_aviso(self.avisos.get(timeout=1))
            except queue.Empty:
                continue


class Despachador:
    """Un hilo por plática: la entrada sigue disponible y las cadenas no se cruzan."""
    def __init__(self, canal):
        self.canal = canal
        self.cola = queue.Queue()
        self.hilo = threading.Thread(target=self.trabajar)
        self.hilo.start()

    def enviar(self, mensaje):
        self.canal.recibir(mensaje)
        self.cola.put(mensaje)

    def trabajar(self):
        while True:
            try:
                mensaje = self.cola.get(timeout=.5)
            except queue.Empty:
                # Entre mensajes, el mismo hilo atiende los avisos del taller: nunca dos cadenas a la vez.
                try:
                    self.canal.procesar_aviso(self.canal.avisos.get_nowait())
                except queue.Empty:
                    pass
                except Exception as error:
                    self.canal.agregar('sistema', 'todos', 'taller: ' + type(error).__name__)
                continue
            try:
                if mensaje is None:
                    return
                self.canal.despachar(mensaje)
            except Exception as error:
                self.canal.agregar('sistema', 'todos', 'despachador: ' + type(error).__name__)
            finally:
                self.cola.task_done()

    def cerrar(self):
        self.cola.put(None)
        self.hilo.join()


def dibujar(canal, entrada):
    columnas, filas = shutil.get_terminal_size((100, 30))
    ancho = max(1, columnas - 8)
    salida = []
    for linea in canal.lineas():
        m = re.match(r'^(\S+) (\S+?)→(\S+?): (.*)$', linea)
        if not m:
            continue
        hora, quien, para, texto = m.groups()
        color = canal.miembros.get(quien, {}).get('color') or COLORES[0]
        if not re.fullmatch(r'[\d;]+', color):
            color = '0'
        destino = '' if para == 'todos' else ' → ' + para
        salida.append('\033[2m{}\033[0m \033[1;{}m{}\033[0m\033[2m{}\033[0m'.format(
            hora, color, quien, destino))
        salida.extend('      \033[{}m{}\033[0m'.format(color, parte)
                      for parte in textwrap.wrap(texto, ancho) or [''])
        salida.append('')
    estado = canal.escribiendo + ' está escribiendo…' if canal.escribiendo else (
        'Esperando a Didier' if canal.estado['esperando_didier'] else 'Listo')
    cabecera = 'Humanify · {} · tarea {} · /cerrar · /proyecto · /salir'.format(canal.proyecto.name, canal.estado['tarea'])
    pantalla = [cabecera[:columnas]] + salida[-max(1, filas - 5):] + [estado[:columnas], 'Didier › ' + entrada]
    sys.stdout.write('\033[H\033[2J' + '\n'.join(pantalla))
    sys.stdout.flush()


def conversar(canal):
    despachador = Despachador(canal)
    try:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            for linea in sys.stdin:
                mensaje = linea.strip()
                if mensaje in ('/salir', '/proyecto', '/cerrar'):
                    return mensaje
                if mensaje:
                    despachador.enviar(mensaje)
            return '/salir'
        import termios
        import tty
        fd = sys.stdin.fileno()
        previo = termios.tcgetattr(fd)
        entrada = ''
        decoder = codecs.getincrementaldecoder('utf-8')()
        sys.stdout.write('\033[?1049h')
        try:
            tty.setcbreak(fd)
            while True:
                dibujar(canal, entrada)
                if not select.select([fd], [], [], .1)[0]:
                    continue
                for tecla in decoder.decode(os.read(fd, 1024)):
                    if tecla in ('\x04', '\x03'):
                        return '/salir'
                    if tecla in ('\r', '\n'):
                        mensaje, entrada = entrada.strip(), ''
                        if mensaje in ('/salir', '/proyecto', '/cerrar'):
                            return mensaje
                        if mensaje:
                            despachador.enviar(mensaje)
                    elif tecla in ('\x7f', '\b'):
                        entrada = entrada[:-1]
                    elif tecla.isprintable():
                        entrada += tecla
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, previo)
            sys.stdout.write('\033[?1049l')
            sys.stdout.flush()
    finally:
        # Salir o cambiar de proyecto espera los mensajes aceptados, sin perder su gasto.
        despachador.cerrar()


def seleccionar():
    carpetas = proyectos()
    for numero, carpeta in enumerate(carpetas, 1):
        print('{} · {}'.format(numero, carpeta.name))
    if not carpetas:
        raise ValueError('no hay proyectos')
    while True:
        opcion = input('Proyecto (número o nombre; /salir): ').strip()
        if opcion == '/salir':
            return None
        for numero, carpeta in enumerate(carpetas, 1):
            if opcion in (str(numero), carpeta.name):
                return carpeta
        print('Proyecto no encontrado')


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ['ruta']:
        import _ruta
        return _ruta.main(argv[1:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--una', metavar='MENSAJE')
    parser.add_argument('--proyecto')
    parser.add_argument('--cerrar', action='store_true', help='cierra la plática abierta del proyecto')
    args = parser.parse_args(argv)
    if (args.una is not None or args.cerrar) and not args.proyecto:
        parser.error('--una y --cerrar necesitan --proyecto')
    try:
        proyecto = proyecto_por_nombre(args.proyecto) if args.proyecto else seleccionar()
        if args.proyecto and proyecto is None:
            raise ValueError('proyecto no encontrado: ' + args.proyecto)
        while proyecto is not None:
            if args.cerrar:
                previo = raiz() / 'registro/canales' / proyecto.name / 'estado.json'
                # Sin plática abierta no se crea una tarea vacía solo para cerrarla.
                if not previo.exists() or json.loads(previo.read_text(encoding='utf-8')).get('cerrada'):
                    print('No hay plática abierta en ' + proyecto.name)
                    return 0
            canal = Canal(proyecto)
            if args.una is not None:
                inicio = len(canal.lineas())
                canal.recibir(args.una)
                canal.despachar(args.una)
                canal.esperar_taller()
                print('\n'.join(canal.lineas()[inicio:]))
                return 0
            if args.cerrar:
                canal.cerrar()
                print(canal.lineas()[-1])
                return 0
            accion = conversar(canal)
            if accion == '/cerrar':
                canal.cerrar()
            if accion in ('/salir', '/cerrar'):
                return 0
            proyecto = seleccionar()
    except (EOFError, KeyboardInterrupt):
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print('Humanify: ' + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
