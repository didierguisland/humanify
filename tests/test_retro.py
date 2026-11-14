"""Retro contra bases temporales del marcador, sin llamadas a modelos."""
from contextlib import redirect_stdout
import importlib.util
import io
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / 'hooks'))
import _marcador as m

spec = importlib.util.spec_from_file_location('retro', RAIZ / 'herramientas/retro.py')
retro = importlib.util.module_from_spec(spec)
spec.loader.exec_module(retro)


class RetroTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='retro-', dir='/tmp')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / 'registro' / 'prueba #?.db'
        self.env = dict(os.environ, MARCADOR_DB=str(self.db), PYTHONDONTWRITEBYTECODE='1')
        for nombre, valor in (('ROOT', self.root), ('REG', self.db.parent), ('DB', self.db),
                              ('COLA', self.db.parent / 'cola.jsonl')):
            parche = mock.patch.object(m, nombre, valor)
            parche.start()
            self.addCleanup(parche.stop)
        self.numero = 0

    def agregar(self, **evento):
        evento.setdefault('fecha', '2026-11-03T12:00:00+00:00')
        with m.conexion() as db:
            db.execute('BEGIN IMMEDIATE')
            resultado = m.aplicar(db, evento)
            db.commit()
            return resultado

    def tarea(self):
        return self.agregar(evento='tarea', tipo='construir', descripcion='Ruta de prueba')

    def corrida(self, tarea, **campos):
        self.numero += 1
        evento = dict(evento='corrida', tarea=tarea, origen='taller', id_externo=str(self.numero),
                      rol='ejecucion', agente='opus', esfuerzo='medium', cuenta='claude',
                      tokens_entrada=1_000_000, tokens_salida=100_000,
                      tokens_cache_leida=0, tokens_cache_escrita=0, turnos=2)
        evento.update(campos)
        self.agregar(**evento)

    def etapa(self, tarea, nombre, **campos):
        evento = dict(evento='etapa', tarea=tarea, etapa=nombre, ruta='prueba', oficio='video',
                      trabajo='construir', agente='opus', modelo='opus', esfuerzo='medium',
                      motivo='elenco de prueba', vueltas=0, aprobada=1)
        evento.update(campos)
        self.agregar(**evento)

    def cli(self, *args, codigo=0):
        resultado = subprocess.run(['/usr/bin/python3', str(RAIZ / 'herramientas/retro.py'),
                                     *map(str, args)], env=self.env, text=True, capture_output=True)
        self.assertEqual(resultado.returncode, codigo, resultado.stderr)
        return resultado

    def seccion(self, texto, nombre):
        return texto.split('## ' + nombre + '\n', 1)[1].split('\n## ', 1)[0]

    def test_usd_oficiales_y_sumas_por_etapa_agente_y_total(self):
        tarea = self.tarea()
        self.etapa(tarea, 'construccion')
        self.corrida(tarea, etapa='construccion', trabajo='construir')
        self.corrida(tarea, etapa='construccion', trabajo='construir',
                     tokens_cache_leida=1_000_000, tokens_cache_escrita=100_000)
        texto = self.cli('--tarea', tarea).stdout
        etapas = self.seccion(texto, 'Por etapa')
        self.assertIn('| 12.70 USD | 4 | 1000000 | 0 | aprobada |', etapas)
        self.assertIn('| opus | claude | 2 | 12.70 USD | 3300000 |', texto)
        self.assertIn('| USD de Claude | 12.70 USD |', texto)
        # La primera corrida cuesta exactamente 4 + 2 = 6 USD.
        with m.conexion() as db:
            self.assertEqual(db.execute('SELECT usd FROM _costos_usd ORDER BY id').fetchone()[0], 6.0)

    def test_cambios_de_tarifa_se_leen_de_las_vistas(self):
        tarea = self.tarea()
        self.etapa(tarea, 'nueva')
        self.corrida(tarea, etapa='nueva')
        with m.conexion() as db:
            db.execute('INSERT INTO precios VALUES (?,?,?,?,?,?)', ('opus', '2026-11-03', 8, 40, .4, 10))
        texto = self.cli('--tarea', tarea).stdout
        self.assertIn('| USD de Claude | 12.00 USD |', texto)
        self.assertIn('| 12.00 USD | 2 |', self.seccion(texto, 'Por etapa'))

    def test_cuotas_en_tokens_separadas_de_usd(self):
        tarea = self.tarea()
        for agente, cuenta in (('sol', 'chatgpt'), ('flash', 'google')):
            self.corrida(tarea, etapa=cuenta, agente=agente, cuenta=cuenta,
                         tokens_entrada=10, tokens_salida=20, tokens_cache_leida=30,
                         tokens_cache_escrita=40, tokens_razonamiento=15)
        texto = self.cli('--tarea', tarea).stdout
        for agente, cuenta in (('sol', 'chatgpt'), ('flash', 'google')):
            self.assertIn('| {} | {} | 1 | 100 tokens (cuota {}) | 100 |'.format(agente, cuenta, cuenta), texto)
            self.assertIn('| Tokens cuota {} | 100 tokens (cuota) |'.format(cuenta), texto)
        self.assertNotIn('0.00 USD', self.seccion(texto, 'Por agente'))
        self.assertIn('Sin gasto USD registrado.', texto)

    def test_orden_primera_corrida_y_perdedor_justo_debajo(self):
        tarea = self.tarea()
        self.etapa(tarea, 'segunda')
        self.etapa(tarea, 'primera:perdedor', agente='sonnet', ab_gano=0, aprobada=0, vueltas=1)
        self.etapa(tarea, 'primera', motivo='Elegido | por prueba\nrápida', ab_gano=1, vueltas=2)
        # Inserción deliberadamente distinta al orden cronológico.
        self.corrida(tarea, etapa='segunda', fecha='2026-11-03T12:05:00Z')
        self.corrida(tarea, etapa='primera', fecha='2026-11-03T06:00:00-06:00')
        self.corrida(tarea, etapa='primera:perdedor', agente='sonnet', fecha='2026-11-03T12:10:00Z')
        texto = self.cli('--tarea', tarea).stdout
        filas = [f for f in self.seccion(texto, 'Por etapa').splitlines() if f.startswith('| ')][2:]
        self.assertEqual([f.split(' | ')[0][2:] for f in filas], ['primera', 'primera:perdedor', 'segunda'])
        self.assertIn('Elegido &#124; por prueba<br>rápida', filas[0])
        self.assertIn('aprobada; ganó el A/B', filas[0])
        self.assertIn('no aprobada; perdió el A/B', filas[1])
        self.assertIn('| Vueltas totales | 3 |', texto)

    def test_corridas_sin_etapa_y_etapas_sin_ficha_no_se_pierden(self):
        tarea = self.tarea()
        self.corrida(tarea, rol='calificador', etapa=None, turnos=None, origen='manual')
        self.corrida(tarea, etapa='', rol='retrabajo', turnos=None, origen='manual')
        self.corrida(tarea, etapa='vieja', trabajo='verificar')
        texto = self.cli('--tarea', tarea).stdout
        etapas = self.seccion(texto, 'Por etapa')
        self.assertEqual(etapas.count('| sin etapa |'), 1)
        self.assertIn('| 12.00 USD | — | 0 | 1 |', etapas)
        self.assertIn('| vieja | verificar |', etapas)
        self.assertIn('| USD de Claude | 18.00 USD |', texto)
        self.assertIn('| opus | claude | 3 | 18.00 USD |', texto)

    def test_top_tres_y_relacion_por_corrida_del_taller(self):
        tarea = self.tarea()
        for n in range(1, 5):
            self.corrida(tarea, etapa='etapa{}'.format(n), tokens_entrada=n * 1_000_000,
                         tokens_salida=100_000, tokens_cache_leida=500_000)
        self.corrida(tarea, etapa='etapa4', tokens_entrada=0, tokens_salida=0,
                     tokens_cache_leida=0, turnos=0)
        texto = self.cli('--tarea', tarea).stdout
        dinero = self.seccion(texto, 'Dónde se fue el dinero')
        top = dinero.split('Caché leída/salida', 1)[0]
        self.assertIn('| etapa4 | 18.10 | 37.4% |', top)
        self.assertIn('| etapa3 | 14.10 |', top)
        self.assertIn('| etapa2 | 10.10 |', top)
        self.assertNotIn('| etapa1 |', top)
        self.assertEqual(dinero.count('5.00×'), 4)
        self.assertIn('n/d (salida 0)', dinero)

    def test_ficha_sin_nombre_no_pierde_corridas_con_etapa_nula(self):
        tarea = self.tarea()
        self.etapa(tarea, '')
        self.corrida(tarea, etapa=None)
        self.corrida(tarea, etapa='')
        texto = self.cli('--tarea', tarea).stdout
        etapas = self.seccion(texto, 'Por etapa')
        self.assertEqual(etapas.count('| sin etapa |'), 1)
        self.assertIn('| 12.00 USD | 4 |', etapas)
        self.assertIn('| USD de Claude | 12.00 USD |', texto)

    def test_comparacion_totales_y_diferencia(self):
        anterior, actual = self.tarea(), self.tarea()
        for tarea, entrada, minutos in ((anterior, 1_000_000, 10), (actual, 2_500_000, 15)):
            self.corrida(tarea, tokens_entrada=entrada, fecha='2026-11-03T12:00:00Z')
            self.corrida(tarea, tokens_entrada=0, tokens_salida=0,
                         fecha='2026-11-03T12:{:02d}:00Z'.format(minutos))
        texto = self.cli('--tarea', actual, '--contra', anterior).stdout
        comparacion = self.seccion(texto, 'Comparación')
        self.assertIn('| USD de Claude | 12.00 USD | 6.00 USD | +100.0% |', comparacion)
        self.assertIn('| Minutos | 15.0 | 10.0 | +50.0% |', comparacion)
        self.assertIn('| Tokens cuota google | 0 tokens (cuota) | 0 tokens (cuota) | 0.0% |', comparacion)

    def test_comparacion_con_base_cero(self):
        vacia, actual = self.tarea(), self.tarea()
        self.corrida(actual)
        texto = self.cli('--tarea', actual, '--contra', vacia).stdout
        self.assertIn('| USD de Claude | 6.00 USD | 0.00 USD | n/d (base 0) |', texto)
        inversa = self.cli('--tarea', vacia, '--contra', actual).stdout
        self.assertIn('| USD de Claude | 0.00 USD | 6.00 USD | -100.0% |', inversa)

    def test_tarea_inexistente_y_comparacion_inexistente(self):
        tarea = self.tarea()
        for args, identificador in ((('--tarea', 999), 999), (('--tarea', tarea, '--contra', 888), 888)):
            with self.subTest(args=args):
                resultado = self.cli(*args, codigo=2)
                self.assertEqual(resultado.stdout, '')
                self.assertIn('La tarea {} no existe'.format(identificador), resultado.stderr)

    def test_salida_a_archivo_y_db_explicita_prevalece(self):
        tarea = self.tarea()
        self.corrida(tarea)
        salida = self.root / 'retro.md'
        self.env['MARCADOR_DB'] = str(self.root / 'no-existe.db')
        texto = self.cli('--tarea', tarea, '--db', self.db, '-o', salida).stdout
        self.assertEqual(salida.read_text(encoding='utf-8'), texto)
        self.assertFalse(Path(self.env['MARCADOR_DB']).exists())

    def test_base_ausente_no_se_crea(self):
        self.env['MARCADOR_DB'] = str(self.root / 'ausente.db')
        resultado = self.cli('--tarea', 1, codigo=2)
        self.assertIn('retro:', resultado.stderr)
        self.assertFalse(Path(self.env['MARCADOR_DB']).exists())

    def test_salida_no_puede_sobrescribir_la_base(self):
        tarea = self.tarea()
        antes = self.db.read_bytes()
        resultado = self.cli('--tarea', tarea, '-o', self.db, codigo=2)
        self.assertIn('distinta de la base', resultado.stderr)
        self.assertEqual(self.db.read_bytes(), antes)

    def test_conexion_efectivamente_solo_lectura(self):
        tarea = self.tarea()
        conectar = sqlite3.connect

        def comprobar(nombre, **opciones):
            self.assertTrue(nombre.endswith('?mode=ro'))
            self.assertTrue(opciones['uri'])
            db = conectar(nombre, **opciones)
            with self.assertRaisesRegex(sqlite3.OperationalError, 'readonly'):
                db.execute('DELETE FROM tareas')
            db.rollback()
            return db

        with mock.patch.object(retro.sqlite3, 'connect', side_effect=comprobar), redirect_stdout(io.StringIO()):
            self.assertEqual(retro.main(['--tarea', str(tarea), '--db', str(self.db)]), 0)

    def test_base_predeterminada_es_relativa_al_script(self):
        destino = self.root / 'registro/marcador.db'
        with mock.patch.object(retro, 'RAIZ', self.root), mock.patch.object(m, 'DB', destino), mock.patch.dict(os.environ, {}, clear=True), redirect_stdout(io.StringIO()):
            tarea = self.tarea()
            self.assertEqual(retro.main(['--tarea', str(tarea)]), 0)

    def test_sin_corridas_y_tarifa_desconocida(self):
        tarea = self.tarea()
        self.etapa(tarea, 'pendiente')
        texto = self.cli('--tarea', tarea).stdout
        self.assertIn('| pendiente | construir | opus / medium |', texto)
        self.assertIn('| Minutos | 0.0 |', texto)
        self.corrida(tarea, agente='sin-precio')
        texto = self.cli('--tarea', tarea).stdout
        self.assertIn('sin tarifa', texto)
        self.assertIn('el total USD es parcial', texto)

    def test_relacion_cero_y_tokens_desconocidos(self):
        tarea = self.tarea()
        self.corrida(tarea, tokens_salida=0, tokens_cache_leida=10, turnos=None)
        self.corrida(tarea, tokens_salida=None, tokens_cache_leida=None)
        texto = self.cli('--tarea', tarea).stdout
        self.assertIn('∞ (salida 0)', texto)
        self.assertIn('sin datos', self.seccion(texto, 'Dónde se fue el dinero'))


if __name__ == '__main__':
    unittest.main()
