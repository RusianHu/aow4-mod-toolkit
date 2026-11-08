"""Native default construction, scalar initialization and fresh-object readback."""
import json
import math
from pathlib import Path
import struct
import uuid

from .core import FormatError, Node, sha
from .filesystem import file_hash, new_directory
from . import catalog, native

CONTAINERS = {'0040009D': 'package', '0400001A': 'pack'}


def storage_fields(data):
    """Compare known class scopes without treating encoding order as data."""
    node = Node(data, 0, len(data))
    result = {}
    for key, value in node.mapping().items():
        if key >= 0x10000:
            for field, leaf in value.mapping().items():
                result[f'0x{key:08X}/0x{field:X}'] = leaf.raw()
        else:
            result[f'meta/0x{key:X}'] = value.raw()
    return result


def compare_bodies(before, after, omitted_fields=None):
    left, right = storage_fields(before), storage_fields(after)
    normalized = []
    for key, default in (omitted_fields or {}).items():
        expected = bytes.fromhex(default)
        if left.get(key) == expected or right.get(key) == expected:
            if left.get(key) == expected:
                left.pop(key)
            if right.get(key) == expected:
                right.pop(key)
            normalized.append(key)
    changed = sorted(k for k in left.keys() | right.keys() if left.get(k) != right.get(k))
    return {'data_equal': not changed, 'byte_identical': before == after, 'changed_fields': changed,
            'before_sha256': sha(before), 'after_sha256': sha(after), 'normalized_package_defaults': normalized}


def compare_mod_data(before, after):
    """Compare the known Mod-resource array structurally, without default inference.

    Native writers may change nested offset-table encodings. Resource identity,
    array indices, class IDs, operations, references and missing fields still
    participate; ambiguous/opaque data remains byte-compared.
    """
    result=compare_bodies(before,after)
    key='0x00401204/0x2'
    if key not in result['changed_fields']:return result
    from .resource_diff import compare_nodes
    left_root=Node(before,0,len(before)).mapping()
    right_root=Node(after,0,len(after)).mapping()
    if 0x401204 not in left_root or 0x401204 not in right_root:return result
    left=left_root[0x401204].mapping()
    right=right_root[0x401204].mapping()
    if 2 not in left or 2 not in right:return result
    comparison=compare_nodes(left[2],right[2])
    if comparison['traversal_complete'] and comparison['recognized_data_equal']:
        changed=[p for p in result['changed_fields'] if p!=key]
        return {**result,'changed_fields':changed,'data_equal':not changed,
                'encoding_only_fields':[key],
                'encoding_scope':'Unambiguous nested Mod-resource maps/typed objects; no omitted/default equivalence.'}
    return result


def type_evidence(game, selected, index):
    cid = selected['cid']
    if cid in CONTAINERS:
        return {'role': CONTAINERS[cid], 'evidence': 'reviewed native container class'}
    with catalog.open_existing(index) as db:
        row = db.execute("SELECT id,file FROM resources WHERE type_id=? AND origin='game' ORDER BY file,ordinal LIMIT 1",
                         (int(cid, 16),)).fetchone()
    if row is None:
        raise FormatError('Construction currently requires a class observed in an indexed original RPK')
    source = Path(row['file']).resolve()
    if (Path(game).resolve()/'Content') not in source.parents:
        raise FormatError('Resource type evidence is outside the selected game installation')
    resource, _, _ = catalog.load_resource(index, row['id'], source)
    if resource.type_id != int(cid, 16):
        raise FormatError('Indexed resource type changed')
    return {'role': 'resource', 'file': str(source), 'sha256': file_hash(source), 'resource_id': row['id']}


def normalized_value(codec, value):
    if codec in ('i32', 'enum_i32'):
        if type(value) is not int or not -2**31 <= value < 2**31:
            raise FormatError('Native integer assignments require a signed 32-bit integer')
    elif codec == 'bool':
        if type(value) is not bool:
            raise FormatError('Native bool assignments require true or false')
    elif codec == 'f32':
        if type(value) not in (int, float) or not math.isfinite(value):
            raise FormatError('Native float assignments require a finite number')
        try:
            value = struct.unpack('<f', struct.pack('<f', value))[0]
        except (OverflowError, struct.error) as exc:
            raise FormatError('Native float exceeds float32 storage') from exc
        if not math.isfinite(value):
            raise FormatError('Native float exceeds float32 storage')
    elif codec == 'string':
        if not isinstance(value, str) or '\0' in value or len(value.encode('utf-8')) > 65536:
            raise FormatError('Native string requires UTF-8 text without NUL, at most 65536 bytes')
    else:
        raise FormatError('Native object/resource/list assignments are not yet supported by this scalar setter')
    return value


def normalize_assignments(values, fields, binding, schema_path=None):
    if isinstance(values, dict):
        values = [{'name': key, 'value': value} for key, value in values.items()]
    if not isinstance(values, list) or len(values) > 200:
        raise FormatError('Values must be an object or a list of at most 200 property assignments')
    if any(f.get('codec') == 'enum_i32' and any(v.get('name') == f['name'] for v in values if isinstance(v, dict))
           for f in fields):
        if schema_path is None:
            raise FormatError('Enum assignments require the matching offline schema')
        fields = native.enrich_enums(fields, binding, schema_path)
    result, seen = [], set()
    for value in values:
        if not isinstance(value, dict) or set(value)-{'name', 'category', 'value'} or 'value' not in value:
            raise FormatError('An assignment needs name, value and optional category')
        matches = [f for f in fields if f['name'] == value.get('name') and
                   ('category' not in value or f['category'] == value['category'])]
        if len(matches) != 1:
            raise FormatError('Property name/category is missing or ambiguous: '+str(value.get('name')))
        field = matches[0]
        key = (field['category'], field['name'])
        if key in seen:
            raise FormatError('Duplicate native property assignment')
        seen.add(key)
        normalized = normalized_value(field['codec'], value['value'])
        if field['codec'] == 'enum_i32':
            allowed = field.get('enum', {}).get('values', [])
            if normalized not in {row['value'] for row in allowed}:
                raise FormatError('Enum value is absent from the verified native enum table')
        result.append({k: field[k] for k in ('name', 'category', 'codec', 'editor_cid', 'editor_parameter')} |
                      {'value': normalized})
    return result


def validate_identity(identity):
    if not isinstance(identity, dict) or set(identity) != {'id', 'name', 'locator'}:
        raise FormatError('Resource identity needs id, name and locator')
    import re
    if not isinstance(identity['id'], str) or not re.fullmatch(r'[0-9A-F]{16}', identity['id']) or int(identity['id'], 16) == 0:
        raise FormatError('Resource identity requires a nonzero 64-bit uppercase hexadecimal ID')
    if (not isinstance(identity['name'], str) or not identity['name'] or '\0' in identity['name']
            or len(identity['name'].encode('utf-8')) > 65536):
        raise FormatError('Resource identity requires a nonempty name without NUL')
    if not isinstance(identity['locator'], str):
        raise FormatError('Resource locator must be a string')
    match = re.fullmatch(r'\[#([0-9A-F]{16})\](\d+)\\(\d+)', identity['locator'])
    if not match or match[1] != identity['id']:
        raise FormatError('Resource locator and ID disagree')
    return identity


def verify_evidence(evidence):
    if evidence.get('file') and file_hash(evidence['file']) != evidence['sha256']:
        raise FormatError('Native resource type evidence changed')


def verify_result(result):
    payload = result['payload']
    before, after = Path(payload['source_file']).read_bytes(), Path(payload['output_file']).read_bytes()
    if sha(before) != payload['source_sha256'] or sha(after) != payload['output_sha256']:
        raise FormatError('Native object state changed after its worker receipt')
    package = payload['cid'] == '0040009D'
    profile = result['binding']['profile']
    # A partial/new profile may have no reviewed package-default equivalences.
    # Keep such differences visible instead of crashing or borrowing old rules.
    comparison = compare_bodies(before, after, profile.get('package_omitted_fields', {}) if package else None)
    if not comparison['data_equal']:
        raise FormatError('Fresh native object did not preserve serialized fields: '+', '.join(comparison['changed_fields'][:12]))
    for assignment, actual in zip(result['params'].get('assignments', []), payload['readbacks']):
        if (actual.get('name') != assignment['name'] or actual.get('category') != assignment['category']
                or actual.get('default_status') != 'read' or actual.get('getter_status') != 0
                or type(actual.get('default')) is not type(assignment['value']) or actual.get('default') != assignment['value']):
            raise FormatError('Native property did not survive fresh-object readback')
    if len(payload['readbacks']) != len(result['params'].get('assignments', [])):
        raise FormatError('Native object readback count mismatch')
    if package:
        fields = Node(before, 0, len(before)).mapping()[0x40009d].mapping()
        expected = {'minor': fields[0x18].uint(4) if 0x18 in fields else profile['package_read_defaults']['minor'],
                    'major': fields[0x19].uint(4) if 0x19 in fields else profile['package_read_defaults']['major']}
        if payload.get('package_version') != expected:
            raise FormatError('Native package version does not match explicit or verified omitted values')
    return after, comparison


def construct_data(game, type_value, values=None, identity=None, schema_path=None,
                   index=Path('.aow4mod/catalog.sqlite'), cache_dir=Path('.aow4mod/native'), timeout=30):
    registry = native.cached(game, 'types', cache_dir=cache_dir, timeout=timeout)
    selected = native.select_type(registry, type_value)
    evidence = type_evidence(game, selected, index)
    params = {**selected, 'role': evidence['role'], 'assignments': []}
    if evidence['role'] == 'package':
        # The native reader replaces a zero package GUID. Assign a fresh identity
        # before readback so the constructor artifact is already stable.
        params['package_id'] = str(uuid.uuid4())
    if values is not None:
        props = native.cached(game, 'properties', selected, cache_dir=cache_dir, timeout=timeout)
        params['assignments'] = normalize_assignments(values, props['payload']['properties'], props['binding'], schema_path)
    if identity is not None:
        if evidence['role'] != 'resource':
            raise FormatError('Only native resources may receive a resource identity')
        params['identity'] = validate_identity(identity)
    verify_evidence(evidence)
    result = native.run_worker(game, 'construct', params, cache_dir, timeout)
    data, comparison = verify_result(result)
    verify_evidence(evidence)
    return data, {'type': selected, 'evidence': evidence, 'assignments': params['assignments'],
                  'identity': identity, 'package_id': params.get('package_id'),
                  'run': result['run'], 'binding': native.public_binding(result['binding']),
                  'comparison': comparison, 'readbacks': result['payload']['readbacks'],
                  'state_artifact': {'file': result['payload']['output_file'], 'sha256': sha(data)}}


def roundtrip_data(game, type_value, data, cache_dir=Path('.aow4mod/native'), timeout=30):
    if not 1 <= len(data) <= 16*1024*1024:
        raise FormatError('Native object body exceeds the supported size bound')
    storage_fields(data)
    registry = native.cached(game, 'types', cache_dir=cache_dir, timeout=timeout)
    selected = native.select_type(registry, type_value)
    # This internal reader only consumes explicitly selected container/resource data.
    params = {**selected, 'body_hex': data.hex(), 'body_sha256': sha(data), 'assignments': []}
    result = native.run_worker(game, 'object-roundtrip', params, cache_dir, timeout)
    after, comparison = verify_result(result)
    return after, {'type': selected, 'run': result['run'], 'binding': native.public_binding(result['binding']),
                   'package_version': result['payload'].get('package_version'),
                   'comparison': comparison,
                   'source_artifact': {'file': result['payload']['source_file'], 'sha256': sha(data)},
                   'state_artifact': {'file': result['payload']['output_file'], 'sha256': sha(after)}}


def construct_object(game, type_value, output, values=None, schema_path=None,
                     index=Path('.aow4mod/catalog.sqlite'), cache_dir=Path('.aow4mod/native'), timeout=30,
                     values_file=None):
    cache_dir = native.check_cache_directory(cache_dir, game, [Path(output).resolve()])
    values_binding = None
    if values_file is not None:
        values_file = Path(values_file).resolve()
        raw = values_file.read_bytes()
        values = json.loads(raw.decode('utf-8-sig'))
        values_binding = {'file': str(values_file), 'sha256': sha(raw)}
    with new_directory(output, [game]) as staged:
        data, proof = construct_data(game, type_value, values, None, schema_path, index, cache_dir, timeout)
        if values_binding and file_hash(values_file) != values_binding['sha256']:
            raise FormatError('Native initialization values changed during construction')
        (staged/'state.bin').write_bytes(data)
        report = {'format': 'aow4mod-native-object/1', **proof, 'output': str(Path(output).resolve()),
                  'values_source': values_binding,
                  'state_sha256': sha(data), 'coverage': 'Native constructor/scalar setters and fresh-object binary readback. '
                  'A resource still needs package identity, references, domain configuration and a game entry point.'}
        (staged/'object.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return report
