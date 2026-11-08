"""Create ACP overrides by cloning exact, hash-verified existing resources."""
import json
from pathlib import Path
import re
import struct

from . import catalog, editing
from .core import FormatError,Node,Resource,mod_file,read_acp,read_rpk,sha
from .filesystem import copy_directory,file_hash

FORMAT='aow4mod-add-override-plan/1'


def new_map(fields):
    return editing.rewrite_map(Node(b'\0',0,1),fields)


def typed(kind,fields):return struct.pack('<I',kind)+new_map(fields)


def string(text):
    raw=text.encode('utf8');return struct.pack('<I',len(raw))+raw


def array(values):return new_map({1:new_map(dict(enumerate(values)))})


def append_array(node,raw):
    items=node.array();inner=node.mapping()[1]
    return editing.rewrite_map(node,{1:editing.rewrite_map(inner,{len(items):raw})})


def find_source(index,rid,source_file=None):
    if source_file:
        source_file=Path(source_file).resolve()
        info,resources=read_rpk(source_file)
        matches=[r for r in resources if r.rid==int(rid,16)]
        if len(matches)!=1:raise FormatError('Source RPK does not contain exactly one matching resource')
        return info['name'],matches[0],source_file,info['sha256']
    db=catalog.open_existing(index)
    try:
        rows=db.execute("SELECT file,package FROM resources WHERE id=? AND origin='game'",(f'{int(rid,16):016X}',)).fetchall()
        if len(rows)!=1:raise FormatError('Original resource is missing/ambiguous; specify --source-file')
        r,row,_=catalog.load_resource(index,rid,rows[0]['file'])
        return rows[0]['package'],r,Path(row['file']),row['sha256']
    finally:db.close()


def make_plan(mod,rid,index,source_file=None,game=None):
    acp=mod_file(Path(mod));meta,changes=read_acp(acp)
    if any(c['resource'].rid==int(rid,16) for c in changes):raise FormatError('Mod already overrides this resource; use edit-plan')
    pack,resource,source,fingerprint=find_source(index,rid,source_file)
    locator=resource.fields[0x14].string() if 0x14 in resource.fields else ''
    match=re.fullmatch(r'\[#([0-9A-Fa-f]{16})\]\d+\\\d+',locator)
    if not match or int(match[1],16)!=resource.rid:raise FormatError('Source resource lacks a verified locator')
    return {'format':FORMAT,'mod_source':str(acp),'mod_sha256':meta['sha256'],
            'resource_source':str(source),'resource_source_sha256':fingerprint,
            'resource_id':f'{resource.rid:016X}','resource_type':resource.type_id,'resource_name':resource.name,
            'pack':pack,'locator':locator,'resource_sha256':sha(resource.node.raw()),
            'game':str(game) if game else None,
            'note':'Adds an exact resource override. No property changes, new resource IDs or publication.'}


def append_override(data,pack,resource,locator):
    root=Node(data,19,len(data));kind,outer=root.typed()
    if kind!=0x40009d:raise FormatError('Unsupported ACP')
    fields=outer[kind].mapping()
    if 0x25 in fields and 0x26 in fields and fields[0x25].uint(4)!=len(fields[0x26].raw()):
        raise FormatError('Unexpected change-blob length field')
    change=typed(0x401206,{0x401206:new_map({1:resource.node.raw(),2:struct.pack('<I',0x40),3:string(locator)})})
    groups=fields[0x26].array() if 0x26 in fields else {}
    matching=[]
    for i,node in groups.items():
        pk,po=node.typed()
        if pk!=0x401204:raise FormatError('Unsupported pack change record')
        pf=po[pk].mapping()
        if pf[1].string().casefold()==pack.casefold():matching.append((i,node,pf))
    if len(matching)>1:raise FormatError('Duplicate ACP pack groups are unsupported')
    if matching:
        i,node,pf=matching[0]
        group=editing.rewrite_wrapper(node,0x401204,{2:append_array(pf[2],change)})
        outer_list=fields[0x26].mapping()[1]
        blob=editing.rewrite_map(fields[0x26],{1:editing.rewrite_map(outer_list,{i:group})})
    else:
        group=typed(0x401204,{0x401204:new_map({1:string(pack),2:array([change])})})
        blob=append_array(fields[0x26],group) if 0x26 in fields else array([group])
    return data[:19]+editing.rewrite_wrapper(root,kind,{0x25:struct.pack('<I',len(blob)),0x26:blob})


def apply_plan(plan,output):
    if not isinstance(plan,dict) or plan.get('format')!=FORMAT:raise FormatError('Unsupported override plan')
    acp=Path(plan['mod_source']).resolve();source=Path(plan['resource_source']).resolve()
    if file_hash(acp)!=plan['mod_sha256'] or file_hash(source)!=plan['resource_source_sha256']:
        raise FormatError('Override input changed since planning')
    info,resources=read_rpk(source)
    matches=[r for r in resources if f'{r.rid:016X}'==plan['resource_id']]
    if len(matches)!=1:raise FormatError('Source resource changed')
    resource=matches[0]
    if info['name']!=plan['pack'] or resource.type_id!=plan['resource_type'] or sha(resource.node.raw())!=plan['resource_sha256']:
        raise FormatError('Override plan does not match source resource')
    if resource.fields[0x14].string()!=plan['locator']:raise FormatError('Locator changed')
    _,before=read_acp(acp)
    if any(c['resource'].rid==resource.rid for c in before):raise FormatError('Override already exists')
    updated=append_override(acp.read_bytes(),plan['pack'],resource,plan['locator'])
    protected=[Path(plan['game'])] if plan.get('game') else []
    with copy_directory(acp.parent,output,protected) as (payload,manifest):
        target=payload/acp.name;target.write_bytes(updated)
        _,after=read_acp(target)
        old={(c['pack'],c['resource'].rid):c['resource'].node.raw() for c in before}
        new={(c['pack'],c['resource'].rid):c['resource'].node.raw() for c in after}
        expected={**old,(plan['pack'],resource.rid):resource.node.raw()}
        if new!=expected or len(after)!=len(before)+1:raise FormatError('Unexpected resource changes after adding override')
        if file_hash(source)!=plan['resource_source_sha256']:raise FormatError('Original resource source changed while staging')
        if file_hash(acp)!=plan['mod_sha256']:raise FormatError('Mod ACP changed while staging')
        result={'format':'aow4mod-override-receipt/1','output':str(Path(output).resolve()),
                'output_file':str(Path(output).resolve()/acp.name),'resource_id':plan['resource_id'],
                'overrides_added':1,'result_sha256':sha(updated),'source_files':manifest,'plan':plan,'gameplay_tested':False}
        (payload/'.aow4mod-override.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return result
