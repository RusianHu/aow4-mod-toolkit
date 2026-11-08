"""Review serialized equipment-set candidates; never claim an executed engine lookup."""
from collections import defaultdict
from pathlib import Path

from . import catalog,native,projects
from .core import FormatError,decode_link,mod_file,read_rpk,sha,string_list
from .filesystem import file_hash

CID=0x471d33
REVIEWED_DLL='58b7210aa024d26cc0c148083d4aca7a322082a75ec3740a26dc2c4b9d4a8eff'


def declaration(resource,file,origin):
    if resource.type_id!=CID:raise FormatError('Expected TEquipmentSet')
    fields=resource.fields[CID].mapping() if CID in resource.fields else {}
    name=fields[0x33].string() if 0x33 in fields else ''
    priority=fields[0x34].uint(4) if 0x34 in fields else 0
    if priority>=0x80000000:priority-=0x100000000
    subsets=string_list(fields[0x35]) if 0x35 in fields else []
    equipment=[]
    if 0x32 in fields:
        outer=fields[0x32].mapping()
        if set(outer)!={0x1e}:raise FormatError('Unsupported equipment-set reference-list envelope')
        for node in outer[0x1e].array().values():
            link=decode_link(node)
            if link is None:raise FormatError('Unsupported equipment-set reference element')
            if int(link['id'],16):equipment.append(link['id'])
    return {'id':f'{resource.rid:016X}','name':name,'priority':priority,'equipment':equipment,
            'subsets':subsets,'file':str(file),'origin':origin,'body_sha256':sha(resource.node.raw())}


def resolve(rows,name,required_item=None):
    if not isinstance(name,str) or not name or '\0' in name:raise FormatError('Select a nonempty exact set name')
    by_name=defaultdict(list)
    for row in rows:by_name[row['name']].append(row)
    selected={};shadowed={};issues=[];items=set();visits=0
    def visit(current,stack):
        nonlocal visits
        visits+=1
        if len(stack)>64 or visits>10000:raise FormatError('Equipment-set traversal exceeded its bound')
        if current in stack:
            issues.append({'issue':'subset_cycle','path':[*stack,current]});return
        choices=by_name.get(current,[])
        if not choices:
            issues.append({'issue':'missing_named_subset','name':current,'path':[*stack,current]});return
        maximum=max(row['priority'] for row in choices)
        active=[row for row in choices if row['priority']==maximum]
        selected[current]=[row['id'] for row in active]
        shadowed[current]=[row['id'] for row in choices if row['priority']!=maximum]
        for row in active:
            items.update(row['equipment'])
            for child in row['subsets']:visit(child,[*stack,current])
    visit(name,[])
    if required_item is not None:
        try:item=f'{int(required_item,16):016X}'
        except (ValueError,TypeError) as exc:raise FormatError('Required item must be a hexadecimal resource ID') from exc
        if not 0<int(item,16)<=0xffffffffffffffff:raise FormatError('Required item ID is out of range')
        if item not in items:issues.append({'issue':'required_item_absent_from_declared_union','id':item})
    return {'set_name':name,'expected_union_ids':sorted(items),'selected_sets':selected,'shadowed_sets':shadowed,
            'issues':issues,'review_required':bool(issues),'engine_lookup_executed':False,
            'coverage':'Case-sensitive names, maximum-priority ties and subset recursion from reviewed native code. Serialized candidate union only; engine allowed-resource context, DLC eligibility, native lookup, drop probability and gameplay are not evaluated.'}


def check(name,mods,game,index,required_item=None):
    game=Path(game).resolve();binding=native.profile_for(game)
    if binding['dll_sha256']!=REVIEWED_DLL:raise FormatError('Equipment-set rules need review for this native build')
    mods=[Path(p).resolve() for p in mods]
    if not 1<=len(mods)<=32:raise FormatError('Select 1..32 explicit Mod inputs')
    db=catalog.open_existing(index)
    try:
        files=db.execute('SELECT DISTINCT f.path,f.sha256 FROM files f JOIN resources r ON r.file=f.path WHERE r.type_id=? AND f.origin=? ORDER BY f.path',(CID,'game')).fetchall()
    finally:db.close()
    by_id={};inputs={}
    def add(resource,file,origin,override=False):
        row=declaration(resource,file,origin);old=by_id.get(row['id'])
        if old and old['body_sha256']!=row['body_sha256']:
            if not override or old['origin']!='game':
                raise FormatError('Ambiguous equipment-set ID; use explicit independently merged input: '+row['id'])
        by_id[row['id']]=row
    for file in files:
        path=Path(file['path']).resolve()
        if not path.is_relative_to(game/'Content') or file_hash(path)!=file['sha256']:
            raise FormatError('Equipment-set original index is stale or outside this game')
        inputs[str(path)]=file['sha256']
        for resource in read_rpk(path)[1]:
            if resource.type_id==CID:add(resource,path,'game')
    for mod in mods:
        acp=mod_file(mod);inputs[str(acp)]=file_hash(acp)
        project=projects.inspect_project(mod)
        registered={row['name'].casefold() for row in project['packs'] if row['registered']}
        for row in project['definitions']:
            if row['kind']=='rpk' and row['pack'].casefold() not in registered:continue
            resource=row['resource']
            if resource.type_id!=CID:continue
            if row['kind']=='acp' and row['operation']!=0x40:raise FormatError('Unsupported equipment-set ACP operation')
            captured=sha(resource.node.data)
            if row['file'] in inputs and inputs[row['file']]!=captured:raise FormatError('Equipment-set Mod changed during capture')
            inputs[row['file']]=captured
            add(resource,row['file'],str(mod),row['kind']=='acp')
    result=resolve(list(by_id.values()),name,required_item)
    if any(file_hash(path)!=digest for path,digest in inputs.items()):raise FormatError('Equipment-set inputs changed during review')
    result.update(format='aow4mod-equipment-set-review/1',inputs=inputs,declared_set_count=len(by_id),
                  reviewed_dll_sha256=binding['dll_sha256'],mods=[str(p) for p in mods])
    return result
