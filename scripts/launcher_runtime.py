"""Add a reviewed official launcher version beside the existing installation."""
import argparse
import ctypes as c
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import uuid
import zipfile

from aow4mod import launcher, loadout
from aow4mod.core import FormatError, write_json
from aow4mod.discovery import user_data
from aow4mod.filesystem import file_hash, inventory, new_directory, outside, reject_reparse, save_record
from launcher_acceptance import signature

FORMAT = 'aow4mod-launcher-runtime-plan/1'
RECEIPT = 'aow4mod-launcher-runtime-receipt/1'
# Reviewed bytes extracted from the signed official 2026.12 installer. Unknown
# archives/bootstrapper revisions require a new review, not a hash override flag.
DISTRIBUTIONS = {
    '0fe1ec3998f692a6f31b42c63490993b9d4aee89a5dd1e58505513fb42d0953f': {
        'version': '2026.12',
        'asar_sha256': '97e4967d91256fbea96b98567e43bfadad5b2b1957d52b71981ce984156cc506',
        'bootstrapper_sha256': '7c9e7210fd6c1b7e3b56c9a91fcec77f6d6d6df40db6368e17a794ef0668bc6f',
        'installer_sha256': '1099ae45ce55c89687c3b70bd32b1386fd224612dae1c2518b06604275baca59',
        'source': 'https://launcher.paradoxinteractive.com/v2/paradox-launcher-installer-2026_12.exe',
    }
}
STATE_NAMES = ('launcher-v2.sqlite', 'content_load.json', 'dlc_load.json', 'game_data.json', 'mods_registry.json')


def cabinet_parts(raw):
    """Find bounded outer CABs; nested CAB signatures are not extra payloads."""
    parts = []; pos = 0; end = 0
    while True:
        pos = raw.find(b'MSCF', pos)
        if pos < 0: break
        start = pos; pos += 4
        if start < end or start + 36 > len(raw): continue
        size = struct.unpack_from('<I', raw, start + 8)[0]
        if raw[start + 24:start + 26] != b'\x03\x01' or not 36 <= size <= len(raw) - start: continue
        if any(struct.unpack_from('<I', raw, start + i)[0] for i in (4, 12, 20)): continue
        parts.append((start, size)); end = start + size
        if len(parts) > 8: raise FormatError('Too many outer installer cabinets')
    return parts


def msi_cabinet(msi_path, output):
    """Read a single MSI data stream with MSIDBOPEN_READONLY, not installation APIs."""
    m = c.WinDLL('msi')
    m.MsiOpenDatabaseW.argtypes = [c.c_wchar_p, c.c_void_p, c.POINTER(c.c_uint)]
    m.MsiDatabaseOpenViewW.argtypes = [c.c_uint, c.c_wchar_p, c.POINTER(c.c_uint)]
    m.MsiViewExecute.argtypes = [c.c_uint, c.c_uint]
    m.MsiViewFetch.argtypes = [c.c_uint, c.POINTER(c.c_uint)]
    m.MsiRecordReadStream.argtypes = [c.c_uint, c.c_uint, c.c_void_p, c.POINTER(c.c_uint)]
    m.MsiCloseHandle.argtypes = [c.c_uint]
    db = c.c_uint(); view = c.c_uint(); row = c.c_uint()
    try:
        if m.MsiOpenDatabaseW(str(msi_path), None, c.byref(db)): raise FormatError('Cannot open official MSI read-only')
        if m.MsiDatabaseOpenViewW(db.value, "SELECT `Data` FROM `_Streams` WHERE `Name`='cab1.cab'", c.byref(view)):
            raise FormatError('Official MSI cabinet stream is unavailable')
        if m.MsiViewExecute(view.value, 0) or m.MsiViewFetch(view.value, c.byref(row)):
            raise FormatError('Cannot read official MSI cabinet stream')
        total = 0; buffer = c.create_string_buffer(1024 * 1024)
        with output.open('xb') as f:
            while True:
                size = c.c_uint(len(buffer))
                if m.MsiRecordReadStream(row.value, 1, buffer, c.byref(size)): raise FormatError('MSI data stream read failed')
                if not size.value: break
                total += size.value
                if total > 256 * 1024 ** 2: raise FormatError('MSI cabinet exceeds its bound')
                f.write(buffer.raw[:size.value])
            f.flush(); os.fsync(f.fileno())
    finally:
        for handle in (row, view, db):
            if handle.value: m.MsiCloseHandle(handle.value)


def unpack(installer, output):
    installer = Path(installer).resolve(); reject_reparse(installer)
    if installer.stat().st_size > 256 * 1024 ** 2: raise FormatError('Installer exceeds 256 MiB')
    digest = file_hash(installer)
    matches = [(h, d) for h, d in DISTRIBUTIONS.items() if d['installer_sha256'] == digest]
    if len(matches) != 1: raise FormatError('Unreviewed official installer; refusing to execute or unpack it')
    if os.name != 'nt': raise FormatError('Official bundle extraction requires Windows expand/MSI stream readers')
    signed = signature(installer); output = outside(output, [installer, user_data().parent])
    output.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(output.parent).free < 1024 ** 3: raise FormatError('Insufficient free space for bounded bundle extraction')
    raw = installer.read_bytes(); parts = cabinet_parts(raw)
    if len(parts) != 2: raise FormatError('Official installer cabinet layout differs')
    expand = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/expand.exe'
    with new_directory(output, [installer, user_data().parent]) as stage:
        with tempfile.TemporaryDirectory(prefix='.aow4-runtime-unpack-', dir=output.parent) as tmp:
            tmp = Path(tmp).resolve()
            if tmp.parent != output.parent: raise FormatError('Unexpected bundle temporary location')
            offset, size = max(parts, key=lambda item: item[1]); outer = tmp / 'outer.cab'
            outer.write_bytes(raw[offset:offset + size]); payload = tmp / 'payload'; payload.mkdir()
            def extract(cab, destination):
                result = subprocess.run([str(expand), '-r', str(cab), '-F:*', str(destination)], capture_output=True,
                                        timeout=60, creationflags=subprocess.CREATE_NO_WINDOW)
                if result.returncode: raise FormatError('Native CAB extraction failed')
            extract(outer, payload)
            found = sorted(p.name for p in payload.iterdir())
            if found != ['a0']: raise FormatError('Official bundle payload differs: ' + repr(found[:8]))
            inner = tmp / 'inner.cab'; msi_cabinet(payload / 'a0', inner)
            zips = tmp / 'zips'; zips.mkdir(); extract(inner, zips)
            if sorted(p.name for p in zips.iterdir()) != ['bootstrapper.zip', 'launcher.zip']:
                raise FormatError('Official MSI archive payload differs')
            if file_hash(zips / 'launcher.zip') != matches[0][0]: raise FormatError('Extracted launcher archive differs from reviewed distribution')
            for name in ('launcher.zip', 'bootstrapper.zip'): copy_file(zips / name, stage / name)
            if file_hash(installer) != digest: raise FormatError('Installer changed during extraction')
            save_record(stage / 'source.json', {'installer': str(installer), 'installer_sha256': digest,
                        'signature': signed, 'source': matches[0][1]['source'],
                        'archives': {name: file_hash(stage / name) for name in ('launcher.zip', 'bootstrapper.zip')},
                        'installer_executed': False, 'msi_installed': False})
    return {'archives': str(output), 'launcher_archive_sha256': matches[0][0], 'installer_executed': False, 'msi_installed': False}


def package(archive):
    archive = Path(archive).resolve(); reject_reparse(archive)
    digest = file_hash(archive)
    if digest not in DISTRIBUTIONS: raise FormatError('Unreviewed official launcher archive')
    distribution = DISTRIBUTIONS[digest]; files = {}; seen = set(); total = 0
    with zipfile.ZipFile(archive) as z:
        if len(z.infolist()) > 8192: raise FormatError('Launcher archive has too many entries')
        for entry in z.infolist():
            name = entry.filename.rstrip('/'); p = PurePosixPath(name)
            if not name or p.is_absolute() or '\\' in name or any(
                part in ('', '.', '..') or ':' in part or part.rstrip(' .') != part or
                re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?', part) for part in name.split('/')):
                raise FormatError('Unsafe launcher archive path')
            if name.casefold() in seen: raise FormatError('Duplicate launcher archive path')
            seen.add(name.casefold())
            kind = entry.external_attr >> 16 & 0xf000
            if kind not in (0, 0x8000, 0x4000) or entry.flag_bits & 1:
                raise FormatError('Unsupported launcher archive entry')
            if entry.is_dir(): continue
            total += entry.file_size
            if entry.file_size > 512 * 1024 ** 2 or total > 2 * 1024 ** 3:
                raise FormatError('Launcher archive exceeds extraction bounds')
            h = hashlib.sha256()
            with z.open(entry) as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b''): h.update(chunk)
            files[name] = {'size': entry.file_size, 'sha256': h.hexdigest()}
        if files.get('resources/app.asar', {}).get('sha256') != distribution['asar_sha256']:
            raise FormatError('Launcher archive application differs from its reviewed profile')
        marker = z.read('.cpatch/launcher-v2_1/version').decode('ascii').strip()
        if marker != distribution['version']: raise FormatError('Launcher archive version marker differs')
    if file_hash(archive) != digest: raise FormatError('Launcher archive changed during inspection')
    return distribution, files


def shared_state(root, profile):
    root = Path(root).resolve(); reject_reparse(root)
    if not root.is_dir(): raise FormatError('Shared game-data parent directory is missing')
    files = []; histories = []
    for game in sorted(root.iterdir()):
        if not game.is_dir(): continue
        reject_reparse(game)
        db = game / 'launcher-v2.sqlite'
        if not db.is_file(): continue
        db = launcher.database_path(db); before = file_hash(db)
        con = launcher.connect(db)
        try:
            if con.execute('PRAGMA quick_check').fetchone()[0] != 'ok': raise FormatError('Shared launcher database is damaged')
            names = [r[0] for r in con.execute('SELECT name FROM knex_migrations ORDER BY id')]
            if not names or len(set(names)) != len(names) or set(names) - set(profile['migration_names']):
                raise FormatError('Candidate launcher does not recognize shared database history: ' + game.name)
        finally: con.close()
        if file_hash(db) != before: raise FormatError('Shared launcher database changed during inspection')
        histories.append({'game': game.name, 'migrations': names})
        for name in STATE_NAMES:
            path = game / name
            if not path.is_file(): continue
            reject_reparse(path)
            if path.stat().st_size > 128 * 1024 ** 2: raise FormatError('Shared launcher state file is too large')
            files.append({'path': str(path), 'sha256': file_hash(path), 'size': path.stat().st_size})
    if len(files) > 256: raise FormatError('Too many shared launcher state files')
    return {'root': str(root), 'files': files, 'histories': histories}


def make_plan(archive, installation=None, state_root=None, identity=None):
    loadout._idle(); archive = Path(archive).resolve()
    distribution, files = package(archive)
    profile = next((p for p in launcher.PROFILES if p['app_asar_sha256'] == distribution['asar_sha256']), None)
    if profile is None: raise FormatError('Candidate application profile is unavailable')
    installation = Path(installation).resolve() if installation else Path(launcher.launcher_binding()['file']).parents[2]
    state_root = Path(state_root).resolve() if state_root else user_data().parent
    outside(installation, [state_root]); reject_reparse(installation)
    before = inventory(installation)
    if len(before) > 8192 or sum(x['size'] for x in before.values()) > 2 * 1024 ** 3:
        raise FormatError('Existing launcher installation exceeds backup bounds')
    if file_hash(installation / 'bootstrapper-v2.exe') != distribution['bootstrapper_sha256']:
        raise FormatError('Unreviewed launcher bootstrapper')
    target = installation / ('launcher-v2.' + distribution['version'])
    if target.exists(): raise FormatError('Candidate launcher directory already exists')
    versions = []
    for existing in installation.glob('launcher-v2.*'):
        match = re.fullmatch(r'launcher-v2\.(\d{4}(?:\.\d+)+)', existing.name)
        if not match: raise FormatError('Unreviewed existing launcher version/branch')
        launcher.launcher_binding(existing / 'resources/app.asar')
        if (existing / '.cpatch/launcher-v2_1/version').read_text(encoding='ascii').strip() != match[1]:
            raise FormatError('Existing launcher directory/version marker differs')
        versions.append(tuple(map(int, match[1].split('.'))))
    if not versions or tuple(map(int, distribution['version'].split('.'))) <= max(versions):
        raise FormatError('Candidate must be newer than every existing reviewed launcher')
    with tempfile.TemporaryDirectory(prefix='aow4-launcher-signature-') as tmp:
        executable = Path(tmp) / 'Paradox Launcher.exe'
        with zipfile.ZipFile(archive) as z, z.open('Paradox Launcher.exe') as src, executable.open('wb') as dst:
            shutil.copyfileobj(src, dst)
        signed = signature(executable)
    state = shared_state(state_root, profile)
    pointer_path = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local'))) / 'Paradox Interactive/launcherpath'
    pointer = None
    if pointer_path.is_file() and Path(pointer_path.read_text(encoding='utf-8-sig').strip()).resolve() == installation:
        reject_reparse(pointer_path)
        pointer = {'path': str(pointer_path.resolve()), 'sha256': file_hash(pointer_path), 'size': pointer_path.stat().st_size}
    if inventory(installation) != before: raise FormatError('Installation changed during planning')
    return {'format': FORMAT, 'id': str(uuid.UUID(identity)) if identity else str(uuid.uuid4()),
            'archive': str(archive), 'archive_sha256': file_hash(archive), 'distribution': distribution,
            'installation': str(installation), 'before_files': before, 'target': str(target), 'target_files': files,
            'signature': signed, 'state': state, 'discovery_pointer': pointer,
            'scope': 'Add one reviewed official version directory. No installer, registry, pointer, existing runtime, game-data or database migration writes.'}


def copy_file(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    with Path(source).open('rb') as src, target.open('xb') as dst:
        shutil.copyfileobj(src, dst); dst.flush(); os.fsync(dst.fileno())


def copy_tree(source, target, files):
    target.mkdir()
    for name in files: copy_file(Path(source) / name, target / name)
    if inventory(target) != files: raise FormatError('Backup copy differs from the original')


def clone(output, installation=None):
    loadout._idle()
    source = Path(installation).resolve() if installation else Path(launcher.launcher_binding()['file']).parents[2]
    before = inventory(source)
    if len(before) > 8192 or sum(f['size'] for f in before.values()) > 2 * 1024 ** 3:
        raise FormatError('Launcher clone exceeds bounds')
    if file_hash(source / 'bootstrapper-v2.exe') not in {d['bootstrapper_sha256'] for d in DISTRIBUTIONS.values()}:
        raise FormatError('Unreviewed clone bootstrapper')
    for app in source.glob('launcher-v2.*/resources/app.asar'): launcher.launcher_binding(app)
    output = outside(output, [source, user_data().parent]); output.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(output.parent).free < sum(f['size'] for f in before.values()) + 64 * 1024 ** 2:
        raise FormatError('Insufficient free space for launcher clone')
    with new_directory(output, [source, user_data().parent]) as stage:
        for name in before: copy_file(source / name, stage / name)
        if inventory(stage) != before or inventory(source) != before: raise FormatError('Launcher changed during clone')
    return {'source': str(source), 'installation': str(output), 'files': len(before), 'activated': False}


def apply_plan(plan, backup):
    if plan.get('format') != FORMAT: raise FormatError('Unsupported runtime plan')
    expected = make_plan(plan['archive'], plan['installation'], plan['state']['root'], plan['id'])
    if plan != expected: raise FormatError('Runtime plan or bound inputs changed')
    installation = Path(plan['installation']); target = Path(plan['target'])
    backup = outside(backup, [installation, Path(plan['archive']), Path(plan['state']['root'])])
    backup.parent.mkdir(parents=True, exist_ok=True)
    if backup.parent.stat().st_dev != installation.stat().st_dev:
        raise FormatError('Runtime backup must be on the installation volume for atomic installation/quarantine')
    required = sum(f['size'] for f in plan['before_files'].values()) + sum(f['size'] for f in plan['target_files'].values())
    required += Path(plan['archive']).stat().st_size + sum(f['size'] for f in plan['state']['files']) + 64 * 1024 ** 2
    if shutil.disk_usage(backup.parent).free < required: raise FormatError('Insufficient free space for durable runtime backup and candidate')
    with new_directory(backup, [installation, Path(plan['archive']), Path(plan['state']['root'])]) as stage:
        copy_tree(installation, stage / 'installation-before', plan['before_files'])
        (stage / 'shared-state').mkdir()
        for i, item in enumerate(plan['state']['files']):
            dest = stage / 'shared-state' / f'{i:04d}.bin'; copy_file(item['path'], dest)
            if file_hash(dest) != item['sha256']: raise FormatError('Shared-state backup differs')
        if plan['discovery_pointer']:
            copy_file(plan['discovery_pointer']['path'], stage / 'discovery-pointer.bin')
            if file_hash(stage / 'discovery-pointer.bin') != plan['discovery_pointer']['sha256']:
                raise FormatError('Installation pointer backup differs')
        copy_file(plan['archive'], stage / 'package.zip')
        if file_hash(stage / 'package.zip') != plan['archive_sha256']: raise FormatError('Archive copy differs')
        candidate = stage / 'candidate'; candidate.mkdir()
        with zipfile.ZipFile(stage / 'package.zip') as z:
            for name in plan['target_files']:
                dest = candidate / name; dest.parent.mkdir(parents=True, exist_ok=True)
                with z.open(name) as src, dest.open('xb') as dst:
                    shutil.copyfileobj(src, dst); dst.flush(); os.fsync(dst.fileno())
        if inventory(candidate) != plan['target_files']: raise FormatError('Extracted runtime differs')
        save_record(stage / 'receipt.json', {'format': RECEIPT, 'backup': str(backup), 'plan': plan})
    # Durable backup and frozen source precede the only installation mutation.
    if make_plan(plan['archive'], installation, plan['state']['root'], plan['id']) != plan:
        raise FormatError('Inputs changed before runtime commit; backup retained')
    if target.parent.resolve() != installation.resolve() or target.exists(): raise FormatError('Runtime target changed')
    os.rename(backup / 'candidate', target)
    result = status(backup / 'receipt.json')
    if result['observed_state'] != 'applied': raise FormatError('Runtime commit diverged; inspect the retained receipt')
    return result


def read_receipt(path):
    path = Path(path).resolve(); record = json.loads(path.read_text(encoding='utf-8'))
    if record.get('format') != RECEIPT or Path(record['backup']).resolve() != path.parent:
        raise FormatError('Invalid or moved runtime receipt')
    plan = record['plan']; distribution, files = package(path.parent / 'package.zip')
    if plan.get('format') != FORMAT or plan['distribution'] != distribution or plan['target_files'] != files:
        raise FormatError('Runtime receipt differs from its frozen official package')
    installation = Path(plan['installation']).resolve()
    if Path(plan['target']) != installation / ('launcher-v2.' + distribution['version']):
        raise FormatError('Runtime receipt target escapes its installation')
    outside(path.parent, [installation, Path(plan['state']['root'])])
    if inventory(path.parent / 'installation-before') != plan['before_files']:
        raise FormatError('Runtime installation backup changed')
    for i, item in enumerate(plan['state']['files']):
        if file_hash(path.parent / 'shared-state' / f'{i:04d}.bin') != item['sha256']:
            raise FormatError('Runtime shared-state backup changed')
    if plan.get('discovery_pointer') and file_hash(path.parent / 'discovery-pointer.bin') != plan['discovery_pointer']['sha256']:
        raise FormatError('Installation pointer backup changed')
    return record


def status(receipt):
    record = read_receipt(receipt); plan = record['plan']; installation = Path(plan['installation'])
    before = plan['before_files']; prefix = Path(plan['target']).name + '/'
    after = {**before, **{prefix + name: data for name, data in plan['target_files'].items()}}
    current = inventory(installation)
    profile = next(p for p in launcher.PROFILES if p['app_asar_sha256'] == plan['distribution']['asar_sha256'])
    state_same = shared_state(plan['state']['root'], profile) == plan['state']
    pointer = plan.get('discovery_pointer')
    if pointer and (not Path(pointer['path']).is_file() or file_hash(pointer['path']) != pointer['sha256']): state_same = False
    current_pointer = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local'))) / 'Paradox Interactive/launcherpath'
    activated_since_plan = not pointer and current_pointer.is_file() and Path(current_pointer.read_text(encoding='utf-8-sig').strip()).resolve() == installation.resolve()
    observed = 'original' if current == before and not Path(plan['target']).exists() else 'applied' if current == after else 'diverged'
    if not state_same: observed = 'diverged'
    return {'receipt': str(Path(receipt).resolve()), 'observed_state': observed, 'shared_state_unchanged': state_same,
            'activated_since_plan': activated_since_plan,
            'installation': str(installation), 'target': plan['target'], 'version': plan['distribution']['version'],
            'database_migrated': False, 'scope': plan['scope']}


def rollback(receipt):
    loadout._idle(); observed = status(receipt)
    if observed['observed_state'] == 'original': return observed
    if observed['activated_since_plan']: raise FormatError('Deactivate the user installation through its location receipt before runtime rollback')
    if observed['observed_state'] != 'applied': raise FormatError('Runtime/shared game state diverged; refusing automatic rollback')
    record = read_receipt(receipt); backup = Path(record['backup']).resolve(); plan = record['plan']
    target = Path(plan['target']).resolve(); installation = Path(plan['installation']).resolve()
    quarantine = (backup / 'quarantined-runtime').resolve()
    if target.parent != installation or quarantine.parent != backup or quarantine.exists():
        raise FormatError('Runtime quarantine paths changed')
    # Both resolved absolute paths stay within the explicitly bound directories.
    # Move only the new exact version; never recursively delete an installation.
    os.rename(target, quarantine)
    return status(receipt)


def main():
    parser = argparse.ArgumentParser(description=__doc__); sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('plan'); p.add_argument('--archive', type=Path, required=True); p.add_argument('--installation', type=Path)
    p.add_argument('--state-root', type=Path); p.add_argument('--output', type=Path, required=True)
    p = sub.add_parser('clone'); p.add_argument('--installation', type=Path); p.add_argument('--output', type=Path, required=True)
    p = sub.add_parser('unpack'); p.add_argument('--installer', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    p = sub.add_parser('apply'); p.add_argument('plan', type=Path); p.add_argument('--backup', type=Path, required=True)
    for command in ('status', 'rollback'): sub.add_parser(command).add_argument('receipt', type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'plan':
            result = make_plan(args.archive, args.installation, args.state_root)
            write_json(args.output, result, [Path(result['installation']), Path(result['archive']), Path(result['state']['root'])])
            result = {'plan': str(args.output.resolve()), 'version': result['distribution']['version'], 'installation': result['installation'],
                      'shared_games': len(result['state']['histories']), 'scope': result['scope']}
        elif args.command == 'unpack': result = unpack(args.installer, args.output)
        elif args.command == 'clone': result = clone(args.output, args.installation)
        elif args.command == 'apply': result = apply_plan(json.loads(args.plan.read_text(encoding='utf-8')), args.backup)
        elif args.command == 'status': result = status(args.receipt)
        else: result = rollback(args.receipt)
        print(json.dumps(result, ensure_ascii=False, indent=2)); return int(result.get('observed_state') == 'diverged')
    except (FormatError, OSError, ValueError, zipfile.BadZipFile) as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False)); return 1


if __name__ == '__main__': sys.exit(main())
