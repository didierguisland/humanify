#!/usr/bin/python3
"""Cazafrases: frases de quien dirige a la MEMORIA del agente, sin modelo."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'hooks'))
import _cazafrases as C

MIEMBROS = {'opus': {}, 'argon': {}, 'flash': {}}
CHAT = [
    '23:58 didier→todos: /carpeta /tmp/x',
    '23:59 argon→todos: Propongo la cama enorme.',
    '00:00 opus→didier: Va la idea de Argon con el pie.',
    '00:01 didier→todos: Me late la idea del pie, eso sí. Pero el arranque no engancha nada todavía. Denle otra vuelta.',
    '00:02 didier→todos: @argon la paleta de noche quedó bonito de verdad.',
    '00:03 orquestador→opus: [escenas] Corrijan: el perro parece salchicha desde arriba.',
    '00:03 orquestador→opus: [cuadros] Corrijan: Solo esto, lo demás déjenlo igual: agranden las escenas.',
    '00:04 orquestador→todos: [escenas] Aprobado.',
    '00:05 didier→todos: Ya grabé la voz y la dejé en la carpeta.',
]


class Cazafrases(unittest.TestCase):
    def test_caza_tipo_destino_y_clave(self):
        h = C.cazar(CHAT, MIEMBROS)
        self.assertIn(('opus', 'eso sí', 'general', 'Me late la idea del pie, eso sí.'), h)
        self.assertIn(('opus', 'regaño', 'general', 'Pero el arranque no engancha nada todavía.'), h)
        self.assertIn(('argon', 'eso sí', 'general', 'la paleta de noche quedó bonito de verdad.'), h)  # la mención manda
        self.assertIn(('opus', 'regaño', 'escenas', 'el perro parece salchicha desde arriba.'), h)
        self.assertIn(('opus', 'regaño', 'cuadros', 'Solo esto, lo demás déjenlo igual: agranden las escenas.'), h)
        self.assertFalse(any('grabé' in f for *_, f in h))  # lo neutro no entra
        self.assertFalse(any(a == 'orquestador' or f.startswith('/') for a, *_, f in h))

    def test_anota_sin_repetir_y_con_tope(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            memoria = base / 'equipo/opus/MEMORIA.md'
            memoria.parent.mkdir(parents=True)
            viejas = ''.join('- 2026-01-01 | aprendizaje | x | vieja {}\n'.format(i) for i in range(39))
            memoria.write_text('# Memoria de Opus\n\nFormato.\n\n' + viejas, encoding='utf-8')
            h = [('opus', 'regaño', 'general', 'frase nueva uno'), ('opus', 'eso sí', 'general', 'frase nueva dos')]
            self.assertEqual(C.anotar(base, h, '2026-11-03'), [memoria])
            self.assertEqual(C.anotar(base, h, '2026-11-03'), [])  # no repite
            lineas = memoria.read_text(encoding='utf-8').splitlines()
            entradas = [l for l in lineas if l.startswith('- ')]
            self.assertEqual(len(entradas), C.TOPE_LINEAS)
            self.assertNotIn('vieja 0', '\n'.join(entradas))
            self.assertEqual(entradas[-1], '- 2026-11-03 | eso sí | general | frase nueva dos')
            self.assertEqual(lineas[0], '# Memoria de Opus')

    def test_commit_solo_lleva_las_memorias(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            git = lambda *a: subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@t'] + list(a), cwd=tmp,
                                            capture_output=True, text=True, check=True).stdout
            git('init', '-q')
            git('config', 'user.name', 't')
            git('config', 'user.email', 't@t')
            (base / 'equipo/opus').mkdir(parents=True)
            memoria = base / 'equipo/opus/MEMORIA.md'
            memoria.write_text('# Memoria\n', encoding='utf-8')
            (base / 'otro.txt').write_text('a', encoding='utf-8')
            git('add', '.')
            git('commit', '-qm', 'inicio')
            (base / 'otro.txt').write_text('preparado', encoding='utf-8')
            git('add', 'otro.txt')
            C.anotar(base, [('opus', 'regaño', 'general', 'una frase cazada aquí')])
            C.guardar_en_git(base, [memoria], 9)
            self.assertIn('cazafrases de la tarea 9', git('log', '-1', '--format=%s'))
            self.assertEqual(git('show', '--name-only', '--format=', 'HEAD').split(), ['equipo/opus/MEMORIA.md'])
            self.assertIn('otro.txt', git('diff', '--cached', '--name-only'))  # lo preparado sigue preparado


if __name__ == '__main__':
    unittest.main()
