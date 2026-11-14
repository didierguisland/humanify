#!/usr/bin/python3
"""Rutas del Teams: el orquestador dirige por etapas. Cada etapa tiene líder, invitados, entregable, revisor
y, si la ruta lo dice, una compuerta donde el orquestador decide. La lógica la pone este archivo, no los agentes.
Solo biblioteca estándar."""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import os
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _mundos  # noqa: E402
import _teams as T  # noqa: E402

TOPES = {'lider': 700, 'invitado': 250, 'cierre': 1500, 'revisor': 300, 'libro': 1500}
PAPELES = {'lider': 'líder: abre con una propuesta', 'invitado': 'invitado: reacciona una vez',
           'cierre': 'líder: cierra e integra', 'revisor': 'revisor',
           'libro': 'revisor del libro de correcciones: reporta, no corrige'}
PEDIDO_LIBRO = ('Revisa regla por regla contra este libro de correcciones. Formato: una línea "FALLA: <la frase o cuadro exacto> · '
                '<tema>: <regla>" por cada falla; una línea "NO SE PUEDE REVISAR: <regla>" si con lo que tienes no se puede saber '
                'o no aplica a este tipo de video (nunca la marques FALLA por eso); al final "PASAN: <cuántas>". No corrijas, no '
                'propongas versiones y no hagas preguntas. Si Didier ya decidió algo que choca con una regla, repórtalo igual y '
                'agrega "(Didier lo decidió)": decide quien tenga la compuerta. Una regla que dice "solo serie Example Brand" no aplica si el brief no es de esa '
                'serie.\n\nLIBRO:\n{}')
TOPE_ENTREGABLE = 12000  # el revisor ve el entregable entero (un storyboard mide ~7 mil)
SOLO_ENTREGABLE = ('Responde SOLO con el entregable, listo para usarse: sin saludos, sin comentarios sobre '
                   'el proceso y sin mencionar a nadie.')  # lo que ve el revisor del entregable
ESPERA_SCRIPT = 900
ESPERAS = {'comprimir': 2400}  # hasta 4 pasadas -preset slow de 1080x1920
FECHA = re.compile(r'\b\d{1,2} de (?:enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)\b', re.I)
# La bitácora no anuncia lo que es del propio trabajador (su copia de la plática, sus pruebas).
NO_ANUNCIAR = ('PLATICA.md', 'HECHOS.json')
CARPETAS_PRIVADAS = ('_verificar',)
SIN_ELENCO = ('espera', 'script', 'render', 'leccion', 'ab', 'taller-ganador')  # sin papel que castear (o con su propia regla)
PREGUNTA = re.compile(r'@(orquestador|didier)\b', re.I)
PARADAS = ('ninguna', 'didier')


class Pausa(ValueError):
    """La ruta no puede seguir sin mentir: se detiene con el error real hasta que se corra `ruta reintentar`."""
    def __init__(self, error, limite=None):
        super().__init__(error)
        self.limite = limite


def proceso_vivo(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True  # existe, aunque no sea nuestro
    return True


def huella_de(archivo):
    try:
        return hashlib.sha1(Path(archivo).read_bytes()).hexdigest()
    except OSError:
        return None


class Conductor:
    def __init__(self, proyecto):
        self.candado = threading.RLock()
        self.canal = T.Canal(proyecto)
        self.archivo = self.canal.carpeta / 'ruta.json'
        try:
            self.estado = json.loads(self.archivo.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self.estado = None

    # --- estado ---------------------------------------------------------------------------------

    def guardar(self):
        temporal = self.archivo.with_suffix('.tmp')
        temporal.write_text(json.dumps(self.estado, ensure_ascii=False, indent=1), encoding='utf-8')
        temporal.replace(self.archivo)
        self.canal.guardar_estado()  # la actividad del canal va con la ruta: si no, contaría desde el arranque

    def ruta(self):
        """La ruta con su oficio ya mezclado: lo del dominio (herramientas, hechos, verificar, notas, rúbrica)
        vive en equipo/oficios/<oficio>/; la ruta solo dice las etapas y puede sobrescribir."""
        archivo = self.canal.base / 'equipo/rutas' / (self.estado['ruta'] + '.json')
        if not archivo.exists():  # una ruta archivada ya no se puede iniciar, pero su corrida vieja se puede consultar
            archivo = self.canal.base / 'equipo/rutas/archivo' / (self.estado['ruta'] + '.json')
        ruta = json.loads(archivo.read_text(encoding='utf-8'))
        oficio = self.datos_oficio(ruta.get('oficio'))
        if oficio:
            ruta['herramientas'] = dict(oficio.get('herramientas') or {}, **(ruta.get('herramientas') or {}))
            ruta['hechos'] = dict(oficio.get('hechos') or {}, **(ruta.get('hechos') or {}))
            ruta['hechos_dados'] = list(dict.fromkeys((oficio.get('hechos_dados') or []) + (ruta.get('hechos_dados') or [])))
            ruta['verificar'] = dict(oficio.get('verificar') or {}, **(ruta.get('verificar') or {}))
        return ruta

    def datos_oficio(self, nombre):
        if not nombre:
            return None
        carpeta = self.canal.base / 'equipo/oficios' / nombre
        try:
            datos = json.loads((carpeta / 'oficio.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None
        for clave in ('rubrica', 'notas', 'libro_correcciones'):
            if datos.get(clave):
                datos[clave] = carpeta / datos[clave]
        return datos

    def notas_oficio(self):
        oficio = self.datos_oficio(self.ruta().get('oficio'))
        archivo = (oficio or {}).get('notas')
        try:
            return archivo.read_text(encoding='utf-8').strip() if archivo else ''
        except OSError:
            return ''

    def etapas(self):
        return self.ruta()['etapas']

    def etapa(self):
        return self.etapas()[self.estado['indice']]

    def carpeta(self):
        return Path(self.estado['carpeta'])

    def foto(self, raiz=None):
        """{ruta relativa: huella} de la carpeta de trabajo (o de una subcarpeta)."""
        base = self.carpeta()
        raiz = Path(raiz) if raiz else base
        resultado = {}
        if not raiz.is_dir():
            return resultado
        for archivo in sorted(raiz.rglob('*')):
            relativa = archivo.relative_to(base)
            if (not archivo.is_file() or archivo.name in NO_ANUNCIAR or archivo.name.startswith('.')
                    or any(parte in CARPETAS_PRIVADAS for parte in relativa.parts)):
                continue
            digesto = hashlib.sha1()
            with archivo.open('rb') as stream:
                for bloque in iter(lambda: stream.read(1 << 20), b''):
                    digesto.update(bloque)
            resultado[str(relativa)] = digesto.hexdigest()[:12]
        return resultado

    def anunciar(self, quien, raiz=None):
        """Invariante del motor: todo archivo que cambia en la carpeta se anuncia en el canal, con su versión
        y quién lo cambió. quien=None solo toma la foto (punto de partida)."""
        with self.candado:
            actual = self.foto(raiz)
            previa = self.estado.setdefault('foto', {})
            base = self.carpeta()
            prefijo = str(Path(raiz).relative_to(base)) + '/' if raiz and Path(raiz) != base else ''
            if prefijo:
                previa_zona = {k: v for k, v in previa.items() if k.startswith(prefijo)}
            else:
                previa_zona = previa
            etiqueta = self.etapas()[min(self.estado['indice'], len(self.etapas()) - 1)]['id']
            for ruta, huella in actual.items():
                anterior = previa_zona.get(ruta)
                if anterior and anterior[0] == huella:
                    continue
                version = (anterior[1] + 1) if anterior else 1
                previa[ruta] = [huella, version]
                if quien:
                    self.canal.agregar('sistema', 'todos', '[{}] {} v{} · {}'.format(
                        etiqueta, ruta, version, 'nuevo' if version == 1 and not anterior else 'cambió') + ' (' + quien + ')')
            for ruta in [r for r in previa_zona if r not in actual]:
                del previa[ruta]
                if quien:
                    self.canal.agregar('sistema', 'todos', '[{}] {} borrado ({})'.format(etiqueta, ruta, quien))
            self.guardar()

    # --- elenco: quién hace cada etapa, siempre con su motivo -----------------------------------------

    def oficio(self):
        return self.ruta().get('oficio', self.estado['ruta'])

    def elenco(self, etapa):
        """Invariante: toda asignación dice por qué (reparto fijo de equipo/elenco.json o regla de la ruta con su
        motivo). Nada cambia de agente solo: probar otro es un A/B que Didier pide (--ab)."""
        guardado = self.estado.setdefault('elenco', {}).get(etapa['id'])
        if guardado:
            return guardado
        explicito = etapa.get('lider') or (etapa.get('taller') or {}).get('agente')
        if explicito:
            config = dict(self.canal.miembros.get(explicito, {}))
            elegido = {'agente': explicito, 'modelo': (etapa.get('taller') or {}).get('modelo') or config.get('modelo'),
                       'esfuerzo': (etapa.get('taller') or {}).get('esfuerzo') or config.get('esfuerzo'),
                       'motivo': etapa.get('motivo_elenco') or 'fijo en la ruta, sin motivo (revisar)'}
        else:
            elegido = self.castear(etapa['trabajo'], etapa.get('papel'))
        self.estado['elenco'][etapa['id']] = elegido
        self.canal.agregar('sistema', 'todos', '[{}] elenco: {} {} · {}'.format(
            etapa['id'], elegido['agente'], elegido['esfuerzo'] or '', elegido['motivo']))
        self.guardar()
        return elegido

    def castear(self, trabajo, papel=None):
        """papel afina el tipo de trabajo dentro de un oficio (animar, retocar). El reparto es fijo (FALLA 49): la
        evidencia del marcador solo se menciona en el motivo, para que Didier decida."""
        oficio = self.oficio()
        a_priori, general = self.reparto_fijo(trabajo, papel)
        if a_priori is None:
            raise ValueError('nadie en el elenco para «{}» ({}): agrégalo a equipo/elenco.json'.format(papel or trabajo, oficio))
        motivo = a_priori.get('motivo') or 'a priori sin motivo (revisar)'
        if general:
            motivo += ' · general: sin a priori propio de {}'.format(oficio)  # FALLA 40
        evidencia, _ = self.evidencia(trabajo, oficio, papel)
        if evidencia and evidencia['agente'] != a_priori['agente']:
            motivo += ' · el marcador sugiere a {} ({}); decide Didier'.format(evidencia['agente'], evidencia.get('motivo'))
        elegido = {'agente': a_priori['agente'], 'modelo': a_priori.get('modelo'), 'esfuerzo': a_priori.get('esfuerzo'), 'motivo': motivo}
        config = self.canal.miembros.get(elegido['agente'])
        if config is None:
            raise ValueError('el elenco nombra a {}, que no está en el equipo'.format(elegido['agente']))
        elegido['modelo'] = elegido['modelo'] or config.get('modelo')
        elegido['esfuerzo'] = elegido.get('esfuerzo') or config.get('esfuerzo')
        return elegido

    def reparto_fijo(self, trabajo, papel=None):
        """(entrada de equipo/elenco.json, si es la general): primero por papel y luego por tipo de trabajo."""
        try:
            tabla = json.loads((self.canal.base / 'equipo/elenco.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            tabla = {}
        propio = tabla.get(self.oficio()) or {}
        entrada = (papel and propio.get(papel)) or propio.get(trabajo)
        return (entrada, False) if entrada else ((tabla.get('*') or {}).get(trabajo), True)

    def ab_pedido(self, etapa):
        return etapa['id'] in (self.estado.get('ab_pedidos') or [])

    def validar_ab(self, pedidos):
        """Solo etapas sin agente fijo y con una alternativa en el elenco (el speech siempre es de Opus)."""
        etapas = {e['id']: e for e in self.etapas()}
        for pedido in pedidos:
            etapa = etapas.get(pedido)
            if etapa is None:
                raise ValueError('--ab: la ruta no tiene la etapa «{}»'.format(pedido))
            if etapa.get('tipo') == 'ab':
                continue
            if etapa.get('tipo', 'platica') != 'platica' or etapa.get('lider') or not self.alternativa(etapa):
                raise ValueError('--ab: «{}» no admite A/B'.format(pedido))

    def alternativa(self, etapa):
        entrada, _ = self.reparto_fijo(etapa.get('trabajo'), etapa.get('papel'))
        return (entrada or {}).get('alternativa')

    def evidencia(self, trabajo, oficio, papel=None):
        db = self.canal.base / 'registro/marcador.db'
        if not db.exists():
            return None, 0
        try:
            import _marcador as M
            with sqlite3.connect('file:{}?mode=ro'.format(db), uri=True) as con:
                con.row_factory = sqlite3.Row
                return M.sugerir_trabajo(con, trabajo, oficio, papel)
        except (sqlite3.Error, ImportError):
            return None, 0  # base vieja sin la vista de etapas: decide el a priori

    def hechos(self):
        """Hechos medidos sin modelo (herramientas/hechos.py) según la ruta, más los que dio el orquestador
        al iniciar. Invariante: nadie estima lo que se puede medir."""
        dados = dict(self.estado.get('hechos_dados') or {})
        for nombre in self.ruta().get('hechos_dados', []):
            dados.setdefault(nombre, None)  # la ruta los pide al iniciar; si no llegaron, quedan en null
        spec = {k: {'fijo': v} for k, v in dados.items()}
        spec.update(self.ruta().get('hechos') or {})
        if not spec:
            return None
        salida = subprocess.run(['/usr/bin/python3', str(self.canal.base / 'herramientas/hechos.py'), str(self.carpeta()),
                                 '--spec', json.dumps(spec)], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                timeout=60)
        if salida.returncode:
            # Medir es una ayuda: si algo no se puede medir se avisa una vez y la ruta sigue.
            error = T.plano((salida.stderr or salida.stdout).splitlines()[-1] if (salida.stderr or salida.stdout) else 'sin salida', 200)
            if self.estado.get('error_hechos') != error:
                self.estado['error_hechos'] = error
                self.canal.agregar('sistema', 'todos', 'hechos: no se pudo medir ({})'.format(error))
            return None
        self.estado.pop('error_hechos', None)
        return json.loads(salida.stdout)['hechos']

    def verificar(self, etapa):
        """El comando de prueba de la etapa: el suyo o el que el oficio declara para su entregable."""
        comando = etapa.get('verificar') or (self.ruta().get('verificar') or {}).get(etapa.get('entregable', ''))
        return comando.format(base=self.canal.base) if comando else None

    def desmarcar(self):
        for variable in ('MARCADOR_ETAPA', 'MARCADOR_TRABAJO'):
            os.environ.pop(variable, None)

    def retro(self, contra=None):
        """Tabla por etapa sin modelo (herramientas/retro.py); queda en la carpeta como RETRO.md."""
        self.exigir()
        self.procesar_marcador()
        comando = ['/usr/bin/python3', str(self.canal.base / 'herramientas/retro.py'), '--tarea', str(self.estado['tarea']),
                   '-o', str(self.carpeta() / 'RETRO.md')]
        if contra:
            comando += ['--contra', str(contra)]
        salida = subprocess.run(comando, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
        if salida.returncode:
            raise ValueError('retro falló: ' + T.plano(salida.stderr or salida.stdout, 200))
        return 'Retro en {}'.format(self.carpeta() / 'RETRO.md')

    def marcar(self, etapa):
        """Toda corrida que se dispare en esta etapa queda marcada con su etapa y tipo de trabajo."""
        os.environ['MARCADOR_ETAPA'] = etapa['id']
        os.environ['MARCADOR_TRABAJO'] = etapa.get('trabajo', '')

    def quien_corrige(self, etapa, fondo=False):
        """Quién corrige: en el render, el papel 'retoca' del elenco (si la ruta lo pide); en una etapa de taller,
        su propio elenco; si no, el ganador del A/B. fondo=True (lo decide el orquestador): lo de fondo no es para el retoque."""
        if fondo:
            return self.elenco({'id': etapa['id'] + ':fondo', 'trabajo': 'construir', 'papel': etapa.get('fondo', 'animar')})
        if etapa.get('tipo') in ('render', 'ab') and etapa.get('retoca'):
            # Un retoque acotado no necesita al constructor con todo su contexto (FALLA 48: 0.66 USD por dos detalles).
            return self.elenco({'id': etapa['id'] + ':retoque', 'trabajo': 'construir', 'papel': etapa['retoca']})
        if etapa.get('tipo') == 'taller':
            return self.elenco(etapa)
        return self.estado['ganador']

    def quien_construyo(self, etapa):
        if etapa.get('tipo') == 'taller':
            return self.estado.get('elenco', {}).get(etapa['id'])
        if etapa.get('tipo') in ('ab', 'taller-ganador'):
            ganador = dict(self.estado.get('ganador') or {})
            if ganador and etapa.get('tipo') == 'ab' and self.estado.get('ab'):
                # Por qué ganó queda en el marcador: así se puede ver después si fue el modelo o la instrucción.
                ganador['motivo'] = 'A/B a ciegas: ' + ((self.estado.get('ab') or {}).get('juez') or '')[:600]
            elif ganador and etapa.get('tipo') != 'ab':
                ganador['motivo'] = etapa.get('motivo_elenco', 'continuidad con el ganador del A/B')
            return ganador or None
        return self.estado.get('elenco', {}).get(etapa['id'])

    def registrar_etapa(self, etapa, aprobada, quien=None, sufijo='', ab_gano=None):
        quien = quien or self.estado.get('elenco', {}).get(etapa['id'])
        herramienta = T.marcador_sh()
        if not quien or not herramienta or not etapa.get('trabajo'):
            return
        evento = {'evento': 'etapa', 'tarea': self.estado['tarea'], 'ruta': self.estado['ruta'], 'oficio': self.oficio(),
                  'etapa': etapa['id'] + sufijo, 'trabajo': etapa['trabajo'], 'papel': etapa.get('papel'),
                  'agente': quien['agente'],
                  'modelo': quien.get('modelo'), 'esfuerzo': quien.get('esfuerzo'), 'motivo': quien.get('motivo'),
                  'vueltas': self.estado['vueltas'].get(etapa['id'], 0), 'aprobada': aprobada, 'ab_gano': ab_gano}
        try:
            subprocess.run(['/bin/bash', str(herramienta), 'encolar', json.dumps(evento, ensure_ascii=False)],
                           stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass  # el registro es medición: nunca detiene la ruta

    def exigir(self):
        if not self.estado:
            raise ValueError('no hay ruta; empieza con: ruta iniciar')

    # --- comandos -------------------------------------------------------------------------------

    def iniciar(self, nombre, carpeta, brief, paradas='ninguna', hechos=None, ab=None, con=None):
        if self.estado and self.estado.get('fase') != 'terminada':
            raise ValueError('ya hay una ruta en curso ({}, etapa {}); mira ruta estado y termínala, o usa: ruta archivar'.format(
                self.estado['ruta'], self.estado['indice'] + 1))
        if not (self.canal.base / 'equipo/rutas' / (nombre + '.json')).exists():
            raise ValueError('no existe la ruta ' + nombre)
        if paradas not in PARADAS:
            raise ValueError('paradas: ' + ' o '.join(PARADAS))
        previo, self.estado = self.estado, {'ruta': nombre}
        try:
            self.validar_ab(ab or [])
        finally:
            self.estado = previo
        # Cada corrida es su propia plática y su propia tarea en el marcador.
        if len(self.canal.lineas()) > self.canal.estado.get('linea_inicio', 0):
            self.canal.cerrar()
            self.canal = T.Canal(self.canal.proyecto)
        carpeta = Path(carpeta).expanduser().resolve()
        carpeta.mkdir(parents=True, exist_ok=True)
        self.canal.estado['carpeta'] = str(carpeta)
        definicion = json.loads((self.canal.base / 'equipo/rutas' / (nombre + '.json')).read_text(encoding='utf-8'))
        oficio = self.datos_oficio(definicion.get('oficio'))
        if oficio and oficio.get('rubrica'):
            self.canal.estado['rubrica'] = str(oficio['rubrica'])  # el calificador juzga con la rúbrica del oficio
        if definicion.get('contexto') == 'paquete':
            # M13: nada de recursos, SRT ni fábrica de otro video; todo lo de este video va en su 00-PAQUETE.md.
            self.canal.estado.update(solo_paquete=True, procesos=[])
        else:
            self.canal.estado.pop('solo_paquete', None)
            self.canal.detectar_procesos(brief)
        self.canal.guardar_estado()
        self.estado = {'ruta': nombre, 'indice': 0, 'fase': 'lista', 'carpeta': str(carpeta), 'brief': brief,
                       'paradas': paradas, 'tarea': self.canal.estado['tarea'], 'inicio': time.time(),
                       'revisiones': {}, 'vueltas': {}, 'decisiones': [], 'ab': None, 'ganador': None,
                       'foto': {}, 'desde': 0, 'hechos_dados': hechos or {}, 'ab_pedidos': list(ab or []),
                       'con': list(con or [])}
        self.canal.agregar('orquestador', 'todos', 'Arranca la ruta {}. Brief: {}'.format(nombre, brief))
        faltan = [h for h in self.ruta().get('hechos_dados', []) if h not in (hechos or {})]
        self.estado['desde'] = len(self.canal.lineas())
        self.anunciar(None)  # lo que ya estaba en la carpeta es el punto de partida, no un cambio
        self.guardar()
        aviso = ' Ojo: faltan los hechos {} (pásalos con --hechos).'.format(', '.join(faltan)) if faltan else ''
        fabrica = self.sin_didier()
        aviso += ' A/B: {}.'.format(', '.join(self.estado['ab_pedidos']) if self.estado['ab_pedidos'] else
                                    'ninguno (reparto fijo)' if fabrica else
                                    'ninguno (reparto fijo; si el video lo amerita, pregúntale a Didier antes de empezar)')
        if definicion.get('contexto') == 'paquete' and not (carpeta / '00-PAQUETE.md').exists():
            aviso += ' Ojo: falta 00-PAQUETE.md en la carpeta; los agentes no tienen tema ni datos permitidos.'
        aviso += self.aviso_libro()
        return 'Ruta {} lista en {} (tarea {}).{} Sigue: ruta avanzar'.format(nombre, carpeta, self.estado['tarea'], aviso)

    def avanzar(self):
        self.exigir()
        if self.estado['fase'] == 'trabajando':
            if self.corre_en_otro_proceso():
                raise ValueError('esta ruta sigue corriendo en el proceso {}: espera a que termine'.format(self.estado.get('pid')))
            self.estado.setdefault('reintento', 'el proceso anterior se cortó sin avisar')
        self.anunciar('cambio externo')
        if self.estado['fase'] == 'compuerta':
            return self.resumen()
        if self.estado['fase'] == 'pausada':
            return self.aviso_pausa()
        if self.estado['fase'] == 'terminada':
            return 'La ruta ya terminó.\n' + self.gasto()
        metodos = {'platica': self.platica, 'espera': self.espera, 'ab': self.ab, 'taller': self.etapa_taller,
                   'taller-ganador': self.taller_ganador, 'render': self.render, 'leccion': self.leccion,
                   'script': self.etapa_script}
        while self.estado['indice'] < len(self.etapas()):
            etapa = self.etapa()
            if etapa.get('solo_con') and etapa['solo_con'] not in self.estado.get('con', []):
                self.estado['indice'] += 1  # etapa opcional apagada (se prende con ruta iniciar --con <nombre>)
                self.guardar()
                continue
            self.estado['fase'] = 'trabajando'
            self.estado['pid'] = os.getpid()
            self.guardar()
            self.marcar(etapa)
            try:
                listo = metodos[etapa.get('tipo', 'platica')](etapa)
                self.soltar_pausa_de_hilo()
            except Pausa as pausa:
                return self.pausar(pausa)
            self.estado.pop('reintento', None)
            if not listo:
                self.estado['fase'] = 'espera'
                self.guardar()
                texto = 'Etapa «{}»: faltan {} en {}. Ponlos ahí y corre: ruta avanzar'.format(
                    etapa['nombre'], ', '.join(self.faltantes(etapa)), self.carpeta())
                if etapa.get('muestra'):
                    muestra = self.carpeta() / etapa['muestra']
                    if muestra.exists():
                        texto += '\n\nPÉGALE ESTO A DIDIER TAL CUAL ({}):\n{}'.format(muestra, muestra.read_text(encoding='utf-8'))
                return texto
            if etapa.get('compuerta') or self.ab_pedido(etapa):
                self.estado['fase'] = 'compuerta'
                self.guardar()
                return self.resumen()
            tope = self.estado.get('tope_actual') or self.ruta().get('tope_usd')
            usd = self.usd() if tope else None
            if tope and usd is not None and usd > tope:
                # Pasarse del tope detiene la ruta en una compuerta: decidir aprobar sigue, gastando con permiso.
                self.estado['fase'] = 'compuerta'
                self.estado['alerta'] = 'Tope de gasto: {:.2f} USD de {:.2f} permitidos.'.format(usd, tope)
                self.guardar()
                return self.resumen()
            self.registrar_etapa(etapa, 1, self.quien_construyo(etapa))
            self.estado['indice'] += 1
            self.guardar()
        self.estado['fase'] = 'terminada'
        self.estado['fin'] = time.time()
        self.guardar()
        self.desmarcar()  # el calificador que dispara el cierre no es trabajo de ninguna etapa
        self.canal.cerrar()
        return 'Ruta terminada en {:.0f} min.\n{}'.format((self.estado['fin'] - self.estado['inicio']) / 60, self.gasto())

    def sin_didier(self):
        return bool(self.ruta().get('sin_didier'))

    def corre_en_otro_proceso(self):
        pid = self.estado.get('pid')
        return pid != os.getpid() and proceso_vivo(pid)

    def ya_hecho(self, etapa, paso, archivo):
        """Un reintento no repite lo que ya salió: una pausa del revisor no reconstruye (ni cobra) lo que el constructor dejó."""
        hecho = (self.estado.get('pasos') or {}).get(etapa['id'], {}).get(paso)
        return bool(self.estado.get('reintento')) and hecho is not None and hecho == huella_de(archivo)

    def anotar_paso(self, etapa, paso, archivo):
        self.estado.setdefault('pasos', {}).setdefault(etapa['id'], {})[paso] = huella_de(archivo)
        self.guardar()

    def pausar(self, pausa, correccion=None):
        etapa = self.etapa()
        self.estado['fase'] = 'pausada'
        self.estado['pausa'] = {'etapa': etapa['id'], 'error': str(pausa), 'limite': pausa.limite, 'correccion': correccion}
        self.canal.agregar('sistema', 'todos', '[{}] ruta pausada: {}'.format(etapa['id'], T.plano(str(pausa), 300)))
        self.guardar()
        return self.aviso_pausa()

    def aviso_pausa(self):
        pausa = self.estado.get('pausa') or {}
        lineas = ['PAUSADA en la etapa «{}»: {}'.format(pausa.get('etapa'), pausa.get('error'))]
        reintento = '{} ruta reintentar --proyecto {}'.format(self.canal.base / 'herramientas/humanify.sh', self.canal.proyecto.name)
        if pausa.get('limite') is not None:
            reinicio = pausa['limite'].get('reinicio')
            if reinicio:
                falta = max(0, int(reinicio - time.time())) + 60
                lineas.append('Límite de uso. Se libera a las {} (en {} min). No sigas de largo; deja esto corriendo:'.format(
                    dt.datetime.fromtimestamp(reinicio).strftime('%H:%M'), falta // 60))
                lineas.append('caffeinate -i /bin/sh -c "sleep {}; {}"'.format(falta, reintento))
            else:
                lineas.append('Límite de uso sin hora de reinicio. Prueba cada 30 min con: caffeinate -i /bin/sh -c "sleep 1800; {}"'.format(reintento))
        else:
            lineas.append('Arregla la causa y sigue con: ' + reintento)
        return '\n'.join(lineas)

    def reintentar(self):
        self.exigir()
        if self.estado['fase'] == 'trabajando':  # se cerró la app o la terminal a media etapa
            if self.corre_en_otro_proceso():
                raise ValueError('esta ruta sigue corriendo en el proceso {}: espera a que termine'.format(self.estado.get('pid')))
            pausa = {'error': 'el proceso anterior se cortó sin avisar'}
        elif self.estado['fase'] != 'pausada':
            raise ValueError('no hay pausa (fase: {})'.format(self.estado['fase']))
        else:
            pausa = self.estado.pop('pausa', None) or {}
        self.estado['reintento'] = pausa.get('error') or 'un intento anterior no terminó'
        correccion = pausa.get('correccion')
        self.estado['fase'] = 'compuerta' if correccion else 'lista'
        self.guardar()
        if correccion:
            try:
                return self.decidir(correccion.get('accion', 'corregir'), correccion['nota'], correccion.get('fondo'))
            except ValueError:
                # Si no se pudo ni empezar (preguntas sin contestar), la corrección sigue en pie: no se pierde.
                self.estado.update(pausa=pausa, fase='pausada')
                self.estado.pop('reintento', None)
                self.guardar()
                raise
        return self.avanzar()

    def cambiar_brief(self, texto):
        """O3: el brief se antepone a todo encargo; si la decisión cambió, el brief viejo no debe seguir mandando."""
        self.exigir()
        if not texto.strip():
            raise ValueError('brief vacío')
        self.estado['brief'] = texto.strip()
        self.canal.agregar('orquestador', 'todos', '[brief] ' + texto.strip())
        self.guardar()
        return 'Brief cambiado.'

    def soltar_pausa_de_hilo(self):
        pausa = getattr(self, 'pausa_de_hilo', None)
        if pausa:
            self.pausa_de_hilo = None
            raise pausa

    def parar(self, error, limite=None):
        """Un límite de cuota nunca sigue de largo. En un hilo (invitados en paralelo, A/B) espera a que termine la ronda."""
        pausa = Pausa(error, limite)
        if threading.current_thread() is threading.main_thread():
            raise pausa
        self.pausa_de_hilo = pausa

    def decidir(self, accion, nota='', fondo=False):
        self.exigir()
        if self.estado['fase'] != 'compuerta':
            raise ValueError('no hay compuerta abierta (fase: {})'.format(self.estado['fase']))
        self.anunciar('cambio externo')
        etapa = self.etapa()
        sin_contestar = self.pendientes()
        if sin_contestar:
            # FALLA 47: el equipo siguió con supuestos porque aprobé sin contestar sus preguntas.
            raise ValueError('contesta primero con ruta nota "<respuesta>" (aunque sea "supón X"):\n' + '\n'.join(sin_contestar))
        self.estado['decisiones'].append({'etapa': etapa['id'], 'accion': accion, 'nota': nota})
        self.estado['desde'] = len(self.canal.lineas())  # lo pendiente se cuenta desde la última decisión
        if self.estado.pop('alerta', None) and accion == 'aprobar':
            # Seguir con permiso abre otra rebanada del mismo tamaño, no un cheque en blanco.
            self.estado['tope_actual'] = (self.usd() or 0) + self.ruta().get('tope_usd', 0)
        self.marcar(etapa)
        hay_b = (self.estado.get('versiones_b') or {}).get(etapa['id'])
        if accion == 'usar-b':
            if not hay_b:
                raise ValueError('esta etapa no tiene versión B')
            perdedor = self.quien_construyo(etapa)
            shutil.copy2(hay_b['archivo'], self.carpeta() / etapa['entregable'])
            self.anunciar('motor: Didier eligió la versión B')
            self.estado['elenco'][etapa['id']] = hay_b['quien']
            self.registrar_etapa(etapa, 0, perdedor, sufijo=':perdedor', ab_gano=0)
            nota = ('Se usa la versión B. ' + nota).strip()
            accion = 'aprobar'
        elif accion == 'aprobar' and hay_b:
            self.registrar_etapa(etapa, 0, hay_b['quien'], sufijo=':perdedor', ab_gano=0)
        if accion == 'aprobar':
            if etapa.get('reajusta') and self.estado['vueltas'].get(etapa['id']):
                try:
                    self.reajustar(etapa)
                except Pausa as pausa:
                    return self.pausar(pausa, correccion={'accion': 'aprobar', 'nota': nota})
            ab_gano = 1 if hay_b or (etapa.get('tipo') == 'ab' and self.estado.get('ab')) else None
            self.registrar_etapa(etapa, 1, self.quien_construyo(etapa), ab_gano=ab_gano)
            self.canal.agregar('orquestador', 'todos', '[{}] Aprobado. {}'.format(etapa['id'], nota).strip())
            self.estado['indice'] += 1
            self.estado['fase'] = 'lista'
            self.guardar()
            return self.avanzar()
        if accion != 'corregir' or not nota:
            raise ValueError('decide: aprobar [nota] | corregir "<nota>" [--fondo] | usar-b [nota]')
        # Sin líder (A/B, animación, render) la corrección es de quien construye: así la memoria cae en el agente correcto.
        if etapa.get('tipo') in ('ab', 'taller-ganador', 'render', 'taller') and (
                self.estado.get('ganador') or etapa.get('tipo') == 'taller' or etapa.get('retoca')):
            destino = self.quien_corrige(etapa, fondo)['agente']
        else:
            destino = ((self.estado.get('elenco') or {}).get(etapa['id']) or {}).get('agente') or etapa.get('lider') \
                or (self.estado.get('ganador') or {}).get('agente') or 'todos'
        self.canal.agregar('orquestador', destino, '[{}] Corrijan: {}'.format(etapa['id'], nota))
        try:
            if self.corregir(etapa, 'el orquestador', nota, fondo):
                self.estado.pop('reintento', None)
                if etapa.get('revisor') and etapa.get('tipo', 'platica') == 'platica':
                    # FALLA 44: la versión corregida por pedido de Didier también pasa por el revisor; lo que diga va al resumen.
                    self.revisar(etapa, self.entregable(etapa))
                elif etapa.get('revisor'):
                    self.revision_en_taller(etapa)  # M9: una hoja corregida también se vuelve a mirar
                self.revisar_libro(etapa)
        except Pausa as pausa:
            return self.pausar(pausa, correccion={'nota': nota, 'fondo': fondo})
        self.guardar()
        return self.resumen()

    def reajustar(self, etapa):
        """FALLA 46: el entregable aprobado cambió de fondo; el dueño de la etapa de la que depende lo confirma o ajusta."""
        destino = next(e for e in self.etapas() if e['id'] == etapa['reajusta'])
        lider = (self.estado.get('elenco', {}).get(destino['id']) or self.elenco(destino))['agente']
        texto = self.hablar(lider, 'cierre', '{} se aprobó con cambios de fondo. Ajusta {} para que diga lo mismo (si ya coincide, '
                            'déjalo igual). Deja el entregable completo. {}'.format(etapa['entregable'], destino['entregable'],
                                                                                   SOLO_ENTREGABLE), destino['entregable'])
        if texto:
            (self.carpeta() / destino['entregable']).write_text(texto + '\n', encoding='utf-8')
            self.anunciar(lider)

    def nota(self, texto):
        """El orquestador escribe en el canal: lo que decide fuera de una compuerta también lo sabe el equipo."""
        self.exigir()
        if not texto.strip():
            raise ValueError('nota vacía')
        self.anunciar('cambio externo')
        indice = min(self.estado['indice'], len(self.etapas()) - 1)
        self.canal.agregar('orquestador', 'todos', '[{}] {}'.format(self.etapas()[indice]['id'], texto.strip()))
        self.guardar()
        return 'Nota en el canal.'

    def abandonar(self):
        self.exigir()
        self.estado['fase'] = 'terminada'
        self.estado['abandonada'] = True
        self.guardar()
        self.canal.descartar()
        return 'Ruta abandonada.'

    # --- piezas de cada etapa -------------------------------------------------------------------

    def hablar(self, agente, papel, pedido, entregable=None):
        etapa = self.etapa()
        motivo = 'etapa «{}» de la ruta; tu papel: {}. Brief del orquestador: {}. {}'.format(
            etapa['nombre'], PAPELES[papel], self.estado['brief'], pedido)
        if papel in ('lider', 'cierre') and self.notas_oficio():
            motivo += ' Notas del oficio: ' + ' '.join(self.notas_oficio().split())
        if papel in ('lider', 'cierre'):
            motivo += self.material_de(etapa)
        hechos = self.hechos()
        if hechos:
            motivo += ' Hechos medidos (no los estimes): ' + json.dumps(hechos, ensure_ascii=False, separators=(',', ':'))
        try:
            texto = self.canal.turno(agente, motivo, (etapa.get('topes') or {}).get(papel, TOPES[papel]), 'orquestador')
        except Exception as error:  # un turno caído no tumba la ruta (salvo que sea la cuota: ver parar)
            detalle = T.plano(str(error) or type(error).__name__, 600)
            self.canal.agregar('sistema', 'todos', '[{}] {} no contestó ({})'.format(etapa['id'], agente, detalle))
            self.ultimo_error = '{} no contestó ({})'.format(agente, detalle)
            limite = T.limite_de_uso(str(error))
            if limite:
                self.parar(self.ultimo_error, limite)
            return ''
        crudo = getattr(self.canal, 'ultima_cruda', texto) or texto
        if entregable:
            # El entregable va al archivo; al canal, una línea. Pegarlo entero lo releían todos en cada turno.
            self.canal.agregar(agente, 'todos', '[{}] dejé {} ({} palabras): {}'.format(
                etapa['id'], entregable, len(crudo.split()), T.plano(crudo, 160)))
        else:
            self.canal.agregar(agente, 'todos', '[{}] {}'.format(etapa['id'], texto))
        return crudo

    def material_de(self, etapa):
        """lee: lo que la etapa usa de etapas anteriores, entero. Al canal solo va una línea y el turno no tiene herramientas."""
        partes = []
        for nombre in etapa.get('lee') or []:
            try:
                partes.append('\n\n{} (completo):\n{}'.format(nombre, recortar((self.carpeta() / nombre).read_text(encoding='utf-8'), TOPE_ENTREGABLE)))
            except OSError:
                partes.append('\n\n{}: no existe todavía.'.format(nombre))
        return ''.join(partes)

    def taller(self, agente, ajustes, instruccion, carpeta=None, chat_hasta=None):
        import _taller
        etapa = self.etapa()
        config = dict(self.canal.miembros[agente])
        config.update({k: v for k, v in ajustes.items() if k in ('modelo', 'esfuerzo')})
        sistema, _ = self.canal.prompt(agente, 'trabajas en el taller', quien_decide='orquestador')
        carpeta = Path(carpeta or self.carpeta())
        carpeta.mkdir(parents=True, exist_ok=True)
        contexto = self.canal.contexto()
        if self.notas_oficio():
            contexto += '\n\nNOTAS DEL OFICIO\n' + self.notas_oficio()
        trabajo = {'agente': agente, 'config': config, 'sistema': sistema, 'contexto': contexto,
                   'chat': '\n'.join(self.canal.lineas()[self.canal.estado.get('linea_inicio', 0):chat_hasta]),
                   'instruccion': 'Brief del orquestador: {}\n\n{}'.format(self.estado['brief'], instruccion),
                   'carpeta': carpeta, 'proyecto': self.canal.proyecto, 'tarea': self.estado['tarea'],
                   'base': self.canal.base, 'canal': self.canal.carpeta / 'CHAT.md',
                   'hechos': self.hechos(), 'verificar': self.verificar(etapa),
                   'permite': [p.format(base=self.canal.base) for p in etapa.get('permite') or []] or None,
                   'entregable': etapa.get('entregable'), 'tope': etapa.get('tope_s'), 'sin_didier': self.sin_didier(),
                   'aislado': bool(self.canal.estado.get('solo_paquete')),
                   'leer': self.para_leer(etapa)}
        if chat_hasta is not None:
            del trabajo['canal']  # a ciegas: ni el chat de esta etapa ni releer el canal
        self.canal.agregar('sistema', 'todos', '[{}] {} ({} {}) entra al taller: {}'.format(
            etapa['id'], agente, config['modelo'], config['esfuerzo'], T.plano(instruccion, 140)))
        avisos = []
        clave = 'ruta:{}:{}'.format(agente, carpeta)
        if 'canal' in trabajo:
            self.canal.espejos[clave] = carpeta / 'PLATICA.md'
        try:
            _taller.lanzar(trabajo, avisos.append).join()
        finally:
            self.canal.espejos.pop(clave, None)
        texto = T.plano(avisos[0] if avisos else 'no terminó (sin aviso)', T.TOPE_AVISO)
        quien = 'sistema' if texto.startswith('no terminó') else agente
        self.canal.agregar(quien, 'todos', '[{}] [taller] {}'.format(etapa['id'], texto))
        self.anunciar(agente, carpeta)
        if texto.startswith('no terminó'):
            limite = T.limite_de_uso(avisos[0] if avisos else '')
            if limite:
                self.parar('{} {}'.format(agente, texto), limite)
        return texto

    def para_leer(self, etapa):
        """Carpetas que el trabajador debe poder leer: la casa del oficio y lo que la etapa pida."""
        lista = []
        oficio = self.ruta().get('oficio')
        if oficio:
            lista.append(str(self.canal.base / 'equipo/oficios' / oficio))
        for carpeta in etapa.get('leer') or []:
            lista.append(carpeta.format(base=self.canal.base, carpeta=self.carpeta()))
        return lista

    def exigir_entrega(self, etapa, quien, texto, antes, entregable=None):
        """M5: un taller caído o sin entregable detiene la ruta con el error real; nunca sigue con un archivo viejo."""
        entregable = entregable or etapa.get('entregable')
        if texto.startswith('no terminó'):
            parcial = ' Lo que haya en la carpeta es parcial.' if 'más de' in texto else ''
            raise Pausa('{} {}.{}'.format(quien, texto, parcial))
        if not entregable:
            return
        archivo = self.carpeta() / entregable
        if not archivo.exists():
            raise Pausa('{} terminó sin entregar {} (dijo: {})'.format(quien, entregable, T.plano(texto, 200)))
        if huella_de(archivo) == antes and not self.estado.get('reintento'):
            raise Pausa('{} terminó sin cambiar {} (dijo: {})'.format(quien, entregable, T.plano(texto, 200)))

    def revision_en_taller(self, etapa):
        """Revisor con herramientas sobre una hoja. A ciegas: ni el chat ni la lista de lo que se corrigió (O6)."""
        hasta = self.canal.estado.get('linea_inicio', 0) if etapa.get('revisa_a_ciegas') else None
        veredicto = self.taller(etapa['revisor'], {}, etapa['revisa'], chat_hasta=hasta)
        self.estado['revisiones'][etapa['id']] = veredicto
        return veredicto

    def revisar(self, etapa, contenido, revisor=None, pedido=None, guardar=True):
        """El revisor responde PASA o CAMBIA; la respuesta queda en el estado para el resumen."""
        revisor = revisor or etapa['revisor']
        pedido = pedido or etapa['revisa']
        texto = self.hablar(revisor, 'revisor', '{} Responde "PASA" o "CAMBIA: <qué, concreto>".\n\nENTREGABLE:\n{}'.format(
            pedido, recortar(contenido, TOPE_ENTREGABLE)))
        if guardar:
            self.estado['revisiones'][etapa['id']] = T.plano(texto, 300) or 'sin revisión: no contestó; coteja tú cada cifra'
        return not cambia(texto), texto

    def aviso_libro(self):
        """El libro es copia del de ProjectA, que sigue aprendiendo: si cambió, se avisa; traer lo nuevo es a mano."""
        origen = (self.datos_oficio(self.ruta().get('oficio')) or {}).get('libro_origen')
        if not origen:
            return ''
        try:
            huella = hashlib.sha1(Path(origen['archivo']).read_bytes()).hexdigest()
        except OSError:
            return ''
        if huella == origen.get('huella'):
            return ''
        return ' Ojo: el libro de ProjectA cambió desde la copia del {}; revisa si hay algo que traer.'.format(origen.get('copiado'))

    def libro_vigente(self, etapa):
        """(texto, aviso) con solo los temas de esta etapa, para no mandar de más a otro modelo."""
        archivo = (self.datos_oficio(self.ruta().get('oficio')) or {}).get('libro_correcciones')
        if not etapa.get('libro') or not archivo:
            return '', ''
        try:
            partes = re.split(r'(?m)^## ', Path(archivo).read_text(encoding='utf-8'))
        except OSError as error:
            return '', 'no se pudo leer el libro ({})'.format(type(error).__name__)
        secciones = {parte.split('\n', 1)[0].strip(): parte for parte in partes[1:]}
        faltan = [tema for tema in etapa['libro'] if tema not in secciones]
        hallados = [secciones[tema] for tema in etapa['libro'] if tema in secciones]
        aviso = 'el libro ya no tiene: {}'.format(', '.join(faltan)) if faltan else ''
        return ('## '.join([partes[0]] + hallados) if hallados else ''), aviso

    def revisar_libro(self, etapa):
        """El libro no corrige ni detiene nada: su lista llega a la compuerta y Didier decide."""
        texto, aviso = self.libro_vigente(etapa)
        if aviso:
            self.canal.agregar('sistema', 'todos', '[{}] {}'.format(etapa['id'], aviso))
        if not texto:
            if aviso:
                self.estado.setdefault('libro', {})[etapa['id']] = {'veredicto': 'sin revisión: ' + aviso, 'archivo': None}
            return
        destino = 'LIBRO-{}.md'.format(etapa['id'])
        archivo = self.carpeta() / destino
        archivo.unlink(missing_ok=True)
        reparto = self.elenco({'id': etapa['id'] + ':libro', 'trabajo': 'verificar', 'papel': 'libro'})
        pedido = PEDIDO_LIBRO.format(texto)
        if etapa.get('tipo') == 'render':
            aviso_taller = self.taller(reparto['agente'], reparto, (
                'Mira hoja.png con Read y lee 03-SPEECH.md. Deja tu lista en {} y no toques ningún otro archivo. {}'.format(destino, pedido)))
            veredicto = archivo.read_text(encoding='utf-8') if archivo.exists() else aviso_taller
        else:
            veredicto = self.hablar(reparto['agente'], 'libro', '{}\n\nENTREGABLE:\n{}'.format(
                pedido, recortar(self.entregable(etapa), TOPE_ENTREGABLE)), destino)
            if veredicto:
                archivo.write_text(veredicto + '\n', encoding='utf-8')
                self.anunciar(reparto['agente'])
        hay_archivo = archivo.exists()
        self.estado.setdefault('libro', {})[etapa['id']] = {
            'veredicto': (veredicto or 'sin revisión').strip() + (' · ' + aviso if aviso else ''),
            'archivo': str(archivo) if hay_archivo else None}

    def entregable(self, etapa):
        archivo = self.carpeta() / etapa['entregable']
        try:
            return archivo.read_text(encoding='utf-8') if archivo.exists() else ''
        except UnicodeDecodeError:
            return '(archivo binario: {})'.format(archivo)

    def platica(self, etapa):
        antes = len(self.canal.lineas())  # la versión B no ve quién hace la A ni su plática
        reparto = self.elenco(etapa)
        lider = reparto['agente']
        archivo = self.carpeta() / etapa['entregable']
        if not self.ya_hecho(etapa, 'entregable', archivo):
            if etapa.get('abre'):
                self.hablar(lider, 'lider', etapa['abre'])
            invitados = [i for i in etapa.get('invitados', []) if i != lider]
            if etapa.get('trabajo') == 'idear' and len(invitados) > 1:
                # Idear diverge: cada invitado reacciona a la propuesta sin esperar a los demás (más rápido, menos eco).
                hilos = [threading.Thread(target=self.hablar, args=(i, 'invitado', etapa['reacciona'])) for i in invitados]
                for hilo in hilos:
                    hilo.start()
                for hilo in hilos:
                    hilo.join()
                self.soltar_pausa_de_hilo()
            else:
                for invitado in invitados:
                    self.hablar(invitado, 'invitado', etapa['reacciona'])
            if 'taller' in etapa:
                previa = huella_de(archivo)
                self.exigir_entrega(etapa, lider, self.taller(lider, reparto, etapa['taller']['instruccion']), previa)
            else:
                texto = self.hablar(lider, 'cierre', etapa['cierra'] + ' ' + SOLO_ENTREGABLE, etapa['entregable'])
                if not texto.strip():
                    # Un líder caído no deja un entregable vacío que parezca bueno.
                    raise Pausa(getattr(self, 'ultimo_error', '') or '{} no entregó {}'.format(lider, etapa['entregable']))
                archivo.write_text(texto + '\n', encoding='utf-8')
                self.anunciar(lider)
            self.anotar_paso(etapa, 'entregable', archivo)
        if etapa.get('revisor') and self.toca_revisor(etapa) and not self.ya_hecho(etapa, 'revisor', archivo):
            pasa, texto = self.revisar(etapa, self.entregable(etapa))
            if not pasa:
                self.corregir(etapa, etapa['revisor'], texto)
            self.anotar_paso(etapa, 'revisor', archivo)
        self.exigir_formato(etapa)
        self.exigir_vetos(etapa)
        self.revisar_libro(etapa)
        if self.ab_pedido(etapa):
            self.version_b(etapa, antes)
        return True

    def toca_revisor(self, etapa):
        """revisor_si: el revisor solo entra si el entregable lo amerita (el de hechos cuesta ~190 mil tokens de cuota por turno)."""
        patron = etapa.get('revisor_si')
        if not patron:
            return True
        texto = self.entregable(etapa)
        # Solo lo que se dice: los números de los silencios o las notas no son afirmaciones.
        bloque = re.search(r'^\s*SPEECH:\s*$(.*?)^\s*FIN\s*$', texto, re.M | re.S)
        dicho = bloque.group(1) if bloque else texto
        # Una fecha que el paquete ya trae verificada no amerita el revisor de hechos.
        paquete = self.canal.paquete().casefold() if self.canal.estado.get('solo_paquete') else ''
        dicho = FECHA.sub(lambda m: '' if m.group(0).casefold() in paquete else m.group(0), dicho)
        return re.search(patron, dicho, re.I) is not None

    def exigir_formato(self, etapa):
        """M12: lo que otras etapas necesitan leer del entregable. Una vuelta para arreglarlo; si no, se detiene."""
        faltan = lambda: [p for p in etapa.get('exige', []) if not re.search(p, self.entregable(etapa), re.M)]
        if faltan():
            self.corregir(etapa, 'el motor', 'al entregable le falta lo que se pidió, con este formato exacto: ' + ', '.join(faltan()))
        if faltan():
            raise Pausa('{} sigue sin el formato pedido: {}'.format(etapa.get('entregable'), ', '.join(faltan())))

    def mundos(self):
        try:
            return (self.canal.base / 'equipo/oficios' / self.oficio() / 'MUNDOS.md').read_text(encoding='utf-8')
        except OSError:
            return ''

    def repeticiones(self, etapa):
        return _mundos.revisar(etapa['vetos'], self.entregable(etapa), self.mundos()) if etapa.get('vetos') else []

    def exigir_vetos(self, etapa):
        """Lo que Didier vetó y los mundos ya usados, por máquina: una vuelta para cambiarlo. Puede ser falso positivo,
        así que lo que quede no detiene: el orquestador lo ve en la siguiente compuerta."""
        hallados = self.repeticiones(etapa)
        if hallados:
            self.corregir(etapa, 'el motor', 'la revisión por máquina encontró: {}. Si es algo prohibido o ya usado, cámbialo por algo de otra '
                          'familia de material y objeto, no un sinónimo, y ajusta lo que dependa de eso. Si es una fecha, corrígela con el '
                          'calendario real, también donde salga en la imagen'.format('; '.join(hallados)))
            hallados = self.repeticiones(etapa)
        if hallados:
            self.canal.agregar('sistema', 'todos', '[{}] alerta de repetición o hecho: {}'.format(etapa['id'], '; '.join(hallados)))

    def version_b(self, etapa, chat_hasta):
        """A/B que pidió Didier: otro agente hace la misma etapa a ciegas, en ab/<etapa>/. Él escoge en la compuerta."""
        alterno = self.alternativa(etapa)
        carpeta = self.carpeta() / 'ab' / etapa['id']
        if 'instruccion' in etapa:
            pedido = etapa['instruccion'].format(carpeta=self.carpeta(), correccion='')
        else:
            pedido = etapa['taller']['instruccion'] if 'taller' in etapa else '{} {}'.format(etapa['abre'], etapa['cierra'])
        config = dict(self.canal.miembros[alterno['agente']])
        quien = {'agente': alterno['agente'], 'modelo': alterno.get('modelo') or config.get('modelo'),
                 'esfuerzo': alterno.get('esfuerzo') or config.get('esfuerzo'), 'motivo': 'versión B del A/B que pidió Didier'}
        self.taller(quien['agente'], quien, 'Haces la versión B de esta etapa, a ciegas. Los archivos del proyecto están en {}: '
                    'léelos de ahí, pero no abras {} ni ab/. Deja tu versión como {} en tu carpeta actual. {}'.format(
                        self.carpeta(), etapa['entregable'], etapa['entregable'], pedido), carpeta, chat_hasta=chat_hasta)
        archivo = carpeta / etapa['entregable']
        if not archivo.exists():
            self.canal.agregar('sistema', 'todos', '[{}] la versión B no se entregó; sigue la A'.format(etapa['id']))
            return
        revision = ''
        if etapa.get('revisor'):
            _, texto = self.revisar(etapa, archivo.read_text(encoding='utf-8'), guardar=False)
            revision = T.plano(texto, 300)
        self.estado.setdefault('versiones_b', {})[etapa['id']] = {'archivo': str(archivo), 'quien': quien, 'revisor': revision}

    def correccion_fallida(self, etapa, motivo):
        """M7: la vuelta no cambió nada: se dice, y no se regenera la hoja vieja como si estuviera corregida."""
        self.estado.setdefault('correcciones_fallidas', {})[etapa['id']] = motivo
        self.canal.agregar('sistema', 'todos', '[{}] la corrección no se aplicó: {}'.format(etapa['id'], T.plano(motivo, 300)))
        return False

    def corregir(self, etapa, quien, nota, fondo=False):
        """Una vuelta de corrección por cada pedido (del revisor o del orquestador). True si de verdad cambió algo."""
        self.estado['vueltas'][etapa['id']] = self.estado['vueltas'].get(etapa['id'], 0) + 1
        (self.estado.get('correcciones_fallidas') or {}).pop(etapa['id'], None)
        tipo = etapa.get('tipo', 'platica')
        if tipo == 'platica' and 'taller' not in etapa:
            lider = self.elenco(etapa)['agente']
            actual = self.entregable(etapa)
            texto = self.hablar(lider, 'cierre', '{} pidió: {}. Corrige sobre el entregable actual y déjalo completo otra vez; lo que no se '
                                'pidió cambiar se queda igual. {}{}'.format(quien, nota, SOLO_ENTREGABLE, '\n\nENTREGABLE ACTUAL:\n' + recortar(
                                    actual, TOPE_ENTREGABLE) if actual.strip() else ''), etapa['entregable'])
            if not texto:
                return self.correccion_fallida(etapa, getattr(self, 'ultimo_error', '') or lider + ' no contestó')
            (self.carpeta() / etapa['entregable']).write_text(texto + '\n', encoding='utf-8')
            self.anunciar(lider)
        elif tipo == 'platica':
            antes = huella_de(self.carpeta() / etapa['entregable'])
            texto = self.taller(self.elenco(etapa)['agente'], self.elenco(etapa),
                                'Corrige {} según {}: {}. No cambies lo demás.'.format(etapa['entregable'], quien, nota))
            if texto.startswith('no terminó') or huella_de(self.carpeta() / etapa['entregable']) == antes:
                return self.correccion_fallida(etapa, texto)
        elif tipo in ('ab', 'taller-ganador', 'render', 'taller'):
            entregable = etapa.get('entregable', 'reel.html')
            quien_corrige = self.quien_corrige(etapa, fondo)
            antes = huella_de(self.carpeta() / entregable)
            hoja = etapa.get('hoja', 'hoja.png' if tipo == 'render' else None)
            instruccion = 'Corrige {} según {}: {}. No cambies lo demás.'.format(entregable, quien, nota)
            if self.estado.get('reintento'):
                instruccion += ' ' + self.nota_de_reintento()
            if hoja and (self.carpeta() / hoja).exists():
                # El retoque a ciegas rompió cosas (B, ronda 1 de Sonnet): ve lo que hay, corrige y vuelve a mirar.
                instruccion += (' Antes de tocar nada abre {} con Read para ver lo que hoy se ve; al terminar corre tu comando '
                                'de prueba y mira su imagen.').format(hoja)
            texto = self.taller(quien_corrige['agente'], quien_corrige, instruccion)
            if texto.startswith('no terminó'):
                return self.correccion_fallida(etapa, texto)
            if huella_de(self.carpeta() / entregable) == antes:
                return self.correccion_fallida(etapa, '{} no cambió {} ({})'.format(quien_corrige['agente'], entregable, T.plano(texto, 200)))
            if tipo == 'ab':
                self.correr(etapa.get('hoja_herramienta', 'hoja'), html=self.carpeta() / entregable,
                            png=self.carpeta() / etapa.get('hoja', 'hoja-cuadros.png'))
            elif tipo == 'render':
                self.render_y_hoja()
        elif tipo == 'script':
            self.etapa_script(etapa)  # p. ej. llegó otra voz: se vuelve a preparar
        else:
            raise ValueError('esta etapa no se corrige')
        return True

    def faltantes(self, etapa):
        return [a for a in etapa.get('archivos', []) if not (self.carpeta() / a).exists()]

    def espera(self, etapa):
        return not self.faltantes(etapa)

    def correr(self, herramienta, **valores):
        comando = [p.format(base=self.canal.base, **{k: str(v) for k, v in valores.items()})
                   for p in self.ruta()['herramientas'][herramienta]]
        try:
            salida = subprocess.run(comando, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                    timeout=ESPERAS.get(herramienta, ESPERA_SCRIPT))
        except subprocess.TimeoutExpired:
            raise Pausa('{} no terminó en {} s'.format(herramienta, ESPERAS.get(herramienta, ESPERA_SCRIPT)))
        if salida.returncode:
            raise Pausa('{} falló: {}'.format(herramienta, T.plano(salida.stderr or salida.stdout, 800)))
        self.anunciar('script ' + herramienta)
        return salida.stdout

    def hoja(self, html, png):
        return self.correr('hoja', html=html, png=png)

    def ab(self, etapa):
        """Sin A/B pedido, construye el del reparto fijo. Con A/B: dos a ciegas; el juez solo ve A y B."""
        if not self.ab_pedido(etapa):
            return self.un_constructor(etapa)
        constructores = list(etapa['constructores'])
        random.shuffle(constructores)
        etiquetas = dict(zip('AB', constructores))
        base = self.carpeta() / 'ab'
        instruccion = etapa['instruccion'].format(carpeta=self.carpeta(), base=self.canal.base)
        entregable = etapa.get('entregable', 'reel.html')
        herramienta_hoja = etapa.get('hoja_herramienta', 'hoja')
        antes = len(self.canal.lineas())  # el juez no ve quién construyó qué
        hilos = [threading.Thread(target=self.taller, args=(c['agente'], c, instruccion, base / e))
                 for e, c in etiquetas.items()]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join()
        self.soltar_pausa_de_hilo()
        for e in etiquetas:
            try:
                self.correr(herramienta_hoja, html=base / e / entregable, png=base / ('hoja' + e + '.png'))
            except (ValueError, subprocess.SubprocessError) as error:
                self.canal.agregar('sistema', 'todos', '[{}] la hoja de {} falló: {}'.format(etapa['id'], e, error))
        veredicto = self.taller(etapa['juez'], {}, etapa['juzga'], base, chat_hasta=antes)
        elegido = re.search(r'GANA\s*([AB])', veredicto.upper())
        gana = elegido.group(1) if elegido else None
        if gana is None or not (base / gana / entregable).exists():
            # Sin veredicto claro gana el que sí entregó; si los dos, A (queda anotado).
            gana = next((e for e in 'AB' if (base / e / entregable).exists()), None)
        if gana is None:
            raise Pausa('ningún constructor entregó ' + entregable)
        self.estado['ab'] = {'A': etiquetas['A'], 'B': etiquetas['B'], 'gana': gana,
                             'juez': recortar(' '.join(veredicto.split()), 1500), 'claro': bool(elegido)}
        self.estado['ganador'] = etiquetas[gana]
        perdedor = etiquetas['B' if gana == 'A' else 'A']
        self.registrar_etapa(etapa, 0, dict(perdedor, motivo='A/B a ciegas'), sufijo=':perdedor', ab_gano=0)
        shutil.copy2(base / gana / entregable, self.carpeta() / entregable)
        self.anunciar('motor: el ganador del A/B')
        self.correr(herramienta_hoja, html=self.carpeta() / entregable,
                    png=self.carpeta() / etapa.get('hoja', 'hoja-cuadros.png'))
        return True

    def un_constructor(self, etapa):
        reparto = self.elenco(etapa)
        entregable = etapa.get('entregable', 'reel.html')
        archivo = self.carpeta() / entregable
        if not self.ya_hecho(etapa, 'entregable', archivo):
            previa = huella_de(archivo)
            instruccion = etapa['instruccion'].format(carpeta=self.carpeta(), base=self.canal.base)
            texto = self.taller(reparto['agente'], reparto, (instruccion + ' ' + self.nota_de_reintento()).strip())
            self.exigir_entrega(etapa, reparto['agente'], texto, previa, entregable)
            self.estado['ganador'] = reparto
            self.correr(etapa.get('hoja_herramienta', 'hoja'), html=archivo, png=self.carpeta() / etapa.get('hoja', 'hoja-cuadros.png'))
            self.anotar_paso(etapa, 'entregable', archivo)
        if etapa.get('revisor'):  # solo reporta: lo que proponga se decide en la compuerta
            self.revision_en_taller(etapa)
        return True

    def etapa_taller(self, etapa):
        """Una pieza que se construye sin plática: la hace quien diga el elenco, con su verificar."""
        antes = len(self.canal.lineas())
        reparto = self.elenco(etapa)
        archivo = self.carpeta() / etapa['entregable'] if etapa.get('entregable') else None
        if not (archivo and self.ya_hecho(etapa, 'entregable', archivo)):
            previa = huella_de(archivo) if archivo else None
            texto = self.taller(reparto['agente'], reparto, etapa['instruccion'].format(
                carpeta=self.carpeta(), base=self.canal.base, correccion=self.nota_de_reintento()))
            self.exigir_entrega(etapa, reparto['agente'], texto, previa)
            if archivo:
                self.anotar_paso(etapa, 'entregable', archivo)
        self.exigir_formato(etapa)  # M12: lo que otra etapa va a leer se pide aquí, no hasta el final de la ruta
        self.exigir_vetos(etapa)
        if self.ab_pedido(etapa):
            self.version_b(etapa, antes)
        return True

    def taller_ganador(self, etapa):
        ganador = self.estado['ganador']
        previa = huella_de(self.carpeta() / etapa['entregable']) if etapa.get('entregable') else None
        texto = self.taller(ganador['agente'], ganador, etapa['instruccion'].format(correccion=self.nota_de_reintento()))
        self.exigir_entrega(etapa, ganador['agente'], texto, previa)
        return True

    def nota_de_reintento(self):
        if not self.estado.get('reintento'):
            return ''
        return 'Un intento anterior se cortó ({}): si ya hay archivos en tu carpeta, sigue sobre ellos en vez de empezar de cero.'.format(
            T.plano(self.estado['reintento'], 160))

    def etapa_script(self, etapa):
        """Una pieza sin modelo: la hace un script de herramientas; si falla, la ruta se detiene con su error."""
        self.correr(etapa['herramienta'], carpeta=self.carpeta())
        if etapa.get('entregable') and not (self.carpeta() / etapa['entregable']).exists():
            raise Pausa('{} no dejó {}'.format(etapa['herramienta'], etapa['entregable']))
        return True

    def render_y_hoja(self):
        c = self.carpeta()
        audio = c / 'audio-final.wav' if (c / 'audio-final.wav').exists() else c / 'voz.wav'  # con ruiditos, si la ruta los hace
        self.correr('render', html=c / 'reel.html', audio=audio, mp4=c / 'reel.mp4')
        if 'comprimir' in self.ruta()['herramientas']:
            # El render sale en crf 18 (~1 MB por segundo); para redes basta una copia en crf 23 (FALLA 22).
            try:
                self.correr('comprimir', mp4=c / 'reel.mp4', ligero=c / 'reel-redes.mp4')
                self.estado.pop('sin_copia', None)
            except Pausa as error:  # el video ya está: sin copia ligera se entrega el pesado, no se detiene
                (c / 'reel-redes.mp4').unlink(missing_ok=True)
                self.estado['sin_copia'] = T.plano(str(error), 300)
                self.canal.agregar('sistema', 'todos', '[render] sin copia ligera: {}'.format(self.estado['sin_copia']))
        self.hoja(c / 'reel.html', c / 'hoja.png')
        if self.etapa().get('medir') and 'medir_camara' in self.ruta()['herramientas']:
            (c / 'CAMARA.md').unlink(missing_ok=True)  # si esta medida falla, no se muestra la del render anterior
            try:
                self.correr('medir_camara', html=c / 'reel.html', salida=c / 'CAMARA.md')
            except Pausa as error:  # medir ayuda a juzgar el movimiento, pero no detiene un video ya hecho
                self.canal.agregar('sistema', 'todos', '[render] no se pudo medir la cámara: {}'.format(error))

    def render(self, etapa):
        html = self.carpeta() / 'reel.html'
        if not self.ya_hecho(etapa, 'render', html):
            self.render_y_hoja()
            self.anotar_paso(etapa, 'render', html)
        if etapa.get('revisor') and not self.ya_hecho(etapa, 'revisor', html):
            # M10: el CAMBIA del revisor no va solo al retoque. Se decide en la compuerta: concreto, retoque; de fondo, --fondo.
            self.revision_en_taller(etapa)
            self.anotar_paso(etapa, 'revisor', html)
        self.revisar_libro(etapa)
        return True

    def leccion(self, etapa):
        texto = T.plano(self.hablar(etapa['lider'], 'invitado', etapa['abre']), 250)
        if texto:
            memoria = self.canal.base / 'equipo' / etapa['lider'] / 'MEMORIA.md'
            with memoria.open('a', encoding='utf-8') as stream:
                stream.write('- {} | aprendizaje | ruta | {}\n'.format(dt.date.today().isoformat(), texto))
        return True

    # --- lo que lee el orquestador ---------------------------------------------------------------

    def resumen(self):
        etapa, c = self.etapa(), self.carpeta()
        lineas = ['Compuerta «{}» · etapa {}/{} «{}» · paradas: {}'.format(
            etapa.get('compuerta') or ('A/B' if self.ab_pedido(etapa) else 'tope de gasto'), self.estado['indice'] + 1, len(self.etapas()), etapa['nombre'], self.estado['paradas'])]
        if etapa.get('entregable') and (c / etapa['entregable']).exists():
            lineas.append('Entregable ({}):\n{}'.format(c / etapa['entregable'], recortar(self.entregable(etapa), 900).rstrip()))
        if etapa.get('muestra_hechos'):
            hechos = self.hechos() or {}
            lineas.append('Hechos: ' + ' · '.join('{}: {}'.format(n, hechos.get(n)) for n in etapa['muestra_hechos']))
        for previa in self.etapas()[:self.estado['indice'] + 1]:
            repetido = self.repeticiones(previa)
            if repetido:
                lineas.append('ALERTA de repetición o hecho en {} (revisión por palabras: puede ser falsa): {}'.format(previa['entregable'], '; '.join(repetido)))
        if self.estado['revisiones'].get(etapa['id']):
            corregido = ' (ya se corrigió)' if self.estado['vueltas'].get(etapa['id']) else ''
            lineas.append('Revisor{}: {}'.format(corregido, self.estado['revisiones'][etapa['id']]))
        fallida = (self.estado.get('correcciones_fallidas') or {}).get(etapa['id'])
        if fallida:
            lineas.append('ALERTA: la última corrección NO se aplicó y la hoja sigue siendo la anterior: ' + fallida)
        libro = (self.estado.get('libro') or {}).get(etapa['id'])
        if libro:
            lineas.append('Libro de correcciones (solo reporta; decide {}){}:\n{}'.format(
                'el orquestador' if self.sin_didier() else 'Didier',
                ' · ' + libro['archivo'] if libro['archivo'] else '', recortar(libro['veredicto'], 1500)))
        if etapa.get('tipo') == 'ab' and self.estado['ab']:
            ab = self.estado['ab']
            lineas.append('A/B: A={} {}, B={} {}; gana {}{}. Juez: {}'.format(
                ab['A']['modelo'], ab['A']['esfuerzo'], ab['B']['modelo'], ab['B']['esfuerzo'], ab['gana'],
                '' if ab['claro'] else ' (sin veredicto claro)', ab['juez']))
            lineas.append('Lo que el juez le corregiría al ganador va completo arriba: decide qué se toma.')
            lineas.append('Hojas: {} · {} · elegida: {}'.format(c / 'ab/hojaA.png', c / 'ab/hojaB.png',
                                                                 c / etapa.get('hoja', 'hoja-cuadros.png')))
        version_b = (self.estado.get('versiones_b') or {}).get(etapa['id'])
        if version_b:
            lineas.append('A/B: el entregable de arriba es la A. Versión B: {}{}'.format(
                version_b['archivo'], ' · Revisor de la B: ' + version_b['revisor'] if version_b['revisor'] else ''))
            lineas.append('Para quedarte con la B: ruta decidir --proyecto {} usar-b'.format(self.canal.proyecto.name))
        if etapa.get('tipo') == 'ab' and not self.estado['ab']:
            lineas.append('Hoja: {}'.format(c / etapa.get('hoja', 'hoja-cuadros.png')))
        if etapa.get('tipo') == 'render':
            lineas.append('Video: {} · copia ligera: {} · hoja: {}'.format(
                c / 'reel.mp4', c / 'reel-redes.mp4' if not self.estado.get('sin_copia') else 'NO SALIÓ (' + self.estado['sin_copia'] + ')', c / 'hoja.png'))
            if (c / 'CAMARA.md').exists():
                lineas.append('Cámara medida:\n' + recortar((c / 'CAMARA.md').read_text(encoding='utf-8').strip(), 700))
            elif etapa.get('medir'):
                lineas.append('Cámara: no se pudo medir en este render; no hay CAMARA.md.')
        if self.estado['vueltas'].get(etapa['id']):
            lineas.append('Vueltas de corrección: {}'.format(self.estado['vueltas'][etapa['id']]))
        if self.estado.get('alerta'):
            lineas.append('ALERTA: ' + self.estado['alerta'])
        reparto = self.quien_construyo(etapa)
        if reparto:
            lineas.append('Elenco: {} {} · {}'.format(reparto['agente'], reparto.get('esfuerzo') or '', reparto.get('motivo')))
        usd = self.usd()
        if usd is not None:
            lineas.append('Gasto Claude hasta aquí: {:.2f} USD (Gemini y Codex van por cuota)'.format(usd))
        lineas.extend(self.pendientes())
        if self.estado['paradas'] == 'didier':
            lineas.append('Paradas: didier → platícale a Didier a nivel general y espera su sí antes de decidir.')
        lineas.append('Decide: ruta decidir --proyecto {} aprobar | corregir "<nota>"{}'.format(
            self.canal.proyecto.name, ' [--fondo]' if etapa.get('retoca') else ''))
        return '\n'.join(lineas)

    def procesar_marcador(self):
        herramienta = T.marcador_sh()
        if herramienta:
            try:
                subprocess.run(['/bin/bash', str(herramienta), 'procesar'], stdin=subprocess.DEVNULL,
                               capture_output=True, timeout=60)
            except (OSError, subprocess.SubprocessError):
                pass  # el gasto sale con lo que ya esté procesado

    def usd(self):
        self.procesar_marcador()  # sin esto la compuerta lee la base antes de que llegue la cola (salía 0.00)
        db = self.canal.base / 'registro/marcador.db'
        if not db.exists():
            return None
        try:
            with sqlite3.connect('file:{}?mode=ro'.format(db), uri=True) as con:
                return con.execute('SELECT coalesce(sum(usd),0) FROM _costos_usd WHERE tarea=?',
                                   (self.estado['tarea'],)).fetchone()[0]
        except sqlite3.Error:
            return None

    def pendientes(self):
        """Preguntas al orquestador o a Didier desde la última decisión: nadie más las contesta."""
        preguntas = []
        for linea in self.canal.lineas()[self.estado.get('desde', 0):]:
            m = re.match(r'^\S+ (\S+?)→\S+?: (.*)$', linea)
            if m and m.group(1) == 'orquestador':
                preguntas = []  # lo que el orquestador escribió después contesta lo anterior
                continue
            if m and m.group(1) in self.canal.miembros and PREGUNTA.search(m.group(2)) \
                    and not re.match(r'^\[[\w-]+\] dejé ', m.group(2)):  # el resumen de un entregable no es pregunta
                preguntas.append('  - {}: {}'.format(m.group(1), T.plano(m.group(2), 300)))
        preguntas = list(dict.fromkeys(preguntas))  # la misma pregunta repetida cuenta una vez
        if not preguntas:
            return []
        return ['Preguntas sin contestar ({}; contesta con: ruta nota "<respuesta>"):'.format(len(preguntas))] + preguntas

    def gasto(self):
        self.procesar_marcador()
        db = self.canal.base / 'registro/marcador.db'
        if not db.exists():
            return 'Gasto: sin marcador.'
        with sqlite3.connect('file:{}?mode=ro'.format(db), uri=True) as con:
            filas = con.execute(
                "SELECT origen, coalesce(agente,'?'), count(*), sum(coalesce(tokens_entrada,0)+coalesce(tokens_cache_escrita,0)),"
                " sum(coalesce(tokens_cache_leida,0)), sum(coalesce(tokens_salida,0)+coalesce(tokens_razonamiento,0))"
                " FROM corridas WHERE tarea=? GROUP BY 1, 2 ORDER BY 1, 2", (self.estado['tarea'],)).fetchall()
        return 'Gasto (tarea {}):\n'.format(self.estado['tarea']) + '\n'.join(
            '  {} {}: {} turnos, {} nuevos, {} caché, {} salida'.format(*f) for f in filas)


def ver_estado(proyecto):
    """Dónde va la ruta y qué sigue, sin abrir el canal: mirar no gasta, no cierra pláticas ni dispara calificador."""
    nombre = Path(proyecto).name
    try:
        e = json.loads((T.raiz() / 'registro/canales' / nombre / 'ruta.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return 'No hay ruta en {}. Sigue: ruta iniciar'.format(nombre)
    rutas = T.raiz() / 'equipo/rutas'
    archivo = rutas / (e['ruta'] + '.json')
    if not archivo.exists():
        archivo = rutas / 'archivo' / (e['ruta'] + '.json')
    etapas = json.loads(archivo.read_text(encoding='utf-8'))['etapas']
    etapa = etapas[min(e['indice'], len(etapas) - 1)]
    fase = e['fase']
    if fase == 'terminada':
        sigue = 'Nada que retomar: terminó o se archivó.'
    elif fase == 'compuerta':
        sigue = 'Compuerta abierta: ruta avanzar reimprime el resumen, sin gastar; luego ruta decidir.'
    elif fase == 'espera':
        sigue = 'Faltan archivos: ruta avanzar reimprime qué falta y el texto para Didier, sin gastar.'
    elif fase == 'pausada':
        sigue = 'Pausada: {}. Arregla la causa y ruta reintentar.'.format(T.plano((e.get('pausa') or {}).get('error') or '', 300))
    elif fase == 'trabajando' and e.get('pid') != os.getpid() and proceso_vivo(e.get('pid')):
        sigue = 'Sigue corriendo (proceso {}): no lances otro; espera a que termine.'.format(e.get('pid'))
    elif fase == 'trabajando':
        sigue = 'El proceso se cortó a media etapa: ruta reintentar sigue sobre lo que dejó.'
    else:
        sigue = 'Sigue: ruta avanzar'
    return 'Ruta {} · etapa {}/{} «{}» · fase {} · carpeta {}\n{}'.format(
        e['ruta'], e['indice'] + 1, len(etapas), etapa['nombre'], fase, e['carpeta'], sigue)


def archivar(proyecto, nota=''):
    """Cierra una ruta abierta sin borrar nada y sin abrir el canal (no dispara calificador ni gasta): su estado se copia a
    registro/canales/<proyecto>/rutas-archivadas/ y la fase queda 'terminada', para que `ruta iniciar` pueda arrancar otra."""
    carpeta = T.raiz() / 'registro/canales' / Path(proyecto).name
    archivo = carpeta / 'ruta.json'
    try:
        estado = json.loads(archivo.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return 'No hay ruta abierta en ' + Path(proyecto).name
    if estado.get('fase') == 'terminada':
        return 'La ruta {} (tarea {}) ya estaba terminada.'.format(estado.get('ruta'), estado.get('tarea'))
    copias = carpeta / 'rutas-archivadas'
    copias.mkdir(parents=True, exist_ok=True)
    shutil.copy2(archivo, copias / 'tarea-{}.json'.format(estado.get('tarea')))
    estado.update(fase='terminada', archivada=True, fin=time.time(), nota_archivo=nota)
    temporal = archivo.with_suffix('.tmp')
    temporal.write_text(json.dumps(estado, ensure_ascii=False, indent=1), encoding='utf-8')
    temporal.replace(archivo)
    # Su plática queda cerrada aquí: si no, el siguiente `ruta iniciar` la mandaría al marcador como un caso calificable.
    charla = carpeta / 'estado.json'
    try:
        canal = json.loads(charla.read_text(encoding='utf-8'))
        if canal.get('tarea') == estado.get('tarea') and not canal.get('cerrada'):
            canal['cerrada'] = True
            temporal = charla.with_suffix('.tmp')
            temporal.write_text(json.dumps(canal, ensure_ascii=False), encoding='utf-8')
            temporal.replace(charla)
    except (OSError, ValueError):
        pass  # sin plática que cerrar
    return 'Ruta {} (tarea {}) archivada; sus archivos siguen en {}. Copia del estado: {}'.format(
        estado.get('ruta'), estado.get('tarea'), estado.get('carpeta'), copias / 'tarea-{}.json'.format(estado.get('tarea')))


def preflight(ping=False):
    """¿Está todo para correr un video? Sin modelos, salvo --ping (un «ok» de Sonnet para ver si hay cuota)."""
    base = T.raiz()
    render = Path(os.environ.get('HUMANIFY_RENDER_DIR', str(base / 'render')))
    revisar = [('puppeteer', render / 'node_modules/puppeteer'),
               ('ffmpeg', render / 'node_modules/ffmpeg-static/ffmpeg'),
               ('three.js', base / 'vendor/three-0.159.0.min.js'),
               ('whisper en caché', Path.home() / '.cache/huggingface/hub/models--mobiuslabsgmbh--faster-whisper-large-v3-turbo')]
    lineas = ['{}: {}'.format(nombre, 'ok' if ruta.exists() else 'FALTA ' + str(ruta)) for nombre, ruta in revisar]
    for programa in ('node', 'claude', 'agy', 'codex'):
        lineas.append('{}: {}'.format(programa, 'ok' if shutil.which(programa) else 'FALTA en el PATH'))
    lineas.append('uv: {}'.format('ok' if shutil.which('uv') else 'no está en el PATH; la voz usa python3 -m uv'))
    env = {k: v for k, v in os.environ.items() if k != 'CLAUDECODE' and not k.startswith('CLAUDE_CODE_')}
    if shutil.which('claude'):
        try:
            salida = subprocess.run(['claude', 'auth', 'status'], env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
            datos = json.loads(salida.stdout)
            # Sin correo ni organización: solo si hay sesión y de qué plan, para saber qué cuenta gasta el motor.
            lineas.append('cuenta de la terminal: sesión {}, plan {}'.format(
                'sí' if datos.get('loggedIn') else 'NO', datos.get('subscriptionType') or '?'))
        except (OSError, ValueError, subprocess.SubprocessError):
            lineas.append('cuenta de la terminal: no se pudo leer')
    if ping and shutil.which('claude'):
        try:
            salida = subprocess.run(['claude', '-p', '--safe-mode', '--no-session-persistence', '--tools', '', '--model', 'sonnet',
                                     '--effort', 'low', '--output-format', 'json', 'responde solo: ok'],
                                    env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
            # Solo si falló: el JSON de un «ok» puede traer un 429 suelto, como una duración.
            try:
                fallo = bool(json.loads(salida.stdout).get('is_error'))
            except (ValueError, AttributeError):
                fallo = False
            limite = T.limite_de_uso(salida.stdout + salida.stderr) if salida.returncode or fallo else None
            if limite and limite.get('reinicio'):
                lineas.append('cuota: SIN CUOTA, se libera a las ' + dt.datetime.fromtimestamp(limite['reinicio']).strftime('%H:%M'))
            elif limite:
                lineas.append('cuota: SIN CUOTA, sin hora de reinicio')
            else:
                lineas.append('cuota: ok' if salida.returncode == 0 else 'cuota: error ' + T.plano(salida.stderr or salida.stdout, 200))
        except (OSError, subprocess.SubprocessError) as error:
            lineas.append('cuota: no se pudo probar ({})'.format(type(error).__name__))
    return '\n'.join(lineas)


def recortar(texto, tope):
    """Invariante: nada se corta en silencio. Si hay que recortar, el que lee sabe que falta algo."""
    if len(texto) <= tope:
        return texto
    return texto[:tope] + ' …[cortado: {} de {} caracteres]'.format(tope, len(texto))


def cambia(texto):
    return re.search(r'\bCAMBIA\b', texto.upper()) is not None


def main(argv=None):
    parser = argparse.ArgumentParser(prog='humanify.sh ruta', description=__doc__)
    sub = parser.add_subparsers(dest='comando', required=True)
    i = sub.add_parser('iniciar')
    i.add_argument('--proyecto', required=True)
    i.add_argument('--ruta', required=True)
    i.add_argument('--carpeta', required=True)
    i.add_argument('--brief', required=True)
    i.add_argument('--paradas', choices=PARADAS, default='ninguna')
    i.add_argument('--ab', default='', help='etapas con A/B, solo si Didier lo pidió (ej. escenas,estilo)')
    i.add_argument('--hechos', default='{}', help='hechos que da el orquestador, JSON (ej. {"duracion_objetivo_s": 35})')
    i.add_argument('--con', default='', help='etapas opcionales que se prenden (ej. imagenes), solo con el sí de Didier')
    for nombre in ('avanzar', 'estado', 'abandonar', 'reintentar'):
        sub.add_parser(nombre).add_argument('--proyecto', required=True)
    b = sub.add_parser('brief')
    b.add_argument('--proyecto', required=True)
    b.add_argument('texto')
    arch = sub.add_parser('archivar')
    arch.add_argument('--proyecto', required=True)
    arch.add_argument('nota', nargs='?', default='')
    pre = sub.add_parser('preflight')
    pre.add_argument('--ping', action='store_true', help='gasta un «ok» de Sonnet para ver si hay cuota')
    r = sub.add_parser('retro')
    r.add_argument('--proyecto', required=True)
    r.add_argument('--contra', type=int)
    n = sub.add_parser('nota')
    n.add_argument('--proyecto', required=True)
    n.add_argument('texto')
    d = sub.add_parser('decidir')
    d.add_argument('--proyecto', required=True)
    d.add_argument('accion', choices=('aprobar', 'corregir', 'usar-b'))
    d.add_argument('nota', nargs='?', default='')
    d.add_argument('--fondo', action='store_true', help='la corrección es de fondo: no va al retoque sino a quien construye')
    args = parser.parse_args(argv)
    try:
        if args.comando == 'preflight':
            print(preflight(args.ping))
            return 0
        proyecto = T.proyecto_por_nombre(args.proyecto)
        if proyecto is None:
            raise ValueError('proyecto no encontrado: ' + args.proyecto)
        if args.comando == 'archivar':
            print(archivar(proyecto, args.nota))
            return 0
        if args.comando == 'estado':
            print(ver_estado(proyecto))
            return 0
        conductor = Conductor(proyecto)
        if args.comando == 'iniciar':
            print(conductor.iniciar(args.ruta, args.carpeta, args.brief, args.paradas, json.loads(args.hechos),
                                    [e.strip() for e in args.ab.split(',') if e.strip()],
                                    [e.strip() for e in args.con.split(',') if e.strip()]))
        elif args.comando == 'avanzar':
            print(conductor.avanzar())
        elif args.comando == 'decidir':
            print(conductor.decidir(args.accion, args.nota, args.fondo))
        elif args.comando == 'reintentar':
            print(conductor.reintentar())
        elif args.comando == 'brief':
            print(conductor.cambiar_brief(args.texto))
        elif args.comando == 'retro':
            print(conductor.retro(args.contra))
        elif args.comando == 'nota':
            print(conductor.nota(args.texto))
        else:
            print(conductor.abandonar())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print('Humanify ruta: ' + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
