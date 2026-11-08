"""Staged same-library default animation binding with native frame/pose evidence."""
import copy
import json
from pathlib import Path
import uuid

from . import asset_yaml,native_assets,native,content_libraries,model_poses,model_authoring
from .core import FormatError,mod_file,sha
from .filesystem import inventory,file_hash,copy_directory,save_record

FORMAT='aow4mod-model-animation-plan/1'


def fingerprint():
    root=Path(__file__).parent
    return sha(b''.join((root/n).read_bytes() for n in ('model_animation_bindings.py','model_poses.py','animation_poses.py','asset_yaml.py')))


def derive(document,model,animation):
    after=copy.deepcopy(document);members=after['21_FList']['1_this']
    models=[m for m in members if m['20_FName']==model and m['cid']==str(0x41004b)]
    clips=[m for m in members if m['20_FName']==animation and m['cid']==str(0x410005)]
    if len(models)!=1 or len(clips)!=1:raise FormatError('Select an existing model and animation in the same library')
    link=models[0]['36_FDefaultAnimation']
    if link.get('cid')!=str(0x41006d) or link.get('cname')!='TREAnimationLink':raise FormatError('Unsupported default animation link type')
    if link.get('20_FName')==animation:raise FormatError('The model already uses the requested default animation')
    before=copy.deepcopy(link);link['20_FName']=animation;link['23_FUserName']=clips[0].get('21_FUserName','')
    return after,{'model':model,'animation':animation,'before_link':before,'after_link':link}


def make_plan(mod,library,model,animation,game,times=None,allow_unmapped=False,cache_dir=Path('.aow4mod/native'),timeout=60):
    acp=mod_file(Path(mod));before=inventory(acp.parent);library=Path(library).resolve();game=Path(game).resolve()
    if not library.is_relative_to(acp.parent):raise FormatError('Bind an animation only in the staged Mod library')
    relative=library.relative_to(acp.parent).as_posix();lib,m,a,skeleton,declared=model_poses.source(library,model,animation)
    parsed=[content_libraries.LOCATOR.fullmatch(r['identity']['locator']) for r in (m,a)]
    if library.stem.casefold()!=lib.name.casefold() or any(not p or p[1].casefold()!=lib.name.casefold() for p in parsed):
        raise FormatError('Model binding requires matching local library filenames and namespaces')
    cache=native.check_cache_directory(cache_dir,game,[acp.parent]);run=cache/'model-animation-plans'/uuid.uuid4().hex;run.mkdir(parents=True)
    baseline=native_assets.export_library(library,game,run/'baseline',cache,timeout)
    if not baseline['comparison']['all_compared_data_equal']:raise FormatError('Model animation binding requires a stable working library; review its original save changes first')
    document=asset_yaml.load((Path(baseline['output'])/baseline['files'][1]['name']).read_bytes());desired,change=derive(document,m['identity']['locator'],a['identity']['locator'])
    inputs=run/'input';inputs.mkdir();yaml=inputs/'desired.yaml';yaml.write_text(asset_yaml.dump(desired),encoding='utf-8')
    compiled=native_assets.compile_library(yaml,game,run/'compiled',cache,timeout);binary=run/'compiled'/compiled['files'][0]['name']
    if times is None:
        period=a['fields'][0x1f].uint(8);times=list(dict.fromkeys([0,period//4,period//2]))
    pose=model_poses.inspect(binary,change['model'],times,game,allow_unmapped=allow_unmapped,cache_dir=cache,timeout=timeout)
    if pose['assessment']['binding_issue_count'] or pose['assessment']['matrix_issue_count']:raise FormatError('Model/animation frame or pose verification failed: '+str(pose['assessment']))
    proof=run/'pose.json';save_record(proof,pose)
    if inventory(acp.parent)!=before:raise FormatError('Model animation source changed during planning')
    return {'format':FORMAT,'generator_sha256':fingerprint(),'source':str(acp),'source_files':before,'game':str(game),
        'library':str(library),'library_sha256':file_hash(library),'relative_path':relative,'binding':compiled['binding'],
        'request':{'model':change['model'],'animation':change['animation'],'times':times,'allow_unmapped':bool(allow_unmapped)},
        'change':change,'baseline':baseline,'desired_yaml':str(yaml),'desired_yaml_sha256':file_hash(yaml),'compiled':compiled,
        'candidate':str(binary),'candidate_sha256':file_hash(binary),'pose':pose,'pose_file':str(proof),'pose_sha256':file_hash(proof),
        'draft':True,'skinning_verified':False,'rendering_verified':False}


def validate(plan):
    if plan.get('format')!=FORMAT or plan.get('generator_sha256')!=fingerprint():raise FormatError('Unsupported/stale model animation plan')
    acp=mod_file(Path(plan['source']));game=Path(plan['game']);library=Path(plan['library']).resolve()
    model_authoring.relative_path(plan['relative_path'])
    for locator in (plan['request']['model'],plan['request']['animation']):
        parsed=content_libraries.LOCATOR.fullmatch(locator)
        if not parsed or parsed[1].casefold()!=library.stem.casefold():raise FormatError('Model animation namespace/path differs')
    if inventory(acp.parent)!=plan['source_files'] or file_hash(library)!=plan['library_sha256'] or library!=(acp.parent/plan['relative_path']).resolve() or not library.is_relative_to(acp.parent):raise FormatError('Model animation source/path changed')
    if native.public_binding(native.profile_for(game))!=plan['binding']:raise FormatError('Model animation native backend changed')
    baseline=plan['baseline'];compiled=plan['compiled'];pose=plan['pose']
    if any(r['binding']!=plan['binding'] for r in (baseline,compiled,pose)):raise FormatError('Model animation evidence backends differ')
    for record in (baseline,compiled):
        for item in record['files']:
            if file_hash(Path(record['output'])/item['name'])!=item['sha256']:raise FormatError('Model animation input/compile witness changed')
    source_yaml=Path(baseline['output'])/baseline['files'][1]['name'];document=asset_yaml.load(source_yaml.read_bytes())
    saved=Path(baseline['output'])/baseline['files'][0]['name']
    if baseline['source_sha256']!=plan['library_sha256'] or not content_libraries.compare(library,saved)['all_compared_data_equal']:raise FormatError('Model animation baseline source differs')
    expected,change=derive(document,plan['request']['model'],plan['request']['animation'])
    if change!=plan['change'] or file_hash(plan['desired_yaml'])!=plan['desired_yaml_sha256'] or asset_yaml.load(Path(plan['desired_yaml']).read_bytes())!=expected:raise FormatError('Model animation requested recipe differs')
    if compiled['source_sha256']!=plan['desired_yaml_sha256'] or Path(plan['candidate']).resolve()!=(Path(compiled['output'])/compiled['files'][0]['name']).resolve() or file_hash(plan['candidate'])!=plan['candidate_sha256']:raise FormatError('Model animation compiled payload differs')
    actual=asset_yaml.load((Path(compiled['output'])/compiled['files'][1]['name']).read_bytes())
    if asset_yaml.differences(expected,actual):raise FormatError('Model animation compilation changes unrequested data')
    if file_hash(plan['pose_file'])!=plan['pose_sha256'] or json.loads(Path(plan['pose_file']).read_text(encoding='utf-8'))!=pose:raise FormatError('Model animation pose record differs')
    if pose['source_sha256']!=plan['candidate_sha256'] or pose['params']['model']['locator']!=plan['request']['model'] or pose['params']['animation']['locator']!=plan['request']['animation'] or pose['params']['times']!=plan['request']['times'] or pose['params']['allow_unmapped']!=plan['request']['allow_unmapped'] or not pose['params']['require_default']:
        raise FormatError('Model animation pose request/payload binding differs')
    assessment=model_poses.verify(pose)
    if assessment['binding_issue_count'] or assessment['matrix_issue_count'] or not plan.get('draft') or plan.get('skinning_verified') is not False or plan.get('rendering_verified') is not False:raise FormatError('Model animation result/scope requires review')
    return assessment


def apply_plan(plan,output):
    assessment=validate(plan);source=Path(plan['source']);game=Path(plan['game']);library=Path(plan['library']);relative=plan['relative_path']
    model=content_libraries.LOCATOR.fullmatch(plan['request']['model'])
    if not model:raise FormatError('Unsupported model locator')
    with copy_directory(source.parent,output,[game]) as (stage,files):
        target=stage/relative;target.write_bytes(Path(plan['candidate']).read_bytes())
        if file_hash(target)!=plan['candidate_sha256'] or file_hash(library)!=plan['library_sha256']:raise FormatError('Model animation output/source changed during copy')
        receipt={'format':'aow4mod-model-animation-bind-receipt/1','target':relative,'model':plan['request']['model'],'animation':plan['request']['animation'],
            'sha256':file_hash(target),'change':plan['change'],'assessment':assessment,'draft':True,'skinning_verified':False,'rendering_verified':False}
        folder=stage/'SourceModelAnimations'/model[1];folder.mkdir(parents=True,exist_ok=True);save_record(folder/('model-'+model[3]+'.json'),receipt)
    return receipt
