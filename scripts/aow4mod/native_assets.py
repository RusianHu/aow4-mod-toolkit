"""Native CLB export into a new diagnostic directory with independent comparison."""
import json
from pathlib import Path
import shutil

from .core import FormatError
from .filesystem import file_hash, new_directory, outside
from . import content_libraries, native, native_validation, asset_yaml
from .authoring import safe_name


def export_library(path, game, output, cache_dir=Path('.aow4mod/native'), timeout=60, max_bytes=256*1024*1024):
    path = Path(path).resolve()
    destination = outside(output, [path.parent, game])
    if destination.exists():
        raise FormatError('Native asset export output already exists')
    if type(max_bytes) is not int or not 1 <= max_bytes <= 2*1024*1024*1024:
        raise FormatError('Native asset size bound must be between 1 byte and 2 GiB')
    if path.stat().st_size > max_bytes:
        raise FormatError('Source library exceeds the selected native asset size bound')
    content_libraries.read(path)
    cache_dir = native.check_cache_directory(cache_dir, game, [path.parent, destination])
    language = Path(game).resolve()/'Language/EN/EN.MO'
    params = {'path': str(path), 'sha256': file_hash(path), 'language': str(language),
              'language_sha256': file_hash(language), 'max_bytes': max_bytes}
    with new_directory(output, [path.parent, game]) as staged:
        result = native.run_worker(game, 'library-export', params, cache_dir, timeout)
        binary, text = result['payload']['artifacts']
        comparison = content_libraries.compare(path, binary['file'])
        if file_hash(path) != params['sha256'] or file_hash(language) != params['language_sha256']:
            raise FormatError('Native asset input changed during export')
        for item in (binary, text):
            if file_hash(item['file']) != item['sha256']:
                raise FormatError('Native asset output changed before staging')
            target = staged/Path(item['file']).name
            shutil.copyfile(item['file'], target)
            if file_hash(target) != item['sha256']:
                raise FormatError('Native asset output changed during staging')
        report = {'format': 'aow4mod-native-assets/1', 'source': str(path), 'source_sha256': params['sha256'],
                  'output': str(destination), 'run': result['run'], 'binding': native.public_binding(result['binding']),
                  'files': [{'name': Path(a['file']).name, 'sha256': a['sha256'], 'size': a['size']} for a in (binary, text)],
                  'comparison': comparison, 'diagnostics': native_validation.diagnostics(result['run']),
                  'status': 'data_preserved' if comparison['all_compared_data_equal'] else 'review_required',
                  'coverage': 'Official native binary CLB save and YAML export, with independent metadata/payload comparison. '
                              'A saved copy is not automatically installed. Rendering, source-image availability and recompilation remain unverified.'}
        (staged/'asset-export.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return report


def compile_library(path, game, output, cache_dir=Path('.aow4mod/native'), timeout=60, max_bytes=256*1024*1024):
    path = Path(path).resolve()
    raw = path.read_bytes()
    document = asset_yaml.load(raw)
    expected_members = asset_yaml.members(document)
    name = safe_name(document['20_FName']+'.clb')
    cache_dir = native.check_cache_directory(cache_dir, game, [path.parent, Path(output).resolve()])
    if type(max_bytes) is not int or not 1 <= max_bytes <= 2*1024*1024*1024:
        raise FormatError('Native asset size bound must be between 1 byte and 2 GiB')
    from .core import sha
    params = {'yaml_path': str(path), 'yaml_sha256': sha(raw), 'file_name': name, 'max_bytes': max_bytes}
    with new_directory(output, [path.parent, game]) as staged:
        result = native.run_worker(game, 'library-compile', params, cache_dir, timeout)
        binary, text = result['payload']['artifacts']
        actual = asset_yaml.load(Path(text['file']).read_bytes())
        changes = asset_yaml.differences(document, actual)
        compiled = content_libraries.read(binary['file'])
        observed = [{'cid': int(m['identity']['type_id'], 16), 'locator': m['identity']['locator'], 'name': m['identity']['name']}
                    for m in compiled.members]
        if changes or observed != expected_members:
            raise FormatError('Native asset compilation did not preserve requested fields/members: '+str(changes[:3]))
        if file_hash(path) != params['yaml_sha256']:
            raise FormatError('Asset YAML changed during verification')
        for item in (binary, text):
            target = staged/Path(item['file']).name
            if file_hash(item['file']) != item['sha256']:
                raise FormatError('Compiled asset changed before staging')
            shutil.copyfile(item['file'], target)
            if file_hash(target) != item['sha256']:
                raise FormatError('Compiled asset changed during staging')
        report = {'format': 'aow4mod-native-asset-compile/1', 'source': str(path), 'source_sha256': params['yaml_sha256'],
                  'output': str(Path(output).resolve()), 'library': compiled.name, 'members': len(compiled.members),
                  'run': result['run'], 'binding': native.public_binding(result['binding']),
                  'files': [{'name': Path(a['file']).name, 'sha256': a['sha256'], 'size': a['size']} for a in (binary, text)],
                  'yaml_fields_preserved': True, 'member_identities_preserved': True,
                  'empty_console_streams_skipped': result['payload']['empty_console_streams_skipped'],
                  'diagnostics': native_validation.diagnostics(result['run']),
                  'coverage': 'Native YAML-to-CLB compilation and exact native YAML/member readback. '
                              'Nonempty console texture streams, arbitrary source-asset imports and rendering remain unverified.'}
        (staged/'asset-compile.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return report
