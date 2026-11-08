"""A staged, inspectable maintenance workflow; never installs or publishes."""
import json
from pathlib import Path
import shutil
import uuid

from . import authoring,catalog,environment,migration,publication,snapshots,maintenance_release,synchronization,maintenance_context,maintenance_review
from .core import FormatError,mod_file,read_acp
from .filesystem import file_hash,inventory,new_directory,outside,save_record

FORMAT='aow4mod-maintenance/1'


def read(workspace,check_origin=True):
    root=Path(workspace).resolve();path=root/'maintenance.json';state=json.loads(path.read_text(encoding='utf-8'))
    if state.get('format')!=FORMAT or state.get('root')!=str(root):raise FormatError('Invalid or moved maintenance workspace')
    config,settings,source=authoring.load_project(root/'project')
    if read_acp(mod_file(source))[0].get('package_id')!=state['package_id']:raise FormatError('Maintenance package identity changed')
    if check_origin and inventory(state['origin'])!=state['origin_files']:raise FormatError('Original Mod changed; start a new import rather than overwrite it')
    if state['baseline_sha256'] is not None and file_hash(root/'baseline.json')!=state['baseline_sha256']:
        raise FormatError('Maintenance baseline changed')
    for item in (state.get('assessment') or {}).get('artifacts',[]):
        target=(root/item['file']).resolve()
        if root not in target.parents or not target.is_file() or file_hash(target)!=item['sha256']:
            raise FormatError('Maintenance assessment artifact changed')
    return root,state,config,settings,source,file_hash(path)


def save(root,state,expected):
    path=root/'maintenance.json'
    if file_hash(path)!=expected:raise FormatError('Maintenance state changed concurrently')
    save_record(path,state)


def context_root(root,state,live=True):
    item=state.get('context')
    if item is None:return None
    directory=(root/item['directory']).resolve()
    if root not in directory.parents or file_hash(directory/'context.json')!=item['context_sha256']:
        raise FormatError('Maintenance context record/path changed')
    if live:maintenance_context.validate_live(directory)
    return directory


def context_settings(root,state,config,source,context):
    paths,disabled=maintenance_context.order(context,source,[Path(m['path']) for m in state['mods']])
    validation=json.loads(Path(config).read_text(encoding='utf-8')).get('validation',{})
    expected_order=['@primary' if p==source else str(p) for p in paths]
    if validation.get('mod_order')!=expected_order or validation.get('mods')!=[str(p) for p in paths if p!=source] or validation.get('disabled_dlc')!=disabled:
        raise FormatError('Project validation context/order changed; recapture maintenance context')
    return paths,disabled


def set_context(workspace,playset,depth=None,max_resources=None):
    root,state,config,settings,source,expected=read(workspace)
    relative=Path('contexts')/uuid.uuid4().hex
    captured=maintenance_context.capture(state['origin'],source,playset,root/relative)
    state['context']={'directory':relative.as_posix(),'context_sha256':captured['context_sha256']}
    paths,disabled=maintenance_context.order(root/relative,source,[Path(m['path']) for m in state['mods']])
    copied={k:v for k,v in settings.items() if k!='_loaded_sha256'};validation=copied.setdefault('validation',{})
    validation['mods']=[str(p) for p in paths if p!=source]
    validation['mod_order']=['@primary' if p==source else str(p) for p in paths];validation['disabled_dlc']=disabled
    if depth is not None:
        if type(depth) is not int or not 0<=depth<=12:raise FormatError('Context depth must be 0..12')
        validation['depth']=depth
    if max_resources is not None:
        if type(max_resources) is not int or not 1<=max_resources<=100000:raise FormatError('Context resource budget must be 1..100000')
        validation['max_resources']=max_resources
    if file_hash(config)!=settings['_loaded_sha256']:raise FormatError('Project config changed before context selection')
    save_record(config,copied)
    state['assessment']=None;state['build']=None;state['preparation']=None;state['review']=None;save(root,state,expected)
    return assess(root)


def start(path,output,game,baseline=None,mods=(),workshop=None):
    origin=mod_file(Path(path)).parent.resolve();game=Path(game).resolve();before=inventory(origin)
    previous=snapshots.load(baseline) if baseline else None
    package=read_acp(mod_file(origin))[0].get('package_id')
    if not package:raise FormatError('Maintenance requires a package UUID')
    if previous and previous['mod'].get('package_id')!=package:raise FormatError('Baseline belongs to another Mod')
    mods=[Path(p).resolve() for p in mods]
    if origin in mods or len(set(mods))!=len(mods):raise FormatError('Dependency Mods must be explicit distinct external inputs')
    final=outside(output,[origin,game,*mods])
    with new_directory(final,[origin,game,*mods]) as stage:
        authoring.import_project(origin,stage/'project')
        config_path=stage/'project'/authoring.CONFIG;config=json.loads(config_path.read_text(encoding='utf-8'))
        config['validation']={'native':True,'mods':[str(p) for p in mods]}
        if workshop:
            for key in ('published_file_id','expected_owner'):publication.identity_integer(workshop.get(key),key)
            if not isinstance(workshop.get('expected_title'),str) or not workshop['expected_title']:raise FormatError('Expected Workshop title is required')
            config['workshop']=workshop
        save_record(config_path,config)
        if baseline:shutil.copyfile(baseline,stage/'baseline.json')
        state={'format':FORMAT,'root':str(final),'package_id':package,'origin':str(origin),'origin_files':before,
               'game':str(game),'baseline_sha256':file_hash(stage/'baseline.json') if baseline else None,
               'mods':[{'path':str(p),'files':inventory(p)} for p in mods],
               'assessment':None,'last_stage':None,'build':None,'scope':'Staged maintenance; no production writes, install, playset change or online submission.'}
        save_record(stage/'maintenance.json',state)
        if inventory(origin)!=before:raise FormatError('Original Mod changed during import')
    return assess(final)


def assess(workspace):
    root,state,config,settings,source,expected=read(workspace)
    before=inventory(source);index=root/'.cache/catalog.sqlite'
    context=context_root(root,state)
    paths,disabled=context_settings(root,state,config,source,context) if context else ([source,*[Path(m['path']) for m in state['mods']]],[])
    catalog.build(index,Path(state['game']),paths,with_links=True)
    previous=snapshots.load(root/'baseline.json') if state['baseline_sha256'] else None
    depth=previous.get('dependency_capture',{}).get('depth',0) if previous else 2
    current=snapshots.capture(source,Path(state['game']),index,depth)
    comparison=snapshots.compare(previous,current) if previous else None
    plan=migration.make_rebase_plan(previous,source,Path(state['game']),index) if previous else None
    context_report=maintenance_context.verify(context,source) if context else None
    report=maintenance_release.assessment(previous,current,context_report['summary'] if context_report else None)
    env=environment.analyze(paths,Path(state['game']),index,disabled_dlc=disabled,
          depth=settings.get('validation',{}).get('depth',2),max_resources=settings.get('validation',{}).get('max_resources',2000))
    directory=Path('assessments')/uuid.uuid4().hex
    with new_directory(root/directory,[source,state['origin'],state['game']]) as stage:
        for name,value in (('current.json',current),('comparison.json',comparison),('rebase.json',plan),('assessment.json',report),('environment.json',env)):
            save_record(stage/name,value)
        if context_report:save_record(stage/'context-check.json',context_report)
    files=[{'file':(directory/name).as_posix(),'sha256':file_hash(root/directory/name)}
           for name in ('current.json','comparison.json','rebase.json','assessment.json','environment.json')]
    if context_report:files.append({'file':(directory/'context-check.json').as_posix(),'sha256':file_hash(root/directory/'context-check.json')})
    if inventory(source)!=before or file_hash(config)!=settings['_loaded_sha256']:raise FormatError('Working source/project changed during assessment')
    state['assessment']={'directory':directory.as_posix(),'artifacts':files,'source':str(source),'source_files':before,
                         'project_config_sha256':file_hash(config),'game_inputs':current['original_source_files'],
                         'summary':report,'environment_summary':env['summary']}
    state['build']=None;state['preparation']=None;state['review']=None;save(root,state,expected)
    return status(root)


def current_assessment(root,state,config,source):
    item=state.get('assessment')
    if not item:raise FormatError('Run maintenance-assess first')
    if item['source']!=str(source) or item['source_files']!=inventory(source) or item['project_config_sha256']!=file_hash(config):
        raise FormatError('Working source/project changed; run maintenance-assess')
    for file,digest in item['game_inputs'].items():
        if file_hash(file)!=digest:raise FormatError('Original game inputs changed; run maintenance-assess')
    for mod in state['mods']:
        if inventory(mod['path'])!=mod['files']:raise FormatError('External dependency Mod changed; start a fresh maintenance run')
    folder=(root/item['directory']).resolve()
    if root not in folder.parents:raise FormatError('Maintenance assessment directory escapes workspace')
    current=snapshots.load(folder/'current.json');previous=snapshots.load(root/'baseline.json') if state['baseline_sha256'] else None
    comparison=json.loads((folder/'comparison.json').read_text(encoding='utf-8'))
    report=json.loads((folder/'assessment.json').read_text(encoding='utf-8'))
    env=json.loads((folder/'environment.json').read_text(encoding='utf-8'))
    context=context_root(root,state)
    if context:context_settings(root,state,config,source,context)
    context_report=maintenance_context.verify(context,source) if context else None
    if context_report and json.loads((folder/'context-check.json').read_text(encoding='utf-8'))!=context_report:
        raise FormatError('Maintenance context report changed')
    if (comparison!=(snapshots.compare(previous,current) if previous else None) or
        report!=maintenance_release.assessment(previous,current,context_report['summary'] if context_report else None) or report!=item['summary'] or
        env['summary']!=item['environment_summary'] or
        maintenance_release.projection(source)!=maintenance_release.snapshot_projection(current)):
        raise FormatError('Maintenance assessment no longer matches its evidence/source')
    return folder


def stage(workspace,partial=False):
    root,state,config,settings,source,expected=read(workspace);folder=current_assessment(root,state,config,source)
    previous=snapshots.load(root/'baseline.json') if state['baseline_sha256'] else None
    plan=migration.make_rebase_plan(previous,source,Path(state['game']),root/'.cache/catalog.sqlite',partial) if previous else None
    stored=json.loads((folder/'rebase.json').read_text(encoding='utf-8'))
    if plan and not partial and plan!=stored:raise FormatError('Recomputed migration differs from assessment')
    target=root/'project/stages'/('maintenance-'+uuid.uuid4().hex)
    if plan and plan['plans']:
        if not plan['applicable']:raise FormatError('Migration includes review items; resolve them or explicitly request safe partial staging')
        migration.apply_rebase(plan,target);action='safe_partial_rebase' if partial else 'safe_rebase'
    else:
        with new_directory(target,[source,state['origin'],state['game']]) as copied:
            shutil.copytree(source,copied,dirs_exist_ok=True)
            if inventory(copied)!=inventory(source):raise FormatError('Unchanged working-copy stage differs')
        action='unchanged_copy_no_automatic_repair'
    authoring.select_source(root/'project',target.relative_to(root/'project').as_posix())
    state['last_stage']={'source':str(target),'files':inventory(target),'action':action,'partial_requested':bool(partial)}
    state['assessment']=None;state['build']=None;state['preparation']=None;state['review']=None;save(root,state,expected)
    return assess(root)


def build(workspace,version=None):
    root,state,config,settings,source,expected=read(workspace);current_assessment(root,state,config,source)
    if not state.get('last_stage'):raise FormatError('Stage a verified working copy before building')
    target=root/'builds'/uuid.uuid4().hex
    result=authoring.build_project(root/'project',target,Path(state['game']),version=version,native_checks=True)
    manifest_path=target/'manifest.json';manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest.get('native_validation_requested') is not True:raise FormatError('Maintenance build did not collect required native evidence')
    previous=snapshots.load(root/'baseline.json') if state['baseline_sha256'] else None
    depth=previous.get('dependency_capture',{}).get('depth',0) if previous else 2
    payload=target/manifest['content_directory'];current=snapshots.capture(payload,Path(state['game']),root/'.cache/catalog.sqlite',depth)
    comparison=snapshots.compare(previous,current) if previous else None
    summary=maintenance_release.attach(manifest_path,previous,current,comparison,context_root(root,state))
    checked=publication.check(manifest_path)
    current_assessment(root,state,config,source)
    state['build']={'manifest':str(manifest_path.relative_to(root)),'manifest_sha256':file_hash(manifest_path),
                    'preflight':checked,'maintenance_summary':summary,'version':result['version']}
    state['preparation']=None
    state['review']=None
    save(root,state,expected)
    return status(root)


def record_review(workspace,note,preview=None):
    root,state,config,settings,source,expected=read(workspace);current_assessment(root,state,config,source)
    if not state.get('build'):raise FormatError('Review requires a current native-built candidate')
    manifest_path=root/state['build']['manifest']
    if file_hash(manifest_path)!=state['build']['manifest_sha256']:raise FormatError('Review candidate manifest changed')
    manifest,_=publication.load_release(manifest_path)
    if preview and root not in Path(preview).resolve().parents:raise FormatError('Preview review evidence must be inside this workspace')
    directory=Path('reviews')/uuid.uuid4().hex
    (root/directory).parent.mkdir(exist_ok=True)
    record=maintenance_review.create(root/directory,state,manifest,note,preview)
    maintenance_review.validate(root/directory,state,manifest)
    current_assessment(root,state,config,source)
    state['review']={'directory':directory.as_posix(),'sha256':record['sha256']};state['preparation']=None
    save(root,state,expected);return status(root)


def current_review(root,state,manifest):
    record=state.get('review')
    if not record:return None
    directory=(root/record['directory']).resolve()
    if root not in directory.parents or file_hash(directory/'review.json')!=record['sha256']:
        raise FormatError('Maintenance review path/content changed')
    return maintenance_review.validate(directory,state,manifest)


def prepare_release(workspace,destination=None,backup=None,online=False,proxy=None,allow_review_plan=False):
    root,state,config,settings,source,expected=read(workspace);current_assessment(root,state,config,source)
    if not state.get('build'):raise FormatError('Build a maintenance candidate first')
    if bool(destination)!=bool(backup):raise FormatError('Installation destination and backup must be supplied together')
    manifest_path=root/state['build']['manifest']
    if file_hash(manifest_path)!=state['build']['manifest_sha256']:raise FormatError('Maintenance manifest changed')
    checked=publication.check(manifest_path,online=online,game=Path(state['game']),proxy=proxy)
    review=current_review(root,state,json.loads(manifest_path.read_text(encoding='utf-8')))
    directory=Path('preparations')/uuid.uuid4().hex;output=root/directory;output.mkdir(parents=True)
    artifacts=[]
    if destination:
        plan=synchronization.make_plan(manifest_path,destination,backup,Path(state['game']),allow_review=allow_review_plan)
        save_record(output/'installation-plan.json',plan)
        artifacts.append({'file':(directory/'installation-plan.json').as_posix(),'sha256':file_hash(output/'installation-plan.json')})
    manifest,content=publication.load_release(manifest_path)
    frozen,payload=publication.stage_release(manifest,content,output);publication.check_staged(frozen)
    save_record(output/'prepared-publication.json',frozen);save_record(output/'preflight.json',checked)
    for name in ('prepared-publication.json','preflight.json'):
        artifacts.append({'file':(directory/name).as_posix(),'sha256':file_hash(output/name)})
    if review:
        shutil.copytree(root/state['review']['directory'],output/'review')
        maintenance_review.validate(output/'review',state,manifest)
        artifacts.append({'file':(directory/'review/review.json').as_posix(),'sha256':file_hash(output/'review/review.json')})
    current_assessment(root,state,config,source)
    state['preparation']={'directory':directory.as_posix(),'manifest_sha256':state['build']['manifest_sha256'],
        'artifacts':artifacts,'preflight':checked,'installation_planned':destination is not None,
        'review_override_for_plan_only':bool(allow_review_plan),'review_sha256':state['review']['sha256'] if review else None,'submitted':False}
    save(root,state,expected)
    return status(root)


def status(workspace):
    root,state,config,settings,source,_=read(workspace);stale=None
    attribution=None
    try:
        folder=current_assessment(root,state,config,source)
        env=json.loads((folder/'environment.json').read_text(encoding='utf-8'))
        explained=environment.explain(env,source)
        attribution={k:v for k,v in explained.items() if k!='rows'}
    except FormatError as error:stale=str(error)
    assessed=state.get('assessment') or {};built=state.get('build');check=None
    if built:
        manifest=root/built['manifest']
        if root not in manifest.resolve().parents or file_hash(manifest)!=built['manifest_sha256']:raise FormatError('Maintenance build manifest changed')
        check=publication.check(manifest)
    review=current_review(root,state,json.loads(manifest.read_text(encoding='utf-8'))) if built else None
    prepared=state.get('preparation')
    if prepared:
        if not built or prepared['manifest_sha256']!=built['manifest_sha256']:raise FormatError('Prepared maintenance candidate differs from build')
        preparation_root=(root/prepared['directory']).resolve()
        if root not in preparation_root.parents:raise FormatError('Maintenance preparation directory escapes workspace')
        for artifact in prepared['artifacts']:
            path=(root/artifact['file']).resolve()
            if root not in path.parents or file_hash(path)!=artifact['sha256']:raise FormatError('Maintenance preparation changed')
        frozen=json.loads((preparation_root/'prepared-publication.json').read_text(encoding='utf-8'))
        if frozen.get('_manifest_sha256')!=built['manifest_sha256'] or preparation_root not in Path(frozen['content_directory']).resolve().parents:
            raise FormatError('Frozen maintenance preparation belongs to a different candidate/location')
        publication.check_staged(frozen)
        if review:
            if prepared.get('review_sha256')!=state['review']['sha256']:raise FormatError('Prepared review differs from current scoped review')
            maintenance_review.validate(preparation_root/'review',state,json.loads(manifest.read_text(encoding='utf-8')))
    summary=assessed.get('summary',{})
    if stale:phase='assessment_stale';next_command='maintenance-assess'
    elif not state.get('last_stage'):phase='needs_staging';next_command='maintenance-stage'
    elif not built:phase='ready_to_build_with_review' if summary.get('review_required') or assessed.get('environment_summary',{}).get('review_required') else 'ready_to_build';next_command='maintenance-build'
    elif review and review.get('preview'):
        phase='reviewed_lifecycle_findings_open' if check['review_required'] else 'reviewed_lifecycle_verified'
        next_command='resolve remaining compatibility findings / explicit authorized publication task'
    elif prepared:
        phase='prepared_review_required' if check['review_required'] else 'prepared_not_submitted' if check['workshop_configured'] else 'prepared_workshop_identity_missing'
        next_command='review findings / configure missing identity / explicit installation or publication task'
    elif check['review_required']:phase='built_review_required';next_command='maintenance-prepare-release'
    elif not check['workshop_configured']:phase='built_workshop_identity_missing';next_command='configure project workshop identity, then assess/build'
    else:phase='built_ready_for_preparation';next_command='sync-plan / release-check --online (read-only)'
    return {'workspace':str(root),'phase':phase,'message':{'needs_staging':'检查已完成，下一步生成暂存候选；不改生产 Mod。',
        'ready_to_build':'暂存源已就绪，可进行原生构建。','ready_to_build_with_review':'可构建候选；尚有待审事项，将保留在发布门槛。',
        'built_review_required':'候选已构建，待审事项仍需处理或明确评估。','built_workshop_identity_missing':'候选已构建，尚未配置现有工坊条目身份。',
        'built_ready_for_preparation':'本地候选可准备安装/在线身份预检；未安装、未提交。','assessment_stale':'输入发生变化，需要重新检查。',
        'prepared_review_required':'安装/发布材料已准备，待审事项仍保留；未安装、未提交。',
        'prepared_workshop_identity_missing':'材料已冻结，尚未配置工坊身份；不能执行现有条目更新。',
        'prepared_not_submitted':'候选与证据已冻结，等待对应的安装/发布指令；未提交。',
        'reviewed_lifecycle_findings_open':'维护预览/撤回证据已验证；兼容性待审项仍开放，未授权发布。',
        'reviewed_lifecycle_verified':'维护生命周期证据已验证；不等于玩法验证或发布授权。'}[phase],
        'next_command':next_command,'stale_reason':stale,'assessment':summary,
        'environment_summary':assessed.get('environment_summary'),'environment_attribution':attribution,
        'stage':state.get('last_stage',{}).get('action') if state.get('last_stage') else None,
        'validation_mod_scope':'Captured enabledMods order with primary candidate substitution, plus explicit dependencies.' if state.get('context') else 'Primary Mod and explicit --mod inputs only; active playset/conflicts are not inferred.',
        'playset_context':summary.get('playset_context'),
        'reports':{'assessment':str(root/assessed['directory']/'assessment.json') if assessed else None,
                   'comparison':str(root/assessed['directory']/'comparison.json') if assessed else None,
                   'environment':str(root/assessed['directory']/'environment.json') if assessed else None,
                   'native':str((root/built['manifest']).parent/'native-validation.json') if built else None},
        'manifest':str(root/built['manifest']) if built else None,
        'release_preflight':{**{k:check[k] for k in ('payload_verified','version','workshop_configured','review_required')},
             'native_validation_summary':{k:check['native_validation_summary'][k] for k in ('checks','errors','mismatches','diagnostic_count')} if check.get('native_validation_summary') else None} if check else None,
        'preparation':{'directory':str(root/prepared['directory']),'online_checked':prepared['preflight']['online_checked'],
                       'installation_planned':prepared['installation_planned'],'submitted':False} if prepared else None,
        'review':{'file':str(root/state['review']['directory']/'review.json'),'conclusion':review} if review else None,
        'installed':False,'published':False,'scope':state['scope']}
