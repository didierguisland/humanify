#!/usr/bin/python3
"""Retrospectiva de una tarea del marcador, sin modelos ni escrituras en la base."""
import argparse
from collections import defaultdict
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import sys


RAIZ = Path(__file__).resolve().parents[1]
CUOTAS = ('google', 'chatgpt')
# Como en el marcador, razonamiento no se suma otra vez a la salida.
TOKENS = ('tokens_entrada', 'tokens_salida', 'tokens_cache_leida', 'tokens_cache_escrita')


def tokens(corridas):
    return sum(c[campo] or 0 for c in corridas for campo in TOKENS)


def celda(valor):
    if valor is None or valor == '':
        return '—'
    return str(valor).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('|', '&#124;').replace('\r', ' ').replace('\n', '<br>')


def tabla(cabeceras, filas):
    return '\n'.join('| ' + ' | '.join(celda(c) for c in fila) + ' |'
                     for fila in [cabeceras, ['---'] * len(cabeceras)] + list(filas))


def cuota(corridas):
    return {cuenta: tokens([c for c in corridas if c['cuenta'] == cuenta])
            for cuenta in CUOTAS if any(c['cuenta'] == cuenta for c in corridas)}


def dinero(corridas, usd=None):
    cobradas = [c for c in corridas if c['cuenta'] not in CUOTAS and c['usd'] is not None]
    partes = []
    if cobradas:
        partes.append('{:.2f} USD'.format(sum(c['usd'] for c in cobradas) if usd is None else usd))
    partes.extend('{} tokens (cuota {})'.format(n, cuenta) for cuenta, n in cuota(corridas).items())
    if any(c['cuenta'] not in CUOTAS and c['usd'] is None for c in corridas):
        partes.append('sin tarifa')
    return ' + '.join(partes) or '—'


def cargar(db, tarea):
    ficha = db.execute('SELECT id, descripcion FROM tareas WHERE id=?', (tarea,)).fetchone()
    if ficha is None:
        raise ValueError('La tarea {} no existe.'.format(tarea))
    corridas = db.execute(
        'SELECT * FROM _costos_usd WHERE tarea=? '
        'ORDER BY julianday(fecha) IS NULL, julianday(fecha), id', (tarea,)).fetchall()
    etapas = db.execute(
        'SELECT * FROM etapas_usd WHERE tarea=? ORDER BY julianday(fecha), id', (tarea,)).fetchall()
    grupos = defaultdict(list)
    for c in corridas:
        grupos[c['etapa'] or None].append(c)
    fichas = {e['etapa'] or None: e for e in etapas}
    # Las etapas sin corridas quedan al final; el resto sigue su primera corrida.
    orden = list(dict.fromkeys(list(grupos) + list(fichas)))
    ordenadas = []
    for nombre in orden:
        if nombre and nombre.endswith(':perdedor') and nombre[:-9] in orden:
            continue
        ordenadas.append(nombre)
        if nombre and nombre + ':perdedor' in orden:
            ordenadas.append(nombre + ':perdedor')
    filas = []
    for nombre in ordenadas:
        ejecuciones = grupos[nombre]
        e = fichas.get(nombre)
        vueltas = (e['vueltas'] or 0) if e is not None else sum(c['rol'] == 'retrabajo' for c in ejecuciones)
        # La cuenta manda: una suscripción no se presenta como gasto USD aunque
        # su agente tenga por casualidad una tarifa en la vista.
        usd = (e['usd'] - sum(c['usd'] or 0 for c in ejecuciones if c['cuenta'] in CUOTAS)
               if e is not None and nombre is not None else
               sum(c['usd'] or 0 for c in ejecuciones if c['cuenta'] not in CUOTAS))
        filas.append({'nombre': nombre or 'sin etapa', 'corridas': ejecuciones, 'ficha': e,
                      'usd': usd,
                      'vueltas': vueltas})
    minutos = db.execute(
        'SELECT (max(julianday(fecha))-min(julianday(fecha)))*1440 '
        'FROM _costos_usd WHERE tarea=?', (tarea,)).fetchone()[0] or 0
    totales = {
        'USD de Claude': sum(c['usd'] or 0 for c in corridas if c['cuenta'] not in CUOTAS),
        'Tokens cuota google': cuota(corridas).get('google', 0),
        'Tokens cuota chatgpt': cuota(corridas).get('chatgpt', 0),
        'Minutos': minutos,
        'Vueltas totales': sum(f['vueltas'] for f in filas),
    }
    return {'tarea': tarea, 'descripcion': ficha['descripcion'], 'corridas': corridas,
            'etapas': filas, 'totales': totales}


def quien(corridas, ficha=None):
    nombres = list(dict.fromkeys('{} / {}'.format(c['agente'] or '?', c['esfuerzo'] or '?') for c in corridas))
    if not nombres and ficha is not None:
        nombres.append('{} / {}'.format(ficha['agente'] or '?', ficha['esfuerzo'] or '?'))
    return ', '.join(nombres)


def resultado(ficha):
    if ficha is None:
        return None
    partes = []
    if ficha['aprobada'] is not None:
        partes.append('aprobada' if ficha['aprobada'] else 'no aprobada')
    if ficha['ab_gano'] is not None:
        partes.append('ganó el A/B' if ficha['ab_gano'] else 'perdió el A/B')
    return '; '.join(partes)


def valor_total(nombre, valor):
    if nombre == 'USD de Claude':
        return '{:.2f} USD'.format(valor)
    if nombre.startswith('Tokens cuota'):
        return '{} tokens (cuota)'.format(valor)
    return '{:.1f}'.format(valor) if nombre == 'Minutos' else str(valor)


def relacion(corrida):
    leida, salida = corrida['tokens_cache_leida'], corrida['tokens_salida']
    if leida is None or salida is None:
        return 'sin datos'
    if salida == 0:
        return '∞ (salida 0)' if leida else 'n/d (salida 0)'
    return '{:.2f}×'.format(leida / salida)


def informe(datos):
    corridas, totales = datos['corridas'], datos['totales']
    lineas = ['# Retro de tarea {}'.format(datos['tarea']), '', celda(datos['descripcion']), '',
              '## Por etapa', '']
    filas = []
    for etapa in datos['etapas']:
        e, cs = etapa['ficha'], etapa['corridas']
        trabajos = ', '.join(dict.fromkeys(c['trabajo'] for c in cs if c['trabajo']))
        turnos = e['turnos'] if e is not None and e['etapa'] else sum(c['turnos'] or 0 for c in cs)
        if not any(c['turnos'] is not None for c in cs):
            turnos = None
        cache = sum(c['tokens_cache_leida'] or 0 for c in cs)
        if not any(c['tokens_cache_leida'] is not None for c in cs):
            cache = None
        filas.append([etapa['nombre'], trabajos or (e['trabajo'] if e is not None else None),
                      quien(cs, e), e['motivo'] if e is not None else None,
                      dinero(cs, etapa['usd']), turnos, cache, etapa['vueltas'], resultado(e)])
    lineas.append(tabla(['Etapa', 'Trabajo', 'Quién (agente / esfuerzo)', 'Motivo del elenco',
                         'USD / cuota', 'Turnos del taller', 'Caché leída (tokens)', 'Vueltas', 'Resultado'], filas))
    lineas.extend(['', '## Por agente', ''])
    agentes = defaultdict(list)
    for c in corridas:
        agentes[(c['agente'], c['cuenta'])].append(c)
    lineas.append(tabla(['Agente', 'Cuenta', 'Corridas', 'USD / cuota', 'Tokens'],
                        [[a, cuenta, len(cs), dinero(cs), tokens(cs)] for (a, cuenta), cs in agentes.items()]))
    lineas.extend(['', 'Tokens = entrada + salida + caché leída + caché escrita; no se suma razonamiento otra vez.',
                   'Los campos de tokens sin registrar no se suman.', '', '## Totales', '',
                   tabla(['Métrica', 'Total'], [[n, valor_total(n, v)] for n, v in totales.items()])])
    desconocidas = sum(c['usd'] is None and c['cuenta'] not in CUOTAS for c in corridas)
    if desconocidas:
        lineas.extend(['', '{} corrida(s) sin tarifa: el total USD es parcial.'.format(desconocidas)])
    lineas.extend(['', '## Dónde se fue el dinero', ''])
    caras = sorted((e for e in datos['etapas'] if e['usd'] > 0), key=lambda e: -e['usd'])[:3]
    if caras:
        lineas.append(tabla(['Etapa', 'USD', '% del total USD'],
                            [[e['nombre'], '{:.2f}'.format(e['usd']),
                              '{:.1f}%'.format(e['usd'] / totales['USD de Claude'] * 100)] for e in caras]))
    else:
        lineas.append('Sin gasto USD registrado.')
    lineas.extend(['', 'Caché leída/salida de cada corrida del taller (con turnos registrados u origen taller).',
                   'Una relación alta indica que trabajó a ciegas.', ''])
    taller = [c for c in corridas if c['turnos'] is not None or c['origen'] == 'taller']
    if taller:
        lineas.append(tabla(['Corrida', 'Etapa', 'Quién', 'Caché leída', 'Salida', 'Relación'],
                            [[c['id'], c['etapa'] or 'sin etapa', quien([c]), c['tokens_cache_leida'],
                              c['tokens_salida'], relacion(c)] for c in taller]))
    else:
        lineas.append('Sin corridas del taller registradas.')
    return '\n'.join(lineas)


def comparar(actual, anterior):
    filas = []
    for nombre, valor in actual['totales'].items():
        base = anterior['totales'][nombre]
        diferencia = '{:+.1f}%'.format((valor - base) / base * 100) if base else ('0.0%' if not valor else 'n/d (base 0)')
        filas.append([nombre, valor_total(nombre, valor), valor_total(nombre, base), diferencia])
    return '\n\n## Comparación\n\n' + tabla(
        ['Métrica', 'Tarea {}'.format(actual['tarea']), 'Tarea {}'.format(anterior['tarea']),
         'Δ % (actual frente a comparación)'], filas)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tarea', type=int, required=True)
    parser.add_argument('--contra', type=int)
    parser.add_argument('--db', type=Path, default=Path(os.environ.get('MARCADOR_DB', str(RAIZ / 'registro/marcador.db'))))
    parser.add_argument('-o', type=Path, metavar='ARCHIVO.md')
    args = parser.parse_args(argv)
    try:
        ruta_db = args.db.expanduser().resolve()
        if args.o is not None and (args.o.resolve() == ruta_db or
                                  (args.o.exists() and os.path.samefile(args.o, ruta_db))):
            raise ValueError('La salida Markdown debe ser distinta de la base de datos.')
        uri = ruta_db.as_uri() + '?mode=ro'
        with closing(sqlite3.connect(uri, uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute('BEGIN')  # Una sola fotografía, también para --contra.
            actual = cargar(db, args.tarea)
            anterior = cargar(db, args.contra) if args.contra is not None else None
        texto = informe(actual)
        if anterior is not None:
            texto += comparar(actual, anterior)
        texto += '\n'
        if args.o is not None:
            args.o.write_text(texto, encoding='utf-8')
        sys.stdout.write(texto)
        return 0
    except (sqlite3.Error, OSError, ValueError) as error:
        print('retro: {}'.format(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
