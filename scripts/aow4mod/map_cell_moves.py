"""Move reviewed flee points together with their explicit cell ownership."""
from pathlib import Path
import uuid

from . import maps,map_entities,map_world_entities,map_metadata,native,native_maps,native_world_entities,native_map_release
from .core import FormatError,sha,mod_file
from .filesystem import file_hash,inventory,copy_directory,save_record

FORMAT='aow4mod-map-cell-move-plan/1'


def fingerprint():
    root=Path(__file__).parent
    return sha(b''.join((root/n).read_bytes() for n in ('map_cell_moves.py','map_world_entities.py','map_entities.py','maps.py',
        'hex_positions.py','editing.py','map_metadata.py','native_world_entities.py','native_map_release.py')))


def derive(data,eid,point):
    if type(eid) is not int or not 0<=eid<=0xffffffff or not isinstance(point,dict) or set(point)!={'x','y','level'}:
        raise FormatError('Select an explicit uint32 ID and exact x/y/level coordinates')
    items=map_world_entities.records(data);map_world_entities.require_consistent(items)
    selected=[r for r in items if r['id']==eid]
    if len(selected)!=1 or selected[0]['cid']!=0x47159e or selected[0]['location']['origin']!='cell' or selected[0]['location']['slot']!=4:
        raise FormatError('Cell movement only supports a reviewed main-slot TTacticalFleePointE')
    desired,item,position=map_world_entities.move_cell(data,eid,point)
    where=item['location'];values=item['fields'][map_entities.HEX_SCOPE].mapping()
    direction=values[1].uint(1) if 1 in values else 2
    if direction not in range(6):raise FormatError('Flee point direction exceeds the reviewed enum')
    return desired,{'id':f'{eid:08X}','cid':f'{item["cid"]:08X}','location':where,'sha256':sha(item['node'].raw()),'direction':direction},position


def proof(report,path,eid=None):
    if report.get('format')!='aow4mod-native-world-entities/1' or report.get('sha256')!=file_hash(path):raise FormatError('World movement witness source/format differs')
    source=map_world_entities.records(Path(path).read_bytes());map_world_entities.require_consistent(source)
    selected=source if eid is None else [r for r in source if r['id']==eid]
    if report.get('identity_summary')!=map_world_entities.identity_summary(source):raise FormatError('World movement complete identity summary differs')
    observed=native_world_entities.validate_observations(selected,report['entities'],report['output'])
    if report.get('entity_count')!=len(source) or observed!=report['entities'] or any(not r['source_data_equal'] or
        r.get('source_cell_data_equal') is False or r['direction_status']!=0 for r in observed):
        raise FormatError('World movement entity/cell evidence differs')
    for expected,row in zip(selected,observed):
        values=expected['fields'][map_entities.HEX_SCOPE].mapping();direction=values[1].uint(1) if 1 in values else 2
        if row['direction']!=direction:raise FormatError('World movement direction differs')


def make_plan(mod,path,eid,point,game,cache_dir=Path('.aow4mod/native'),timeout=120):
    acp=mod_file(Path(mod));path=Path(path).resolve();game=Path(game).resolve()
    if not path.is_relative_to(acp.parent):raise FormatError('Stage the map inside the Mod before moving cell entities')
    relative=map_metadata._relative(path.relative_to(acp.parent).as_posix());before=inventory(acp.parent);data,_=maps.read(path)
    desired,old,position=derive(data,eid,point);lc,pc=native_map_release.context(acp.parent)
    context={'libraries':[r['file'] for r in lc],'packs':[r['file'] for r in pc]}
    cache=native.check_cache_directory(cache_dir,game,[acp.parent]);run=cache/'map-cell-moves'/uuid.uuid4().hex;run.mkdir(parents=True)
    baseline=native_maps.run(path,game,run/'baseline',cache,timeout,**context);map_metadata._proof(baseline,path,run/'baseline')
    (run/'input').mkdir();candidate=run/'input'/path.name;candidate.write_bytes(desired)
    witness=native_maps.run(candidate,game,run/'candidate',cache,timeout,**context);map_metadata._proof(witness,candidate,run/'candidate')
    entities=native_world_entities.run(candidate,game,run/'entities',[eid],cache,timeout,**context);proof(entities,candidate,eid)
    if inventory(acp.parent)!=before:raise FormatError('Mod changed during cell-movement planning')
    return {'format':FORMAT,'generator_sha256':fingerprint(),'source':str(acp),'source_files':before,'map':str(path),'relative_path':relative,
            'map_sha256':sha(data),'game':str(game),'entity_id':eid,'before':old,'position':position,'candidate':str(candidate),
            'candidate_sha256':sha(desired),'context_libraries':lc,'context_packs':pc,'baseline':baseline,'witness':witness,
            'entity_witness':entities,'binding':baseline['binding'],
            'coverage':'Existing flee-point main-cell ownership and packed position only, exact unrelated bytes and independent native map/cell/entity observations. No registered-world routing, legal fleeing, terrain/rendering or gameplay certification.'}


def validate(plan):
    if not isinstance(plan,dict) or plan.get('format')!=FORMAT or plan.get('generator_sha256')!=fingerprint():raise FormatError('Unsupported or stale cell-movement plan')
    source=Path(plan['source']).resolve();path=Path(plan['map']).resolve();relative=map_metadata._relative(plan['relative_path']);game=Path(plan['game'])
    if inventory(source.parent)!=plan['source_files'] or path!=(source.parent/relative).resolve() or not path.is_relative_to(source.parent):
        raise FormatError('Cell-movement source/path changed')
    data,_=maps.read(path)
    if sha(data)!=plan['map_sha256']:raise FormatError('Cell-movement source hash differs')
    desired,old,point=derive(data,plan['entity_id'],plan['position']);candidate=Path(plan['candidate'])
    if old!=plan['before'] or point!=plan['position'] or sha(desired)!=plan['candidate_sha256'] or candidate.read_bytes()!=desired:
        raise FormatError('Cell movement differs from the exact requested edit')
    binding=native.public_binding(native.profile_for(game));lc,pc=native_map_release.context(source.parent)
    for r in (plan,plan['baseline'],plan['witness'],plan['entity_witness']):
        if r['binding']!=binding or r.get('context_libraries')!=lc or r.get('context_packs')!=pc:raise FormatError('Cell movement backend/context differs')
    map_metadata._proof(plan['baseline'],path,plan['baseline']['output']);map_metadata._proof(plan['witness'],candidate,plan['witness']['output'])
    proof(plan['entity_witness'],candidate,plan['entity_id']);return desired


def apply_plan(plan,output):
    desired=validate(plan);source=Path(plan['source']);candidate=Path(plan['candidate'])
    with copy_directory(source.parent,output,[plan['game'],candidate.parent]) as (stage,files):
        if files!=plan['source_files']:raise FormatError('Cell movement source changed before staging')
        target=stage/plan['relative_path'];target.write_bytes(desired)
        if file_hash(target)!=plan['candidate_sha256'] or file_hash(candidate)!=plan['candidate_sha256']:raise FormatError('Cell movement candidate changed during staging')
        receipt={'format':'aow4mod-map-cell-move-receipt/1','target':plan['relative_path'],'entity':plan['before'],'position':plan['position'],
                 'sha256':plan['candidate_sha256'],'draft':True,'playability_verified':False,
                 'diagnostic_count':sum(len(plan[k]['diagnostics']) for k in ('baseline','witness','entity_witness'))}
        marker=stage/'SourceMaps'/Path(plan['relative_path']).name/'.aow4mod-map.json';marker.parent.mkdir(parents=True,exist_ok=True)
        save_record(marker,receipt);save_record(stage/'.aow4mod-map-cell-move.json',receipt)
    return receipt
