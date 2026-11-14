#!/usr/bin/python3
"""Hechos medidos: archivos locales y CLI, sin modelos ni red."""
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


RAIZ = Path(__file__).resolve().parent.parent
SCRIPT = RAIZ / 'herramientas/hechos.py'
_spec = importlib.util.spec_from_file_location('hechos', SCRIPT)
H = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(H)


class Hechos(unittest.TestCase):
    def setUp(self):
        self.temporal = tempfile.TemporaryDirectory(prefix='hechos-prueba-')
        self.addCleanup(self.temporal.cleanup)
        self.carpeta = Path(self.temporal.name) / 'Trabajo con espacios'
        self.carpeta.mkdir()

    def ejecutar(self, spec):
        return subprocess.run(['/usr/bin/python3', str(SCRIPT), str(self.carpeta),
                               '--spec', json.dumps(spec, ensure_ascii=False)],
                              capture_output=True, text=True)

    def hechos(self, spec):
        resultado = self.ejecutar(spec)
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        archivo = (self.carpeta / 'HECHOS.json').read_text(encoding='utf-8')
        self.assertEqual(resultado.stdout, archivo)
        datos = json.loads(archivo)
        self.assertEqual(set(datos), {'hechos', 'calculado'})
        self.assertIsNotNone(datetime.fromisoformat(datos['calculado']).tzinfo)
        return datos['hechos']

    def test_fijos_conservan_valores_json(self):
        valores = {'ancho': 1080, 'título': 'Perfil de Lucía', 'opciones': ['cv', 2],
                   'config': {'publicar': False}, 'vacio': None, 'activo': True}
        self.assertEqual(self.hechos({k: {'fijo': v} for k, v in valores.items()}), valores)

    def test_palabras_del_archivo_completo(self):
        (self.carpeta / 'speech.md').write_text('Hola, equipo.\nHay tres ideas más.', encoding='utf-8')
        self.assertEqual(self.hechos({'palabras': {'palabras': 'speech.md'}}), {'palabras': 6})

    def test_palabras_del_citado_mas_largo_como_voz_prueba(self):
        for apertura, cierre in (('«', '»'), ('"', '"'), ('“', '”')):
            with self.subTest(comillas=apertura):
                texto = ('Título fuera del discurso. {0}Un primer texto citado.{1}\n'
                         '{0}Este es el discurso más largo para contar sus palabras.{1}\n'
                         'Indicaciones que tampoco se cuentan.').format(apertura, cierre)
                (self.carpeta / '03-SPEECH.md').write_text(texto, encoding='utf-8')
                self.assertEqual(self.hechos({'n': {'palabras': '03-SPEECH.md'}}), {'n': 10})

    def test_bloque_speech_manda_sobre_lo_citado(self):
        # FALLA 45: una alternativa entre «» para el revisor se contaba como el speech.
        texto = ('**Speech final**\nSPEECH:\nSeca, vuelve a ser puente.\nFIN\n\n'
                 'Para Terra: «¿Por qué tu pelo puede romperse más cuando te peinas mojada?»')
        (self.carpeta / '03-SPEECH.md').write_text(texto, encoding='utf-8')
        self.assertEqual(self.hechos({'n': {'palabras': '03-SPEECH.md'}}), {'n': 5})

    def test_cita_corta_no_reemplaza_archivo_como_voz_prueba(self):
        (self.carpeta / 'corto.md').write_text('Texto con "cita breve" y cierre.', encoding='utf-8')
        self.assertEqual(self.hechos({'n': {'palabras': 'corto.md'}}), {'n': 6})

    def test_caracteres_incluyen_espacios_saltos_y_unicode(self):
        (self.carpeta / 'linkedin.md').write_text('¡Hola!\nCV: á 🛠', encoding='utf-8')
        self.assertEqual(self.hechos({'n': {'caracteres': 'linkedin.md'}}), {'n': 14})

    def test_productos_siguen_orden_dependencias_y_redondean_hacia_abajo(self):
        hechos = self.hechos({'duracion_objetivo_s': {'fijo': 12.5},
                             'palabras_max': {'producto': ['duracion_objetivo_s', 2.3],
                                              'redondeo': 'abajo'},
                             'doble': {'producto': ['palabras_max', 2], 'redondeo': 'abajo'},
                             'negativo': {'producto': ['doble', -0.1], 'redondeo': 'abajo'}})
        self.assertEqual(list(hechos), ['duracion_objetivo_s', 'palabras_max', 'doble', 'negativo'])
        self.assertEqual(hechos, {'duracion_objetivo_s': 12.5, 'palabras_max': 28,
                                 'doble': 56, 'negativo': -6})

    def test_archivos_faltantes_y_productos_son_null(self):
        hechos = self.hechos({'audio': {'duracion_audio': 'no.wav'},
                             'palabras': {'palabras': 'no.md'},
                             'caracteres': {'caracteres': 'no.txt'},
                             'limite': {'producto': ['audio', 2.3], 'redondeo': 'abajo'},
                             'doble': {'producto': ['limite', 2], 'redondeo': 'abajo'}})
        self.assertEqual(hechos, dict.fromkeys(['audio', 'palabras', 'caracteres', 'limite', 'doble']))

    def test_duracion_audio_con_ffmpeg_de_voz_prueba(self):
        ffmpeg = H._voz.FFMPEG
        if not Path(ffmpeg).is_file():
            self.skipTest('no existe el ffmpeg de voz-prueba.py')
        subprocess.run([ffmpeg, '-y', '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=mono',
                        '-t', '0.37', str(self.carpeta / 'voz.wav')],
                       capture_output=True, check=True)
        self.assertEqual(self.hechos({'duracion_s': {'duracion_audio': 'voz.wav'}}),
                         {'duracion_s': 0.37})

    def test_dependencia_debe_estar_calculada_antes(self):
        resultado = self.ejecutar({'producto': {'producto': ['posterior', 2], 'redondeo': 'abajo'},
                                  'posterior': {'fijo': 10}})
        self.assertEqual(resultado.returncode, 2)
        self.assertIn('posterior debe calcularse antes', resultado.stderr)
        self.assertFalse((self.carpeta / 'HECHOS.json').exists())

    def test_regla_desconocida_sale_2_con_error_claro(self):
        resultado = self.ejecutar({'tamaño': {'bytes': 'cv.pdf'}})
        self.assertEqual(resultado.returncode, 2)
        self.assertIn('tamaño: regla desconocida', resultado.stderr)
        self.assertEqual(resultado.stdout, '')
        self.assertFalse((self.carpeta / 'HECHOS.json').exists())


if __name__ == '__main__':
    unittest.main()
