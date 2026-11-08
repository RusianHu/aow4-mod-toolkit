"""Guarded user-level launcher location switch; preserves the system installation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

from aow4mod import launcher, loadout
from aow4mod.core import FormatError, write_json
from aow4mod.filesystem import file_hash, inventory, new_directory, outside, save_record
import launcher_runtime as runtime

FORMAT = 'aow4mod-launcher-location-plan/1'
RECEIPT = 'aow4mod-launcher-location-receipt/1'
REGISTRY_KEY = r'SOFTWARE\Paradox Interactive\Paradox Launcher v2'
REGISTRY_NAME = 'LauncherInstallation'


def registry_read():
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_KEY) as key:
        value, kind = winreg.QueryValueEx(key, REGISTRY_NAME)
    if kind != winreg.REG_SZ or not isinstance(value, str): raise FormatError('Unreviewed user launcher installation value')
    return {'value': value, 'type': kind}


def registry_write(value):
    import winreg
    if value['type'] != winreg.REG_SZ or not isinstance(value['value'], str): raise FormatError('Unsupported user installation value')
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, REGISTRY_NAME, 0, winreg.REG_SZ, value['value']); winreg.FlushKey(key)


def pointer_file():
    return Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local'))) / 'Paradox Interactive/launcherpath'


def pointer_value(raw): return Path(raw.decode('utf-8-sig').strip()).resolve()


def make_plan(runtime_receipt, identity=None):
    loadout._idle(); runtime_receipt = Path(runtime_receipt).resolve()
    observed = runtime.status(runtime_receipt)
    if observed['observed_state'] != 'applied': raise FormatError('New user runtime must be applied with unchanged shared state')
    record = runtime.read_receipt(runtime_receipt); install = record['plan']; destination = Path(install['installation']).resolve()
    pointer = pointer_file().resolve(); before = pointer.read_bytes()
    if len(before) > 4096: raise FormatError('Launcher installation pointer is too large')
    original = pointer_value(before); registration = registry_read()
    if Path(registration['value']).resolve() != original: raise FormatError('User launcher pointer/registry disagree')
    if original == destination: raise FormatError('New runtime is already selected')
    if inventory(original) != install['before_files']:
        raise FormatError('Original installation differs from the preserved user-level clone')
    outside(destination, [original, Path(install['state']['root'])])
    target_binding = launcher.launcher_binding(Path(install['target']) / 'resources/app.asar')
    after = (str(destination) + os.sep).encode('utf-8')
    return {'format': FORMAT, 'id': str(uuid.UUID(identity)) if identity else str(uuid.uuid4()),
            'runtime_receipt': str(runtime_receipt), 'runtime_receipt_sha256': file_hash(runtime_receipt),
            'pointer': str(pointer), 'before_hex': before.hex(), 'after_hex': after.hex(),
            'before_registry': registration, 'after_registry': {'type': registration['type'], 'value': str(destination) + os.sep},
            'original_installation': str(original), 'new_installation': str(destination), 'application': target_binding,
            'scope': 'Switch only the existing user launcherpath file and HKCU LauncherInstallation value. Original system runtime and game data are preserved.'}


def read_receipt(path):
    path = Path(path).resolve(); record = json.loads(path.read_text(encoding='utf-8'))
    if record.get('format') != RECEIPT or Path(record['backup']).resolve() != path.parent: raise FormatError('Invalid or moved launcher location receipt')
    plan = record['plan']
    if plan.get('format') != FORMAT or Path(plan['pointer']).resolve() != pointer_file().resolve():
        raise FormatError('Location receipt does not match the fixed user pointer')
    if file_hash(plan['runtime_receipt']) != plan['runtime_receipt_sha256']: raise FormatError('Bound runtime receipt changed')
    frozen = (path.parent / 'launcherpath-before.bin').read_bytes()
    if frozen.hex() != plan['before_hex']: raise FormatError('Original launcher pointer backup changed')
    install = runtime.read_receipt(plan['runtime_receipt'])['plan']
    original = Path(plan['original_installation']).resolve(); destination = Path(install['installation']).resolve()
    if pointer_value(frozen) != original or Path(plan['before_registry']['value']).resolve() != original:
        raise FormatError('Original location values disagree')
    if Path(plan['new_installation']).resolve() != destination or pointer_value(bytes.fromhex(plan['after_hex'])) != destination or Path(plan['after_registry']['value']).resolve() != destination:
        raise FormatError('Candidate location values disagree')
    if plan['before_registry']['type'] != 1 or plan['after_registry']['type'] != 1: raise FormatError('Unsupported location registry type')
    outside(destination, [original, Path(install['state']['root'])])
    outside(path.parent, [original, destination, Path(install['state']['root'])])
    if inventory(original) != install['before_files']: raise FormatError('Original system runtime changed')
    launcher.check_binding(plan['application'])
    return record


def status(receipt):
    record = read_receipt(receipt); plan = record['plan']; pointer = Path(plan['pointer'])
    raw = pointer.read_bytes(); registration = registry_read()
    pointer_state = 'original' if raw.hex() == plan['before_hex'] else 'applied' if raw.hex() == plan['after_hex'] else 'diverged'
    registry_state = 'original' if registration == plan['before_registry'] else 'applied' if registration == plan['after_registry'] else 'diverged'
    runtime_state = runtime.status(plan['runtime_receipt'])
    observed = pointer_state if pointer_state == registry_state else 'partial' if 'diverged' not in (pointer_state, registry_state) else 'diverged'
    return {'receipt': str(Path(receipt).resolve()), 'observed_state': observed,
            'pointer_state': pointer_state, 'registry_state': registry_state,
            'runtime_state': runtime_state['observed_state'], 'shared_state_unchanged': runtime_state['shared_state_unchanged'],
            'new_installation': plan['new_installation'], 'application': plan['application'],
            'automatic_rollback_allowed': observed in ('original', 'applied', 'partial') and runtime_state['observed_state'] == 'applied',
            'scope': plan['scope']}


def apply_plan(plan, backup):
    if plan.get('format') != FORMAT: raise FormatError('Unsupported location plan')
    if plan != make_plan(plan['runtime_receipt'], plan['id']): raise FormatError('Location plan or current inputs changed')
    backup = outside(backup, [Path(plan['original_installation']), Path(plan['new_installation']), pointer_file().parent])
    with new_directory(backup, [Path(plan['original_installation']), Path(plan['new_installation']), pointer_file().parent]) as stage:
        runtime.copy_file(plan['pointer'], stage / 'launcherpath-before.bin')
        if (stage / 'launcherpath-before.bin').read_bytes().hex() != plan['before_hex']: raise FormatError('Original pointer changed while copying')
        save_record(stage / 'receipt.json', {'format': RECEIPT, 'backup': str(backup), 'plan': plan})
    if plan != make_plan(plan['runtime_receipt'], plan['id']): raise FormatError('Location inputs changed before commit')
    try:
        registry_write(plan['after_registry'])
        loadout._replace(Path(plan['pointer']), bytes.fromhex(plan['after_hex']), hashlib.sha256(bytes.fromhex(plan['before_hex'])).hexdigest())
    except BaseException:
        # Restore only our known values; never overwrite a concurrent user edit.
        if registry_read() == plan['after_registry']: registry_write(plan['before_registry'])
        if Path(plan['pointer']).read_bytes().hex() == plan['after_hex']:
            loadout._replace(Path(plan['pointer']), bytes.fromhex(plan['before_hex']), hashlib.sha256(bytes.fromhex(plan['after_hex'])).hexdigest())
        raise
    result = status(backup / 'receipt.json')
    if result['observed_state'] != 'applied': raise FormatError('Location commit diverged; inspect its receipt')
    return result


def rollback(receipt):
    loadout._idle(); result = status(receipt)
    if result['observed_state'] == 'original': return result
    if not result['automatic_rollback_allowed']:
        raise FormatError('Location/runtime/shared game state changed; refusing automatic location rollback')
    plan = read_receipt(receipt)['plan']
    registry_write(plan['before_registry'])
    expected = plan['after_hex'] if result['pointer_state'] == 'applied' else plan['before_hex']
    loadout._replace(Path(plan['pointer']), bytes.fromhex(plan['before_hex']), hashlib.sha256(bytes.fromhex(expected)).hexdigest())
    return status(receipt)


def main():
    p = argparse.ArgumentParser(description=__doc__); sub = p.add_subparsers(dest='command', required=True)
    q = sub.add_parser('plan'); q.add_argument('--runtime-receipt', type=Path, required=True); q.add_argument('--output', type=Path, required=True)
    q = sub.add_parser('apply'); q.add_argument('plan', type=Path); q.add_argument('--backup', type=Path, required=True)
    for command in ('status', 'rollback'): sub.add_parser(command).add_argument('receipt', type=Path)
    args = p.parse_args()
    try:
        if args.command == 'plan':
            result = make_plan(args.runtime_receipt)
            write_json(args.output, result, [Path(result['original_installation']), Path(result['new_installation']), pointer_file().parent])
            result = {'plan': str(args.output.resolve()), 'old_installation': result['original_installation'], 'new_installation': result['new_installation'], 'scope': result['scope']}
        elif args.command == 'apply': result = apply_plan(json.loads(args.plan.read_text(encoding='utf-8')), args.backup)
        elif args.command == 'status': result = status(args.receipt)
        else: result = rollback(args.receipt)
        print(json.dumps(result, ensure_ascii=False, indent=2)); return int(result.get('observed_state') in ('diverged', 'partial'))
    except (FormatError, OSError, ValueError) as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False)); return 1


if __name__ == '__main__': sys.exit(main())
