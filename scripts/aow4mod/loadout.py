"""Reversible edits to the game's content_load.json, not the launcher's database."""
import ctypes as c
import json
import os
from pathlib import Path
import tempfile

from . import projects
from .core import FormatError, mod_file, read_acp, sha
from .filesystem import file_hash, inventory, new_directory, outside, reject_reparse, save_record

FORMAT = 'aow4mod-loadout-plan/1'
RECEIPT = 'aow4mod-loadout-receipt/1'
COVERAGE = ('Game launch configuration only. Launcher regeneration may replace it; '
            'launcher UI/database state, engine mounting, DLC eligibility and gameplay are not verified.')


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise FormatError('Duplicate JSON key in launch configuration')
        result[key] = value
    return result


def _read(path):
    path = Path(path).resolve()
    if path.name.casefold() != 'content_load.json':
        raise FormatError('Expected an existing content_load.json launch configuration')
    reject_reparse(path)
    if path.stat().st_size > 1024*1024: raise FormatError('Launch configuration exceeds 1 MiB')
    raw = path.read_bytes()
    data = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_unique_object)
    if not isinstance(data, dict) or not isinstance(data.get('enabledMods'), list):
        raise FormatError('Expected enabledMods in launch configuration')
    for item in data['enabledMods']:
        if not isinstance(item, dict) or not isinstance(item.get('path'), str) or not item['path']:
            raise FormatError('Invalid enabledMods entry')
    return path, raw, data


def _path(value, parent):
    path = Path(value)
    return (path if path.is_absolute() else parent/path).resolve()


def _identity(path):
    acp = mod_file(path); metadata, _ = read_acp(acp)
    if not metadata.get('package_id'): raise FormatError('Selected Mod has no package UUID')
    return {'path': str(path), 'acp': str(acp), 'sha256': metadata['sha256'],
            'package_id': metadata['package_id'], 'name': metadata['name']}


def make_plan(path, enable=(), disable=(), position=None, game=None):
    path, raw, before = _read(path)
    game = Path(game).resolve() if game else None
    if game: outside(path, [game])
    additions = [Path(p).resolve() for p in enable]
    removals = [Path(p).resolve() for p in disable]
    if len(set(additions)) != len(additions) or len(set(removals)) != len(removals):
        raise FormatError('Duplicate requested Mod paths')
    if set(additions) & set(removals): raise FormatError('Cannot enable and disable the same path')
    existing = [_path(item['path'], path.parent) for item in before['enabledMods']]
    if len(set(existing)) != len(existing): raise FormatError('Duplicate enabled Mod paths need explicit repair first')
    if any(p not in existing for p in removals): raise FormatError('Requested disabled path is not enabled')
    if any(p in existing for p in additions): raise FormatError('Requested Mod is already enabled')
    entries = [entry for entry, p in zip(before['enabledMods'], existing) if p not in removals]
    if position is None: position = len(entries)
    if type(position) is not int or not 0 <= position <= len(entries): raise FormatError('Insertion position is out of bounds')
    if not additions and position != len(entries): raise FormatError('Insertion position requires newly enabled Mods')
    protected = [game] if game else []
    for p in additions: outside(p, protected)
    new_inputs = []
    for p in additions:
        project = projects.inspect_project(p)
        if project['issues']: raise FormatError('Selected Mod declarations need repair before enabling')
        new_inputs.append({'path': str(p), 'files': inventory(p)})
    entries[position:position] = [{'path': str(p)} for p in additions]
    identities = [_identity(_path(item['path'], path.parent)) for item in entries]
    uuids = [x['package_id'] for x in identities]
    if len(set(uuids)) != len(uuids):
        raise FormatError('Duplicate package UUID: disable the other local/Workshop copy explicitly')
    after = dict(before); after['enabledMods'] = entries
    if after == before: raise FormatError('Loadout plan makes no change')
    encoded = (json.dumps(after, ensure_ascii=False, separators=(',', ':'))+'\n').encode('utf-8')
    if file_hash(path) != sha(raw): raise FormatError('Launch configuration changed during planning')
    return {'format': FORMAT, 'file': str(path), 'game': str(game) if game else None,
            'before_sha256': sha(raw), 'after_sha256': sha(encoded), 'after': after,
            'enable': [str(p) for p in additions], 'disable': [str(p) for p in removals],
            'position': position, 'selected_mods': identities, 'new_mod_inputs': new_inputs,
            'coverage': COVERAGE, 'gameplay_tested': False}


def running_hosts(names=None):
    """Avoid editing a generated file while a known game/launcher writer is active."""
    if os.name != 'nt': return []
    if names is None:names={'aow4.exe', 'dowser.exe', 'paradox launcher.exe', 'bootstrapper-v2.exe'}
    names={name.casefold() for name in names}
    from ctypes import wintypes as w
    class Entry(c.Structure):
        _fields_ = [('dwSize', w.DWORD), ('cntUsage', w.DWORD), ('th32ProcessID', w.DWORD),
                    ('th32DefaultHeapID', c.c_size_t), ('th32ModuleID', w.DWORD), ('cntThreads', w.DWORD),
                    ('th32ParentProcessID', w.DWORD), ('pcPriClassBase', w.LONG), ('dwFlags', w.DWORD),
                    ('szExeFile', w.WCHAR*260)]
    kernel = c.WinDLL('kernel32', use_last_error=True)
    snapshot = kernel.CreateToolhelp32Snapshot
    snapshot.argtypes = [w.DWORD, w.DWORD]; snapshot.restype = w.HANDLE
    first, following = kernel.Process32FirstW, kernel.Process32NextW
    for function in (first, following):
        function.argtypes = [w.HANDLE, c.POINTER(Entry)]; function.restype = w.BOOL
    close = kernel.CloseHandle; close.argtypes = [w.HANDLE]; close.restype = w.BOOL
    open_process=kernel.OpenProcess;open_process.argtypes=[w.DWORD,w.BOOL,w.DWORD];open_process.restype=w.HANDLE
    wait=kernel.WaitForSingleObject;wait.argtypes=[w.HANDLE,w.DWORD];wait.restype=w.DWORD
    handle = snapshot(2, 0)
    if handle == c.c_void_p(-1).value: raise FormatError('Cannot inspect active game/launcher processes')
    entry = Entry(); entry.dwSize = c.sizeof(entry); result = []
    try:
        success = first(handle, c.byref(entry))
        if not success: raise FormatError('Cannot enumerate active processes')
        while success:
            if entry.szExeFile.casefold() in names:
                process=open_process(0x00100000,False,entry.th32ProcessID)
                finished=False
                if process:
                    try:finished=wait(process,0)==0
                    finally:close(process)
                elif c.get_last_error()==87:finished=True  # process disappeared after the snapshot
                if not finished:result.append({'name': entry.szExeFile, 'pid': entry.th32ProcessID})
            success = following(handle, c.byref(entry))
        if c.get_last_error() != 18: raise FormatError('Process enumeration ended unexpectedly')
    finally: close(handle)
    return result


def _idle():
    hosts = running_hosts()
    if hosts: raise FormatError('Close active game/launcher before changing launch configuration: '+json.dumps(hosts))


def _replace(path, data, expected):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.aow4mod-loadout-', delete=False) as handle:
            temporary = Path(handle.name); handle.write(data); handle.flush(); os.fsync(handle.fileno())
        if file_hash(path) != expected: raise FormatError('Launch configuration changed before replacement')
        os.replace(temporary, path)
        if file_hash(path) != sha(data): raise FormatError('Launch configuration changed after replacement; inspect receipt')
    finally:
        if temporary and temporary.exists(): temporary.unlink()


def apply_plan(plan, backup):
    if not isinstance(plan, dict) or plan.get('format') != FORMAT: raise FormatError('Unsupported loadout plan')
    _idle()
    if file_hash(plan['file']) != plan['before_sha256']: raise FormatError('Launch configuration changed since planning')
    expected = make_plan(plan['file'], plan['enable'], plan['disable'], plan['position'], plan['game'])
    if expected != plan: raise FormatError('Loadout plan or selected Mod input changed')
    path = Path(plan['file']); raw = path.read_bytes()
    if sha(raw) != plan['before_sha256']: raise FormatError('Launch configuration changed before backup')
    backup = outside(backup, [path, *[x['path'] for x in plan['selected_mods']], *([plan['game']] if plan['game'] else [])])
    record = {'format': RECEIPT, 'state': 'prepared', 'file': str(path), 'backup': str(backup/'original.bin'),
              'before_sha256': plan['before_sha256'], 'after_sha256': plan['after_sha256'],
              'coverage': COVERAGE}
    with new_directory(backup, [path]) as staged:
        (staged/'original.bin').write_bytes(raw)
        save_record(staged/'plan.json', plan); save_record(staged/'receipt.json', record)
    _idle()
    _replace(path, (json.dumps(plan['after'], ensure_ascii=False, separators=(',', ':'))+'\n').encode('utf-8'), plan['before_sha256'])
    record['state'] = 'written'; save_record(backup/'receipt.json', record)
    return {'receipt': str(backup/'receipt.json'), **status(backup/'receipt.json')}


def status(receipt):
    receipt = Path(receipt).resolve(); record = json.loads(receipt.read_text(encoding='utf-8'))
    if record.get('format') != RECEIPT: raise FormatError('Unsupported loadout receipt')
    backup = Path(record['backup']).resolve()
    if backup != receipt.parent/'original.bin' or file_hash(backup) != record['before_sha256']:
        raise FormatError('Launch configuration backup changed')
    plan = json.loads((receipt.parent/'plan.json').read_text(encoding='utf-8'))
    if any(record.get(k) != plan.get(k) for k in ('file', 'before_sha256', 'after_sha256')):
        raise FormatError('Loadout receipt differs from its plan')
    encoded = (json.dumps(plan['after'], ensure_ascii=False, separators=(',', ':'))+'\n').encode('utf-8')
    if plan.get('format') != FORMAT or sha(encoded) != record['after_sha256']:
        raise FormatError('Loadout desired state differs from its bound hash')
    path = Path(record['file'])
    if path.name.casefold() != 'content_load.json': raise FormatError('Unexpected launch configuration path')
    if plan.get('game'): outside(path, [plan['game']])
    if path.exists(): reject_reparse(path)
    current = file_hash(path) if path.is_file() else None
    observed = 'original' if current == record['before_sha256'] else 'applied' if current == record['after_sha256'] else 'diverged'
    return {'observed_state': observed, 'file': str(path), 'sha256': current, 'receipt_state': record['state'], 'coverage': COVERAGE}


def rollback(receipt):
    _idle(); observed = status(receipt)
    if observed['observed_state'] != 'applied': raise FormatError('Launch configuration no longer matches the applied state')
    receipt = Path(receipt).resolve(); record = json.loads(receipt.read_text(encoding='utf-8'))
    _replace(Path(record['file']), Path(record['backup']).read_bytes(), record['after_sha256'])
    record['state'] = 'rolled_back'; save_record(receipt, record)
    return status(receipt)
