"""Official native RPK serialization into a new, independently compared copy."""
from pathlib import Path
import json
import shutil

from .core import FormatError, Node, read_rpk
from .filesystem import file_hash, new_directory, outside
from . import native, native_validation


def compare(source, saved):
    source, saved = Path(source), Path(saved)
    before_meta, before_rows = read_rpk(source)
    after_meta, after_rows = read_rpk(saved)
    before = {r.rid: r for r in before_rows}
    after = {r.rid: r for r in after_rows}
    if len(before) != len(before_rows) or len(after) != len(after_rows):
        raise FormatError('Duplicate resource IDs prevent an unambiguous roundtrip comparison')
    changes = []
    for rid in sorted(before.keys() & after.keys()):
        a, b = before[rid], after[rid]
        left, right = a.leaves(), b.leaves()
        fields = sorted(k for k in left.keys() | right.keys() if left.get(k) != right.get(k))
        if a.type_id != b.type_id or fields:
            changes.append({'id': f'{rid:016X}', 'name': a.name, 'type_changed': a.type_id != b.type_id,
                            'changed_fields': fields})
    a_bytes, b_bytes = source.read_bytes(), saved.read_bytes()
    left_root = Node(a_bytes, 0xb0, len(a_bytes)).mapping()
    right_root = Node(b_bytes, 0xb0, len(b_bytes)).mapping()
    root_changes = [f'0x{k:X}' for k in sorted(left_root.keys() | right_root.keys()) if k != 0x0d and
                    (left_root[k].raw() if k in left_root else None) != (right_root[k].raw() if k in right_root else None)]
    def category_layout(root):
        result = {}
        for key, category in root[0x0d].mapping().items():
            kind, wrapper = category.typed()
            pack = wrapper[0x33].mapping()
            rows = pack[0x15].array()
            result[key] = {'kind': kind,
                          'wrapper': {k: v.raw() for k, v in wrapper.items() if k != 0x33},
                          'pack': {k: v.raw() for k, v in pack.items() if k != 0x15},
                          'resource_order': [row.typed()[1][0x18].uint(8) for row in rows.values()]}
        return result
    categories_equal = category_layout(left_root) == category_layout(right_root)
    # read_rpk independently checks each derived payload length against its file.
    # Map-table normalization can change that length without changing any data.
    header_metadata_equal = a_bytes[:12]+a_bytes[16:0xb0] == b_bytes[:12]+b_bytes[16:0xb0]
    missing, added = sorted(before.keys()-after.keys()), sorted(after.keys()-before.keys())
    return {'source_resources': len(before), 'saved_resources': len(after),
            'missing_ids': [f'{x:016X}' for x in missing], 'added_ids': [f'{x:016X}' for x in added],
            'changed_resources': changes, 'changed_pack_fields': root_changes,
            'category_metadata_and_resource_order_equal': categories_equal,
            'header_equal': a_bytes[:0xb0] == b_bytes[:0xb0], 'byte_identical': a_bytes == b_bytes,
            'header_metadata_equal': header_metadata_equal,
            'resource_data_equal': not (missing or added or changes),
            'all_serialized_data_equal': not (missing or added or changes or root_changes)
                                         and categories_equal and header_metadata_equal,
            'source_sha256': before_meta['sha256'], 'saved_sha256': after_meta['sha256']}


def run(path, game, output, cache_dir=Path('.aow4mod/native'), timeout=45,libraries=(),languages=(),packages=()):
    path = Path(path).resolve()
    destination = outside(output, [path.parent, game])
    if destination.exists():
        raise FormatError('Native roundtrip output directory already exists')
    context=native.library_context(libraries)
    from . import language_context,native_context
    catalogs=language_context.bind(languages)
    packs=native_context.bind(packages)
    protected=[path.parent,*[Path(p['file']).parent for p in [*context,*catalogs,*packs]]]
    outside(destination,[game,*protected])
    cache_dir = native.check_cache_directory(cache_dir, game, [destination,*protected])
    if path.suffix.casefold() != '.rpk':
        raise FormatError('Native roundtrip currently supports RPK files only')
    before = file_hash(path)
    _, resources = read_rpk(path)
    if not resources:
        raise FormatError('Empty RPK roundtrip cannot establish resource preservation')
    if len({r.rid for r in resources}) != len(resources):
        raise FormatError('Duplicate resource IDs prevent native roundtrip verification')
    registry = native.cached(game, 'types', cache_dir=cache_dir, timeout=timeout)
    known = {r['cid'] for r in registry['payload']['types']}
    missing = sorted({f'{r.type_id:08X}' for r in resources}-known)
    if missing:
        raise FormatError('Resource classes absent from native registry: '+', '.join(missing))
    language = Path(game).resolve()/'Language/EN/EN.MO'
    params = {'path': str(path), 'sha256': before, 'language': str(language), 'language_sha256': file_hash(language)}
    params['libraries']=context
    if catalogs:params['languages']=catalogs
    if packs:params['packs']=packs
    # Validate the public destination before running any native code or writing it.
    with new_directory(output, [game,*protected]) as staged:
        evidence = native.run_worker(game, 'roundtrip', params, cache_dir, timeout)
        saved = Path(evidence['payload']['output_file'])
        comparison = compare(path, saved)
        if file_hash(path) != before or file_hash(language) != params['language_sha256']:
            raise FormatError('A roundtrip input changed during verification')
        if any(file_hash(p['file'])!=p['sha256'] for p in params['libraries']):raise FormatError('Native CLB context changed during verification')
        if any(file_hash(p['file'])!=p['sha256'] for p in catalogs):raise FormatError('Native MO context changed during verification')
        if any(file_hash(p['file'])!=p['sha256'] for p in packs):raise FormatError('Native RPK context changed during verification')
        if file_hash(saved) != evidence['payload']['output_sha256']:
            raise FormatError('Native saved file changed before comparison')
        shutil.copyfile(saved, staged/path.name)
        if file_hash(staged/path.name) != evidence['payload']['output_sha256']:
            raise FormatError('Native saved file changed during output staging')
        report = {'format': 'aow4mod-native-roundtrip/1', 'source': str(path),
                  'output_file': str(Path(output).resolve()/path.name), 'run': evidence['run'],
                  'binding': native.public_binding(evidence['binding']), 'comparison': comparison,
                  'context_libraries':params['libraries'],
                  'status': ('byte_identical' if comparison['byte_identical'] else
                             'equivalent_data' if comparison['all_serialized_data_equal'] else 'review_required'),
                  'diagnostics': native_validation.diagnostics(evidence['run']),
                  'complete_compatibility_verified': False,
                  'coverage': 'Official native RPK read/save with full byte, resource ID/type/field and pack-field comparison. '
                              'Does not certify ACP overrides, loaded effective semantics, CLB/TAM assets or gameplay.'}
        if catalogs:report['context_languages']=catalogs
        if packs:report['context_packs']=packs
        (staged/'roundtrip.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return report
