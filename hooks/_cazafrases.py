#!/usr/bin/python3
"""Cazafrases (Teams fase 2): al cerrar una plática, lo que dijo quien dirige entra solo a la MEMORIA del agente. Sin modelo."""
import datetime as dt
import os
from pathlib import Path
import re
import subprocess

TOPE_LINEAS = 40
TOPE_FRASE = 200
DIRIGE = ('didier', 'orquestador')
ESO_SI = ('eso sí', 'me encanta', 'me late', 'déjenlo igual', 'quedó bonito', 'quedó joya', 'aprobado', 'ese sí')
REGANO = ('no engancha', 'corrijan', 'tibio', 'parece', 'se ve raro', 'se ve mal', 'no me gusta', 'falta',
          'sobra', 'denle otra vuelta')
LINEA = re.compile(r'^\d\d:\d\d (\S+?)→(\S+?): (.*)$')
ETAPA = re.compile(r'^\[([\w-]+)\]\s*')


def oraciones(texto):
    return [o.strip() for o in re.split(r'(?<=[.!?])\s+', texto) if o.strip()]


def clasificar(oracion):
    baja = ' ' + oracion.lower() + ' '
    if any(p in baja for p in REGANO) or baja.startswith((' pero ', ' no ')):
        return 'regaño'
    if any(p in baja for p in ESO_SI):
        return 'eso sí'
    return None


def cazar(lineas, miembros):
    """Devuelve [(agente, tipo, clave, frase)] de las líneas de quien dirige, sin repetir."""
    hallazgos, ultimo = [], None
    for linea in lineas:
        m = LINEA.match(linea)
        if not m:
            continue
        quien, para, texto = m.group(1), m.group(2), m.group(3)
        if quien in miembros:
            ultimo = quien
            continue
        if quien not in DIRIGE or texto.startswith('/') or texto.startswith('Arranca la ruta'):
            continue
        clave, forzado = 'general', None
        etapa = ETAPA.match(texto)
        if etapa:
            clave, texto = etapa.group(1), texto[etapa.end():]
            # Un pedido de corrección es regaño entero, aunque diga "lo demás déjenlo igual".
            forzado = 'regaño' if texto.startswith('Corrijan:') else None
            texto = re.sub(r'^(Aprobado\.|Corrijan:)\s*', '', texto)
            if not texto:
                continue
        menciones = [n.lower() for n in re.findall(r'(?<![\w@])@([\w-]+)', texto) if n.lower() in miembros]
        destinos = menciones or [d for d in para.split(',') if d in miembros] or ([ultimo] if ultimo else [])
        for oracion in oraciones(re.sub(r'(?<![\w@])@[\w-]+\s*', '', texto)):
            tipo = forzado or clasificar(oracion)
            if tipo is None or len(oracion.split()) < 4:
                continue
            for agente in destinos:
                hallazgos.append((agente, tipo, clave, oracion[:TOPE_FRASE]))
    return list(dict.fromkeys(hallazgos))


def anotar(base, hallazgos, hoy=None):
    """Agrega a cada MEMORIA.md lo nuevo y la recorta al tope (se van las entradas más viejas)."""
    hoy = hoy or dt.date.today().isoformat()
    tocados = []
    for agente in dict.fromkeys(a for a, *_ in hallazgos):
        archivo = base / 'equipo' / agente / 'MEMORIA.md'
        if not archivo.is_file():
            continue
        texto = archivo.read_text(encoding='utf-8')
        cabeza, entradas = [], []
        for linea in texto.splitlines():
            (entradas if linea.startswith('- ') else cabeza).append(linea)
        cabeza = [l for l in cabeza if not l.startswith('(Vacía.')]
        nuevas = ['- {} | {} | {} | {}'.format(hoy, tipo, clave, frase) for a, tipo, clave, frase in hallazgos
                  if a == agente and not any(frase in e for e in entradas)]
        if not nuevas:
            continue
        entradas = (entradas + nuevas)[-TOPE_LINEAS:]
        while cabeza and not cabeza[-1].strip():
            cabeza.pop()
        archivo.write_text('\n'.join(cabeza + [''] + entradas) + '\n', encoding='utf-8')
        tocados.append(archivo)
    return tocados


def guardar_en_git(base, archivos, tarea):
    """Entran solos, con git: un commit que solo lleva las memorias (no arrastra lo que haya preparado)."""
    if not archivos or not (base / '.git').exists():
        return
    rutas = [str(a.relative_to(base)) for a in archivos]
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    subprocess.run(['git', 'add', '--'] + rutas, cwd=str(base), env=env, stdin=subprocess.DEVNULL,
                   capture_output=True, timeout=30)
    subprocess.run(['git', 'commit', '-m', 'Memoria del equipo: cazafrases de la tarea {}'.format(tarea), '--only', '--']
                   + rutas, cwd=str(base), env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=30)


def correr(base, lineas, miembros, tarea):
    tocados = anotar(base, cazar(lineas, miembros))
    guardar_en_git(base, tocados, tarea)
    return [a.parent.name for a in tocados]
