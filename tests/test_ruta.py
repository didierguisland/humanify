#!/usr/bin/python3
import json
from pathlib import Path
import re
import shutil
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_teams import Base, RAIZ  # noqa: E402
import _ruta as R  # noqa: E402


class TallerFalso:
    """Escribe lo que pide la instrucción y avisa como el taller real."""
    def __init__(self, veredictos=None):
        self.trabajos = []
        self.veredictos = veredictos or {}

    def lanzar(self, trabajo, al_terminar):
        self.trabajos.append(trabajo)
        carpeta, texto = Path(trabajo['carpeta']), trabajo['instruccion']
        carpeta.mkdir(parents=True, exist_ok=True)
        if 'versión B' in texto:
            entregable = re.search(r'Deja tu versión como (\S+)', texto).group(1)
            (carpeta / entregable).write_text('VERSION B de ' + trabajo['agente'], encoding='utf-8')
            aviso = 'dejé ' + entregable
        elif 'hojaA.png' in texto:
            aviso = self.veredictos.get('juez', 'GANA B: se lee mejor')
        elif texto.startswith('Corrige reel.html') or 'Corrige reel.html' in texto:
            (carpeta / 'reel.html').write_text('HTML de {} (corregido {})'.format(
                trabajo['config']['modelo'], len(self.trabajos)), encoding='utf-8')
            aviso = 'dejé reel.html'
        elif 'hoja.png' in texto:
            aviso = self.veredictos.get('render', 'PASA')
        elif 'estilo.html' in texto and ('Construye estilo' in texto or 'Corrige estilo' in texto):
            (carpeta / 'estilo.html').write_text('ESTILO de ' + trabajo['config']['modelo'], encoding='utf-8')
            aviso = 'dejé estilo.html'
        elif '04-STORYBOARD.md' in texto and 'Escribe 04' in texto:
            (carpeta / '04-STORYBOARD.md').write_text('STORYBOARD', encoding='utf-8')
            aviso = 'dejé 04-STORYBOARD.md'
        elif 'audio-final.wav' in texto:
            (carpeta / 'audio-final.wav').write_text('audio de ' + trabajo['config']['modelo'], encoding='utf-8')
            aviso = 'dejé audio-final.wav'
        elif trabajo.get('entregable') and trabajo['entregable'] in texto and trabajo['entregable'] != 'reel.html':
            (carpeta / trabajo['entregable']).write_text('hecho por ' + trabajo['config']['modelo'], encoding='utf-8')
            aviso = 'dejé ' + trabajo['entregable']
        else:
            (carpeta / 'reel.html').write_text('HTML de ' + trabajo['config']['modelo'], encoding='utf-8')
            aviso = 'dejé reel.html'
        hilo = threading.Thread(target=al_terminar, args=(aviso,))
        hilo.start()
        return hilo


class Ruta(Base):
    def setUp(self):
        super().setUp()
        carpeta = self.base / 'equipo/argon'
        carpeta.mkdir()
        (carpeta / 'config.json').write_text(json.dumps(dict(
            motor='agy', modelo='argon', esfuerzo='medium', activo=True, orden=6, color='38;5;177')))
        (carpeta / 'ALMA.md').write_text('ALMA argon')
        (carpeta / 'MEMORIA.md').write_text('MEMORIA argon')
        (self.base / 'equipo/rutas').mkdir()
        shutil.copy2(RAIZ / 'equipo/rutas/video-animado.json', self.base / 'equipo/rutas/video-animado.json')
        shutil.copy2(RAIZ / 'equipo/elenco.json', self.base / 'equipo/elenco.json')
        shutil.copytree(RAIZ / 'equipo/oficios', self.base / 'equipo/oficios')
        self.poner_libro(None)  # las pruebas no dependen del libro real de ProjectA
        (self.base / 'herramientas').mkdir(exist_ok=True)
        for nombre in ('hechos.py', 'voz-prueba.py'):
            shutil.copy2(RAIZ / 'herramientas' / nombre, self.base / 'herramientas' / nombre)
        self.taller = TallerFalso()
        self.modulo = mock.patch.dict(sys.modules, _taller=self.taller)
        self.modulo.start()
        self.trabajo = self.tmp / 'trabajo'
        self.turnos = []
        self.respuestas = {}
        self.scripts = []
        self.c = self.conductor()

    def tearDown(self):
        self.modulo.stop()
        super().tearDown()

    def conductor(self):
        c = R.Conductor(self.proyecto)
        def turno(nombre, motivo, tope=400, quien_decide='didier'):
            self.turnos.append((nombre, tope, motivo))
            respuesta = self.respuestas.get(nombre, 'Respuesta de ' + nombre + '\nsegunda línea')
            if callable(respuesta):
                respuesta = respuesta(motivo)
            c.canal.ultima_cruda = respuesta
            return R.T.plano(respuesta, tope)
        c.canal.turno = turno
        def correr(herramienta, **valores):
            self.scripts.append(herramienta)
            destino = valores.get('png') or valores.get('ligero') or valores.get('mp4')
            if not destino and c.etapa().get('entregable'):  # etapa de script: deja lo que la ruta pide
                destino = c.carpeta() / c.etapa()['entregable']
            if destino:
                Path(destino).write_text(herramienta + str(len(self.scripts)))
            c.anunciar('script ' + herramienta)
            return ''
        c.correr = correr
        return c

    def quienes(self):
        return [(n, t) for n, t, _ in self.turnos]

    def iniciar(self, ab=None):
        return self.c.iniciar('video-animado', self.trabajo, 'perros que duermen contigo, 25 s',
                              hechos={'duracion_objetivo_s': 25}, ab=ab)

    def poner_libro(self, archivo):
        ruta = self.base / 'equipo/oficios/video/oficio.json'
        oficio = json.loads(ruta.read_text(encoding='utf-8'))
        oficio.pop('libro_correcciones', None)
        oficio.pop('libro_origen', None)
        if archivo:
            oficio['libro_correcciones'] = str(archivo)
        ruta.write_text(json.dumps(oficio, ensure_ascii=False), encoding='utf-8')

    def libro_de_prueba(self):
        archivo = self.tmp / 'LIBRO.md'
        archivo.write_text('# Libro\nCómo lo usa el revisor.\n\n## Palabras y tono\n- Regla de palabras.\n\n'
                           '## Speech y retención\n- Regla de speech.\n\n## Salud y producto\n- Regla de salud.\n\n'
                           '## Visual y zonas\n- Regla visual.\n\n## Cómo hablarle a Didier\n- Marta es privada.\n', encoding='utf-8')
        self.poner_libro(archivo)
        return archivo

    def llegar_al_speech(self):
        self.iniciar()
        self.c.avanzar()
        self.c.decidir('aprobar')
        return self.c.decidir('aprobar')

    def test_libro_llega_al_revisor_solo_con_los_temas_de_la_etapa(self):
        self.libro_de_prueba()
        self.respuestas['sonnet'] = 'FALLA: «se rompe» · Salud y producto: Regla de salud\nPASAN: 2'
        salida = self.llegar_al_speech()
        pedido = next(m for n, _, m in self.turnos if n == 'sonnet' and 'LIBRO:' in m)
        for tema in ('Regla de palabras', 'Regla de speech', 'Regla de salud', 'Cómo lo usa el revisor'):
            self.assertIn(tema, pedido)
        self.assertNotIn('Regla visual', pedido)
        self.assertNotIn('Marta', pedido)  # lo que no es de esta etapa no sale a otro modelo
        self.assertIn('ENTREGABLE:', pedido)
        self.assertIn('Libro de correcciones', salida)
        self.assertIn('FALLA: «se rompe»', salida)
        self.assertIn('FALLA: «se rompe»', (self.trabajo / 'LIBRO-speech.md').read_text(encoding='utf-8'))

    def test_el_libro_solo_reporta_no_corrige(self):
        self.libro_de_prueba()
        self.respuestas['sonnet'] = 'CAMBIA: FALLA grave\nPASAN: 0'
        self.llegar_al_speech()
        self.assertEqual(self.c.estado['vueltas'].get('speech'), None)
        self.assertEqual(self.c.estado['fase'], 'compuerta')

    def test_el_libro_vuelve_a_revisar_tras_corregir(self):
        self.libro_de_prueba()
        self.llegar_al_speech()
        self.turnos.clear()
        self.respuestas['sonnet'] = 'PASAN: 3 tras corregir'
        salida = self.c.decidir('corregir', 'más corto')
        self.assertTrue(any(n == 'sonnet' and 'LIBRO:' in m for n, _, m in self.turnos))
        self.assertIn('PASAN: 3 tras corregir', salida)

    def test_sin_libro_la_ruta_sigue_y_lo_dice(self):
        self.poner_libro(self.tmp / 'no-existe.md')
        salida = self.llegar_al_speech()
        self.assertIn('Compuerta «speech»', salida)
        self.assertIn('sin revisión: no se pudo leer el libro', salida)

    def test_un_tema_que_desaparece_se_avisa(self):
        archivo = self.libro_de_prueba()
        archivo.write_text(archivo.read_text(encoding='utf-8').replace('## Salud y producto', '## Salud'), encoding='utf-8')
        salida = self.llegar_al_speech()
        self.assertIn('el libro ya no tiene: Salud y producto', salida)

    def test_el_libro_del_render_va_al_taller_con_la_hoja(self):
        self.libro_de_prueba()
        self.iniciar()
        self.c.revisar_libro({'id': 'render', 'tipo': 'render', 'libro': ['Visual y zonas']})
        instruccion = self.taller.trabajos[-1]['instruccion']
        self.assertIn('hoja.png', instruccion)
        self.assertIn('Regla visual', instruccion)
        self.assertNotIn('Regla de salud', instruccion)
        self.assertEqual(self.taller.trabajos[-1]['agente'], 'sonnet')
        self.assertEqual(self.c.estado['libro']['render']['veredicto'], 'PASA')

    def test_dinamica_de_etapa_y_compuerta(self):
        self.iniciar()
        salida = self.c.avanzar()
        quienes = self.quienes()
        self.assertEqual(quienes[0], ('argon', 700))
        self.assertEqual(sorted(quienes[1:3]), [('flash', 250), ('opus', 250)])  # idear: en paralelo, sin orden
        self.assertEqual(quienes[3:], [('argon', 1500), ('opus', 300)])
        self.assertIn('Compuerta «ángulo»', salida)
        self.assertEqual((self.trabajo / '01-CONCEPTO.md').read_text(), 'Respuesta de argon\nsegunda línea\n')
        self.assertIn('Brief del orquestador: perros', self.turnos[0][2])
        self.assertIn('[concepto] Respuesta de argon', '\n'.join(self.c.canal.lineas()))
        self.assertEqual(self.c.estado['fase'], 'compuerta')

    def test_revisor_cambia_una_sola_vuelta(self):
        self.respuestas['opus'] = lambda motivo: 'CAMBIA: falta la duda' if 'revisor' in motivo else 'ok'
        self.iniciar()
        self.c.avanzar()
        self.assertEqual(self.quienes()[-2:], [('opus', 300), ('argon', 1500)])
        self.assertIn('falta la duda', self.turnos[-1][2])
        self.assertEqual(self.c.estado['vueltas'], {'concepto': 1})

    def test_corregir_desde_la_compuerta_y_aprobar_sigue(self):
        self.iniciar()
        self.c.avanzar()
        salida = self.c.decidir('corregir', 'más ternura')
        self.assertIn('el orquestador pidió: más ternura', self.turnos[-2][2])
        self.assertEqual(self.turnos[-1][0], 'opus')  # FALLA 44: el revisor vuelve a revisar lo corregido
        self.assertIn('Compuerta «ángulo»', salida)
        self.turnos.clear()
        salida = self.c.decidir('aprobar')
        # escenas (sin compuerta) y luego el boceto de estilo, que se detiene en su compuerta.
        self.assertEqual([n for n, _ in self.quienes()], ['opus', 'argon', 'opus', 'argon'])
        self.assertIn('Compuerta «estilo»', salida)
        self.turnos.clear()
        salida = self.c.decidir('aprobar')
        self.assertEqual([n for n, _ in self.quienes()], ['opus', 'argon', 'opus', 'terra'])
        self.assertIn('Compuerta «speech»', salida)

    def test_corrida_completa_con_estilo_ab_a_ciegas_animacion_render_y_leccion(self):
        self.iniciar(ab=['estilo'])
        self.c.avanzar()
        # concepto aprobado → escenas → boceto de estilo A/B (un cuadro por escena), con el orden invertido: A = sonnet.
        with mock.patch.object(R.random, 'shuffle', side_effect=lambda l: l.reverse()):
            salida = self.c.decidir('aprobar')
        self.assertIn('Compuerta «estilo»', salida)
        ab = self.c.estado['ab']
        self.assertEqual((ab['A']['modelo'], ab['B']['modelo'], ab['gana']), ('sonnet', 'opus', 'B'))
        self.assertEqual((self.trabajo / 'estilo.html').read_text(), 'ESTILO de opus')
        self.assertIn('hoja_estilo', self.scripts)  # la hoja del boceto: un cuadro por escena
        self.assertTrue((self.trabajo / 'estilo.png').exists())
        juez = [t for t in self.taller.trabajos if 'hojaA.png' in t['instruccion']][0]
        # A ciegas: nada de la etapa del boceto (quién entró al taller, dónde dejó su archivo).
        self.assertNotIn('[estilo]', juez['chat'])
        for modelo in ('opus', 'sonnet'):
            self.assertNotIn(modelo, juez['instruccion'])
        self.assertNotIn('canal', juez)
        self.assertEqual(juez['agente'], 'argon')
        salida = self.c.decidir('aprobar')
        self.assertIn('Compuerta «speech»', salida)
        salida = self.c.decidir('aprobar')
        self.assertIn('faltan voz.wav, voz.srt', salida)
        (self.trabajo / 'voz.wav').write_text('w')
        (self.trabajo / 'voz.srt').write_text('s')
        salida = self.c.avanzar()
        # storyboard: lo decide el elenco, no la ruta; la animación la hace el papel «animar» del elenco.
        sb = [t for t in self.taller.trabajos if 'Escribe 04' in t['instruccion']][0]
        self.assertEqual((sb['agente'], sb['config']['modelo'], sb['config']['esfuerzo']), ('opus', 'opus', 'medium'))
        animacion = [t for t in self.taller.trabajos if 'Construye reel.html' in t['instruccion']][0]
        self.assertEqual(animacion['agente'], 'opus')
        self.assertIn('estilo.html (aprobado)', animacion['instruccion'])
        self.assertIn('[animacion] elenco: opus medium · reparto fijo, decisión de Didier', '\n'.join(self.c.canal.lineas()))
        self.assertIn('Compuerta «final»', salida)
        self.assertIn('render', self.scripts)  # el render es un script, sin modelo
        salida = self.c.decidir('aprobar')
        self.assertIn('Ruta terminada', salida)
        self.assertEqual(self.c.estado['fase'], 'terminada')
        self.assertIn('| aprendizaje | ruta |', (self.base / 'equipo/opus/MEMORIA.md').read_text())
        self.assertNotIn('MARCADOR_ETAPA', R.os.environ)  # lo que dispare el cierre ya no es de ninguna etapa

    def test_render_con_cambios_corrige_una_vez_y_vuelve_a_renderizar(self):
        self.taller.veredictos['render'] = 'CAMBIA: el subtítulo se enciman'
        self.iniciar()
        self.c.estado.update(indice=7, ganador={'agente': 'opus', 'modelo': 'opus', 'esfuerzo': 'medium'})
        salida = self.c.avanzar()
        # M10: el CAMBIA del revisor no va solo al retoque; llega a la compuerta y el orquestador lo manda.
        self.assertEqual(self.scripts.count('render'), 1)
        self.assertIn('el subtítulo se enciman', salida)
        self.assertFalse(any('Corrige reel.html' in t['instruccion'] for t in self.taller.trabajos))
        salida = self.c.decidir('corregir', 'el subtítulo se enciman')
        self.assertEqual(self.scripts.count('render'), 2)
        # Retoca el papel «retocar» del elenco (Sonnet, práctica de Didier), no el ganador del boceto.
        retoque = [t for t in self.taller.trabajos if 'Corrige reel.html' in t['instruccion']][0]
        self.assertEqual(retoque['agente'], 'sonnet')
        self.assertEqual(self.scripts.count('comprimir'), 2)  # cada render deja su copia ligera para redes
        self.assertTrue((self.trabajo / 'reel-redes.mp4').read_text().startswith('comprimir'))
        self.assertTrue((self.trabajo / 'reel.mp4').read_text().startswith('render'))
        # Invariante: el MP4 rehecho se anuncia con su versión y quién lo hizo (FALLA 30).
        chat = '\n'.join(self.c.canal.lineas())
        self.assertIn('[render] reel.mp4 v1 · nuevo (script render)', chat)
        self.assertIn('[render] reel.mp4 v2 · cambió (script render)', chat)
        self.assertTrue(any('Corrige reel.html' in t['instruccion'] for t in self.taller.trabajos))
        self.assertIn('Vueltas de corrección: 1', salida)

    def test_correccion_sin_lider_va_a_quien_construye(self):
        self.iniciar()
        self.c.estado.update(indice=7, ganador={'agente': 'opus', 'modelo': 'opus', 'esfuerzo': 'medium'})
        self.c.avanzar()
        self.c.decidir('corregir', 'la mano se corta')
        self.assertTrue(any('orquestador→sonnet: [render] Corrijan: la mano se corta' in l for l in self.c.canal.lineas()))

    def test_todo_cambio_de_archivo_se_anuncia_con_version_y_autor(self):
        self.iniciar()
        self.c.avanzar()
        chat = '\n'.join(self.c.canal.lineas())
        self.assertIn('[concepto] 01-CONCEPTO.md v1 · nuevo (argon)', chat)
        # Lo que cambia fuera del motor (el orquestador recorta el speech, Didier deja la voz) también se anuncia.
        (self.trabajo / '01-CONCEPTO.md').write_text('recortado a mano')
        (self.trabajo / 'PLATICA.md').write_text('copia del trabajador: no se anuncia')
        self.c.decidir('aprobar')
        chat = '\n'.join(self.c.canal.lineas())
        self.assertIn('01-CONCEPTO.md v2 · cambió (cambio externo)', chat)
        self.assertNotIn('PLATICA.md v', chat)
        (self.trabajo / '01-CONCEPTO.md').unlink()
        self.c.nota('Recorté el speech para que quepa en 37 s')
        lineas = self.c.canal.lineas()
        self.assertIn('01-CONCEPTO.md borrado (cambio externo)', '\n'.join(lineas))
        self.assertTrue(lineas[-1].endswith('orquestador→todos: [estilo] Recorté el speech para que quepa en 37 s'))

    def test_speech_corregido_y_aprobado_reajusta_las_escenas(self):
        # FALLA 46: el speech cambió de fondo y 02-ESCENAS.md se quedó con el texto viejo.
        self.iniciar()
        self.c.estado.update(indice=next(i for i, e in enumerate(self.c.etapas()) if e['id'] == 'speech'), fase='compuerta')
        self.c.estado['vueltas']['speech'] = 1
        (self.trabajo / '03-SPEECH.md').write_text('SPEECH:\nNuevo.\nFIN\n', encoding='utf-8')
        self.turnos.clear()
        try:
            self.c.decidir('aprobar')
        except ValueError:
            pass  # la voz falta: la ruta se detiene en la espera
        self.assertTrue(any('Ajusta 02-ESCENAS.md' in m for _, _, m in self.turnos))
        self.assertTrue((self.trabajo / '02-ESCENAS.md').exists())

    def test_retoque_del_estilo_va_al_papel_retocar(self):
        # FALLA 48: el retoque chico iba al ganador con todo su contexto.
        self.iniciar()
        estilo = next(e for e in self.c.etapas() if e['id'] == 'estilo')
        self.assertEqual(estilo.get('retoca'), 'retocar')
        visto = {}
        self.c.elenco = lambda etapa: visto.setdefault('papel', etapa.get('papel')) and {'agente': 'sonnet'}
        self.assertEqual(self.c.quien_corrige(estilo)['agente'], 'sonnet')
        self.assertEqual(visto['papel'], 'retocar')

    def test_preguntas_al_orquestador_salen_en_la_compuerta(self):
        self.respuestas['argon'] = lambda motivo: '@orquestador ¿el SRT es el audio grabado?' if 'abre' in motivo or 'propuesta' in motivo else 'ok'
        self.iniciar()
        salida = self.c.avanzar()
        self.assertIn('Preguntas sin contestar (1', salida)
        self.assertIn('argon: [concepto] @orquestador ¿el SRT es el audio grabado?', salida)
        with self.assertRaises(ValueError):  # FALLA 47: no se decide con preguntas sin contestar
            self.c.decidir('aprobar')
        self.c.nota('Sí, el SRT es el de la voz grabada')
        salida = self.c.decidir('corregir', 'otra vuelta')
        self.assertNotIn('Preguntas sin contestar', salida)  # la nota las contestó y se cuentan desde la última decisión

    def test_el_trabajador_recibe_en_su_platica_lo_que_se_dice_mientras_trabaja(self):
        self.iniciar()
        carpeta = self.trabajo
        original = self.taller.lanzar
        def lanzar(trabajo, al_terminar):
            self.c.canal.agregar('sol', 'todos', 'un detalle tardío')
            return original(trabajo, al_terminar)
        self.taller.lanzar = lanzar
        self.c.taller('sonnet', {}, 'Escribe 04-STORYBOARD.md')
        self.assertIn('sol→todos: un detalle tardío', (carpeta / 'PLATICA.md').read_text())
        self.assertEqual(self.c.canal.espejos, {})
        self.c.canal.agregar('sol', 'todos', 'después de terminar')
        self.assertNotIn('después de terminar', (carpeta / 'PLATICA.md').read_text())

    def test_el_reparto_es_fijo_y_el_marcador_solo_sugiere(self):
        self.iniciar()
        storyboard = [e for e in self.c.etapas() if e['id'] == 'storyboard'][0]
        for casos in range(1, 12):  # FALLA 49: ningún caso le toca a la alternativa por turno
            with mock.patch.object(self.c, 'evidencia', return_value=(None, casos)):
                self.assertEqual(self.c.castear(storyboard['trabajo'])['agente'], 'opus')
        con_datos = {'agente': 'argon', 'modelo': None, 'esfuerzo': 'medium', 'motivo': 'evidencia x6'}
        with mock.patch.object(self.c, 'evidencia', return_value=(dict(con_datos), 6)):
            elegido = self.c.castear(storyboard['trabajo'])
        self.assertEqual(elegido['agente'], 'opus')
        self.assertIn('el marcador sugiere a argon', elegido['motivo'])

    def test_sin_ab_pedido_el_estilo_lo_hace_uno_solo(self):
        salida = self.iniciar()
        self.assertIn('pregúntale a Didier', salida)
        self.c.avanzar()
        salida = self.c.decidir('aprobar')
        self.assertIn('Compuerta «estilo»', salida)
        estilos = [t for t in self.taller.trabajos if 'estilo.html' in t['instruccion']]
        self.assertEqual([t['agente'] for t in estilos], ['opus'])
        self.assertFalse(any('hojaA.png' in t['instruccion'] for t in self.taller.trabajos))
        self.assertEqual((self.trabajo / 'estilo.html').read_text(), 'ESTILO de opus')
        self.assertIsNone(self.c.estado['ab'])
        self.assertEqual(self.c.estado['ganador']['agente'], 'opus')
        self.assertNotIn('A/B a ciegas', self.c.quien_construyo(self.c.etapa())['motivo'])

    def test_ab_pedido_en_escenas_detiene_y_didier_escoge(self):
        self.iniciar(ab=['escenas'])
        self.c.avanzar()
        salida = self.c.decidir('aprobar')
        self.assertIn('Compuerta «A/B»', salida)
        b = self.taller.trabajos[-1]
        self.assertEqual(b['agente'], 'sonnet')
        self.assertNotIn('canal', b)  # a ciegas
        self.assertNotIn('[escenas]', b['chat'])
        self.assertIn('usar-b', salida)
        self.assertEqual((self.trabajo / 'ab/escenas/02-ESCENAS.md').read_text(), 'VERSION B de sonnet')
        salida = self.c.decidir('usar-b', 'mejor ritmo')
        self.assertEqual((self.trabajo / '02-ESCENAS.md').read_text(), 'VERSION B de sonnet')
        self.assertIn('Compuerta «estilo»', salida)
        self.assertEqual(self.c.estado['elenco']['escenas']['agente'], 'sonnet')
        self.assertIn('Se usa la versión B. mejor ritmo', '\n'.join(self.c.canal.lineas()))

    def test_ab_en_etapa_de_taller_con_entregable_binario(self):
        self.iniciar()
        etapa = {'id': 'sonidos', 'tipo': 'taller', 'trabajo': 'construir', 'papel': 'animar', 'entregable': 'audio-final.wav',
                 'instruccion': 'Haz el audio audio-final.wav con {carpeta}. {correccion}'}
        self.c.estado['ab_pedidos'] = ['sonidos']
        self.c.etapa_taller(etapa)
        self.assertEqual(self.taller.trabajos[-2]['agente'], 'opus')
        self.assertEqual(self.taller.trabajos[-1]['agente'], 'sonnet')
        self.assertNotIn('canal', self.taller.trabajos[-1])  # a ciegas
        self.assertIn('sonidos', self.c.estado['versiones_b'])
        (self.trabajo / 'audio-final.wav').write_bytes(b'RIFF\xff\xfe\x00')
        self.assertIn('binario', self.c.entregable(etapa))

    def test_ab_pedido_y_aprobar_se_queda_con_la_a(self):
        self.iniciar(ab=['escenas'])
        self.c.avanzar()
        self.c.decidir('aprobar')
        antes = (self.trabajo / '02-ESCENAS.md').read_text()
        self.c.decidir('aprobar')
        self.assertEqual((self.trabajo / '02-ESCENAS.md').read_text(), antes)
        self.assertEqual(self.c.estado['elenco']['escenas']['agente'], 'opus')

    def test_el_speech_no_admite_ab_y_usar_b_exige_version_b(self):
        with self.assertRaisesRegex(ValueError, 'speech.*no admite'):
            self.iniciar(ab=['speech'])
        self.assertFalse(self.c.estado)  # no arrancó nada
        self.iniciar()
        self.c.avanzar()
        with self.assertRaisesRegex(ValueError, 'no tiene versión B'):
            self.c.decidir('usar-b')

    def test_avisa_si_el_libro_original_cambio(self):
        origen = self.tmp / 'ORIGINAL.md'
        origen.write_text('cambió')
        ruta = self.base / 'equipo/oficios/video/oficio.json'
        oficio = json.loads(ruta.read_text(encoding='utf-8'))
        oficio['libro_origen'] = {'archivo': str(origen), 'copiado': '2026-11-03', 'huella': 'vieja'}
        ruta.write_text(json.dumps(oficio, ensure_ascii=False), encoding='utf-8')
        self.assertIn('el libro de ProjectA cambió', self.iniciar())

    def test_elenco_por_papel_y_aviso_de_a_priori_general(self):
        self.iniciar()
        animar = self.c.castear('construir', 'animar')
        retocar = self.c.castear('construir', 'retocar')
        self.assertEqual((animar['agente'], retocar['agente']), ('opus', 'sonnet'))
        self.assertIn('decisión de Didier', animar['motivo'])
        # Un oficio sin a priori propio usa el general, y lo dice (FALLA 40).
        with mock.patch.object(self.c, 'oficio', return_value='perfil-linkedin'):
            general = self.c.castear('construir')
        self.assertIn('general: sin a priori propio de perfil-linkedin', general['motivo'])

    def test_el_ganador_del_ab_guarda_por_que_gano(self):
        self.iniciar()
        self.c.estado.update(ab={'juez': 'GANA A: surcos legibles a tamaño de teléfono'},
                             ganador={'agente': 'sonnet', 'modelo': 'sonnet', 'esfuerzo': 'medium'})
        estilo = [e for e in self.c.etapas() if e['id'] == 'estilo'][0]
        self.assertEqual(self.c.quien_construyo(estilo)['motivo'], 'A/B a ciegas: GANA A: surcos legibles a tamaño de teléfono')

    def test_toda_etapa_con_agente_dice_por_que(self):
        self.iniciar()
        for etapa in self.c.etapas():
            self.assertIn(etapa.get('trabajo'), ('idear', 'decidir', 'especificar', 'construir', 'verificar', 'juzgar'), etapa['id'])
            if etapa.get('tipo', 'platica') == 'platica':
                self.assertTrue(self.c.elenco(etapa)['motivo'])
                self.assertNotIn('sin motivo', self.c.elenco(etapa)['motivo'])
            if etapa.get('lider') or etapa.get('tipo') == 'taller-ganador':
                self.assertTrue(etapa.get('motivo_elenco'), etapa['id'])

    def test_hechos_medidos_llegan_a_la_platica_y_al_taller_con_su_verificar(self):
        self.iniciar()
        self.c.avanzar()
        self.assertIn('"palabras_max_speech":57', self.turnos[0][2])  # 25 s × 2.3, sin modelo
        (self.trabajo / 'voz.srt').write_text('1')
        self.c.estado.update(indice=6)
        animacion = self.c.etapa()
        self.assertEqual(animacion['id'], 'animacion')
        self.c.marcar(animacion)
        self.c.taller('opus', {}, 'Construye reel.html')
        trabajo = self.taller.trabajos[-1]
        self.assertEqual(trabajo['hechos']['ancho'], 1080)
        # La prueba del animador muestra cada escena en su tiempo clave, no un cuadro cada N s (FALLA 39).
        self.assertEqual(trabajo['verificar'], 'node {}/herramientas/hoja-html.mjs reel.html _verificar/hoja.png '
                                               '--tiempos-de 04-STORYBOARD.md --cada 2'.format(self.base))

    def test_el_oficio_pone_herramientas_verificar_notas_y_rubrica(self):
        oficio = self.base / 'equipo/oficios/video'
        datos = json.loads((oficio / 'oficio.json').read_text())
        datos['herramientas']['extra'] = ['echo', '{carpeta}']
        datos.update(verificar={'reel.html': 'node {base}/v.mjs reel.html'}, rubrica='rubrica.md', notas='notas.md')
        (oficio / 'oficio.json').write_text(json.dumps(datos))
        (oficio / 'rubrica.md').write_text('RUBRICA DE VIDEO')
        (oficio / 'notas.md').write_text('Cuadros fijos antes de animar.')
        self.iniciar()
        ruta = self.c.ruta()
        self.assertIn('extra', ruta['herramientas'])
        self.assertIn('render', ruta['herramientas'])  # el resto del oficio sigue
        animacion = [e for e in self.c.etapas() if e['id'] == 'animacion'][0]
        sin_propio = dict(animacion)
        sin_propio.pop('verificar', None)
        self.assertEqual(self.c.verificar(sin_propio), 'node {}/v.mjs reel.html'.format(self.base))
        self.assertEqual(self.c.canal.estado['rubrica'], str(oficio / 'rubrica.md'))
        self.c.avanzar()
        self.assertIn('Notas del oficio: Cuadros fijos antes de animar.', self.turnos[0][2])
        self.c.taller('opus', {}, 'Corrige reel.html')
        self.assertIn('NOTAS DEL OFICIO\nCuadros fijos antes de animar.', self.taller.trabajos[-1]['contexto'])
        self.c.canal.cerrar()
        self.assertTrue(list((self.base / 'registro/marcador-evidencia').glob('tarea-*/RUBRICA.md')))

    def test_el_entregable_va_al_archivo_y_al_canal_una_linea(self):
        self.respuestas['argon'] = 'Dirección completa ' + 'muy larga ' * 80
        self.iniciar()
        self.c.avanzar()
        concepto = (self.trabajo / '01-CONCEPTO.md').read_text()
        self.assertIn('muy larga ' * 80, concepto)
        cierre = [l for l in self.c.canal.lineas() if 'dejé 01-CONCEPTO.md' in l][0]
        self.assertIn('(162 palabras)', cierre)
        self.assertLess(len(cierre), 260)

    def test_idear_en_paralelo_y_lo_demas_en_orden(self):
        self.iniciar()
        with mock.patch.object(R.threading, 'Thread', wraps=R.threading.Thread) as hilos:
            self.c.avanzar()
        self.assertEqual(sum(1 for c in hilos.call_args_list if c.kwargs.get('target') == self.c.hablar), 2)
        self.assertEqual(sorted(n for n, _ in self.quienes()[1:3]), ['flash', 'opus'])

    def test_pasarse_del_tope_de_gasto_detiene_la_ruta_y_seguir_abre_otra_rebanada(self):
        self.iniciar()
        ruta = self.c.ruta()
        ruta['tope_usd'] = 1.0
        gastado = [0.5]
        with mock.patch.object(self.c, 'ruta', return_value=ruta), mock.patch.object(self.c, 'usd', side_effect=lambda: gastado[0]):
            self.c.avanzar()
            gastado[0] = 1.5
            salida = self.c.decidir('aprobar')  # concepto aprobado; escenas gasta y rebasa el tope
            self.assertIn('ALERTA: Tope de gasto: 1.50 USD de 1.00', salida)
            self.assertEqual(self.c.etapa()['id'], 'escenas')
            salida = self.c.decidir('aprobar')
            self.assertEqual(self.c.estado['tope_actual'], 2.5)
            self.assertIn('Compuerta «estilo»', salida)

    def test_nada_se_corta_en_silencio(self):
        self.assertEqual(R.recortar('corto', 10), 'corto')
        self.assertEqual(R.recortar('x' * 20, 10), 'x' * 10 + ' …[cortado: 10 de 20 caracteres]')
        self.iniciar()
        etapa = self.c.etapas()[0]
        (self.trabajo / etapa['entregable']).write_text('y' * 9000)
        self.c.revisar(etapa, self.c.entregable(etapa))
        self.assertIn('y' * 9000, self.turnos[-1][2])  # el revisor ve el storyboard entero

    def test_abandonar_descarta_la_platica_sin_calificarla(self):
        self.iniciar()
        self.c.avanzar()
        tarea = self.c.estado['tarea']
        self.assertEqual(self.c.abandonar(), 'Ruta abandonada.')
        self.assertIn('plática descartada (tarea {}, sin calificar)'.format(tarea), self.c.canal.lineas()[-1])
        self.assertFalse((self.base / 'registro/marcador-evidencia/tarea-{}'.format(tarea)).exists())
        self.assertTrue(self.c.canal.estado['cerrada'])
        self.iniciar()  # la siguiente corrida abre su propia plática

    def test_errores_de_uso(self):
        with self.assertRaises(ValueError):
            self.c.avanzar()
        self.iniciar()
        with self.assertRaises(ValueError):
            self.iniciar()
        with self.assertRaises(ValueError):
            self.c.decidir('aprobar')

    def test_turno_caido_no_tumba_la_ruta(self):
        def falla(motivo):
            raise ValueError('motor caído')
        self.respuestas['flash'] = falla
        self.iniciar()
        salida = self.c.avanzar()
        self.assertIn('Compuerta «ángulo»', salida)
        self.assertTrue(any('flash no contestó' in l for l in self.c.canal.lineas()))


if __name__ == '__main__':
    unittest.main()
