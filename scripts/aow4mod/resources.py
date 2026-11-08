"""Project-scoped resource naming, local removal, and original-override reversion."""
import json
from pathlib import Path
import re
import struct

from . import catalog,editing,structured
from .core import FormatError,Node,Resource,decode_link,mod_file,references,sha
from .filesystem import copy_directory,file_hash

FORMAT='aow4mod-resource-plan/1'


def project_files(mod):
    acp=mod_file(Path(mod));root=acp.parent
    files=sorted({*root.rglob('*.rpk'),*root.rglob('*.acp')})
    records={str(p.resolve()):editing.resources_in_file(p) for p in files}
    return root,records


def rename_links(node,rid,name,counter,depth=0):
    if depth>64:raise FormatError('Reference-label update exceeds structural depth limit')
    link=decode_link(node)
    if link:
        if int(link['id'],16)!=rid:return node.raw()
        if link['name']==name:return node.raw()
        counter[0]+=1
        return (editing.rewrite_map(node,{0x17:editing.encode_value('string',name)}) if 'locator' in link else
                editing.rewrite_typed(node,{0x16:editing.encode_value('string',name)}))
    try:node.string();return node.raw()
    except FormatError:pass
    try:typed,fields,_=structured.children(node)
    except FormatError:return node.raw()
    values={k:rename_links(v,rid,name,counter,depth+1) for k,v in fields.items()}
    return (editing.rewrite_typed if typed else editing.rewrite_map)(node,values)


def external_references(index,rid,root):
    if not index:return {'status':'not_checked','references':[]}
    db=catalog.open_existing(index)
    try:
        missing=db.execute('SELECT count(*) FROM files WHERE links_indexed=0 AND error IS NULL').fetchone()[0]
        if missing:return {'status':'index_has_unscanned_files','files':missing,'references':[]}
        rows=db.execute('''SELECT r.file,r.id,r.name,l.property FROM links l
                         JOIN resources r ON l.source=r.row_id WHERE l.target=? ORDER BY r.file,r.id,l.property''',(f'{rid:016X}',))
        refs=[dict(r) for r in rows if not Path(r['file']).resolve().is_relative_to(root)]
        return {'status':'indexed_scope_checked','references':refs}
    finally:db.close()


def prepare(mod,rid,action,name=None,index=None,allow_dangling=False,game=None):
    if action not in ('rename','remove','revert'):raise FormatError('Unknown resource action')
    root,files=project_files(mod);rid=int(rid,16)
    targets=[(file,pack,r) for file,rs in files.items() for pack,r in rs if r.rid==rid]
    if len(targets)!=1:raise FormatError('Resource ID is absent or defined more than once in this project')
    target_file,target_pack,target=targets[0]
    if action=='remove' and Path(target_file).suffix.lower()!='.rpk':raise FormatError('Use revert to remove an ACP override')
    if action=='revert' and Path(target_file).suffix.lower()!='.acp':raise FormatError('Revert applies only to an ACP override')
    if action=='rename' and (not isinstance(name,str) or not name.strip() or '\0' in name or len(name.encode('utf8'))>4096):
        raise FormatError('An internal resource name must be nonempty text without NUL, up to 4096 UTF-8 bytes')
    if action!='rename' and name is not None:raise FormatError('Only rename accepts a new name')
    incoming=[];unknown=[];ref_ranges={file:[] for file in files}
    for file,rs in files.items():
        for _,r in rs:
            if file==target_file and r.rid==rid:continue
            for ref in references(r):
                if int(ref['id'],16)!=rid:continue
                incoming.append({'file':file,'id':f'{r.rid:016X}','name':r.name,'property':ref['property']})
                keys=[int(k,16) for k in ref['property'].split('/')]
                try:
                    node=structured.find_node(r.fields[keys[0]],keys[1:]);ref_ranges[file].append((node.start,node.end))
                except FormatError:pass
    external=external_references(index,rid,root) if action=='remove' else {'status':'not_needed_for_stable_id_operation','references':[]}
    if action=='remove':
        needles={struct.pack('<Q',rid),f'{rid:016X}'.encode(),f'{rid:016x}'.encode()}
        pattern=re.compile(b'|'.join(re.escape(x) for x in needles))
        for file in files:
            accepted=list(ref_ranges[file])
            if file==target_file:accepted.append((target.node.start,target.node.end))
            for match in pattern.finditer(Path(file).read_bytes()):
                if not any(start<=match.start() and match.end()<=end for start,end in accepted):
                    unknown.append({'file':file,'offset':match.start()})
        if unknown:raise FormatError('Resource ID occurs in unrecognized data; removal refused: '+json.dumps(unknown[:8]))
        if (incoming or external['references']) and not allow_dangling:
            raise FormatError('Resource has incoming references; retarget them first or explicitly stage with --allow-dangling')
    outputs={};expected={};label_count=[0]
    for file,rs in files.items():
        expected[file]=[]
        def transform(pack,node):
            r=Resource.parse(node)
            if file==target_file and r.rid==rid and action in ('remove','revert'):return None
            updates={}
            if action=='rename':
                if r.rid==rid:updates[0x15]=editing.encode_value('string',name)
                for key,value in r.fields.items():
                    if key>=0x10000:updates[key]=rename_links(value,rid,name,label_count)
            raw=editing.rewrite_typed(node,updates)
            expected[file].append((pack,r.rid,r.type_id,sha(raw)))
            return raw
        before=Path(file).read_bytes();after=editing.transform_records(before,Path(file).suffix.lower(),transform)
        if after!=before:outputs[file]=after
    plan={'format':FORMAT,'source_root':str(root),'id':f'{rid:016X}','action':action,'new_name':name,
          'target_file':target_file,'target_pack':target_pack,'old_name':target.name,'type_id':target.type_id,
          'index':str(Path(index).resolve()) if index else None,'allow_dangling':bool(allow_dangling),
          'game':str(Path(game).resolve()) if game else None,
          'input_hashes':{file:file_hash(file) for file in files},'output_hashes':{file:sha(raw) for file,raw in outputs.items()},
          'incoming_project_references':incoming,'external_references':external,
          'proof':{'changed_files':len(outputs),'reference_labels_refreshed':label_count[0],
                   'resource_definitions_removed':int(action in ('remove','revert')),'resource_ids_preserved':True},
          'review_required':action=='remove' and bool(incoming or external['references'] or external['status']!='indexed_scope_checked'),
          'coverage':'Internal editor names only, not localization text. Removal affects local definitions; revert drops an override and does not emit an engine-delete operation. External/opaque graph coverage is limited.'}
    return plan,outputs,expected


def make_plan(*args,**kwargs):return prepare(*args,**kwargs)[0]


def apply_plan(plan,output):
    if not isinstance(plan,dict) or plan.get('format')!=FORMAT:raise FormatError('Unsupported resource plan')
    for file,expected_hash in plan['input_hashes'].items():
        if file_hash(file)!=expected_hash:raise FormatError('Resource plan input changed')
    current,outputs,expected=prepare(plan['source_root'],plan['id'],plan['action'],plan['new_name'],plan.get('index'),
                                     plan['allow_dangling'],plan.get('game'))
    if current!=plan:raise FormatError('Resource plan or reference graph changed; create a new plan')
    root=Path(plan['source_root']);protected=[Path(plan['game'])] if plan.get('game') else []
    with copy_directory(root,output,protected) as (payload,source_manifest):
        for file,raw in outputs.items():
            target=payload/Path(file).relative_to(root);target.write_bytes(raw)
            actual=[(pack,r.rid,r.type_id,sha(r.node.raw())) for pack,r in editing.resources_in_file(target)]
            if actual!=expected[file]:raise FormatError('Resource transformation verification failed')
        for file,h in plan['input_hashes'].items():
            if file_hash(file)!=h:raise FormatError('Input changed while staging')
        receipt={'format':'aow4mod-resource-receipt/1','output':str(Path(output).resolve()),'plan':plan,
                 'source_files':source_manifest,'proof':plan['proof'],'review_required':plan['review_required'],'gameplay_tested':False}
        (payload/'.aow4mod-resource.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return receipt
