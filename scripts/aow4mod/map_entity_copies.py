"""Template-based deployment-zone creation with complete loaded-list witnesses."""
from pathlib import Path
import struct
import uuid

from . import editing,hex_positions,map_entities,map_world_entities,map_metadata,maps,native,native_maps,native_map_entities,native_map_release
from .core import FormatError,sha,mod_file
from .filesystem import copy_directory,file_hash,inventory,save_record

FORMAT='aow4mod-map-entity-copy-plan/1'
SUPPORTED=0x476e5d
ID_RANGE=(0x40000000,0x5fffffff)  # Official TGCEManager constructor 447D10.
EMPTY_SCOPES={0x4000072,0x4000403,0x4719be,0x471a54,SUPPORTED}
# The two independent current-map templates share these property descriptors.
# Entity scope field 5 is ResourceID.Value in official serializer 45F690,
# not another entity identity. Preserve the resource and descriptor data.
DESCRIPTORS=bytes.fromhex('040a00140115091611036515470001140002c7100104011400036515470001140004')


def fingerprint():
    root=Path(__file__).parent
    return sha(b''.join((root/name).read_bytes() for name in ('map_entity_copies.py','map_entities.py','map_world_entities.py','map_metadata.py',
        'maps.py','hex_positions.py','editing.py','native_map_entities.py','native_maps.py','native_map_release.py')))


def derive(data,eid,new_id,requested,direction=None):
    if type(eid) is not int or not 0<=eid<=0xffffffff or (new_id is not None and (type(new_id) is not int or not 0<=new_id<=0xffffffff)):
        raise FormatError('Select a uint32 source ID and optional exact uint32 new ID')
    parsed=maps.decode(data);world=parsed['map'][0x14];_,world_fields=world.typed()
    base=world_fields.get(0x401107c)
    if base is None or 0x15 not in base.mapping():raise FormatError('Stored world entity-ID range position is required')
    counter=base.mapping()[0x15];next_id=counter.uint(4)
    if new_id is not None and new_id!=next_id:raise FormatError('New ID must equal the stored next entity-ID range position')
    new_id=next_id
    if not ID_RANGE[0]<=new_id<=ID_RANGE[1]:
        raise FormatError('World ID position is outside the reviewed deployment namespace')
    following=ID_RANGE[0] if new_id==ID_RANGE[1] else new_id+1
    raw_id=struct.pack('<I',new_id)
    if data.find(raw_id)!=counter.start or data.find(raw_id,counter.start+1)!=-1:
        raise FormatError('World next ID also occurs outside its counter; possible hidden cell/other identity collision requires review')
    if not isinstance(requested,dict) or set(requested)!={'x','y','level'}:
        raise FormatError('Select exact x/y/level coordinates')
    if direction is not None and (type(direction) is not int or direction not in range(6)):
        raise FormatError('Deployment direction must be 0..5')
    entities=map_entities.records(data)
    map_world_entities.require_consistent(map_world_entities.records(data))
    if not 1<=len(entities)<native_map_entities.MAX_SELECTED:
        raise FormatError('Entity copy requires 1..255 source entries for complete native coverage')
    selected=[r for r in entities if r['id']==eid]
    if len(selected)!=1:raise FormatError('Source tactical entity is missing or ambiguous')
    item=selected[0];fields=item['fields']
    if (item['cid']!=SUPPORTED or eid&0xc0000000!=0x40000000 or
            set(fields)!={map_entities.ENTITY_SCOPE,map_entities.HEX_SCOPE,*EMPTY_SCOPES}):
        raise FormatError('Entity copy only supports the reviewed basic deployment-zone template')
    if any(fields[cid].mapping() for cid in EMPTY_SCOPES):
        raise FormatError('Nonempty deployment settings require separate template review')
    identity=fields[map_entities.ENTITY_SCOPE].mapping();position=fields[map_entities.HEX_SCOPE].mapping()
    if (set(identity)!={1,4,5} or identity[4].raw()!=DESCRIPTORS or not identity[5].uint(8) or
            set(position) not in ({8},{1,8})):
        raise FormatError('Deployment template resource/property/hex layout requires review')
    old_point=hex_positions.decode(position[8].uint(4));layout=hex_positions.grids(data);hex_positions.checked(old_point,layout)
    point={**requested,'level':old_point['level'] if requested['level'] is None else requested['level']}
    grid=hex_positions.checked(point,layout);old_direction=position[1].uint(1) if 1 in position else 2
    if old_direction not in range(6):raise FormatError('Source deployment direction exceeds the reviewed enum')
    direction=old_direction if direction is None else direction
    clone=editing.rewrite_typed(item['node'],{
        map_entities.ENTITY_SCOPE:editing.rewrite_map(fields[map_entities.ENTITY_SCOPE],{1:struct.pack('<I',new_id)}),
        map_entities.HEX_SCOPE:editing.rewrite_map(fields[map_entities.HEX_SCOPE],{
            1:None if direction==2 else bytes([direction]),8:struct.pack('<I',hex_positions.encode(point))})})
    scope=world_fields[map_entities.WORLD_CID]
    manager=scope.mapping()[2];objects=manager.mapping()[0xa]
    replacement=editing.replace_array_items(objects,[r['node'].raw() for r in entities]+[clone])
    replacement=editing.rewrite_map(manager,{0xa:replacement});replacement=editing.rewrite_map(scope,{2:replacement})
    replacement=editing.rewrite_typed(world,{map_entities.WORLD_CID:replacement,
        0x401107c:editing.rewrite_map(base,{0x15:struct.pack('<I',following)})})
    desired=parsed['envelope']+parsed['header_bytes']+editing.rewrite_map(parsed['body'],{0x14:replacement})
    result=map_entities.records(desired)
    if [r['node'].raw() for r in result[:-1]]!=[r['node'].raw() for r in entities] or result[-1]['id']!=new_id:
        raise FormatError('Entity append changed existing entries or new identity')
    overlaps=[]
    for other in entities:
        p=other['fields'].get(map_entities.HEX_SCOPE)
        if p is not None and 8 in p.mapping() and hex_positions.decode(p.mapping()[8].uint(4))==point:
            overlaps.append(f'{other["id"]:08X}')
    template={'id':f'{eid:08X}','cid':f'{SUPPORTED:08X}','ordinal':item['ordinal'],'sha256':sha(item['node'].raw()),
              'resource_id':f'{identity[5].uint(8):016X}','position':old_point,'direction':old_direction}
    created={'id':f'{new_id:08X}','cid':f'{SUPPORTED:08X}','ordinal':len(entities),'position':point,'direction':direction,
             'resource_id':template['resource_id'],'id_range':1,'range_position_before':new_id,'range_position_after':following}
    return desired,template,created,{k:v for k,v in grid.items() if k!='cells'},overlaps


def _proof(report,path):
    if report.get('format')!='aow4mod-native-map-entities/2' or report.get('sha256')!=file_hash(path):
        raise FormatError('Map entity-copy report source/format differs')
    source=map_entities.records(Path(path).read_bytes())
    rows=native_map_entities.validate_observations(source,report['entities'],report['output'],with_position=True)
    if (report.get('entity_count')!=len(source) or rows!=report['entities'] or
            any(not r['source_data_equal'] or r['direction_status']!=0 for r in rows)):
        raise FormatError('Loaded entity-copy coverage/data/direction differs')
    for expected,observed in zip(source,rows):
        fields=expected['fields'].get(map_entities.HEX_SCOPE)
        if fields is None:raise FormatError('Loaded entity lacks the reviewed hex scope')
        fields=fields.mapping();direction=fields[1].uint(1) if 1 in fields else 2
        if observed['direction']!=direction:raise FormatError('Loaded entity direction differs from stored/default value')


def make_plan(mod,path,eid,new_id,requested,game,direction=None,cache_dir=Path('.aow4mod/native'),timeout=120):
    acp=mod_file(Path(mod));path=Path(path).resolve();game=Path(game).resolve()
    if not path.is_relative_to(acp.parent):raise FormatError('Stage the map inside the Mod before copying an entity')
    relative=map_metadata._relative(path.relative_to(acp.parent).as_posix());before=inventory(acp.parent);data,_=maps.read(path)
    desired,template,created,grid,overlaps=derive(data,eid,new_id,requested,direction)
    libraries,packs=native_map_release.context(acp.parent);context={'libraries':[r['file'] for r in libraries],'packs':[r['file'] for r in packs]}
    cache=native.check_cache_directory(cache_dir,game,[acp.parent]);run=cache/'map-entity-copies'/uuid.uuid4().hex;run.mkdir(parents=True)
    baseline=native_maps.run(path,game,run/'baseline',cache,timeout,**context);map_metadata._proof(baseline,path,run/'baseline')
    (run/'input').mkdir();candidate=run/'input'/path.name;candidate.write_bytes(desired)
    witness=native_maps.run(candidate,game,run/'candidate',cache,timeout,**context);map_metadata._proof(witness,candidate,run/'candidate')
    entity=native_map_entities.run(candidate,game,run/'entities',None,cache,timeout,**context);_proof(entity,candidate)
    if inventory(acp.parent)!=before:raise FormatError('Map Mod changed during entity-copy planning')
    return {'format':FORMAT,'generator_sha256':fingerprint(),'source':str(acp),'source_files':before,'map':str(path),'map_sha256':sha(data),
            'relative_path':relative,'game':str(game),'entity_id':eid,'new_id':int(created['id'],16),'position':created['position'],'direction':created['direction'],
            'template':template,'created':created,'grid':grid,'overlap_ids':overlaps,'context_libraries':libraries,'context_packs':packs,
            'candidate':str(candidate),'candidate_sha256':sha(desired),'baseline':baseline,'witness':witness,'entity_witness':entity,'binding':baseline['binding'],
            'coverage':'One basic deployment-zone template appended using the stored next ID, advancing only ID-range position 1 and refusing other raw occurrences. Exact unrelated bytes, independent saves and complete loaded FZones/position/direction coverage; no registered-world allocator, terrain, legal spawning, rendering or gameplay certification.'}


def validate(plan):
    if not isinstance(plan,dict) or plan.get('format')!=FORMAT or plan.get('generator_sha256')!=fingerprint():
        raise FormatError('Unsupported or stale map entity-copy plan')
    source=Path(plan['source']).resolve();path=Path(plan['map']).resolve();relative=map_metadata._relative(plan['relative_path']);game=Path(plan['game'])
    if inventory(source.parent)!=plan['source_files'] or path!=(source.parent/relative).resolve() or not path.is_relative_to(source.parent):
        raise FormatError('Map entity-copy source inventory/path differs')
    data,_=maps.read(path)
    if sha(data)!=plan['map_sha256']:raise FormatError('Map entity-copy source changed')
    desired,template,created,grid,overlaps=derive(data,plan['entity_id'],plan['new_id'],plan['position'],plan['direction']);candidate=Path(plan['candidate'])
    if (template!=plan['template'] or created!=plan['created'] or grid!=plan['grid'] or overlaps!=plan['overlap_ids'] or
            sha(desired)!=plan['candidate_sha256'] or candidate.read_bytes()!=desired):
        raise FormatError('Map entity-copy candidate differs from the exact requested append')
    binding=native.public_binding(native.profile_for(game))
    if any(r['binding']!=binding for r in (plan,plan['baseline'],plan['witness'],plan['entity_witness'])):
        raise FormatError('Map entity-copy backend changed')
    libraries,packs=native_map_release.context(source.parent)
    if any(r.get('context_libraries')!=libraries or r.get('context_packs')!=packs for r in (plan,plan['baseline'],plan['witness'],plan['entity_witness'])):
        raise FormatError('Map entity-copy lookup context differs')
    map_metadata._proof(plan['baseline'],path,plan['baseline']['output']);map_metadata._proof(plan['witness'],candidate,plan['witness']['output'])
    _proof(plan['entity_witness'],candidate)
    return desired


def apply_plan(plan,output):
    desired=validate(plan);source=Path(plan['source']);candidate=Path(plan['candidate'])
    with copy_directory(source.parent,output,[plan['game'],candidate.parent]) as (stage,files):
        if files!=plan['source_files']:raise FormatError('Map entity-copy source changed before staging')
        target=stage/plan['relative_path'];target.write_bytes(desired)
        if file_hash(target)!=plan['candidate_sha256'] or file_hash(candidate)!=plan['candidate_sha256']:
            raise FormatError('Map entity-copy candidate changed during staging')
        receipt={'format':'aow4mod-map-entity-copy-receipt/1','target':plan['relative_path'],'template':plan['template'],'created':plan['created'],
                 'grid':plan['grid'],'overlap_ids':plan['overlap_ids'],'sha256':plan['candidate_sha256'],'draft':True,'playability_verified':False,
                 'diagnostic_count':sum(len(plan[k]['diagnostics']) for k in ('baseline','witness','entity_witness'))}
        marker=stage/'SourceMaps'/Path(plan['relative_path']).name/'.aow4mod-map.json';marker.parent.mkdir(parents=True,exist_ok=True)
        save_record(marker,receipt);save_record(stage/'.aow4mod-map-entity-copy.json',receipt)
    return receipt
