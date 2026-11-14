#!/usr/bin/python3
"""Salas temporales de Humanify (diseño v2.1, sección C). Solo biblioteca estándar."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import textwrap
import time
import uuid as uuidlib

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _humanify as H  # noqa: E402

LARGO = 200
TOPE_MENSAJES = 12
RONDAS = 2
VENCE = {'planes': 600, 'ronda': 150, 'revision': 180}
PRESUPUESTO = 4
FALLOS_VIGILANTE = 2  # fallas seguidas que apagan al vigilante; la sala sigue sin él
# Tope de esperar y escuchar. agy suelta el turno con todo comando de más de unos 6-20 s (lo manda a segundo
# plano y al terminar cierra la corrida): a sus agentes se les lanza con SALA_ESPERA=6.
ESPERA = int(os.environ.get('SALA_ESPERA') or 25)
MODOS = ('fases', 'platica')
MARGEN_ALTO = 120
QUIETA_BARRIDO = 3 * 3600
ROLES = ('director', 'par', 'apoyo')
ESQUEMA_VIGILANTE = {
    'type': 'object',
    'properties': {'veredicto': {'enum': ['OK', 'ALTO']}, 'motivo': {'type': 'string'},
                   'a_quien': {'type': 'string'}},
    'required': ['veredicto', 'motivo'],
}
ESQUEMA_RESCATE = {
    'type': 'object',
    'properties': {'guardar': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 3}},
    'required': ['guardar'],
}


class Rechazo(Exception):
    """Un comando de la sala que no procede; el motivo se le dice al agente."""


def ahora():
    return time.time()


def hora(segundos=None):
    return dt.datetime.fromtimestamp(segundos or ahora()).strftime('%H:%M')


def base():
    return H.reg_path('salas')


def carpeta(uid):
    if not re.fullmatch(r'[0-9a-f]{32}', uid or ''):
        raise Rechazo('uuid de sala inválido')
    return H.reg_path('salas', uid)


def leer_estado(uid):
    path = carpeta(uid) / 'estado.json'
    if not path.exists():
        raise Rechazo('no existe la sala ' + uid)
    return H.read_json(path)


def guardar_estado(est):
    est['version'] += 1
    H.atomic_json(carpeta(est['uuid']) / 'estado.json', est)


def con_sala(uid, funcion):
    """Lee, modifica y guarda el estado bajo el candado de la sala; el candado nunca se tiene esperando."""
    with H.lock(carpeta(uid) / '.candado'):
        est = leer_estado(uid)
        cambio = tick(est)
        try:
            resultado = funcion(est)
        except Rechazo:
            # Un rechazo también deja rastro (contador, banderas) antes de avisarle al agente.
            if cambio or est.get('_sucio'):
                est.pop('_sucio', None)
                guardar_estado(est)
            raise
        # Un segundo tick deja ver de inmediato la transición que causó este comando (el último plan, el último listo).
        cambio = tick(est) or cambio
        if cambio or resultado is not None or est.get('_sucio'):
            est.pop('_sucio', None)
            guardar_estado(est)
        return est, resultado


def huella(linea):
    return hashlib.sha256(linea.encode('utf-8')).hexdigest()[:16]


def agregar_chat(est, linea):
    folder = carpeta(est['uuid'])
    with (folder / 'CHAT.md').open('a', encoding='utf-8') as stream:
        stream.write(linea + '\n')
    with (folder / 'huellas').open('a', encoding='utf-8') as stream:
        stream.write(huella(linea) + '\n')
    est['actividad'] = ahora()
    copiar_chat(est)


def copiar_chat(est):
    destino = Path(est['trabajo'])
    try:
        destino.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(str(carpeta(est['uuid']) / 'CHAT.md'), str(destino / 'CHAT-copia.md'))
    except OSError:
        pass


def lineas_chat(uid):
    path = carpeta(uid) / 'CHAT.md'
    return path.read_text(encoding='utf-8').splitlines() if path.exists() else []


def revisar_huellas(est):
    """Detectar, no blindar: una línea sin huella o unas reglas cambiadas son bandera."""
    folder = carpeta(est['uuid'])
    conocidas = set((folder / 'huellas').read_text(encoding='utf-8').split()) if (folder / 'huellas').exists() else set()
    ajenas = [l for l in lineas_chat(est['uuid']) if l.strip() and huella(l) not in conocidas]
    if ajenas and 'linea-ajena' not in est['vistas']:
        bandera(est, 'linea-ajena', 'línea escrita fuera de sala.sh: ' + ajenas[0][:80])
    reglas = (folder / 'REGLAS.md').read_text(encoding='utf-8') if (folder / 'REGLAS.md').exists() else ''
    if huella(reglas) != est['huella_reglas'] and 'reglas-cambiadas' not in est['vistas']:
        bandera(est, 'reglas-cambiadas', 'REGLAS.md cambió desde que se abrió la sala')


def bandera(est, clave, texto):
    est['vistas'].append(clave)
    est['banderas'].append({'clave': clave, 'texto': texto[:200], 'hora': ahora(), 'version': est['version']})
    est['_sucio'] = True


def activos(est):
    return [a for a, p in est['participantes'].items() if p['estado'] == 'activo']


def cambiar_fase(est, fase):
    est['fase'], est['fase_desde'] = fase, ahora()
    est['_sucio'] = True


def tick(est):
    """Avanza las fases por tiempo o por condición. Devuelve True si cambió algo."""
    if est['fase'] in ('detenida', 'cerrando', 'cerrada'):
        return False
    antes = (est['fase'], est.get('ronda'), len(est['banderas']))
    revisar_huellas(est)
    edad = ahora() - est['fase_desde']
    part = est['participantes']
    if est['fase'] == 'planes':
        faltan = [a for a in activos(est) if not part[a].get('plan')]
        if not faltan:
            iniciar_arranque(est)
        elif edad > VENCE['planes']:
            for a in faltan:
                part[a]['estado'] = 'ausente'
                bandera(est, 'ausente-' + a, a + ' no entregó plan a tiempo')
            iniciar_arranque(est)
    elif est['fase'] == 'arranque':
        escribieron = [a for a in activos(est) if part[a].get('ronda_escribio') == est['ronda']]
        listos = [a for a in activos(est) if part[a].get('listo')]
        if activos(est) and len(listos) == len(activos(est)):
            iniciar_revision(est)
        elif len(escribieron) == len(activos(est)) or ahora() - est['ronda_desde'] > VENCE['ronda']:
            if est['ronda'] >= RONDAS:
                iniciar_revision(est)
            else:
                est['ronda'] += 1
                est['ronda_desde'] = ahora()
                est['_sucio'] = True
    elif est['fase'] == 'revision':
        vig = est['vigilante']
        resultado = vig.get('ultimo')
        if vig.get('apagado'):
            cambiar_fase(est, 'trabajo')
        elif vig.get('necesita_arranque'):
            pedir_vigilancia(est, 'arranque', obligatoria=True)
            edad = ahora() - (vig.get('arranque_pedido') or est['fase_desde'])
        else:
            edad = ahora() - (vig.get('arranque_pedido') or est['fase_desde'])
        if resultado and resultado.get('motivo_llamada') == 'arranque':
            if resultado['veredicto'] == 'ALTO':
                detener(est, 'vigilante: ' + resultado.get('motivo', ''))
            else:
                cambiar_fase(est, 'trabajo')
        elif edad > VENCE['revision']:
            bandera(est, 'sin-vigilancia', 'la revisión de arranque venció o falló; se sigue sin vigilancia')
            cambiar_fase(est, 'trabajo')
    elif est['fase'] == 'trabajo':
        pendientes = [b for b in est['banderas'] if not b.get('enviada')]
        if est['mensajes_desde_revision'] >= 8 or pendientes:
            pedir_vigilancia(est, 'banderas' if pendientes else 'cada-8')
    return antes != (est['fase'], est.get('ronda'), len(est['banderas']))


def iniciar_arranque(est):
    cambiar_fase(est, 'arranque')
    est['ronda'], est['ronda_desde'] = 1, ahora()
    pedir_vigilancia(est, 'planes')


def iniciar_revision(est):
    cambiar_fase(est, 'revision')
    est['vigilante']['necesita_arranque'] = True
    pedir_vigilancia(est, 'arranque', obligatoria=True)


def en_sandbox():
    """Un proceso dentro del sandbox de Codex no tiene red: el vigilante que lance ahí falla en segundos."""
    if os.environ.get('SALA_SIN_FONDO'):
        return False  # pruebas: no se lanza nada en segundo plano
    return bool(os.environ.get('CODEX_SANDBOX') or os.environ.get('CODEX_SANDBOX_NETWORK_DISABLED'))


def pedir_vigilancia(est, motivo, obligatoria=False):
    vig = est['vigilante']
    if vig.get('apagado'):
        for b in est['banderas']:
            b['enviada'] = True
        est['mensajes_desde_revision'] = 0
        return
    if vig.get('activa') and ahora() - vig['activa'] < VENCE['revision']:
        return  # una revisión activa a la vez; la obligatoria se vuelve a pedir en el siguiente tick
    if en_sandbox():
        return  # sin tocar nada: la pide el siguiente comando que corra fuera del sandbox
    if motivo == 'arranque':
        vig['necesita_arranque'] = False
        vig['arranque_pedido'] = ahora()
    for b in est['banderas']:
        b['enviada'] = True
    est['mensajes_desde_revision'] = 0
    if vig['usadas'] >= est.get('presupuesto', PRESUPUESTO):
        if obligatoria or motivo == 'banderas':
            detener(est, 'presupuesto del vigilante agotado; decide el orquestador')
        return
    vig['usadas'] += 1
    vig['activa'] = ahora()
    est['_sucio'] = True
    # La "versión" que ve el vigilante es el largo del chat: leer no la mueve, escribir sí.
    lanzar_fondo([sys.executable, str(Path(__file__).resolve()), '_vigilar', est['uuid'],
                  str(len(lineas_chat(est['uuid']))), motivo])


def lanzar_fondo(comando, env=None):
    if os.environ.get('SALA_SIN_FONDO'):
        return None
    log = open(os.devnull, 'wb')
    return subprocess.Popen(comando, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                            start_new_session=True, env=env or os.environ.copy())


def detener(est, motivo):
    if est['fase'] in ('detenida', 'cerrando', 'cerrada'):
        return
    est['fase_anterior'] = est['fase']
    cambiar_fase(est, 'detenida')
    est['motivo_alto'] = motivo[:200]
    est['altos'] += 1
    agregar_chat(est, '{} sala→todos: ALTO: {}'.format(hora(), motivo[:180]))
    lanzar_fondo([sys.executable, str(Path(__file__).resolve()), '_matar', est['uuid']])


# --- procesos -------------------------------------------------------------

def inicio_pid(pid):
    try:
        salida = subprocess.run(['ps', '-o', 'lstart=', '-p', str(pid)], capture_output=True, text=True, timeout=2)
        return salida.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def vivo(participante):
    pid = participante.get('pid')
    return bool(pid) and inicio_pid(pid) == participante.get('inicio')


def matar(uid):
    time.sleep(0 if os.environ.get('SALA_SIN_FONDO') else MARGEN_ALTO)
    for senal, espera in ((signal.SIGTERM, 30), (signal.SIGKILL, 0)):
        quedan = 0
        for p in leer_estado(uid)['participantes'].values():
            if vivo(p):
                quedan += 1
                try:
                    os.killpg(p['pid'], senal)
                except OSError:
                    pass
        if not quedan:
            return
        time.sleep(0 if os.environ.get('SALA_SIN_FONDO') else espera)


# --- comandos -------------------------------------------------------------

def plantilla_reglas(modo='fases'):
    nombre = 'sala-REGLAS-platica.md' if modo == 'platica' else 'sala-REGLAS.md'
    path = H.HOME_DIR / 'instalacion/plantillas' / nombre
    return path.read_text(encoding='utf-8') if path.exists() else '# Reglas de la sala\n'


def permitidos(est, agente):
    rol = est['participantes'][agente]['rol']
    otros = [a for a in est['participantes'] if a != agente]
    if rol == 'apoyo':
        return ['orquestador', 'todos']
    return ['orquestador', 'todos'] + otros


def entero(valor, nombre, minimo, maximo):
    try:
        valor = int(valor)
    except (TypeError, ValueError):
        raise Rechazo('{} debe ser un entero de {} a {}'.format(nombre, minimo, maximo))
    if not minimo <= valor <= maximo:
        raise Rechazo('{} debe estar entre {} y {}'.format(nombre, minimo, maximo))
    return valor


def cmd_abrir(nombre, agentes, tipo, riesgo, criterio, largo=LARGO, modo='fases', mensajes=TOPE_MENSAJES,
              presupuesto=PRESUPUESTO):
    try:
        largo = int(largo)
    except (TypeError, ValueError):
        raise Rechazo('--largo debe ser un entero de 80 a 2000 caracteres')
    if not 80 <= largo <= 2000:
        raise Rechazo('--largo debe estar entre 80 y 2000 caracteres')
    if modo not in MODOS:
        raise Rechazo('--modo debe ser ' + ' o '.join(MODOS))
    mensajes = entero(mensajes, '--mensajes', 1, 100)
    presupuesto = entero(presupuesto, '--vigilante', 0, 12)
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,40}', nombre):
        raise Rechazo('nombre inválido: minúsculas, números y guiones')
    participantes = {}
    for item in agentes.split(','):
        agente, _, rol = item.strip().partition(':')
        rol = rol or 'par'
        if not re.fullmatch(r'[a-z0-9-]{1,20}', agente) or rol not in ROLES or agente in ('todos', 'orquestador', 'sala'):
            raise Rechazo('agente o rol inválido: ' + item)
        participantes[agente] = {'rol': rol, 'estado': 'activo', 'mensajes': 0, 'rechazos': 0, 'leido': 0}
    if len(participantes) < 2:
        raise Rechazo('una sala necesita al menos dos agentes')
    raiz, _, folder = H.project(os.getcwd())
    proyecto = folder or Path(os.getcwd()).resolve()
    uid = uuidlib.uuid4().hex
    tarea = crear_tarea(tipo, riesgo, criterio, nombre, uid, raiz)
    est = {
        'uuid': uid, 'nombre': nombre, 'version': 0, 'creada': ahora(), 'actividad': ahora(),
        'dueno': os.environ.get('CLAUDE_CODE_SESSION_ID') or os.environ.get('CLAUDE_SESSION_ID', ''), 'proyecto': str(proyecto), 'raiz': raiz,
        'trabajo': str(proyecto / 'salas' / nombre), 'tipo': tipo, 'riesgo': riesgo, 'tarea': tarea,
        # En plática no hay planes ni arranque: se entra directo a conversar.
        'fase': 'trabajo' if modo == 'platica' else 'planes', 'fase_desde': ahora(), 'fase_anterior': None,
        'ronda': 0, 'ronda_desde': 0,
        'participantes': participantes, 'banderas': [], 'vistas': [], 'altos': 0, 'motivo_alto': '',
        'mensajes_desde_revision': 0, 'forzada': False, 'largo': largo,
        'modo': modo, 'tope_mensajes': mensajes, 'presupuesto': presupuesto,
        'vigilante': {'usadas': 0, 'activa': None, 'ultimo': None, 'apagado': presupuesto == 0},
    }
    folder = carpeta(uid)
    folder.mkdir(parents=True)
    tabla = '\n'.join('- {} ({}): puede escribir a {}'.format(a, p['rol'], ', '.join(permitidos(est, a)))
                      for a, p in participantes.items())
    reglas = (plantilla_reglas(modo).replace('{{PARTICIPANTES}}', tabla).replace('{{UUID}}', uid)
              .replace('{{LARGO}}', str(largo)).replace('{{MENSAJES}}', str(mensajes)))
    (folder / 'REGLAS.md').write_text(reglas, encoding='utf-8')
    est['huella_reglas'] = huella(reglas)
    (folder / 'CHAT.md').write_text('', encoding='utf-8')
    (folder / 'huellas').write_text('', encoding='utf-8')
    Path(est['trabajo']).mkdir(parents=True, exist_ok=True)
    H.atomic_json(folder / 'estado.json', est)
    return 'sala {} · uuid {} · tarea {}\nencabezado para cada agente: {}'.format(
        nombre, uid, tarea if tarea is not None else 'sin marcador', folder / 'REGLAS.md')


def marcador_sh():
    path = H.HOME_DIR / 'herramientas/marcador.sh'
    return path if path.exists() else None


def crear_tarea(tipo, riesgo, criterio, nombre, uid, raiz):
    herramienta = marcador_sh()
    if not herramienta or os.environ.get('SALA_SIN_MARCADOR'):
        return None
    comando = ['/bin/bash', str(herramienta), 'tarea', '--tipo', tipo, '--desc', 'sala ' + nombre, '--sala', uid]
    if riesgo == 'caro':
        comando += ['--riesgo', 'caro']
    if criterio:
        comando += ['--criterio', criterio]
    if raiz:
        comando += ['--proyecto', raiz]
    salida = subprocess.run(comando, capture_output=True, text=True, timeout=10)
    numero = re.search(r'\d+', salida.stdout or '')
    return int(numero.group()) if salida.returncode == 0 and numero else None


def exigir(est, agente, fases=None):
    if est['fase'] == 'detenida':
        raise Rechazo('ALTO: {}. Termina y reporta.'.format(est['motivo_alto']))
    if est['fase'] in ('cerrando', 'cerrada'):
        raise Rechazo('sala cerrándose; termina y reporta')
    if agente not in est['participantes']:
        raise Rechazo('no eres participante de esta sala: ' + agente)
    if fases and est['fase'] not in fases:
        raise Rechazo('fuera de fase: la sala está en ' + est['fase'])


def rechazar(est, agente, motivo):
    p = est['participantes'].get(agente)
    if p:
        p['rechazos'] += 1
        if p['rechazos'] in (3, 6):
            bandera(est, 'rechazos-{}-{}'.format(agente, p['rechazos']), agente + ' acumula rechazos')
        est['_sucio'] = True
    raise Rechazo(motivo)


def cmd_lanzar(uid, agente, comando):
    def registrar(est):
        exigir(est, agente)
        return True
    est, _ = con_sala(uid, registrar)
    env = dict(os.environ, SALA_UUID=uid, SALA_AGENTE=agente)
    if est.get('tarea') is not None:
        env['MARCADOR_TAREA'] = str(est['tarea'])
    log = (carpeta(uid) / (agente + '.log')).open('ab')
    proceso = subprocess.Popen(comando, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                               start_new_session=True, env=env)
    inicio = None
    for _ in range(20):
        inicio = inicio_pid(proceso.pid)
        if inicio:
            break
        time.sleep(0.05)

    def guardar(est):
        est['participantes'][agente].update(pid=proceso.pid, inicio=inicio)
        return True
    con_sala(uid, guardar)
    print('lanzado {} · pid {}'.format(agente, proceso.pid), flush=True)
    return proceso.wait()


def cmd_unir(uid, agente, subagente):
    def unir(est):
        exigir(est, agente)
        est['participantes'][agente]['subagente'] = subagente
        return True
    con_sala(uid, unir)
    return agente + ' unido como subagente ' + subagente


def cmd_plan(uid, agente, texto):
    lineas = [l for l in texto.strip().splitlines() if l.strip()]

    def plan(est):
        exigir(est, agente, ('planes',))
        largo = est.get('largo', LARGO)
        if not lineas or len(lineas) > 5 or any(len(l) > largo for l in lineas):
            raise Rechazo('el plan son de 1 a 5 líneas de hasta {} caracteres'.format(largo))
        est['participantes'][agente]['plan'] = '\n'.join(lineas)
        (Path(est['trabajo']) / ('plan-' + agente + '.md')).write_text('\n'.join(lineas) + '\n', encoding='utf-8')
        agregar_chat(est, '{} {}→todos: plan entregado ({} líneas)'.format(hora(), agente, len(lineas)))
        return True
    est, _ = con_sala(uid, plan)
    return 'plan guardado · fase ' + est['fase']


ORDEN = ('planes', 'arranque', 'revision', 'trabajo')


def cmd_esperar(uid, agente, fase, tope=ESPERA):
    if fase not in ORDEN:
        raise Rechazo('fase desconocida: ' + fase)
    limite = ahora() + tope
    while True:
        est, _ = con_sala(uid, lambda e: None)
        exigir(est, agente)
        if ORDEN.index(est['fase']) >= ORDEN.index(fase):
            return 'fase ' + est['fase'] + ('' if est['fase'] != 'arranque' else ' · ronda ' + str(est['ronda']))
        if ahora() > limite:
            # Volver pronto y sin error: una espera larga la manda agy a segundo plano y se cuelga.
            return 'todavía en {}; vuelve a correr esperar'.format(est['fase'])
        time.sleep(2)


def cmd_escuchar(uid, agente, tope=ESPERA):
    """Espera hasta que haya algo nuevo para el agente y se lo entrega; así nadie relee el chat a lo tonto."""
    limite = ahora() + tope
    while True:
        est, _ = con_sala(uid, lambda e: None)
        exigir(est, agente)
        if len(lineas_chat(uid)) > est['participantes'][agente]['leido']:
            return cmd_leer(uid, agente)
        if ahora() > limite:
            return 'fase {} · nada nuevo todavía; vuelve a escuchar'.format(est['fase'])
        time.sleep(2)


def cmd_leer(uid, agente):
    def leer(est):
        exigir(est, agente)
        p = est['participantes'][agente]
        todas = lineas_chat(uid)
        nuevas = todas[p['leido']:]
        p['leido'] = len(todas)
        planes = []
        if not p.get('vio_planes') and est['fase'] != 'planes':
            p['vio_planes'] = True
            planes = ['plan de {}: {}'.format(a, q['plan'].replace('\n', ' | '))
                      for a, q in est['participantes'].items() if q.get('plan') and a != agente]
        return planes + nuevas[-20:]
    est, salida = con_sala(uid, leer)
    cabeza = 'fase {}{}'.format(est['fase'], ' · ronda ' + str(est['ronda']) if est['fase'] == 'arranque' else '')
    return '\n'.join([cabeza] + salida) if salida else cabeza + ' · nada nuevo'


def cmd_decir_orquestador(uid, para, texto):
    """El orquestador (o Didier a través de él) entra a la plática sin topes ni fases."""
    def decir(est):
        if est['fase'] in ('cerrando', 'cerrada'):
            raise Rechazo('sala cerrándose')
        if not texto:
            raise Rechazo('mensaje vacío')
        if para != 'todos' and para not in est['participantes']:
            raise Rechazo('destinatario desconocido: ' + para)
        agregar_chat(est, '{} orquestador→{}: {}'.format(hora(), para, texto))
        return True
    con_sala(uid, decir)
    return 'enviado'


def cmd_decir(uid, agente, para, texto):
    texto = ' '.join(texto.split())
    if agente == 'orquestador':
        return cmd_decir_orquestador(uid, para, texto)

    def decir(est):
        exigir(est, agente, ('arranque', 'trabajo'))
        p = est['participantes'][agente]
        if p['estado'] != 'activo':
            rechazar(est, agente, 'ya no estás activo en la sala')
        if not texto:
            rechazar(est, agente, 'mensaje vacío')
        largo = est.get('largo', LARGO)
        if len(texto) > largo:
            rechazar(est, agente, 'mensaje de {} caracteres; el tope es {}'.format(len(texto), largo))
        if para not in permitidos(est, agente):
            rechazar(est, agente, 'no puedes escribirle a {}; permitido: {}'.format(para, ', '.join(permitidos(est, agente))))
        tope = est.get('tope_mensajes', TOPE_MENSAJES)
        if p['mensajes'] >= tope:
            bandera(est, 'tope-' + agente, agente + ' llegó al tope de mensajes')
            rechazar(est, agente, 'llegaste al tope de {} mensajes; termina tu parte'.format(tope))
        if est['fase'] == 'arranque':
            if p.get('ronda_escribio') == est['ronda']:
                rechazar(est, agente, 'ya escribiste en esta ronda; espera la siguiente')
            p['ronda_escribio'] = est['ronda']
            if re.match(r'listo\b', texto, re.I):
                p['listo'] = True
        elif est.get('modo') != 'platica' and not re.match(r'(avance|duda|bloqueo):', texto, re.I):
            rechazar(est, agente, 'en trabajo empieza con avance:, duda: o bloqueo:')
        previos = []
        for linea in lineas_chat(uid):
            mensaje = re.match(r'^\S+ ' + re.escape(agente) + r'→[^:]+: (.*)$', linea)
            if mensaje and not re.match(r'^(listo\b|plan entregado\b|GUARDAR:|FIN:|ALTO:)', mensaje[1], re.I):
                previos.append(mensaje[1].lower())
        if not re.match(r'^(listo\b|plan entregado\b|GUARDAR:|FIN:|ALTO:)', texto, re.I) and texto.lower() in previos[-30:]:
            bandera(est, 'repite-{}-{}'.format(agente, p['mensajes']), agente + ' repitió un mensaje')
        if texto.lower().startswith('duda:'):
            est.setdefault('dudas', []).append({'de': agente, 'en': len(lineas_chat(uid))})
        p['mensajes'] += 1
        est['mensajes_desde_revision'] += 1
        agregar_chat(est, '{} {}→{}: {}'.format(hora(), agente, para, texto))
        dudas_sin_respuesta(est)
        return True
    est, _ = con_sala(uid, decir)
    return 'enviado · fase ' + est['fase']


def dudas_sin_respuesta(est):
    total = len(lineas_chat(est['uuid']))
    for duda in est.get('dudas', []):
        if duda.get('marcada'):
            continue
        despues = lineas_chat(est['uuid'])[duda['en'] + 1:]
        if any('→' + duda['de'] in l for l in despues):
            duda['marcada'] = True
        elif total - duda['en'] > 2:
            duda['marcada'] = True
            bandera(est, 'duda-{}-{}'.format(duda['de'], duda['en']), 'duda de {} sin respuesta'.format(duda['de']))


def desglosar_guardar(guardar):
    guardar = ' '.join(guardar.split())
    if guardar.upper().startswith('GUARDAR:'):
        guardar = guardar[len('GUARDAR:'):].strip()
    partes = re.fullmatch(r'(.+?)\s+·\s+cambia:\s*(.*?)\s+·\s+base:\s*(\S+)', guardar)
    if not partes:
        raise ValueError('usa GUARDAR: <texto> · cambia: <qué harás distinto, al menos 4 palabras> · base: hecho|inferencia')
    texto, cambia, base_candidato = partes.groups()
    if base_candidato == 'didier':
        raise ValueError('en sala solo hablan agentes; usa base: hecho o inferencia sin atribuir nada a Didier')
    return texto, cambia, base_candidato


def validar_guardar(est, guardar, previos):
    texto, cambia, base_candidato = desglosar_guardar(guardar)
    H.validar_candidato(texto, base_candidato, '', cambia, None, destino_aprendizajes(est), previos)
    return '{} · cambia: {} · base: {}'.format(texto, cambia, base_candidato)


def cmd_fin(uid, agente, guardar):
    def fin(est):
        if est['fase'] in ('cerrando', 'cerrada'):
            raise Rechazo('sala cerrándose')
        if agente not in est['participantes']:
            raise Rechazo('no eres participante de esta sala: ' + agente)
        aceptados, descartados = [], []
        previos = [g for _, g in guardados(uid)]
        for g in guardar:
            try:
                valido = validar_guardar(est, g, previos + aceptados)
                aceptados.append(valido)
            except ValueError as exc:
                if est['fase'] != 'detenida':
                    raise Rechazo('GUARDAR rechazado: ' + str(exc))
                descartados.append(str(exc))
        for g in aceptados:
            agregar_chat(est, '{} {}→todos: GUARDAR: {}'.format(hora(), agente, g))
        for motivo in descartados:
            agregar_chat(est, '{} {}→todos: GUARDAR descartado: {}'.format(hora(), agente, motivo))
        agregar_chat(est, '{} {}→todos: FIN: {}'.format(hora(), agente, agente))
        est['participantes'][agente]['estado'] = 'terminado'
        return True
    con_sala(uid, fin)
    return 'FIN registrado para ' + agente


def cmd_alto(uid, motivo):
    def alto(est):
        detener(est, motivo or 'orquestador')
        return True
    con_sala(uid, alto)
    return 'sala detenida'


def cmd_estado(uid):
    est, _ = con_sala(uid, lambda e: None)
    part = ', '.join('{}:{}'.format(a, p['estado']) for a, p in est['participantes'].items())
    vig = est['vigilante']
    return '{} · fase {} · {} · banderas {} · vigilante {}/{}{} · largo {}{}'.format(
        est['nombre'], est['fase'], part, len(est['banderas']), vig['usadas'], est.get('presupuesto', PRESUPUESTO),
        ' apagado' if vig.get('apagado') else '', est.get('largo', LARGO),
        ' · ALTO: ' + est['motivo_alto'] if est['fase'] == 'detenida' else '')


COLORES = ('38;5;209', '38;5;75', '38;5;114', '38;5;221', '38;5;177', '38;5;80', '38;5;216', '38;5;147')


def buscar_sala(clave):
    if re.fullmatch(r'[0-9a-f]{32}', clave):
        return clave
    if base().exists():
        for folder in sorted(base().iterdir(), key=lambda f: f.stat().st_mtime, reverse=True):
            try:
                if H.read_json(folder / 'estado.json').get('nombre') == clave:
                    return folder.name
            except (OSError, ValueError):
                continue
    raise Rechazo('no encontré la sala ' + clave)


def cmd_ver(clave):
    """Visor en vivo para Didier: el chat de la sala a color, conforme llega. Solo lee; Ctrl-C para salir."""
    uid = buscar_sala(clave)
    est = leer_estado(uid)
    color = {a: COLORES[i % len(COLORES)] for i, a in enumerate(est['participantes'])}
    color.update(orquestador='1;37', sala='1;31')
    ancho = max(40, shutil.get_terminal_size((100, 40)).columns - 12)
    print('\033[1m{}\033[0m · {}\n'.format(est['nombre'], ', '.join(est['participantes'])), flush=True)
    vistas, fase = 0, None
    try:
        while True:
            lineas = lineas_chat(uid) if carpeta(uid).exists() else None
            if lineas is None:
                print('\n(la sala se cerró)')
                return ''
            for linea in lineas[vistas:]:
                m = re.match(r'^(\S+) (\S+?)→(\S+?): (.*)$', linea)
                if not m:
                    print(linea)
                    continue
                hora_, quien, para, texto = m.groups()
                c = color.get(quien, '0')
                destino = '' if para == 'todos' else ' → ' + para
                print('\033[2m{}\033[0m \033[1;{}m{}\033[0m\033[2m{}\033[0m'.format(hora_, c, quien, destino))
                for parte in textwrap.wrap(texto, ancho) or ['']:
                    print('      \033[{}m{}\033[0m'.format(c, parte))
                print(flush=True)
            vistas = len(lineas)
            est = leer_estado(uid)
            if est['fase'] != fase:
                fase = est['fase']
                print('\033[2m── fase {} ──\033[0m\n'.format(fase), flush=True)
                if fase in ('cerrada', 'cerrando'):
                    return ''
            time.sleep(1)
    except KeyboardInterrupt:
        return ''


def subagente_termino(agent_id):
    """Hook SubagentStop: el subagente de una sala queda terminado aunque no haya dicho FIN."""
    if not agent_id or not base().exists():
        return
    for folder in base().iterdir():
        if not (folder / 'estado.json').exists():
            continue
        est = H.read_json(folder / 'estado.json')
        for agente, p in est['participantes'].items():
            if p.get('subagente') == agent_id and p['estado'] == 'activo':
                def marcar(e, agente=agente):
                    e['participantes'][agente]['estado'] = 'terminado'
                    return True
                con_sala(est['uuid'], marcar)


def guardia(payload):
    """Hook PreToolUse: nadie edita los archivos de una sala a mano."""
    entrada = payload.get('tool_input') or {}
    ruta = str(entrada.get('file_path') or entrada.get('notebook_path') or '')
    if not ruta:
        return ''
    resuelta = os.path.abspath(ruta)
    if H.under(resuelta, str(H.REG / 'salas')) or re.search(r'/salas/[^/]+/CHAT[^/]*$', resuelta):
        return json.dumps({'hookSpecificOutput': {
            'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
            'permissionDecisionReason': 'Los archivos de la sala solo se escriben con herramientas/sala.sh.'}},
            ensure_ascii=False)
    return ''


# --- vigilante, cierre y barrido ---------------------------------------------

def claude_json(sistema, prompt, esquema, esfuerzo):
    vacia = H.reg_path('vacia')
    vacia.mkdir(parents=True, exist_ok=True)
    comando = ['claude', '-p', '--safe-mode', '--model', 'opus', '--effort', esfuerzo, '--output-format', 'json',
               '--no-session-persistence', '--tools', '', '--system-prompt', sistema,
               '--json-schema', json.dumps(esquema), prompt]
    salida = subprocess.run(comando, cwd=str(vacia), capture_output=True, text=True, timeout=170)
    datos = json.loads(salida.stdout)
    if datos.get('is_error') or not isinstance(datos.get('structured_output'), dict):
        raise ValueError('respuesta inválida del modelo')
    return datos['structured_output'], datos.get('usage') or {}


def encolar_corrida(est, rol, n, usage, esfuerzo, segundos):
    herramienta = marcador_sh()
    if not herramienta or est.get('tarea') is None:
        return
    evento = {'evento': 'corrida', 'tarea': est['tarea'], 'origen': rol, 'id_externo': '{}:{}:{}'.format(rol, est['uuid'], n),
              'rol': rol, 'agente': 'opus', 'esfuerzo': esfuerzo, 'cuenta': 'claude',
              'tokens_entrada': usage.get('input_tokens'), 'tokens_salida': usage.get('output_tokens'),
              'tokens_cache_leida': usage.get('cache_read_input_tokens'),
              'tokens_cache_escrita': usage.get('cache_creation_input_tokens'),
              'segundos': segundos, 'exacto': 1}
    subprocess.run(['/bin/bash', str(herramienta), 'encolar', json.dumps(evento, ensure_ascii=False)],
                   capture_output=True, timeout=10)


SISTEMA_VIGILANTE = (
    'Eres el vigilante de una sala donde varios agentes de IA colaboran por turnos. Tu único trabajo es detectar '
    'si alguien se fue de largo, no entiende la tarea, contradice su plan, da indicaciones que su rol no permite '
    'o si la conversación perdió el hilo. Si todo va razonable, responde OK. Responde ALTO solo si seguir así '
    'desperdiciaría trabajo. motivo: una línea concreta. a_quien: el agente responsable o "todos".')


def vigilar(uid, version, motivo):
    est = leer_estado(uid)
    planes = '\n'.join('{} ({}): {}'.format(a, p['rol'], (p.get('plan') or '(sin plan)').replace('\n', ' | '))
                       for a, p in est['participantes'].items())
    banderas = '\n'.join(b['texto'] for b in est['banderas'][-6:]) or '(ninguna)'
    reglas = (carpeta(uid) / 'REGLAS.md').read_text(encoding='utf-8')[:2500]
    prompt = 'REGLAS:\n{}\n\nPLANES:\n{}\n\nÚLTIMAS LÍNEAS:\n{}\n\nBANDERAS:\n{}\n\nMomento de la revisión: {}.'.format(
        reglas, planes, '\n'.join(lineas_chat(uid)[-20:]) or '(vacío)', banderas, motivo)
    inicio = ahora()
    try:
        resultado, usage = claude_json(SISTEMA_VIGILANTE, prompt, ESQUEMA_VIGILANTE, 'medium')
    except Exception as exc:
        resultado, usage = {'veredicto': 'OK', 'motivo': 'falló el vigilante: ' + str(exc)[:120], 'fallo': True}, {}
    resultado['motivo_llamada'] = motivo
    resultado['version'] = int(version)

    def aplicar(e):
        e['vigilante']['activa'] = None
        # Vigente si desde que se pidió entraron como mucho 2 líneas nuevas al chat.
        vigente = len(lineas_chat(uid)) - int(version) <= 2 or motivo == 'arranque'
        vig = e['vigilante']
        if resultado.get('fallo'):
            # Una falla no gasta presupuesto ni pide otra revisión: antes cada falla era una bandera
            # que volvía a llamar al vigilante, y la cascada agotaba el presupuesto en segundos.
            vig['usadas'] = max(0, vig['usadas'] - 1)
            vig['fallos'] = vig.get('fallos', 0) + 1
            bandera(e, 'vigilante-fallo-{}'.format(vig['fallos']), resultado['motivo'])
            if vig['fallos'] >= FALLOS_VIGILANTE:
                vig['apagado'] = True
                bandera(e, 'vigilante-apagado', 'el vigilante falló {} veces seguidas; la sala sigue sin vigilancia'
                        .format(vig['fallos']))
            for b in e['banderas']:
                b['enviada'] = True
        else:
            vig['fallos'] = 0
            if resultado['veredicto'] == 'ALTO' and vigente and motivo != 'arranque':
                detener(e, 'vigilante: {} ({})'.format(resultado['motivo'], resultado.get('a_quien', 'todos')))
            elif resultado['veredicto'] == 'ALTO' and motivo != 'arranque':
                bandera(e, 'vigilante-viejo-{}'.format(e['version']), 'ALTO sobre estado viejo: ' + resultado['motivo'])
        if motivo == 'arranque' and resultado.get('fallo'):
            resultado['veredicto'] = 'OK'
        e['vigilante']['ultimo'] = resultado
        return True
    est, _ = con_sala(uid, aplicar)
    encolar_corrida(est, 'vigilante', est['vigilante']['usadas'], usage, 'medium', ahora() - inicio)


def destino_aprendizajes(est):
    if est.get('raiz') == 'ProjectA':
        return Path(est['proyecto']) / 'Bases/HUMANIFY-CANDIDATOS.md'
    return Path(est['proyecto']) / 'Bases/APRENDIZAJES.md'


def guardados(uid):
    salida = []
    for linea in lineas_chat(uid):
        partes = linea.split(': GUARDAR: ', 1)
        if len(partes) == 2:
            quien = partes[0].split(' ', 1)[-1].split('→')[0]
            salida.append((quien, partes[1].strip()))
    return salida


def confirmar_marcador(est):
    herramienta = marcador_sh()
    if not herramienta:
        return False, 'no hay marcador instalado'
    p = est['participantes']
    evento = {'evento': 'sala', 'uuid': est['uuid'], 'id_externo': 'sala:' + est['uuid'], 'tarea': est.get('tarea'),
              'agentes': ','.join(p), 'mensajes': sum(q['mensajes'] for q in p.values()),
              'rechazos': sum(q['rechazos'] for q in p.values()), 'banderas': len(est['banderas']),
              'altos': est['altos'], 'llamadas_vigilante': est['vigilante']['usadas'],
              'forzada': 1 if est.get('forzada') else 0}
    for paso in (['encolar', json.dumps(evento, ensure_ascii=False)], ['procesar']):
        r = subprocess.run(['/bin/bash', str(herramienta)] + paso, capture_output=True, text=True, timeout=30)
        if r.returncode:
            return False, 'marcador {}: {}'.format(paso[0], (r.stderr or r.stdout).strip()[:120])
    db = Path(os.environ.get('MARCADOR_DB', str(H.REG / 'marcador.db')))
    try:
        import sqlite3
        with sqlite3.connect('file:{}?mode=ro'.format(db), uri=True, timeout=2) as con:
            fila = con.execute('SELECT 1 FROM salas WHERE uuid = ?', (est['uuid'],)).fetchone()
    except Exception as exc:
        return False, 'no se pudo confirmar en el marcador: ' + str(exc)[:100]
    return (True, '') if fila else (False, 'el marcador no confirmó la sala')


def cmd_cerrar(uid, forzar=False, barrido=False):
    folder = carpeta(uid)
    if not folder.exists():
        return 'la sala ya no existe (cerrada)'
    with H.lock(folder / '.candado'):
        est = leer_estado(uid)
        if est['fase'] == 'cerrada':
            return 'ya estaba cerrada'
        vivos = [a for a, p in est['participantes'].items() if vivo(p)]
        sin_fin = [a for a, p in est['participantes'].items() if p['estado'] == 'activo']
        reciente = ahora() - est['actividad'] < 600
        if (vivos or sin_fin or reciente) and not forzar:
            raise Rechazo('no se cierra: {}'.format('; '.join(filter(None, [
                'procesos vivos: ' + ', '.join(vivos) if vivos else '',
                'sin FIN: ' + ', '.join(sin_fin) if sin_fin else '',
                'actividad en los últimos 10 min' if reciente else '']))))
        if forzar:
            est['forzada'] = True
            for a in sin_fin:
                est['participantes'][a]['estado'] = 'ausente'
        est['fase_anterior'] = est['fase'] if est['fase'] != 'cerrando' else est.get('fase_anterior')
        est['fase'] = 'cerrando'
        guardar_estado(est)
    try:
        if barrido and not guardados(uid) and len(lineas_chat(uid)) > 40:
            rescatar(est)
        destino = H.destination(str(destino_aprendizajes(est)))
        existente = destino.read_text(encoding='utf-8') if destino.exists() else ''
        for n, (quien, texto) in enumerate(guardados(uid), 1):
            ident = 'sala:{}:{}'.format(uid[:8], n)
            if ident in existente:
                continue
            texto, cambia, base_candidato = desglosar_guardar(texto)
            H.escribir_candidato(destino, texto, base_candidato, cambia,
                                 'sala {} ({})'.format(est['nombre'], quien), ident=ident)
        existente = destino.read_text(encoding='utf-8') if destino.exists() else ''
        faltan = [n for n in range(1, len(guardados(uid)) + 1) if 'sala:{}:{}'.format(uid[:8], n) not in existente]
        if faltan:
            raise ValueError('faltan recibos de GUARDAR: {}'.format(faltan))
        if est.get('tarea') is not None:
            evidencia = H.reg_path('marcador-evidencia', 'tarea-{}'.format(est['tarea']))
            evidencia.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(str(folder / 'CHAT.md'), str(evidencia / 'CHAT.md'))
            entregables = evidencia / 'entregables'
            if entregables.exists():
                shutil.rmtree(str(entregables))
            entregables.mkdir()
            cantidad, peso, omitidos = 0, 0, []
            for archivo in sorted(Path(est['trabajo']).iterdir()):
                if (archivo.is_symlink() or not archivo.is_file() or
                        archivo.suffix not in ('.md', '.txt', '.json') or
                        archivo.match('plan-*.md') or archivo.name in ('CHAT.md', 'CHAT-copia.md')):
                    continue
                tamano = archivo.stat().st_size
                if cantidad >= 10 or peso + tamano > 200 * 1024:
                    omitidos.append(archivo.name)
                    continue
                shutil.copyfile(str(archivo), str(entregables / archivo.name))
                cantidad += 1
                peso += tamano
            (evidencia / 'entregables-omitidos.txt').write_text(
                'ENTREGABLES OMITIDOS (tope de 10 archivos / 200 KB): ' + ', '.join(omitidos) + '\n'
                if omitidos else '', encoding='utf-8')
        ok, motivo = confirmar_marcador(est)
        if not ok:
            raise ValueError(motivo)
    except Exception as exc:
        def restaurar(e):
            e['fase'] = e.get('fase_anterior') or 'trabajo'
            return True
        with H.lock(folder / '.candado'):
            e = leer_estado(uid)
            restaurar(e)
            guardar_estado(e)
        H.log_event('sala', uid[:8], 'error', 0, 'cerrar falló, se conserva: ' + str(exc))
        raise Rechazo('no se cerró y se conserva todo: ' + str(exc))
    total = len(guardados(uid))
    linea = {'hora': H.now(), 'uuid': uid, 'nombre': est['nombre'], 'proyecto': est.get('raiz'),
             'guardados': total, 'forzada': bool(est.get('forzada')), 'barrido': barrido}
    H.append(H.reg_path('salas-cerradas-' + H.now()[:7] + '.jsonl'), json.dumps(linea, ensure_ascii=False))
    shutil.rmtree(str(folder), ignore_errors=True)
    shutil.rmtree(est['trabajo'], ignore_errors=True)
    try:
        Path(est['trabajo']).parent.rmdir()
    except OSError:
        pass
    return 'sala {} cerrada · {} aprendizajes a {} · carpetas borradas'.format(
        est['nombre'], total, destino_aprendizajes(est).name)


SISTEMA_RESCATE = (
    'Lees el chat de una sala de agentes que terminó sin marcar aprendizajes. Propón como mucho 3 aprendizajes '
    'reutilizables (qué funcionó o falló y por qué), cada uno en una línea con el formato '
    'GUARDAR: <texto> · cambia: <qué harás distinto, al menos 4 palabras> · base: hecho|inferencia. '
    'Solo hablan agentes: no atribuyas preferencias a Didier ni uses base: didier. '
    'Si no hay nada que valga, lista vacía.')


def rescatar(est):
    texto = '\n'.join(lineas_chat(est['uuid'])[-120:])
    inicio = ahora()
    resultado, usage = claude_json(SISTEMA_RESCATE, texto, ESQUEMA_RESCATE, 'low')
    with H.lock(carpeta(est['uuid']) / '.candado'):
        e = leer_estado(est['uuid'])
        previos = [g for _, g in guardados(est['uuid'])]
        for g in resultado.get('guardar', [])[:3]:
            try:
                valido = validar_guardar(e, g, previos)
            except ValueError as exc:
                H.log_event('sala-rescate', est['uuid'][:8], 'rechazado', 0, str(exc))
                continue
            agregar_chat(e, '{} rescate→todos: GUARDAR: {}'.format(hora(), valido))
            previos.append(valido)
        guardar_estado(e)
    encolar_corrida(est, 'rescate', 1, usage, 'low', ahora() - inicio)


def cmd_barrido():
    if not base().exists():
        return 'barrido: sin salas'
    cerradas, conservadas = 0, []
    for folder in sorted(base().iterdir()):
        if not (folder / 'estado.json').exists():
            continue
        try:
            est = H.read_json(folder / 'estado.json')
            if any(vivo(p) for p in est['participantes'].values()):
                continue
            if ahora() - est['actividad'] < QUIETA_BARRIDO:
                continue
            cmd_cerrar(est['uuid'], forzar=True, barrido=True)
            cerradas += 1
        except Exception as exc:
            conservadas.append(folder.name[:8] + ': ' + str(exc)[:80])
    H.append(H.reg_path('barridos-' + H.now()[:7] + '.jsonl'),
             json.dumps({'hora': H.now(), 'cerradas': cerradas, 'conservadas': conservadas}, ensure_ascii=False))
    return 'barrido: {} cerradas, {} conservadas'.format(cerradas, len(conservadas))


def detenidas(raiz):
    """Para el arranque: salas detenidas o quietas del proyecto."""
    if not base().exists():
        return []
    salida = []
    for folder in base().iterdir():
        try:
            est = H.read_json(folder / 'estado.json')
        except (OSError, ValueError):
            continue
        if est.get('raiz') == raiz and est['fase'] == 'detenida':
            salida.append('{} ({})'.format(est['nombre'], est['motivo_alto'][:80]))
    return salida


USO = '''uso: sala.sh abrir <nombre> --agentes a[:rol],b[:rol] --tipo T [--riesgo caro] [--criterio "…"]
       [--largo N] (80 a 2000 caracteres; por defecto 200) [--modo fases|platica] [--mensajes N] (1 a 100; 12)
       [--vigilante N] (revisiones, 0 a 12; 4; 0 lo apaga)
       sala.sh lanzar <uuid> <agente> -- <comando…>
       sala.sh unir <uuid> <agente> --subagente <agentId>
       sala.sh plan|leer|escuchar|esperar|decir|fin <uuid> <agente> …   (decir <uuid> orquestador … sin topes)
       sala.sh alto <uuid> "<motivo>" · estado <uuid> · ver <uuid|nombre> · cerrar <uuid> [--forzar] · barrido'''


def opcion(args, nombre, defecto=None):
    if nombre in args:
        i = args.index(nombre)
        if i + 1 >= len(args):
            raise Rechazo('falta el valor de ' + nombre)
        valor = args[i + 1]
        del args[i:i + 2]
        return valor
    return defecto


def main(argv):
    args = list(argv)
    if not args:
        raise Rechazo(USO)
    c = args.pop(0)
    if c == 'abrir' and args:
        nombre = args.pop(0)
        agentes, tipo = opcion(args, '--agentes'), opcion(args, '--tipo', 'sin-tipo')
        riesgo, criterio = opcion(args, '--riesgo', 'normal'), opcion(args, '--criterio', '')
        largo = opcion(args, '--largo', LARGO)
        modo, mensajes = opcion(args, '--modo', 'fases'), opcion(args, '--mensajes', TOPE_MENSAJES)
        presupuesto = opcion(args, '--vigilante', PRESUPUESTO)
        if not agentes or args:
            raise Rechazo(USO)
        return cmd_abrir(nombre, agentes, tipo, riesgo, criterio, largo, modo, mensajes, presupuesto)
    if c == 'lanzar' and len(args) >= 4 and args[2] == '--':
        sys.exit(cmd_lanzar(args[0], args[1], args[3:]))
    if c == 'unir' and len(args) == 4 and args[2] == '--subagente':
        return cmd_unir(args[0], args[1], args[3])
    if c == 'plan' and len(args) == 3:
        return cmd_plan(*args)
    if c == 'esperar' and len(args) == 4 and args[2] == '--fase':
        return cmd_esperar(args[0], args[1], args[3])
    if c == 'leer' and len(args) == 2:
        return cmd_leer(*args)
    if c == 'escuchar' and len(args) == 2:
        return cmd_escuchar(*args)
    if c == 'ver' and len(args) == 1:
        return cmd_ver(args[0])
    if c == 'decir' and len(args) == 4:
        return cmd_decir(*args)
    if c == 'fin' and len(args) >= 2:
        return cmd_fin(args[0], args[1], args[2:])
    if c == 'alto' and len(args) == 2:
        return cmd_alto(*args)
    if c == 'estado' and len(args) == 1:
        return cmd_estado(args[0])
    if c == 'cerrar' and len(args) in (1, 2):
        return cmd_cerrar(args[0], forzar=args[1:] == ['--forzar'])
    if c == 'barrido' and not args:
        return cmd_barrido()
    if c == '_vigilar' and len(args) == 3:
        return vigilar(*args) or ''
    if c == '_matar' and len(args) == 1:
        return matar(args[0]) or ''
    raise Rechazo(USO)


if __name__ == '__main__':
    try:
        salida = main(sys.argv[1:])
        if salida:
            print(salida)
        sys.exit(0)
    except Rechazo as exc:
        print('sala: ' + str(exc), file=sys.stderr)
        sys.exit(2)
    except Exception as exc:
        print('sala: error: {}: {}'.format(type(exc).__name__, exc), file=sys.stderr)
        sys.exit(1)
