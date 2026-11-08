"""Draft new skeletal model authoring from explicitly prepared FBX source."""
import copy
import json
from pathlib import Path
import uuid

from . import native,native_assets,native_skeletal,native_meshes,native_validation,asset_yaml,asset_templates,animation_imports,animation_baking
from . import skeletal_sources,skeletal_data,skin_meshes,model_authoring,model_skinning,content_libraries,native_objects,editing,source_correspondence,animation_precision,source_motion
from .core import FormatError,sha,mod_file,Node
from .filesystem import file_hash,inventory,copy_directory,save_record

FORMAT='aow4mod-skeletal-import-plan/2'
RECEIPT='aow4mod-skeletal-import-receipt/2'


def fingerprint():
    return sha(b''.join((Path(__file__).parent/n).read_bytes() for n in ('skeletal_imports.py','skeletal_data.py','skeletal_sources.py','skin_meshes.py','native_skeletal.py','model_authoring.py','model_skinning.py','fbx.py','source_correspondence.py','animation_precision.py','source_motion.py')))


def checked(item):
    path=Path(item['file'])
    if not 1<=path.stat().st_size<=32*1024*1024 or file_hash(path)!=item['sha256']:raise FormatError('Skeletal artifact size/hash differs')
    return path.read_bytes()


def document(record):
    for item in record['files']:
        if file_hash(Path(record['output'])/item['name'])!=item['sha256']:raise FormatError('Skeletal native file witness changed')
    return asset_yaml.load((Path(record['output'])/record['files'][1]['name']).read_bytes())


def normalization(before,after):
    a,b=copy.deepcopy(before),copy.deepcopy(after)
    if len(a['21_FList']['1_this'])!=1 or a['21_FList']['1_this'][0]['cid']!=str(0x410035):raise FormatError('Initial skeleton normalization requires one newly imported mesh')
    for d in (a,b):
        mesh=d['21_FList']['1_this'][0]
        for key in ('26_FFrames','63_FFrames'):
            import base64
            frames=mesh[key];frames.pop('26_FHash',None);frames['22_Frames']=base64.b64encode(skin_meshes.frame_data(base64.b64decode(frames['22_Frames']))).decode()
    if asset_yaml.differences(a,b):raise FormatError('Initial skeletal library load changes more than frame hash/reserved bytes')
    return [{'path':d['path'],'issue':d['issue']} for d in asset_yaml.differences(before,after)]


def mesh_assess(payload,proof):
    raw={k:checked(v) for k,v in payload['objects'].items()}
    hierarchy,change=source_correspondence.repair_hierarchy(payload['source_summary']['file'],raw['before'])
    if hierarchy!=raw['hierarchy_authored'] or change!=payload['hierarchy_change']:raise FormatError('Source hierarchy authoring recipe differs')
    if source_correspondence.hierarchy_readback(raw['hierarchy_authored'],raw['hierarchy_loaded'])!=payload['hierarchy_initialization']:raise FormatError('Source hierarchy initialization differs')
    native_skeletal.compare_finalization(raw['hierarchy_loaded'],raw['finalized'])
    expected,change=skeletal_data.extend(raw['finalized']);expected=editing.rewrite_map(Node(expected,0,len(expected)),{0x14:editing.encode_value('string',f'[{payload["namespace"]}]MESH\\0')})
    if expected!=raw['extended'] or change!=payload['change']:raise FormatError('Skeletal source mesh/shading derivation differs')
    if skin_meshes.compare_body(raw['extended'],raw['readback'])!=payload['initial_object_comparison']:raise FormatError('Skeletal initial object comparison differs')
    # Initial in-memory copies can differ in proven serializer-reserved bytes.
    for left,right in [('extended','readback'),('readback','stable')]:
        a,b=Node(raw[left],0,len(raw[left])).mapping(),Node(raw[right],0,len(raw[right])).mapping()
        if a.keys()!=b.keys():raise FormatError('Skeletal object field coverage changed')
        for key in a:
            if key not in (0x1a,0x3f):
                if a[key].raw()!=b[key].raw():raise FormatError('Skeletal object semantic data changed')
            else:
                x,y=a[key].mapping(),b[key].mapping()
                if x.keys()!=y.keys() or any((skin_meshes.frame_data(x[k].raw())!=skin_meshes.frame_data(y[k].raw())) if k==0x16 else x[k].raw()!=y[k].raw() for k in x):raise FormatError('Skeletal object frame data changed')
    if proof['source_sha256']!=payload['artifacts'][0]['sha256'] or Path(proof['source']).resolve()!=Path(payload['artifacts'][0]['file']).resolve():raise FormatError('Skeletal mesh file proof source differs')
    direct=asset_yaml.load(checked(payload['artifacts'][1]));loaded=document(proof)
    changes=normalization(direct,loaded)
    mesh=loaded['21_FList']['1_this'][0];skin_meshes.compare_yaml(raw['readback'],mesh)
    checked(payload['artifacts'][0])
    return changes


def make_plan(mod,source,namespace,title,game,dxc,weights='strict',max_dropped_fraction=None,bake_fps=None,bake_max_error=0.001,cache_dir=Path('.aow4mod/native'),timeout=120,rotation_storage='snorm16'):
    acp=mod_file(Path(mod));source=Path(source).resolve();game=Path(game).resolve();name=native_meshes.namespace(namespace);before=inventory(acp.parent);original={'file':str(source),'sha256':file_hash(source)}
    if rotation_storage not in ('snorm16','float32-guarded'):raise FormatError('Unknown source rotation storage policy')
    if not isinstance(title,str) or not title.strip() or len(title.encode())>96 or '\0' in title:raise FormatError('Skeleton title must fit 1..96 UTF-8 bytes')
    if weights not in ('strict','normalize-top4') or (weights=='strict' and max_dropped_fraction is not None) or (weights=='normalize-top4' and max_dropped_fraction is None):raise FormatError('Explicit normalization needs --max-dropped-fraction; strict mode has no repair bound')
    if any(content_libraries.identity(p)['name'].casefold()==name.casefold() for p in acp.parent.rglob('*.clb')) or (acp.parent/'SourceSkeletons'/name).exists():raise FormatError('Skeletal library/source namespace exists')
    cache=native.check_cache_directory(cache_dir,game,[acp.parent,source.parent]);root=cache/'skeletal-imports'/uuid.uuid4().hex;root.mkdir(parents=True)
    bake=animation_baking.prepare(source,bake_fps,cache,bake_max_error) if bake_fps is not None else None
    if bake:source=Path(bake['candidate'])
    normalization_record=None
    if weights=='normalize-top4':
        raw,details=skeletal_sources.normalize(source,max_dropped_fraction);candidate=root/'prepared.fbx';candidate.write_bytes(raw);normalization_record={'source':str(source),'details':details};source=candidate
    info=skeletal_sources.source(source)[3];params={'path':str(source),'sha256':file_hash(source),'namespace':name}
    mesh=native.run_worker(game,'skeletal-import',params,cache,timeout);mesh_proof=native_assets.export_library(mesh['payload']['artifacts'][0]['file'],game,root/'mesh-loaded',cache,timeout);mesh_changes=mesh_assess(mesh['payload'],mesh_proof)
    animation=native.run_worker(game,'animation-import',dict(params,title=title+' motion'),cache,timeout);anim_proof=native_assets.export_library(animation['payload']['artifacts'][0]['file'],game,root/'animation-loaded',cache,timeout);animation_imports.assess(animation['payload'],anim_proof)
    templates=asset_templates.extract(game/'Content/System/Libraries/System_Effects.clb',list(model_authoring.TEMPLATES),game,root/'templates',cache,timeout);template_yaml=Path(templates['files'][1]['file'])
    mesh_document=document(mesh_proof);correspondence=source_correspondence.assess(source,source_correspondence.from_clb(Path(mesh_proof['output'])/mesh_proof['files'][0]['name'],f'[{name}]MESH\\0'))
    desired,change=skeletal_data.derive(mesh_document,document(anim_proof),asset_yaml.load(template_yaml.read_bytes()),title)
    precision=None
    if rotation_storage=='float32-guarded':desired,precision=animation_precision.derive(desired,change['animation'],source)
    folder=root/'input';folder.mkdir();yaml=folder/'desired.yaml';yaml.write_text(asset_yaml.dump(desired),encoding='utf-8',newline='\n')
    compiled=native_assets.compile_library(yaml,game,root/'compiled',cache,timeout);binary=root/'compiled'/compiled['files'][0]['name'];loaded=native_assets.export_library(binary,game,root/'loaded',cache,timeout)
    if not loaded['comparison']['all_compared_data_equal']:raise FormatError('New skeletal model library is not stable after construction')
    period=animation['payload']['assessment']['period_native'];times=[0,period//2,period]
    reference=model_skinning.inspect(binary,change['model'],times,game,dxc,cache_dir=cache,timeout=timeout,directions=True,allow_unmapped=change['added_root'])
    correspondence['model_correspondence']=source_correspondence.model_assess(source,binary,change['model'],change['mesh'])
    missing=reference['pose']['assessment']['unmapped_frames_using_bind_pose']
    if missing!=(['_CLI_SkeletonRoot'] if change['added_root'] else []):raise FormatError('Source skeleton has unexpected unmapped animation frames')
    diagnostics=native_validation.diagnostics(mesh['run'])+native_validation.diagnostics(animation['run'])+mesh_proof['diagnostics']+anim_proof['diagnostics']+compiled['diagnostics']+loaded['diagnostics']+reference['diagnostics']
    if diagnostics or not reference['assessment']['position_reference_verified'] or not reference['assessment']['direction_reference_verified']:raise FormatError('Skeletal import has diagnostics or reference differences')
    if inventory(acp.parent)!=before or file_hash(original['file'])!=original['sha256']:raise FormatError('Skeletal source Mod/FBX changed')
    return {'format':FORMAT,'generator_sha256':fingerprint(),'source':str(acp),'source_files':before,'input':original,'bake':bake,'weight_policy':weights,'max_dropped_fraction':max_dropped_fraction,'weight_preparation':normalization_record,'prepared':info,'namespace':name,'title':title,'game':str(game),'binding':compiled['binding'],
        'rotation_storage':rotation_storage,'rotation_precision':precision,'mesh':mesh,'mesh_proof':mesh_proof,'source_correspondence':correspondence,'mesh_initial_changes':mesh_changes,'animation':animation,'animation_proof':anim_proof,'templates':templates,'template_yaml':str(template_yaml),'template_yaml_sha256':file_hash(template_yaml),'change':change,'desired_yaml':str(yaml),'desired_yaml_sha256':file_hash(yaml),'compiled':compiled,'candidate':str(binary),'candidate_sha256':file_hash(binary),'loaded':loaded,'reference':reference,'diagnostics':diagnostics,'draft':True,'bind_space_verified':False,'rendering_verified':False}


def validate(plan):
    if plan.get('format')!=FORMAT or plan.get('generator_sha256')!=fingerprint():raise FormatError('Unsupported/stale skeletal import plan')
    if plan.get('draft') is not True or plan.get('bind_space_verified') is not False or plan.get('rendering_verified') is not False or plan.get('diagnostics'):raise FormatError('Skeletal draft verification scope differs')
    source=mod_file(Path(plan['source']));game=Path(plan['game']);original=plan['input'];name=native_meshes.namespace(plan['namespace'])
    if inventory(source.parent)!=plan['source_files'] or file_hash(original['file'])!=original['sha256']:raise FormatError('Skeletal source changed')
    if native.public_binding(native.profile_for(game))!=plan['binding']:raise FormatError('Skeletal native backend changed')
    prepared=Path(plan['bake']['candidate']) if plan['bake'] else Path(original['file'])
    if plan['bake']:
        animation_baking.verify(plan['bake'])
        if Path(plan['bake']['original']).resolve()!=Path(original['file']).resolve():raise FormatError('Skeletal bake input differs')
    if plan['weight_policy']=='normalize-top4':
        raw,details=skeletal_sources.normalize(prepared,plan['max_dropped_fraction'])
        if not plan['weight_preparation'] or details!=plan['weight_preparation']['details'] or Path(plan['weight_preparation']['source']).resolve()!=prepared.resolve() or sha(raw)!=plan['prepared']['sha256']:raise FormatError('Skeletal weight preparation differs')
    elif plan['weight_policy']!='strict' or plan['weight_preparation'] is not None or plan['max_dropped_fraction'] is not None or prepared.resolve()!=Path(plan['prepared']['file']).resolve():raise FormatError('Skeletal source weight policy differs')
    if skeletal_sources.source(plan['prepared']['file'])[3]!=plan['prepared']:raise FormatError('Prepared skeletal source differs')
    for worker in (plan['mesh'],plan['animation']):
        if native.public_binding(worker['binding'])!=plan['binding'] or worker['payload']['sha256']!=plan['prepared']['sha256'] or worker['payload']['namespace']!=name:raise FormatError('Skeletal native source/backend differs')
        if native_validation.diagnostics(worker['run']):raise FormatError('Skeletal native raw logs contain diagnostics')
    if plan['mesh']['payload']['source_summary']!=plan['prepared']:raise FormatError('Skeletal mesh source policy differs')
    if mesh_assess(plan['mesh']['payload'],plan['mesh_proof'])!=plan['mesh_initial_changes']:raise FormatError('Skeletal initial mesh normalization differs')
    correspondence=source_correspondence.assess(plan['prepared']['file'],source_correspondence.from_clb(Path(plan['mesh_proof']['output'])/plan['mesh_proof']['files'][0]['name'],f'[{name}]MESH\\0'))
    expected_correspondence=dict(plan['source_correspondence']);expected_correspondence.pop('model_correspondence',None)
    if correspondence!=expected_correspondence:raise FormatError('Source correspondence differs from independent FBX recomputation')
    animation_imports.assess(plan['animation']['payload'],plan['animation_proof'])
    if plan['animation']['payload']['title']!=plan['title']+' motion':raise FormatError('Skeletal requested motion name differs')
    template_record=plan['templates'];official=(game/'Content/System/Libraries/System_Effects.clb').resolve()
    if Path(template_record['source']).resolve()!=official or template_record['source_sha256']!=file_hash(official) or template_record['binding']!=plan['binding']:raise FormatError('Skeletal Default templates are not bound to the official source')
    for item in template_record['files']:model_authoring._checked(item['file'],item['sha256'])
    if Path(plan['template_yaml']).resolve()!=Path(template_record['files'][1]['file']).resolve() or plan['template_yaml_sha256']!=template_record['files'][1]['sha256']:raise FormatError('Skeletal template YAML selection differs')
    templates=asset_yaml.load(model_authoring._checked(plan['template_yaml'],plan['template_yaml_sha256']))
    desired,change=skeletal_data.derive(document(plan['mesh_proof']),document(plan['animation_proof']),templates,plan['title'])
    if plan.get('rotation_storage')=='float32-guarded':
        desired,precision=animation_precision.derive(desired,change['animation'],plan['prepared']['file'])
        if precision!=plan.get('rotation_precision'):raise FormatError('Source float32 rotation recipe differs')
    elif plan.get('rotation_storage')!='snorm16' or plan.get('rotation_precision') is not None:raise FormatError('Source rotation storage policy differs')
    if desired['20_FName']!=name or change!=plan['change'] or asset_yaml.load(model_authoring._checked(plan['desired_yaml'],plan['desired_yaml_sha256']))!=desired:raise FormatError('Skeletal model request/recipe differs')
    for record in (plan['mesh_proof'],plan['animation_proof'],plan['compiled'],plan['loaded']):
        if record['binding']!=plan['binding'] or record.get('diagnostics'):raise FormatError('Skeletal independent evidence backend/diagnostics differ')
        if native_validation.diagnostics(record['run']):raise FormatError('Skeletal native file logs contain diagnostics')
        document(record)
    candidate=Path(plan['candidate']);compiled=plan['compiled'];loaded=plan['loaded']
    if candidate.resolve()!=(Path(compiled['output'])/compiled['files'][0]['name']).resolve() or file_hash(candidate)!=plan['candidate_sha256'] or compiled['source_sha256']!=plan['desired_yaml_sha256']:raise FormatError('Skeletal compiled payload differs')
    if document(compiled)!=desired or document(loaded)!=desired or loaded['source_sha256']!=plan['candidate_sha256'] or not content_libraries.compare(candidate,Path(loaded['output'])/loaded['files'][0]['name'])['all_compared_data_equal']:raise FormatError('Skeletal complete native file data differs')
    if source_correspondence.model_assess(plan['prepared']['file'],candidate,change['model'],change['mesh'])!=plan['source_correspondence'].get('model_correspondence'):raise FormatError('Source model default frames/palette differ')
    reference=plan['reference'];period=plan['animation']['payload']['assessment']['period_native']
    if reference['source_sha256']!=plan['candidate_sha256'] or reference['params']['model']['locator']!=change['model'] or reference['pose']['params']['times']!=[0,period//2,period] or reference['diagnostics']:raise FormatError('Skeletal model reference selection differs')
    if Path(reference['source']).resolve()!=candidate.resolve() or reference['pose']['assessment']['unmapped_frames_using_bind_pose']!=(['_CLI_SkeletonRoot'] if change['added_root'] else []):raise FormatError('Skeletal reference input/unmapped frame policy differs')
    assessment=model_skinning.verify(reference)
    if not assessment['all_component_vertices_checked'] or not assessment['position_reference_verified'] or not assessment['direction_reference_verified']:raise FormatError('Skeletal references are incomplete')
    return change


def apply_plan(plan,output):
    change=validate(plan);source=Path(plan['source']);name=plan['namespace']
    with copy_directory(source.parent,output,[Path(plan['game']),Path(plan['input']['file']).parent]) as (stage,before):
        if before!=plan['source_files']:raise FormatError('Skeletal source changed while copying')
        target=stage/'Libraries'/(name+'.clb');target.parent.mkdir(exist_ok=True);target.write_bytes(Path(plan['candidate']).read_bytes())
        if file_hash(target)!=plan['candidate_sha256']:raise FormatError('Skeletal library copy differs')
        folder=stage/'SourceSkeletons'/name;folder.mkdir(parents=True);(folder/'original.fbx').write_bytes(Path(plan['input']['file']).read_bytes());(folder/'prepared.fbx').write_bytes(Path(plan['prepared']['file']).read_bytes())
        if file_hash(folder/'original.fbx')!=plan['input']['sha256'] or file_hash(folder/'prepared.fbx')!=plan['prepared']['sha256']:raise FormatError('Skeletal retained source copy differs')
        receipt={'format':RECEIPT,'target':target.relative_to(stage).as_posix(),'model':change['model'],'change':change,'rotation_storage':plan['rotation_storage'],'rotation_precision':plan['rotation_precision'],'prepared_source_sha256':plan['prepared']['sha256'],'original_source_sha256':plan['input']['sha256'],'weight_policy':plan['weight_policy'],'weight_preparation':plan['weight_preparation'],'mesh_initial_changes':plan['mesh_initial_changes'],'source_correspondence':plan['source_correspondence'],'hierarchy_change':plan['mesh']['payload']['hierarchy_change'],'draft':True,'bind_space_verified':False,'rendering_verified':False}
        save_record(folder/'.aow4mod-skeleton-v2.json',receipt)
    return receipt
