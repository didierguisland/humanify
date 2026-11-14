#!/usr/bin/python3
"""Trabajadores del Teams en segundo plano, con herramientas y carpeta propia."""
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import time
import uuid


def _ejecutar(comando, carpeta, env, limite, prompt=None):
    proceso = subprocess.Popen(
        comando, cwd=str(carpeta), env=env, text=True, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        salida, errores = proceso.communicate(input=prompt, timeout=max(0, limite - time.monotonic()))
    except BaseException:
        try:
            os.killpg(proceso.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proceso.communicate()
        raise
    if proceso.returncode:
        raise ValueError(_detalle(proceso.returncode, errores, salida))
    return salida


def _detalle(codigo, errores, salida):
    """El error real: de un JSON, lo que dice (el 429 de Claude llega así, con su hora de reinicio)."""
    try:
        datos = json.loads(salida)
        partes = [str(datos[k]) for k in ('result', 'response', 'error', 'api_error', 'api_error_status') if datos.get(k)]
    except (TypeError, ValueError, AttributeError):
        partes = []
    detalle = ' · '.join(partes) or ' '.join((errores or salida or '').split())[-600:]
    return ('{} {}: {}'.format('salida' if isinstance(codigo, int) else '', codigo, detalle)).strip().rstrip(': ')


def _codex_home(base):
    destino = base / 'registro/codex-equipo'
    destino.mkdir(parents=True, exist_ok=True)
    for nombre in ('auth.json', 'config.toml'):
        enlace = destino / nombre
        try:
            enlace.symlink_to(Path.home() / '.codex' / nombre)
        except FileExistsError:
            pass  # Otro trabajador o el canal ya preparó este hogar.
    return destino


def _gasto_claude(trabajo, datos, segundos, env, limite):
    config = trabajo['config']
    uso = datos.get('usage') or {}
    evento = dict(evento='corrida', tarea=trabajo['tarea'], origen='taller',
                  id_externo=uuid.uuid4().hex, rol='ejecucion', agente=config['modelo'],
                  esfuerzo=config['esfuerzo'], cuenta='claude', segundos=segundos, exacto=1,
                  turnos=datos.get('num_turns'))  # vueltas del trabajador: lo que cuesta construir a ciegas
    for campo, clave in (('entrada', 'input_tokens'), ('salida', 'output_tokens'),
                         ('cache_leida', 'cache_read_input_tokens'),
                         ('cache_escrita', 'cache_creation_input_tokens')):
        evento['tokens_' + campo] = uso.get(clave)
    comando = ['/bin/bash', str(Path(trabajo['base']) / 'herramientas/marcador.sh')]
    _ejecutar(comando + ['encolar', json.dumps(evento, ensure_ascii=False)],
              trabajo['carpeta'], env, limite)
    _ejecutar(comando + ['procesar'], trabajo['carpeta'], env, limite)


def _entorno():
    return {k: v for k, v in os.environ.items()
            if k not in ('CLAUDECODE', 'SALA_UUID', 'MARCADOR_TAREA') and not k.startswith('CLAUDE_CODE_')}


def _gasto_cortado(trabajo, segundos):
    """Un trabajo matado por el tope gastó sin que se sepa cuánto (M11): queda la corrida, sin tokens, para que se note."""
    config = trabajo['config']
    if config.get('motor') != 'claude':
        return
    try:
        evento = dict(evento='corrida', tarea=trabajo['tarea'], origen='taller', id_externo=uuid.uuid4().hex, rol='ejecucion',
                      agente=config['modelo'], esfuerzo=config['esfuerzo'], cuenta='claude', segundos=segundos, exacto=0)
        comando = ['/bin/bash', str(Path(trabajo['base']) / 'herramientas/marcador.sh')]
        _ejecutar(comando + ['encolar', json.dumps(evento, ensure_ascii=False)], trabajo['carpeta'], _entorno(), time.monotonic() + 30)
    except Exception:
        pass  # medir nunca tapa el error del trabajo


def _trabajar(trabajo, limite):
    carpeta = Path(trabajo['carpeta']).resolve()
    proyecto = Path(trabajo['proyecto']).resolve()
    base = Path(trabajo['base']).resolve()
    carpeta.mkdir(parents=True, exist_ok=True)
    (carpeta / 'PLATICA.md').write_text(trabajo['chat'], encoding='utf-8')
    config = trabajo['config']
    motor, modelo, esfuerzo = config['motor'], config['modelo'], config['esfuerzo']
    sistema = trabajo['sistema']
    verificar = trabajo.get('verificar')
    extra = [str(Path(d)) for d in (trabajo.get('leer') or [])]
    # Ruta de paquete: la casa del oficio y lo que la etapa pida, nunca el proyecto entero (M13: la fábrica vieja queda fuera).
    aislado = trabajo.get('aislado')
    lectura = (', '.join(extra) or 'solo tu carpeta') if aislado else proyecto
    dirs_proyecto = [] if aislado else ['--add-dir', str(proyecto)]
    prompt = ('CONTEXTO DEL PROYECTO\n{}\n\nCHAT COMPLETO\n{}\n\nTU TRABAJO\n{}\n\n'
              'Escribe tus archivos en {}. Puedes leer {}. '
              'Escribe cada archivo de una vez y luego corrige solo lo necesario. '
              'Al terminar responde en máximo 3 líneas qué dejaste y la ruta. {}').format(
                  trabajo['contexto'], trabajo['chat'], trabajo['instruccion'], carpeta, lectura,
                  # Fábrica: Didier no está y nadie contesta a media corrida (M18).
                  ('Nadie puede contestarte mientras trabajas: decide con las notas del oficio y di en tu respuesta '
                   'qué supusiste. Solo si de verdad no se puede seguir, empieza la respuesta con @orquestador.')
                  if trabajo.get('sin_didier') else 'Si necesitas una decisión de Didier, empieza la respuesta con @didier.')
    if trabajo.get('hechos') is not None:
        prompt += '\n\nHECHOS MEDIDOS (no los vuelvas a medir)\n' + json.dumps(
            trabajo['hechos'], ensure_ascii=False, separators=(',', ':'))
    if verificar:
        prompt += ('\nPara probar tu trabajo corre exactamente: `{}` desde `{}`. '
                   'Si deja una imagen, ábrela con Read y mírala antes de terminar. '
                   'Corrígelo hasta que el comando salga sin errores.').format(verificar, carpeta)
    if trabajo.get('canal'):
        # El equipo sigue platicando mientras trabajas: lo que digan después también cuenta.
        prompt += ('\nAntes de terminar, relee `PLATICA.md` en tu carpeta por si el equipo agregó algo '
                   'después de que empezaste, y tómalo en cuenta.')
    env = _entorno()
    inicio = time.monotonic()
    # extra: lo que la etapa debe poder leer fuera de su carpeta. Para agy es obligatorio: una negación de read_file le corta la corrida.
    if motor == 'claude':
        herramientas = ['Read', 'Write', 'Edit', 'Glob', 'Grep']
        if verificar:
            herramientas += ['Bash({})'.format(verificar),
                             'Bash({}:*)'.format(' '.join(verificar.split()[:2]))]
        else:
            herramientas.append('Bash(node:*)')
        herramientas += trabajo.get('permite') or []  # lo que la etapa necesita además de su comando de prueba
        comando = ['claude', '-p', '--safe-mode', '--no-session-persistence',
                   '--model', modelo, '--effort', esfuerzo, '--output-format', 'json',
                   '--permission-mode', 'acceptEdits', '--allowedTools'] + herramientas + [
                   ] + dirs_proyecto + [v for d in extra for v in ('--add-dir', d)] + [
                   '--append-system-prompt', sistema]
        datos = json.loads(_ejecutar(comando, carpeta, env, limite, prompt))
        _gasto_claude(trabajo, datos, time.monotonic() - inicio, env, limite)
        if datos.get('is_error') or datos.get('error'):
            raise ValueError(_detalle('error del motor', '', json.dumps(datos)))
        respuesta = datos.get('result', '')
    elif motor in ('codex', 'agy'):
        with tempfile.TemporaryDirectory(prefix='taller-') as temporal:
            archivo = Path(temporal) / 'respuesta'
            prompt = sistema + '\n\n' + prompt
            comunes = ['--tarea', str(trabajo['tarea'])]
            if motor == 'codex':
                env['CODEX_HOME'] = str(_codex_home(base))
                comando = ['/bin/bash', str(base / 'herramientas/codex.sh')] + comunes + [
                    '--', '--skip-git-repo-check', '-s', 'workspace-write', '-m', modelo,
                    '-c', 'model_reasoning_effort=' + json.dumps(esfuerzo), '-C', str(carpeta),
                    # Sin --add-dir: el sandbox de Codex ya lee todo; así solo escribe en la carpeta del trabajo.
                    '-o', str(archivo), '-']
                _ejecutar(comando, carpeta, env, limite, prompt)
            else:
                comando = ['/bin/bash', str(base / 'herramientas/argon.sh')] + comunes + [
                    '-o', str(archivo), '--', '-p', prompt, '--model', modelo] + dirs_proyecto + [
                    v for d in extra for v in ('--add-dir', d)] + ['--mode', 'accept-edits']
                _ejecutar(comando, carpeta, env, limite)
            respuesta = archivo.read_text(encoding='utf-8')
            if motor == 'agy':
                datos = json.loads(respuesta)
                if datos.get('is_error') or datos.get('error'):
                    raise ValueError(_detalle('error del motor', '', json.dumps(datos)))
                respuesta = datos.get('response', '')
    else:
        raise ValueError('motor desconocido')
    if not isinstance(respuesta, str) or not respuesta.strip():
        raise ValueError('respuesta vacía')
    lineas = [linea.strip() for linea in respuesta.splitlines() if linea.strip()]
    # Conservar el texto del resumen incluso si el motor excede las tres líneas.
    return '\n'.join(lineas[:2] + [' '.join(lineas[2:])]) if len(lineas) > 3 else '\n'.join(lineas)


def lanzar(trabajo, al_terminar):
    """Regresa un Thread de inmediato; notifica una vez desde él, sin propagar errores.

    HUMANIFY_TALLER_TOPE expresa segundos y manda; si no, el 'tope' de la etapa; si no, 20 minutos.
    """
    def correr():
        tope = 1200.0
        try:
            tope = float(os.environ.get('HUMANIFY_TALLER_TOPE') or trabajo.get('tope') or 1200)
            if not math.isfinite(tope) or tope <= 0:
                raise ValueError('tope inválido')
            texto = _trabajar(trabajo, time.monotonic() + tope)
        except subprocess.TimeoutExpired:
            _gasto_cortado(trabajo, tope)
            texto = 'no terminó (más de {:g} min)'.format(tope / 60)
        except Exception as error:
            motivo = str(error) if isinstance(error, ValueError) else type(error).__name__
            texto = 'no terminó ({})'.format(' '.join(motivo.split())[:600])
        try:
            al_terminar(texto)
        except Exception:
            pass  # El callback tampoco puede dejar una excepción suelta en el hilo.

    hilo = threading.Thread(target=correr, name='taller', daemon=True)
    hilo.start()
    return hilo
