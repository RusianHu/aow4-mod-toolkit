"""Isolated official-launcher acceptance. Run --help; never migrates user data."""
from contextlib import contextmanager
import argparse
import ctypes as c
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import time

from aow4mod import launcher, loadout, projects
from aow4mod.core import FormatError
from aow4mod.filesystem import file_hash, inventory, new_directory, outside, save_record
from aow4mod.session_evidence import renderer_healthy

FORMAT = 'aow4mod-launcher-acceptance/1'
MAX_FILES = 4096
MAX_BYTES = 2 * 1024 ** 3


def bounded_inventory(path):
    files = inventory(path)
    if len(files) > MAX_FILES or sum(f['size'] for f in files.values()) > MAX_BYTES:
        raise FormatError('Acceptance input exceeds 4096 files / 2 GiB')
    return files


def signature(executable):
    """Use a literal parameter, never interpolate paths into PowerShell code."""
    script = "$ErrorActionPreference='Stop';$s=Get-AuthenticodeSignature -LiteralPath $env:AOW4_ACCEPTANCE_EXE;@{status=[string]$s.Status;subject=$s.SignerCertificate.Subject}|ConvertTo-Json -Compress"
    env = dict(os.environ, AOW4_ACCEPTANCE_EXE=str(executable))
    # A PowerShell 7 parent can export module paths incompatible with Windows
    # PowerShell 5. Let the selected host populate its own built-in module paths.
    env.pop('PSModulePath', None)
    host = shutil.which('pwsh') or 'powershell.exe'
    result = subprocess.run([host, '-NoProfile', '-NonInteractive', '-Command', script],
                            env=env, capture_output=True, text=True, timeout=30,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise FormatError('Cannot check launcher Authenticode signature')
    record = json.loads(result.stdout)
    if record.get('status') != 'Valid' or 'Paradox Interactive' not in (record.get('subject') or ''):
        raise FormatError('Expected a valid Paradox Interactive launcher signature')
    return record


def prepare(database, game, asar, output, probe_mod=None):
    loadout._idle()
    database = launcher.database_path(database)
    game = Path(game).resolve()
    binding = launcher.launcher_binding(asar)
    app = Path(binding['file']).parents[1]
    executable = app / 'Paradox Launcher.exe'
    signed = signature(executable)
    configuration, raw, config = loadout._read(database.parent / 'content_load.json')
    if config.get('enabledUGC'): raise FormatError('UGC fixture redirection is not reviewed')
    settings = json.loads((game / 'launcher-settings.json').read_text(encoding='utf-8-sig'))
    if settings.get('gameId') != 'aow4': raise FormatError('Expected AoW4 launcher settings')
    if settings.get('modPath') != 'Mods': raise FormatError('Only the local Mods subdirectory is reviewed')
    for key in ('themeFile', 'dlcPath', 'ingameSettingsPath', 'ingameSettingsLayoutPath'):
        if key in settings:
            p = Path(settings[key])
            if p.is_absolute() or '..' in p.parts or not p.parts or p.parts[0].casefold() != 'launcher':
                raise FormatError('Unreviewed launcher asset path: ' + key)
    inputs = [{'path': str(database), 'sha256': file_hash(database)},
              {'path': str(configuration), 'sha256': file_hash(configuration)},
              {'path': str(game / 'launcher-settings.json'), 'sha256': file_hash(game / 'launcher-settings.json')}]
    con = launcher.connect(database)
    try:
        rows = [dict(r) for r in con.execute('SELECT * FROM mods')]
        migrations = [r[0] for r in con.execute('SELECT name FROM knex_migrations ORDER BY id')]
    finally: con.close()
    if len(rows) > 128: raise FormatError('Acceptance supports at most 128 registered Mods')
    sources = []
    for row in rows:
        if row.get('archivePath') or row.get('gameRegistryId'):
            raise FormatError('Acceptance requires loose-file Mod records')
        path = launcher.resolved(row['dirPath'])
        if path not in sources: sources.append(path)
    if probe_mod:
        probe_mod = Path(probe_mod).resolve()
        if projects.inspect_project(probe_mod)['issues']: raise FormatError('Probe Mod declarations require repair')
        if probe_mod in sources: raise FormatError('Use a separate unregistered probe Mod')
        sources.append(probe_mod)
    sources = [{'path': str(p), 'files': bounded_inventory(p), 'copy': 'Mod-' + str(i)} for i, p in enumerate(sources)]
    if sum(len(s['files']) for s in sources) > MAX_FILES or sum(f['size'] for s in sources for f in s['files'].values()) > MAX_BYTES:
        raise FormatError('Combined Mod inputs exceed acceptance bounds')
    assets = bounded_inventory(game / 'Launcher')
    final = outside(output, [database.parent, game, app, *[s['path'] for s in sources]])
    mapping = {s['path']: str(final / 'data/Mods' / s['copy']) for s in sources}
    for entry in config['enabledMods']:
        p = str(loadout._path(entry['path'], configuration.parent))
        if p not in mapping: raise FormatError('Enabled Mod is not registered in the copied database')
        entry['path'] = mapping[p]
    host_files = inventory(app)
    with new_directory(final, [database.parent, game, app, *[s['path'] for s in sources]]) as stage:
        data = stage / 'data'; (data / 'Mods').mkdir(parents=True)
        shutil.copyfile(database, data / database.name)
        for source in sources:
            destination = data / 'Mods' / source['copy']
            shutil.copytree(source['path'], destination)
            if inventory(destination) != source['files'] or inventory(source['path']) != source['files']:
                raise FormatError('Mod input changed while copying')
        copied = sqlite3.connect(data / database.name)
        try:
            for row in rows:
                copied.execute('UPDATE mods SET dirPath=?,thumbnailPath=NULL,repositoryPath=NULL WHERE id=?',
                               (mapping[str(launcher.resolved(row['dirPath']))], row['id']))
            copied.commit()
        finally: copied.close()
        save_record(data / 'content_load.json', config)
        fixture_game = stage / 'game'; fixture_game.mkdir()
        shutil.copytree(game / 'Launcher', fixture_game / 'Launcher')
        if inventory(fixture_game / 'Launcher') != assets: raise FormatError('Launcher assets changed during copy')
        settings.update(gameDataPath=str(final / 'data'), exePath='CLI-DO-NOT-START-GAME.exe', exeArgs=[],
                        alternativeExecutables=[], distPlatform='gog', distPlatformModsEnabled=False)
        save_record(fixture_game / 'launcher-settings.json', settings)
        record = {'format': FORMAT, 'root': str(final), 'application': binding, 'host_files': host_files,
                  'signature': signed, 'source_files': inputs, 'source_mods': sources,
                  'source_assets': {'path': str(game / 'Launcher'), 'files': assets},
                  'original_migrations': migrations, 'initial_files': inventory(data),
                  'game_files': inventory(fixture_game), 'probe': mapping[str(probe_mod)] if probe_mod else None,
                  'scope': 'Copied data only; GOG/offline fixture, no Steam synchronization or game launch. Not production pairing certification.'}
        for item in inputs:
            if file_hash(item['path']) != item['sha256']: raise FormatError('Source changed during preparation')
        save_record(stage / 'fixture.json', record)
    return {'fixture': str(final / 'fixture.json'), 'copied_mods': len(sources),
            'source_migrations': len(migrations), 'probe': record['probe'], 'scope': record['scope']}


def read_fixture(path):
    path = Path(path).resolve()
    record = json.loads(path.read_text(encoding='utf-8'))
    if record.get('format') != FORMAT or Path(record['root']).resolve() != path.parent:
        raise FormatError('Invalid or moved acceptance fixture')
    root = path.parent
    if inventory(root / 'data') != record['initial_files'] or inventory(root / 'game') != record['game_files']:
        raise FormatError('Acceptance fixture changed before execution')
    if (root / 'result.json').exists(): raise FormatError('Acceptance already attempted; prepare a new fixture')
    launcher.check_binding(record['application'])
    app = Path(record['application']['file']).parents[1]
    if inventory(app) != record['host_files']: raise FormatError('Launcher host files changed')
    for item in record['source_files']:
        if file_hash(item['path']) != item['sha256']: raise FormatError('Original input changed; prepare again')
    for source in [*record['source_mods'], record['source_assets']]:
        if inventory(source['path']) != source['files']: raise FormatError('Original input tree changed; prepare again')
    # Revalidate path redirection independently of caller-editable inventory hashes.
    con = launcher.connect(root / 'data/launcher-v2.sqlite')
    try:
        for row in con.execute('SELECT dirPath,thumbnailPath,repositoryPath,archivePath FROM mods'):
            p = launcher.resolved(row[0])
            if p.parent != root / 'data/Mods' or not p.is_dir() or any(row[1:]):
                raise FormatError('Fixture Mod path is not isolated')
    finally: con.close()
    _, _, config = loadout._read(root / 'data/content_load.json')
    if config.get('enabledUGC'): raise FormatError('Fixture UGC is not isolated')
    for item in config['enabledMods']:
        if launcher.resolved(item['path']).parent != root / 'data/Mods': raise FormatError('Fixture enabled path escapes')
    settings = json.loads((root / 'game/launcher-settings.json').read_text(encoding='utf-8'))
    if settings.get('gameId') != 'aow4' or settings.get('modPath') != 'Mods' or settings.get('gameDataPath') != str(root / 'data') or settings.get('exePath') != 'CLI-DO-NOT-START-GAME.exe' or settings.get('exeArgs') or settings.get('alternativeExecutables') or settings.get('distPlatform') != 'gog' or settings.get('distPlatformModsEnabled') is not False or (root / 'game' / settings['exePath']).exists():
        raise FormatError('Fixture game/platform boundary changed')
    for key in ('themeFile', 'dlcPath', 'ingameSettingsPath', 'ingameSettingsLayoutPath'):
        if key in settings:
            p = Path(settings[key])
            if p.is_absolute() or '..' in p.parts or not p.parts or p.parts[0].casefold() != 'launcher':
                raise FormatError('Fixture launcher asset path escapes')
    return root, record, app


@contextmanager
def guarded_desktop(record):
    from ctypes import wintypes as w
    kernel = c.WinDLL('kernel32', use_last_error=True); user = c.WinDLL('user32', use_last_error=True)
    kernel.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, c.c_void_p, w.DWORD, w.DWORD, c.c_void_p]
    kernel.CreateFileW.restype = w.HANDLE; kernel.CloseHandle.argtypes = [w.HANDLE]
    user.CreateDesktopW.argtypes = [w.LPCWSTR, w.LPCWSTR, c.c_void_p, w.DWORD, w.DWORD, c.c_void_p]
    user.CreateDesktopW.restype = w.HANDLE
    user.GetThreadDesktop.argtypes = [w.DWORD]; user.GetThreadDesktop.restype = w.HANDLE
    user.SetThreadDesktop.argtypes = [w.HANDLE]; user.CloseDesktop.argtypes = [w.HANDLE]
    original = user.GetThreadDesktop(kernel.GetCurrentThreadId()); handles = []; desktop = None
    try:
        paths = {item['path'] for item in record['source_files']}
        for source in [*record['source_mods'], record['source_assets']]:
            paths.update(str(Path(source['path']) / name) for name in source['files'])
        for path in sorted(paths):
            handle = kernel.CreateFileW(path, 0x80000000, 1, None, 3, 0x80, None)
            if handle in (None, c.c_void_p(-1).value): raise c.WinError(c.get_last_error())
            handles.append(handle)
        name = 'Aow4CliAcceptance' + str(os.getpid())
        desktop = user.CreateDesktopW(name, None, None, 0, 0x01ff, None)
        if not desktop or not user.SetThreadDesktop(desktop): raise c.WinError(c.get_last_error())
        yield 'winsta0\\' + name
    finally:
        if desktop:
            user.SetThreadDesktop(original); user.CloseDesktop(desktop)
        for handle in handles: kernel.CloseHandle(handle)


def wait_hosts_exit(timeout=5):
    """Wait only on currently observed process handles; never restart or kill them."""
    if os.name!='nt':loadout._idle();return []
    from ctypes import wintypes as w
    kernel=c.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];kernel.OpenProcess.restype=w.HANDLE
    kernel.WaitForSingleObject.argtypes=[w.HANDLE,w.DWORD];kernel.WaitForSingleObject.restype=w.DWORD
    kernel.CloseHandle.argtypes=[w.HANDLE]
    deadline=time.monotonic()+timeout;observed=[]
    while True:
        hosts=loadout.running_hosts()
        if not hosts:return observed
        for host in hosts:
            handle=kernel.OpenProcess(0x00100000,False,host['pid'])
            if not handle:
                if c.get_last_error()==87:continue
                raise FormatError('Cannot verify exiting launcher process: '+str(host['pid']))
            try:
                remaining=max(0,int((deadline-time.monotonic())*1000))
                if kernel.WaitForSingleObject(handle,remaining)!=0:
                    raise FormatError('Observed launcher/game process remains active: '+str(host['pid']))
                observed.append(host['pid'])
            finally:kernel.CloseHandle(handle)


def launch_inputs(root, app, port, live_game=None):
    env = dict(os.environ)
    for key in ('SteamAppId', 'SteamGameId', 'ELECTRON_RUN_AS_NODE'): env.pop(key, None)
    if live_game is None:
        for key, subdir in (('APPDATA', 'roaming'), ('LOCALAPPDATA', 'local')):
            dest = root / subdir; dest.mkdir(exist_ok=True); env[key] = str(dest)
    else:
        env.update(SteamAppId='1669000', SteamGameId='1669000')
    env.update(HTTP_PROXY='http://127.0.0.1:9', HTTPS_PROXY='http://127.0.0.1:9', ALL_PROXY='http://127.0.0.1:9')
    args = [str(app / 'Paradox Launcher.exe'), '--pdxlGameDir', str(live_game or root / 'game'), '--pdxlEnableTelemetry=false',
            '--pdxlDisableHardwareAcceleration', '--pdxlDisableSteamSearch',
            '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=' + str(port)]
    if live_game is not None: args.append('--pdxlSteamAppIdOverride=1669000')
    return env, args


def phase(root, app, desktop, node, name, live_game=None):
    from ctypes import wintypes as w
    folder = root / name; folder.mkdir()
    with socket.socket() as sock: sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    env, args = launch_inputs(root, app, port, live_game)
    startup = subprocess.STARTUPINFO(); startup.lpDesktop = desktop
    process = None
    try:
        with (folder / 'stdout.log').open('w', encoding='utf-8') as out, (folder / 'stderr.log').open('w', encoding='utf-8') as err:
            process = subprocess.Popen(args, cwd=app, env=env, stdout=out, stderr=err,
                                       creationflags=subprocess.CREATE_NO_WINDOW, startupinfo=startup)
            probe = subprocess.run([node, str(Path(__file__).with_suffix('.mjs')), str(port), str(folder / 'renderer.json')],
                                   capture_output=True, text=True, timeout=35, creationflags=subprocess.CREATE_NO_WINDOW)
            if probe.returncode: raise FormatError('Renderer probe failed; inspect isolated renderer.json')
    finally:
        if process and process.poll() is None:
            user = c.WinDLL('user32', use_last_error=True)
            user.GetWindowThreadProcessId.argtypes = [w.HWND, c.POINTER(w.DWORD)]
            user.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
            callback_type = c.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
            @callback_type
            def close(hwnd, _):
                pid = w.DWORD(); user.GetWindowThreadProcessId(hwnd, c.byref(pid))
                if pid.value == process.pid: user.PostMessageW(hwnd, 0x10, 0, 0)
                return True
            user.EnumWindows(close, 0)
            try: process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                subprocess.run(['taskkill.exe', '/PID', str(process.pid), '/T', '/F'], capture_output=True,
                               timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
                process.wait(timeout=5)
    observation = json.loads((folder / 'renderer.json').read_text(encoding='utf-8'))
    logs = [(folder / name).read_text(encoding='utf-8', errors='replace') for name in ('stdout.log', 'stderr.log')]
    healthy = renderer_healthy(observation, logs)
    return {'healthy_ui': healthy, 'process_id':process.pid,'process_exit': process.returncode, 'renderer': str(folder / 'renderer.json'),
            'observed_text': observation.get('last_text', ''), 'log_files': [str(folder / n) for n in ('stdout.log', 'stderr.log')],
            'steam_api_initialized': any('Steam API initialized successfully' in s for s in logs),
            'steam_app_id_1669000': any('Initializing Steam API with App ID: 1669000' in s for s in logs),
            'profile_context': 'actual-user-steam' if live_game is not None else 'isolated-gog'}


def run(fixture, node=None):
    if os.name != 'nt': raise FormatError('Official launcher acceptance requires Windows')
    node = node or shutil.which('node')
    if not node: raise FormatError('Node.js 22+ is required for renderer evidence')
    check = subprocess.run([node, '--version'], capture_output=True, text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
    if check.returncode or int(check.stdout.strip().lstrip('v').split('.')[0]) < 22: raise FormatError('Node.js 22+ is required')
    loadout._idle()
    root, record, app = read_fixture(fixture)
    signature(app / 'Paradox Launcher.exe')
    result = {'format': FORMAT, 'fixture': str(Path(fixture).resolve()), 'scope': record['scope'],
              'production_pairing_verified': False, 'gameplay_tested': False, 'phases': []}
    try:
        with guarded_desktop(record) as desktop:
            # Recheck after deny-write handles have been acquired.
            read_fixture(fixture)
            result['phases'].append(phase(root, app, desktop, node, 'first'))
            inspected = launcher.inspect(root / 'data/launcher-v2.sqlite', launcher_asar=record['application']['file'])
            result['migrations_after'] = inspected['migrations']
            if not inspected['pairing_supported'] or not result['phases'][0]['healthy_ui']:
                raise FormatError('Copied application/database or renderer did not pass')
            if record['probe']:
                plan = launcher.make_plan(root / 'data/launcher-v2.sqlite', enable=[Path(record['probe'])],
                                          launcher_asar=record['application']['file'])
                save_record(root / 'enable-plan.json', plan)
                launcher.apply_plan(plan, root / 'enable-backup')
            result['phases'].append(phase(root, app, desktop, node, 'reopen'))
            reopened = launcher.inspect(root / 'data/launcher-v2.sqlite', launcher_asar=record['application']['file'])
            result['configuration_matches_database'] = reopened['configuration_matches_database']
            result['probe_enabled_after_reopen'] = None if not record['probe'] else any(m['enabled'] and m['path'] == record['probe'] for m in reopened['members'])
            if record['probe']:
                plan = launcher.make_plan(root / 'data/launcher-v2.sqlite', disable=[Path(record['probe'])],
                                          launcher_asar=record['application']['file'])
                changed = launcher.apply_plan(plan, root / 'rollback-backup')
                result['fresh_disable_rollback'] = launcher.rollback(changed['receipt'])['observed_state'] == 'original'
    except (FormatError, OSError, ValueError, subprocess.SubprocessError) as error:
        result['error'] = str(error)
    finally:
        preserved = all(file_hash(s['path']) == s['sha256'] for s in record['source_files'])
        preserved = preserved and all(inventory(s['path']) == s['files'] for s in [*record['source_mods'], record['source_assets']])
        result['original_inputs_unchanged'] = preserved
        result['passed'] = bool(not result.get('error') and preserved and len(result['phases']) == 2 and
                                all(p['healthy_ui'] for p in result['phases']) and result.get('configuration_matches_database') is True and
                                result.get('probe_enabled_after_reopen') is not False and result.get('fresh_disable_rollback') is not False)
        if not result['passed'] and not result.get('error'):
            result['error'] = 'An acceptance check did not pass; inspect result.json and renderer/log evidence'
        save_record(root / 'result.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('prepare', help='copy data/Mods/assets and disable real game/platform operations')
    p.add_argument('--database', type=Path, required=True); p.add_argument('--game', type=Path, required=True)
    p.add_argument('--launcher-asar', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    p.add_argument('--probe-mod', type=Path)
    p = commands.add_parser('run', help='migrate only the copy, inspect UI, reopen, and exercise probe rollback')
    p.add_argument('fixture', type=Path); p.add_argument('--node')
    args = parser.parse_args()
    try:
        if args.command == 'prepare': result = prepare(args.database, args.game, args.launcher_asar, args.output, args.probe_mod)
        else:
            result = run(args.fixture, args.node)
            result = {k: result.get(k) for k in ('passed', 'error', 'original_inputs_unchanged', 'configuration_matches_database',
                                                'probe_enabled_after_reopen', 'fresh_disable_rollback', 'production_pairing_verified', 'scope')}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return int(result.get('passed') is False)
    except (FormatError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False)); return 1


if __name__ == '__main__': sys.exit(main())
