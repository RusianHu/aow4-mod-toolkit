"""Retarget existing localized text links without replacing their parameter objects."""
import json
from pathlib import Path
import re
import uuid

from . import editing, native, native_objects, native_roundtrip, native_validation
from .core import FormatError, Node, Resource, mod_file, sha
from .filesystem import copy_directory, file_hash, inventory, reject_reparse

FORMAT = 'aow4mod-localized-link-plan/1'


def fingerprint():
    root = Path(__file__).parent
    return sha(b''.join((root/name).read_bytes() for name in
                       ('localized_links.py', 'editing.py', 'core.py', 'filesystem.py')))


def envelope(raw,allow_empty=False):
    node = Node(raw, 0, len(raw)); fields = node.mapping()
    if set(fields) not in ({1,2},{2}) or 0x401604 not in fields[2].mapping():
        raise FormatError('Expected an existing string-key / TEStringKeyReplacer link')
    key = fields[1].string() if 1 in fields else None
    if (not key and not allow_empty) or key is not None and '\0' in key:
        raise FormatError('Localized source key must be nonempty and contain no NUL')
    return node, key, fields[2].raw()


def rebuild(data, suffix, values):
    if not isinstance(values, list) or not 1 <= len(values) <= 32:
        raise FormatError('Localized links require 1..32 explicit assignments')
    jobs = {}; seen = set(); proof = []
    for row in values:
        if (not isinstance(row,dict) or set(row)-{'id','property','key','initialize'}
                or not {'id','property','key'}<=set(row) or type(row.get('initialize',False)) is not bool):
            raise FormatError('Each localized assignment needs exactly id, property and key')
        rid, prop, key = row['id'], row['property'], row['key']
        if not isinstance(rid, str) or not re.fullmatch(r'[0-9A-Fa-f]{16}', rid):
            raise FormatError('Localized assignment ID must be 16 hexadecimal digits')
        cid, field, normalized = editing.property_parts(prop)
        if not isinstance(key, str) or not re.fullmatch(r'[A-Z0-9_@.:-]{1,240}', key):
            raise FormatError('New localization keys use 1..240 uppercase ASCII identifier characters')
        identity = (int(rid, 16), cid, field)
        if identity in seen: raise FormatError('Duplicate localized field assignment')
        seen.add(identity); jobs.setdefault(int(rid, 16), []).append((cid, field, normalized, key,row.get('initialize',False)))
    objects = []
    def transform(pack, node):
        resource = Resource.parse(node)
        if resource.rid not in jobs: return node.raw()
        updates = {}; changed = []
        for cid, field, prop, key,initialize in jobs[resource.rid]:
            if cid not in resource.fields or field not in resource.fields[cid].mapping():
                raise FormatError('Localized field is absent; initialization requires a reviewed template')
            raw = resource.fields[cid].mapping()[field].raw()
            link, old, parameters = envelope(raw,initialize)
            if initialize and old:raise FormatError('Localized field already has a key; retarget without initialize')
            if key == old: raise FormatError('Localized assignment does not change its key')
            # The native localized-string reader needs key 1 before replacer 2.
            # Appending an omitted key after 2 is parsed by our offset-map reader
            # but loses the key through the actual native deserializer.
            updated = (editing.rewrite_map(Node(b'\0',0,1),{1:editing.encode_value('string',key),2:parameters})
                       if initialize else editing.rewrite_map(link,{1:editing.encode_value('string',key)}))
            if envelope(updated)[2] != parameters: raise FormatError('Localized parameters changed')
            updates.setdefault(cid, {})[field] = updated
            proof.append({'id': f'{resource.rid:016X}', 'pack': pack, 'property': prop,
                          'before': old, 'after': key, 'parameters_sha256': sha(parameters)})
            changed.append(prop)
        result = editing.rewrite_typed(node, {cid: editing.rewrite_map(resource.fields[cid], fields)
                                             for cid, fields in updates.items()})
        objects.append((resource.type_id, result[4:]))
        return result
    result = editing.transform_records(data, suffix, transform)
    if len(objects) != len(jobs) or len(proof) != len(values):
        raise FormatError('Localized resource selection is missing or ambiguous')
    return result, objects, proof


def make_plan(path, values, game, source_root=None, cache_dir=Path('.aow4mod/native'), timeout=45):
    source = Path(path).resolve()
    if not source.is_file() or source.suffix.lower() not in ('.rpk', '.acp'):
        raise FormatError('Localized links require an exact ACP or RPK file')
    root = Path(source_root).resolve() if source_root else source.parent
    if not source.is_relative_to(root): raise FormatError('Localized file is outside its Mod root')
    mod_file(root); before = inventory(root)
    data = source.read_bytes(); updated, objects, proof = rebuild(data, source.suffix.lower(), values)
    cache = native.check_cache_directory(cache_dir, game, [root])
    witnesses = []
    for cid, body in objects:
        _, witness = native_objects.roundtrip_data(game, f'{cid:08X}', body, cache, timeout)
        if not witness['comparison']['data_equal']: raise FormatError('Native reader changed a localized object')
        witnesses.append(witness)
    directory = cache/'localized-links'/uuid.uuid4().hex; directory.mkdir(parents=True)
    candidate = directory/source.name; candidate.write_bytes(updated)
    report = native_roundtrip.run(candidate, game, directory.parent/(directory.name+'-roundtrip'), cache, timeout,
                                  libraries=list(root.rglob('*.clb'))) if source.suffix.lower() == '.rpk' else None
    if report and not report['comparison']['all_serialized_data_equal']:
        raise FormatError('Native pack save changed localized content')
    if inventory(root) != before: raise FormatError('Localized source changed while planning')
    return {'format': FORMAT, 'generator_sha256': fingerprint(), 'source': str(source),
            'source_root': str(root), 'source_files': before, 'values': values,
            'game': str(Path(game).resolve()), 'binding': native.public_binding(native.profile_for(game)),
            'proof': proof, 'objects': witnesses, 'candidate_file': str(candidate),
            'candidate_sha256': sha(updated), 'native_pack': report,
            'coverage': 'Existing localization keys changed; parameter objects and every unrelated field preserved. Native object/pack readback, not catalog completeness or rendered text verification.'}


def apply_plan(plan, output):
    if plan.get('format') != FORMAT or plan.get('generator_sha256') != fingerprint():
        raise FormatError('Unsupported or outdated localized-link plan')
    root = Path(plan['source_root']).resolve(); source = Path(plan['source']).resolve(); game = Path(plan['game'])
    if not source.is_relative_to(root) or inventory(root) != plan['source_files']:
        raise FormatError('Localized Mod source changed or escaped its root')
    if native.public_binding(native.profile_for(game)) != plan['binding']:
        raise FormatError('Localized native backend changed')
    data, objects, proof = rebuild(source.read_bytes(), source.suffix.lower(), plan['values'])
    candidate = Path(plan['candidate_file']); reject_reparse(candidate)
    if proof != plan['proof'] or sha(data) != plan['candidate_sha256'] or candidate.read_bytes() != data:
        raise FormatError('Localized candidate differs from the requested key edits')
    if len(objects) != len(plan['objects']): raise FormatError('Missing localized object evidence')
    for (cid, body), witness in zip(objects, plan['objects']):
        if witness['binding'] != plan['binding'] or witness['type']['cid'] != f'{cid:08X}':
            raise FormatError('Localized object native identity differs')
        for name, expected in [('source_artifact', body), ('state_artifact', body)]:
            item = witness[name]; reject_reparse(item['file']); captured = Path(item['file']).read_bytes()
            if sha(captured) != item['sha256'] or not native_objects.compare_bodies(expected, captured)['data_equal']:
                raise FormatError('Localized object witness changed')
    report = plan['native_pack']
    if bool(report) != (source.suffix.lower() == '.rpk'): raise FormatError('Localized container evidence missing')
    if report:
        comparison = native_roundtrip.compare(candidate, Path(report['output_file']))
        if (report['binding'] != plan['binding'] or comparison != report['comparison']
                or not comparison['all_serialized_data_equal'] or native_validation.diagnostics(report['run']) != report['diagnostics']):
            raise FormatError('Localized native pack evidence changed')
    with copy_directory(root, output, [game]) as (stage, files):
        if files != plan['source_files']: raise FormatError('Localized source changed before staging')
        (stage/source.relative_to(root)).write_bytes(data)
        receipt = {'format': 'aow4mod-localized-link-receipt/1', 'output': str(Path(output).resolve()),
                   'assignments': proof, 'native_diagnostic_count': len(report['diagnostics']) if report else 0,
                   'coverage': plan['coverage'], 'gameplay_tested': False}
        (stage/'.aow4mod-localized-links.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
    return receipt
