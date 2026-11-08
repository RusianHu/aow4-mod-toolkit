"""Staged existing deployment-zone moves with grid and native position witnesses."""
from pathlib import Path
import struct
import uuid

from . import maps,map_entities,hex_positions,map_metadata,editing,native,native_maps,native_map_entities,native_map_release
from .core import FormatError,sha,mod_file
from .filesystem import file_hash,inventory,copy_directory,save_record

FORMAT='aow4mod-map-position-plan/1'
SUPPORTED={0x476e5d}


def fingerprint():
    root=Path(__file__).parent
    return sha(b''.join((root/name).read_bytes() for name in ('map_positions.py','hex_positions.py','map_entities.py','map_metadata.py','maps.py','editing.py')))


def derive(data,eid,requested):
    if type(eid) is not int or not 0<=eid<=0xffffffff or not isinstance(requested,dict) or set(requested)!={'x','y','level'}:
        raise FormatError('Select an explicit uint32 entity ID and exact x/y/level coordinates')
    selected=[r for r in map_entities.records(data) if r['id']==eid]
    if len(selected)!=1:raise FormatError('Selected tactical entity is missing or ambiguous')
    item=selected[0]
    if item['cid'] not in SUPPORTED or map_entities.HEX_SCOPE not in item['fields']:raise FormatError('Position editing only supports the reviewed deployment-zone class')
    fields=item['fields'][map_entities.HEX_SCOPE].mapping()
    if 8 not in fields:raise FormatError('A stored source hex position is required')
    old=hex_positions.decode(fields[8].uint(4));layout=hex_positions.grids(data);hex_positions.checked(old,layout)
    point={**requested,'level':old['level'] if requested['level'] is None else requested['level']};grid=hex_positions.checked(point,layout)
    if point==old:raise FormatError('Map position edit makes no change')
    direction=fields[1].uint(1) if 1 in fields else 2
    if direction not in range(6):raise FormatError('Source deployment direction exceeds the reviewed enum')
    replacement=editing.rewrite_typed(item['node'],{map_entities.HEX_SCOPE:editing.rewrite_map(item['fields'][map_entities.HEX_SCOPE],{8:struct.pack('<I',hex_positions.encode(point))})})
    desired=map_entities.replace(data,item['ordinal'],replacement);overlaps=[]
    for other in map_entities.records(data):
        if other['id']==eid or map_entities.HEX_SCOPE not in other['fields']:continue
        values=other['fields'][map_entities.HEX_SCOPE].mapping()
        if 8 in values and hex_positions.decode(values[8].uint(4))==point:overlaps.append(f'{other["id"]:08X}')
    return desired,{'id':f'{eid:08X}','cid':f'{item["cid"]:08X}','ordinal':item['ordinal'],'position':old,'direction':direction},point,{k:v for k,v in grid.items() if k!='cells'},overlaps


def _proof(report,path,eid,point,direction):
    if report.get('format')!='aow4mod-native-map-entities/2' or report.get('sha256')!=file_hash(path):raise FormatError('Map position report source/format differs')
    source=[r for r in map_entities.records(Path(path).read_bytes()) if r['id']==eid]
    rows=native_map_entities.validate_observations(source,report['entities'],report['output'],with_position=True)
    if (rows!=report['entities'] or len(rows)!=1 or not rows[0]['source_data_equal'] or rows[0]['position']!=point or
            rows[0]['direction_status']!=0 or rows[0]['direction']!=direction):
        raise FormatError('Loaded deployment position/direction differs from the exact requested edit')


def make_plan(mod,path,eid,requested,game,cache_dir=Path('.aow4mod/native'),timeout=120):
    acp=mod_file(Path(mod));path=Path(path).resolve();game=Path(game).resolve()
    if not path.is_relative_to(acp.parent):raise FormatError('Stage the reviewed map inside the Mod before editing position')
    relative=map_metadata._relative(path.relative_to(acp.parent).as_posix());before=inventory(acp.parent);data,_=maps.read(path)
    desired,old,point,grid,overlaps=derive(data,eid,requested)
    libraries,packs=native_map_release.context(acp.parent);context={'libraries':[r['file'] for r in libraries],'packs':[r['file'] for r in packs]}
    cache=native.check_cache_directory(cache_dir,game,[acp.parent]);run=cache/'map-positions'/uuid.uuid4().hex;run.mkdir(parents=True)
    baseline=native_maps.run(path,game,run/'baseline',cache,timeout,**context);map_metadata._proof(baseline,path,run/'baseline')
    (run/'input').mkdir();candidate=run/'input'/path.name;candidate.write_bytes(desired)
    witness=native_maps.run(candidate,game,run/'candidate',cache,timeout,**context);map_metadata._proof(witness,candidate,run/'candidate')
    entity=native_map_entities.run(candidate,game,run/'entity',[eid],cache,timeout,**context);_proof(entity,candidate,eid,point,old['direction'])
    if inventory(acp.parent)!=before:raise FormatError('Map Mod changed during position planning')
    return {'format':FORMAT,'generator_sha256':fingerprint(),'source':str(acp),'source_files':before,'map':str(path),'map_sha256':sha(data),
            'relative_path':relative,'game':str(game),'entity_id':eid,'position':point,'before':old,'grid':grid,'overlap_ids':overlaps,
            'context_libraries':libraries,'context_packs':packs,
            'candidate':str(candidate),'candidate_sha256':sha(desired),'baseline':baseline,'witness':witness,'entity_witness':entity,'binding':baseline['binding'],
            'coverage':'Existing reviewed deployment-zone position within declared tactical grid bounds; exact other bytes, independent source/candidate saves and loaded native position/packer/direction. Terrain, legal spawning, shape, rendering and gameplay remain unverified.'}


def validate(plan):
    if not isinstance(plan,dict) or plan.get('format')!=FORMAT or plan.get('generator_sha256')!=fingerprint():raise FormatError('Unsupported or stale map position plan')
    source=Path(plan['source']).resolve();path=Path(plan['map']).resolve();relative=map_metadata._relative(plan['relative_path']);game=Path(plan['game'])
    if inventory(source.parent)!=plan['source_files'] or path!=(source.parent/relative).resolve() or not path.is_relative_to(source.parent):raise FormatError('Map position source inventory/path differs')
    data,_=maps.read(path)
    if sha(data)!=plan['map_sha256']:raise FormatError('Map position source changed')
    desired,old,point,grid,overlaps=derive(data,plan['entity_id'],plan['position']);candidate=Path(plan['candidate'])
    if (old!=plan['before'] or point!=plan['position'] or grid!=plan['grid'] or overlaps!=plan['overlap_ids'] or
            sha(desired)!=plan['candidate_sha256'] or candidate.read_bytes()!=desired):raise FormatError('Map position candidate differs from the exact requested edit')
    binding=native.public_binding(native.profile_for(game))
    if any(r['binding']!=binding for r in (plan,plan['baseline'],plan['witness'],plan['entity_witness'])):raise FormatError('Map position backend changed')
    libraries,packs=native_map_release.context(source.parent)
    if any(r.get('context_libraries')!=libraries or r.get('context_packs')!=packs for r in (plan,plan['baseline'],plan['witness'],plan['entity_witness'])):
        raise FormatError('Map position lookup context differs')
    map_metadata._proof(plan['baseline'],path,plan['baseline']['output']);map_metadata._proof(plan['witness'],candidate,plan['witness']['output'])
    _proof(plan['entity_witness'],candidate,plan['entity_id'],point,old['direction'])
    return desired


def apply_plan(plan,output):
    desired=validate(plan);source=Path(plan['source']);candidate=Path(plan['candidate'])
    with copy_directory(source.parent,output,[plan['game'],candidate.parent]) as (stage,files):
        if files!=plan['source_files']:raise FormatError('Map position source changed before staging')
        target=stage/plan['relative_path'];target.write_bytes(desired)
        if file_hash(target)!=plan['candidate_sha256'] or file_hash(candidate)!=plan['candidate_sha256']:raise FormatError('Map position candidate changed during staging')
        receipt={'format':'aow4mod-map-position-receipt/1','target':plan['relative_path'],'entity':plan['before'],'position':plan['position'],'grid':plan['grid'],
                 'overlap_ids':plan['overlap_ids'],'sha256':plan['candidate_sha256'],'draft':True,'playability_verified':False,
                 'diagnostic_count':sum(len(plan[k]['diagnostics']) for k in ('baseline','witness','entity_witness'))}
        marker=stage/'SourceMaps'/Path(plan['relative_path']).name/'.aow4mod-map.json';marker.parent.mkdir(parents=True,exist_ok=True)
        save_record(marker,receipt);save_record(stage/'.aow4mod-map-position.json',receipt)
    return receipt
