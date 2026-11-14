#!/usr/bin/python3
"""La puerta de candidatos: todo se escribe en carpetas falsas en /tmp."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / 'hooks'))
import _humanify as H

TEXTO = ('en tareas de extracción, marcar en cada candidato el solape probable con v1 y su nivel '
         '(N1/N2) ahorra la ronda de contraste al crítico')
CAMBIA = 'el encargo de extracción pide columnas de solape y nivel'


class Candidatos(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='candidatos-prueba-', dir='/tmp')
        self.raiz = Path(self.tmp.name)
        self.destino = self.raiz / 'Bases/APRENDIZAJES.md'
        self.parches = [mock.patch.object(H, n, self.raiz) for n in ('HOME_DIR', 'PROJECTS', 'PROJECTS_SPELLING')]
        for parche in self.parches:
            parche.start()

    def tearDown(self):
        for parche in reversed(self.parches):
            parche.stop()
        self.tmp.cleanup()

    def escribir(self, texto=TEXTO, base='hecho', cambia=CAMBIA, **opciones):
        return H.escribir_candidato(self.destino, texto, base, cambia, 'prueba 2-nov', **opciones)

    def transcript(self, *mensajes):
        ruta = self.raiz / 'transcript.jsonl'
        ruta.write_text('\n'.join(json.dumps(m, ensure_ascii=False) for m in mensajes), encoding='utf-8')
        return ruta

    def cli(self, *args):
        env = dict(os.environ, HUMANIFY_DIR=str(self.raiz), HUMANIFY_PROYECTOS=str(self.raiz))
        return subprocess.run(['/bin/bash', str(RAIZ / 'herramientas/anota.sh'), 'candidato',
                               str(self.destino)] + list(args), env=env, capture_output=True, text=True)

    def test_ruido_sin_cambia_y_cambios_vacios_se_rechazan(self):
        for cambia in ('', 'nada', '—', '-', 'ninguna', 'n/a', 'hacer algo nuevo'):
            with self.subTest(cambia=cambia), self.assertRaisesRegex(ValueError, 'cambia.*4 palabras'):
                self.escribir('protocolo claro para Codex Sol', cambia=cambia)
        self.assertFalse(self.destino.exists())

    def test_diez_variantes_del_ruido_no_se_acumulan(self):
        variantes = ['protocolo claro para Codex Sol'] + ['protocolo claro para Codex Sol ' + str(n) for n in range(2, 11)]
        self.escribir(variantes[0])
        for texto in variantes[1:]:
            with self.subTest(texto=texto), self.assertRaisesRegex(ValueError, 'repetido'):
                self.escribir(texto)
        self.assertEqual(self.destino.read_text().count('[por validar]'), 1)

    def test_hecho_real_del_2_nov_y_formato_completo(self):
        self.escribir(ident='extraccion')
        self.assertRegex(self.destino.read_text(), r'^- \[por validar\] ' + re.escape(TEXTO)
                         + r' — base: hecho · cambia: ' + CAMBIA
                         + r' · fuente: prueba 2-nov · \d{4}-\d{2}-\d{2} · id extraccion\n$')

    def test_detecta_entrada_vieja_y_candidato_con_metadatos_sin_cambiarlos(self):
        self.destino.parent.mkdir()
        for linea in ('- ' + TEXTO, '- [por validar] ' + TEXTO + ' — fuente: antigua · 2026-11-02 · id viejo',
                      '- [por validar] ' + TEXTO + ' — base: hecho · cambia: ' + CAMBIA + ' · fuente: antes'):
            with self.subTest(linea=linea):
                self.destino.write_text('# Aprendizajes\n' + linea + '\n')
                antes = self.destino.read_bytes()
                with self.assertRaisesRegex(ValueError, 'repetido'):
                    self.escribir('  ' + TEXTO.upper().replace(' ', '   ') + '  ')
                self.assertEqual(self.destino.read_bytes(), antes)

    def test_base_desconocida_se_rechaza(self):
        with self.assertRaisesRegex(ValueError, 'base inválida'):
            self.escribir(base='opinion')

    def test_mencion_a_didier_exige_su_base(self):
        for base in ('inferencia', 'hecho'):
            with self.subTest(base=base), self.assertRaisesRegex(ValueError, 'menciona a Didier'):
                self.escribir('DIDIER prefiere reportes cortos', base=base)

    def test_sin_transcript_no_se_verifica_a_didier(self):
        with self.assertRaisesRegex(ValueError, 'sin transcript no se puede verificar; usa base: inferencia'):
            self.escribir('Didier prefiere reportes cortos', base='didier', cita='prefiero reportes cortos')

    def test_cita_ausente_o_corta_se_rechaza(self):
        ruta = self.transcript({'type': 'user', 'message': {'content': 'prefiero reportes cortos'}})
        for cita in ('', 'reportes cortos', '— — cortos'):
            with self.subTest(cita=cita), self.assertRaisesRegex(ValueError, 'al menos 3 palabras'):
                self.escribir('Didier prefiere reportes cortos', base='didier', cita=cita, transcript=ruta)

    def test_cita_de_usuario_se_acepta_con_puntuacion_mayusculas_y_espacios(self):
        ruta = self.transcript({'type': 'user', 'message': {'role': 'user', 'content': [
            {'type': 'text', 'text': 'Prefiero   reportes cortos, por favor.'}]}})
        self.escribir('Didier prefiere reportes cortos', base='didier', cita='prefiero reportes cortos', transcript=ruta)
        self.assertIn('base: didier · cita: "prefiero reportes cortos" · cambia:', self.destino.read_text())

    def test_cita_en_texto_simple_de_usuario_se_acepta(self):
        ruta = self.transcript({'type': 'user', 'message': {'content': 'prefiero reportes cortos'}})
        self.escribir('Didier prefiere reportes cortos', base='didier', cita='prefiero reportes cortos', transcript=ruta)
        self.assertTrue(self.destino.exists())

    def test_cita_solo_del_asistente_o_herramienta_no_cuenta(self):
        contenido = 'prefiero reportes cortos'
        falsos = [
            {'type': 'assistant', 'message': {'role': 'assistant', 'content': contenido}},
            {'type': 'user', 'message': {'content': [{'type': 'tool_result', 'content': contenido}]}},
            {'type': 'user', 'message': {'role': 'assistant', 'content': contenido}},
            {'type': 'user', 'isMeta': True, 'message': {'content': contenido}},
            {'type': 'user', 'isSynthetic': True, 'message': {'content': contenido}},
        ]
        for mensaje in falsos:
            with self.subTest(mensaje=mensaje):
                ruta = self.transcript(mensaje)
                with self.assertRaisesRegex(ValueError, 'no aparece en mensajes de usuario'):
                    self.escribir('Didier prefiere reportes cortos', base='didier', cita=contenido, transcript=ruta)

    def test_no_une_citas_de_mensajes_distintos(self):
        ruta = self.transcript({'type': 'user', 'message': {'content': 'prefiero reportes'}},
                               {'type': 'user', 'message': {'content': 'cortos'}})
        with self.assertRaisesRegex(ValueError, 'no aparece en mensajes de usuario'):
            self.escribir('Didier prefiere reportes cortos', base='didier', cita='prefiero reportes cortos', transcript=ruta)

    def test_transcript_inexistente_se_rechaza(self):
        with self.assertRaisesRegex(ValueError, 'transcript .jsonl legible'):
            self.escribir('Didier prefiere reportes cortos', base='didier', cita='prefiero reportes cortos',
                          transcript=self.raiz / 'no-existe.jsonl')

    def test_inferencia_conserva_su_marca_y_id_generado(self):
        ident = self.escribir(base='inferencia')
        self.assertIn('base: inferencia', self.destino.read_text())
        self.assertTrue(self.destino.read_text().endswith(' · id ' + ident + '\n'))

    def test_cli_escribe_y_reintentar_id_salta_incluso_si_el_texto_es_invalido(self):
        args = [TEXTO, '--base', 'hecho', '--cambia', CAMBIA, '--fuente', '2-nov', '--id', 'sala:abc:1']
        primero = self.cli(*args)
        self.assertEqual((primero.returncode, primero.stdout), (0, 'candidato escrito · id sala:abc:1\n'))
        antes = self.destino.read_bytes()
        args[2] = 'invalida'
        segundo = self.cli(*args)
        self.assertEqual(segundo.returncode, 0)
        self.assertEqual(self.destino.read_bytes(), antes)

    def test_cli_rechaza_sin_cambia_y_sin_opciones_obligatorias(self):
        for args in (['protocolo claro para Codex Sol', '--base', 'hecho', '--cambia', '', '--fuente', '2-nov'],
                     [TEXTO, '--base', 'hecho']):
            with self.subTest(args=args):
                resultado = self.cli(*args)
                self.assertEqual(resultado.returncode, 2)
                self.assertTrue(resultado.stdout.startswith('rechazado: '))
                self.assertEqual(len(resultado.stdout.splitlines()), 1)
                self.assertFalse(self.destino.exists())

    def test_cli_pasa_cita_y_transcript_al_validador(self):
        ruta = self.transcript({'type': 'user', 'message': {'content': 'prefiero reportes cortos'}})
        resultado = self.cli('Didier prefiere reportes cortos', '--base', 'didier', '--cambia', CAMBIA,
                             '--fuente', 'rescate', '--cita', 'prefiero reportes cortos', '--transcript', str(ruta))
        self.assertEqual(resultado.returncode, 0, resultado.stdout + resultado.stderr)
        self.assertIn('base: didier · cita: "prefiero reportes cortos"', self.destino.read_text())


if __name__ == '__main__':
    unittest.main()
