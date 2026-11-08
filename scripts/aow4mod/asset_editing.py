"""Reviewable CLB scalar/vector edits, native compilation and staged Mod copies."""
import copy
import json
import math
from pathlib import Path, PurePosixPath
import struct
import uuid

from . import asset_yaml, native, native_assets, content_libraries
from .core import FormatError, mod_file
from .filesystem import file_hash, inventory, copy_directory

FORMAT = 'aow4mod-asset-edit-plan/1'


def relative_clb(value):
    if not isinstance(value, str) or '\0' in value:
        raise FormatError('Asset destination must be a relative CLB path')
    path = PurePosixPath(value.replace('\\', '/'))
    if path.is_absolute() or '..' in path.parts or ':' in value or path.suffix.lower() != '.clb':
        raise FormatError('Asset destination must be a safe relative CLB path')
    if not path.parts or path.parts[0].lower() not in ('libraries', 'title'):
        raise FormatError('Stage CLB assets under Libraries/ or the explicit Title/ hierarchy')
    return path.as_posix()


def float32_value(value):
    if isinstance(value, list):
        return [float32_value(x) for x in value]
    if type(value) not in (int, float) or not math.isfinite(value):
        raise FormatError('--float32 requires a finite number or vector')
    try:
        result = struct.unpack('<f', struct.pack('<f', value))[0]
    except (OverflowError, struct.error) as exc:
        raise FormatError('Asset value exceeds float32') from exc
    if not math.isfinite(result) or (result == 0 and value != 0):
        raise FormatError('Asset value overflows or underflows float32')
    # Match the native YAML emitter's double-precision textual representation
    # of a float32 value; no approximate string/identity comparison is needed.
    return format(result, '.17g')


def make_plan(mod, library, path, value, relative, game, cache_dir=Path('.aow4mod/native'), timeout=60, float32=False):
    acp = mod_file(Path(mod))
    library = Path(library).resolve()
    relative = relative_clb(relative)
    cache_dir = native.check_cache_directory(cache_dir, game, [acp.parent, library.parent])
    source_files = inventory(acp.parent)
    source_hash = file_hash(library)
    run = cache_dir/'asset-plans'/uuid.uuid4().hex
    run.mkdir(parents=True)
    exported = native_assets.export_library(library, game, run/'export', cache_dir, timeout)
    yaml_path = run/'export'/exported['files'][1]['name']
    before = asset_yaml.load(yaml_path.read_bytes())
    after = copy.deepcopy(before)
    encoded = float32_value(value) if float32 else value
    old, new = asset_yaml.set_value(after, path, encoded)
    changes = asset_yaml.differences(before, after)
    if not changes:
        raise FormatError('Asset edit makes no change')
    (run/'input').mkdir()
    desired = run/'input/desired.yaml'
    desired.write_text(asset_yaml.dump(after), encoding='utf-8')
    compiled = native_assets.compile_library(desired, game, run/'compiled', cache_dir, timeout)
    binary = run/'compiled'/compiled['files'][0]['name']
    # Renaming a library is a separate graph operation; scalar edits preserve it.
    if Path(relative).stem.casefold() != compiled['library'].casefold():
        raise FormatError('Asset destination filename must preserve the internal library identity')
    if inventory(acp.parent) != source_files or file_hash(library) != source_hash:
        raise FormatError('Asset edit source changed during planning')
    return {'format': FORMAT, 'generator_sha256': file_hash(__file__), 'game': str(Path(game).resolve()),
            'mod_source': str(acp), 'source_files': source_files, 'library': str(library), 'library_sha256': source_hash,
            'relative_path': relative, 'edit': {'path': path, 'old': old, 'new': new, 'requested': value, 'float32': bool(float32)},
            'changes': changes, 'export': exported, 'compile': compiled,
            'desired_yaml': str(desired), 'desired_yaml_sha256': file_hash(desired),
            'compiled_file': str(binary), 'compiled_sha256': file_hash(binary),
            'normalization_review_required': not exported['comparison']['all_compared_data_equal'],
            'coverage': 'The requested existing scalar/vector field changed and the full YAML/member readback matched. '
                        'Native normalization, source-asset reimports and runtime rendering require separate review.'}


def apply_plan(plan, output):
    if not isinstance(plan, dict) or plan.get('format') != FORMAT or plan.get('generator_sha256') != file_hash(__file__):
        raise FormatError('Unsupported or stale asset edit plan')
    source = Path(plan['mod_source']).resolve()
    game = Path(plan['game']).resolve()
    if inventory(source.parent) != plan['source_files']:
        raise FormatError('Mod source changed since asset planning')
    for path, digest in [(plan['library'], plan['library_sha256']), (plan['desired_yaml'], plan['desired_yaml_sha256']),
                         (plan['compiled_file'], plan['compiled_sha256'])]:
        if file_hash(path) != digest:
            raise FormatError('Asset plan input or compiled artifact changed')
    if native.public_binding(native.profile_for(game)) != plan['compile']['binding']:
        raise FormatError('Native asset backend changed since planning')
    compiled_report = plan['compile']
    if (compiled_report.get('format') != 'aow4mod-native-asset-compile/1'
            or not compiled_report.get('yaml_fields_preserved') or not compiled_report.get('member_identities_preserved')
            or compiled_report.get('source_sha256') != plan['desired_yaml_sha256']
            or Path(compiled_report['source']).resolve() != Path(plan['desired_yaml']).resolve()
            or compiled_report['files'][0]['sha256'] != plan['compiled_sha256']
            or (Path(compiled_report['output'])/compiled_report['files'][0]['name']).resolve() != Path(plan['compiled_file']).resolve()):
        raise FormatError('Asset compilation receipt does not bind the planned input and binary output')
    if (plan['export']['source_sha256'] != plan['library_sha256']
            or Path(plan['export']['source']).resolve() != Path(plan['library']).resolve()):
        raise FormatError('Asset baseline receipt does not bind its original library')
    original_yaml = Path(plan['export']['output'])/plan['export']['files'][1]['name']
    if file_hash(original_yaml) != plan['export']['files'][1]['sha256']:
        raise FormatError('Asset baseline YAML changed')
    baseline = asset_yaml.load(original_yaml.read_bytes())
    expected = copy.deepcopy(baseline)
    edit = plan['edit']
    value = float32_value(edit['requested']) if edit['float32'] else edit['requested']
    old, new = asset_yaml.set_value(expected, edit['path'], value)
    desired = asset_yaml.load(Path(plan['desired_yaml']).read_bytes())
    if old != edit['old'] or new != edit['new'] or desired != expected or asset_yaml.differences(baseline, expected) != plan['changes']:
        raise FormatError('Asset plan changes differ from the reviewed edit')
    compiled_yaml = Path(plan['compile']['output'])/plan['compile']['files'][1]['name']
    if file_hash(compiled_yaml) != plan['compile']['files'][1]['sha256'] or asset_yaml.load(compiled_yaml.read_bytes()) != desired:
        raise FormatError('Compiled asset readback no longer matches the desired state')
    relative = relative_clb(plan['relative_path'])
    data = Path(plan['compiled_file']).read_bytes()
    lib = content_libraries.read(plan['compiled_file'])
    expected_members = asset_yaml.members(desired)
    actual_members = [{'cid': int(m['identity']['type_id'], 16), 'locator': m['identity']['locator'], 'name': m['identity']['name']}
                      for m in lib.members]
    if actual_members != expected_members:
        raise FormatError('Compiled CLB members do not match the reviewed YAML')
    if Path(relative).stem.casefold() != lib.name.casefold():
        raise FormatError('Compiled library identity differs from its staged filename')
    with copy_directory(source.parent, output, [game, Path(plan['library']).parent]) as (staged, source_files):
        destination = staged/relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        if file_hash(destination) != plan['compiled_sha256']:
            raise FormatError('Staged asset does not match its compiled artifact')
        receipt = {'format': 'aow4mod-asset-edit-receipt/1', 'output': str(Path(output).resolve()),
                   'relative_path': relative, 'compiled_sha256': plan['compiled_sha256'],
                   'edit': edit, 'normalization_review_required': plan['normalization_review_required'],
                   'native_yaml_readback_verified': True, 'source_files': source_files, 'gameplay_tested': False,
                   'coverage': plan['coverage']}
        (staged/'.aow4mod-asset-edit.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return receipt
