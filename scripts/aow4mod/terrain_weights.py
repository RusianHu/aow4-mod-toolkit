"""Exact rectangular terrain-texture weights with independent native CPU evidence."""
from pathlib import Path
import uuid

from . import map_terrain,maps,map_metadata,native,native_maps,native_map_terrain,native_map_release
from .core import FormatError,sha,mod_file
from .filesystem import file_hash,inventory,copy_directory,save_record

FORMAT='aow4mod-terrain-weight-plan/1'


def fingerprint():
    root=Path(__file__).parent
    return sha(b''.join((root/n).read_bytes() for n in ('terrain_weights.py','map_terrain.py','native_map_terrain.py','maps.py','map_metadata.py','editing.py','hex_positions.py','native_map_release.py')))


def samples(rect):
    x,y,w,h=(rect[k] for k in ('x','y','width','height'))
    return [{'x':a,'y':b} for a,b in dict.fromkeys([(x,y),(x+w-1,y),(x,y+h-1),(x+w-1,y+h-1),(x+w//2,y+h//2)])]


def proof(report,path,points,level):
    if report.get('level')!=level:raise FormatError('Native terrain witness names another grid level')
    if not native_map_terrain.validate(report,path,report['output']):raise FormatError('Native terrain pixel object differs from the candidate')
    if [{k:r[k] for k in ('x','y')} for r in report['samples']]!=points:raise FormatError('Native terrain sample coverage differs from the plan')


def make_plan(mod,path,level,rect,weights,game,cache_dir=Path('.aow4mod/native'),timeout=120):
    acp=mod_file(Path(mod));path=Path(path).resolve();game=Path(game).resolve()
    if not path.is_relative_to(acp.parent):raise FormatError('Stage the map inside the Mod before painting texture weights')
    relative=map_metadata._relative(path.relative_to(acp.parent).as_posix());before=inventory(acp.parent);data,_=maps.read(path)
    desired,resolution,changed=map_terrain.derive(data,level,rect,weights);points=samples(rect)
    lc,pc=native_map_release.context(acp.parent);context={'libraries':[r['file'] for r in lc],'packs':[r['file'] for r in pc]}
    cache=native.check_cache_directory(cache_dir,game,[acp.parent]);run=cache/'terrain-weights'/uuid.uuid4().hex;run.mkdir(parents=True)
    baseline=native_maps.run(path,game,run/'baseline',cache,timeout,**context);map_metadata._proof(baseline,path,run/'baseline')
    (run/'input').mkdir();candidate=run/'input'/path.name;candidate.write_bytes(desired)
    witness=native_maps.run(candidate,game,run/'candidate',cache,timeout,**context);map_metadata._proof(witness,candidate,run/'candidate')
    pixels=native_map_terrain.run(candidate,game,run/'pixels',level,points,cache,timeout,**context);proof(pixels,candidate,points,level)
    if inventory(acp.parent)!=before:raise FormatError('Mod changed during terrain weight planning')
    return {'format':FORMAT,'generator_sha256':fingerprint(),'source':str(acp),'source_files':before,'map':str(path),'relative_path':relative,'map_sha256':sha(data),
            'game':str(game),'level':level,'rectangle':rect,'weights':weights,'resolution':resolution,'changed_pixels':changed,
            'candidate':str(candidate),'candidate_sha256':sha(desired),'baseline':baseline,'witness':witness,'pixel_witness':pixels,
            'context_libraries':lc,'context_packs':pc,'binding':baseline['binding'],
            'coverage':'Eight serialized terrain-texture weights in an explicit pixel rectangle only; exact other bytes, independent map saves and native CPU pixel object/buffer evidence. No height sculpting, brush sessions, material-name mapping, navigation or rendering certification.'}


def validate(plan):
    if not isinstance(plan,dict) or plan.get('format')!=FORMAT or plan.get('generator_sha256')!=fingerprint():raise FormatError('Unsupported or stale terrain weight plan')
    source=Path(plan['source']).resolve();path=Path(plan['map']).resolve();relative=map_metadata._relative(plan['relative_path']);game=Path(plan['game'])
    if inventory(source.parent)!=plan['source_files'] or path!=(source.parent/relative).resolve() or not path.is_relative_to(source.parent):raise FormatError('Terrain weight source/path differs')
    data,_=maps.read(path)
    if sha(data)!=plan['map_sha256']:raise FormatError('Terrain weight source hash differs')
    desired,resolution,changed=map_terrain.derive(data,plan['level'],plan['rectangle'],plan['weights']);candidate=Path(plan['candidate'])
    if resolution!=plan['resolution'] or changed!=plan['changed_pixels'] or sha(desired)!=plan['candidate_sha256'] or candidate.read_bytes()!=desired:
        raise FormatError('Terrain candidate differs from the exact requested texture edit')
    binding=native.public_binding(native.profile_for(game));lc,pc=native_map_release.context(source.parent)
    for r in (plan,plan['baseline'],plan['witness'],plan['pixel_witness']):
        if r['binding']!=binding or r.get('context_libraries')!=lc or r.get('context_packs')!=pc:raise FormatError('Terrain weight backend/context differs')
    map_metadata._proof(plan['baseline'],path,plan['baseline']['output']);map_metadata._proof(plan['witness'],candidate,plan['witness']['output'])
    proof(plan['pixel_witness'],candidate,samples(plan['rectangle']),plan['level']);return desired


def apply_plan(plan,output):
    desired=validate(plan);source=Path(plan['source']);candidate=Path(plan['candidate'])
    with copy_directory(source.parent,output,[plan['game'],candidate.parent]) as (stage,files):
        if files!=plan['source_files']:raise FormatError('Terrain source changed before staging')
        target=stage/plan['relative_path'];target.write_bytes(desired)
        if file_hash(target)!=plan['candidate_sha256'] or file_hash(candidate)!=plan['candidate_sha256']:raise FormatError('Terrain weight candidate changed during staging')
        receipt={'format':'aow4mod-terrain-weight-receipt/1','target':plan['relative_path'],'level':plan['level'],'rectangle':plan['rectangle'],
                 'weights':plan['weights'],'resolution':plan['resolution'],'changed_pixels':plan['changed_pixels'],'sha256':plan['candidate_sha256'],
                 'draft':True,'playability_verified':False,'diagnostic_count':sum(len(plan[k]['diagnostics']) for k in ('baseline','witness','pixel_witness'))}
        marker=stage/'SourceMaps'/Path(plan['relative_path']).name/'.aow4mod-map.json';marker.parent.mkdir(parents=True,exist_ok=True)
        save_record(marker,receipt);save_record(stage/'.aow4mod-terrain-weights.json',receipt)
    return receipt
