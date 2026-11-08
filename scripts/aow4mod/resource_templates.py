"""Copy explicit resource templates into an existing pack with native witnesses."""
from collections import Counter
import json
from pathlib import Path
import re
import secrets
import struct
import uuid

from . import authoring, editing, native, native_objects, native_roundtrip, native_authoring, resource_groups
from .core import FormatError,Node,Resource,read_rpk,mod_file,references,decode_link,sha
from .filesystem import file_hash,inventory,copy_directory

FORMAT='aow4mod-resource-template-plan/1'
MAX_RESOURCES=256


def fingerprint():
    root=Path(__file__).parent
    return sha(b''.join((root/name).read_bytes() for name in (
        'resource_templates.py','resource_groups.py','native_authoring.py','editing.py','core.py','filesystem.py')))


def select(rows,ids,closure=False):
    by_id={r.rid:r for r in rows}
    if len(by_id)!=len(rows):raise FormatError('Template RPK contains duplicate resource IDs')
    selected={int(value,16) for value in ids}
    if not selected or selected-set(by_id):raise FormatError('Select existing template resource IDs')
    if closure:
        pending=list(selected)
        while pending:
            for ref in references(by_id[pending.pop()]):
                rid=int(ref['id'],16)
                if rid in by_id and rid not in selected:
                    selected.add(rid);pending.append(rid)
                    if len(selected)>MAX_RESOURCES:raise FormatError('Template dependency closure exceeds 256 resources')
    if len(selected)>MAX_RESOURCES:raise FormatError('Template copy supports at most 256 resources per plan')
    return [r for r in rows if r.rid in selected]


def copy_definition(resource,mapping):
    """Rewrite known links/identity only; refuse selected old IDs in opaque data."""
    if resource.rid not in mapping:raise FormatError('Resource is absent from template ID map')
    item=mapping[resource.rid];identity=native_objects.validate_identity(item['identity'])
    patterns=set()
    for old in mapping:
        patterns.update((struct.pack('<Q',old),f'{old:016X}'.encode(),f'{old:016x}'.encode()))
    pattern=re.compile(b'|'.join(re.escape(value) for value in patterns));visits=0
    def walk(node,depth=0):
        nonlocal visits
        visits+=1
        if visits>200000 or depth>64:raise FormatError('Template reference rewrite exceeds structural bounds')
        if not pattern.search(node.raw()):return node.raw()
        link=decode_link(node)
        if link and int(link['id'],16) in mapping:
            target=mapping[int(link['id'],16)]['identity']
            if 'locator' in link:
                return editing.rewrite_map(node,{0x14:authoring.string(target['locator']),
                    0x16:struct.pack('<Q',int(target['id'],16)),0x17:authoring.string(target['name'])})
            return editing.rewrite_typed(node,{0x14:struct.pack('<Q',int(target['id'],16)),0x16:authoring.string(target['name'])})
        if len(node.raw())==8:
            raise FormatError('Selected template ID occurs in an unrecognized 64-bit field')
        try:node.string()
        except FormatError:pass
        else:raise FormatError('Selected template ID occurs in an opaque string')
        try:kind,fields=node.typed()
        except FormatError:kind=None
        if kind is not None and 0x10000<=kind<0x10000000:
            return editing.rewrite_typed(node,{key:walk(value,depth+1) for key,value in fields.items()})
        try:fields=node.mapping()
        except FormatError as exc:raise FormatError('Selected template ID occurs in unrecognized data') from exc
        return editing.rewrite_map(node,{key:walk(value,depth+1) for key,value in fields.items()})
    updates={0x14:authoring.string(identity['locator']),0x15:authoring.string(identity['name']),
             0x18:struct.pack('<Q',int(identity['id'],16))}
    for key,value in resource.fields.items():
        if key not in updates:updates[key]=walk(value)
    data=editing.rewrite_typed(resource.node,updates)
    copied=Resource.parse(Node(data,0,len(data)))
    expected=Counter((ref['property'],mapping[int(ref['id'],16)]['identity']['id'] if int(ref['id'],16) in mapping else ref['id'])
                     for ref in references(resource))
    observed=Counter((ref['property'],ref['id']) for ref in references(copied))
    if expected!=observed:raise FormatError('Template copy changed the recognized reference graph')
    return data


def imports_for(source_info,target_info,source,target,selected,all_rows):
    if source_info['name'].casefold()==target_info['name'].casefold():
        if source.resolve()!=target.resolve() and source_info['sha256']!=target_info['sha256']:
            raise FormatError('Different template/target RPK files share the same pack name')
        return []
    dependencies=[source_info['name']] if len(selected)<len(all_rows) else list(source_info['dependencies'])
    return [value for value in dependencies if value.casefold()!=target_info['name'].casefold()]


def rebuild(target_data,rows,id_map,category,container,dependencies):
    mapping={int(key,16):value for key,value in id_map.items()}
    if set(mapping)!={row.rid for row in rows}:raise FormatError('Template selection and ID map disagree')
    new_ids=[int(item['identity']['id'],16) for item in mapping.values()]
    if len(set(new_ids))!=len(new_ids) or set(new_ids)&set(mapping):raise FormatError('Template new IDs collide')
    result=target_data;objects=[];placements=[]
    for row in rows:
        place=resource_groups.placement(result,category,container)
        identity=native_objects.validate_identity(mapping[row.rid]['identity'])
        expected=f'[#{identity["id"]}]{place["category_id"]}\\{place["position"]}'
        if identity['locator']!=expected or mapping[row.rid]['type_id']!=row.type_id or mapping[row.rid]['source_name']!=row.name:
            raise FormatError('Template identity map differs from its type or allocated locator')
        data=copy_definition(row,mapping)
        result,proof=resource_groups.append(result,data,category,dependencies,container)
        objects.append(data);placements.append(proof)
    return result,objects,placements


def make_plan(mod,template,ids,into_pack,game,index,category='Copies',container=None,prefix='Copy - ',
              name=None,with_dependencies=False,cache_dir=Path('.aow4mod/native'),timeout=45):
    ids=list(ids)
    acp=mod_file(Path(mod));before=inventory(acp.parent);target=native_authoring.append_target(acp,into_pack)
    template=Path(template).resolve();info,all_rows=read_rpk(template);target_info,_=read_rpk(target)
    selected=select(all_rows,ids,with_dependencies)
    if name is not None and len(selected)!=1:raise FormatError('--name requires one copied resource without a larger closure')
    if not isinstance(prefix,str) or '\0' in prefix:raise FormatError('Invalid template name prefix')
    occupied=native_authoring.used_ids(acp,index)|{r.rid for r in all_rows}
    target_data=target.read_bytes();place=resource_groups.placement(target_data,category,container)
    mapping={}
    for offset,row in enumerate(selected):
        rid=0
        while not rid or rid in occupied:rid=secrets.randbits(64)
        occupied.add(rid)
        identity={'id':f'{rid:016X}','name':name if name is not None else prefix+(row.name or f'Resource {row.rid:016X}'),
                  'locator':f'[#{rid:016X}]{place["category_id"]}\\{place["position"]+offset}'}
        native_objects.validate_identity(identity)
        mapping[f'{row.rid:016X}']={'identity':identity,'type_id':row.type_id,'source_name':row.name}
    dependencies=imports_for(info,target_info,template,target,selected,all_rows)
    data,objects,placements=rebuild(target_data,selected,mapping,category,container,dependencies)
    cache=native.check_cache_directory(cache_dir,game,[acp.parent,template])
    proofs=[]
    for row,obj in zip(selected,objects):
        after,proof=native_objects.roundtrip_data(game,f'{row.type_id:08X}',obj[4:],cache,timeout)
        if not proof['comparison']['data_equal']:raise FormatError('Copied resource changed through fresh native object readback')
        proofs.append(proof)
    directory=cache/'resource-templates'/uuid.uuid4().hex;directory.mkdir(parents=True)
    candidate=directory/target.name;candidate.write_bytes(data)
    report=native_roundtrip.run(candidate,game,directory.parent/(directory.name+'-roundtrip'),cache,timeout,
                                libraries=list(acp.parent.rglob('*.clb')))
    if not report['comparison']['all_serialized_data_equal']:raise FormatError('Copied pack differs from its native read/save evidence')
    if inventory(acp.parent)!=before or file_hash(template)!=info['sha256']:
        raise FormatError('Template or Mod changed during resource-copy planning')
    return {'format':FORMAT,'generator_sha256':fingerprint(),'source':str(acp),'source_files':before,
        'template':{'file':str(template),'sha256':info['sha256']},'selected_ids':[f'{r.rid:016X}' for r in selected],
        'requested_ids':list(ids),'with_local_dependencies':bool(with_dependencies),'id_map':mapping,
        'target':{'file':str(target),'relative':target.relative_to(acp.parent).as_posix(),'sha256':target_info['sha256']},
        'category':category,'container':container,'dependencies':dependencies,'placements':placements,
        'game':str(Path(game).resolve()),'index':str(Path(index).resolve()),'native_objects':proofs,
        'candidate_file':str(candidate),'pack_hex':data.hex(),'pack_sha256':sha(data),'native_roundtrip':report,
        'binding':native.public_binding(native.profile_for(game)),
        'coverage':'Explicit template definitions and recognized internal links copied; source assets/localization reused. Native object/pack preservation, not effective gameplay certification.'}


def apply_plan(plan,output):
    if not isinstance(plan,dict) or plan.get('format')!=FORMAT or plan.get('generator_sha256')!=fingerprint():
        raise FormatError('Unsupported or outdated resource-copy plan')
    acp=Path(plan['source']).resolve();game=Path(plan['game']).resolve()
    if inventory(acp.parent)!=plan['source_files']:raise FormatError('Resource-copy Mod source changed')
    if native.public_binding(native.profile_for(game))!=plan['binding']:raise FormatError('Native resource-copy backend changed')
    template=Path(plan['template']['file']);target=native_authoring.append_target(acp,plan['target']['relative'])
    if file_hash(template)!=plan['template']['sha256'] or str(target)!=plan['target']['file'] or file_hash(target)!=plan['target']['sha256']:
        raise FormatError('Resource-copy template or target changed')
    info,all_rows=read_rpk(template);rows=select(all_rows,plan['requested_ids'],plan['with_local_dependencies'])
    if [f'{r.rid:016X}' for r in rows]!=plan['selected_ids']:raise FormatError('Resource-copy closure changed')
    target_info,_=read_rpk(target)
    dependencies=imports_for(info,target_info,template,target,rows,all_rows)
    if dependencies!=plan['dependencies']:raise FormatError('Resource-copy imports differ from the template')
    occupied=native_authoring.used_ids(acp,plan['index'])|{r.rid for r in all_rows}
    if occupied & {int(item['identity']['id'],16) for item in plan['id_map'].values()}:
        raise FormatError('Resource-copy new ID now exists')
    data,objects,placements=rebuild(target.read_bytes(),rows,plan['id_map'],plan['category'],plan['container'],dependencies)
    if placements!=plan['placements'] or data.hex()!=plan['pack_hex'] or sha(data)!=plan['pack_sha256']:
        raise FormatError('Resource-copy plan changes data outside the selected templates/target append')
    candidate=Path(plan['candidate_file'])
    if (plan['native_roundtrip']['binding']!=plan['binding'] or file_hash(candidate)!=sha(data)
            or not native_roundtrip.compare(candidate,Path(plan['native_roundtrip']['output_file']))['all_serialized_data_equal']):
        raise FormatError('Resource-copy native pack evidence changed')
    if len(objects)!=len(plan['native_objects']):raise FormatError('Resource-copy native object coverage is incomplete')
    for obj,proof,row in zip(objects,plan['native_objects'],rows):
        if proof['binding']!=plan['binding'] or proof['type']['cid']!=f'{row.type_id:08X}':
            raise FormatError('Resource-copy native object binding/type differs')
        source=proof['source_artifact'];state=proof['state_artifact']
        if (file_hash(source['file'])!=source['sha256'] or Path(source['file']).read_bytes()!=obj[4:]
                or file_hash(state['file'])!=state['sha256'] or not native_objects.compare_bodies(obj[4:],Path(state['file']).read_bytes())['data_equal']):
            raise FormatError('Resource-copy native object evidence differs from its template')
    with copy_directory(acp.parent,output,[game,template]) as (stage,source_files):
        if source_files!=plan['source_files']:raise FormatError('Resource-copy source changed before staging')
        (stage/plan['target']['relative']).write_bytes(data)
        receipt={'format':'aow4mod-resource-template-receipt/1','output':str(Path(output).resolve()),
                 'target':plan['target']['relative'],'id_map':plan['id_map'],'copied_resources':len(objects),
                 'native_data_preserved':True,'diagnostic_count':len(plan['native_roundtrip']['diagnostics']),
                 'coverage':plan['coverage'],'gameplay_tested':False}
        (stage/'.aow4mod-resource-copy.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
        if file_hash(template)!=plan['template']['sha256']:raise FormatError('Resource-copy template changed during staging')
    return receipt
