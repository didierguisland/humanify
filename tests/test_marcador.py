"""Pruebas aisladas: ninguna llamada real a modelos ni escritura en perfiles."""
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / 'hooks'))
spec = importlib.util.spec_from_file_location('marcador', RAIZ / 'hooks/_marcador.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
import _califica as C


class MarcadorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='marcador-', dir='/tmp')
        self.root = Path(self.tmp.name)
        self.env = dict(os.environ, HUMANIFY_DIR=str(self.root),
                        MARCADOR_DB=str(self.root / 'registro/prueba.db'),
                        MARCADOR_COLA=str(self.root / 'registro/cola.jsonl'),
                        CODEX_HOME=str(self.root / 'codex'), HOME=str(self.root),
                        PYTHONDONTWRITEBYTECODE='1')
        self.parches = [mock.patch.object(m, 'ROOT', self.root),
                        mock.patch.object(m, 'REG', self.root / 'registro'),
                        mock.patch.object(m, 'DB', Path(self.env['MARCADOR_DB'])),
                        mock.patch.object(m, 'COLA', Path(self.env['MARCADOR_COLA']))]
        for parche in self.parches:
            parche.start()

    def tearDown(self):
        # Los procesadores de los envoltorios son independientes: esperar su marca.
        if m.COLA.exists():
            for _ in range(100):
                if not Path(str(m.DB) + '.procesar').exists():
                    break
                time.sleep(.01)
        for parche in reversed(self.parches):
            parche.stop()
        self.tmp.cleanup()

    def cli(self, *args, codigo=0, entrada=None):
        r = subprocess.run(['/bin/bash', str(RAIZ / 'herramientas/marcador.sh')] + list(args),
                           env=self.env, input=entrada, text=True, capture_output=True)
        self.assertEqual(r.returncode, codigo, r.stderr)
        self.assertLessEqual(len((r.stdout+r.stderr).splitlines()), 10)
        return r.stdout.strip()

    def agregar(self, evento):
        with m.conexion() as db:
            db.execute('BEGIN IMMEDIATE')
            resultado = m.aplicar(db, evento)
            db.commit()
            return resultado

    def tarea(self, **campos):
        return self.agregar(dict(evento='tarea', tipo='construir', descripcion='ejemplo', **campos))

    def corrida(self, tarea, **campos):
        datos = dict(evento='corrida', tarea=tarea, origen='manual', id_externo=str(time.time_ns()),
                     rol='ejecucion', agente='sol', esfuerzo='high', tokens_entrada=10,
                     tokens_salida=2, tokens_cache_leida=100, tokens_cache_escrita=4, exacto=1)
        datos.update(campos)
        self.agregar(datos)
        return datos

    def veredicto(self, tarea, juez='calificador', nivel=2):
        self.agregar(dict(evento='veredicto', tarea=tarea, juez=juez, nivel=nivel, motivo='prueba', huella_evidencia='h'))

    def fila(self, consulta, parametros=()):
        with m.conexion() as db:
            return db.execute(consulta, parametros).fetchone()

    def test_esquema_precarga_y_primer_uso(self):
        identificador = self.cli('tarea', '--tipo', 'bug', '--desc', 'Reparar prueba')
        self.assertEqual(identificador, '1')
        self.assertEqual(self.fila('SELECT count(*) FROM tipos')[0], 12)
        self.assertEqual(self.fila('SELECT min(peso),max(peso),min(desde) FROM pesos')[:], (1., 1., dt.date.today().isoformat()))
        self.assertEqual(self.fila('PRAGMA journal_mode')[0], 'wal')
        self.assertEqual(self.fila('PRAGMA busy_timeout')[0], 2000)

    def test_ejecucion_retrabajo_suma_costo_y_tope_uno(self):
        t = self.tarea()
        self.corrida(t)
        self.corrida(t, rol='retrabajo')
        self.veredicto(t)
        r = self.fila('SELECT * FROM _resultados')
        self.assertEqual(r['costo'], 70.)
        self.assertEqual(r['calidad'], 1)
        self.assertEqual(self.fila('SELECT max(vuelta) FROM corridas')[0], 2)

    def test_apoyo_comparte_sin_subir_vuelta(self):
        t = self.tarea()
        self.corrida(t)
        self.corrida(t, rol='apoyo', agente='argon')
        self.assertEqual(self.fila('SELECT compartida FROM tareas')[0], 1)
        self.assertEqual(self.fila('SELECT max(vuelta) FROM corridas')[0], 1)
        self.assertEqual(self.fila('SELECT count(*) FROM eficiencia')[0], 0)

    def test_didier_gana_y_auditor_gana_calificador(self):
        t = self.tarea()
        self.corrida(t)
        self.veredicto(t, 'auditor', 1)
        self.veredicto(t, 'calificador', 2)
        self.assertEqual(self.fila('SELECT calidad FROM _resultados')[0], 1)
        self.veredicto(t, 'didier', 0)
        self.veredicto(t, 'calificador', 2)
        self.assertEqual(self.fila('SELECT calidad FROM _resultados')[0], 0)
        self.assertEqual(self.fila('SELECT acuerdo FROM calibracion')[0], 0)

    def test_nulo_no_evaluable(self):
        t = self.tarea()
        self.corrida(t)
        self.cli('veredicto', '--tarea', str(t), '--juez', 'calificador', '--nivel', 'nulo', '--motivo', 'sin evidencia', '--huella', 'h')
        r = self.fila('SELECT * FROM eficiencia')
        self.assertEqual(r['no_evaluables'], 1)
        self.assertEqual(r['evaluables'], 0)
        self.assertIsNone(r['pct_aceptadas'])

    def test_nueva_huella_invalida_jueces_anteriores(self):
        t = self.tarea()
        self.veredicto(t, 'didier', 2)
        self.agregar(dict(evento='veredicto', tarea=t, juez='calificador', nivel=0,
                          motivo='otra evidencia', huella_evidencia='h-nueva'))
        self.assertEqual(self.fila('SELECT calidad FROM _resultados')[0], 0)

    def test_retrabajo_invalida_tambien_didier(self):
        t = self.tarea()
        self.agregar(dict(evento='veredicto', tarea=t, juez='didier', nivel=2,
                          fecha='2020-01-01', huella_evidencia='h'))
        self.corrida(t, rol='retrabajo')
        self.assertIsNone(self.fila('SELECT calidad FROM _resultados')[0])
        self.veredicto(t)
        self.assertEqual(self.fila('SELECT calidad FROM _resultados')[0], 1)

    def test_calibracion_nulo_no_es_acuerdo(self):
        t = self.tarea()
        self.veredicto(t, 'calificador', 2)
        self.veredicto(t, 'didier', None)
        self.assertEqual(self.fila('SELECT acuerdo FROM calibracion')[0], 0)

    def test_veredicto_anterior_por_microsegundos_no_sigue_vigente(self):
        t = self.tarea()
        self.agregar(dict(evento='veredicto', tarea=t, juez='didier', nivel=2,
                          fecha='2026-11-02T12:00:00.000001+00:00', huella_evidencia='h'))
        self.corrida(t, fecha='2026-11-02T06:00:00.000002-06:00', rol='retrabajo')
        self.assertIsNone(self.fila('SELECT calidad FROM _resultados')[0])

    def test_repetido_y_linea_rota_no_bloquean(self):
        t = self.tarea()
        e = dict(evento='corrida', tarea=t, origen='manual', id_externo='uno', rol='ejecucion')
        m.encolar(e)
        m.append(m.COLA, '{rota')
        m.encolar(e)
        m.encolar(dict(e, id_externo='dos'))
        m.procesar()
        m.procesar()
        self.assertEqual(self.fila('SELECT count(*) FROM corridas')[0], 2)
        self.assertEqual(self.fila('SELECT count(*) FROM _fallos')[0], 1)
        self.assertEqual(self.fila('SELECT marca FROM _colas')[0], m.COLA.stat().st_size)

    def test_reintento_referencia_futura_y_linea_incompleta(self):
        m.encolar(dict(evento='corrida', tarea='futura', origen='manual', id_externo='uno', rol='ejecucion'))
        m.encolar(dict(evento='tarea', uuid='futura', tipo='bug', descripcion='futura'))
        with m.COLA.open('ab') as stream:
            stream.write(b'{"evento":"tarea","uuid":"parcial"}')
        m.procesar()
        self.assertEqual(self.fila('SELECT count(*) FROM corridas')[0], 0)
        m.procesar()
        self.assertEqual(self.fila('SELECT count(*) FROM corridas')[0], 1)
        self.assertEqual(self.fila('SELECT count(*) FROM _fallos')[0], 0)
        with m.COLA.open('ab') as stream:
            stream.write(b'\n')
        m.procesar()
        self.assertEqual(self.fila('SELECT count(*) FROM tareas')[0], 2)

    def test_tokens_desconocidos_null_y_costos_medicion_aparte(self):
        t = self.tarea()
        self.agregar(dict(evento='corrida', tarea=t, origen='manual', id_externo='nulo', rol='ejecucion', tokens_entrada=7))
        r = self.fila('SELECT * FROM corridas')
        self.assertIsNone(r['tokens_salida'])
        self.assertEqual(r['exacto'], 0)
        self.corrida(t, rol='vigilante', tokens_razonamiento=999)
        r = self.fila('SELECT costo,costo_medir FROM _resultados')
        self.assertEqual(tuple(r), (7., 35.))

    def test_peso_vigente_en_fecha(self):
        t = self.tarea()
        with m.conexion() as db:
            db.execute('BEGIN IMMEDIATE')
            db.executemany('INSERT INTO pesos VALUES (?,?,?,?)', [('sol', 'high', 2., '2020-01-01'), ('sol', 'high', 3., '2021-01-01')])
            db.commit()
        self.corrida(t, fecha='2020-06-01T12:00:00+00:00')
        self.assertEqual(self.fila('SELECT costo FROM _resultados')[0], 70.)

    def test_sugerir_cuatro_y_riesgo_caro(self):
        for _ in range(4):
            t = self.tarea()
            self.corrida(t)
            self.veredicto(t)
        self.assertIn('pocos datos', self.cli('sugerir', '--tipo', 'construir'))
        for _ in range(5):
            t = self.tarea()
            self.corrida(t)
            self.veredicto(t)
        normal = self.cli('sugerir', '--tipo', 'construir')
        self.assertIn('Explorar', normal)
        self.assertIn('No demuestra equivalencia', normal)
        self.assertNotIn('Explorar', self.cli('sugerir', '--tipo', 'construir', '--riesgo', 'caro'))

    def test_sin_tipo_excluido(self):
        t = self.agregar(dict(evento='tarea', tipo='sin-tipo', tipo_sugerido='bug'))
        self.corrida(t)
        self.veredicto(t)
        self.assertEqual(self.fila('SELECT count(*) FROM eficiencia')[0], 0)

    def test_sala_y_vigilante_contrato_exacto(self):
        t = self.tarea()
        evento = {'evento': 'sala', 'uuid': 'sala-uno', 'id_externo': 'sala:sala-uno', 'tarea': t,
                  'agentes': ['sol', 'argon'], 'mensajes': 4, 'rechazos': 0, 'banderas': 1,
                  'altos': 0, 'llamadas_vigilante': 1, 'forzada': False}
        for _ in range(2):
            m.encolar(evento)
        m.encolar({'evento': 'corrida', 'tarea': t, 'origen': 'vigilante', 'id_externo': 'vigilante:sala-uno:1',
                   'rol': 'vigilante', 'agente': 'opus', 'esfuerzo': 'medium', 'cuenta': 'claude',
                   'tokens_entrada': 10, 'tokens_salida': 2, 'tokens_cache_leida': 0,
                   'tokens_cache_escrita': 0, 'segundos': 1.2, 'exacto': True})
        m.procesar()
        self.assertEqual(self.fila('SELECT count(*) FROM salas')[0], 1)
        self.assertEqual(self.fila('SELECT con_sala FROM salas_metricas')[0], 1)
        self.assertEqual(self.fila('SELECT costo_medir FROM _resultados')[0], 20.)

    def test_adaptador_subagente_formato_real(self):
        ruta = self.root / 'agent-falso.jsonl'
        filas = [{'type': 'assistant', 'message': {'id': 'msg-'+str(i), 'model': 'claude-sonnet-4-5',
                  'usage': {'input_tokens': 10, 'output_tokens': 3, 'cache_read_input_tokens': 100, 'cache_creation_input_tokens': 2}}} for i in range(2)]
        ruta.write_text('\n'.join(map(json.dumps, filas)))
        payload = {'tool_input': {'description': '[bug#12] Reparar'},
                   'tool_response': {'status': 'async_launched', 'agentId': 'falso', 'resolvedModel': 'sonnet'}}
        r = m.de_subagente(payload, ruta)
        self.assertEqual((r['tokens_entrada'], r['tokens_salida'], r['tokens_cache_leida']), (20, 6, 200))
        self.assertEqual((r['tarea'], r['rol'], r['exacto']), (12, 'retrabajo', 1))
        for texto, esperado in [('[bug]', ('normal', 'ejecucion', None)), ('[bug!]', ('caro', 'ejecucion', None)),
                                 ('[bug+4]', ('normal', 'apoyo', 4)), ('[bug@7]', ('normal', 'ejecucion', 7))]:
            etiqueta = m.etiqueta(texto)
            self.assertEqual((etiqueta['riesgo'], etiqueta['rol'], etiqueta['tarea']), esperado)

    def test_adaptador_codex_total_y_reanudacion(self):
        ruta = self.root / 'sesion.jsonl'
        primero = dict(input_tokens=100, cached_input_tokens=20, output_tokens=30, reasoning_output_tokens=10)
        ultimo = dict(input_tokens=180, cached_input_tokens=40, output_tokens=70, reasoning_output_tokens=20)
        ruta.write_text('\n'.join(json.dumps({'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {'total_token_usage': total}}}) for total in (primero, ultimo)))
        r = m.de_codex(ruta)
        self.assertEqual((r['tokens_entrada'], r['tokens_salida']), (140, 70))
        r = m.de_codex(ruta, primero)
        self.assertEqual((r['tokens_entrada'], r['tokens_salida'], r['tokens_cache_leida'], r['tokens_razonamiento']), (60, 40, 20, 10))

    def test_adaptador_agy_formato_real(self):
        r = m.de_agy({'usage': {'input_tokens': 10, 'output_tokens': 8, 'thinking_tokens': 3, 'cache_read_tokens': 2},
                      'duration_seconds': 1.25, 'conversation_id': 'falsa'})
        self.assertEqual((r['tokens_entrada'], r['tokens_salida'], r['tokens_razonamiento']), (10, 8, 3))
        self.assertEqual((r['id_externo'], r['segundos'], r['exacto']), ('argon:falsa', 1.25, 1))
        self.assertIsNone(m.de_agy({})['tokens_entrada'])

    def test_hooks_delegacion_stop_y_payload_desconocido(self):
        transcript = self.root / 'parent' / 'subagents/agent-falso.jsonl'
        transcript.parent.mkdir(parents=True)
        transcript.write_text(json.dumps({'message': {'usage': {'input_tokens': 1, 'output_tokens': 2, 'cache_read_input_tokens': 0, 'cache_creation_input_tokens': 0}}})+'\n')
        payload = {'tool_input': {'description': '[bug!] Ejemplo'}, 'tool_response': {'status': 'async_launched', 'agentId': 'falso', 'resolvedModel': 'sonnet'}, 'transcript_path': str(self.root / 'parent.jsonl')}
        with mock.patch.object(m, 'segundo_plano'):
            m.hook('hook-delegacion', payload)
            m.hook('hook-subagente', {'agent_id': 'falso'})
            m.hook('hook-subagente', {'nuevo_campo': 42})
        m.procesar()
        self.assertEqual(self.fila('SELECT riesgo FROM tareas')[0], 'caro')
        self.assertEqual(self.fila('SELECT tokens_salida FROM corridas')[0], 2)
        self.assertTrue(any(json.loads(p.read_text()).get('nuevo_campo') == 42 for p in (m.REG/'marcador-payloads').glob('*.json')))

    def test_encolar_no_abre_base(self):
        self.cli('encolar', json.dumps({'evento': 'tarea', 'uuid': 'sin-base'}))
        self.assertFalse(m.DB.exists())
        self.assertTrue(m.COLA.exists())

    def test_hook_stop_solo_ruta_conserva_tarea(self):
        ruta = self.root/'agent-ruta.jsonl'
        ruta.write_text(json.dumps({'message': {'usage': {'input_tokens': 3, 'output_tokens': 5}}})+'\n')
        with mock.patch.object(m, 'segundo_plano'):
            m.hook('hook-delegacion', {'tool_input': {'description': '[bug!] una tarea'},
                                     'tool_response': {'agentId': 'ruta', 'resolvedModel': 'sonnet'}})
            m.hook('hook-subagente', {'agent_transcript_path': str(ruta)})
        m.procesar()
        self.assertEqual(self.fila('SELECT count(*) FROM tareas')[0], 1)
        self.assertEqual(self.fila('SELECT tipo,riesgo FROM tareas')[:], ('bug', 'caro'))
        self.assertEqual(self.fila('SELECT tarea,tokens_salida FROM corridas')[:], (1, 5))

    def test_procesadores_simultaneos(self):
        t = self.tarea()
        for i in range(40):
            m.encolar(dict(evento='corrida', tarea=t, origen='manual', id_externo=str(i), rol='ejecucion'))
        comando = ['/bin/bash', str(RAIZ/'herramientas/marcador.sh'), 'procesar']
        procesos = [subprocess.Popen(comando, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
        for p in procesos:
            out, err = p.communicate(timeout=10)
            self.assertEqual(p.returncode, 0, err)
        self.assertEqual(self.fila('SELECT count(*) FROM corridas')[0], 40)

    def test_calificador_en_segundo_plano_reintentos_y_tope(self):
        t = self.tarea()
        self.corrida(t)
        herramientas = self.root/'herramientas'
        herramientas.mkdir()
        (herramientas/'califica.sh').write_text('#!/bin/bash\nexit 0\n')
        with mock.patch.object(m.subprocess, 'Popen') as lanzar:
            m.procesar()
            self.assertEqual(lanzar.call_args[0][0][-1], str(t))
            self.assertTrue(lanzar.call_args[1]['start_new_session'])
            m.procesar()
            self.assertEqual(lanzar.call_count, 1)
            for _ in range(3):
                with m.conexion() as db:
                    db.execute('BEGIN IMMEDIATE')
                    db.execute("UPDATE trabajos SET fecha='2020-01-01T00:00:00+00:00'")
                    db.commit()
                m.procesar()
            self.assertEqual(lanzar.call_count, 3)
        self.assertEqual(self.fila('SELECT intentos FROM trabajos')[0], 3)

    def test_auditar_dos_de_semana_con_calificador(self):
        validas = []
        for _ in range(4):
            t = self.tarea()
            self.veredicto(t)
            validas.append(t)
        vieja = self.tarea(fecha='2020-01-01')
        self.veredicto(vieja)
        self.tarea()
        self.cli('auditar')
        with m.conexion() as db:
            tareas = [r[0] for r in db.execute("SELECT tarea FROM trabajos WHERE clase='auditar'")]
        self.assertEqual(len(tareas), 2)
        self.assertTrue(set(tareas).issubset(validas))

    def test_resumen_legible_y_pendientes(self):
        t = self.tarea(fecha='2020-01-01')
        self.assertIn(str(t)+' ·', self.cli('pendientes'))
        t = self.tarea()
        self.corrida(t)
        self.cli('corregir', str(t), '2', 'aceptada')
        texto = self.cli('resumen')
        self.assertIn('1/1 aceptadas', texto)
        self.assertIn('Sin sala', texto)
        self.assertLessEqual(len(texto.splitlines()), 10)

    def test_base_ocupada_codigo_tres(self):
        self.tarea()
        with sqlite3.connect(str(m.DB), isolation_level=None) as db:
            db.execute('BEGIN IMMEDIATE')
            self.cli('tarea', '--tipo', 'bug', '--desc', 'ocupada', codigo=3)
            db.rollback()

    def test_errores_codigo_dos(self):
        self.cli('encolar', '{rota', codigo=2)
        self.cli('tarea', '--tipo', 'bug', '--desc', 'x'*121, codigo=2)
        self.cli('resumen', '--dias', '-1', codigo=2)

    def test_medicion_alerta_solo_sobre_8000_e_idempotente(self):
        t = self.tarea()
        for rol in ('calificador', 'vigilante', 'auditor'):
            for total in (3000, 8000, 9000):
                with self.subTest(rol=rol, total=total):
                    evento = dict(evento='corrida', tarea=t, origen=rol, id_externo=rol+str(total),
                                  rol=rol, agente='opus', tokens_entrada=1000, tokens_cache_leida=1000,
                                  tokens_cache_escrita=500, tokens_salida=total-2500,
                                  tokens_razonamiento=10000)
                    m.encolar(evento)
                    m.encolar(evento)
        m.procesar()
        m.procesar()
        self.assertEqual(self.fila('SELECT count(*) FROM corridas')[0], 9)
        self.assertEqual(self.fila('SELECT count(*) FROM alertas')[0], 3)
        self.assertEqual(self.fila('SELECT count(*) FROM alertas a JOIN corridas c ON c.id=a.corrida '
                                   'WHERE c.tokens_salida=6500 AND a.tarea=?', (t,))[0], 3)

    def test_ejecucion_cuatro_previas_no_alerta(self):
        t = self.tarea()
        for _ in range(4):
            self.corrida(t, tokens_entrada=1000, tokens_salida=0, tokens_cache_leida=0, tokens_cache_escrita=0)
        self.corrida(t, tokens_entrada=4000, tokens_salida=0, tokens_cache_leida=0, tokens_cache_escrita=0)
        self.assertEqual(self.fila('SELECT count(*) FROM alertas')[0], 0)

    def test_ejecucion_cinco_previas_mediana_y_filtros(self):
        t = self.tarea()
        otro_tipo = int(self.cli('tarea', '--tipo', 'bug', '--desc', 'otro tipo'))
        # Estas corridas no deben alterar la mediana de sol/construir.
        for _ in range(5):
            self.corrida(otro_tipo, tokens_entrada=90000)
            self.corrida(t, agente='flash', tokens_entrada=90000)
            self.corrida(t, rol='apoyo', tokens_entrada=90000)
        for total in (100, 500, 1000, 2000, 90000):
            self.corrida(t, tokens_entrada=total, tokens_salida=0, tokens_cache_leida=0, tokens_cache_escrita=0)
        self.corrida(t, tokens_entrada=4000, tokens_salida=0, tokens_cache_leida=0, tokens_cache_escrita=0)
        alerta = self.fila('SELECT * FROM alertas')
        self.assertEqual(self.fila('SELECT count(*) FROM alertas')[0], 1)
        self.assertEqual(alerta['tarea'], t)
        self.assertIn('mediana 1000', alerta['motivo'])

    def test_ejecucion_tres_veces_mediana_no_alerta(self):
        t = self.tarea()
        for _ in range(5):
            self.corrida(t, tokens_entrada=1000, tokens_salida=0, tokens_cache_leida=0, tokens_cache_escrita=0)
        self.corrida(t, tokens_entrada=3000, tokens_salida=0, tokens_cache_leida=0, tokens_cache_escrita=0)
        self.assertEqual(self.fila('SELECT count(*) FROM alertas')[0], 0)

    def test_alertas_atendidas_quita_aviso_arranque_y_lista_acotada(self):
        t = self.tarea()
        for _ in range(12):
            self.corrida(t, rol='calificador', tokens_entrada=9000)
        texto = self.cli('alertas')
        self.assertIn('sin atender: 12', texto)
        self.assertEqual(len(texto.splitlines()), 10)
        with mock.patch.dict(os.environ, self.env), mock.patch.object(C.S, 'detenidas', return_value=[]):
            # El aviso solo necesita un receptor con add; evita el recorte general del arranque.
            salida = mock.Mock()
            C.H.fase2_avisos(salida, str(self.root))
            avisos = [llamada.args[0] for llamada in salida.add.call_args_list]
            self.assertTrue(any('12 alertas de gasto' in aviso and 'calificador:' in aviso for aviso in avisos))
            self.cli('alertas', '--atendidas')
            salida.reset_mock()
            C.H.fase2_avisos(salida, str(self.root))
            self.assertFalse(any('alertas de gasto' in llamada.args[0] for llamada in salida.add.call_args_list))
        self.assertEqual(self.fila('SELECT count(*) FROM alertas WHERE atendida=0')[0], 0)

    def test_evidencia_sin_sala_conserva_todos_los_reportes_y_tope(self):
        t = self.tarea(evidencia=str(self.root))
        (self.root/'pruebas.txt').write_text('pruebas verificables\n' * 400)
        for n in range(3):
            self.corrida(t, origen='codex', id_externo='codex:sesion{}:1'.format(n), agente='sol{}'.format(n))
        with mock.patch.dict(os.environ, self.env), mock.patch.object(
                C, 'de_codex', side_effect=lambda sesion: ('encargo', 'x' * 5000 + 'reporte '+sesion)):
            tarea, corridas, _ = C.leer_tarea(t)
            texto = C.evidencia(tarea, corridas)
        self.assertLessEqual(len(texto), C.TOPE_EVIDENCIA)
        self.assertIn('pruebas verificables', texto)
        for n in range(3):
            self.assertIn('--- sol{} ---'.format(n), texto)
            self.assertIn('reporte sesion{}'.format(n), texto)

    def test_argon_guarda_prompt_y_llega_a_evidencia(self):
        self.falso('agy', '''import json,sys
print(json.dumps({'conversation_id':'flash-prueba','status':'success','response':'reporte de Flash',
 'duration_seconds':1,'num_turns':1,'usage':{'input_tokens':100,'output_tokens':20}}))
''')
        salida = self.root/'salida-personalizada.json'
        r = subprocess.run(['/bin/bash', str(RAIZ/'herramientas/argon.sh'), '-o', str(salida),
                            '--', '-p', 'el encargo completo', '--model', 'flash'],
                           env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.esperar_cola()
        copia = self.root/'registro/marcador-respuestas/argon-flash-prueba.json'
        self.assertEqual(json.loads(copia.read_text()), {'prompt':'el encargo completo', 'response':'reporte de Flash'})
        with mock.patch.dict(os.environ, self.env), mock.patch.object(C.H, 'REG', self.root/'registro'):
            tarea, corridas, _ = C.leer_tarea(1)
            texto = C.evidencia(tarea, corridas)
        self.assertIn('el encargo completo', texto)
        self.assertIn('--- flash ---\nreporte de Flash', texto)

    def test_sala_chat_largo_respeta_reserva_de_reportes_desiguales(self):
        t = self.tarea(sala='sala-prueba', criterio='criterio de la sala')
        carpeta = self.root/'registro/marcador-evidencia'/('tarea-'+str(t))
        carpeta.mkdir(parents=True)
        (carpeta/'CHAT.md').write_text(('mensaje del chat ' + 'x'*180 + '\n') * 40)
        for n in range(3):
            self.corrida(t, origen='codex', id_externo='codex:sesion{}:1'.format(n), agente='sol'+str(n))
        prefijo = 'INICIO REPORTE LARGO '
        largo = prefijo + 'x'*(700-len(prefijo))
        with mock.patch.dict(os.environ, self.env), mock.patch.object(C.H, 'REG', self.root/'registro'), \
                mock.patch.object(C, 'de_codex', side_effect=[('encargo', largo), ('', 'corto uno'), ('', 'corto dos')]):
            tarea, corridas, _ = C.leer_tarea(t)
            texto = C.evidencia(tarea, corridas)
        self.assertLessEqual(len(texto), C.TOPE_EVIDENCIA)
        self.assertIn(largo, texto)
        self.assertIn('corto uno', texto)
        self.assertIn('corto dos', texto)
        self.assertIn('CHAT (últimas 40 líneas)', texto)

    def test_usd_con_tarifas_oficiales_y_cuota_aparte(self):
        t = self.tarea()
        self.corrida(t, agente='opus', esfuerzo='medium', cuenta='claude', tokens_entrada=1_000_000,
                     tokens_salida=100_000, tokens_cache_leida=1_000_000, tokens_cache_escrita=100_000, etapa='cuadros')
        self.corrida(t, agente='gpt-6.1-sol', cuenta='chatgpt')
        with m.conexion() as db:
            filas = [tuple(r) for r in db.execute('SELECT agente, round(usd,4), cuota FROM _costos_usd ORDER BY id')]
        # 4 + 2 + 0.2 + 0.5 = 6.7 USD; Codex no tiene precio en USD, va por cuota.
        self.assertEqual(filas, [('opus', 6.7, 0), ('gpt-6.1-sol', None, 1)])

    def test_etapas_y_elenco_con_evidencia(self):
        t = self.tarea()
        for n in range(5):
            for agente, aprobada, vueltas in (('opus', 1, 0), ('sonnet', 1, 1)):
                self.agregar(dict(evento='etapa', tarea=t, ruta='r', oficio='video', etapa='sb{}{}'.format(agente, n),
                                  trabajo='especificar', agente=agente, modelo=agente, esfuerzo='medium', vueltas=vueltas,
                                  aprobada=aprobada))
        with m.conexion() as db:
            elegido, casos = m.sugerir_trabajo(db, 'especificar', 'video')
            self.assertEqual((elegido['agente'], casos), ('opus', 10))
            self.assertIn('evidencia x5', elegido['motivo'])
            self.assertEqual(m.sugerir_trabajo(db, 'construir', 'video'), (None, 0))
        with self.assertRaises(ValueError):
            self.agregar(dict(evento='etapa', tarea=t, etapa='x', trabajo='inventado', agente='opus'))
        for n in range(5):
            self.agregar(dict(evento='etapa', tarea=t, ruta='r', oficio='video', etapa='anim{}'.format(n), trabajo='construir',
                              papel='animar', agente='sonnet', modelo='sonnet', esfuerzo='medium', vueltas=0, aprobada=1))
        with m.conexion() as db:
            self.assertEqual(m.sugerir_trabajo(db, 'construir', 'video', 'animar')[0]['agente'], 'sonnet')
            self.assertEqual(m.sugerir_trabajo(db, 'construir', 'video'), (None, 0))  # el papel no se mezcla

    def test_la_corrida_hereda_etapa_y_trabajo_del_conductor(self):
        with mock.patch.dict(os.environ, MARCADOR_ETAPA='render', MARCADOR_TRABAJO='verificar'):
            m.encolar(dict(evento='corrida', tarea=1, origen='x', id_externo='1'))
        evento = json.loads(m.COLA.read_text().splitlines()[-1])
        self.assertEqual((evento['etapa'], evento['trabajo']), ('render', 'verificar'))

    def test_migracion_de_esquema_2_agrega_el_papel(self):
        m.DB.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(m.DB)) as viejo:
            viejo.execute('CREATE TABLE etapas(id INTEGER PRIMARY KEY, tarea INTEGER, etapa TEXT, trabajo TEXT)')
            viejo.execute("INSERT INTO etapas(etapa, trabajo) VALUES ('estilo', 'construir')")
            viejo.execute('PRAGMA user_version=2')
        with m.conexion() as db:
            self.assertIn('papel', {r[1] for r in db.execute('PRAGMA table_info(etapas)')})
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 3)
            self.assertEqual(db.execute('SELECT etapa FROM etapas').fetchone()[0], 'estilo')

    def test_migracion_respalda_y_agrega_columnas(self):
        m.DB.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(m.DB)) as viejo:
            viejo.execute('CREATE TABLE corridas(id INTEGER PRIMARY KEY, tarea INTEGER, origen TEXT, id_externo TEXT, rol TEXT, '
                          'vuelta INTEGER, agente TEXT, esfuerzo TEXT, cuenta TEXT, tokens_entrada INTEGER, tokens_salida INTEGER, '
                          'tokens_razonamiento INTEGER, tokens_cache_leida INTEGER, tokens_cache_escrita INTEGER, segundos REAL, '
                          'exacto INTEGER, pruebas TEXT, fecha TEXT, UNIQUE(origen,id_externo))')
            viejo.execute("INSERT INTO corridas(origen,id_externo,agente) VALUES ('a','1','opus')")
        with m.conexion() as db:
            columnas = {r[1] for r in db.execute('PRAGMA table_info(corridas)')}
            self.assertTrue({'etapa', 'trabajo', 'turnos'} <= columnas)
            self.assertEqual(db.execute('SELECT agente FROM corridas').fetchone()[0], 'opus')
        respaldos = list((self.root / 'registro/respaldos').glob('marcador-v1-*.db'))
        self.assertEqual(len(respaldos), 1)
        with sqlite3.connect(str(respaldos[0])) as copia:
            self.assertEqual(copia.execute('SELECT count(*) FROM corridas').fetchone()[0], 1)

    def test_evidencia_teams_pedido_criterio_y_chat_entero(self):
        carpeta = self.root / 'registro/marcador-evidencia/tarea-5'
        (carpeta / 'entregables').mkdir(parents=True)
        (carpeta / 'entregables/reel.html').write_text('<canvas>' * 3000)
        (carpeta / 'entregables/voz.srt').write_text('1\nTiene toda la cama')
        chat = ['00:00 orquestador→todos: Arranca la ruta video-animado. Brief: perros que duermen contigo']
        chat += ['00:01 opus→todos: turno {} '.format(n) + 'x' * 300 for n in range(40)]
        chat += ['00:02 didier→todos: Me late el pie, pero el cierre no engancha.', '00:03 didier→opus: Aprobado.']
        (carpeta / 'CHAT.md').write_text('\n'.join(chat))
        (carpeta / 'RUBRICA.md').write_text('el gancho abre una duda')
        with mock.patch.object(C.H, 'REG', self.root / 'registro'):
            texto = C.evidencia(dict(id=5, sala='sala', descripcion='teams:ProjectA'), [])
        self.assertIn('RÚBRICA DEL OFICIO', texto)
        self.assertIn('el gancho abre una duda', texto)
        self.assertLessEqual(len(texto), C.TOPE_TEAMS)
        self.assertIn('ENCARGO:\nperros que duermen contigo', texto)
        self.assertIn('- didier: Me late el pie, pero el cierre no engancha.', texto)
        self.assertIn('reel.html (', texto)
        self.assertNotIn('<canvas>', texto)  # el código de animación no se manda cortado
        self.assertIn('Tiene toda la cama', texto)
        self.assertIn('turno 0 ', texto)  # el chat va entero
        self.assertIn('didier→opus: Aprobado.', texto)

    def test_evidencia_prioriza_criterio_entregables_y_recorta_cada_uno(self):
        carpeta = self.root / 'registro/marcador-evidencia/tarea-1'
        (carpeta / 'entregables').mkdir(parents=True)
        for n in range(10):
            (carpeta / 'entregables' / ('{}.md'.format(n))).write_text('dato' * 500 + 'NO DEBE LLEGAR')
        (carpeta / 'CHAT.md').write_text('chat de menor prioridad' * 300)
        with mock.patch.object(C.H, 'REG', self.root / 'registro'), \
                mock.patch.object(C, 'de_codex', return_value=('encargo', 'reporte de menor prioridad')):
            texto = C.evidencia(dict(id=1, sala='sala', criterio='criterio primero'),
                                [dict(rol='ejecucion', id_externo='codex:sesion:1', agente='sol')])
        self.assertLessEqual(len(texto), 6000)
        self.assertIn('criterio primero', texto)
        self.assertIn('--- entregable: 0.md ---\n' + 'dato' * 375, texto)
        self.assertNotIn('NO DEBE LLEGAR', texto)
        self.assertNotIn('reporte de menor prioridad', texto)
        self.assertNotIn('chat de menor prioridad', texto)

    def test_descripcion_larga_no_desplaza_criterio(self):
        texto = C.evidencia(dict(descripcion='x' * 2000, criterio='criterio prioritario'), [])
        self.assertTrue(texto.startswith('CRITERIO DE "QUEDÓ BIEN": criterio prioritario'))
        self.assertLessEqual(len(texto), C.TOPE_EVIDENCIA)

    def falso(self, nombre, codigo):
        carpeta = self.root/'bin'
        carpeta.mkdir(exist_ok=True)
        ruta = carpeta/nombre
        ruta.write_text('#!/usr/bin/python3\n'+codigo)
        ruta.chmod(0o755)
        self.env['PATH'] = str(carpeta)+os.pathsep+self.env.get('PATH', '')

    def esperar_cola(self, corridas=1):
        # Sin sleep arbitrario: el propio procesador serializa con los productores.
        self.cli('procesar')
        self.assertEqual(self.fila('SELECT count(*) FROM corridas')[0], corridas)

    def test_codex_git_y_pruebas_declaradas_llegan_a_evidencia(self):
        repo = self.root / 'repo'
        repo.mkdir()
        for comando in (['git', 'init', '-q'], ['git', 'add', 'archivo.txt']):
            (repo / 'archivo.txt').write_text('inicial\n')
            subprocess.run(comando, cwd=repo, env=self.env, check=True, capture_output=True)
        self.falso('codex', '''import os,json,sys
from pathlib import Path
cwd=sys.argv[sys.argv.index('-C')+1] if '-C' in sys.argv else os.getcwd()
sesion='prueba-'+sys.argv[-1]
p=Path(os.environ['CODEX_HOME'])/'sessions'/(sesion+'.jsonl')
p.parent.mkdir(parents=True,exist_ok=True)
rows=[{'type':'session_meta','payload':{'id':sesion,'cwd':cwd}},
{'payload':{'type':'token_count','info':{'total_token_usage':{'input_tokens':10,'output_tokens':2}}}}]
p.write_text(''.join(json.dumps(r)+'\\n' for r in rows))
(Path(cwd)/'archivo.txt').write_text('modificado\\nsegunda línea\\n')
print('session id: '+sesion,file=sys.stderr)
''')
        casos = [('paso', "test -f archivo.txt || exit 3; i=0; while [ $i -lt 45 ]; do echo linea-$i; i=$((i+1)); done; exit 0"),
                 ('fallo', 'test -f archivo.txt && echo error-verificable; exit 1'), ('na', None)]
        for n, (estado, comando) in enumerate(casos, 1):
            with self.subTest(estado=estado):
                opciones = ['--pruebas', comando] if comando else []
                # También cubrir el cwd cuando no se proporcionó -C.
                motor = ['-C', str(repo)] if comando else []
                r = subprocess.run(['/bin/bash', str(RAIZ / 'herramientas/codex.sh')] +
                                   opciones + ['--'] + motor + [estado],
                                   cwd=repo, env=self.env, capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.esperar_cola(n)
                corrida = self.fila('SELECT * FROM corridas ORDER BY id DESC LIMIT 1')
                self.assertEqual(corrida['pruebas'], estado)
                archivo = self.root / 'registro/marcador-respuestas' / ('codex-prueba-' + estado + '.txt')
                guardado = archivo.read_text()
                self.assertIn('$ git status --short\nAM archivo.txt', guardado)
                self.assertIn('$ git diff --stat\n archivo.txt', guardado)
                with mock.patch.dict(os.environ, self.env), \
                        mock.patch.object(C.H, 'REG', self.root / 'registro'), \
                        mock.patch.object(C, 'de_codex', return_value=('encargo', 'reporte')):
                    tarea, corridas, _ = C.leer_tarea(corrida['tarea'])
                    texto = C.evidencia(tarea, corridas)
                self.assertIn('$ git diff --stat\n archivo.txt', texto)
                if comando:
                    self.assertIn('PRUEBAS: ' + estado, texto)
                if estado == 'paso':
                    self.assertIn('linea-5\n', texto)
                    self.assertIn('linea-44', texto)
                    self.assertNotIn('linea-4\n', guardado)
                elif estado == 'fallo':
                    self.assertIn('error-verificable', texto)
                else:
                    self.assertNotIn('PRUEBAS:', guardado)

    def test_pruebas_agotan_diez_minutos_y_quedan_como_fallo(self):
        proceso = mock.Mock(pid=12345)
        proceso.wait.side_effect = [subprocess.TimeoutExpired('pruebas', 600), -9]
        with mock.patch.object(m.subprocess, 'Popen', return_value=proceso), \
                mock.patch.object(m.subprocess, 'run', return_value=mock.Mock(returncode=1)), \
                mock.patch.object(m.os, 'killpg') as matar:
            estado = m.guardar_evidencia_codex(self.root, 'timeout', 'sleep 1000')
        self.assertEqual(estado, 'fallo')
        self.assertIn('tope de 10 min agotado', (m.REG / 'marcador-respuestas/codex-timeout.txt').read_text())
        matar.assert_called_once_with(12345, m.signal.SIGKILL)

    def test_codex_falso_sesion_propia_y_codigo_salida(self):
        self.falso('codex', '''import os,json,sys
from pathlib import Path
p=Path(os.environ['CODEX_HOME'])/'sessions'/'falso.jsonl'
p.parent.mkdir(parents=True,exist_ok=True)
rows=[{'type':'session_meta','payload':{'id':'sesion-propia','cwd':os.getcwd()}},
{'type':'turn_context','payload':{'model':'sol','effort':'high'}},
{'type':'event_msg','payload':{'type':'token_count','info':{'total_token_usage':{'input_tokens':100,'cached_input_tokens':20,'output_tokens':30,'reasoning_output_tokens':10}}}}]
p.write_text(''.join(json.dumps(r)+'\\n' for r in rows))
print('session id: sesion-propia',file=sys.stderr)
print('respuesta directa')
sys.exit(7)
''')
        r = subprocess.run(['/bin/bash', str(RAIZ/'herramientas/codex.sh'), '--tipo', 'bug', '--', '-m', 'sol', 'prompt'], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 7, r.stderr)
        self.assertIn('respuesta directa', r.stdout)
        self.esperar_cola()
        datos = self.fila('SELECT * FROM corridas')
        self.assertEqual((datos['tokens_entrada'], datos['tokens_salida'], datos['agente']), (80, 30, 'sol'))
        self.assertEqual(datos['id_externo'], 'codex:sesion-propia:1')

    def test_codex_falso_reanudacion_diferencia(self):
        carpeta = Path(self.env['CODEX_HOME'])/'sessions'
        carpeta.mkdir(parents=True)
        ruta = carpeta/'resume.jsonl'
        ruta.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': 'reanudar', 'cwd': os.getcwd()}})+'\n'+json.dumps({'payload': {'type': 'token_count', 'info': {'total_token_usage': {'input_tokens': 100, 'cached_input_tokens': 20, 'output_tokens': 30, 'reasoning_output_tokens': 10}}}})+'\n')
        t = self.tarea()
        self.falso('codex', '''import os,json,sys
from pathlib import Path
p=Path(os.environ['CODEX_HOME'])/'sessions'/'resume.jsonl'
with p.open('a') as f:f.write(json.dumps({'payload':{'type':'token_count','info':{'total_token_usage':{'input_tokens':150,'cached_input_tokens':30,'output_tokens':50,'reasoning_output_tokens':15}}}})+'\\n')
print('session id: reanudar',file=sys.stderr)
''')
        r = subprocess.run(['/bin/bash', str(RAIZ/'herramientas/codex.sh'), '--retrabajo', str(t), '--', 'resume', 'reanudar', 'prompt'], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.esperar_cola()
        r = self.fila('SELECT * FROM corridas')
        self.assertEqual((r['tokens_entrada'], r['tokens_salida'], r['tokens_cache_leida'], r['rol']), (40, 20, 10, 'retrabajo'))

    def test_codex_falso_cd_y_sesion_ajena_descartada(self):
        destino = self.root/'proyecto'
        destino.mkdir()
        self.falso('codex', '''import os,json,sys
from pathlib import Path
cwd=sys.argv[sys.argv.index('--cd')+1]
carpeta=Path(os.environ['CODEX_HOME'])/'sessions'
carpeta.mkdir(parents=True,exist_ok=True)
for nombre,carpeta_cwd in [('propia',cwd),('ajena','/tmp')]:
 rows=[{'type':'session_meta','payload':{'id':nombre,'cwd':carpeta_cwd}},
 {'payload':{'type':'token_count','info':{'total_token_usage':{'input_tokens':100,'cached_input_tokens':20,'output_tokens':30}}}}]
 (carpeta/(nombre+'.jsonl')).write_text(''.join(json.dumps(r)+'\\n' for r in rows))
# Sin id impreso: debe funcionar la búsqueda por creación y cwd.
''')
        r = subprocess.run(['/bin/bash', str(RAIZ/'herramientas/codex.sh'), '--', '--cd', str(destino), 'prompt'], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.esperar_cola()
        self.assertEqual(self.fila('SELECT id_externo FROM corridas')[0], 'codex:propia:1')

    def test_argon_falso_archivo_y_dos_lineas(self):
        self.falso('agy', '''import json,sys
assert sys.argv[1]=='-p'
assert sys.argv[-2:]==['--output-format','json']
print(json.dumps({'response':'hola','conversation_id':'conversacion','duration_seconds':2.5,'usage':{'input_tokens':10,'output_tokens':7,'thinking_tokens':2,'cache_read_tokens':5}}))
''')
        salida = self.root/'respuesta.json'
        r = subprocess.run(['/bin/bash', str(RAIZ/'herramientas/argon.sh'), '--tipo', 'creativo', '-o', str(salida), '--', 'idea', '--model', 'flash', '--output-format', 'text'], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(r.stdout.splitlines()), 2)
        self.assertEqual(r.stdout.splitlines()[0], str(salida))
        self.assertEqual(json.loads(salida.read_text())['response'], 'hola')
        self.esperar_cola()
        r = self.fila('SELECT * FROM corridas')
        self.assertEqual((r['tokens_salida'], r['tokens_razonamiento'], r['id_externo'], r['segundos']), (7, 2, 'argon:conversacion', 2.5))


    def test_argon_en_sala_suma_gasto_en_vivo(self):
        self.falso('agy', '''import json,sys
assert sys.argv[-2:]==['--output-format','stream-json']
for e in [{'event':'init','conversation_id':'sala1'},
          {'event':'step_update','step_update':{'state':'DONE','step_type':'agent_response','text_delta':'hola','usage':{'input_tokens':10,'output_tokens':2,'thinking_tokens':1,'cache_read_tokens':0}}},
          {'event':'result','result':{'conversation_id':'sala1','response':'hola','duration_seconds':3,'usage':{'input_tokens':30,'output_tokens':6,'thinking_tokens':2,'cache_read_tokens':4}}}]:
    print(json.dumps(e), flush=True)
''')
        self.env['SALA_UUID'] = 'f' * 32
        salida = self.root/'sala.json'
        r = subprocess.run(['/bin/bash', str(RAIZ/'herramientas/argon.sh'), '-o', str(salida), '--', 'idea', '--model', 'argon'], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.esperar_cola()
        f = self.fila('SELECT tokens_entrada, tokens_salida, exacto, id_externo FROM corridas')
        self.assertEqual(tuple(f), (30, 6, 1, 'argon:sala1'))

    def test_argon_en_sala_cortado_guarda_lo_gastado(self):
        # agy muere sin evento final (ALTO o cuelgue): queda lo sumado paso a paso, marcado no exacto.
        self.falso('agy', '''import json,sys
paso = lambda n: {'event':'step_update','step_update':{'state':'DONE','step_type':'agent_response','text_delta':'p','usage':{'input_tokens':n,'output_tokens':1,'thinking_tokens':0,'cache_read_tokens':n}}}
for e in [{'event':'init','conversation_id':'cortada'}, paso(100), paso(200)]:
    print(json.dumps(e), flush=True)
sys.exit(143)
''')
        self.env['SALA_UUID'] = 'f' * 32
        salida = self.root/'cortada.json'
        subprocess.run(['/bin/bash', str(RAIZ/'herramientas/argon.sh'), '-o', str(salida), '--', 'idea', '--model', 'flash'], env=self.env, capture_output=True, text=True)
        self.assertTrue(json.loads(salida.read_text())['cortado'])
        self.esperar_cola()
        f = self.fila('SELECT tokens_entrada, tokens_cache_leida, exacto, id_externo FROM corridas')
        self.assertEqual(tuple(f), (300, 300, 0, 'argon:cortada'))


if __name__ == '__main__':
    unittest.main()
