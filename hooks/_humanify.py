#!/usr/bin/python3
"""Humanify fase 1. Solo biblioteca estándar; los hooks fallan en silencio."""
import contextlib
import datetime as dt
import difflib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata
import uuid


IMPORT_DIR = Path(os.environ.get('HUMANIFY_DIR', str(Path(__file__).resolve().parent.parent))).absolute()
HOME_DIR = IMPORT_DIR.resolve()
PROJECTS_SPELLING = Path(os.environ.get('HUMANIFY_PROYECTOS', str(IMPORT_DIR.parent))).absolute()
PROJECTS = PROJECTS_SPELLING.resolve()
GLOBAL = Path(os.environ.get('HUMANIFY_GLOBAL_CLAUDE', str(Path.home() / '.claude/CLAUDE.md')))
REG = HOME_DIR / 'registro'
ERRORS = []


def now():
    return dt.datetime.now().astimezone().isoformat(timespec='microseconds')


def date(value):
    parsed = dt.datetime.fromisoformat(str(value).strip().replace('Z', '+00:00'))
    return parsed.astimezone() if parsed.tzinfo is None else parsed


def under(path, parent):
    return os.path.commonpath((str(path), str(parent))) == str(parent)


def reg_path(*parts):
    path = REG.joinpath(*parts)
    # Ningún enlace del registro puede redirigir escrituras fuera de él.
    if REG.is_symlink() or not under(path.resolve(), REG.absolute()):
        raise ValueError('ruta de registro insegura')
    return path


def identifier(value):
    value = str(value or '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', value):
        raise ValueError('id de sesión inválido')
    return value


def read_json(path):
    with path.open(encoding='utf-8') as stream:
        return json.load(stream)


def atomic_json(path, value):
    reg_path(*path.relative_to(REG).parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.humanify-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.write('\n')
        os.replace(temporary, str(path))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def active():
    expected = '@' + str(IMPORT_DIR / 'cerebro/CONSTITUCION.md')
    with GLOBAL.open(encoding='utf-8') as stream:
        return any(line.rstrip(' \t\r\n') == expected for line in stream)


def project(cwd):
    current = Path(cwd).resolve()
    if not under(current, PROJECTS) or current == PROJECTS:
        return None, 'suelta', None
    root = current.relative_to(PROJECTS).parts[0]
    if root == 'Tareas':
        return None, 'suelta', None
    folder = PROJECTS / root
    names = {entry.name for entry in folder.iterdir()}
    bases = folder / 'Bases'
    base_names = {entry.name for entry in bases.iterdir()} if 'Bases' in names else set()
    if root == 'ProjectA':
        kind = 'projecta'
    elif 'CLAUDE.md' not in names:
        kind = 'sin-estructura'
    elif 'ESTADO.md' in base_names:
        kind = 'cerebro'
    elif any(re.fullmatch(r'RELEVO-\d+\.md', name) for name in base_names):
        kind = 'viejo'
    else:
        kind = 'basico'
    return root, kind, folder


def failure(message):
    ERRORS.append(str(message).replace('\n', ' ').replace('\t', ' ')[:240])


def log_event(event, sid, status, milliseconds, message):
    path = reg_path('hooks-' + now()[:7] + '.log')
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = (now(), event, status, str(milliseconds), str(sid)[:8], message)
    line = '\t'.join(str(field).replace('\n', ' ').replace('\t', ' ')[:300] for field in fields) + '\n'
    fd = os.open(str(path), os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
    try:
        os.write(fd, line.encode('utf-8'))
    finally:
        os.close(fd)


def reverse_lines(path):
    """JSONL al revés, sin cargar el archivo ni una línea desmesurada."""
    with path.open('rb') as stream:
        stream.seek(0, os.SEEK_END)
        position, tail = stream.tell(), b''
        while position:
            size = min(position, 65536)
            position -= size
            stream.seek(position)
            chunks = (stream.read(size) + tail).split(b'\n')
            tail = chunks[0]
            if len(tail) > 1048576:
                raise ValueError('línea de registro excesiva')
            for line in reversed(chunks[1:]):
                if line:
                    yield line
        if tail:
            yield tail


def receipt(sid, start):
    since = date(start)
    for path in sorted(reg_path().glob('cierres-????-??.jsonl'), reverse=True):
        if path.name[8:15] < since.astimezone().strftime('%Y-%m'):
            continue
        for line in reverse_lines(path):
            try:
                item = json.loads(line)
                if item.get('id') == sid and date(item['hora']) > since:
                    return True
            except (ValueError, KeyError, TypeError):
                continue
    return False


def pending_errors():
    marker = reg_path('errores-vistos')
    since = date(marker.read_text(encoding='utf-8')) if marker.exists() else None
    total, last = 0, []
    for path in sorted(reg_path().glob('hooks-????-??.log')):
        for raw in reverse_lines(path):
            line = raw.decode('utf-8', errors='replace')
            fields = line.split('\t')
            if len(fields) >= 6 and fields[2] == 'error':
                if since is None or date(fields[0]) > since:
                    total += 1
                    last.append(line)
                    last = sorted(last)[-5:]
    return total, last, since.isoformat() if since else 'el inicio del registro'


class Output:
    def __init__(self):
        self.lines = []
        self.size = 0

    def add(self, text):
        text = str(text).rstrip('\n')
        if self.size + len(text) + 1 > 10000:
            return False
        self.lines.append(text)
        self.size += len(text) + 1
        return True

    def render(self):
        return '\n'.join(self.lines) + ('\n' if self.lines else '')


def memory_context(out, kind, folder):
    path = None
    if kind == 'cerebro':
        path = folder / 'Bases/ESTADO.md'
    elif kind in ('viejo', 'projecta'):
        out.add('Formato viejo: este relevo puede no ser el estado completo del proyecto.')
        candidates = []
        for item in (folder / 'Bases').glob('RELEVO-*.md'):
            match = re.fullmatch(r'RELEVO-(\d+)\.md', item.name)
            if match:
                candidates.append((int(match.group(1)), item))
        if candidates:
            path = max(candidates)[1]
    elif kind == 'basico':
        out.add('Proyecto con CLAUDE.md pero sin Bases/: sigue su CLAUDE.md.')
    elif kind == 'sin-estructura':
        out.add('Proyecto sin estructura: propón a Didier la ronda de arranque (/arranque) antes de trabajar; no la hagas sin su sí.')
    if path:
        with path.open(encoding='utf-8') as stream:
            lines = []
            for _ in range(61):
                line = stream.readline(10001)
                if not line:
                    break
                if len(line) > 8000:
                    lines = None
                    break
                lines.append(line)
        text = ''.join(lines if lines is not None and len(lines) <= 60 else (lines or [])[:40])
        if lines is None or len(text) > 8000:
            out.add('Memoria del proyecto: ' + str(path))
        else:
            out.add(text)
            if len(lines) > 60:
                out.add('… sigue en ' + str(path))
    if kind == 'projecta':
        out.add('ProjectA: tú sigues su ciclo de siempre y reescribes sus relevos, aprendizajes y libro de correcciones como lo pide su CLAUDE.md; la fábrica corre igual. Solo los procesos automáticos de Humanify (rescates, revisión semanal) se limitan a Bases/HUMANIFY-CANDIDATOS.md. Lo que sirva para todos los proyectos, márcalo ↑centro.')


def unclosed(item):
    if item.get('estado') == 'sin-cerrar':
        return True
    if item.get('estado') == 'abierta' and date(now()) - date(item['inicio']) > dt.timedelta(hours=12):
        return not receipt(item['id'], item['inicio'])
    return False


def start_hook(data):
    sid = identifier(data.get('session_id'))
    try:
        root, kind, folder = project(data.get('cwd', os.getcwd()))
    except (OSError, ValueError):
        return 'Humanify: no se pudo decidir la raíz o el tipo; no se registró esta sesión.\n'
    source = data.get('source', 'startup')
    out = Output()
    out.add('Humanify activo · sesión ' + sid + (' · proyecto ' + root if root else ' · tarea suelta'))
    if source == 'compact':
        memory_context(out, kind, folder)
        reg_path('estado', sid + '.json').unlink(missing_ok=True)
        out.add('Se compactó el contexto. Si antes se aprendió algo con sustento, anótalo en Bases/APRENDIZAJES.md con su origen (en ProjectA, donde lo pida su propio ciclo). Si no hubo nada, sigue.')
        return out.render()
    if source not in ('startup', 'resume', 'clear', 'fork'):
        raise ValueError('source desconocido')
    if not root:
        return out.render()
    mark = {'id': sid, 'raiz': root, 'cwd': data.get('cwd', os.getcwd()), 'inicio': now(),
            'transcript_path': data.get('transcript_path', ''), 'source': source, 'estado': 'abierta'}
    # Primero se lee el contexto: una carpeta ilegible nunca deja una marca nueva.
    memory_context(out, kind, folder)
    atomic_json(reg_path('sesiones', sid + '.json'), mark)
    for path in sorted(reg_path('sesiones').glob('*.json')):
        if path.stem == sid:
            continue
        item = read_json(path)
        if item.get('raiz') == root and not item.get('ofrecida') and unclosed(item):
            text = ('La sesión {short} del {start} no cerró (transcript: {transcript}). '
                    'Sugiere a Didier rescatarla con un Sonnet o descartarla; al resolverla corre: '
                    'herramientas/anota.sh resuelto {sid} rescatada|descartada').format(
                        short=item['id'][:8], start=item['inicio'], transcript=item.get('transcript_path', ''), sid=item['id'])
            if out.add(text):
                item['ofrecida'] = True
                atomic_json(path, item)
    proposals = HOME_DIR / 'cerebro/PROPUESTAS.md'
    if proposals.exists():
        with proposals.open(encoding='utf-8') as stream:
            count = sum(bool(re.match(r'^\s*- \[ \]', line)) for line in stream)
        if count:
            out.add('Propuestas esperando a Didier: ' + str(count))
    weekly = reg_path('ultima-semanal')
    if weekly.exists() and date(now()) - date(weekly.read_text(encoding='utf-8')) > dt.timedelta(days=7):
        out.add('Toca la revisión semanal (/semana).')
    count, _, since = pending_errors()
    if count:
        out.add('Humanify: {} errores de hooks desde {}. Corre /humanify-estado.'.format(count, since))
    fase2_avisos(out, root)
    return out.render()


def fase2_avisos(out, root):
    """Fase 2: salas detenidas del proyecto y tareas del marcador sin calificar."""
    try:
        import _sala
        for sala in _sala.detenidas(root):
            out.add('Sala detenida: {}. Revisa con herramientas/sala.sh estado y decide si se corrige o se cierra.'.format(sala))
    except Exception as exc:
        failure('salas: {}'.format(exc))
    db = Path(os.environ.get('MARCADOR_DB', str(REG / 'marcador.db')))
    if not db.exists():
        return
    try:
        import sqlite3
        with sqlite3.connect('file:{}?mode=ro'.format(db), uri=True, timeout=0.2) as con:
            pendientes = con.execute('SELECT count(*) FROM sin_veredicto').fetchone()[0]
            nulos = con.execute('SELECT count(*) FROM veredictos v WHERE nivel IS NULL AND juez = \'calificador\' '
                                'AND date(v.fecha) >= date(\'now\', \'-7 days\')').fetchone()[0]
            alertas, motivo = 0, ''
            if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='alertas'").fetchone():
                alertas = con.execute('SELECT count(*) FROM alertas WHERE atendida=0').fetchone()[0]
                if alertas:
                    motivo = con.execute('SELECT motivo FROM alertas WHERE atendida=0 '
                                         'ORDER BY fecha DESC,id DESC LIMIT 1').fetchone()[0]
        if pendientes or nulos:
            out.add('Marcador: {} tareas sin calificar y {} no evaluables esta semana. Si Didier opina distinto de una, '
                    'herramientas/marcador.sh corregir <tarea> 0|1|2 "motivo".'.format(pendientes, nulos))
        if alertas:
            out.add('Marcador: {} alertas de gasto (la más reciente: {}). herramientas/marcador.sh alertas'.format(
                alertas, ' '.join(motivo.split())[:100]))
    except Exception as exc:
        failure('marcador: {}'.format(exc))


def lanzar_marcador(subcomando, data):
    """Entrega el payload al marcador en un proceso aparte; el hook nunca espera."""
    herramienta = HOME_DIR / 'herramientas/marcador.sh'
    if not herramienta.exists():
        return
    proceso = subprocess.Popen(['/bin/bash', str(herramienta), subcomando], stdin=subprocess.PIPE,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    proceso.stdin.write(json.dumps(data).encode('utf-8'))
    proceso.stdin.close()


def delegation_hook(data):
    lanzar_marcador('hook-delegacion', data)
    return ''


def subagent_hook(data):
    lanzar_marcador('hook-subagente', data)
    import _sala
    _sala.subagente_termino(data.get('agent_id') or '')
    return ''


def guard_hook(data):
    import _sala
    return _sala.guardia(data)


def normalize(text):
    return ''.join(char for char in unicodedata.normalize('NFD', text.lower()) if not unicodedata.combining(char))


def router_hook(data):
    prompt = data.get('prompt', '')
    if prompt.startswith('/'):
        return ''
    try:
        project(data.get('cwd', os.getcwd()))
    except (OSError, ValueError):
        return 'Humanify: no se pudo decidir la raíz o el tipo; no se registró esta sesión.\n'
    sid = identifier(data.get('session_id'))
    router = HOME_DIR / 'cerebro/ROUTER.md'
    if not router.exists():
        failure('falta cerebro/ROUTER.md')
        return ''
    prompt = normalize(prompt)
    matches = {}
    with router.open(encoding='utf-8') as stream:
        for index, line in enumerate(stream):
            line = line.strip()
            if not line or line.startswith('#') or '->' not in line:
                continue
            words, destination = line.split('->', 1)
            match = re.fullmatch(r'\s*(\S+?)(?:\s+\(prioridad\s+(\d+)\))?\s*', destination)
            if not match:
                continue
            route, priority = match.group(1), int(match.group(2) or 5)
            found = [word.strip() for word in words.split('|') if word.strip() and
                     re.search(r'(?<!\w)' + re.escape(normalize(word.strip())) + r'(?!\w)', prompt)]
            if found:
                target = (HOME_DIR / 'cerebro' / route).resolve()
                # Además de los contratos, el router puede sugerir el router de modelos (MODELOS.md).
                es_modelos = route == 'MODELOS.md' and target == (HOME_DIR / 'cerebro/MODELOS.md').resolve()
                if not es_modelos and (not under(target, (HOME_DIR / 'cerebro/contratos').resolve()) or not route.startswith('contratos/')):
                    failure('ruta de contrato inválida: ' + route)
                    return ''
                if route not in matches:
                    matches[route] = [priority, index, target, []]
                matches[route][0] = min(matches[route][0], priority)
                matches[route][3].extend(word for word in found if word not in matches[route][3])
    if not matches:
        return ''
    state_path = reg_path('estado', sid + '.json')
    previous = read_json(state_path) if state_path.exists() else {}
    fresh = []
    for route, (priority, index, target, words) in matches.items():
        try:
            mtime = target.stat().st_mtime_ns
            if not target.is_file():
                raise OSError('no es archivo')
        except OSError:
            failure('falta contrato: ' + route)
            return ''
        if previous.get(route) != mtime:
            fresh.append((priority, index, route, target, words, mtime))
    if not fresh:
        return ''
    fresh.sort()
    words = list(dict.fromkeys(word for item in fresh for word in item[4]))
    out = Output()
    if not out.add('Contratos sugeridos por palabra ({}). Úsalos solo si aplican a lo que Didier quiere:'.format(', '.join(words))):
        return ''
    best = fresh[0]
    with best[3].open(encoding='utf-8') as stream:
        content = stream.read(10001)
    # Un contrato nunca se corta: si no cabe, se ofrece solamente su ruta.
    text = content if len(content) <= 8000 and len(content.splitlines()) <= 60 else best[2]
    if not out.add(text):
        return ''
    printed = [best]
    others = []
    for item in fresh[1:]:
        candidate = 'También: ' + ', '.join(others + [item[2]])
        if out.size + len(candidate) + 1 <= 10000:
            others.append(item[2])
            printed.append(item)
    if others:
        out.add('También: ' + ', '.join(others))
    for item in printed:
        previous[item[2]] = item[5]
    atomic_json(state_path, previous)
    return out.render()


def user_count(path):
    deadline = time.monotonic() + 0.35
    count = 0
    try:
        with Path(path).open('rb') as stream:
            while True:
                if time.monotonic() > deadline:
                    return 3
                line = stream.readline(262145)
                if not line:
                    return count
                if len(line) > 262144:
                    return 3
                try:
                    item = json.loads(line)
                except (ValueError, UnicodeError):
                    if line.strip():
                        return 3
                    continue
                if not isinstance(item, dict) or item.get('type') != 'user':
                    continue
                content = item.get('message', {}).get('content', item.get('content', ''))
                if isinstance(content, list):
                    real = any(not isinstance(block, dict) or block.get('type') != 'tool_result' for block in content)
                else:
                    real = bool(content)
                if real:
                    count += 1
                    if count >= 3:
                        return count
    except (OSError, ValueError, TypeError, AttributeError):
        return 3


def end_hook(data):
    sid = identifier(data.get('session_id'))
    path = reg_path('sesiones', sid + '.json')
    if not path.exists():
        return ''
    item = read_json(path)
    if receipt(sid, item['inicio']) or user_count(data.get('transcript_path') or item.get('transcript_path', '')) < 3:
        path.unlink()
    else:
        item.update(estado='sin-cerrar', fin=now())
        atomic_json(path, item)
    return ''


@contextlib.contextmanager
def lock(path):
    """Candado por mkdir, con recuperación de directorios abandonados."""
    deadline = time.monotonic() + 5
    token = uuid.uuid4().hex
    owner = path / token
    while True:
        try:
            path.mkdir()
            owner.touch()
            break
        except FileExistsError:
            try:
                info = path.lstat()
                if not stat.S_ISDIR(info.st_mode):
                    raise ValueError('el candado no es una carpeta: ' + str(path))
                if time.time() - info.st_mtime > 30:
                    # Serializar la recuperación dentro del propio candado evita
                    # que dos recuperadores renombren el candado recién adquirido.
                    guard = path / '.recuperando'
                    recovering = False
                    try:
                        guard.mkdir()
                        recovering = True
                    except FileExistsError:
                        if time.time() - guard.stat().st_mtime > 30:
                            guard.rmdir()
                            try:
                                guard.mkdir()
                                recovering = True
                            except FileExistsError:
                                pass
                    if recovering:
                        current = path.stat()
                        if (current.st_dev, current.st_ino) == (info.st_dev, info.st_ino):
                            stale = path.with_name(path.name + '.abandonado-' + token)
                            os.rename(str(path), str(stale))
                            shutil.rmtree(str(stale))
                            continue
                        guard.rmdir()
            except FileNotFoundError:
                continue
            if time.monotonic() >= deadline:
                raise ValueError('no se pudo tomar el candado en 5 s: ' + str(path))
            time.sleep(0.025)
    try:
        yield
    finally:
        # Nunca se suelta un candado que haya cambiado de dueño.
        if owner.exists():
            # Un recuperador con una observación vieja puede estar comprobando
            # este inode nuevo; se espera a que retire su guardia antes de soltar.
            while (path / '.recuperando').exists() and time.monotonic() < deadline:
                time.sleep(0.005)
            owner.unlink()
            path.rmdir()


def append(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with lock(Path(str(path) + '.candado')):
        prefix = ''
        if path.exists() and path.stat().st_size:
            with path.open('rb') as stream:
                stream.seek(-1, os.SEEK_END)
                if stream.read(1) != b'\n':
                    prefix = '\n'
        payload = (prefix + text + ('' if text.endswith('\n') else '\n')).encode('utf-8')
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            if os.write(fd, payload) != len(payload):
                raise OSError('escritura incompleta')
        finally:
            os.close(fd)


def destination(value):
    path = Path(value).resolve()
    humanify = HOME_DIR.resolve()
    if not (under(path, PROJECTS) or under(path, humanify)):
        raise ValueError('destino rechazado: fuera de HUMANIFY_PROYECTOS y HUMANIFY_DIR')
    projecta = PROJECTS / 'ProjectA'
    candidate = projecta / 'Bases/HUMANIFY-CANDIDATOS.md'
    if under(path, projecta) and path != candidate:
        raise ValueError('destino rechazado: en ProjectA solo Bases/HUMANIFY-CANDIDATOS.md')
    # También se aplica la política al nombre original si cruza enlaces simbólicos.
    lexical = Path(os.path.abspath(value))
    for projects_spelling in (PROJECTS, PROJECTS_SPELLING):
        lexical_projecta = projects_spelling / 'ProjectA'
        if under(lexical, lexical_projecta) and (lexical != lexical_projecta / 'Bases/HUMANIFY-CANDIDATOS.md' or path != candidate):
            raise ValueError('destino rechazado: enlace o ruta no permitida en ProjectA')
    return path


def photo(root, reason):
    if not root or root == 'Tareas':
        return False
    if root in ('.', '..') or '/' in root or '\\' in root or not (PROJECTS / root).is_dir():
        raise ValueError('raíz de foto inválida')
    folder = PROJECTS / root
    if folder.is_symlink() or not under(folder.resolve(), PROJECTS):
        raise ValueError('raíz de foto con enlace no permitida')
    repo = reg_path('memoria.git')
    repo.parent.mkdir(parents=True, exist_ok=True)
    # El entorno del git del proyecto no puede redirigir el índice o los objetos.
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT='0')
    base = ['git', '--git-dir=' + str(repo), '--work-tree=' + str(PROJECTS),
            '-c', 'core.hooksPath=' + os.devnull, '-c', 'commit.gpgSign=false',
            '-c', 'core.fsmonitor=false', '-c', 'core.attributesFile=' + os.devnull,
            '-c', 'core.excludesFile=' + os.devnull, '-c', 'core.bare=false']

    def git(args, allowed=(0,), input_data=None):
        result = subprocess.run(base + args, input=input_data, env=env, cwd=str(PROJECTS),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        if result.returncode not in allowed:
            raise ValueError('falló git de memoria: ' + result.stderr.decode('utf-8', errors='replace').strip()[:200])
        return result

    with lock(Path(str(repo) + '.candado')):
        if not (repo / 'HEAD').exists():
            result = subprocess.run(['git', '-c', 'init.templateDir=', 'init', '--bare', str(repo)],
                                    env=env, cwd=str(PROJECTS), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
            if result.returncode:
                raise ValueError('no se pudo crear el git de memoria')
            # El status no ofrece código, secretos ni archivos de otros proyectos.
            (repo / 'info').mkdir(exist_ok=True)
            (repo / 'info/exclude').write_text('*\n', encoding='utf-8')
        # Solo se limpia el índice sombra; read-tree sin -u no toca proyectos.
        head = git(['rev-parse', '--verify', 'HEAD'], allowed=(0, 128)).returncode == 0
        git(['read-tree', 'HEAD'] if head else ['read-tree', '--empty'])
        escaped = ''.join('\\' + char if char in '*?[]\\' else char for char in root)
        specs = [':(literal)' + root + '/CLAUDE.md', ':(glob)' + escaped + '/Bases/**/*.md']
        tracked = git(['ls-files', '-z', '--'] + specs).stdout.decode('utf-8').split('\0')
        valid = []
        seed = folder / 'CLAUDE.md' if (folder / 'CLAUDE.md').is_file() else None
        if seed or root + '/CLAUDE.md' in tracked:
            valid.append(specs[0])
        bases = folder / 'Bases'
        has_md = False
        if bases.is_dir() and not bases.is_symlink():
            for walk_path, _directories, files in os.walk(str(bases), followlinks=False):
                if any(name.endswith('.md') for name in files):
                    has_md = True
                    if seed is None:
                        seed = Path(walk_path) / next(name for name in files if name.endswith('.md'))
                    break
        if has_md or any(item.startswith(root + '/Bases/') for item in tracked):
            valid.append(specs[1])
        if seed is not None and not any(tracked):
            # Git no entra en un repo anidado aún desconocido por el índice.
            # Sembrar UN archivo permitido en el índice sombra habilita el glob.
            if seed.is_symlink():
                blob = git(['hash-object', '-w', '--stdin'], input_data=os.fsencode(os.readlink(str(seed)))).stdout.strip()
                mode = '120000'
            else:
                blob = git(['hash-object', '-w', '--no-filters', '--', str(seed)]).stdout.strip()
                mode = '100755' if seed.stat().st_mode & 0o111 else '100644'
            git(['update-index', '--add', '--cacheinfo', mode, blob.decode('ascii'), str(seed.relative_to(PROJECTS))])
        if valid:
            git(['add', '--force', '--all', '--'] + valid)
        if git(['diff', '--cached', '--quiet', '--exit-code'], allowed=(0, 1)).returncode == 0:
            return False
        git(['-c', 'user.name=Humanify', '-c', 'user.email=humanify@local',
             'commit', '-m', 'foto {} {}'.format(root, reason)])
        return True


def normalizar_candidato(texto):
    return ' '.join(texto.lower().split())


def normalizar_cita(texto):
    return ' '.join(''.join(c for c in texto.lower() if not unicodedata.category(c).startswith('P')).split())


def textos_usuario(transcript):
    """Solo texto humano; nunca contenido anidado en resultados de herramientas."""
    with Path(transcript).open(encoding='utf-8') as stream:
        for linea in stream:
            try:
                item = json.loads(linea)
            except ValueError:
                continue
            if not isinstance(item, dict) or item.get('type') != 'user' or item.get('isMeta') or item.get('isSynthetic'):
                continue
            mensaje = item.get('message', {})
            if not isinstance(mensaje, dict) or mensaje.get('role', 'user') != 'user':
                continue
            contenido = mensaje.get('content', item.get('content', ''))
            if isinstance(contenido, str):
                yield contenido
            elif isinstance(contenido, list):
                yield ' '.join(b['text'] for b in contenido if isinstance(b, dict)
                               and b.get('type') == 'text' and isinstance(b.get('text'), str))


def validar_candidato(texto, base, cita, cambia, transcript, destino, previos=()):
    """Rechaza con una corrección concreta; no escribe ni llama a modelos."""
    if not texto.strip():
        raise ValueError('texto vacío; escribe el aprendizaje concreto')
    if normalizar_candidato(cambia) in ('', 'nada', '—', '-', 'ninguna', 'n/a') or len(cambia.split()) < 4:
        raise ValueError('cambia debe tener al menos 4 palabras; explica qué harás distinto')
    if base not in ('hecho', 'didier', 'inferencia'):
        raise ValueError('base inválida; usa hecho, didier o inferencia')
    if re.search(r'\bdidier\b', texto, re.I) and base != 'didier':
        raise ValueError('el texto menciona a Didier; usa base: didier con sus palabras exactas y transcript')
    if base == 'didier':
        if not transcript:
            raise ValueError('sin transcript no se puede verificar; usa base: inferencia')
        if len(normalizar_cita(cita).split()) < 3:
            raise ValueError('falta una cita de al menos 3 palabras; copia las palabras exactas de Didier')
        try:
            verificada = any(normalizar_cita(cita) in normalizar_cita(m) for m in textos_usuario(transcript))
        except (OSError, UnicodeError):
            raise ValueError('no se pudo leer el transcript; indica un transcript .jsonl legible')
        if not verificada:
            raise ValueError('la cita no aparece en mensajes de usuario; copia sus palabras exactas del transcript')
    path = Path(destino)
    entradas = path.read_text(encoding='utf-8').splitlines() if path.exists() else []
    for entrada in list(previos) + entradas:
        entrada = re.sub(r'^\s*(?:[-*+] |\d+[.)] )\s*', '', entrada)
        entrada = re.sub(r'^\[[^\]]+\]\s*', '', entrada)
        entrada = re.split(r'\s+—\s+(?:base|fuente):|\s+·\s+(?:base|cita|cambia|fuente):', entrada, maxsplit=1)[0]
        if difflib.SequenceMatcher(None, normalizar_candidato(texto), normalizar_candidato(entrada)).ratio() >= 0.85:
            raise ValueError('candidato repetido; aporta un aprendizaje distinto del que ya está registrado')


def escribir_candidato(destino, texto, base, cambia, fuente, cita='', transcript=None, ident=None):
    path = destination(str(destino))
    ident = ident or uuid.uuid4().hex
    if not re.fullmatch(r'[A-Za-z0-9_:-]{1,200}', ident):
        raise ValueError('id inválido; usa letras, números, guiones o dos puntos')
    texto, cambia, fuente, cita = (' '.join(v.split()) for v in (texto, cambia, fuente, cita))
    path.parent.mkdir(parents=True, exist_ok=True)
    with lock(Path(str(path) + '.candidatos-candado')):
        existente = path.read_text(encoding='utf-8') if path.exists() else ''
        if re.search(r'\bid ' + re.escape(ident) + r'(?=\s|$)', existente):
            return ident
        validar_candidato(texto, base, cita, cambia, transcript, path)
        linea = '- [por validar] {} — base: {}'.format(texto, base)
        if cita:
            linea += ' · cita: "{}"'.format(cita)
        append(path, linea + ' · cambia: {} · fuente: {} · {} · id {}'.format(
            cambia, fuente, dt.date.today().isoformat(), ident))
    return ident


def anota_candidato(args):
    uso = 'usa candidato <destino.md> "<texto>" --base B --cambia "…" --fuente "…" [--cita "…"] [--transcript RUTA] [--id ID]'
    if len(args) < 2 or (len(args) - 2) % 2:
        raise ValueError(uso)
    opciones = {}
    for i in range(2, len(args), 2):
        nombre = args[i]
        if nombre not in ('--base', '--cambia', '--fuente', '--cita', '--transcript', '--id') or nombre in opciones:
            raise ValueError(uso)
        opciones[nombre] = args[i + 1]
    if not all(nombre in opciones for nombre in ('--base', '--cambia', '--fuente')):
        raise ValueError(uso)
    ident = escribir_candidato(args[0], args[1], opciones['--base'], opciones['--cambia'], opciones['--fuente'],
                               opciones.get('--cita', ''), opciones.get('--transcript'), opciones.get('--id'))
    print('candidato escrito · id ' + ident)


def anota(args):
    if not args:
        raise ValueError('uso: anota.sh candidato|bitacora|cierre|foto|resuelto ...')
    command = args[0]
    if command == 'candidato':
        anota_candidato(args[1:])
    elif command == 'bitacora' and len(args) == 3:
        path = destination(args[1])
        text = sys.stdin.read() if args[2] == '-' else args[2]
        append(path, text)
    elif command == 'cierre' and len(args) == 3:
        sid = identifier(args[1])
        if '\n' in args[2] or '\r' in args[2]:
            raise ValueError('el cierre debe ser una sola línea')
        path = reg_path('sesiones', sid + '.json')
        root = read_json(path).get('raiz') if path.exists() else project(os.getcwd())[0]
        entry = {'id': sid, 'raiz': root, 'hora': now(), 'linea': args[2]}
        append(reg_path('cierres-' + now()[:7] + '.jsonl'), json.dumps(entry, ensure_ascii=False))
        changed = photo(root, 'cierre')
        print('Cierre registrado' + (' · foto de memoria guardada' if changed else ' · sin cambios de memoria' if root else ''))
    elif command == 'foto' and len(args) == 2:
        print('Foto de memoria guardada' if photo(args[1], 'manual') else 'Sin cambios de memoria')
    elif command == 'resuelto' and len(args) == 3 and args[2] in ('rescatada', 'descartada'):
        sid = identifier(args[1])
        path = reg_path('sesiones', sid + '.json')
        item = read_json(path) if path.exists() else {}
        entry = {'id': sid, 'raiz': item.get('raiz'), 'hora': now(), 'resultado': args[2]}
        append(reg_path('rescates-' + now()[:7] + '.jsonl'), json.dumps(entry, ensure_ascii=False))
        path.unlink(missing_ok=True)
        print('Sesión ' + args[2])
    else:
        raise ValueError('argumentos inválidos para anota.sh')


def status(args):
    if args not in ([], ['--atendidos']):
        raise ValueError('uso: estado.sh [--atendidos]')
    try:
        print('Humanify: ' + ('activo' if active() else 'apagado (falta la línea @import)'))
    except (OSError, UnicodeError) as exc:
        print('Humanify: en duda (no se pudo leer el CLAUDE.md global: {})'.format(type(exc).__name__))
    settings = Path(os.environ.get('HUMANIFY_SETTINGS', str(Path.home() / '.claude/settings.json')))
    try:
        config = read_json(settings)
        hooks = config.get('hooks', {})
        for event in ('SessionStart', 'UserPromptSubmit', 'SessionEnd'):
            found = any('humanify.sh' in str(hook.get('command', ''))
                        for group in hooks.get(event, []) for hook in group.get('hooks', [])
                        if hook.get('type') == 'command')
            print('{}: {}'.format(event, 'configurado' if found else 'falta humanify.sh'))
    except (OSError, ValueError, TypeError, AttributeError):
        print('Hooks en settings: en duda (no se pudo leer la configuración)')
    scripts = ['hooks/humanify.sh', 'hooks/arranque.sh', 'hooks/router.sh', 'hooks/cierre.sh',
               'herramientas/anota.sh', 'herramientas/estado.sh']
    missing = [name for name in scripts if not os.access(str(HOME_DIR / name), os.X_OK)]
    print('Scripts ejecutables: ' + ('sí' if not missing else 'no: ' + ', '.join(missing)))
    try:
        directory = reg_path()
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=str(directory)):
            pass
        print('registro/ escribible: sí')
    except (OSError, ValueError):
        print('registro/ escribible: no')
    if args == ['--atendidos']:
        path = reg_path('errores-vistos')
        path.write_text(now() + '\n', encoding='utf-8')
    count, lines, _ = pending_errors()
    print('Errores de hooks pendientes: ' + str(count))
    for line in lines:
        print(line)
    groups = {}
    for path in sorted(reg_path('sesiones').glob('*.json')):
        item = read_json(path)
        if unclosed(item):
            groups.setdefault(item.get('raiz') or 'tarea suelta', []).append(item['id'][:8])
    print('Sesiones sin cerrar: ' + ('ninguna' if not groups else ''))
    for root, ids in sorted(groups.items()):
        print('  {}: {} ({})'.format(root, len(ids), ', '.join(ids)))


def dispatch(event):
    try:
        if not active():
            return
    except (OSError, UnicodeError):
        return
    started = time.monotonic()
    sid, status, message, output = '', 'ok', '', ''
    temporary = None
    try:
        targets = {'arranque': 'arranque.sh', 'compactado': 'arranque.sh', 'mensaje': 'router.sh', 'cierre': 'cierre.sh',
                   'delegacion': 'delegacion.sh', 'subagente': 'subagente.sh', 'guardia': 'guardia.sh'}
        if event not in targets:
            raise ValueError('evento desconocido')
        payload = sys.stdin.buffer.read()
        data = json.loads(payload)
        sid = identifier(data.get('session_id'))
        if event == 'compactado':
            data['source'] = 'compact'
            payload = json.dumps(data).encode('utf-8')
        reg_path().mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.resultado-', dir=str(REG))
        os.close(fd)
        env = dict(os.environ, HUMANIFY_RESULT=temporary)
        process = subprocess.Popen(['/bin/bash', str(HOME_DIR / 'hooks' / targets[event])],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env=env, start_new_session=True)
        try:
            stdout, stderr = process.communicate(payload, timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise ValueError('tiempo excedido (3 s)')
        detail = Path(temporary).read_text(encoding='utf-8')
        if process.returncode or stderr or detail != 'ok':
            raise ValueError((detail if detail != 'ok' else '') or
                             'falló el script o no confirmó ejecución (código {})'.format(process.returncode))
        output = stdout.decode('utf-8')
        if len(output) > 10000:
            raise ValueError('salida mayor de 10000 caracteres')
    except Exception as exc:
        status, message, output = 'error', str(exc), ''
    finally:
        if temporary:
            Path(temporary).unlink(missing_ok=True)
    log_event(event, sid, status, round((time.monotonic() - started) * 1000), message)
    sys.stdout.write(output)


def alarm_handler(signum, frame):
    raise TimeoutError('hook excedió su presupuesto de 0.8 s')


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ''
    if mode == 'dispatch':
        try:
            dispatch(sys.argv[2] if len(sys.argv) > 2 else '')
        except Exception:
            pass
        return 0
    if mode in ('arranque', 'router', 'cierre', 'delegacion', 'subagente', 'guardia'):
        started = time.monotonic()
        data, output = {}, ''
        try:
            signal.signal(signal.SIGALRM, alarm_handler)
            signal.setitimer(signal.ITIMER_REAL, 0.8)
            data = json.load(sys.stdin)
            output = {'arranque': start_hook, 'router': router_hook, 'cierre': end_hook, 'delegacion': delegation_hook,
                      'subagente': subagent_hook, 'guardia': guard_hook}[mode](data)
        except Exception as exc:
            failure('{}: {}'.format(type(exc).__name__, exc))
            output = ''
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
        try:
            result = os.environ.get('HUMANIFY_RESULT')
            if result:
                reg_path(*Path(result).relative_to(REG).parts).write_text('; '.join(ERRORS) if ERRORS else 'ok', encoding='utf-8')
            elif ERRORS:
                log_event(mode, data.get('session_id', ''), 'error', round((time.monotonic() - started) * 1000), '; '.join(ERRORS))
        except Exception:
            pass
        sys.stdout.write(output)
        return 0
    try:
        if mode == 'anota':
            anota(sys.argv[2:])
        elif mode == 'estado':
            status(sys.argv[2:])
        else:
            raise ValueError('modo desconocido')
        return 0
    except Exception as exc:
        if mode == 'anota' and sys.argv[2:3] == ['candidato']:
            print('rechazado: ' + ' '.join(str(exc).split()))
        else:
            print('Humanify: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
