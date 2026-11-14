#!/usr/bin/python3
"""Vetos y mundos usados, por máquina: lee la línea PROHIBIDOS y el registro de equipo/oficios/video/MUNDOS.md y dice qué
repite un concepto o un bloque MUNDO. Es una revisión por palabras: agarra el descuido, no sustituye el juicio de quien aterriza.
Solo biblioteca estándar."""
import datetime as dt
import re
import unicodedata

EJES = ['material base', 'luz', 'paleta', 'escala', 'columna', 'camara', 'protagonista', 'salida']
NO_SE_REPITEN = ('material base', 'protagonista', 'columna')
MINIMO_DE_CAMBIO = 5
# Palabras de relleno en los ejes: compartirlas no es repetir el mundo.
VACIAS = set('sobre como desde para entre pero muy mate mates tipo cada todo toda este esta '
             'real grande grandes chico chica chicos chicas pequeno pequena video mundo objeto objetos cuadro plano toma '
             'calida calido fria frio suave dura duro'.split())


def plano(texto):
    sin_acentos = unicodedata.normalize('NFD', texto.casefold())
    return ''.join(c for c in sin_acentos if not unicodedata.combining(c))


def significativas(texto):
    return {p for p in re.findall(r'[a-z0-9]+', plano(texto)) if len(p) >= 4 and p not in VACIAS}


def prohibidos(mundos):
    hallado = re.search(r'^\W*PROHIBIDOS\W*:\W*(.+)$', mundos, re.M)
    return [p.strip(' .*`') for p in hallado.group(1).split(',') if p.strip(' .*`')] if hallado else []


def registro(mundos):
    """[{eje: valor, 'video': …, 'objetos': …}] de la tabla del registro; acepta filas sin la columna de objetos."""
    filas = []
    columnas = ['video'] + EJES + ['objetos']
    for linea in mundos.splitlines():
        if not linea.startswith('|') or re.match(r'^\|\s*(Video\b|:?-)', linea):
            continue
        filas.append(dict(zip(columnas, (c.strip() for c in linea.strip().strip('|').split('|')))))
    return filas


def bloque_mundo(texto):
    hallado = re.search(r'^\s*\**MUNDO:?\**\s*$(.*?)^\s*\**FIN-MUNDO\**\s*$', texto, re.M | re.S)
    if not hallado:
        return None
    datos = {}
    for linea in hallado.group(1).splitlines():
        if ':' in linea:
            clave, valor = linea.split(':', 1)
            datos[plano(clave).strip('-* ')] = valor.strip('* ').strip()
    return datos


def seccion_metafora(texto):
    hallada = re.search(r'LA MET[AÁ]FORA(.*?)(?=EL DATO|C[OÓ]MO LO CONTESTA|CHISTES|\Z)', texto, re.S)
    return hallada.group(1) if hallada else ''


def nombra(texto, palabra):
    return re.search(r'\b{}(?:e?s)?\b'.format(re.escape(plano(palabra))), plano(texto)) is not None


DIAS = ['lunes', 'martes', 'miercoles', 'jueves', 'viernes', 'sabado', 'domingo']
MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre']
FECHA = re.compile(r'\b({})\s+(\d{{1,2}}(?:\s*(?:,|y)\s*\d{{1,2}})*)\s+de\s+({})\b'.format('|'.join(DIAS), '|'.join(MESES)))


def fechas_mal(texto, hoy=None):
    """Hechos dentro de la imagen: un día de la semana que no cae en esa fecha (la siguiente vez que toque, o la de hace poco)."""
    hoy = hoy or dt.date.today()
    hallados = []
    for dia, numeros, mes in FECHA.findall(plano(texto)):
        for numero in re.findall(r'\d+', numeros):
            try:
                fecha = dt.date(hoy.year, MESES.index(mes) + 1, int(numero))
                if fecha < hoy - dt.timedelta(days=60):
                    fecha = fecha.replace(year=hoy.year + 1)
            except ValueError:
                hallados.append('«{} de {}» no existe'.format(numero, mes))
                continue
            if DIAS[fecha.weekday()] != dia:
                hallados.append('«{} {} de {}» está mal: cae {}'.format(dia, numero, mes, DIAS[fecha.weekday()]))
    return list(dict.fromkeys(hallados))


def revisar(tipo, texto, mundos, hoy=None):
    """tipo 'concepto': la metáfora de 01-CONCEPTO.md. tipo 'mundo': el bloque MUNDO de 05-DIRECCION.md. Más las fechas de todo el
    texto. Lista de lo que repite o está mal."""
    hallados = fechas_mal(texto, hoy)
    if mundos:
        hallados = (revisar_mundo if tipo == 'mundo' else revisar_concepto)(texto, mundos) + hallados
    return hallados


def revisar_concepto(texto, mundos):
    metafora = seccion_metafora(texto)
    if not metafora:
        return []
    hallados = ['«{}» está prohibido'.format(p) for p in prohibidos(mundos) if nombra(metafora, p)]
    tokens = significativas(metafora)
    for fila in registro(mundos):
        repetidas = tokens & significativas(fila.get('protagonista', '') + ' ' + fila.get('objetos', ''))
        if repetidas:
            hallados.append('{} ya usó: {}'.format(fila.get('video'), ', '.join(sorted(repetidas))))
    return hallados


def revisar_mundo(texto, mundos):
    mundo = bloque_mundo(texto)
    if not mundo:
        return []
    junto = ' '.join(mundo.values())
    hallados = ['«{}» está prohibido'.format(p) for p in prohibidos(mundos) if nombra(junto, p)]
    filas = registro(mundos)
    for fila in filas:
        for eje in NO_SE_REPITEN:
            repetidas = significativas(mundo.get(eje, '')) & significativas(fila.get(eje, ''))
            if repetidas:
                hallados.append('{} de {} repite: {}'.format(eje, fila.get('video'), ', '.join(sorted(repetidas))))
    if filas:
        anterior = filas[-1]
        cambios = sum(not (significativas(mundo.get(e, '')) & significativas(anterior.get(e, ''))) for e in EJES)
        if cambios < MINIMO_DE_CAMBIO:
            hallados.append('solo cambia {} de {} ejes contra {}'.format(cambios, len(EJES), anterior.get('video')))
    return hallados
