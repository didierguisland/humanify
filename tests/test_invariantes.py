#!/usr/bin/python3
"""Invariantes del motor: valen para TODA ruta y TODO oficio, presentes y futuros. Cada uno nace de una
familia de fallas (Bases/FALLAS-MOTOR.md); si una ruta nueva rompe uno, la prueba lo dice antes de correrla."""
import json
from pathlib import Path
import shutil
import sys
import unittest
from unittest import mock

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / 'hooks'))
sys.path.insert(0, str(RAIZ / 'tests'))
import _marcador as M  # noqa: E402
import _ruta as R  # noqa: E402
import test_ruta as TR  # noqa: E402

RUTAS = sorted((RAIZ / 'equipo/rutas').glob('*.json'))
OFICIOS = sorted((RAIZ / 'equipo/oficios').glob('*/oficio.json'))
TRABAJOS = set(M.TRABAJOS)
CONSTRUYE_EN_TALLER = ('ab', 'taller-ganador')


def cargar(archivo):
    return json.loads(archivo.read_text(encoding='utf-8'))


def oficio_de(ruta):
    archivo = RAIZ / 'equipo/oficios' / ruta.get('oficio', '') / 'oficio.json'
    return cargar(archivo) if ruta.get('oficio') and archivo.exists() else {}


class Estatico(unittest.TestCase):
    def test_hay_rutas(self):
        self.assertTrue(RUTAS)

    def test_toda_etapa_dice_su_tipo_de_trabajo(self):
        # Familia "asignar por intuición": sin tipo de trabajo no hay elenco ni evidencia por etapa.
        for archivo in RUTAS:
            for etapa in cargar(archivo)['etapas']:
                self.assertIn(etapa.get('trabajo'), TRABAJOS, '{}: {}'.format(archivo.name, etapa['id']))

    def test_toda_ruta_con_oficio_lo_tiene(self):
        for archivo in RUTAS:
            ruta = cargar(archivo)
            if ruta.get('oficio'):
                self.assertTrue((RAIZ / 'equipo/oficios' / ruta['oficio'] / 'oficio.json').exists(), archivo.name)

    def test_nadie_construye_sin_poder_verificarse(self):
        # Familia "construir a ciegas" (FALLAS 15, 34).
        for archivo in RUTAS:
            ruta = cargar(archivo)
            verificar = dict(oficio_de(ruta).get('verificar') or {}, **(ruta.get('verificar') or {}))
            for etapa in ruta['etapas']:
                construye = etapa.get('tipo') in CONSTRUYE_EN_TALLER or (
                    etapa.get('trabajo') == 'construir' and 'taller' in etapa)
                if construye:
                    self.assertTrue(etapa.get('verificar') or verificar.get(etapa.get('entregable', '')),
                                    '{}: {} construye sin verificar'.format(archivo.name, etapa['id']))

    def test_toda_asignacion_fija_dice_por_que(self):
        for archivo in RUTAS:
            for etapa in cargar(archivo)['etapas']:
                fijo = etapa.get('lider') or (etapa.get('taller') or {}).get('agente') or etapa.get('tipo') == 'taller-ganador'
                if fijo:
                    self.assertTrue(etapa.get('motivo_elenco'), '{}: {}'.format(archivo.name, etapa['id']))

    def test_el_elenco_cubre_todo_trabajo_con_motivo_y_agentes_reales(self):
        elenco = cargar(RAIZ / 'equipo/elenco.json')
        equipo = {p.parent.name for p in (RAIZ / 'equipo').glob('*/config.json')}
        self.assertEqual(set(elenco['*']), TRABAJOS)
        for oficio, tabla in elenco.items():
            if oficio.startswith('_'):
                continue
            for trabajo, entrada in tabla.items():
                self.assertTrue(entrada.get('motivo'), '{}/{}'.format(oficio, trabajo))
                for quien in (entrada, entrada.get('alternativa') or entrada):
                    self.assertIn(quien['agente'], equipo, '{}/{}'.format(oficio, trabajo))

    def test_todo_modelo_de_claude_tiene_precio(self):
        # Familia "costeo falso": sin tarifa, el elenco no puede pesar calidad contra precio.
        con_precio = {modelo for modelo, *_ in M.PRECIOS}
        for archivo in (RAIZ / 'equipo').glob('*/config.json'):
            config = cargar(archivo)
            if config['motor'] == 'claude':
                self.assertIn(config['modelo'], con_precio, archivo.parent.name)

    def test_los_hechos_del_oficio_se_pueden_calcular(self):
        spec_herramienta = (RAIZ / 'herramientas/hechos.py')
        self.assertTrue(spec_herramienta.exists())
        for archivo in RUTAS:
            ruta = cargar(archivo)
            oficio = oficio_de(ruta)
            dados = (oficio.get('hechos_dados') or []) + (ruta.get('hechos_dados') or [])
            hechos = dict(oficio.get('hechos') or {}, **(ruta.get('hechos') or {}))
            definidos = set(dados)
            for nombre, regla in hechos.items():
                if 'producto' in regla:
                    self.assertIn(regla['producto'][0], definidos, '{}: {}'.format(archivo.name, nombre))
                definidos.add(nombre)


class TallerGenerico(TR.TallerFalso):
    """Escribe un archivo por trabajo, para cualquier oficio."""
    def lanzar(self, trabajo, al_terminar):
        texto = trabajo['instruccion']
        if 'GANA' in texto.upper() or 'hojaA' in texto:
            return super().lanzar(trabajo, al_terminar)
        carpeta = Path(trabajo['carpeta'])
        carpeta.mkdir(parents=True, exist_ok=True)
        for nombre in ('reel.html', 'estilo.html', 'PERFIL.md', '04-STORYBOARD.md', '05-DIRECCION.md', '05b-AJUSTES.md', 'audio-final.wav'):
            if nombre in texto:
                (carpeta / nombre).write_text('{} de {}'.format(nombre, trabajo['agente']), encoding='utf-8')
        (carpeta / 'salida-{}.md'.format(trabajo['agente'])).write_text(texto[:40], encoding='utf-8')
        hilo = super().lanzar(trabajo, al_terminar)
        # Lo que la fábrica exige en estos entregables (como el SPEECH de Opus en setUp).
        bloques = {'05-DIRECCION.md': '\nMUNDO:\nFIN-MUNDO\nTIEMPOS_CLAVE: 0\n', '05b-AJUSTES.md': '\nPOSES:\nHECHOS EN PANTALLA:\n'}
        if trabajo.get('entregable') in bloques:
            with (carpeta / trabajo['entregable']).open('a', encoding='utf-8') as stream:
                stream.write(bloques[trabajo['entregable']])
        return hilo


class Dinamico(TR.Ruta):
    """Corre cada ruta de punta a punta con motores falsos y revisa los invariantes de conducta."""

    def setUp(self):
        super().setUp()
        for archivo in RUTAS:
            shutil.copy2(archivo, self.base / 'equipo/rutas' / archivo.name)
        for nombre in [p.parent.name for p in (RAIZ / 'equipo').glob('*/config.json')]:
            destino = self.base / 'equipo' / nombre
            if not destino.exists():
                shutil.copytree(RAIZ / 'equipo' / nombre, destino)
        self.taller = TallerGenerico()
        self.respuestas['opus'] = 'SPEECH:\nHola.\nFIN\nVOZ:\nHola.\nFIN-VOZ\nSILENCIOS:\nFIN-SILENCIOS\n'
        self.modulo.stop()
        self.modulo = mock.patch.dict(sys.modules, _taller=self.taller)
        self.modulo.start()

    def correr_completa(self, archivo):
        ruta = cargar(archivo)
        self.c = self.conductor()
        dados = {h: 30 for h in oficio_de(ruta).get('hechos_dados', []) + ruta.get('hechos_dados', [])}
        self.c.iniciar(archivo.stem, self.trabajo / archivo.stem, 'brief de prueba', hechos=dados)
        salida = self.c.avanzar()
        for _ in range(40):
            if 'Ruta terminada' in salida:
                return
            if 'faltan' in salida:
                etapa = self.c.etapa()
                for nombre in etapa.get('archivos', []):
                    (self.c.carpeta() / nombre).write_text('dato')
                salida = self.c.avanzar()
            else:
                salida = self.c.decidir('aprobar')
        self.fail('{} no terminó: {}'.format(archivo.name, salida[:300]))

    def test_cada_ruta_corre_y_cumple_los_invariantes_de_conducta(self):
        for archivo in RUTAS:
            with self.subTest(ruta=archivo.name):
                self.correr_completa(archivo)
                chat = '\n'.join(self.c.canal.lineas())
                # Todo archivo que quedó en la carpeta se anunció con versión (familia "verdad no compartida").
                for ruta_archivo in self.c.foto():
                    self.assertRegex(chat, r'\] {} v\d+ · '.format(ruta_archivo.replace('.', r'\.')), ruta_archivo)
                # Toda asignación dice por qué; ninguna sin motivo.
                for etapa, elegido in self.c.estado.get('elenco', {}).items():
                    self.assertTrue(elegido.get('motivo'), etapa)
                    self.assertNotIn('sin motivo', elegido['motivo'], etapa)


# Las pruebas de test_ruta ya corren en su módulo: aquí solo las de invariantes.
for _nombre in dir(TR.Ruta):
    if _nombre.startswith('test_'):
        setattr(Dinamico, _nombre, None)


if __name__ == '__main__':
    unittest.main()
