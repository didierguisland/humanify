#!/usr/bin/python3
"""Calcula hechos declarados por la ruta y guarda HECHOS.json en la carpeta."""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
import math
from pathlib import Path
import re


_spec_voz = importlib.util.spec_from_file_location(
    'voz_prueba', Path(__file__).with_name('voz-prueba.py'))
_voz = importlib.util.module_from_spec(_spec_voz)
_spec_voz.loader.exec_module(_voz)


def calcular(carpeta, spec):
    if not isinstance(spec, dict):
        raise ValueError('spec debe ser un objeto {nombre: regla}')
    carpeta = Path(carpeta)
    hechos = {}
    for nombre, regla in spec.items():
        if not isinstance(regla, dict):
            raise ValueError('{}: la regla debe ser un objeto'.format(nombre))
        claves = set(regla)
        if claves == {'fijo'}:
            valor = regla['fijo']
        elif claves in ({'duracion_audio'}, {'palabras'}, {'caracteres'}):
            tipo = next(iter(regla))
            archivo = carpeta / regla[tipo]
            valor = None
            if archivo.exists():
                if tipo == 'duracion_audio':
                    try:
                        valor = round(_voz.duracion(archivo), 2)
                    except AttributeError as error:
                        raise ValueError('{}: no se pudo medir el audio {}'.format(
                            nombre, archivo)) from error
                else:
                    texto = archivo.read_text(encoding='utf-8')
                    if tipo == 'palabras':
                        # La misma selección que voz-prueba.py --archivo.
                        texto = _voz.texto_speech(texto)
                        valor = len(texto.split())
                    else:
                        valor = len(texto)
        elif claves == {'producto', 'redondeo'} and regla['redondeo'] == 'abajo':
            producto = regla['producto']
            if (not isinstance(producto, list) or len(producto) != 2
                    or not isinstance(producto[0], str)
                    or isinstance(producto[1], bool)
                    or not isinstance(producto[1], (int, float))):
                raise ValueError('{}: producto debe ser [hecho, número]'.format(nombre))
            referencia, factor = producto
            if referencia not in hechos:
                raise ValueError('{}: el hecho {} debe calcularse antes'.format(nombre, referencia))
            previo = hechos[referencia]
            if previo is not None and (isinstance(previo, bool)
                                      or not isinstance(previo, (int, float))):
                raise ValueError('{}: el hecho {} debe ser numérico'.format(nombre, referencia))
            valor = None if previo is None else math.floor(previo * factor)
        else:
            raise ValueError('{}: regla desconocida {}'.format(
                nombre, json.dumps(regla, ensure_ascii=False)))
        hechos[nombre] = valor
    return {'hechos': hechos, 'calculado': datetime.now(timezone.utc).isoformat()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('carpeta', type=Path)
    parser.add_argument('--spec', required=True)
    args = parser.parse_args()
    try:
        datos = calcular(args.carpeta, json.loads(args.spec))
        salida = json.dumps(datos, ensure_ascii=False)
        args.carpeta.mkdir(parents=True, exist_ok=True)
        (args.carpeta / 'HECHOS.json').write_text(salida + '\n', encoding='utf-8')
    except (ValueError, TypeError, OSError) as error:
        parser.error(str(error))
    print(salida)


if __name__ == '__main__':
    main()
