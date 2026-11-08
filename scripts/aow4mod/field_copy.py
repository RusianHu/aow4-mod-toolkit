"""Atomic, hash-bound field transfers between explicitly paired resource definitions."""
from collections import defaultdict
import json
from pathlib import Path

from . import editing, structured, resource_diff
from .core import FormatError, Node, Resource, sha
from .filesystem import copy_directory, file_hash, inventory, outside, reject_reparse

FORMAT = 'aow4mod-field-copy-plan/1'
SPEC = 'aow4mod-field-copy-spec/1'
MAX_OPERATIONS = 1024
MAX_TRANSFER_BYTES = 32 * 1024 * 1024
IDENTITY_FIELDS = {0x13, 0x14, 0x15, 0x17, 0x18, 0x12C}


def fingerprint():
    return {name: file_hash(Path(__file__).with_name(name)) for name in
            ('field_copy.py', 'resource_diff.py', 'editing.py', 'structured.py', 'core.py', 'filesystem.py')}


def resource(path, rid, pack=None):
    path = Path(path).resolve()
    if not isinstance(rid, str):
        raise FormatError('Resource IDs must be hexadecimal strings')
    try:
        value = int(rid, 16)
    except ValueError as exc:
        raise FormatError('Invalid resource ID') from exc
    if not 0 < value <= 0xFFFFFFFFFFFFFFFF:
        raise FormatError('Resource ID outside 64-bit range')
    return structured.source_resource(path, rid, pack)


def slot(r, keys):
    """Return parent layout and field, retaining typed ancestry and array positions."""
    if keys[0] in IDENTITY_FIELDS:
        raise FormatError('Resource identity/metadata fields cannot be copied; use the dedicated identity commands')
    if keys[0] >= 0x10000 and len(keys) < 2:
        raise FormatError('Whole class sections cannot be copied')
    if len(keys) == 1:
        return r.node, True, r.fields, keys[0], (r.type_id, 'meta', keys[0])
    if keys[0] not in r.fields:
        raise FormatError('Property ancestor is absent; no class/container is created')
    node = r.fields[keys[0]]
    # Array element indices may differ between same-type component copies.
    # Every other path key and typed-object boundary must still match.
    signature = [r.type_id, keys[0]]
    array_items = False
    for key in keys[1:-1]:
        typed, fields, cid = structured.children(node, key)
        if cid is not None:
            signature.append(('type', cid))
        signature.append(('index',) if array_items else key)
        try:
            node.array()
        except FormatError:
            is_array = False
        else:
            is_array = True
        array_items = is_array and key == 1
        node = fields[key]
    key = keys[-1]
    try:
        typed, fields, cid = structured.children(node, key)
    except FormatError:
        typed, fields, cid = structured.children(node)
    if array_items:
        raise FormatError('Copying/removing individual array elements requires array-plan; choose a field or whole array property')
    try:
        node.array()
    except FormatError:
        pass
    else:
        raise FormatError('Array envelope internals cannot be copied as fields')
    if cid is not None:
        signature.append(('type', cid))
    signature.append(key)
    return node, typed, fields, key, tuple(signature)


def replace_slot(r, keys, replacement):
    parent, typed, fields, key, _ = slot(r, keys)
    if len(keys) == 1:
        return editing.rewrite_typed(r.node, {key: replacement})
    rewritten = (editing.rewrite_typed if typed else editing.rewrite_map)(parent, {key: replacement})
    branch = r.fields[keys[0]]
    updated = structured.replace(branch, keys[1:-1], rewritten) if len(keys) > 2 else rewritten
    return editing.rewrite_typed(r.node, {keys[0]: updated})


def normalize_requests(root, spec, spec_directory=None):
    if not isinstance(spec, dict) or set(spec) != {'format', 'operations'} or spec['format'] != SPEC:
        raise FormatError('Expected field-copy spec with format and operations only')
    if not isinstance(spec['operations'], list) or not 1 <= len(spec['operations']) <= MAX_OPERATIONS:
        raise FormatError(f'Field-copy needs 1..{MAX_OPERATIONS} operations')
    result = []
    for row in spec['operations']:
        if not isinstance(row, dict) or set(row) - {'file', 'id', 'pack', 'property', 'template', 'missing'}:
            raise FormatError('Unknown field-copy operation fields')
        if not {'file', 'id', 'property', 'template'} <= set(row):
            raise FormatError('Each operation needs file, id, property and template')
        template = row['template']
        if not isinstance(template, dict) or set(template) - {'file', 'id', 'pack', 'property'} or not {'file', 'id'} <= set(template):
            raise FormatError('Template needs file and id, with optional pack/property')
        if row.get('missing', 'error') not in ('error', 'remove'):
            raise FormatError('missing must be error or explicit remove')
        if not isinstance(row['file'], str) or not isinstance(template['file'], str):
            raise FormatError('File paths must be strings')
        target = (root / row['file']).resolve()
        if target == root or root not in target.parents:
            raise FormatError('Target file is outside the Mod source tree')
        ref = (Path(spec_directory or root) / template['file']).resolve()
        reject_reparse(ref)
        keys = resource_diff.path_keys(row['property'])
        refkeys = resource_diff.path_keys(template.get('property', row['property']))
        result.append({'file': target.relative_to(root).as_posix(), 'id': row['id'], 'pack': row.get('pack'),
                       'property': resource_diff.path_text(keys), 'missing': row.get('missing', 'error'),
                       'template': {'file': str(ref), 'id': template['id'], 'pack': template.get('pack'),
                                    'property': resource_diff.path_text(refkeys)}})
    return result


def prepare(root, requests):
    operations = []
    paths = defaultdict(list)
    inputs = {}
    originals = {}
    records = {}
    total = 0
    for request in requests:
        target, pack, dest = resource(root / request['file'], request['id'], request['pack'])
        ref = request['template']
        source, refpack, template = resource(ref['file'], ref['id'], ref['pack'])
        inputs[str(source)] = file_hash(source)
        keys = resource_diff.path_keys(request['property'])
        other_keys = resource_diff.path_keys(ref['property'])
        _, _, before_fields, key, signature = slot(dest, keys)
        _, _, ref_fields, refkey, other_signature = slot(template, other_keys)
        if dest.type_id != template.type_id or signature != other_signature:
            raise FormatError('Template and destination must use the same resource type, field and typed container path')
        if refkey not in ref_fields and request['missing'] != 'remove':
            raise FormatError('Template field is absent; use missing: remove only to restore that omission')
        identity = (request['file'].casefold(), pack.casefold(), dest.rid)
        if any(keys[:len(p)] == p or p[:len(keys)] == keys for p in paths[identity]):
            raise FormatError('Overlapping or duplicate destination field operations')
        paths[identity].append(keys)
        before = before_fields[key].raw() if key in before_fields else None
        after = ref_fields[refkey].raw() if refkey in ref_fields else None
        total += len(after or b'')
        if total > MAX_TRANSFER_BYTES:
            raise FormatError('Field-copy payload exceeds 32 MiB')
        current = records.get(identity, dest)
        changed = replace_slot(current, keys, after)
        records[identity] = Resource.parse(Node(changed, 0, len(changed)))
        originals.setdefault(request['file'], target.read_bytes())
        operations.append({**request, 'pack': pack, 'id': f'{dest.rid:016X}', 'name': dest.name,
                           'type_id': f'0x{dest.type_id:08X}',
                           'template': {**ref, 'pack': refpack, 'id': f'{template.rid:016X}', 'name': template.name},
                           'before_hex': before.hex() if before is not None else None,
                           'replacement_hex': after.hex() if after is not None else None,
                           'action': 'unchanged' if before == after else 'remove' if after is None else 'add' if before is None else 'replace',
                           'value_source': 'template_serialized' if after is not None else 'template_omission',
                           'nested_comparison': resource_diff.compare_nodes(before_fields.get(key), ref_fields.get(refkey))})
    updated = {}
    for rel, data in originals.items():
        seen = set()
        def transform(pack, node):
            r = Resource.parse(node)
            identity = (rel.casefold(), pack.casefold(), r.rid)
            if identity not in records:
                return node.raw()
            if identity in seen:
                raise FormatError('Duplicate target resource identity')
            seen.add(identity)
            return records[identity].node.raw()
        updated[rel] = editing.transform_records(data, Path(rel).suffix.lower(), transform)
        if seen != {key for key in records if key[0] == rel.casefold()}:
            raise FormatError('Not all field-copy targets were transformed')
    return operations, inputs, updated


def make_plan(source_root, spec, *, allow_opaque=False, spec_directory=None, game=None):
    if not allow_opaque:
        raise FormatError('Field-copy requires --allow-opaque: serialized template copying does not validate field semantics or dependencies')
    root = Path(source_root).resolve()
    before = inventory(root)
    requests = normalize_requests(root, spec, spec_directory)
    operations, inputs, updated = prepare(root, requests)
    if inventory(root) != before or any(file_hash(p) != h for p, h in inputs.items()):
        raise FormatError('Input changed during field-copy planning')
    return {'format': FORMAT, 'source_root': str(root), 'source_inventory': before,
            'game': str(Path(game).resolve()) if game else None, 'backend_sha256': fingerprint(),
            'requests': requests, 'required_inputs': inputs, 'operations': operations,
            'output_sha256': {rel: sha(data) for rel, data in updated.items()},
            'summary': {'operations': len(operations), 'changed_operations': sum(op['action'] != 'unchanged' for op in operations),
                        'files': len(updated), 'resources': len({(op['file'], op['pack'], op['id']) for op in operations}),
                        'template_omissions': sum(op['value_source'] == 'template_omission' for op in operations)},
            'validation_level': 'structural_template_copy', 'gameplay_tested': False,
            'note': 'Exact selected serialized fields only. Resource identity, unselected bytes and import declarations stay unchanged. '
                    'References keep their IDs; investigate dependency closure and use native build before installing. '
                    'Omitted template fields are never interpreted as zero/false or runtime defaults.'}


def validate(plan):
    if not isinstance(plan, dict) or plan.get('format') != FORMAT:
        raise FormatError('Unsupported field-copy plan')
    if plan.get('backend_sha256') != fingerprint():
        raise FormatError('Field-copy backend changed; regenerate the plan')
    root = Path(plan['source_root']).resolve()
    if inventory(root) != plan['source_inventory']:
        raise FormatError('Source tree changed since field-copy planning')
    for path, expected in plan['required_inputs'].items():
        if file_hash(path) != expected:
            raise FormatError('A field-copy template changed since planning')
    spec = {'format': SPEC, 'operations': plan['requests']}
    requests = normalize_requests(root, spec)
    operations, inputs, updated = prepare(root, requests)
    if requests != plan['requests'] or operations != plan['operations'] or inputs != plan['required_inputs']:
        raise FormatError('Field-copy operations disagree with their bound templates/requests')
    if 'canonicalization' in plan:
        updated = canonical_payload(plan, updated, plan['canonicalization'])
    if {rel: sha(data) for rel, data in updated.items()} != plan['output_sha256']:
        raise FormatError('Field-copy output hash differs from the reconstructed operations')
    if plan.get('validation_level') != 'structural_template_copy' or plan.get('gameplay_tested') is not False:
        raise FormatError('Invalid field-copy evidence scope')
    return root, updated


def canonical_payload(plan, updated, witnesses):
    """Adopt only selected field encodings from a frozen independent pack readback."""
    from . import native_roundtrip
    if not isinstance(witnesses, list) or not witnesses or len(witnesses) > len(updated):
        raise FormatError('Canonicalization requires bounded readback witnesses')
    seen = set()
    result = dict(updated)
    for witness in witnesses:
        if not isinstance(witness, dict) or set(witness) != {'file', 'report', 'report_sha256', 'source', 'source_sha256', 'saved', 'saved_sha256'}:
            raise FormatError('Invalid canonicalization witness')
        rel = witness['file']
        if rel in seen or rel not in updated or Path(rel).suffix.lower() != '.rpk':
            raise FormatError('Readback must uniquely select an edited RPK')
        seen.add(rel)
        for pathkey, hashkey in [('report', 'report_sha256'), ('source', 'source_sha256'), ('saved', 'saved_sha256')]:
            if file_hash(witness[pathkey]) != witness[hashkey]:
                raise FormatError('Native readback witness changed')
        report = json.loads(Path(witness['report']).read_text(encoding='utf-8-sig'))
        if report.get('format') != 'aow4mod-native-roundtrip/1' or not isinstance(report.get('diagnostics'), list) or not report.get('binding'):
            raise FormatError('Expected a complete native-roundtrip report with retained binding and diagnostics')
        if Path(report['source']).resolve() != Path(witness['source']).resolve() or Path(report['output_file']).resolve() != Path(witness['saved']).resolve():
            raise FormatError('Readback paths disagree with the frozen witness')
        if sha(updated[rel]) != witness['source_sha256']:
            raise FormatError('Native readback did not read this exact planned candidate')
        comparison = native_roundtrip.compare(Path(witness['source']), Path(witness['saved']))
        if comparison != report['comparison']:
            raise FormatError('Native readback comparison disagrees with the frozen files')
        if comparison['missing_ids'] or comparison['added_ids'] or comparison['changed_pack_fields'] or not comparison['category_metadata_and_resource_order_equal'] or not comparison['header_metadata_equal']:
            raise FormatError('Readback changes pack structure or identities')
        a = editing.resources_in_file(Path(witness['source']))
        b = editing.resources_in_file(Path(witness['saved']))
        if len(a) != len(b):
            raise FormatError('Readback resource count changed')
        native_resources = {}
        current = {}
        for (ap, ar), (bp, br) in zip(a, b):
            if (ap, ar.rid, ar.type_id) != (bp, br.rid, br.type_id):
                raise FormatError('Readback resource identity/order changed')
            if not resource_diff.compare_nodes(ar.node, br.node)['recognized_data_equal']:
                raise FormatError('Readback changes values, types or opaque/ambiguous data; cannot canonicalize')
            key = (ap.casefold(), ar.rid)
            if key in current:
                raise FormatError('Duplicate readback resource identity')
            current[key] = ar
            native_resources[key] = br
        selected_paths = defaultdict(list)
        for op in plan['operations']:
            if op['file'] != rel:
                continue
            key = (op['pack'].casefold(), int(op['id'], 16))
            node = resource_diff.select(native_resources[key], op['property'])
            original = bytes.fromhex(op['replacement_hex']) if op['replacement_hex'] is not None else None
            expected = Node(original, 0, len(original)) if original is not None else None
            if not resource_diff.compare_nodes(expected, node)['recognized_data_equal']:
                raise FormatError('Readback selected field differs from its template value')
            selected_paths[key].append(resource_diff.path_keys(op['property']))
        for key, paths in selected_paths.items():
            if not selected_encoding_only(current[key].node, native_resources[key].node, paths):
                raise FormatError('Readback normalization extends outside selected fields')
            current[key] = native_resources[key]
        # The strict release comparator must now pass, without adopting an
        # unselected normalization or weakening its comparison rules.
        if any(current[key].leaves() != native_resources[key].leaves() for key in current):
            raise FormatError('Readback normalization extends outside selected fields')
        result[rel] = editing.transform_records(updated[rel], '.rpk',
            lambda pack, node: current[(pack.casefold(), Resource.parse(node).rid)].node.raw())
    return result


def selected_encoding_only(before, after, paths):
    """Container offset/order changes may propagate along selected field paths.

    Every unselected sibling still requires byte identity. Only a selected
    subtree may use the conservative recognized-layout comparison.
    """
    if before is None or after is None:
        return before is None and after is None
    if before.raw() == after.raw():
        return True
    if [] in paths:
        return resource_diff.compare_nodes(before, after)['recognized_data_equal']
    if not paths:
        return False
    try:
        at, af, ac = structured.children(before)
        bt, bf, bc = structured.children(after)
    except FormatError:
        return False
    if (at, ac) != (bt, bc) or af.keys() != bf.keys():
        return False
    return all(selected_encoding_only(af[k], bf[k], [p[1:] for p in paths if p[0] == k]) for k in af)


def canonicalize_plan(plan, readbacks):
    if 'canonicalization' in plan:
        raise FormatError('Canonicalize the original field-copy plan once with all selected readbacks')
    _, updated = validate(plan)
    witnesses = []
    for value in readbacks:
        path = Path(value).resolve()
        report = json.loads(path.read_text(encoding='utf-8-sig'))
        source, saved = Path(report['source']).resolve(), Path(report['output_file']).resolve()
        digest = file_hash(source)
        matching = [rel for rel, data in updated.items() if sha(data) == digest]
        if len(matching) != 1:
            raise FormatError('Native readback source does not uniquely match a planned RPK')
        witnesses.append({'file': matching[0], 'report': str(path), 'report_sha256': file_hash(path),
                          'source': str(source), 'source_sha256': digest,
                          'saved': str(saved), 'saved_sha256': file_hash(saved)})
    candidate = canonical_payload(plan, updated, witnesses)
    result = {**plan, 'canonicalization': witnesses, 'output_sha256': {rel: sha(data) for rel, data in candidate.items()}}
    validate(result)
    return result


def apply_plan(plan, output):
    root, updated = validate(plan)
    protected = [Path(p).parent for p in plan['required_inputs']]
    for witness in plan.get('canonicalization', []):
        protected.extend(Path(witness[k]).parent for k in ('report', 'source', 'saved'))
    if plan.get('game'):
        protected.append(Path(plan['game']))
    outside(output, protected)
    with copy_directory(root, output, protected) as (stage, before):
        for rel, data in updated.items():
            (stage / rel).write_bytes(data)
            # Parse every result, including ACP operation guards, before committing.
            editing.resources_in_file(stage / rel)
        actual = inventory(stage)
        expected = dict(before)
        expected.update({rel: {'sha256': sha(data), 'size': len(data)} for rel, data in updated.items()})
        if actual != expected:
            raise FormatError('Unplanned file change during field-copy staging')
        validate(plan)
        result = {'format': 'aow4mod-field-copy-receipt/1', 'source_root': str(root),
                  'output': str(Path(output).resolve()), 'source_inventory': before,
                  'output_inventory': actual, 'summary': plan['summary'],
                  'plan': plan, 'gameplay_tested': False}
        (stage / '.aow4mod-field-copy.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return result
