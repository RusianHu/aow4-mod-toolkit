"""Scoped deployment-zone Direction edits with independent loaded-map witnesses."""
from pathlib import Path
import uuid

from . import maps,map_entities,map_metadata,editing,native,native_maps,native_map_entities
from .core import FormatError,sha,mod_file
from .filesystem import file_hash,inventory,copy_directory,save_record

FORMAT='aow4mod-map-entity-plan/1'
# Live source/object/getter witnesses currently cover this deployment-zone class.
SUPPORTED={0x476e5d}


def fingerprint():
    root=Path(__file__).parent
    return sha(b''.join((root/name).read_bytes() for name in ('map_entity_edits.py','map_entities.py','map_metadata.py','maps.py','editing.py')))


def derive(data,eid,direction):
    if type(eid) is not int or not 0<=eid<=0xffffffff or type(direction) is not int or direction not in range(6):raise FormatError('Select an explicit uint32 entity ID and direction 0..5')
    selected=[r for r in map_entities.records(data) if r['id']==eid]
    if len(selected)!=1:raise FormatError('Selected tactical entity is missing or ambiguous')
    item=selected[0]
    if item['cid'] not in SUPPORTED or map_entities.HEX_SCOPE not in item['fields']:raise FormatError('Entity Direction editing only supports the reviewed deployment-zone classes')
    fields=item['fields'][map_entities.HEX_SCOPE].mapping()
    old=fields[1].uint(1) if 1 in fields else None
    if old is not None and old not in range(6):raise FormatError('Stored entity direction is outside the reviewed enum')
    # The default candidate is accepted only if independent loaded-map getters
    # confirm the requested 2 and native file saves retain these exact bytes.
    replacement=editing.rewrite_typed(item['node'],{map_entities.HEX_SCOPE:editing.rewrite_map(item['fields'][map_entities.HEX_SCOPE],{1:None if direction==2 else bytes([direction])})})
    result=map_entities.replace(data,item['ordinal'],replacement)
    if result==data:raise FormatError('Map entity edit makes no change')
    return result,{'id':f'{eid:08X}','cid':f'{item["cid"]:08X}','ordinal':item['ordinal'],'stored_direction':old}


def _entity_proof(report,path,eid,direction):
    if report.get('format') not in ('aow4mod-native-map-entities/1','aow4mod-native-map-entities/2') or report.get('sha256')!=file_hash(path):raise FormatError('Map entity report source differs')
    source=[r for r in map_entities.records(Path(path).read_bytes()) if r['id']==eid]
    rows=native_map_entities.validate_observations(source,report['entities'],report['output'],with_position=report['format'].endswith('/2'))
    if rows!=report['entities'] or len(rows)!=1 or not rows[0]['source_data_equal'] or rows[0]['direction_status']!=0 or rows[0]['direction']!=direction:
        raise FormatError('Live map entity does not retain the requested Direction and exact object data')


def make_plan(mod,path,eid,direction,game,cache_dir=Path('.aow4mod/native'),timeout=120):
    acp=mod_file(Path(mod));path=Path(path).resolve();game=Path(game).resolve()
    if not path.is_relative_to(acp.parent):raise FormatError('Stage the reviewed map inside the Mod before editing entities')
    relative=map_metadata._relative(path.relative_to(acp.parent).as_posix());before=inventory(acp.parent);data,_=maps.read(path)
    desired,old=derive(data,eid,direction)
    cache=native.check_cache_directory(cache_dir,game,[acp.parent]);run=cache/'map-entity-edits'/uuid.uuid4().hex;run.mkdir(parents=True)
    baseline=native_maps.run(path,game,run/'baseline',cache,timeout);map_metadata._proof(baseline,path,run/'baseline')
    (run/'input').mkdir();candidate=run/'input'/path.name;candidate.write_bytes(desired)
    witness=native_maps.run(candidate,game,run/'candidate',cache,timeout);map_metadata._proof(witness,candidate,run/'candidate')
    entity=native_map_entities.run(candidate,game,run/'entity',[eid],cache,timeout);_entity_proof(entity,candidate,eid,direction)
    if inventory(acp.parent)!=before:raise FormatError('Map Mod changed during entity planning')
    return {'format':FORMAT,'generator_sha256':fingerprint(),'source':str(acp),'source_files':before,'map':str(path),'map_sha256':sha(data),
            'relative_path':relative,'game':str(game),'entity_id':eid,'direction':direction,'before':old,'candidate':str(candidate),'candidate_sha256':sha(desired),
            'baseline':baseline,'witness':witness,'entity_witness':entity,'binding':baseline['binding'],
            'coverage':'Existing reviewed deployment-zone Direction only; exact unrelated map bytes, two-process baseline/candidate save checks and a live loaded-map getter. Position, shape, deployment logic and gameplay remain unverified.'}


def validate(plan):
    if not isinstance(plan,dict) or plan.get('format')!=FORMAT or plan.get('generator_sha256')!=fingerprint():raise FormatError('Unsupported or stale map entity plan')
    source=Path(plan['source']).resolve();path=Path(plan['map']).resolve();relative=map_metadata._relative(plan['relative_path']);game=Path(plan['game'])
    if inventory(source.parent)!=plan['source_files'] or path!=(source.parent/relative).resolve() or not path.is_relative_to(source.parent):raise FormatError('Map entity source inventory/path differs')
    data,_=maps.read(path)
    if sha(data)!=plan['map_sha256']:raise FormatError('Map entity source changed')
    desired,old=derive(data,plan['entity_id'],plan['direction']);candidate=Path(plan['candidate'])
    if old!=plan['before'] or sha(desired)!=plan['candidate_sha256'] or candidate.read_bytes()!=desired:raise FormatError('Map entity candidate differs from the exact requested edit')
    binding=native.public_binding(native.profile_for(game))
    if any(r['binding']!=binding for r in (plan,plan['baseline'],plan['witness'],plan['entity_witness'])):raise FormatError('Map entity backend changed')
    map_metadata._proof(plan['baseline'],path,plan['baseline']['output']);map_metadata._proof(plan['witness'],candidate,plan['witness']['output'])
    _entity_proof(plan['entity_witness'],candidate,plan['entity_id'],plan['direction'])
    return desired


def apply_plan(plan,output):
    desired=validate(plan);source=Path(plan['source']);candidate=Path(plan['candidate'])
    with copy_directory(source.parent,output,[plan['game'],candidate.parent]) as (stage,files):
        if files!=plan['source_files']:raise FormatError('Map entity source changed before staging')
        target=stage/plan['relative_path'];target.write_bytes(desired)
        if file_hash(target)!=plan['candidate_sha256'] or file_hash(candidate)!=plan['candidate_sha256']:raise FormatError('Map entity candidate changed during staging')
        receipt={'format':'aow4mod-map-entity-receipt/1','target':plan['relative_path'],'entity':plan['before'],'direction':plan['direction'],'sha256':plan['candidate_sha256'],
                 'draft':True,'playability_verified':False,'diagnostic_count':sum(len(plan[k]['diagnostics']) for k in ('baseline','witness','entity_witness'))}
        marker=stage/'SourceMaps'/Path(plan['relative_path']).name/'.aow4mod-map.json';marker.parent.mkdir(parents=True,exist_ok=True)
        save_record(marker,receipt);save_record(stage/'.aow4mod-map-entity.json',receipt)
    return receipt
