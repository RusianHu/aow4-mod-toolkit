"""Isolated official-editor sessions; launch is not an import/validation API."""
from datetime import datetime, timezone
from contextlib import contextmanager
import ctypes
import json
import os
from pathlib import Path
import shutil
import subprocess

from .core import FormatError, read_acp, read_rpk
from .filesystem import inventory, file_hash, new_directory, save_record, reject_reparse
from . import native, projects, content_libraries


FORMAT = 'aow4mod-editor-session/1'
EDITORS = {
    'content': ('ContentEd.exe', '5799adc21f15c7fe1ab7ded0a1cc457515a40c690ea9554c0e83914122ec61ef'),
    'resource': ('ResourceEd.exe', '60d53fd3852ae26fcb9822d78692d788b0334971582e85a776090e4af44efe88'),
    'level': ('LevelEd.exe', '86a818d2dfae2aa5b4b780bef2a49a4843e6805425e609498876a56372796c91'),
}
RECEIPT = 'editor-session.json'
LIMITS = ('Official GUI with a copied Mod context. No verified batch import/save, '
          'effective ACP merge, rendering or gameplay certification. Collection only stages files; '
          'run the normal native build/release checks afterward. Copied context is not an OS filesystem sandbox.')


def timestamp():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def session_lock(path):
    lock = Path(path).parent / 'editor-operation.lock'
    try:
        with lock.open('x', encoding='utf-8') as stream:
            stream.write(str(os.getpid()))
    except FileExistsError as exc:
        raise FormatError('Another or interrupted editor operation owns this session lock') from exc
    try:
        yield
    finally:
        lock.unlink()


def binding(game, editor):
    if editor not in EDITORS:
        raise FormatError('Unknown official editor')
    profile = native.profile_for(game)
    name, expected = EDITORS[editor]
    executable = Path(profile['game']) / name
    if not executable.is_file() or file_hash(executable) != expected:
        raise FormatError('Official editor hash does not match its reviewed command-line profile')
    return {'game': profile['game'], 'editor': editor, 'executable': str(executable),
            'executable_sha256': expected, 'dll_sha256': profile['dll_sha256']}


def checked_source(path):
    path = Path(path).resolve()
    if path.is_file() and path.suffix.lower() != '.acp':
        raise FormatError('Editor source must be a Mod directory or its ACP file')
    source = path.parent if path.is_file() else path
    files = inventory(source)
    acps = [key for key in files if '/' not in key and key.lower().endswith('.acp')]
    if len(acps) != 1:
        raise FormatError('Editor sessions require exactly one top-level ACP')
    meta, _ = read_acp(source / acps[0])
    if not meta.get('package_id'):
        raise FormatError('Editor session requires an explicit package UUID')
    return source, files, acps[0], meta['package_id']


def prepare(source, editor, game, output):
    source, files, acp, package_id = checked_source(source)
    tool = binding(game, editor)
    output = native.check_cache_directory(output, game, [source])
    if output in source.parents:
        raise FormatError('Editor session cannot contain its source tree')
    destination = output / 'Mods' / 'Working Mod'
    record = {'format': FORMAT, 'session': str(output), 'source': str(source),
              'source_files': files, 'acp': acp, 'package_id': package_id,
              'tool': tool, 'created_at': timestamp(), 'launch': None, 'scope': LIMITS}
    with new_directory(output, [source, game]) as stage:
        copied = stage / 'Mods' / destination.name
        shutil.copytree(source, copied)
        for name in ('roaming', 'local'):
            (stage / name).mkdir()
        if inventory(copied) != files or inventory(source) != files:
            raise FormatError('Source changed while preparing editor copy')
        save_record(stage / RECEIPT, record)
    return {'session': str(output / RECEIPT), 'mod': str(destination), 'editor': editor,
            'files': len(files), 'started': False, 'scope': LIMITS}


def load(path, check_tool=True, check_source=True):
    path = Path(path).absolute()
    reject_reparse(path)
    root = path.parent
    record = json.loads(path.read_text(encoding='utf-8'))
    if path.name != RECEIPT or record.get('format') != FORMAT or Path(record.get('session', '')).resolve() != root.resolve():
        raise FormatError('Invalid or moved editor session receipt')
    tool = record['tool']
    source = Path(record['source']).resolve()
    native.check_cache_directory(root, tool['game'], [source])
    if root.resolve() in source.parents:
        raise FormatError('Editor source cannot be inside its session')
    if check_tool and binding(tool['game'], tool['editor']) != tool:
        raise FormatError('Editor installation changed after preparation')
    if check_source:
        _, files, acp, identity = checked_source(source)
        if files != record['source_files'] or acp != record['acp'] or identity != record['package_id']:
            raise FormatError('Original source changed after session preparation')
    mod = root / 'Mods' / 'Working Mod'
    for folder in (root, root/'Mods', mod, root/'roaming', root/'local'):
        reject_reparse(folder)
    return path, record, mod


def process_info(pid):
    """Query process ownership only; no window enumeration, input or termination."""
    if os.name != 'nt':
        raise FormatError('Official editor process tracking requires Windows')
    if type(pid) is not int or not 0 < pid <= 0xffffffff:
        raise FormatError('Invalid editor process ID')
    from ctypes import wintypes as w
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.GetExitCodeProcess.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
    kernel.GetProcessTimes.argtypes = [w.HANDLE] + [ctypes.POINTER(w.FILETIME)] * 4
    kernel.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:
            return None
        raise FormatError('Cannot establish editor process identity')
    try:
        created, exited, system, user = [w.FILETIME() for _ in range(4)]
        code, capacity = w.DWORD(), w.DWORD(32768)
        name = ctypes.create_unicode_buffer(capacity.value)
        if (not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or
            not kernel.GetProcessTimes(handle, *[ctypes.byref(x) for x in (created, exited, system, user)]) or
            not kernel.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(capacity))):
            raise FormatError('Cannot read editor process identity')
        return {'pid': pid, 'executable': str(Path(name.value).resolve()),
                'creation_time': (created.dwHighDateTime << 32) | created.dwLowDateTime,
                'running': code.value == 259, 'exit_code': None if code.value == 259 else code.value}
    finally:
        kernel.CloseHandle(handle)


def observed(launch):
    if launch is None:
        return {'state': 'prepared'}
    if launch['state'] != 'started':
        return {'state': launch['state']}
    expected = launch['process']
    actual = process_info(expected['pid'])
    if actual is None:
        return {'state': 'exited', 'exit_code': None}
    if any(actual[key] != expected[key] for key in ('pid', 'executable', 'creation_time')):
        return {'state': 'pid_reused', 'exit_code': None}
    return {'state': 'running' if actual['running'] else 'exited', 'exit_code': actual['exit_code']}


def running_editors():
    from .loadout import running_hosts
    return running_hosts(names={row[0] for row in EDITORS.values()} | {'PackageManager.exe'})


def require_closed_editors():
    active = running_editors()
    if active:
        raise FormatError('Close active official editors before this operation: '+json.dumps(active))


def start(path, show=False):
    load(path)
    with session_lock(path):
        return _start(path, show)


def _start(path, show=False):
    path, record, mod = load(path)
    if record['launch'] is not None:
        raise FormatError('This session already attempted a launch; inspect it instead of retrying')
    if inventory(mod) != record['source_files']:
        raise FormatError('Prepared editor copy changed before first launch')
    if os.name != 'nt':
        raise FormatError('Official editor launch requires Windows')
    require_closed_editors()
    # Derive arguments from validated paths; never execute arguments stored in JSON.
    args = [record['tool']['executable'], '-modpath', str(mod.parent), '-mod', mod.name]
    record['launch'] = {'state': 'starting', 'at': timestamp(), 'show_requested': bool(show)}
    save_record(path, record)  # An interrupted launch remains unknown, never retried.
    environment = dict(os.environ, APPDATA=str(path.parent/'roaming'), LOCALAPPDATA=str(path.parent/'local'))
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 1 if show else 0
    process = None
    try:
        with (path.parent/'editor-stdout.log').open('xb') as stdout, (path.parent/'editor-stderr.log').open('xb') as stderr:
            process = subprocess.Popen(args, cwd=record['tool']['game'], env=environment, stdin=subprocess.DEVNULL,
                stdout=stdout, stderr=stderr, shell=False, startupinfo=startup,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        info = process_info(process.pid)
        if info is None or Path(info['executable']) != Path(record['tool']['executable']):
            raise FormatError('Launched editor exited before ownership could be established')
        record['launch'].update(state='started', process=info)
        save_record(path, record)
    except BaseException:
        record['launch']['state'] = 'unknown' if process is not None else 'launch_failed'
        if process is not None:
            record['launch']['unconfirmed_pid'] = process.pid
        save_record(path, record)
        raise
    return {'session': str(path), **observed(record['launch']), 'process': info, 'mod': str(mod), 'scope': LIMITS}


def changes(before, after):
    return {'added': sorted(set(after)-set(before)), 'removed': sorted(set(before)-set(after)),
            'modified': sorted(key for key in set(before)&set(after) if before[key] != after[key])}


def status(path, limit=20, offset=0):
    from .catalog import check_page
    check_page(limit, offset)
    path, record, mod = load(path, check_tool=False, check_source=False)
    delta = changes(record['source_files'], inventory(mod))
    flattened = [{'change': action, 'file': name} for action, names in delta.items() for name in names]
    return {'session': str(path), **observed(record['launch']), 'mod': str(mod),
            'original_source_unchanged': inventory(record['source']) == record['source_files'],
            'counts': {key: len(value) for key, value in delta.items()}, 'total': len(flattened),
            'changes': flattened[offset:offset+limit], 'limit': limit, 'offset': offset, 'scope': LIMITS}


def validate_result(mod, package_id):
    _, files, acp, identity = checked_source(mod)
    if identity != package_id:
        raise FormatError('Editor result changed the package UUID')
    result = projects.inspect_project(mod, include_unregistered=True)
    if any(issue['issue'] == 'resource_pack_parse_error' for issue in result['issues']):
        raise FormatError('Editor result contains a malformed resource pack')
    seen = set()
    for row in result['definitions']:
        rid = row['resource'].rid
        if rid in seen:
            raise FormatError('Editor result has duplicate local resource definitions')
        seen.add(rid)
    parsed = [acp]
    opaque = []
    for name in files:
        suffix = Path(name).suffix.lower()
        if suffix == '.clb':
            content_libraries.read(mod/name)
            parsed.append(name)
        elif suffix == '.rpk':
            read_rpk(mod/name)
            parsed.append(name)
        elif suffix in ('.tam', '.cam', '.srm', '.env', '.fbx'):
            opaque.append(name)
    return {'parsed_files': sorted(parsed), 'opaque_files': sorted(opaque),
            'resources': len(seen), 'issues': result['issues'], 'unregistered_rpk_files': result['unregistered_rpk_files'],
            'coverage': 'ACP/RPK and CLB structure only; no native effective-field or rendering claim.'}


def collect(path, output):
    load(path)
    with session_lock(path):
        return _collect(path, output)


def _collect(path, output):
    path, record, mod = load(path)
    state = observed(record['launch'])['state']
    if state not in ('prepared', 'exited', 'pid_reused', 'launch_failed'):
        raise FormatError('Editor must be confirmed closed before collecting its files')
    require_closed_editors()
    files = inventory(mod)
    validation = validate_result(mod, record['package_id'])
    output = native.check_cache_directory(output, record['tool']['game'], [path.parent, record['source']])
    if output in path.parent.parents or output in Path(record['source']).parents:
        raise FormatError('Collection output cannot contain its inputs')
    delta = changes(record['source_files'], files)
    with new_directory(output, [path.parent, record['source'], record['tool']['game']]) as stage:
        shutil.copytree(mod, stage, dirs_exist_ok=True)
        # Check again on both sides of the copy; no half-written result is committed.
        if inventory(stage) != files or inventory(mod) != files:
            raise FormatError('Editor output changed during collection')
        _, latest, _ = load(path)
        if latest != record or observed(record['launch'])['state'] not in ('prepared', 'exited', 'pid_reused', 'launch_failed'):
            raise FormatError('Editor session changed during collection')
        require_closed_editors()
    return {'session': str(path), 'output': str(output), 'editor_was_started': record['launch'] is not None,
            'process_state': state, 'changes': delta, 'validation': validation,
            'source_unchanged': True, 'scope': LIMITS}
