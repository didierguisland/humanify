#!/usr/bin/python3
"""Teams: despachador y procesos falsos, sin modelos ni red."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / 'hooks'))
import _teams as T

MOTOR = '''#!/usr/bin/python3
import json, os, pathlib, subprocess, sys, time
motor, args = sys.argv[1], sys.argv[2:]
def valor(opcion):
    return args[args.index(opcion) + 1]
registro = dict(motor=motor, args=args, cwd=os.getcwd(), stdin=sys.stdin.read(),
                env={k:v for k,v in os.environ.items() if k.startswith(('CLAUDE', 'CODEX', 'SALA_', 'MARCADOR_TAREA'))})
with open(os.environ['FAKE_LOG'], 'a') as f:
    f.write(json.dumps(registro) + '\\n')
if motor == 'marcador':
    print('17' if args[0] == 'tarea' else '')
    sys.exit(0)
if os.environ.get('FAKE_LENTO'):
    hijo = subprocess.Popen(['/bin/sleep', '30'])
    pathlib.Path(os.environ['FAKE_PID']).write_text(str(hijo.pid))
    time.sleep(30)
if os.environ.get('FAKE_FALLA') == motor:
    sys.exit(3)
modelo = valor('--model') if '--model' in args else valor('-m')
respuesta = json.loads(os.environ.get('FAKE_RESPUESTAS', '{}')).get(modelo, 'Hola de ' + modelo)
if motor == 'claude':
    print(json.dumps(dict(result=respuesta, usage=dict(input_tokens=23, output_tokens=7,
                         cache_read_input_tokens=11, cache_creation_input_tokens=5))))
elif motor == 'agy':
    pathlib.Path(valor('-o')).write_text(json.dumps(dict(response=respuesta)))
else:
    pathlib.Path(valor('-o')).write_text(respuesta)
'''


class Base(unittest.TestCase):
    def setUp(self):
        self.temporal = tempfile.TemporaryDirectory(prefix='teams-prueba-')
        self.tmp = Path(self.temporal.name).resolve()
        self.base = self.tmp / 'Humanify'
        self.proyectos = self.tmp / 'Proyectos'
        self.proyecto = self.proyectos / 'Demo con espacios'
        (self.proyecto / 'Bases').mkdir(parents=True)
        (self.proyecto / 'CLAUDE.md').write_text('CONTEXTO DEL PROYECTO', encoding='utf-8')
        (self.proyecto / 'Bases/RELEVO-1.md').write_text('RELEVO VIEJO', encoding='utf-8')
        nuevo = self.proyecto / 'Bases/RELEVO-2.md'
        nuevo.write_text('RELEVO NUEVO', encoding='utf-8')
        os.utime(str(nuevo), (2000000000, 2000000000))
        (self.base / 'equipo').mkdir(parents=True)
        (self.base / 'equipo/EQUIPO.md').write_text('REGLAS PRIMERO', encoding='utf-8')
        for nombre, motor, orden, activo in (('sol', 'codex', 2, True), ('flash', 'agy', 1, True),
                                             ('sonnet', 'claude', 3, True), ('opus', 'claude', 4, False),
                                             ('terra', 'codex', 5, False)):
            carpeta = self.base / 'equipo' / nombre
            carpeta.mkdir()
            (carpeta / 'config.json').write_text(json.dumps(dict(
                motor=motor, modelo=nombre, esfuerzo='low', activo=activo, orden=orden, color='38;5;75')))
            (carpeta / 'ALMA.md').write_text('ALMA ' + nombre)
            (carpeta / 'MEMORIA.md').write_text('MEMORIA ' + nombre)
        (self.base / 'herramientas').mkdir()
        self.bin = self.tmp / 'bin'
        self.bin.mkdir()
        motor = self.tmp / 'motor.py'
        motor.write_text(MOTOR)
        motor.chmod(0o755)
        for nombre, tipo in (('marcador.sh', 'marcador'), ('codex.sh', 'codex'), ('argon.sh', 'agy')):
            (self.base / 'herramientas' / nombre).write_text(
                '#!/bin/bash\nexec /usr/bin/python3 "' + str(motor) + '" ' + tipo + ' "$@"\n')
        claude = self.bin / 'claude'
        claude.write_text('#!/bin/bash\nexec /usr/bin/python3 "' + str(motor) + '" claude "$@"\n')
        claude.chmod(0o755)
        self.env = mock.patch.dict(os.environ, HUMANIFY_DIR=str(self.base),
                                   HUMANIFY_PROYECTOS=str(self.proyectos),
                                   FAKE_LOG=str(self.tmp / 'llamadas.jsonl'),
                                   PATH=str(self.bin) + ':' + os.environ['PATH'])
        self.env.start()
        self.marcador = mock.patch.object(T, 'marcador_sh', return_value=self.base / 'herramientas/marcador.sh')
        self.marcador.start()
        self.canal = T.Canal(self.proyecto)
        self.vistas = []

    def tearDown(self):
        self.marcador.stop()
        self.env.stop()
        self.temporal.cleanup()

    def llamadas(self):
        return [json.loads(l) for l in (self.tmp / 'llamadas.jsonl').read_text().splitlines()]

    def falso(self, respuestas=None, fallan=()):
        respuestas = respuestas or {}
        def turno(nombre, motivo):
            self.vistas.append((nombre, self.canal.prompt(nombre, motivo)))
            if nombre in fallan:
                raise ValueError('fallo simulado')
            return respuestas.get(nombre, 'Opinión de ' + nombre)
        self.canal.turno = turno

    def enviar(self, texto):
        self.canal.recibir(texto)
        self.canal.despachar(texto)

    def nombres(self):
        return [n for n, _ in self.vistas]


class Despacho(Base):
    def test_espejo_de_la_platica_para_el_taller(self):
        espejo = self.tmp / 'trabajo' / 'PLATICA.md'
        espejo.parent.mkdir()
        self.canal.espejos['sonnet'] = espejo
        self.canal.agregar('sol', 'todos', 'detalle para el que construye')
        self.canal.procesar_aviso(('didier', 'sonnet', 'dejé el archivo'))
        self.canal.agregar('sol', 'todos', 'ya no le llega')
        texto = espejo.read_text()
        self.assertIn('sol→todos: detalle para el que construye', texto)
        self.assertNotIn('ya no le llega', texto)

    def test_ronda_orden_y_cada_uno_ve_anteriores(self):
        self.falso()
        self.enviar('qué onda equipo')
        self.assertEqual(self.nombres(), ['flash', 'sol', 'sonnet'])
        self.assertIn('Opinión de flash', self.vistas[1][1][1])
        self.assertIn('Opinión de sol', self.vistas[2][1][1])
        self.assertNotIn('Opinión de sol', self.vistas[0][1][1])

    def test_mencion_solo_uno(self):
        self.falso()
        self.enviar('Hola @Sol')
        self.assertEqual(self.nombres(), ['sol'])

    def test_varias_menciones_sin_repeticion(self):
        self.falso()
        self.enviar('@sonnet y @flash, @flash')
        self.assertEqual(self.nombres(), ['sonnet', 'flash'])

    def test_opus_e_inactivo_se_despiertan_por_mencion(self):
        self.falso()
        self.enviar('@opus y @terra')
        self.assertEqual(self.nombres(), ['opus', 'terra'])

    def test_cadena_mencion_entre_agentes(self):
        self.falso({'sol': 'Que lo vea @sonnet', 'sonnet': 'Listo @opus'})
        self.enviar('@sol revisa')
        self.assertEqual(self.nombres(), ['sol', 'sonnet', 'opus'])
        self.assertIn('Que lo vea @sonnet', self.vistas[1][1][1])

    def test_mencion_adelanta_sin_duplicar_opinion_pendiente(self):
        self.falso({'flash': 'Ven @sonnet'})
        self.enviar('opiniones')
        self.assertEqual(self.nombres(), ['flash', 'sonnet', 'sol'])

    def test_didier_en_ronda_espera_a_que_todos_opinen(self):
        self.falso({'flash': '@didier ¿qué prefieres?'})
        self.enviar('opiniones')
        self.assertEqual(self.nombres(), ['flash', 'sol', 'sonnet'])
        self.assertTrue(self.canal.estado['esperando_didier'])

    def test_didier_detiene_incluso_con_otra_mencion(self):
        self.falso({'flash': '@sol espera, @didier ¿qué prefieres?'})
        self.enviar('@flash opina')
        self.assertEqual(self.nombres(), ['flash'])
        self.assertTrue(json.loads((self.canal.carpeta / 'estado.json').read_text())['esperando_didier'])
        self.enviar('@sol continúa')
        self.assertEqual(self.nombres(), ['flash', 'sol'])
        self.assertFalse(self.canal.estado['esperando_didier'])

    def test_tope_ocho_por_mensaje(self):
        self.falso({'sol': 'Ven @sonnet', 'sonnet': 'Ven @sol'})
        self.enviar('@sol')
        self.assertEqual(len(self.vistas), 8)
        self.assertIn('tope de 8', self.canal.lineas()[-1])
        self.enviar('@sol')
        self.assertEqual(len(self.vistas), 16)

    def test_fallo_no_tumba_canal(self):
        self.falso(fallan=('flash',))
        self.enviar('opiniones')
        self.assertEqual(self.nombres(), ['flash', 'sol', 'sonnet'])
        self.assertTrue(any('flash no contestó (fallo simulado)' in l for l in self.canal.lineas()))
        self.assertIn('sonnet→todos:', self.canal.lineas()[-1])
        self.assertIsNone(self.canal.escribiendo)

    def test_menciones_no_confunden_correo_o_nombres(self):
        self.assertEqual(self.canal.menciones('sol@sol @solana @@sol @desconocido @SOL'), ['sol'])

    def test_hilo_acepta_entrada_mientras_responden_y_serializa(self):
        iniciado, continuar = threading.Event(), threading.Event()
        def turno(nombre, motivo):
            iniciado.set()
            self.assertTrue(continuar.wait(3))
            return 'Listo'
        self.canal.turno = turno
        despachador = T.Despachador(self.canal)
        try:
            despachador.enviar('@sol primero')
            self.assertTrue(iniciado.wait(3))
            despachador.enviar('@flash segundo')
            self.assertTrue(any('segundo' in l for l in self.canal.lineas()))
            self.assertEqual(despachador.cola.qsize(), 1)
        finally:
            continuar.set()
            despachador.cerrar()
        respuestas = [l for l in self.canal.lineas() if 'Listo' in l]
        self.assertIn('sol→todos', respuestas[0])
        self.assertIn('flash→todos', respuestas[1])


class ContextoYRegistro(Base):
    def test_prompt_completo_y_solo_40_lineas(self):
        for i in range(45):
            self.canal.agregar('didier', 'todos', 'linea-' + str(i))
        sistema, resto = self.canal.prompt('sol', 'te mencionaron')
        self.assertTrue(sistema.startswith('REGLAS PRIMERO'))
        for texto in ('ALMA sol', 'MEMORIA sol', 'CONTEXTO DEL PROYECTO', 'RELEVO NUEVO'):
            self.assertIn(texto, sistema)  # lo fijo va en el prefijo cacheable
        for texto in ('linea-44', 'te mencionaron', '400', 'español'):
            self.assertIn(texto, resto)
        self.assertNotIn('CONTEXTO DEL PROYECTO', resto)
        for texto in ('RELEVO VIEJO', 'linea-4\n'):
            self.assertNotIn(texto, resto)
        self.assertIn('linea-5\n', resto)

    def test_cache_por_modificacion_y_nuevo_relevo(self):
        self.canal.contexto()
        with mock.patch.object(Path, 'open', side_effect=AssertionError('relectura')):
            self.assertIn('CONTEXTO', self.canal.contexto())
        claude = self.proyecto / 'CLAUDE.md'
        claude.write_text('CONTEXTO CAMBIADO')
        os.utime(str(claude), (2100000000, 2100000000))
        self.assertIn('CAMBIADO', self.canal.contexto())
        relevo = self.proyecto / 'Bases/RELEVO-3.md'
        relevo.write_text('RELEVO TRES')
        os.utime(str(relevo), (2200000000, 2200000000))
        self.assertIn('RELEVO TRES', self.canal.contexto())
        self.assertNotIn('RELEVO NUEVO', self.canal.contexto())

    def test_contexto_recortado_y_archivos_opcionales(self):
        (self.proyecto / 'CLAUDE.md').write_text('x' * 20000)
        self.assertLessEqual(len(self.canal.contexto()), T.TOPE_CONTEXTO)
        shutil.rmtree(str(self.proyecto / 'Bases'))
        (self.proyecto / 'CLAUDE.md').unlink()
        self.assertEqual(self.canal.contexto(), '')

    def test_tarea_y_huellas_al_abrir(self):
        tarea = self.llamadas()[0]['args']
        self.assertEqual(tarea[:5], ['tarea', '--tipo', 'sin-tipo', '--desc', 'teams:Demo con espacios'])
        self.assertEqual(self.canal.estado['tarea'], 17)
        self.assertEqual(tarea[tarea.index('--sala') + 1], self.canal.estado['uuid'])
        self.canal.agregar('didier', 'todos', 'hola\n\x1b[31mmundo')
        self.assertIn('hola mundo', self.canal.lineas()[0])
        self.assertEqual((self.canal.carpeta / 'huellas').read_text().splitlines(),
                         [T.huella(l) for l in self.canal.lineas()])
        # Reabrir el canal con actividad reciente sigue la misma plática (una sola tarea).
        otra = T.Canal(self.proyecto)
        self.assertEqual(otra.estado['uuid'], self.canal.estado['uuid'])
        self.assertEqual(len([l for l in self.llamadas() if l['args'][0] == 'tarea']), 1)

    def test_selector_solo_carpetas_y_no_acepta_traversal(self):
        (self.proyectos / 'archivo').write_text('no proyecto')
        self.assertEqual(T.proyectos(), [self.proyecto])
        self.assertIsNone(T.proyecto_por_nombre('../Humanify'))
        with mock.patch('builtins.input', side_effect=['incorrecto', '1']), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(T.seleccionar(), self.proyecto)


class Motores(Base):
    def test_codex_home_stdin_vacio_y_efimero(self):
        with mock.patch.dict(os.environ, CLAUDECODE='1', CLAUDE_CODE_TEST='x'):
            self.assertEqual(self.canal.turno('sol', 'te mencionaron'), 'Hola de sol')
        llamada = self.llamadas()[-1]
        self.assertEqual(llamada['motor'], 'codex')
        self.assertEqual(llamada['stdin'], '')
        self.assertEqual(llamada['cwd'], str(self.canal.vacia))
        self.assertEqual(llamada['env']['CODEX_HOME'], str(self.base / 'registro/codex-equipo'))
        args = llamada['args']
        self.assertEqual(args[:3], ['--tarea', '17', '--'])
        for opcion in ('--skip-git-repo-check', 'read-only', 'model_reasoning_effort="low"'):
            self.assertIn(opcion, args)
        self.assertNotIn('--ephemeral', args)  # sin archivo de sesión el marcador no ve tokens
        hogar =self.base / 'registro/codex-equipo'
        self.assertEqual(sorted(p.name for p in hogar.iterdir()), ['auth.json', 'config.toml'])
        for nombre in ('auth.json', 'config.toml'):
            self.assertTrue((hogar / nombre).is_symlink())
            self.assertEqual(os.readlink(str(hogar / nombre)), str(Path.home() / '.codex' / nombre))
        self.assertNotIn('resume', args)
        self.assertNotIn('CLAUDECODE', llamada['env'])

    def test_claude_limpia_entorno_y_anota_gasto(self):
        with mock.patch.dict(os.environ, CLAUDECODE='1', CLAUDE_CODE_ENTRYPOINT='app', CLAUDE_CODE_OTHER='x'):
            self.assertEqual(self.canal.turno('sonnet', 'te toca opinar'), 'Hola de sonnet')
        llamadas = self.llamadas()
        llamada = next(l for l in llamadas if l['motor'] == 'claude')
        self.assertFalse(any(k == 'CLAUDECODE' or k.startswith('CLAUDE_CODE_') for k in llamada['env']))
        self.assertEqual(llamada['cwd'], str(self.canal.vacia))
        args = llamada['args']
        for opcion in ('--safe-mode', '--no-session-persistence', '--tools', '--system-prompt'):
            self.assertIn(opcion, args)
        self.assertEqual(args[args.index('--tools') + 1], '')
        self.assertTrue(args[args.index('--system-prompt') + 1].startswith('REGLAS PRIMERO'))
        evento = json.loads(llamadas[-1]['args'][1])
        self.assertEqual((evento['cuenta'], evento['tarea'], evento['agente'], evento['rol']),
                         ('claude', 17, 'sonnet', 'ejecucion'))
        self.assertEqual((evento['tokens_entrada'], evento['tokens_salida'], evento['tokens_cache_leida']), (23, 7, 11))

    def test_agy_wrapper_contexto_completo_y_sin_sesion(self):
        self.assertEqual(self.canal.turno('flash', 'te toca opinar'), 'Hola de flash')
        llamada = self.llamadas()[-1]
        args = llamada['args']
        self.assertEqual(args[:2], ['--tarea', '17'])
        prompt = args[args.index('-p') + 1]
        for texto in ('ALMA flash', 'MEMORIA flash', 'CONTEXTO', 'No uses herramientas'):
            self.assertIn(texto, prompt)
        self.assertIn('-o', args)
        self.assertNotIn('--conversation', args)
        self.assertEqual(llamada['stdin'], '')

    def test_recorte_respuesta_a_400(self):
        with mock.patch.dict(os.environ, FAKE_RESPUESTAS=json.dumps({'sol': 'x' * 800})):
            self.enviar('@sol')
        self.assertEqual(len(self.canal.lineas()[-1].split(': ', 1)[1]), 400)

    def test_motor_falla_y_sigue_otro(self):
        with mock.patch.dict(os.environ, FAKE_FALLA='agy'):
            self.enviar('opiniones')
        self.assertTrue(any('flash no contestó (salida 3)' in l for l in self.canal.lineas()))
        self.assertIn('sonnet→todos', self.canal.lineas()[-1])

    def test_respuesta_vacia_se_registra_y_canal_sigue(self):
        with mock.patch.dict(os.environ, FAKE_RESPUESTAS=json.dumps({'sol': ''})):
            self.enviar('@sol @sonnet')
        self.assertTrue(any('sol no contestó (respuesta vacía)' in l for l in self.canal.lineas()))
        self.assertIn('sonnet→todos', self.canal.lineas()[-1])

    def test_timeout_mata_grupo_y_canal_sigue(self):
        pidfile = self.tmp / 'hijo.pid'
        with mock.patch.dict(os.environ, FAKE_LENTO='1', FAKE_PID=str(pidfile)), \
                mock.patch.object(T, 'ESPERA', .3), \
                mock.patch.object(T.os, 'killpg', wraps=os.killpg) as matar:
            self.enviar('@sol')
        matar.assert_called_once_with(mock.ANY, signal.SIGKILL)
        self.assertIn('sol no contestó (más de 120 s)', self.canal.lineas()[-1])
        self.assertTrue(pidfile.exists(), 'el motor alcanzó a lanzar un hijo')
        self.enviar('@sol')
        self.assertIn('Hola de sol', self.canal.lineas()[-1])

    def test_cli_una_imprime_solo_lineas_nuevas(self):
        self.canal.agregar('didier', 'todos', 'ANTERIOR')
        salida = subprocess.run(['/bin/bash', str(RAIZ / 'herramientas/humanify.sh'),
                                 '--una', '@sol hola', '--proyecto', self.proyecto.name],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(salida.returncode, 0, salida.stderr)
        self.assertNotIn('ANTERIOR', salida.stdout)
        self.assertIn('didier→sol: @sol hola', salida.stdout)
        self.assertIn('sol→todos: Hola de sol', salida.stdout)


if __name__ == '__main__':
    unittest.main()


class Recorte(unittest.TestCase):
    def test_corta_en_palabra_completa(self):
        from _teams import plano
        self.assertEqual(plano('hola mundo cruel', 12), 'hola mundo…')
        self.assertEqual(plano('corto', 12), 'corto')
        self.assertLessEqual(len(plano('x' * 50, 10)), 10)


class Platica(Base):
    def test_platica_vencida_se_cierra_y_abre_otra(self):
        self.falso()
        self.enviar('@sol hola')
        anterior = dict(self.canal.estado)
        self.canal.estado['actividad'] = 0
        self.canal.guardar_estado()
        otra = T.Canal(self.proyecto)
        self.assertNotEqual(otra.estado['uuid'], anterior['uuid'])
        eventos = [json.loads(l['args'][1]) for l in self.llamadas() if l['args'][0] == 'encolar']
        sala = [e for e in eventos if e['evento'] == 'sala']
        self.assertEqual(len(sala), 1)
        self.assertEqual((sala[0]['uuid'], sala[0]['agentes'], sala[0]['mensajes']), (anterior['uuid'], 'sol', 1))
        chat = (self.base / 'registro/marcador-evidencia/tarea-17/CHAT.md').read_text()
        self.assertIn('@sol hola', chat)
        self.assertIn('plática cerrada', self.canal.lineas()[-1])

    def test_cerrar_dos_veces_no_duplica(self):
        self.canal.cerrar()
        self.canal.cerrar()
        eventos = [l for l in self.llamadas() if l['args'][0] == 'encolar']
        self.assertEqual(len(eventos), 1)

    def test_proceso_entra_al_contexto_por_palabra_y_se_queda(self):
        (self.proyecto / 'fabrica.md').write_text('RUTA DE VIDEO', encoding='utf-8')
        (self.base / 'equipo/PROCESOS.json').write_text(json.dumps(
            {'Demo con espacios': [{'palabras': ['video', 'reel'], 'archivo': 'fabrica.md'}]}))
        self.falso()
        self.enviar('@sol hola')
        self.assertNotIn('RUTA DE VIDEO', self.vistas[-1][1][0])
        self.enviar('@sol hagamos un video')
        self.assertIn('RUTA DE VIDEO', self.vistas[-1][1][0])
        self.enviar('@sol otra cosa')
        self.assertIn('RUTA DE VIDEO', self.vistas[-1][1][0])

    def test_ronda_corta_y_todos(self):
        config = self.base / 'equipo/sol/config.json'
        datos = json.loads(config.read_text())
        datos['ronda'] = False
        config.write_text(json.dumps(datos))
        self.canal.miembros = T.equipo(self.base)
        self.falso()
        self.enviar('opiniones')
        self.assertEqual(self.nombres(), ['flash', 'sonnet'])
        self.vistas.clear()
        self.enviar('@todos opiniones')
        self.assertEqual(self.nombres(), ['flash', 'sol', 'sonnet'])


class TallerFalso:
    def __init__(self):
        self.trabajos = []

    def lanzar(self, trabajo, al_terminar):
        import threading
        self.trabajos.append(trabajo)
        hilo = threading.Thread(target=al_terminar, args=('dejé PROMPT.md en ' + str(trabajo['carpeta']),))
        hilo.start()
        return hilo


class Taller(Base):
    def setUp(self):
        super().setUp()
        self.taller = TallerFalso()
        self.modulo = mock.patch.dict(sys.modules, _taller=self.taller)
        self.modulo.start()

    def tearDown(self):
        self.modulo.stop()
        super().tearDown()

    def una(self, texto):
        self.enviar(texto)
        self.canal.esperar_taller()

    def test_didier_pide_taller_con_chat_completo_y_nadie_mas_despierta(self):
        self.falso()
        self.una('@sol idea larga ' + 'x' * 500)
        self.vistas.clear()
        self.una('/taller sonnet: arma el encargo')
        trabajo = self.taller.trabajos[0]
        self.assertEqual((trabajo['agente'], trabajo['instruccion'], trabajo['tarea']), ('sonnet', 'arma el encargo', 17))
        self.assertIn('x' * 500, trabajo['chat'])  # el chat va sin el tope de 400
        self.assertIn('ALMA sonnet', trabajo['sistema'])
        self.assertTrue(str(trabajo['carpeta']).startswith(str(self.canal.carpeta / 'trabajo')))
        self.assertIn('sonnet→todos: [taller] dejé PROMPT.md', self.canal.lineas()[-1])
        self.assertEqual(self.nombres(), [])  # ronda de opinión aparte: el mensaje fue al taller

    def test_agente_pide_taller_y_al_terminar_lo_despiertan(self):
        self.falso({'sol': 'Va. /taller sonnet: arma el encargo con lo decidido'})
        self.una('@sol decide')
        self.assertEqual(self.taller.trabajos[0]['instruccion'], 'arma el encargo con lo decidido')
        self.assertIn('terminó el trabajo que le pediste a sonnet', self.vistas[1][1][1])
        # Si cada vez vuelve a pedir trabajo, el tope por mensaje de Didier corta la cadena.
        self.assertEqual(len(self.taller.trabajos), 4)
        self.assertEqual(self.nombres(), ['sol'] * 5)

    def test_tope_de_trabajos_por_mensaje(self):
        self.falso()
        self.una(' '.join('/taller sol: t{}'.format(i) for i in range(6)))
        self.assertEqual(len(self.taller.trabajos), 4)
        self.assertTrue(any('tope de 4 trabajos' in l for l in self.canal.lineas()))

    def test_carpeta_elegida_por_didier(self):
        destino = self.tmp / 'mi trabajo'
        self.falso()
        self.una('/carpeta ' + str(destino))
        self.una('/taller sol: hazlo')
        self.assertEqual(self.taller.trabajos[0]['carpeta'], destino)

    def test_aviso_con_pregunta_a_didier_espera(self):
        self.taller.lanzar = lambda trabajo, fin: (self.taller.trabajos.append(trabajo), fin('@didier ¿vertical o cuadrado?'))[0] or __import__('threading').Thread()
        self.falso({'sol': '/taller sonnet: arma'})
        self.una('@sol decide')
        self.assertTrue(self.canal.estado['esperando_didier'])
        self.assertEqual(self.nombres(), ['sol'])


class Comandos(Base):
    def test_carpeta_no_abre_ronda(self):
        self.falso()
        self.enviar('/carpeta ' + str(self.tmp / 'x'))
        self.assertEqual(self.nombres(), [])
        self.assertEqual(self.canal.estado['carpeta'], str((self.tmp / 'x').resolve()))
