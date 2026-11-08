"""Clone complete RPK definitions with new IDs and conservative link rewriting."""
from collections import Counter
import json
from pathlib import Path
import re
import secrets
import struct

from . import catalog,editing
from .authoring import safe_name,string,string_vector
from .core import FormatError,Node,Resource,decode_link,mod_file,read_acp,read_rpk,references,sha
from .filesystem import copy_directory,file_hash

FORMAT='aow4mod-clone-rpk-plan/1'


def normalized_name(name):
    if not name.lower().endswith('.rpk'):name+='.rpk'
    safe_name(name)
    if len(name.upper().encode('utf8'))>=160:raise FormatError('RPK name exceeds the fixed header capacity')
    return name


def occupied_ids(index):
    db=catalog.open_existing(index)
    try:return {int(row[0],16) for row in db.execute('SELECT DISTINCT id FROM resources')}
    finally:db.close()


def make_plan(mod,template,name,index,prefix='',game=None,selected_ids=None,with_dependencies=False):
    acp=mod_file(Path(mod));meta,changes=read_acp(acp)
    template=Path(template).resolve();info,resources=read_rpk(template);name=normalized_name(name)
    if not resources:raise FormatError('Template has no resource definitions')
    old_ids={r.rid for r in resources}
    if len(old_ids)!=len(resources):raise FormatError('Template contains duplicate resource IDs')
    requested={int(x,16) for x in selected_ids} if selected_ids else old_ids
    if not requested or requested-old_ids:raise FormatError('Selected IDs must exist in the template RPK')
    selected=set(requested)
    if with_dependencies:
        if not selected_ids:raise FormatError('Select starting resource IDs before requesting local dependencies')
        by_id={r.rid:r for r in resources};pending=list(selected)
        while pending:
            for ref in references(by_id[pending.pop()]):
                target=int(ref['id'],16)
                if target in old_ids and target not in selected:selected.add(target);pending.append(target)
    if name.casefold()==Path(info['name']).name.casefold():raise FormatError('Choose a new package name')
    if any(p.name.casefold()==name.casefold() for p in acp.parent.rglob('*.rpk')):
        raise FormatError('Destination Mod already has a pack with this filename')
    if name.upper() in {n.upper() for n in meta['registered_resource_packs']}:raise FormatError('Pack name already registered')
    used=occupied_ids(index)|old_ids|{c['resource'].rid for c in changes}
    for p in acp.parent.rglob('*.rpk'):
        _,existing=read_rpk(p);used.update(r.rid for r in existing)
    mapping={}
    for r in resources:
        if r.rid not in selected:continue
        locator=r.fields[0x14].string() if 0x14 in r.fields else ''
        match=re.fullmatch(r'\[#([0-9A-Fa-f]{16})\]\d+\\\d+',locator)
        if not match or int(match[1],16)!=r.rid:raise FormatError('Template resource has no verified locator')
        new=0
        while not new or new in used:new=secrets.randbits(64)
        used.add(new)
        mapping[f'{r.rid:016X}']={'new_id':f'{new:016X}','old_name':r.name,
                                 'new_name':prefix+r.name if r.name is not None else None,
                                 'type_id':r.type_id,'locator':locator}
    plan={'format':FORMAT,'mod_source':str(acp),'mod_sha256':meta['sha256'],
          'template':str(template),'template_sha256':info['sha256'],'old_pack_name':info['name'],
          'new_pack_name':name,'id_map':mapping,'index':str(Path(index).resolve()),
          'selection':{'mode':'subset' if selected_ids else 'complete','requested_ids':[f'{x:016X}' for x in sorted(requested)],
                       'selected_ids':[f'{x:016X}' for x in sorted(selected)],'with_local_dependencies':bool(with_dependencies)},
          'game':str(game) if game else None,
          'coverage':'Selected definitions cloned with recognized internal links remapped; unselected original resources, external assets/localization reused. No in-game test.'}
    _,proof=clone_bytes(template.read_bytes(),plan)
    plan['proof']=proof
    return plan


def clone_bytes(data,plan):
    if sha(data)!=plan['template_sha256']:raise FormatError('RPK template changed')
    mapping={int(k,16):v for k,v in plan['id_map'].items()}
    if not mapping:raise FormatError('Clone plan has no resources')
    selection=plan.get('selection',{'mode':'complete'})
    if selection.get('mode') not in ('complete','subset'):raise FormatError('Unsupported clone selection')
    partial=selection['mode']=='subset'
    if selection.get('selected_ids') is not None and {int(x,16) for x in selection['selected_ids']}!=set(mapping):
        raise FormatError('Clone selection and ID map disagree')
    new_ids=[int(v['new_id'],16) for v in mapping.values()]
    if len(new_ids)!=len(set(new_ids)) or any(not 0<n<=0xffffffffffffffff or n in mapping for n in new_ids):
        raise FormatError('New resource IDs must be unique, nonzero and different from template IDs')
    patterns=[]
    for old in mapping:
        patterns.extend([struct.pack('<Q',old),f'{old:016X}'.encode(),f'{old:016x}'.encode()])
    pattern=re.compile(b'|'.join(re.escape(p) for p in set(patterns)))
    unknown=[];link_count=0;seen_resources=[];source_ids=set()

    def locator(text,new):
        return re.sub(r'(?<=\[#)[0-9A-Fa-f]{16}',new,text,count=1)

    def walk(node,depth=0):
        nonlocal link_count
        if not pattern.search(node.raw()):return node.raw()
        link=decode_link(node)
        if link and int(link['id'],16) in mapping:
            replacement=mapping[int(link['id'],16)];new= replacement['new_id'];link_count+=1
            if 'locator' in link:
                updates={0x14:string(locator(link['locator'],new)),0x16:struct.pack('<Q',int(new,16))}
                if replacement['new_name'] is not None:updates[0x17]=string(replacement['new_name'])
                return editing.rewrite_map(node,updates)
            updates={0x14:struct.pack('<Q',int(new,16))}
            if replacement['new_name'] is not None:updates[0x16]=string(replacement['new_name'])
            return editing.rewrite_typed(node,updates)
        if depth>=48 or len(node.raw())==8:
            unknown.append({'offset':node.start,'length':node.end-node.start});return node.raw()
        # Scalar strings containing old IDs are ambiguous and must not be altered.
        try:
            node.string()
            unknown.append({'offset':node.start,'length':node.end-node.start});return node.raw()
        except FormatError:pass
        try:
            kind,fields=node.typed()
            if 0x10000<=kind<0x10000000:
                return editing.rewrite_typed(node,{k:walk(v,depth+1) for k,v in fields.items()})
        except FormatError:pass
        try:
            fields=node.mapping()
            return editing.rewrite_map(node,{k:walk(v,depth+1) for k,v in fields.items()})
        except FormatError:
            unknown.append({'offset':node.start,'length':node.end-node.start});return node.raw()

    def transform_resource(node,_):
        r=Resource.parse(node)
        if r.rid in source_ids:raise FormatError('Duplicate template resource ID')
        source_ids.add(r.rid)
        if partial and r.rid not in mapping:return None
        if r.rid not in mapping or r.type_id!=mapping[r.rid]['type_id']:
            raise FormatError('Clone plan does not describe the template resource set')
        new=mapping[r.rid];seen_resources.append(r.rid)
        updates={0x18:struct.pack('<Q',int(new['new_id'],16)),
                 0x14:string(locator(r.fields[0x14].string(),new['new_id']))}
        if r.name is not None:updates[0x15]=string(new['new_name'])
        for k,v in r.fields.items():
            if k not in updates:updates[k]=walk(v)
        return editing.rewrite_typed(node,updates)

    root=Node(data,0xb0,len(data));fields=root.mapping();new_pack=normalized_name(plan['new_pack_name']).upper()
    containers=fields[13].mapping()
    changed_containers={}
    for key,node in containers.items():
        _,outer=node.typed();pf=outer[0x33].mapping()
        objects=editing.replace_array_items(pf[0x15],[raw for i,obj in pf[0x15].array().items()
                                                    if (raw:=transform_resource(obj,i)) is not None])
        updates={0x15:objects}
        if 0x14 in pf:updates[0x14]=string(new_pack)
        for k,v in pf.items():
            if k not in updates:updates[k]=walk(v)
        changed_containers[key]=editing.rewrite_typed(node,{0x33:editing.rewrite_map(outer[0x33],updates)})
    updates={13:editing.rewrite_map(fields[13],changed_containers),0x16:string(new_pack)}
    retain_original=partial and bool(source_ids-set(mapping))
    if set(new_ids)&source_ids:raise FormatError('New ID collides with a template resource')
    if 0x1e in fields or retain_original:
        from .core import string_list
        deps=string_list(fields[0x1e]) if 0x1e in fields else []
        if retain_original:
            if plan['old_pack_name'].casefold() not in {x.casefold() for x in deps}:deps.append(plan['old_pack_name'])
        else:deps=[new_pack if x.casefold()==plan['old_pack_name'].casefold() else x for x in deps]
        updates[0x1e]=string_vector(deps)
    for k,v in fields.items():
        if k not in updates:updates[k]=walk(v)
    if unknown:raise FormatError('Old IDs occur in unrecognized data; clone refused: '+json.dumps(unknown[:8]))
    if len(seen_resources)!=len(mapping) or set(seen_resources)!=set(mapping):raise FormatError('Clone resource coverage mismatch')
    payload=editing.rewrite_map(root,updates)
    name_bytes=new_pack.encode('utf8')
    header=data[:12]+struct.pack('<I',len(payload))+name_bytes+bytes(160-len(name_bytes))
    return header+payload,{'resources':len(mapping),'internal_links_remapped':link_count,
                           'unknown_old_id_occurrences':0,'categories_and_unknown_fields_preserved':True,
                           'original_pack_dependency_retained':retain_original,'source_resources':len(source_ids)}


def verify_clone(before,after,plan):
    mapping={int(k,16):v for k,v in plan['id_map'].items()}
    selected=[r for r in before if r.rid in mapping]
    if plan.get('selection',{}).get('mode')!='subset' and len(selected)!=len(before):
        raise FormatError('Complete clone omitted source resources')
    if len(selected)!=len(after):raise FormatError('Clone resource count changed')
    for old,new in zip(selected,after):
        item=mapping[old.rid]
        if new.rid!=int(item['new_id'],16) or old.type_id!=new.type_id or new.name!=item['new_name']:
            raise FormatError('Cloned identity/type/name mismatch')
        expected=Counter((ref['property'],mapping[int(ref['id'],16)]['new_id'] if int(ref['id'],16) in mapping else ref['id']) for ref in references(old))
        observed=Counter((ref['property'],ref['id']) for ref in references(new))
        if expected!=observed:raise FormatError('Cloned reference graph differs from ID remapping')


def apply_plan(plan,output):
    if not isinstance(plan,dict) or plan.get('format')!=FORMAT:raise FormatError('Unsupported RPK clone plan')
    acp=Path(plan['mod_source']).resolve();template=Path(plan['template']).resolve();name=normalized_name(plan['new_pack_name'])
    if file_hash(acp)!=plan['mod_sha256'] or file_hash(template)!=plan['template_sha256']:
        raise FormatError('Clone inputs changed since planning')
    meta,changes=read_acp(acp);new_ids={int(v['new_id'],16) for v in plan['id_map'].values()}
    used=occupied_ids(plan['index'])|{c['resource'].rid for c in changes}
    for p in acp.parent.rglob('*.rpk'):
        _,items=read_rpk(p);used.update(r.rid for r in items)
    if used&new_ids:raise FormatError('A planned new ID now exists; create a new plan')
    if any(p.name.casefold()==name.casefold() for p in acp.parent.rglob('*.rpk')):raise FormatError('Pack filename already exists')
    clone,proof=clone_bytes(template.read_bytes(),plan)
    protected=[Path(plan['game'])] if plan.get('game') else []
    template_info,before=read_rpk(template)
    if new_ids&{r.rid for r in before}:raise FormatError('New ID collides with a template resource')
    if template_info['name']!=plan['old_pack_name']:raise FormatError('Template package identity mismatch')
    with copy_directory(acp.parent,output,protected) as (payload,source_files):
        target=payload/'Packs'/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(clone)
        clone_info,after=read_rpk(target);verify_clone(before,after,plan)
        original=acp.read_bytes();root=Node(original,19,len(original))
        registered=meta['registered_resource_packs']+[name.upper()]
        updated=original[:19]+editing.rewrite_wrapper(root,0x40009d,{0x1a:string_vector(registered)})
        (payload/acp.name).write_bytes(updated)
        new_meta,new_changes=read_acp(payload/acp.name)
        if new_meta['registered_resource_packs']!=registered or [c['resource'].node.raw() for c in changes]!=[c['resource'].node.raw() for c in new_changes]:
            raise FormatError('Pack registration changed existing overrides')
        if file_hash(template)!=plan['template_sha256']:raise FormatError('Template changed during cloning')
        if file_hash(acp)!=plan['mod_sha256']:raise FormatError('Mod ACP changed during cloning')
        receipt={'format':'aow4mod-clone-rpk-receipt/1','output':str(Path(output).resolve()),
                 'pack':name,'pack_sha256':clone_info['sha256'],'proof':proof,'id_map':plan['id_map'],
                 'source_files':source_files,'plan':plan,'gameplay_tested':False}
        (payload/'.aow4mod-clone.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return receipt
