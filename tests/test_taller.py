#!/usr/bin/python3
"""Taller: motores falsos, sin modelos ni red."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / 'hooks'))
import _taller as T

MOTOR = r'''#!/usr/bin/python3
import json, os, pathlib, subprocess, sys, time
motor, args = sys.argv[1], sys.argv[2:]
def valor(opcion):
    return args[args.index(opcion) + 1]
registro = dict(motor=motor, args=args, cwd=os.getcwd(), stdin=sys.stdin.read(),
                env={k:v for k,v in os.environ.items()
                     if k.startswith(('CLAUDE', 'CODEX', 'SALA_', 'MARCADOR_TAREA'))})
with open(os.environ['FAKE_TALLER_LOG'], 'a') as f:
    f.write(json.dumps(registro) + '\n')
if motor == 'marcador':
    sys.exit(0)
if os.environ.get('FAKE_TALLER_LENTO'):
    hijo = subprocess.Popen(['/usr/bin/python3', '-c',
        'import pathlib, sys, time; time.sleep(1); pathlib.Path(sys.argv[1]).touch()',
        os.environ['FAKE_TALLER_SUPERVIVIENTE']])
    pathlib.Path(os.environ['FAKE_TALLER_PID']).write_text(json.dumps([os.getpid(), hijo.pid]))
    time.sleep(30)
if os.environ.get('FAKE_TALLER_PUERTA'):
    while not pathlib.Path(os.environ['FAKE_TALLER_PUERTA']).exists():
        time.sleep(.01)
if os.environ.get('FAKE_TALLER_FALLA'):
    sys.exit(3)
respuesta = os.environ.get('FAKE_TALLER_RESPUESTA', 'Dejé el HTML en trabajo/tazas.html')
datos = dict(result=respuesta, response=respuesta,
             usage=dict(input_tokens=23, output_tokens=7,
                        cache_read_input_tokens=11, cache_creation_input_tokens=5))
if os.environ.get('FAKE_TALLER_ERROR'):
    datos['is_error'] = True
salida = '{' if os.environ.get('FAKE_TALLER_JSON_ROTO') else json.dumps(datos)
if motor == 'claude':
    print(salida)
elif motor == 'agy':
    pathlib.Path(valor('-o')).write_text(salida)
else:
    pathlib.Path(valor('-o')).write_text(respuesta)
'''


class Taller(unittest.TestCase):
    def setUp(self):
        self.temporal = tempfile.TemporaryDirectory(prefix='taller-prueba-')
        self.tmp = Path(self.temporal.name).resolve()
        self.base = self.tmp / 'Humanify'
        self.proyecto = self.tmp / 'Proyecto con espacios'
        self.proyecto.mkdir()
        self.carpeta = self.proyecto / 'trabajo con espacios'
        herramientas = self.base / 'herramientas'
        herramientas.mkdir(parents=True)
        self.bin = self.tmp / 'bin'
        self.bin.mkdir()
        motor = self.tmp / 'motor.py'
        motor.write_text(MOTOR, encoding='utf-8')
        for nombre, tipo in (('claude', 'claude'), ('codex', 'codex'), ('agy', 'agy'),
                             ('marcador', 'marcador')):
            ejecutable = self.bin / nombre
            ejecutable.write_text('#!/bin/bash\nexec /usr/bin/python3 "{}" {} "$@"\n'.format(motor, tipo))
            ejecutable.chmod(0o755)
        for nombre, ejecutable in (('codex.sh', 'codex'), ('argon.sh', 'agy'), ('marcador.sh', 'marcador')):
            (herramientas / nombre).write_text('#!/bin/bash\nexec ' + ejecutable + ' "$@"\n')
        self.env = mock.patch.dict(os.environ, {
            'PATH': str(self.bin) + ':' + os.environ['PATH'],
            'FAKE_TALLER_LOG': str(self.tmp / 'llamadas.jsonl'),
            'HUMANIFY_TALLER_TOPE': '5',
            'CLAUDECODE': 'sesión', 'CLAUDE_CODE_ENTRYPOINT': 'app',
            'CLAUDE_CODE_TEST': 'x', 'SALA_UUID': 'sala-ajena', 'MARCADOR_TAREA': '999',
        })
        self.env.start()
        self.trabajo = dict(agente='sonnet', config=dict(motor='claude', modelo='sonnet', esfuerzo='medium'),
                            sistema='EQUIPO\nALMA\nMEMORIA', contexto='CONTEXTO: node prueba.mjs',
                            chat='\n'.join('mensaje-{}'.format(i) for i in range(90)),
                            instruccion='Construye un HTML de tazas', carpeta=self.carpeta,
                            proyecto=self.proyecto, tarea=17, base=self.base)
        self.hilos = []
        self.respuestas = []

    def tearDown(self):
        for hilo in self.hilos:
            hilo.join(6)
        self.env.stop()
        self.temporal.cleanup()

    def lanzar(self, motor='claude', callback=None):
        self.trabajo['config']['motor'] = motor
        hilo = T.lanzar(self.trabajo, callback or self.respuestas.append)
        self.hilos.append(hilo)
        return hilo

    def terminar(self, motor='claude'):
        hilo = self.lanzar(motor)
        hilo.join(6)
        self.assertFalse(hilo.is_alive())
        self.assertEqual(len(self.respuestas), 1)
        return self.respuestas[0]

    def llamadas(self):
        return [json.loads(linea) for linea in (self.tmp / 'llamadas.jsonl').read_text().splitlines()]

    def verificar_prompt(self, prompt):
        for texto in (self.trabajo['contexto'], self.trabajo['chat'], self.trabajo['instruccion'],
                      'TU TRABAJO', str(self.carpeta), str(self.proyecto), 'de una vez',
                      'corrige solo lo necesario', 'máximo 3 líneas', '@didier'):
            self.assertIn(texto, prompt)

    def test_regresa_enseguida_y_callback_una_vez_desde_hilo(self):
        puerta = self.tmp / 'continuar'
        identidades = []
        def callback(texto):
            identidades.append(threading.get_ident())
            self.respuestas.append(texto)
        with mock.patch.dict(os.environ, FAKE_TALLER_PUERTA=str(puerta)):
            inicio = time.monotonic()
            hilo = self.lanzar(callback=callback)
            try:
                self.assertIsInstance(hilo, threading.Thread)
                self.assertLess(time.monotonic() - inicio, .5)
                self.assertTrue(hilo.is_alive())
                self.assertEqual(self.respuestas, [])
            finally:
                puerta.touch()
                hilo.join(6)
        self.assertFalse(hilo.is_alive())
        self.assertEqual(self.respuestas, ['Dejé el HTML en trabajo/tazas.html'])
        self.assertEqual(identidades, [hilo.ident])
        self.assertNotEqual(identidades[0], threading.get_ident())

    def test_claude_stdin_herramientas_directorio_y_entorno(self):
        self.terminar()
        llamada = self.llamadas()[0]
        args = llamada['args']
        self.verificar_prompt(llamada['stdin'])
        self.assertEqual(args[args.index('--append-system-prompt') + 1], self.trabajo['sistema'])
        self.assertNotIn(llamada['stdin'], args)
        self.assertEqual(args[args.index('--allowedTools') + 1:args.index('--add-dir')],
                         ['Read', 'Write', 'Edit', 'Glob', 'Grep', 'Bash(node:*)'])
        for opcion in ('-p', '--safe-mode', '--no-session-persistence'):
            self.assertIn(opcion, args)
        for opcion, valor in (('--model', 'sonnet'), ('--effort', 'medium'),
                              ('--output-format', 'json'), ('--permission-mode', 'acceptEdits'),
                              ('--add-dir', str(self.proyecto))):
            self.assertEqual(args[args.index(opcion) + 1], valor)
        self.assertEqual(llamada['cwd'], str(self.carpeta))
        self.assertFalse(any(k in ('CLAUDECODE', 'SALA_UUID', 'MARCADOR_TAREA')
                             or k.startswith('CLAUDE_CODE_') for k in llamada['env']))
        self.assertTrue(self.carpeta.is_dir())

    def test_gasto_claude_encola_procesa_y_tiene_id_unico(self):
        self.terminar()
        self.respuestas.clear()
        self.terminar()
        llamadas = self.llamadas()
        self.assertEqual([l['motor'] for l in llamadas], ['claude', 'marcador', 'marcador'] * 2)
        eventos = []
        for inicio in (0, 3):
            self.assertEqual(llamadas[inicio + 1]['args'][0], 'encolar')
            self.assertEqual(llamadas[inicio + 2]['args'], ['procesar'])
            eventos.append(json.loads(llamadas[inicio + 1]['args'][1]))
        evento = eventos[0]
        for clave, valor in dict(evento='corrida', tarea=17, origen='taller', rol='ejecucion',
                                 agente='sonnet', esfuerzo='medium', cuenta='claude', exacto=1,
                                 tokens_entrada=23, tokens_salida=7, tokens_cache_leida=11,
                                 tokens_cache_escrita=5).items():
            self.assertEqual(evento[clave], valor)
        self.assertGreater(evento['segundos'], 0)
        self.assertTrue(evento['id_externo'])
        self.assertNotEqual(eventos[0]['id_externo'], eventos[1]['id_externo'])

    def test_codex_stdin_home_equipo_y_workspace_write(self):
        self.terminar('codex')
        llamada = self.llamadas()[0]
        args = llamada['args']
        self.assertEqual(args[:3], ['--tarea', '17', '--'])
        self.assertEqual(args[-1], '-')
        for opcion, valor in (('-s', 'workspace-write'), ('-m', 'sonnet'),
                              ('-c', 'model_reasoning_effort="medium"'), ('-C', str(self.carpeta))):
            self.assertEqual(args[args.index(opcion) + 1], valor)
        self.assertNotIn('--add-dir', args)  # escribir solo en la carpeta del trabajo
        self.assertIn('--skip-git-repo-check', args)
        self.assertNotIn('--ephemeral', args)
        self.verificar_prompt(llamada['stdin'])
        self.assertTrue(llamada['stdin'].startswith(self.trabajo['sistema']))
        self.assertEqual(llamada['cwd'], str(self.carpeta))
        hogar = self.base / 'registro/codex-equipo'
        self.assertEqual(llamada['env']['CODEX_HOME'], str(hogar))
        self.assertFalse(any(k in ('CLAUDECODE', 'SALA_UUID', 'MARCADOR_TAREA')
                             or k.startswith('CLAUDE_CODE_') for k in llamada['env']))
        self.assertEqual(sorted(p.name for p in hogar.iterdir()), ['auth.json', 'config.toml'])
        for nombre in ('auth.json', 'config.toml'):
            self.assertTrue((hogar / nombre).is_symlink())
            self.assertEqual(os.readlink(hogar / nombre), str(Path.home() / '.codex' / nombre))
        self.assertFalse(Path(args[args.index('-o') + 1]).exists())

    def test_ruta_de_paquete_no_abre_el_proyecto(self):
        casa = str(Path(self.temporal.name) / 'casa')
        self.trabajo.update(aislado=True, leer=[casa])
        for motor in ('claude', 'agy'):
            self.respuestas.clear()
            self.terminar(motor)
            llamada = [l for l in self.llamadas() if l['motor'] == motor][-1]
            args, prompt = llamada['args'], llamada['stdin'] or llamada['args'][llamada['args'].index('-p') + 1]
            directorios = [args[i + 1] for i, a in enumerate(args) if a == '--add-dir']
            self.assertEqual(directorios, [casa], motor)
            self.assertNotIn('Puedes leer ' + str(self.proyecto), prompt, motor)
            self.assertIn('Puedes leer ' + casa, prompt, motor)

    def test_agy_prompt_add_dir_y_respuesta_json(self):
        self.assertEqual(self.terminar('agy'), 'Dejé el HTML en trabajo/tazas.html')
        llamada = self.llamadas()[0]
        args = llamada['args']
        self.assertEqual(args[:2], ['--tarea', '17'])
        self.assertIn('--', args)
        self.verificar_prompt(args[args.index('-p') + 1])
        self.assertTrue(args[args.index('-p') + 1].startswith(self.trabajo['sistema']))
        self.assertEqual(args[args.index('--add-dir') + 1], str(self.proyecto))
        self.assertEqual(args[args.index('--mode') + 1], 'accept-edits')
        self.assertEqual(args[args.index('--model') + 1], 'sonnet')
        self.assertEqual(llamada['cwd'], str(self.carpeta))
        self.assertEqual(llamada['stdin'], '')
        self.assertFalse(Path(args[args.index('-o') + 1]).exists())

    def test_resumen_tres_lineas_sin_recorte_a_400(self):
        respuesta = '@didier ' + 'decisión ' * 70 + '\nRuta: trabajo/tazas.html\nTercera\nCuarta'
        with mock.patch.dict(os.environ, FAKE_TALLER_RESPUESTA=respuesta):
            texto = self.terminar('codex')
        self.assertGreater(len(texto), 400)
        self.assertTrue(texto.startswith('@didier'))
        self.assertEqual(len(texto.splitlines()), 3)
        self.assertTrue(texto.endswith('Tercera Cuarta'))

    def test_fallos_motor_no_propagan_y_notifican_una_vez(self):
        for motor in ('claude', 'codex', 'agy'):
            with self.subTest(motor=motor), mock.patch.dict(os.environ, FAKE_TALLER_FALLA='1'):
                self.respuestas.clear()
                self.assertEqual(self.terminar(motor), 'no terminó (salida 3)')

    def test_respuesta_vacia(self):
        for motor in ('claude', 'codex', 'agy'):
            with self.subTest(motor=motor), mock.patch.dict(os.environ, FAKE_TALLER_RESPUESTA=' \n'):
                self.respuestas.clear()
                self.assertEqual(self.terminar(motor), 'no terminó (respuesta vacía)')

    def test_json_error_motor_y_json_invalido(self):
        for motor in ('claude', 'agy'):
            with self.subTest(motor=motor), mock.patch.dict(os.environ, FAKE_TALLER_ERROR='1'):
                self.respuestas.clear()
                self.assertTrue(self.terminar(motor).startswith('no terminó (error del motor'))
            with self.subTest(motor=motor), mock.patch.dict(os.environ, FAKE_TALLER_JSON_ROTO='1'):
                self.respuestas.clear()
                self.assertTrue(self.terminar(motor).startswith('no terminó ('))

    def test_tope_mata_motor_y_descendiente(self):
        pidfile = self.tmp / 'procesos.json'
        superviviente = self.tmp / 'hijo-sigue-vivo'
        with mock.patch.dict(os.environ, HUMANIFY_TALLER_TOPE='.5', FAKE_TALLER_LENTO='1',
                             FAKE_TALLER_PID=str(pidfile),
                             FAKE_TALLER_SUPERVIVIENTE=str(superviviente)), \
                mock.patch.object(T.os, 'killpg', wraps=os.killpg) as matar:
            texto = self.terminar('codex')
        self.assertTrue(texto.startswith('no terminó (más de '))
        self.assertTrue(texto.endswith(' min)'))
        pids = json.loads(pidfile.read_text())
        matar.assert_called_once_with(pids[0], signal.SIGKILL)
        time.sleep(1.1)
        self.assertFalse(superviviente.exists(), 'el descendiente sobrevivió al tope')

    def test_429_de_claude_llega_con_su_mensaje_y_su_hora(self):
        with mock.patch.object(T.subprocess, 'Popen') as popen:
            proceso = popen.return_value
            proceso.communicate.return_value = (json.dumps({
                'is_error': True, 'result': "You've hit your 5-hour limit · resets 11:20pm (America/Mexico_City)",
                'api_error_status': 429, 'api_error': 'usage_limit_reached'}), '')
            proceso.returncode = 1
            with self.assertRaisesRegex(ValueError, r"salida 1: You've hit your 5-hour limit · resets 11:20pm.*429"):
                T._ejecutar(['claude'], self.tmp, {}, time.monotonic() + 5, 'x')

    def test_leer_tope_de_etapa_y_sin_didier(self):
        extra = str(self.tmp / 'casa de video')
        self.trabajo.update(leer=[extra], tope=77, sin_didier=True)
        for motor in ('claude', 'agy'):
            self.trabajo['config'] = dict(motor=motor, modelo='m', esfuerzo='high')
            self.respuestas.clear()
            self.terminar(motor)
            llamada = [l for l in self.llamadas() if l['motor'] == motor][-1]
            self.assertIn(extra, llamada['args'])
            self.assertEqual(llamada['args'][llamada['args'].index(extra) - 1], '--add-dir')
            prompt = llamada['stdin'] or ' '.join(llamada['args'])
            self.assertIn('Nadie puede contestarte', prompt)
            self.assertNotIn('@didier', prompt)
        with mock.patch.dict(os.environ):
            os.environ.pop('HUMANIFY_TALLER_TOPE')
            limites = []
            with mock.patch.object(T, '_trabajar', side_effect=lambda t, l: limites.append(l - time.monotonic()) or 'ok'):
                T.lanzar(self.trabajo, lambda _: None).join()
        self.assertAlmostEqual(limites[0], 77, delta=2)

    def test_tope_predeterminado_veinte_minutos(self):
        with mock.patch.dict(os.environ):
            os.environ.pop('HUMANIFY_TALLER_TOPE', None)
            with mock.patch.object(T, '_trabajar', side_effect=subprocess.TimeoutExpired('motor', 1200)):
                self.assertEqual(self.terminar(), 'no terminó (más de 20 min)')

    def test_error_preparacion_y_callback_no_escapan(self):
        self.trabajo.pop('carpeta')
        self.assertEqual(self.terminar(), 'no terminó (KeyError)')
        callback = mock.Mock(side_effect=RuntimeError('callback roto'))
        with mock.patch.object(threading, 'excepthook') as excepcion:
            hilo = self.lanzar(callback=callback)
            hilo.join(6)
        callback.assert_called_once_with('no terminó (KeyError)')
        excepcion.assert_not_called()


if __name__ == '__main__':
    unittest.main()


class Relectura(Taller):
    def prompt(self, llamada):
        if llamada['motor'] == 'agy':
            return llamada['args'][llamada['args'].index('-p') + 1]
        return llamada['stdin']

    def test_claude_verificar_permite_comando_exacto_y_script_sin_node_generico(self):
        verificar = 'node /abs/herramientas/hoja-html.mjs reel.html _verificar/hoja.png --cada 3'
        self.trabajo['verificar'] = verificar
        self.terminar()
        llamada = self.llamadas()[0]
        args = llamada['args']
        self.assertEqual(args[args.index('--allowedTools') + 1:args.index('--add-dir')],
                         ['Read', 'Write', 'Edit', 'Glob', 'Grep',
                          'Bash(' + verificar + ')', 'Bash(node /abs/herramientas/hoja-html.mjs:*)'])
        self.assertNotIn('Bash(node:*)', args)
        self.assertIn('Para probar tu trabajo corre exactamente: `{}` desde `{}`.'.format(
            verificar, self.carpeta), llamada['stdin'])

    def test_claude_permite_lo_que_pide_la_etapa(self):
        self.trabajo['verificar'] = 'ffmpeg -i audio.wav'
        self.trabajo['permite'] = ['Bash(python3:*)']
        self.terminar()
        args = self.llamadas()[0]['args']
        self.assertIn('Bash(python3:*)', args[args.index('--allowedTools') + 1:args.index('--add-dir')])

    def test_comando_e_imagen_en_prompt_de_codex_y_agy(self):
        verificar = '/usr/bin/python3 /abs/herramientas/revisar.py cv.html _verificar/cv.png'
        self.trabajo['verificar'] = verificar
        for motor in ('codex', 'agy'):
            with self.subTest(motor=motor):
                self.respuestas.clear()
                self.terminar(motor)
                prompt = self.prompt(self.llamadas()[-1])
                self.assertIn('Para probar tu trabajo corre exactamente: `{}` desde `{}`.'.format(
                    verificar, self.carpeta), prompt)
                self.assertIn('Si deja una imagen, ábrela con Read y mírala antes de terminar.', prompt)
                self.assertIn('Corrígelo hasta que el comando salga sin errores.', prompt)
                self.assertNotIn('Si hay un comando de prueba en el contexto, úsalo', prompt)

    def test_hechos_medidos_en_json_compacto_en_todos_los_motores(self):
        self.trabajo['hechos'] = {'duración_s': 12.34, 'palabras_max': 28, 'pendiente': None}
        for motor in ('claude', 'codex', 'agy'):
            with self.subTest(motor=motor):
                self.respuestas.clear()
                self.terminar(motor)
                llamada = next(l for l in reversed(self.llamadas()) if l['motor'] == motor)
                self.assertIn('HECHOS MEDIDOS (no los vuelvas a medir)\n'
                              '{"duración_s":12.34,"palabras_max":28,"pendiente":null}',
                              self.prompt(llamada))

    def test_opcionales_none_conservan_herramientas_y_omiten_secciones(self):
        self.trabajo.update(verificar=None, hechos=None)
        self.terminar()
        llamada = self.llamadas()[0]
        args = llamada['args']
        self.assertEqual(args[args.index('--allowedTools') + 1:args.index('--add-dir')],
                         ['Read', 'Write', 'Edit', 'Glob', 'Grep', 'Bash(node:*)'])
        self.assertNotIn('HECHOS MEDIDOS', llamada['stdin'])
        self.assertNotIn('Para probar tu trabajo corre exactamente:', llamada['stdin'])

    def test_hechos_vacios_tambien_se_incluyen(self):
        self.trabajo['hechos'] = {}
        self.terminar()
        self.assertIn('HECHOS MEDIDOS (no los vuelvas a medir)\n{}', self.llamadas()[0]['stdin'])

    def test_platica_con_chat_y_relectura_local_en_todos_los_motores(self):
        canal = self.tmp / 'fuera-del-trabajo' / 'CHAT.md'
        self.trabajo['canal'] = canal
        for motor in ('claude', 'codex', 'agy'):
            with self.subTest(motor=motor):
                self.respuestas.clear()
                self.terminar(motor)
                self.assertEqual((self.carpeta / 'PLATICA.md').read_text(encoding='utf-8'),
                                 self.trabajo['chat'])
                llamada = next(l for l in reversed(self.llamadas()) if l['motor'] == motor)
                prompt = self.prompt(llamada)
                self.assertIn('Antes de terminar, relee `PLATICA.md` en tu carpeta por si el equipo '
                              'agregó algo después de que empezaste', prompt)
                self.assertNotIn(str(canal), prompt)

    def test_juez_sin_canal_crea_platica_sin_frase_de_relectura(self):
        for motor in ('claude', 'codex', 'agy'):
            with self.subTest(motor=motor):
                self.respuestas.clear()
                self.terminar(motor)
                self.assertEqual((self.carpeta / 'PLATICA.md').read_text(encoding='utf-8'),
                                 self.trabajo['chat'])
                llamada = next(l for l in reversed(self.llamadas()) if l['motor'] == motor)
                self.assertNotIn('Antes de terminar, relee', self.prompt(llamada))
