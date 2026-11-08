"""Reviewable FBX take to animation-library authoring plans."""
import json
from pathlib import Path
import uuid

from . import animation_sources,animation_baking,animation_data,native,native_meshes,native_assets,asset_yaml,content_libraries,native_validation
from .core import FormatError,sha,mod_file
from .filesystem import inventory,file_hash,copy_directory,save_record
from .native_overlays import locked_inputs

FORMAT='aow4mod-animation-import-plan/1'


def fingerprint():
    return sha(b''.join((Path(__file__).parent/name).read_bytes() for name in (
        'animation_imports.py','animation_baking.py','animation_data.py','animation_sources.py','native_animations.py','native_fbx.py','fbx.py')))


def assess(payload,proof):
    objects=payload['objects'];count=payload['assessment']['track_count']
    expected={'default','rotation','translation','compressed_rotation','scale','frames','assembled','readback','independent'}|{f'track-{i:03}' for i in range(count)}
    if set(objects)!=expected or not payload.get('guards_preserved') or not 1<=count<=512 or len(payload['artifacts'])!=2:raise FormatError('Animation object coverage/guards differ')
    raw={}
    for key,item in objects.items():
        path=Path(item['file'])
        if not 1<=path.stat().st_size<=16*1024*1024 or file_hash(path)!=item['sha256']:raise FormatError('Animation object witness changed')
        raw[key]=path.read_bytes()
    pools={key:raw[key] for key in ('rotation','translation','compressed_rotation','scale')}
    derived=animation_data.derive(raw['default'],[raw[f'track-{i:03}'] for i in range(count)],pools,payload['namespace'],payload['title'])
    if derived!=raw['assembled']:raise FormatError('Animation recipe differs from its complete source tracks/pools')
    comparison=animation_data.compare(raw['assembled'],raw['readback'])
    if comparison!=payload['object_comparison']:raise FormatError('Animation object comparison summary differs')
    animation_data.compare(raw['readback'],raw['independent'])
    observed=animation_data.analyze(raw['independent'],payload['source_summary'])
    if observed!=payload['assessment']:raise FormatError('Animation assessment differs')
    for item in payload['artifacts']:
        if file_hash(item['file'])!=item['sha256']:raise FormatError('Animation library artifact changed')
    if len(proof['files'])!=2 or proof['source_sha256']!=payload['artifacts'][0]['sha256']:raise FormatError('Independent animation library input differs')
    comparison=content_libraries.compare(payload['artifacts'][0]['file'],Path(proof['output'])/proof['files'][0]['name'])
    if comparison!=proof['comparison'] or comparison['all_compared_data_equal'] is not True:raise FormatError('Independent animation library readback differs')
    original=asset_yaml.load(Path(payload['artifacts'][1]['file']).read_bytes())
    for item in proof['files']:
        if file_hash(Path(proof['output'])/item['name'])!=item['sha256']:raise FormatError('Independent animation readback witness changed')
    loaded=asset_yaml.load((Path(proof['output'])/proof['files'][1]['name']).read_bytes())
    if asset_yaml.differences(original,loaded):raise FormatError('Independent animation YAML fields differ')
    identities=asset_yaml.members(loaded)
    if identities!=[{'cid':0x410005,'locator':f'[{payload["namespace"]}]ANIM\\0','name':payload['title']}]:raise FormatError('Animation library identity differs')
    animation_data.compare_yaml(raw['independent'],loaded)
    return {**observed,'independent_object_data_equal':True,'independent_file_data_equal':True,'draft':True}


def make_plan(mod,source,namespace,title,game,cache_dir=Path('.aow4mod/native'),timeout=60,bake_fps=None,bake_max_error=0.001):
    acp=mod_file(Path(mod));before=inventory(acp.parent);source=Path(source).resolve()
    name=native_meshes.namespace(namespace);game=Path(game).resolve()
    input_source={'file':str(source),'sha256':file_hash(source)}
    cache=native.check_cache_directory(cache_dir,game,[acp.parent,source.parent]);baking=None
    if bake_fps is not None:
        with locked_inputs([source]):baking=animation_baking.prepare(source,bake_fps,cache,bake_max_error)
        source=Path(baking['candidate'])
    summary=animation_sources.inspect(source)
    if not isinstance(title,str) or not title or len(title.encode('utf-8'))>128 or '\0' in title:raise FormatError('Animation title must be 1..128 UTF-8 bytes')
    profile=native.profile_for(game)
    native.require_operation(profile,'animation-import')
    if file_hash(game/'libfbxsdk.dll')!=profile['profile']['fbx']['sdk_sha256']:raise FormatError('FBX SDK is not reviewed')
    target=Path('Libraries')/(name+'.clb')
    if (acp.parent/target).exists() or (acp.parent/'SourceAnimations'/name).exists():raise FormatError('Animation target/source directory already exists')
    if any(content_libraries.identity(p)['name'].casefold()==name.casefold() for p in acp.parent.rglob('*.clb')):raise FormatError('Animation library namespace already exists')
    cache=native.check_cache_directory(cache_dir,game,[acp.parent,source.parent])
    params={'path':str(source),'sha256':summary['sha256'],'namespace':name,'title':title}
    with locked_inputs([source,game/'libfbxsdk.dll']):result=native.run_worker(game,'animation-import',params,cache,timeout)
    diagnostics=native_validation.diagnostics(result['run'])
    if diagnostics:raise FormatError('Native animation importer emitted diagnostics; refusing a potentially incomplete take: '+str(diagnostics[:2]))
    payload=result['payload'];work=cache/'animation-imports'/uuid.uuid4().hex;work.parent.mkdir(parents=True,exist_ok=True)
    proof=native_assets.export_library(payload['artifacts'][0]['file'],game,work,cache,timeout)
    if proof.get('diagnostics'):raise FormatError('Independent animation library emitted diagnostics; review before import')
    binding=native.public_binding(result['binding'])
    if proof['binding']!=binding:raise FormatError('Native animation import/readback backend changed')
    assessment=assess(payload,proof)
    if file_hash(input_source['file'])!=input_source['sha256']:raise FormatError('Original animation FBX changed during planning')
    if inventory(acp.parent)!=before or animation_sources.inspect(source)!=summary:raise FormatError('Animation source changed during planning')
    return {'format':FORMAT,'generator_sha256':fingerprint(),'source':str(acp),'source_files':before,'fbx':summary,
        'namespace':name,'title':title,'target':target.as_posix(),'game':str(game),'binding':binding,
        'native_payload':payload,'file_readback':proof,'assessment':assessment,
        'diagnostics':native_validation.diagnostics(result['run'])+proof.get('diagnostics',[]),'source_bake':baking,
        'input_source':input_source,'bake_fps':bake_fps,'bake_max_error':bake_max_error if baking else None,
        'draft':True,'model_binding_verified':False,'pose_sampling_verified':False,'rendering_verified':False}


def apply_plan(plan,output):
    if plan.get('format')!=FORMAT or plan.get('generator_sha256')!=fingerprint():raise FormatError('Unsupported/outdated animation import plan')
    acp=Path(plan['source']);source=Path(plan['fbx']['file']);game=Path(plan['game'])
    baking=plan.get('source_bake')
    original=plan.get('input_source')
    if not isinstance(original,dict) or file_hash(original['file'])!=original['sha256']:raise FormatError('Animation original source policy differs')
    if plan.get('bake_fps') is None:
        if baking is not None or Path(original['file']).resolve()!=source.resolve() or original['sha256']!=plan['fbx']['sha256']:raise FormatError('Animation baking policy was removed')
    elif not isinstance(baking,dict) or baking['original']!=original['file'] or baking['original_sha256']!=original['sha256'] or baking['details']['fps_requested']!=plan['bake_fps'] or baking['details']['max_error_requested']!=plan.get('bake_max_error'):
        raise FormatError('Animation baking policy differs')
    if baking:
        animation_baking.verify(baking)
        if Path(baking['candidate']).resolve()!=source.resolve():raise FormatError('Animation baked source binding differs')
    if inventory(acp.parent)!=plan['source_files'] or animation_sources.inspect(source)!=plan['fbx']:raise FormatError('Animation FBX or Mod changed since planning')
    if native.public_binding(native.profile_for(game))!=plan['binding']:raise FormatError('Native animation backend changed')
    if plan['file_readback']['binding']!=plan['binding']:raise FormatError('Animation file readback backend differs')
    payload=plan['native_payload'];name=native_meshes.namespace(plan['namespace']);target=Path('Libraries')/(name+'.clb')
    if payload['source_summary']!=plan['fbx'] or payload['sha256']!=plan['fbx']['sha256'] or payload['title']!=plan['title'] or payload['namespace']!=name or plan['target']!=target.as_posix():raise FormatError('Animation source/request/target binding differs')
    if payload['sdk_sha256']!=file_hash(game/'libfbxsdk.dll'):raise FormatError('Animation SDK changed')
    if not plan.get('draft') or plan.get('model_binding_verified') is not False or plan.get('pose_sampling_verified') is not False or plan.get('rendering_verified') is not False:raise FormatError('Animation import scope was expanded')
    if assess(payload,plan['file_readback'])!=plan['assessment']:raise FormatError('Animation assessment differs')
    candidate=Path(payload['artifacts'][0]['file']);expected=payload['artifacts'][0]['sha256']
    protected=[game,source.parent]+([Path(baking['original']).parent] if baking else [])
    with copy_directory(acp.parent,output,protected) as (stage,files):
        destination=stage/target;destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(candidate.read_bytes())
        retained=stage/'SourceAnimations'/name/source.name;retained.parent.mkdir(parents=True,exist_ok=True);retained.write_bytes(source.read_bytes())
        if file_hash(candidate)!=expected or file_hash(destination)!=expected or file_hash(retained)!=plan['fbx']['sha256']:raise FormatError('Animation output/source copy differs')
        receipt={'format':'aow4mod-animation-import-receipt/1','target':target.as_posix(),'sha256':expected,
            'source_animation':retained.relative_to(stage).as_posix(),'assessment':plan['assessment'],
            'draft':True,'model_binding_verified':False,'pose_sampling_verified':False,'rendering_verified':False}
        if baking:
            original=Path(baking['original']);original_copy=retained.parent/'original.fbx';original_copy.write_bytes(original.read_bytes())
            if file_hash(original_copy)!=baking['original_sha256'] or file_hash(original)!=baking['original_sha256']:raise FormatError('Original animation source copy changed')
            receipt['source_bake']={'original_file':'original.fbx','original_sha256':baking['original_sha256'],'baked_file':retained.name,'details':baking['details']}
        save_record(retained.parent/'.aow4mod-animation.json',receipt)
    return receipt
