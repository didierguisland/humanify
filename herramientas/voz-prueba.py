#!/usr/bin/python3
"""Voz de prueba y su SRT para experimentos (simula la grabación de AI Studio): voz del sistema (say, Paulina),
frase por frase, repartida en la duración pedida. Uso:
  voz-prueba.py <carpeta> --duracion 25 --texto "Frase uno. ¿Frase dos? Frase tres."
  voz-prueba.py <carpeta> --duracion 25 --archivo 03-SPEECH.md   (toma el bloque entre 'SPEECH:' y 'FIN'; si no hay, lo entrecomillado)
Deja <carpeta>/voz.wav (duración exacta) y <carpeta>/voz.srt.
  voz-prueba.py <carpeta> --original --archivo 03-SPEECH.md   hace de la grabación de la fábrica: una toma corrida del bloque VOZ en
      <carpeta>/voz-original.wav, para seguir con voz-preparar.py. Nunca pisa una voz que ya esté ahí, salvo con --forzar."""
import argparse
import os
from pathlib import Path
import re
import subprocess
import tempfile

FFMPEG = os.environ.get('HUMANIFY_FFMPEG', 'ffmpeg')


def texto_speech(crudo):
    """El speech de un archivo: el bloque entre una línea 'SPEECH:' y una 'FIN' (FALLA 45: lo entrecomillado
    agarraba alternativas y notas); en archivos viejos, lo entrecomillado más largo; si no, todo."""
    bloque = re.search(r'^\s*\**SPEECH:?\**\s*$(.*?)^\s*\**FIN\**\s*$', crudo, re.M | re.S)
    if bloque:
        return ' '.join(bloque.group(1).split())
    citado = re.findall(r'[«"“]([^«»"“”]{20,})[»"”]', crudo)
    return max(citado, key=len) if citado else crudo


def texto_de_voz(crudo):
    """Lo que se graba: el bloque VOZ sin etiquetas; si no hay, el SPEECH."""
    bloque = re.search(r'^\s*\**VOZ:?\**\s*$(.*?)^\s*\**FIN-VOZ\**\s*$', crudo, re.M | re.S)
    return ' '.join(re.sub(r'<[^>]*>', ' ', bloque.group(1)).split()) if bloque else texto_speech(crudo)


def grabar_original(carpeta, texto, voz, forzar=False):
    destino = Path(carpeta) / 'voz-original.wav'
    if destino.exists() and not forzar:
        raise SystemExit('{} ya existe: no piso una voz (usa --forzar si es de prueba)'.format(destino))
    with tempfile.TemporaryDirectory() as tmp:
        aiff = Path(tmp) / 'toma.aiff'
        subprocess.run(['say', '-v', voz, '-r', '178', '-o', str(aiff), texto], check=True)
        subprocess.run([FFMPEG, '-y', '-v', 'error', '-i', str(aiff), '-ar', '24000', '-ac', '1', '-c:a', 'pcm_s16le', str(destino)], check=True)
    print('VOZ DE PRUEBA en {}: sirve para probar el flujo, no para publicar.'.format(destino))


def frases(texto):
    partes = re.split(r'(?<=[.?!…])\s+', ' '.join(texto.split()))
    return [p for p in partes if re.search(r'\w', p)]


def duracion(archivo):
    salida = subprocess.run([FFMPEG, '-i', str(archivo)], capture_output=True, text=True).stderr
    h, m, s = re.search(r'Duration: (\d+):(\d+):([\d.]+)', salida).groups()
    return int(h) * 3600 + int(m) * 60 + float(s)


def marca(segundos):
    ms = int(round(segundos * 1000))
    return '{:02}:{:02}:{:02},{:03}'.format(ms // 3600000, ms // 60000 % 60, ms // 1000 % 60, ms % 1000)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('carpeta')
    p.add_argument('--duracion', type=float)
    p.add_argument('--original', action='store_true', help='una toma corrida en voz-original.wav, como la de AI Studio')
    p.add_argument('--forzar', action='store_true', help='con --original, pisa el voz-original.wav que haya')
    p.add_argument('--texto')
    p.add_argument('--archivo')
    p.add_argument('--voz', default='Paulina')
    args = p.parse_args()
    if args.original:
        crudo = Path(args.archivo).read_text(encoding='utf-8') if args.archivo else args.texto
        if not crudo:
            raise SystemExit('no hay texto')
        grabar_original(args.carpeta, texto_de_voz(crudo), args.voz, args.forzar)
        return
    if args.duracion is None:
        p.error('--duracion es obligatorio salvo con --original')
    texto = args.texto
    if args.archivo:
        texto = texto_speech(Path(args.archivo).read_text(encoding='utf-8'))
    lista = frases(texto or '')
    if not lista:
        raise SystemExit('no hay texto')
    carpeta = Path(args.carpeta)
    with tempfile.TemporaryDirectory() as tmp:
        partes = []
        for i, frase in enumerate(lista):
            aiff = Path(tmp) / 'f{}.aiff'.format(i)
            subprocess.run(['say', '-v', args.voz, '-r', '145', '-o', str(aiff), frase], check=True)
            partes.append((frase, aiff, duracion(aiff)))
        hablado = sum(d for _, _, d in partes)
        hueco = (args.duracion - 1.5 - 0.5 - hablado) / max(1, len(partes) - 1)
        if hueco < 0.15:
            raise SystemExit('el texto no cabe en {} s (habla {:.1f} s)'.format(args.duracion, hablado))
        t, tiempos = 0.5, []
        for frase, aiff, d in partes:
            tiempos.append((t, frase, d, aiff))
            t += d + min(hueco, 1.2)
        comando = [FFMPEG, '-y']
        for _, _, _, aiff in tiempos:
            comando += ['-i', str(aiff)]
        filtro = ';'.join('[{0}]adelay={1}|{1}[a{0}]'.format(i, int(t * 1000)) for i, (t, _, _, _) in enumerate(tiempos))
        filtro += ';' + ''.join('[a{}]'.format(i) for i in range(len(tiempos)))
        filtro += 'amix=inputs={}:normalize=0,apad,atrim=0:{}[o]'.format(len(tiempos), args.duracion)
        subprocess.run(comando + ['-filter_complex', filtro, '-map', '[o]', '-ar', '48000', '-ac', '1',
                                  str(carpeta / 'voz.wav')], check=True, capture_output=True)
    with (carpeta / 'voz.srt').open('w', encoding='utf-8') as srt:
        for i, (t, frase, d, _) in enumerate(tiempos, 1):
            srt.write('{}\n{} --> {}\n{}\n\n'.format(i, marca(t), marca(t + d), frase))
    print(' '.join('[{:.2f}-{:.2f}] {}'.format(t, t + d, f) for t, f, d, _ in tiempos))


if __name__ == '__main__':
    main()
