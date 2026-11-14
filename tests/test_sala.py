#!/usr/bin/python3
"""Pruebas de las salas (diseño v2.1, sección C). Todo corre en carpetas falsas en /tmp."""
import importlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

RAIZ = Path(__file__).resolve().parent.parent
MARCADOR_FALSO = '''#!/bin/bash
# Marcador falso: guarda la fila de sala en una base mínima.
if [ "$1" = "tarea" ]; then echo 7; exit 0; fi
if [ "$1" = "encolar" ]; then printf '%s\\n' "$2" >> "$MARCADOR_COLA"; exit 0; fi
if [ "$1" = "procesar" ]; then
  /usr/bin/python3 - <<'PY'
import json, os, sqlite3
con = sqlite3.connect(os.environ['MARCADOR_DB'])
con.execute('CREATE TABLE IF NOT EXISTS salas(uuid TEXT PRIMARY KEY, mensajes INTEGER)')
for linea in open(os.environ['MARCADOR_COLA']):
    d = json.loads(linea)
    if d.get('evento') == 'sala':
        con.execute('INSERT OR IGNORE INTO salas VALUES (?, ?)', (d['uuid'], d['mensajes']))
con.commit()
PY
  exit 0
fi
exit 0
'''


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='sala-prueba-', dir='/tmp'))
        self.humanify = self.tmp / 'Humanify'
        self.proyectos = self.tmp / 'Proyectos'
        self.proyecto = self.proyectos / 'Demo'
        (self.humanify / 'registro').mkdir(parents=True)
        (self.humanify / 'instalacion/plantillas').mkdir(parents=True)
        shutil.copy(str(RAIZ / 'instalacion/plantillas/sala-REGLAS.md'), str(self.humanify / 'instalacion/plantillas'))
        (self.proyecto / 'Bases').mkdir(parents=True)
        (self.proyecto / 'CLAUDE.md').write_text('# Demo\n')
        (self.proyecto / 'Bases/APRENDIZAJES.md').write_text('# Aprendizajes\n')
        self.env_previo = dict(os.environ)
        os.environ.update(HUMANIFY_DIR=str(self.humanify), HUMANIFY_PROYECTOS=str(self.proyectos),
                          SALA_SIN_FONDO='1', MARCADOR_DB=str(self.tmp / 'marcador.db'),
                          MARCADOR_COLA=str(self.tmp / 'cola.jsonl'))
        sys.path.insert(0, str(RAIZ / 'hooks'))
        import _humanify
        import _sala
        importlib.reload(_humanify)
        self.S = importlib.reload(_sala)
        self.reloj = [1000000.0]
        self.S.ahora = lambda: self.reloj[0]
        self.lanzados = []
        self.S.lanzar_fondo = lambda comando, env=None: self.lanzados.append(comando)
        self.cwd = os.getcwd()
        os.chdir(str(self.proyecto))

    def tearDown(self):
        os.chdir(self.cwd)
        os.environ.clear()
        os.environ.update(self.env_previo)
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def con_marcador(self):
        herramientas = self.humanify / 'herramientas'
        herramientas.mkdir(exist_ok=True)
        (herramientas / 'marcador.sh').write_text(MARCADOR_FALSO)

    def abrir(self, agentes='argon:director,sonnet:apoyo'):
        salida = self.S.cmd_abrir('prueba', agentes, 'diseno', 'normal', '')
        return salida.split('uuid ')[1].split(' ')[0]

    def avanzar(self, segundos):
        self.reloj[0] += segundos

    def estado(self, uid):
        return self.S.leer_estado(uid)

    def vigilante_responde(self, uid, veredicto, motivo='ok', lineas=None):
        self.S.claude_json = lambda *a, **k: ({'veredicto': veredicto, 'motivo': motivo, 'a_quien': 'todos'}, {})
        llamada = [c for c in self.lanzados if '_vigilar' in c][-1]
        version = str(lineas if lineas is not None else llamada[-2])
        self.S.vigilar(uid, version, llamada[-1])

    def hasta_trabajo(self, uid):
        self.S.cmd_plan(uid, 'argon', 'hago el boceto')
        self.S.cmd_plan(uid, 'sonnet', 'reviso que se pueda programar')
        self.vigilante_responde(uid, 'OK')
        self.S.cmd_decir(uid, 'argon', 'todos', 'listo')
        self.S.cmd_decir(uid, 'sonnet', 'todos', 'listo')
        self.vigilante_responde(uid, 'OK')
        self.S.cmd_estado(uid)
        self.assertEqual(self.estado(uid)['fase'], 'trabajo')


class LargoPorSala(Base):
    def abrir_cli(self, largo=None):
        args = ['abrir', 'prueba', '--agentes', 'argon:director,sonnet:apoyo', '--tipo', 'diseno']
        if largo is not None:
            args += ['--largo', str(largo)]
        return self.S.main(args).split('uuid ')[1].split(' ')[0]

    def test_por_defecto_200(self):
        uid = self.abrir_cli()
        self.assertEqual(self.estado(uid)['largo'], 200)
        self.assertIn('hasta 200 caracteres', (self.S.carpeta(uid) / 'REGLAS.md').read_text())
        self.assertIn('· largo 200', self.S.main(['estado', uid]))
        with self.assertRaisesRegex(self.S.Rechazo, 'hasta 200 caracteres'):
            self.S.cmd_plan(uid, 'argon', 'x' * 201)
        self.S.cmd_plan(uid, 'argon', 'x' * 200)
        self.S.cmd_plan(uid, 'sonnet', 'plan')
        with self.assertRaisesRegex(self.S.Rechazo, 'tope es 200'):
            self.S.cmd_decir(uid, 'argon', 'todos', 'x' * 201)
        self.S.cmd_decir(uid, 'argon', 'todos', 'x' * 200)

    def test_mensajes_con_tope_600(self):
        uid = self.abrir_cli(600)
        self.assertEqual(self.estado(uid)['largo'], 600)
        self.assertIn('hasta 600 caracteres', (self.S.carpeta(uid) / 'REGLAS.md').read_text())
        self.assertIn('· largo 600', self.S.main(['estado', uid]))
        self.hasta_trabajo(uid)
        with self.assertRaisesRegex(self.S.Rechazo, '650 caracteres; el tope es 600'):
            self.S.cmd_decir(uid, 'argon', 'todos', 'avance: ' + 'x' * 642)
        self.S.cmd_decir(uid, 'argon', 'todos', 'avance: ' + 'x' * 542)
        self.assertTrue(self.S.lineas_chat(uid)[-1].endswith('avance: ' + 'x' * 542))
        self.S.cmd_decir(uid, 'argon', 'todos', 'avance: ' + 'y' * 592)

    def test_plan_usa_el_tope_por_linea_de_la_sala(self):
        uid = self.abrir_cli(600)
        with self.assertRaisesRegex(self.S.Rechazo, 'hasta 600 caracteres'):
            self.S.cmd_plan(uid, 'argon', 'x' * 650)
        self.assertNotIn('plan', self.estado(uid)['participantes']['argon'])
        texto = 'x' * 550 + '\n' + 'y' * 600
        self.S.cmd_plan(uid, 'argon', texto)
        self.assertEqual(self.estado(uid)['participantes']['argon']['plan'], texto)

    def test_valores_invalidos_no_abren_sala(self):
        for largo in ('79', '2001', 'texto', '80.5', ''):
            with self.subTest(largo=largo):
                with self.assertRaisesRegex(self.S.Rechazo, '--largo.*80.*2000'):
                    self.abrir_cli(largo)
        self.assertFalse(self.S.base().exists())
        self.assertFalse((self.proyecto / 'salas').exists())

    def test_largo_sin_valor_se_rechaza(self):
        with self.assertRaisesRegex(self.S.Rechazo, 'falta el valor de --largo'):
            self.S.main(['abrir', 'prueba', '--agentes', 'a:par,b:par', '--largo'])

    def test_limites_inclusivos(self):
        for largo in (80, 2000):
            with self.subTest(largo=largo):
                uid = self.abrir_cli(largo)
                self.assertEqual(self.estado(uid)['largo'], largo)
                self.S.cmd_plan(uid, 'argon', 'x' * largo)
                self.S.cmd_plan(uid, 'sonnet', 'plan')
                self.S.cmd_decir(uid, 'argon', 'todos', 'x' * largo)
                with self.assertRaisesRegex(self.S.Rechazo, 'tope es {}'.format(largo)):
                    self.S.cmd_decir(uid, 'sonnet', 'todos', 'x' * (largo + 1))

    def test_sala_vieja_sin_campo_sigue_en_200(self):
        uid = self.abrir()
        est = self.estado(uid)
        del est['largo']
        self.S.H.atomic_json(self.S.carpeta(uid) / 'estado.json', est)
        self.assertIn('· largo 200', self.S.main(['estado', uid]))
        with self.assertRaisesRegex(self.S.Rechazo, 'hasta 200 caracteres'):
            self.S.cmd_plan(uid, 'argon', 'x' * 201)
        self.S.cmd_plan(uid, 'argon', 'x' * 200)
        self.S.cmd_plan(uid, 'sonnet', 'plan')
        with self.assertRaisesRegex(self.S.Rechazo, 'tope es 200'):
            self.S.cmd_decir(uid, 'argon', 'todos', 'x' * 201)
        self.S.cmd_decir(uid, 'argon', 'todos', 'x' * 200)
        self.assertNotIn('largo', self.estado(uid))


class Fases(Base):
    def test_planes_completos_pasan_a_arranque(self):
        uid = self.abrir()
        self.S.cmd_plan(uid, 'argon', 'hago el boceto')
        self.assertEqual(self.estado(uid)['fase'], 'planes')
        self.S.cmd_plan(uid, 'sonnet', 'reviso')
        self.assertEqual(self.estado(uid)['fase'], 'arranque')
        self.assertTrue(any('_vigilar' in c for c in self.lanzados))

    def test_plan_ausente_vence_y_queda_bandera(self):
        uid = self.abrir()
        self.S.cmd_plan(uid, 'argon', 'hago el boceto')
        self.avanzar(601)
        est = self.estado(uid)
        self.S.cmd_estado(uid)
        est = self.estado(uid)
        self.assertEqual(est['fase'], 'arranque')
        self.assertEqual(est['participantes']['sonnet']['estado'], 'ausente')

    def test_esperar_regresa_si_la_fase_ya_paso(self):
        uid = self.abrir()
        self.hasta_trabajo(uid)
        self.assertIn('fase trabajo', self.S.cmd_esperar(uid, 'argon', 'arranque', tope=1))

    def test_alto_del_vigilante_en_arranque_detiene(self):
        uid = self.abrir()
        self.S.cmd_plan(uid, 'argon', 'a')
        self.S.cmd_plan(uid, 'sonnet', 'b')
        self.vigilante_responde(uid, 'OK')
        self.S.cmd_decir(uid, 'argon', 'todos', 'listo')
        self.S.cmd_decir(uid, 'sonnet', 'todos', 'listo')
        self.vigilante_responde(uid, 'ALTO', 'sonnet no entendió la tarea')
        self.S.cmd_estado(uid)
        self.assertEqual(self.estado(uid)['fase'], 'detenida')
        with self.assertRaisesRegex(self.S.Rechazo, 'ALTO'):
            self.S.cmd_decir(uid, 'argon', 'todos', 'avance: sigo')
        self.assertIn('FIN', self.S.cmd_fin(uid, 'argon', []))

    def test_revision_vencida_sigue_sin_vigilancia(self):
        uid = self.abrir()
        self.S.cmd_plan(uid, 'argon', 'a')
        self.S.cmd_plan(uid, 'sonnet', 'b')
        self.vigilante_responde(uid, 'OK')
        self.S.cmd_decir(uid, 'argon', 'todos', 'listo')
        self.S.cmd_decir(uid, 'sonnet', 'todos', 'listo')
        self.avanzar(181)
        self.S.cmd_estado(uid)
        est = self.estado(uid)
        self.assertEqual(est['fase'], 'trabajo')
        self.assertIn('sin-vigilancia', est['vistas'])


class ReglasDuras(Base):
    def test_tres_listos_no_generan_banderas(self):
        uid = self.abrir('a:par,b:par,c:par')
        for agente in ('a', 'b', 'c'):
            self.S.cmd_plan(uid, agente, 'plan ' + agente)
        self.vigilante_responde(uid, 'OK')
        for agente in ('a', 'b', 'c'):
            self.S.cmd_decir(uid, agente, 'todos', 'listo')
        self.assertEqual(self.estado(uid)['banderas'], [])
        self.vigilante_responde(uid, 'OK')
        self.assertEqual(self.estado(uid)['vigilante']['usadas'], 2)

    def test_repeticion_solo_del_mismo_agente(self):
        uid = self.abrir()
        self.hasta_trabajo(uid)
        self.S.cmd_decir(uid, 'argon', 'todos', 'avance: revisar pruebas')
        self.S.cmd_decir(uid, 'sonnet', 'todos', 'avance: revisar pruebas')
        self.assertEqual(self.estado(uid)['banderas'], [])
        self.S.cmd_decir(uid, 'argon', 'orquestador', 'avance: revisar pruebas')
        self.assertTrue(any(b['clave'].startswith('repite-argon-') for b in self.estado(uid)['banderas']))

    def test_lineas_del_guion_no_cuentan_como_repeticion(self):
        uid = self.abrir('a:par,b:par')
        self.S.cmd_plan(uid, 'a', 'x')
        self.S.cmd_plan(uid, 'b', 'y')
        self.vigilante_responde(uid, 'OK')
        def sembrar(est):
            for texto in ('listo', 'plan entregado', 'GUARDAR: dato', 'FIN: a', 'ALTO: motivo'):
                self.S.agregar_chat(est, '{} a→todos: {}'.format(self.S.hora(), texto))
            return True
        self.S.con_sala(uid, sembrar)
        self.S.cmd_decir(uid, 'a', 'todos', 'GUARDAR: dato')
        self.S.cmd_decir(uid, 'b', 'todos', 'plan entregado')
        self.S.cmd_decir(uid, 'a', 'todos', 'ALTO: motivo')
        self.S.cmd_decir(uid, 'b', 'todos', 'FIN: a')
        self.assertFalse(any(b['clave'].startswith('repite-') for b in self.estado(uid)['banderas']))

    def test_rechazos(self):
        uid = self.abrir()
        with self.assertRaisesRegex(self.S.Rechazo, 'fuera de fase'):
            self.S.cmd_decir(uid, 'argon', 'todos', 'hola')
        self.S.cmd_plan(uid, 'argon', 'a')
        self.S.cmd_plan(uid, 'sonnet', 'b')
        with self.assertRaisesRegex(self.S.Rechazo, 'caracteres'):
            self.S.cmd_decir(uid, 'argon', 'todos', 'x' * 201)
        with self.assertRaisesRegex(self.S.Rechazo, 'no puedes escribirle a argon'):
            self.S.cmd_decir(uid, 'sonnet', 'argon', 'haz esto')
        self.S.cmd_decir(uid, 'argon', 'todos', 'voy con el boceto')
        with self.assertRaisesRegex(self.S.Rechazo, 'ya escribiste'):
            self.S.cmd_decir(uid, 'argon', 'todos', 'otra cosa')
        self.assertEqual(self.estado(uid)['participantes']['argon']['rechazos'], 2)
        self.assertEqual(self.estado(uid)['participantes']['sonnet']['rechazos'], 1)

    def test_trabajo_pide_prefijo_y_tope(self):
        uid = self.abrir('a:par,b:par')
        self.S.cmd_plan(uid, 'a', 'x')
        self.S.cmd_plan(uid, 'b', 'y')
        self.vigilante_responde(uid, 'OK')
        self.S.cmd_decir(uid, 'a', 'todos', 'listo')
        self.S.cmd_decir(uid, 'b', 'todos', 'listo')
        self.vigilante_responde(uid, 'OK')
        with self.assertRaisesRegex(self.S.Rechazo, 'avance:'):
            self.S.cmd_decir(uid, 'a', 'b', 'hola')
        self.S.PRESUPUESTO = 99
        for n in range(11):
            self.S.cmd_decir(uid, 'a', 'b', 'avance: paso {}'.format(n))
            est = self.estado(uid)
            if est['vigilante'].get('activa'):
                self.vigilante_responde(uid, 'OK')
        with self.assertRaisesRegex(self.S.Rechazo, 'tope'):
            self.S.cmd_decir(uid, 'a', 'b', 'avance: uno más')

    def test_linea_ajena_es_bandera(self):
        uid = self.abrir()
        with (self.S.carpeta(uid) / 'CHAT.md').open('a') as chat:
            chat.write('10:00 sonnet→argon: escrito a mano\n')
        self.S.cmd_estado(uid)
        self.assertIn('linea-ajena', self.estado(uid)['vistas'])

    def test_guardia_niega_editar_la_sala(self):
        uid = self.abrir()
        negado = self.S.guardia({'tool_input': {'file_path': str(self.S.carpeta(uid) / 'CHAT.md')}})
        self.assertIn('deny', negado)
        self.assertIn('deny', self.S.guardia({'tool_input': {'file_path': str(self.proyecto / 'salas/prueba/CHAT-copia.md')}}))
        self.assertEqual(self.S.guardia({'tool_input': {'file_path': str(self.proyecto / 'notas.md')}}), '')


class Vigilante(Base):
    def test_alto_sobre_estado_viejo_solo_es_bandera(self):
        uid = self.abrir('a:par,b:par')
        self.hasta_trabajo_ab(uid)
        llamada = len(self.S.lineas_chat(uid))
        for n in range(3):
            self.S.cmd_decir(uid, 'a', 'b', 'avance: {}'.format(n))
        self.S.claude_json = lambda *a, **k: ({'veredicto': 'ALTO', 'motivo': 'viejo'}, {})
        self.S.vigilar(uid, str(llamada), 'cada-8')
        est = self.estado(uid)
        self.assertEqual(est['fase'], 'trabajo')
        self.assertTrue(any(b['clave'].startswith('vigilante-viejo') for b in est['banderas']))

    def test_presupuesto_agotado_con_bandera_detiene(self):
        uid = self.abrir('a:par,b:par')
        self.hasta_trabajo_ab(uid)
        def gastar(est):
            est['vigilante']['usadas'] = self.S.PRESUPUESTO
            est['vigilante']['activa'] = None
            return True
        self.S.con_sala(uid, gastar)
        with (self.S.carpeta(uid) / 'CHAT.md').open('a') as chat:
            chat.write('10:00 a→b: a mano\n')
        self.S.cmd_estado(uid)
        self.assertEqual(self.estado(uid)['fase'], 'detenida')

    def hasta_trabajo_ab(self, uid):
        self.S.cmd_plan(uid, 'a', 'x')
        self.S.cmd_plan(uid, 'b', 'y')
        self.vigilante_responde(uid, 'OK')
        self.S.cmd_decir(uid, 'a', 'todos', 'listo')
        self.S.cmd_decir(uid, 'b', 'todos', 'listo')
        self.vigilante_responde(uid, 'OK')
        self.S.cmd_estado(uid)
        self.assertEqual(self.estado(uid)['fase'], 'trabajo')


class Procesos(Base):
    def test_matar_tumba_el_grupo_verificado(self):
        uid = self.abrir()
        hijo = subprocess.Popen(['/bin/sh', '-c', 'sleep 60 & wait'], start_new_session=True)
        time.sleep(0.2)
        inicio = self.S.inicio_pid(hijo.pid)
        def registrar(est):
            est['participantes']['argon'].update(pid=hijo.pid, inicio=inicio)
            return True
        self.S.con_sala(uid, registrar)
        self.S.matar(uid)
        hijo.wait(timeout=5)
        self.assertIsNotNone(hijo.returncode)

    def test_pid_con_otro_inicio_no_se_toca(self):
        self.assertFalse(self.S.vivo({'pid': os.getpid(), 'inicio': 'otra hora'}))
        self.assertTrue(self.S.vivo({'pid': os.getpid(), 'inicio': self.S.inicio_pid(os.getpid())}))


class CandidatosSala(Base):
    GUARDAR = ('GUARDAR: en tareas de extracción, marcar en cada candidato el solape probable con v1 '
               'y su nivel (N1/N2) ahorra la ronda de contraste al crítico · cambia: el encargo de '
               'extracción pide columnas de solape y nivel · base: hecho')

    def test_fin_sin_cambia_rechaza_todo_y_agente_sigue_activo(self):
        uid = self.abrir()
        antes = self.S.lineas_chat(uid)
        with self.assertRaisesRegex(self.S.Rechazo, 'cambia:'):
            self.S.cmd_fin(uid, 'argon', [self.GUARDAR, 'GUARDAR: protocolo claro para Codex Sol'])
        self.assertEqual(self.estado(uid)['participantes']['argon']['estado'], 'activo')
        self.assertEqual(self.S.lineas_chat(uid), antes)
        self.S.cmd_fin(uid, 'argon', [self.GUARDAR])
        self.assertEqual(self.estado(uid)['participantes']['argon']['estado'], 'terminado')
        self.assertIn(self.GUARDAR, '\n'.join(self.S.lineas_chat(uid)))  # Sin recortar campos.

    def test_detenida_descarta_solo_invalido_y_registra_fin_con_motivo(self):
        uid = self.abrir()
        self.S.cmd_alto(uid, 'detener')
        self.S.cmd_fin(uid, 'argon', ['GUARDAR: protocolo claro para Codex Sol', self.GUARDAR])
        self.assertEqual(self.estado(uid)['participantes']['argon']['estado'], 'terminado')
        self.assertEqual(len(self.S.guardados(uid)), 1)
        chat = '\n'.join(self.S.lineas_chat(uid))
        self.assertIn('GUARDAR descartado: usa GUARDAR:', chat)
        self.assertIn('FIN: argon', chat)

    def test_fin_rechaza_duplicados_del_mismo_lote_y_de_otros_agentes(self):
        uid = self.abrir()
        with self.assertRaisesRegex(self.S.Rechazo, 'repetido'):
            self.S.cmd_fin(uid, 'argon', [self.GUARDAR, self.GUARDAR])
        self.assertEqual(self.S.guardados(uid), [])
        self.S.cmd_fin(uid, 'argon', [self.GUARDAR])
        with self.assertRaisesRegex(self.S.Rechazo, 'repetido'):
            self.S.cmd_fin(uid, 'sonnet', [self.GUARDAR])
        self.assertEqual(self.estado(uid)['participantes']['sonnet']['estado'], 'activo')

    def test_fin_rechaza_duplicado_que_ya_esta_en_destino(self):
        uid = self.abrir()
        texto, cambia, base = self.S.desglosar_guardar(self.GUARDAR)
        self.S.H.escribir_candidato(self.proyecto / 'Bases/APRENDIZAJES.md', texto, base, cambia, 'previo')
        with self.assertRaisesRegex(self.S.Rechazo, 'repetido'):
            self.S.cmd_fin(uid, 'argon', [self.GUARDAR])

    def test_fin_no_permite_base_didier_ni_menciones_con_inferencia(self):
        uid = self.abrir()
        for guardar in (self.GUARDAR.replace('base: hecho', 'base: didier'),
                        'GUARDAR: Didier prefiere reportes cortos · cambia: escribir reportes de menor longitud · base: inferencia'):
            with self.subTest(guardar=guardar), self.assertRaisesRegex(self.S.Rechazo, 'Didier|agentes'):
                self.S.cmd_fin(uid, 'argon', [guardar])
        self.assertEqual(self.S.guardados(uid), [])

    def test_cerrar_revalida_y_conserva_si_entro_un_guardar_invalido(self):
        self.con_marcador()
        uid = self.abrir()
        self.S.cmd_fin(uid, 'argon', [])
        self.S.cmd_fin(uid, 'sonnet', [])
        self.S.con_sala(uid, lambda e: self.S.agregar_chat(e, '{} argon→todos: GUARDAR: ruido'.format(self.S.hora())))
        self.avanzar(601)
        with self.assertRaisesRegex(self.S.Rechazo, 'se conserva.*cambia:'):
            self.S.cmd_cerrar(uid)
        self.assertTrue(self.S.carpeta(uid).exists())
        self.assertNotIn('[por validar]', (self.proyecto / 'Bases/APRENDIZAJES.md').read_text())

    def test_cerrar_revalida_duplicado_nuevo_sin_borrar_la_sala(self):
        self.con_marcador()
        uid = self.abrir()
        self.S.cmd_fin(uid, 'argon', [self.GUARDAR])
        self.S.cmd_fin(uid, 'sonnet', [])
        texto, cambia, base = self.S.desglosar_guardar(self.GUARDAR)
        self.S.H.escribir_candidato(self.proyecto / 'Bases/APRENDIZAJES.md', texto, base, cambia, 'otro rescate')
        self.avanzar(601)
        with self.assertRaisesRegex(self.S.Rechazo, 'se conserva.*repetido'):
            self.S.cmd_cerrar(uid)
        self.assertTrue(self.S.carpeta(uid).exists())

    def test_rescate_del_barrido_descarta_y_loguea_invalidos_y_repetidos(self):
        self.con_marcador()
        uid = self.abrir()
        def llenar(est):
            for n in range(41):
                self.S.agregar_chat(est, '{} argon→todos: avance {}'.format(self.S.hora(), n))
            return True
        self.S.con_sala(uid, llenar)
        resultado = {'guardar': ['GUARDAR: protocolo claro para Codex Sol', self.GUARDAR, self.GUARDAR]}
        with mock.patch.object(self.S, 'claude_json', return_value=(resultado, {})) as llamada:
            self.avanzar(3 * 3600 + 1)
            self.assertIn('1 cerradas', self.S.cmd_barrido())
        self.assertEqual(llamada.call_args.args[-1], 'low')
        self.assertIn('cambia:', llamada.call_args.args[0])
        texto = (self.proyecto / 'Bases/APRENDIZAJES.md').read_text()
        self.assertEqual(texto.count('[por validar]'), 1)
        self.assertIn(' · cambia: el encargo de extracción pide columnas de solape y nivel', texto)
        log = next((self.humanify / 'registro').glob('hooks-*.log')).read_text()
        self.assertEqual(log.count('sala-rescate\trechazado'), 2)
        self.assertIn('repetido', log)
        self.assertFalse(self.S.carpeta(uid).exists())


class Cierre(Base):
    def test_sala_tres_corridas_un_trabajo_y_evidencia_persistente(self):
        # Marcador real, aislado y sin guion califica.sh: ninguna llamada a modelos.
        herramientas = self.humanify/'herramientas'
        herramientas.mkdir()
        import shlex
        (herramientas/'marcador.sh').write_text(
            '#!/bin/bash\nexec /usr/bin/python3 ' + shlex.quote(str(RAIZ/'hooks/_marcador.py')) + ' "$@"\n')
        import _marcador
        import _califica as C
        M = importlib.reload(_marcador)
        uid = self.abrir('sol:par,argon:par,sonnet:par')
        t = self.estado(uid)['tarea']
        with M.conexion() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('UPDATE tareas SET criterio=? WHERE id=?', ('los tres reportan', t))
            db.commit()
        respuestas = self.humanify/'registro/marcador-respuestas'
        respuestas.mkdir()
        (respuestas/'argon-conversacion.json').write_text(json.dumps({'prompt':'encargo agy', 'response':'reporte Argon'}))
        for agente, origen, externo in (('sol','codex','codex:sesion:1'),
                                       ('argon','argon','argon:conversacion'),
                                       ('sonnet','agent','agent:subagente')):
            M.encolar(dict(evento='corrida', tarea=t, origen=origen, id_externo=externo,
                           rol='ejecucion', agente=agente))
            M.procesar()
            with M.conexion() as db:
                self.assertEqual(db.execute("SELECT count(*) FROM trabajos WHERE clase='calificar'").fetchone()[0], 0)
        # Un chat largo permite comprobar el recorte por líneas tras borrar la sala.
        def chat(est):
            for n in range(45):
                self.S.agregar_chat(est, '{} sol→todos: linea {} {}'.format(self.S.hora(), n, 'x' * 65))
            return True
        self.S.con_sala(uid, chat)
        trabajo = Path(self.estado(uid)['trabajo'])
        (trabajo / 'veredicto.md').write_text('Entregado: veredicto comprobable')
        (trabajo / 'plan-x.md').write_text('No es un entregable')
        for agente in ('sol', 'argon', 'sonnet'):
            self.S.cmd_fin(uid, agente, [])
        original = (self.S.carpeta(uid)/'CHAT.md').read_text()
        self.avanzar(601)
        self.S.cmd_cerrar(uid)
        copia = self.humanify/'registro/marcador-evidencia'/('tarea-'+str(t))/'CHAT.md'
        self.assertEqual(copia.read_text(), original)
        self.assertEqual((copia.parent / 'entregables/veredicto.md').read_text(),
                         'Entregado: veredicto comprobable')
        self.assertEqual([p.name for p in (copia.parent / 'entregables').iterdir()], ['veredicto.md'])
        self.assertFalse(self.S.carpeta(uid).exists())
        # Reingresar el recibo de cierre tampoco crea otro trabajo.
        evento = next(json.loads(l) for l in M.COLA.read_text().splitlines() if json.loads(l)['evento']=='sala')
        M.encolar(dict(evento, cerrada_en='2026-11-02T20:00:00+00:00'))
        M.procesar()
        with M.conexion() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM trabajos WHERE clase='calificar'").fetchone()[0], 1)
        with mock.patch.object(C, 'de_codex', return_value=('encargo Sol', 'reporte Sol')), \
                mock.patch.object(C, 'de_subagente', return_value=('encargo Sonnet', 'reporte Sonnet')):
            tarea, corridas, _ = C.leer_tarea(t)
            texto = C.evidencia(tarea, corridas)
        self.assertLessEqual(len(texto), 6000)
        self.assertIn('los tres reportan', texto)
        self.assertIn('--- entregable: veredicto.md ---\nEntregado: veredicto comprobable', texto)
        self.assertNotIn('plan-x.md', texto)
        for agente in ('Sol', 'Argon', 'Sonnet'):
            self.assertIn('--- {} ---\nreporte {}'.format(agente.lower(), agente), texto)
        self.assertIn('linea 44', texto)
        self.assertNotIn('linea 0 ', texto)
        self.assertEqual(texto.count('→todos: linea '), 37)  # 40 líneas menos los tres FIN.

    def test_entregables_respetan_topes_de_archivos_y_bytes(self):
        for modo in ('archivos', 'bytes'):
            with self.subTest(modo=modo):
                self.con_marcador()
                uid = self.abrir()
                trabajo = Path(self.estado(uid)['trabajo'])
                nombres = ['{:02}.txt'.format(n) for n in range(12)]
                for nombre in nombres:
                    (trabajo / nombre).write_bytes(b'x' * (20 * 1024 if modo == 'archivos' else 110 * 1024))
                (trabajo / 'ignorar.py').write_text('no copiar')
                self.terminar(uid, guardar=False)
                self.S.cmd_cerrar(uid)
                destino = self.humanify / 'registro/marcador-evidencia/tarea-7'
                archivos = list((destino / 'entregables').iterdir())
                self.assertEqual(len(archivos), 10 if modo == 'archivos' else 1)
                self.assertLessEqual(sum(p.stat().st_size for p in archivos), 200 * 1024)
                aviso = (destino / 'entregables-omitidos.txt').read_text().splitlines()
                self.assertEqual(len(aviso), 1)
                for nombre in nombres[len(archivos):]:
                    self.assertIn(nombre, aviso[0])

    def test_entregable_mayor_al_tope_no_impide_copiar_el_siguiente(self):
        self.con_marcador()
        uid = self.abrir()
        trabajo = Path(self.estado(uid)['trabajo'])
        (trabajo / 'a-grande.json').write_bytes(b'x' * (200 * 1024 + 1))
        (trabajo / 'veredicto.md').write_text('sí cabe')
        self.terminar(uid)
        self.S.cmd_cerrar(uid)
        destino = self.humanify / 'registro/marcador-evidencia/tarea-7'
        self.assertEqual([p.name for p in (destino / 'entregables').iterdir()], ['veredicto.md'])
        self.assertIn('a-grande.json', (destino / 'entregables-omitidos.txt').read_text())

    def terminar(self, uid, guardar=True):
        self.S.cmd_fin(uid, 'argon', ['GUARDAR: Argon rinde mejor con referencias visuales · cambia: adjuntar referencias visuales al encargo · base: hecho'] if guardar else [])
        self.S.cmd_fin(uid, 'sonnet', ['GUARDAR: revisar factibilidad antes de pulir · cambia: revisar factibilidad antes del pulido · base: inferencia'] if guardar else [])
        self.avanzar(601)

    def test_no_cierra_sin_fin(self):
        uid = self.abrir()
        self.avanzar(601)
        with self.assertRaisesRegex(self.S.Rechazo, 'sin FIN'):
            self.S.cmd_cerrar(uid)

    def test_cierra_guarda_confirma_y_borra(self):
        self.con_marcador()
        uid = self.abrir()
        self.terminar(uid)
        salida = self.S.cmd_cerrar(uid)
        self.assertIn('2 aprendizajes', salida)
        texto = (self.proyecto / 'Bases/APRENDIZAJES.md').read_text()
        self.assertEqual(texto.count('[por validar]'), 2)
        self.assertIn('sala:{}:1'.format(uid[:8]), texto)
        self.assertFalse(self.S.carpeta(uid).exists())
        self.assertFalse((self.proyecto / 'salas/prueba').exists())
        fila = sqlite3.connect(os.environ['MARCADOR_DB']).execute('SELECT uuid FROM salas').fetchone()
        self.assertEqual(fila[0], uid)
        self.assertIn('ya no existe', self.S.cmd_cerrar(uid))

    def test_sin_marcador_conserva_y_restaura(self):
        uid = self.abrir()
        self.terminar(uid)
        with self.assertRaisesRegex(self.S.Rechazo, 'se conserva'):
            self.S.cmd_cerrar(uid)
        self.assertTrue(self.S.carpeta(uid).exists())
        self.assertNotEqual(self.estado(uid)['fase'], 'cerrando')
        # Reintentar no duplica los aprendizajes ya escritos.
        self.con_marcador()
        self.S.cmd_cerrar(uid)
        self.assertEqual((self.proyecto / 'Bases/APRENDIZAJES.md').read_text().count('[por validar]'), 2)

    def test_barrido_respeta_salas_recientes(self):
        self.con_marcador()
        uid = self.abrir()
        self.assertIn('0 cerradas', self.S.cmd_barrido())
        self.assertTrue(self.S.carpeta(uid).exists())
        self.avanzar(3 * 3600 + 1)
        self.assertIn('1 cerradas', self.S.cmd_barrido())
        self.assertFalse(self.S.carpeta(uid).exists())

    def test_subagente_terminado_por_hook(self):
        uid = self.abrir()
        self.S.cmd_unir(uid, 'sonnet', 'abc123')
        self.S.subagente_termino('abc123')
        self.assertEqual(self.estado(uid)['participantes']['sonnet']['estado'], 'terminado')


class VigilanteQueFalla(Base):
    """Sala del 2-nov: tres fallas en 5 s (sandbox sin red) agotaron el presupuesto y detuvieron la sala."""

    def fallar(self, uid):
        def roto(*a, **k):
            raise ValueError('respuesta inválida del modelo')
        self.S.claude_json = roto
        llamada = [c for c in self.lanzados if '_vigilar' in c][-1]
        self.S.vigilar(uid, llamada[-2], llamada[-1])

    def linea_ajena(self, uid, texto):
        with (self.S.carpeta(uid) / 'CHAT.md').open('a') as chat:
            chat.write('10:00 argon→sonnet: {}\n'.format(texto))

    def test_falla_no_gasta_presupuesto_ni_pide_otra_revision(self):
        uid = self.abrir()
        self.hasta_trabajo(uid)
        usadas = self.estado(uid)['vigilante']['usadas']
        self.linea_ajena(uid, 'a mano')
        self.S.cmd_estado(uid)
        self.fallar(uid)
        lanzadas = len(self.lanzados)
        self.S.cmd_estado(uid)
        est = self.estado(uid)
        self.assertEqual(est['vigilante']['usadas'], usadas)
        self.assertEqual(len(self.lanzados), lanzadas)  # la bandera de la falla no dispara otra llamada
        self.assertEqual(est['fase'], 'trabajo')

    def test_dos_fallas_apagan_al_vigilante_y_la_sala_sigue(self):
        uid = self.abrir()
        self.hasta_trabajo(uid)
        self.linea_ajena(uid, 'a mano')
        self.S.cmd_estado(uid)
        self.fallar(uid)
        for n in range(8):
            self.S.cmd_decir(uid, 'argon', 'todos', 'avance: paso {}'.format(n))
        self.fallar(uid)
        est = self.estado(uid)
        self.assertTrue(est['vigilante']['apagado'])
        self.assertIn('vigilante-apagado', est['vistas'])
        def gastar(e):
            e['vigilante']['usadas'] = self.S.PRESUPUESTO
            return True
        self.S.con_sala(uid, gastar)
        self.linea_ajena(uid, 'otra a mano')
        lanzadas = len(self.lanzados)
        self.S.cmd_estado(uid)
        self.assertEqual(self.estado(uid)['fase'], 'trabajo')
        self.assertEqual(len(self.lanzados), lanzadas)

    def test_dentro_del_sandbox_de_codex_no_se_lanza(self):
        del os.environ['SALA_SIN_FONDO']
        os.environ['CODEX_SANDBOX'] = 'seatbelt'
        uid = self.abrir()
        self.S.cmd_plan(uid, 'argon', 'a')
        self.S.cmd_plan(uid, 'sonnet', 'b')
        self.assertEqual(self.estado(uid)['fase'], 'arranque')
        self.assertEqual(self.estado(uid)['vigilante']['usadas'], 0)
        self.S.cmd_decir(uid, 'argon', 'todos', 'listo')
        self.S.cmd_decir(uid, 'sonnet', 'todos', 'listo')
        self.assertEqual(self.estado(uid)['fase'], 'revision')
        self.assertFalse([c for c in self.lanzados if '_vigilar' in c])
        del os.environ['CODEX_SANDBOX']
        self.S.cmd_estado(uid)  # el siguiente comando fuera del sandbox pide la revisión obligatoria
        self.assertEqual(self.estado(uid)['vigilante']['usadas'], 1)
        self.assertEqual([c for c in self.lanzados if '_vigilar' in c][-1][-1], 'arranque')

    def test_vigilante_cero_salta_la_revision(self):
        uid = self.S.main(['abrir', 'prueba', '--agentes', 'argon:director,sonnet:apoyo', '--vigilante', '0']
                          ).split('uuid ')[1].split(' ')[0]
        self.S.cmd_plan(uid, 'argon', 'a')
        self.S.cmd_plan(uid, 'sonnet', 'b')
        self.S.cmd_decir(uid, 'argon', 'todos', 'listo')
        self.S.cmd_decir(uid, 'sonnet', 'todos', 'listo')
        self.S.cmd_estado(uid)
        self.assertEqual(self.estado(uid)['fase'], 'trabajo')
        self.assertFalse([c for c in self.lanzados if '_vigilar' in c])


class Platica(Base):
    def setUp(self):
        super().setUp()
        shutil.copy(str(RAIZ / 'instalacion/plantillas/sala-REGLAS-platica.md'),
                    str(self.humanify / 'instalacion/plantillas'))

    def abrir_platica(self):
        return self.S.main(['abrir', 'charla', '--agentes', 'argon:director,opus:par', '--modo', 'platica',
                            '--largo', '600', '--mensajes', '3']).split('uuid ')[1].split(' ')[0]

    def test_entra_directo_a_conversar_sin_prefijos(self):
        uid = self.abrir_platica()
        self.assertEqual(self.estado(uid)['fase'], 'trabajo')
        reglas = (self.S.carpeta(uid) / 'REGLAS.md').read_text()
        self.assertIn('600 caracteres', reglas)
        self.assertIn('máximo 3 por agente', reglas)
        self.S.cmd_decir(uid, 'argon', 'opus', 'Oye Opus, ¿qué tenemos de Monat? ' + 'x' * 400)
        self.S.cmd_decir(uid, 'opus', 'argon', 'Va, déjame ver')
        self.assertIn('argon→opus: Oye Opus', self.S.lineas_chat(uid)[0])

    def test_tope_de_mensajes_de_la_sala(self):
        uid = self.abrir_platica()
        for n in range(3):
            self.S.cmd_decir(uid, 'argon', 'todos', 'idea {}'.format(n))
        with self.assertRaisesRegex(self.S.Rechazo, 'tope de 3'):
            self.S.cmd_decir(uid, 'argon', 'todos', 'una más')

    def test_orquestador_entra_sin_topes(self):
        uid = self.abrir_platica()
        self.assertEqual(self.S.cmd_decir(uid, 'orquestador', 'todos', 'Didier dice: más vida'), 'enviado')
        self.assertIn('orquestador→todos: Didier dice: más vida', self.S.lineas_chat(uid)[-1])
        with self.assertRaisesRegex(self.S.Rechazo, 'destinatario'):
            self.S.cmd_decir(uid, 'orquestador', 'nadie', 'hola')

    def test_escuchar_entrega_solo_lo_nuevo(self):
        uid = self.abrir_platica()
        self.assertIn('nada nuevo', self.S.cmd_escuchar(uid, 'opus', tope=-1))
        self.S.cmd_decir(uid, 'argon', 'todos', 'arranquemos')
        self.assertIn('arranquemos', self.S.cmd_escuchar(uid, 'opus', tope=-1))
        self.assertIn('nada nuevo', self.S.cmd_escuchar(uid, 'opus', tope=-1))

    def test_esperar_regresa_sin_error_al_vencer(self):
        uid = self.abrir()
        self.assertIn('todavía en planes', self.S.cmd_esperar(uid, 'argon', 'trabajo', tope=-1))

    def test_valores_invalidos(self):
        for opcion, valor in (('--modo', 'libre'), ('--mensajes', '0'), ('--vigilante', '13')):
            with self.assertRaises(self.S.Rechazo):
                self.S.main(['abrir', 'x', '--agentes', 'a,b', opcion, valor])

    def test_ver_encuentra_la_sala_por_nombre(self):
        uid = self.abrir_platica()
        self.assertEqual(self.S.buscar_sala('charla'), uid)
        with self.assertRaises(self.S.Rechazo):
            self.S.buscar_sala('no-existe')


if __name__ == '__main__':
    unittest.main()
