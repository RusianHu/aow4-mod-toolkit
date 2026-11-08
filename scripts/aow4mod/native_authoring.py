"""Create native-backed projects and resources without an existing Mod template."""
import json
from pathlib import Path
import struct
import uuid

from . import authoring, catalog, cloning, editing, native, native_edits, native_objects, native_roundtrip, projects, resource_groups
from .core import FormatError, Node, Resource, mod_file, read_acp, read_rpk, sha
from .filesystem import copy_directory, file_hash, inventory, new_directory

PLAN_FORMAT = 'aow4mod-native-resource-plan/1'
ACP_HEADER = b'CPR\0'+bytes(12)+b'\x01\0\0'


def encode_map(fields):
    return editing.rewrite_map(Node(b'\0', 0, 1), fields)


def encode_array(items):
    return encode_map({1: encode_map(dict(enumerate(items)))})


def create_project(game, name, author, output, cache_dir=Path('.aow4mod/native'), timeout=30):
    authoring.safe_name(name)
    cache_dir = native.check_cache_directory(cache_dir, game, [Path(output).resolve()])
    for ancestor in Path(output).resolve().parents:
        if ancestor.is_dir() and any(ancestor.glob('*.acp')):
            raise FormatError('A new authoring project must not be nested inside an existing Mod')
    with new_directory(output, [game]) as staged:
        data, defaults = native_objects.construct_data(game, '0040009D', cache_dir=cache_dir, timeout=timeout)
        template = ACP_HEADER+struct.pack('<I', 0x40009d)+data
        blank, package_id = authoring.blank_from_template(template, name, author)
        body, verification = native_objects.roundtrip_data(game, '0040009D', blank[23:], cache_dir, timeout)
        actual = ACP_HEADER+struct.pack('<I', 0x40009d)+body
        mod = staged/'mod'
        (mod/'.metadata').mkdir(parents=True)
        acp = mod/(name+'.acp')
        acp.write_bytes(actual)
        metadata, changes = read_acp(acp)
        if (changes or metadata['package_id'] != package_id or metadata['registered_resource_packs']
                or metadata['name'] != name or (metadata['author'] or '') != author
                or verification['package_version'] != {'major': 0, 'minor': 1}):
            raise FormatError('Native project identity/content validation failed')
        (mod/'.metadata/metadata.json').write_text(json.dumps({
            'name': name, 'version': '0.1', 'id': package_id, 'short_description': '', 'tags': ['Mod']},
            ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        config=authoring.basic_config(name,'0.1');config['validation']={'native':True}
        (staged/authoring.CONFIG).write_text(json.dumps(config,
            ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        proof = {'creation_backend': 'official_native_constructor', 'new_package_id': package_id,
                 'existing_mod_template_used': False, 'gameplay_content_copied': False,
                 'defaults': defaults, 'metadata_readback': verification}
        (staged/'project-origin.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return {'project': str(Path(output).resolve()), 'package_id': package_id, 'version': '0.1',
            'backend': 'official_native_constructor', 'existing_mod_template_used': False,
            'native_metadata_readback_verified': True, 'gameplay_tested': False}


def resource_pack_bytes(pack_defaults, object_body, cid, pack_name, category='Resources', dependencies=()):
    pack_name = cloning.normalized_name(pack_name)
    root = Node(pack_defaults, 0, len(pack_defaults))
    fields = root.mapping()
    if 0x17 not in fields or fields[0x0d].mapping():
        raise FormatError('Native resource-pack default layout is not an empty pack')
    resource = struct.pack('<I', int(cid, 16))+object_body
    Resource.parse(Node(resource, 0, len(resource)))
    if not isinstance(category, str) or not category or '\0' in category:
        raise FormatError('Resource category requires a nonempty name without NUL')
    feature = fields[0x17].mapping()
    if 0x1d in feature and feature[0x1d].raw() not in (b'\0', b'\x01\x01\0\0'):
        raise FormatError('Native default feature has unexpected nonempty metadata')
    category_entry = struct.pack('<I', 0x40100e)+encode_map({
        0x0a: authoring.string(category), 0x0b: struct.pack('<I', 0)})
    category_data = editing.rewrite_map(fields[0x17], {
        0x14: authoring.string(pack_name.upper()), 0x15: encode_array([resource]),
        0x18: encode_array([category_entry]), 0x1d: None})
    wrapper = struct.pack('<I', 0x0400017f)+encode_map({0x32: struct.pack('<I', 64), 0x33: category_data})
    updates = {0x0d: encode_map({0: wrapper}), 0x16: authoring.string(pack_name.upper())}
    if dependencies:
        if any(not isinstance(x, str) or '\0' in x or not x for x in dependencies):
            raise FormatError('Invalid resource-pack dependency')
        updates[0x1e] = authoring.string_vector(list(dict.fromkeys(dependencies)))
    data = editing.rewrite_map(root, updates)
    header = bytearray(0xb0)
    header[:4] = b'RPK\0'
    struct.pack_into('<I', header, 4, 0x0400001a)
    struct.pack_into('<I', header, 12, len(data))
    encoded = pack_name.upper().encode('utf-8')[:30]
    header[16:16+len(encoded)] = encoded
    return bytes(header)+data


def used_ids(acp, index):
    _, changes = read_acp(acp)
    result = cloning.occupied_ids(index) | {c['resource'].rid for c in changes}
    for path in acp.parent.rglob('*.rpk'):
        _, resources = read_rpk(path)
        result.update(r.rid for r in resources)
    return result


def append_target(acp, value):
    value = Path(value)
    target = (value if value.is_absolute() else acp.parent/value).resolve()
    if not target.is_relative_to(acp.parent) or target.suffix.casefold() != '.rpk' or not target.is_file():
        raise FormatError('Append target must be an existing RPK inside the selected Mod')
    if len([p for p in acp.parent.rglob('*.rpk') if p.name.casefold()==target.name.casefold()]) != 1:
        raise FormatError('Append target RPK filename is ambiguous inside the Mod')
    project = projects.inspect_project(acp)
    if not any(Path(row['path']).resolve()==target and row['registered'] for row in project['packs']):
        raise FormatError('Append target must be reachable from the ACP registered pack/import graph')
    return target


def initialized_building_script(before,after,rid):
    """Only official first-load owner binding of four empty building callbacks."""
    old=next((r for r in read_rpk(before)[1] if r.rid==rid),None)
    new=next((r for r in read_rpk(after)[1] if r.rid==rid),None)
    if old is None or new is None or old.type_id!=0x4714ad or new.type_id!=old.type_id:
        raise FormatError('Empty script initialization requires a new regular building')
    comparison=native_objects.compare_bodies(old.node.raw()[4:],new.node.raw()[4:])
    if comparison['changed_fields']!=['0x004714AD/0x70']:
        raise FormatError('Building initialization changes fields outside its empty script')
    a=old.fields[0x4714ad].mapping()[0x70].mapping();b=new.fields[0x4714ad].mapping()[0x70].mapping()
    if set(a)!={0x47432e} or set(b)!=set(a):raise FormatError('Unsupported building script type')
    a=a[0x47432e].mapping();b=b[0x47432e].mapping()
    if set(a)!={0,3,4,0xb,0xc,0xd,0xe} or set(b)!=set(a):raise FormatError('Building script callback set differs')
    if any(a[k].raw()!=b[k].raw() for k in (0,3,4)):raise FormatError('Building script settings changed')
    for key,trigger in ((0xb,0),(0xc,1),(0xd,3),(0xe,2)):
        x=a[key].mapping();y=b[key].mapping()
        if set(x)!={2,5,7,0x14,0x15} or set(y)!=set(x)|{6}:
            raise FormatError('Building empty callback layout differs')
        if x[5].uint(4)!=4 or x[0x15].uint(4)!=trigger or y[6].uint(4)!=2:
            raise FormatError('Building callback kind/trigger differs')
        if any(x[k].raw()!=y[k].raw() for k in (2,5,0x14,0x15)):
            raise FormatError('Building callback expressions changed')
        f=x[7].mapping();g=y[7].mapping()
        empty=b'\0'*16+b'\xff'*12+b'\0'*4
        if (set(f)!={0xa} or set(g)!={0,0xa} or f[0xa].raw()!=empty
                or g[0xa].raw()!=empty or g[0].uint(8)!=rid):
            raise FormatError('Building callback is nonempty or binds another owner')
    expected=native_edits.patch_file(Path(before).read_bytes(),'.rpk',f'{rid:016X}',read_rpk(before)[0]['name'],
        '004714AD',editing.rewrite_map(Node(old.node.raw()[4:],0,len(old.node.raw())-4),
            {0x4714ad:editing.rewrite_map(old.fields[0x4714ad],{0x70:new.fields[0x4714ad].mapping()[0x70].raw()})}))
    comparison=native_roundtrip.compare(before,after)
    if (comparison['missing_ids'] or comparison['added_ids'] or comparison['changed_pack_fields']
            or not comparison['category_metadata_and_resource_order_equal']
            or comparison['changed_resources']!=[{'id':f'{rid:016X}','name':old.name,'type_changed':False,'changed_fields':['0x004714AD/0x70']}]
            or any(r.node.raw()!=n.node.raw() for r,n in zip(read_rpk(before)[1],read_rpk(after)[1]) if r.rid!=rid)):
        raise FormatError('Building initialization changes other pack/resource data')
    # Compare resource bytes, retaining the official writer's independent header.
    expected_row=next(r for r in read_rpk_bytes(expected) if r.rid==rid)
    if expected_row.node.raw()!=new.node.raw():raise FormatError('Building initialization changes unrelated serialized bytes')
    return {'resource_id':f'{rid:016X}','property':'0x004714AD/0x70','empty_callbacks_bound':4,
            'source':str(before),'source_sha256':file_hash(before),'output':str(after),'output_sha256':file_hash(after)}


def read_rpk_bytes(data):
    # Use the existing in-memory resource layout reader; no temporary game file.
    return [row['resource'] for group in resource_groups.layout(data)[2] for row in group['resources']]


def make_resource_plan(path, game, type_value, name, pack_name=None, values=None,
                       category='Resources', dependencies=(), schema_path=None,
                       index=Path('.aow4mod/catalog.sqlite'), cache_dir=Path('.aow4mod/native'), timeout=45,
                       into_pack=None, container=None, initialize_empty_script=False):
    acp = mod_file(Path(path))
    if initialize_empty_script and into_pack is None:
        raise FormatError('Empty building script initialization currently requires appending to an existing pack')
    dependencies = list(dependencies)
    cache_dir = native.check_cache_directory(cache_dir, game, [acp.parent])
    original = inventory(acp.parent)
    if (pack_name is None)==(into_pack is None):
        raise FormatError('Choose a new --pack name or --into-pack, exclusively')
    if container is not None and (into_pack is None or type(container) is not int or container<0):
        raise FormatError('--container requires an existing pack and a nonnegative integer')
    append = None; target = None
    if into_pack is not None:
        target = append_target(acp,into_pack); existing = target.read_bytes()
        place = resource_groups.placement(existing,category,container)
        pack_name = target.name
        append = {'file':str(target),'relative':target.relative_to(acp.parent).as_posix(),
                  'sha256':sha(existing),'category':category,'container':container,
                  'dependencies':list(dependencies),'generator_sha256':file_hash(resource_groups.__file__)}
    else:
        pack_name = cloning.normalized_name(pack_name)
        if any(p.name.casefold() == pack_name.casefold() for p in acp.parent.rglob('*.rpk')):
            raise FormatError('New resource pack filename already exists')
    occupied = used_ids(acp, index)
    rid = uuid.uuid4().int & ((1 << 64)-1)
    while rid == 0 or rid in occupied:
        rid = uuid.uuid4().int & ((1 << 64)-1)
    suffix = f'{place["category_id"]}\\{place["position"]}' if append else '0\\0'
    identity = {'id': f'{rid:016X}', 'name': name, 'locator': f'[#{rid:016X}]{suffix}'}
    native_objects.validate_identity(identity)
    body, object_proof = native_objects.construct_data(game, type_value, values, identity, schema_path, index, cache_dir, timeout)
    if initialize_empty_script and object_proof['type']['cid']!='004714AD':
        raise FormatError('Empty script initialization is reviewed only for TStructureUpgradeR')
    if object_proof['evidence']['role'] != 'resource':
        raise FormatError('A Mod resource requires a class observed in an original RPK')
    if append:
        resource = struct.pack('<I',int(object_proof['type']['cid'],16))+body
        data,append['proof'] = resource_groups.append(existing,resource,category,dependencies,container)
        pack_proof = None
    else:
        defaults, pack_proof = native_objects.construct_data(game, '0400001A', cache_dir=cache_dir, timeout=timeout)
        data = resource_pack_bytes(defaults, body, object_proof['type']['cid'], pack_name, category, dependencies)
    directory = native.check_cache_directory(cache_dir, game, [acp.parent])/'authoring'/uuid.uuid4().hex
    directory.mkdir(parents=True)
    candidate = directory/pack_name
    candidate.write_bytes(data)
    libraries = list(acp.parent.rglob('*.clb'))
    report = native_roundtrip.run(candidate, game, directory.parent/(directory.name+'-roundtrip'), cache_dir, timeout,
                                  libraries=libraries)
    initialization=None
    if not report['comparison']['all_serialized_data_equal']:
        if not initialize_empty_script:raise FormatError('New resource pack did not preserve its data through the official native writer')
        initialization=initialized_building_script(candidate,Path(report['output_file']),rid)
        first=report;canonical=Path(report['output_file']).read_bytes()
        report=native_roundtrip.run(Path(first['output_file']),game,directory.parent/(directory.name+'-initialized-roundtrip'),cache_dir,timeout,libraries=libraries)
        if not report['comparison']['all_serialized_data_equal']:raise FormatError('Initialized building script is unstable on independent pack reload')
        initialization['first_roundtrip']=first
    else:canonical = data if append else Path(report['output_file']).read_bytes()
    info, resources = read_rpk(Path(report['output_file']))
    matches = [row for row in resources if row.rid==rid and row.name==name]
    expected_count = append['proof']['resource_count_before']+1 if append else 1
    if len(resources) != expected_count or len(matches)!=1:
        raise FormatError('Native-created resource identity mismatch')
    if inventory(acp.parent) != original:
        raise FormatError('Mod source changed during native resource planning')
    return {'format': PLAN_FORMAT, 'mod_source': str(acp), 'source_files': original,
            'generator_sha256': file_hash(__file__),
            'game': str(Path(game).resolve()), 'index': str(Path(index).resolve()),
            'new_pack_name': pack_name, 'identity': identity, 'type': object_proof['type'],
            'pack_mode':'append' if append else 'new','append':append,
            'candidate_file':str(initialization['output']) if initialization and append else str(candidate) if append else None,
            'script_initialization':initialization,
            'pack_hex': canonical.hex(), 'pack_sha256': sha(canonical),
            'native_pack_file': report['output_file'], 'native_object': object_proof,
            'native_pack_defaults': pack_proof, 'native_roundtrip': report,
            'readiness': 'draft resource; references, domain configuration and game entry point still need authoring',
            'gameplay_tested': False}


def apply_resource_plan(plan, output):
    if not isinstance(plan, dict) or plan.get('format') != PLAN_FORMAT:
        raise FormatError('Unsupported native resource plan')
    source = Path(plan['mod_source']).resolve()
    if plan.get('generator_sha256') != file_hash(__file__):
        raise FormatError('Native resource generator changed since planning; create a new plan')
    game = Path(plan['game']).resolve()
    if inventory(source.parent) != plan['source_files']:
        raise FormatError('Mod source changed since native resource planning')
    values_source=plan.get('values_source')
    if values_source and file_hash(values_source['file']) != values_source['sha256']:
        raise FormatError('Native initialization values changed since planning')
    expected = plan['native_object']['binding']
    if native.public_binding(native.profile_for(game)) != expected:
        raise FormatError('Native authoring backend/library changed since planning')
    native_objects.verify_evidence(plan['native_object']['evidence'])
    mode = plan.get('pack_mode','new')
    if mode not in ('new','append'):
        raise FormatError('Unsupported resource pack creation mode')
    data = bytes.fromhex(plan['pack_hex'])
    artifact = plan.get('candidate_file') if mode=='append' else plan['native_pack_file']
    if sha(data) != plan['pack_sha256'] or not artifact or file_hash(artifact) != plan['pack_sha256']:
        raise FormatError('Native resource artifact differs from its reviewed plan')
    name = cloning.normalized_name(plan['new_pack_name'])
    identity = native_objects.validate_identity(plan['identity'])
    if int(identity['id'], 16) in used_ids(source, plan['index']):
        raise FormatError('Planned native resource ID now exists; create a new plan')
    if mode=='new' and any(p.name.casefold() == name.casefold() for p in source.parent.rglob('*.rpk')):
        raise FormatError('Planned native pack filename now exists')
    info, resources = read_rpk(Path(artifact))
    matches = [r for r in resources if r.rid==int(identity['id'],16)]
    relative = Path('Packs')/name
    expected_count = 1; expected_name = name.upper()
    if mode=='append':
        spec = plan['append']
        if spec['generator_sha256'] != file_hash(resource_groups.__file__):
            raise FormatError('Resource group generator changed since planning')
        target = append_target(source,spec['relative'])
        if str(target)!=spec['file'] or file_hash(target)!=spec['sha256'] or target.name!=name:
            raise FormatError('Existing resource pack target changed since planning')
        state = plan['native_object']['state_artifact']
        body = Path(state['file']).read_bytes()
        if sha(body)!=state['sha256'] or plan['native_object']['identity']!=identity or plan['native_object']['type']!=plan['type']:
            raise FormatError('Native constructor state differs from the appended resource plan')
        encoded = struct.pack('<I',int(plan['type']['cid'],16))+body
        expected_data, proof = resource_groups.append(target.read_bytes(),encoded,spec['category'],spec['dependencies'],spec['container'])
        initialization=plan.get('script_initialization')
        if initialization:
            if file_hash(initialization['source'])!=sha(expected_data):raise FormatError('Building initialization source differs from constructor append')
            actual=initialized_building_script(Path(initialization['source']),Path(initialization['output']),int(identity['id'],16))
            if actual!={k:v for k,v in initialization.items() if k!='first_roundtrip'}:
                raise FormatError('Building initialization proof changed')
            expected_data=Path(initialization['output']).read_bytes()
        if data!=expected_data or proof!=spec['proof']:
            raise FormatError('Append plan changes data outside the allocated new resource/category/imports')
        if not native_roundtrip.compare(Path(artifact),Path(plan['native_pack_file']))['all_serialized_data_equal']:
            raise FormatError('Appended resource pack differs from its native read/save evidence')
        relative = target.relative_to(source.parent)
        expected_count = proof['resource_count_before']+1
        expected_name = read_rpk(target)[0]['name']
    if (len(resources)!=expected_count or len(matches)!=1 or matches[0].name!=identity['name']
            or matches[0].type_id!=int(plan['type']['cid'],16) or info['name']!=expected_name
            or matches[0].fields[0x14].string()!=identity['locator']):
        raise FormatError('Native plan identity/type/name does not match the saved pack')
    metadata, changes = read_acp(source)
    with copy_directory(source.parent, output, [game]) as (staged, source_files):
        if source_files != plan['source_files']:
            raise FormatError('Mod source changed before staging the native resource plan')
        pack = staged/relative
        pack.parent.mkdir(parents=True, exist_ok=True)
        pack.write_bytes(data)
        original = source.read_bytes()
        registered = metadata['registered_resource_packs']+([name.upper()] if mode=='new' else [])
        updated = (original[:19]+editing.rewrite_wrapper(Node(original, 19, len(original)), 0x40009d,
                   {0x1a: authoring.string_vector(registered)})) if mode=='new' else original
        (staged/source.name).write_bytes(updated)
        after_meta, after_changes = read_acp(staged/source.name)
        if after_meta['registered_resource_packs'] != registered or [c['resource'].node.raw() for c in changes] != [c['resource'].node.raw() for c in after_changes]:
            raise FormatError('Native resource registration changed existing overrides')
        receipt = {'format': 'aow4mod-native-resource-receipt/1', 'output': str(Path(output).resolve()),
                   'pack': name, 'identity': identity, 'type': plan['type'], 'pack_sha256': sha(data),
                   'pack_mode':mode,'pack_relative':relative.as_posix(),'append':plan.get('append'),
                   'native_data_preservation_verified': True, 'native_diagnostics': plan['native_roundtrip']['diagnostics'],
                   'readiness': plan['readiness'], 'source_files': source_files, 'gameplay_tested': False}
        (staged/'.aow4mod-native-resource.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return receipt
