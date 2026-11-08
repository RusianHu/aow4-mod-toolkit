"""Retarget recognized resource links while preserving the surrounding object."""
import json
from pathlib import Path
import re
import struct

from . import catalog,editing
from .core import FormatError,Node,Resource,decode_link,mod_file,sha,references,read_rpk
from .filesystem import file_hash,copy_directory

FORMAT='aow4mod-link-plan/1'
FLAT_FORMAT='aow4mod-flat-reference-edit/1'


def resource_file(path,rid,pack=None):
    path=Path(path).resolve()
    if path.is_dir():path=mod_file(path)
    matches=[(p,r) for p,r in editing.resources_in_file(path) if r.rid==int(rid,16) and
             (pack is None or p.casefold()==pack.casefold())]
    if len(matches)!=1:raise FormatError(f'Expected one resource in {path}, found {len(matches)}')
    return path,*matches[0]


def target_resource(index,rid,path=None):
    if path:
        path,pack,r=resource_file(path,rid)
        return r,path,file_hash(path)
    r,row,_=catalog.load_resource(index,rid)
    return r,Path(row['file']),row['sha256']


def branch(node,key=None):
    from .structured import children
    typed,fields,_=children(node,key)
    return typed,fields


def replace_link(node,path,target):
    if path:
        key=path[0];typed,fields=branch(node,key)
        if key not in fields:raise FormatError('Reference path does not exist')
        raw,old=replace_link(fields[key],path[1:],target)
        return (editing.rewrite_typed if typed else editing.rewrite_map)(node,{key:raw}),old
    old=decode_link(node)
    if old is None:raise FormatError('Selected path is not a recognized resource link')
    label=target.name or ''
    if 'locator' in old:
        locator=target.fields[0x14].string() if 0x14 in target.fields else ''
        match=re.fullmatch(r'\[#([0-9A-Fa-f]{16})\]\d+\\\d+',locator)
        if not match or int(match[1],16)!=target.rid:raise FormatError('Target lacks a verified resource locator')
        updates={0x14:editing.encode_value('string',locator),0x16:struct.pack('<Q',target.rid),
                 0x17:editing.encode_value('string',label)}
        return editing.rewrite_map(node,updates),old
    return editing.rewrite_typed(node,{0x14:struct.pack('<Q',target.rid),0x16:editing.encode_value('string',label)}),old


def make_plan(source,rid,property_path,target_id,index,target_file=None,old_target_file=None,
              pack=None,game=None,allow_type_change=False,source_root=None):
    source,pack,r=resource_file(source,rid,pack)
    from .resource_diff import path_keys
    keys=path_keys(property_path)
    flat=property_path.startswith('meta/')
    if flat:
        top=f'meta/0x{keys[0]:02X}'
        path=top+''.join(f'/0x{k:X}' for k in keys[1:])
    else:
        top=f'0x{keys[0]:08X}/0x{keys[1]:02X}'
        path=f'0x{keys[0]:08X}'+''.join(f'/0x{k:X}' for k in keys[1:])
    matching=[x for x in references(r) if x['property']==path]
    if len(matching)!=1:raise FormatError('Reference path is missing or ambiguous; select an exact path from refs')
    selected=matching[0]
    target,target_file,target_hash=target_resource(index,target_id,target_file)
    old,old_target_file,old_hash=target_resource(index,selected['id'],old_target_file)
    changed_type=target.type_id!=old.type_id
    if changed_type and not allow_type_change:
        raise FormatError('New target type differs; --allow-type-change stages an explicit review-required exception')
    original=r.fields[keys[0]] if flat else r.fields[keys[0]].mapping()[keys[1]]
    updated,previous=replace_link(original,keys[1:] if flat else keys[2:],target)
    if flat:
        edit={'format':FLAT_FORMAT,'source':str(source),'source_sha256':file_hash(source),'game':str(game) if game else None,
              'operations':[{'pack':pack,'id':f'{r.rid:016X}','type_id':r.type_id,'property':top,
                             'expected_hex':original.raw().hex(),'replacement_hex':updated.hex()}]}
    else:edit=editing.make_plan(source,rid,top,updated.hex(),'hex',pack,game,True)
    edit['required_inputs']={str(target_file):target_hash,str(old_target_file):old_hash}
    root=Path(source_root).resolve() if source_root else source.parent
    if not source.is_relative_to(root):raise FormatError('Source is outside the requested project root')
    imports = import_update(source,target_file,pack)
    return {'format':FORMAT,'edit':edit,'source_root':str(root),'reference_path':path,
            'before':previous,'after':{'id':f'{target.rid:016X}','name':target.name,'type_id':target.type_id},
            'previous_target':{'id':f'{old.rid:016X}','type_id':old.type_id,'file':str(old_target_file),'sha256':old_hash},
            'target':{'file':str(target_file),'sha256':target_hash},'allow_type_change':bool(allow_type_change),
            'review_required':changed_type,'imports':imports,
            'validation_level':'recognized_reference_same_target_type' if not changed_type else 'recognized_reference_type_changed',
            'coverage':'Recognized link encoding and target identity/type checked. Field-specific target constraints, activation and gameplay are not proven.'}


def import_update(source,target_file,source_pack=None):
    if target_file.suffix.lower()!='.rpk':return None
    if source.suffix.lower()=='.acp':
        if not source_pack:raise FormatError('ACP reference import update requires its source pack')
        from . import overlay_imports
        return overlay_imports.update(source.read_bytes(),source_pack,read_rpk(target_file)[0]['name'])
    if source.suffix.lower()!='.rpk':return None
    info,_=read_rpk(source);target_info,_=read_rpk(target_file)
    name=target_info['name']
    if info['name'].casefold()==name.casefold():
        if source.resolve()!=target_file.resolve() and info['sha256']!=target_info['sha256']:
            raise FormatError('Different RPK files share the same pack name; resolve the identity collision first')
        return None
    if name.casefold() in {x.casefold() for x in info['dependencies']}:return None
    from .pack_imports import identifier
    identifier(name)
    return {'before':info['dependencies'],'after':info['dependencies']+[name],'added':name}


def apply_plan(plan,output):
    if not isinstance(plan,dict) or plan.get('format')!=FORMAT:raise FormatError('Unsupported link plan')
    edit=plan['edit']
    if edit.get('format')==FLAT_FORMAT:
        if file_hash(edit['source'])!=edit['source_sha256']:raise FormatError('Flat reference source changed')
    else:editing.validate_plan(edit)
    if len(edit['operations'])!=1:raise FormatError('Link plan must contain one selected reference')
    for evidence in (plan['target'],plan['previous_target']):
        if file_hash(evidence['file'])!=evidence['sha256']:raise FormatError('Link target evidence changed')
    op=edit['operations'][0]
    verified=make_plan(edit['source'],op['id'],plan['reference_path'],plan['after']['id'],None,
        plan['target']['file'],plan['previous_target']['file'],op['pack'],edit.get('game'),
        plan['allow_type_change'],plan['source_root'])
    if verified!=plan:raise FormatError('Link plan does not match its source, target evidence or encoded change')
    changes=None
    if plan['imports']:
        relative=Path(edit['source']).relative_to(Path(plan['source_root'])).as_posix()
        changes={relative:plan['imports']}
    result=apply_flat(plan,output) if edit.get('format')==FLAT_FORMAT else editing.apply_project_plans([edit],plan['source_root'],output,changes)
    result.update(reference_path=plan['reference_path'],before=plan['before'],after=plan['after'],
                  validation_level=plan['validation_level'],review_required=plan['review_required'],coverage=plan['coverage'],imports=plan['imports'])
    return result


def apply_flat(plan,output):
    edit=plan['edit'];op=edit['operations'][0]
    source=Path(edit['source']);root=Path(plan['source_root'])
    key=int(op['property'].split('/')[1],16)
    data=source.read_bytes()
    if sha(data)!=edit['source_sha256']:raise FormatError('Flat reference source changed before staging')
    seen=[]
    def transform(pack,node):
        r=Resource.parse(node)
        if pack.casefold()!=op['pack'].casefold() or r.rid!=int(op['id'],16):return node.raw()
        if seen or r.type_id!=op['type_id'] or r.fields[key].raw().hex()!=op['expected_hex']:
            raise FormatError('Flat reference identity or selected field changed')
        seen.append(r.rid)
        return editing.rewrite_typed(node,{key:bytes.fromhex(op['replacement_hex'])})
    updated=editing.transform_records(data,source.suffix.lower(),transform,canonical_changed_wrappers=True)
    if not seen:raise FormatError('Flat reference target disappeared')
    imports=plan['imports']
    if imports:
        if source.suffix.lower()=='.rpk':
            from . import pack_imports
            updated=pack_imports.rewrite(updated,imports['after'])
        else:
            from . import overlay_imports
            updated=overlay_imports.rewrite(updated,imports['pack'],imports['after'],imports['optional'])
    protected=[Path(plan[k]['file']).parent for k in ('target','previous_target')]
    if edit.get('game'):protected.append(Path(edit['game']))
    before=editing.resources_in_file(source)
    with copy_directory(root,output,protected) as (stage,manifest):
        target=stage/source.relative_to(root);target.write_bytes(updated)
        after=editing.resources_in_file(target)
        if len(before)!=len(after):raise FormatError('Flat reference changed resource count')
        count=0
        for (bp,br),(ap,ar) in zip(before,after):
            if (bp,br.rid,br.type_id)!=(ap,ar.rid,ar.type_id):raise FormatError('Flat reference changed resource identity/order')
            a,b=br.leaves(),ar.leaves()
            for field in a.keys()|b.keys():
                if a.get(field)!=b.get(field):
                    if (bp.casefold(),br.rid,field)!=(op['pack'].casefold(),int(op['id'],16),op['property']) or b[field].hex()!=op['replacement_hex']:
                        raise FormatError('Flat reference changed an unselected field')
                    count+=1
        if file_hash(source)!=edit['source_sha256'] or any(file_hash(plan[k]['file'])!=plan[k]['sha256'] for k in ('target','previous_target')):
            raise FormatError('Flat reference input changed during staging')
        result={'format':'aow4mod-project-edit-receipt/1','source_root':str(root),'source_files':manifest,
                'output':str(Path(output).resolve()),'files':[{'source':str(source),'output_file':str(Path(output).resolve()/source.relative_to(root)),
                'result_sha256':sha(updated),'verified_changed_fields':count,'plan':edit}],'gameplay_tested':False}
        (stage/'.aow4mod-edit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return result
