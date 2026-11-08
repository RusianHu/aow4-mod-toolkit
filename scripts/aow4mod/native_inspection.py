"""Read actual native values after deserializing a selected ACP/RPK resource."""
from pathlib import Path

from .core import FormatError, Node, mod_file, read_acp, read_rpk, sha
from .filesystem import file_hash
from . import native, native_objects, projects


def validate_object_path(value):
    if not isinstance(value,list) or not 1<=len(value)<=8:
        raise FormatError('Nested object path requires 1..8 selectors')
    for row in value:
        if not isinstance(row,dict) or not {'name'}<=row.keys() or set(row)-{'name','category'}:
            raise FormatError('Object selectors require name and optional category')
        if any(not isinstance(v,str) or '\0' in v or len(v.encode('utf-8'))>1024 for v in row.values()) or not row['name']:
            raise FormatError('Invalid nested object selector string')
    return value


def source_body(path, rid=None, pack=None):
    path = Path(path).resolve()
    if rid is None:
        source = mod_file(path)
        if source.suffix.casefold() != '.acp':
            raise FormatError('Select an RPK resource with --id; without --id this command inspects ACP metadata')
        metadata, changes = read_acp(source)
        data = source.read_bytes()
        if metadata['sha256'] != sha(data):
            raise FormatError('Package changed while its native inspection input was captured')
        kind, _ = Node(data, 19, len(data)).typed()
        if kind != 0x40009d:
            raise FormatError('Unsupported native package class')
        return data[23:], {'file': str(source), 'sha256': sha(data), 'cid': '0040009D',
                          'package_name': metadata['name'], 'package_id': metadata.get('package_id'),
                          'serialized_version': {'major': metadata['version_major'], 'minor': metadata['version_minor']},
                          'override_count': len(changes)}
    if path.is_file() and path.suffix.casefold() == '.rpk':
        metadata, resources = read_rpk(path)
        rows = [{'resource': r, 'pack': metadata['name'], 'file': str(path)} for r in resources]
    else:
        rows = projects.inspect_project(path)['definitions']
    matches = [row for row in rows if row['resource'].rid == int(rid, 16) and
               (pack is None or row['pack'].casefold() == pack.casefold())]
    if len(matches) != 1:
        raise FormatError('Resource is missing or ambiguous; use an exact RPK path or --pack')
    row = matches[0]
    resource = row['resource']
    return resource.node.raw()[4:], {'file': row['file'], 'sha256': sha(resource.node.data),
                                    'cid': f'{resource.type_id:08X}', 'resource_id': f'{resource.rid:016X}',
                                    'resource_name': resource.name, 'pack': row['pack']}


def inspect(path, game, rid=None, pack=None, schema_path=None, cache_dir=Path('.aow4mod/native'), timeout=30, object_path=None):
    body, source = source_body(path, rid, pack)
    protected_cache = native.check_cache_directory(cache_dir, game, [Path(source['file']).parent])
    registry = native.cached(game, 'types', cache_dir=protected_cache, timeout=timeout)
    selected = native.select_type(registry, source['cid'])
    params = {**selected, 'body_hex': body.hex(), 'body_sha256': sha(body), 'assignments': [], 'include_properties': True}
    if object_path is not None:params['object_path']=validate_object_path(object_path)
    result = native.run_worker(game, 'object-roundtrip', params, protected_cache, timeout)
    if file_hash(source['file']) != source['sha256']:
        raise FormatError('Native inspection source changed during decoding')
    after = Path(result['payload']['output_file']).read_bytes()
    if sha(after) != result['payload']['output_sha256']:
        raise FormatError('Native inspection state artifact changed')
    omitted = result['binding']['profile']['package_omitted_fields'] if source['cid'] == '0040009D' else None
    comparison = native_objects.compare_bodies(body, after, omitted)
    fields = result['payload']['properties']
    if schema_path is not None:
        fields = native.enrich_enums(fields, result['binding'], schema_path)
    values = []
    for field in fields:
        row = dict(field)
        for old, new in [('default', 'value'), ('default_label', 'value_label'), ('default_status', 'value_status')]:
            if old in row:
                row[new] = row.pop(old)
        if row.get('enum') and row.get('value_status') == 'read':
            row['enum_value_known'] = row['value'] in {v['value'] for v in row['enum']['values']}
        values.append(row)
    return {'format': 'aow4mod-native-inspection/1', 'source': source, 'type': selected,
            'binding': native.public_binding(result['binding']), 'run': result['run'], 'comparison': comparison,
            'roundtrip_artifact': {'file': result['payload']['output_file'], 'sha256': sha(after)},
            'package_version': result['payload']['package_version'], 'properties': values,
            'values_read': sum(v.get('value_status') == 'read' for v in values),
            'object_path': result['payload'].get('nested_objects',[]),
            'descriptor_source': 'fresh instance of observed child class; values read from loaded child' if object_path else 'loaded resource property list',
            'source_of_values': 'owned nested object reached through the loaded parent getter' if object_path else 'fresh native object after deserializing the selected file data',
            'coverage': 'Supported primitive property values and native binary readback. '
                        'Complex links are retained in serialized comparisons, not resolved into a running game; no gameplay certification.'}
