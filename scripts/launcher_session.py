"""Back up and observe one actual AoW4 official launcher session; never press Play."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

from aow4mod import launcher, loadout
from aow4mod.core import FormatError
from aow4mod.discovery import find_game, user_data
from aow4mod.filesystem import file_hash, inventory, new_directory, outside, save_record
import launcher_acceptance as acceptance
import launcher_runtime as runtime

FORMAT = 'aow4mod-launcher-session/1'


def inspect_inputs(game=None):
    loadout._idle(); game = find_game(game); data = user_data().resolve()
    settings_file = game / 'launcher-settings.json'
    settings = json.loads(settings_file.read_text(encoding='utf-8-sig'))
    if settings.get('gameId') != 'aow4' or settings.get('distPlatform') != 'steam' or settings.get('distPlatformModsEnabled') is not True:
        raise FormatError('Actual session requires unchanged AoW4 Steam launcher settings')
    declared = settings.get('gameDataPath')
    if declared != '%USER_DOCUMENTS%/Paradox Interactive/Age of Wonders 4' and (not isinstance(declared, str) or Path(declared).resolve() != data):
        raise FormatError('Actual launcher data path is not the reviewed user-data directory')
    binding = launcher.launcher_binding(); profile = launcher.binding_profile(binding)
    shared = runtime.shared_state(data.parent, profile)
    database = launcher.database_path(data / 'launcher-v2.sqlite')
    con = launcher.connect(database)
    try:
        mods = [dict(row) for row in con.execute('SELECT * FROM mods')]
        before_logical = launcher.logical_hash(con)
    finally: con.close()
    if len(mods) > 128: raise FormatError('Actual session supports at most 128 registered Mods')
    roots = {data / 'Mods'}
    for mod in mods:
        if mod.get('archivePath') or mod.get('gameRegistryId'): raise FormatError('Actual session supports existing loose-file Mods only')
        roots.add(launcher.resolved(mod['dirPath']))
    sources = [{'path': str(p), 'files': acceptance.bounded_inventory(p)} for p in sorted(roots)]
    if sum(len(s['files']) for s in sources) > 8192: raise FormatError('Too many protected Mod files')
    protected = [item for item in shared['files'] if Path(item['path']).parent != data]
    protected.append({'path': str(settings_file), 'sha256': file_hash(settings_file), 'size': settings_file.stat().st_size})
    mutable = [item for item in shared['files'] if Path(item['path']).parent == data]
    for folder in (Path(os.environ['APPDATA']) / 'Paradox Interactive/launcher-v2',):
        for name in ('userSettings.json', 'session.js'):
            path = folder / name
            if path.is_file(): mutable.append({'path': str(path), 'sha256': file_hash(path), 'size': path.stat().st_size})
    assets = {'path': str(game / 'Launcher'), 'files': acceptance.bounded_inventory(game / 'Launcher')}
    app = Path(binding['file']).parents[1]
    return {'format': FORMAT, 'game': str(game), 'data': str(data), 'application': binding,
            'host_files': inventory(app), 'signature': acceptance.signature(app / 'Paradox Launcher.exe'),
            'source_files': protected, 'source_mods': sources, 'source_assets': assets,
            'before_mutable_files': mutable, 'shared_histories': shared['histories'], 'before_logical_sha256': before_logical,
            'helpers': {str(p.name): file_hash(p) for p in (Path(__file__), Path(acceptance.__file__), Path(acceptance.__file__).with_suffix('.mjs'))},
            'scope': 'Actual user AppData, original game Steam settings and real launcher database. Official application writes are backed up; existing Mod files/other games are deny-write guarded. No Play/publish/subscribe/legal-accept action.'}


def prepare(output, game=None):
    record = inspect_inputs(game)
    protected = [Path(record['game']), Path(record['data']).parent, Path(record['application']['file']).parents[2],
                 *[s['path'] for s in record['source_mods']]]
    output = outside(output, protected)
    with new_directory(output, protected) as stage:
        original = stage / 'original'; original.mkdir()
        inputs = [*record['source_files'], *record['before_mutable_files']]
        for i, item in enumerate(inputs):
            destination = original / f'{i:04d}.bin'; runtime.copy_file(item['path'], destination)
            if file_hash(destination) != item['sha256']: raise FormatError('Session backup differs from original input')
        record['backup_files'] = inputs
        if inspect_inputs(record['game']) != {k: v for k, v in record.items() if k != 'backup_files'}:
            raise FormatError('Session inputs changed during backup')
        save_record(stage / 'session.json', record)
    return {'session': str(output / 'session.json'), 'protected_mod_roots': len(record['source_mods']),
            'shared_game_count': len(record['shared_histories']), 'scope': record['scope']}


def validate(path):
    path = Path(path).resolve(); record = json.loads(path.read_text(encoding='utf-8'))
    if record.get('format') != FORMAT: raise FormatError('Unsupported actual launcher session')
    if (path.parent / 'result.json').exists(): raise FormatError('Session already attempted; prepare a fresh session')
    current = inspect_inputs(record['game'])
    if current != {k: v for k, v in record.items() if k != 'backup_files'}:
        raise FormatError('Actual session inputs changed since preparation')
    if record['backup_files'] != [*current['source_files'], *current['before_mutable_files']]:
        raise FormatError('Session backup mapping changed')
    for i, item in enumerate(record['backup_files']):
        if file_hash(path.parent / 'original' / f'{i:04d}.bin') != item['sha256']: raise FormatError('Session backup changed')
    return path.parent, record


def preserved(record):
    try:
        return all(file_hash(p['path']) == p['sha256'] for p in record['source_files']) and all(
            inventory(s['path']) == s['files'] for s in [*record['source_mods'], record['source_assets']])
    except OSError: return False


def checkpoint(root, record):
    result = {'files': []}
    for i, before in enumerate(record['before_mutable_files']):
        path = Path(before['path']); copied = root / 'after' / f'{i:04d}.bin'
        if not path.is_file(): raise FormatError('Actual launcher removed a tracked mutable file')
        runtime.copy_file(path, copied)
        digest = file_hash(path)
        if file_hash(copied) != digest: raise FormatError('Session checkpoint changed during copy')
        result['files'].append({'path': str(path), 'sha256': digest, 'size': path.stat().st_size})
    result['inspection'] = launcher.inspect(Path(record['data']) / 'launcher-v2.sqlite')
    return result


def run(session, node=None):
    if os.name != 'nt': raise FormatError('Actual launcher sessions require Windows')
    node = node or shutil.which('node')
    if not node: raise FormatError('Node.js 22+ is required for renderer evidence')
    version = subprocess.run([node, '--version'], capture_output=True, text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
    if version.returncode or int(version.stdout.strip().lstrip('v').split('.')[0]) < 22: raise FormatError('Node.js 22+ is required')
    root, record = validate(session)
    result = {'format': FORMAT, 'session': str(Path(session).resolve()), 'scope': record['scope'], 'gameplay_tested': False}
    # A durable running marker prevents replay after a crash/unknown outcome.
    if (root / 'attempt.json').exists(): raise FormatError('Session outcome may be unknown; inspect existing attempt before preparing another')
    save_record(root / 'attempt.json', {'session_sha256': file_hash(session), 'state': 'starting'})
    try:
        with acceptance.guarded_desktop(record) as desktop:
            validate(session)
            result['phase'] = acceptance.phase(root, Path(record['application']['file']).parents[1], desktop, node,
                                               'official', live_game=Path(record['game']))
        result['exiting_processes_waited']=acceptance.wait_hosts_exit()
        loadout._idle()
        result['protected_inputs_unchanged'] = preserved(record)
        result['host_files_unchanged'] = inventory(Path(record['application']['file']).parents[1]) == record['host_files']
        result['checkpoint'] = checkpoint(root, record)
        result['pairing_supported'] = result['checkpoint']['inspection']['pairing_supported']
        result['passed'] = bool(result['phase']['healthy_ui'] and result['phase']['steam_api_initialized'] and
                                result['phase']['steam_app_id_1669000'] and result['protected_inputs_unchanged'] and
                                result['host_files_unchanged'] and result['pairing_supported'] and
                                result['checkpoint']['inspection']['configuration_matches_database'] is True)
    except (FormatError, OSError, ValueError, subprocess.SubprocessError) as error:
        result['error'] = str(error); result['passed'] = False
    finally:
        result['protected_inputs_unchanged'] = preserved(record)
        if not result['protected_inputs_unchanged']: result['passed'] = False
        if 'checkpoint' not in result:
            try:
                loadout._idle(); result['checkpoint'] = checkpoint(root, record)
            except (FormatError, OSError, ValueError) as error: result['checkpoint_error'] = str(error)
        save_record(root / 'result.json', result)
        save_record(root / 'attempt.json', {'session_sha256': file_hash(session), 'state': 'finished', 'passed': result.get('passed', False)})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__); sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare'); p.add_argument('--output', type=Path, required=True); p.add_argument('--game', type=Path)
    p = sub.add_parser('run'); p.add_argument('session', type=Path); p.add_argument('--node')
    args = parser.parse_args()
    try:
        result = prepare(args.output, args.game) if args.command == 'prepare' else run(args.session, args.node)
        if args.command == 'run':
            result = {k: result.get(k) for k in ('passed', 'error', 'pairing_supported', 'protected_inputs_unchanged', 'host_files_unchanged', 'scope')}
        print(json.dumps(result, ensure_ascii=False, indent=2)); return int(result.get('passed') is False)
    except (FormatError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False)); return 1


if __name__ == '__main__': sys.exit(main())
