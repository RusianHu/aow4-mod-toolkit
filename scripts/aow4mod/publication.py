"""Release validation and existing-item Workshop operations with recovery evidence."""
from datetime import datetime,timezone
import json
from pathlib import Path
import uuid

from .core import FormatError,sha
from .filesystem import inventory,file_hash,copy_directory

APP_ID=1669000


def release_path(root,value,directory=False):
    if not isinstance(value,str):raise FormatError('Invalid release path')
    p=Path(value);p=p.resolve() if p.is_absolute() else (root/p).resolve()
    if root not in p.parents:raise FormatError('Release files/content must be inside the release directory')
    if directory and not p.is_dir():raise FormatError('Release content directory missing')
    if not directory and not p.is_file():raise FormatError('Release file missing: '+str(p))
    return p


def identity_integer(value,label):
    if isinstance(value,bool) or not isinstance(value,(str,int)):
        raise FormatError(f'{label} must be an integer or decimal string')
    if isinstance(value,str) and not value.isdecimal():raise FormatError(f'Invalid {label}')
    result=int(value)
    if not 0<result<=0xffffffffffffffff:raise FormatError(f'Invalid {label}')
    return result


def check_model_drafts(manifest,expected):
    models=manifest.get('draft_model_assets',[])
    if not isinstance(models,list) or any(not isinstance(m,dict) for m in models):raise FormatError('Invalid draft model asset policy')
    # Model authoring keeps library-named provenance outside the runtime
    # payload. Its source inventory entry must not silently lose the gate.
    for relative,entry in manifest.get('source_files',{}).items():
        parts=Path(relative).parts
        if len(parts)==3 and parts[0]=='SourceModels' and parts[-1]=='.aow4mod-model.json':
            for asset in expected:
                if Path(asset).suffix.casefold()=='.clb' and Path(asset).stem.casefold()==parts[1].casefold():
                    if not any(d.get('file')==asset and d.get('source_receipt')==relative and d.get('source_receipt_sha256')==entry['sha256'] for d in models):
                        raise FormatError('Draft model provenance is missing from the manifest')
    for draft in models:
        if not isinstance(draft,dict) or draft.get('file') not in expected or not draft.get('models'):
            raise FormatError('Draft model policy names a missing asset/model')
        gate=manifest.get('release_gate',{})
        if not gate.get('review_required') or not any(i.get('issue')=='draft_model_asset_requires_variant_review' and i.get('file')==draft['file'] for i in gate.get('issues',[])):
            raise FormatError('Draft model findings are missing from the release gate')


def check_material_drafts(manifest,expected):
    entries=manifest.get('draft_material_assets',[])
    if not isinstance(entries,list) or any(not isinstance(e,dict) for e in entries):raise FormatError('Invalid material draft policy')
    for relative,record in manifest.get('source_files',{}).items():
        parts=Path(relative).parts
        if len(parts)==3 and parts[0]=='SourceMaterials' and parts[-1]=='.aow4mod-material.json':
            for file in expected:
                if Path(file).suffix.casefold()=='.clb' and Path(file).stem.casefold()==parts[1].casefold():
                    if not any(e.get('file')==file and e.get('source_receipt')==relative and e.get('source_receipt_sha256')==record['sha256'] for e in entries):
                        raise FormatError('Material draft provenance is missing from manifest')
    for entry in entries:
        gate=manifest.get('release_gate',{})
        if entry.get('file') not in expected or not gate.get('review_required') or not any(e.get('issue')=='draft_material_asset_requires_texture_variant_review' and e.get('file')==entry['file'] for e in gate.get('issues',[])):
            raise FormatError('Material texture/variant findings are missing from release gate')


def check_skeletal_drafts(manifest,expected):
    entries=manifest.get('draft_skeletal_assets',[])
    if not isinstance(entries,list) or any(not isinstance(e,dict) for e in entries):raise FormatError('Invalid skeletal draft policy')
    for relative,record in manifest.get('source_files',{}).items():
        parts=Path(relative).parts
        if len(parts)==3 and parts[0]=='SourceSkeletons' and parts[-1] in ('.aow4mod-skeleton.json','.aow4mod-skeleton-v2.json'):
            for file in expected:
                if Path(file).suffix.casefold()=='.clb' and Path(file).stem.casefold()==parts[1].casefold():
                    if not any(e.get('file')==file and e.get('source_receipt')==relative and e.get('source_receipt_sha256')==record['sha256'] for e in entries):raise FormatError('Skeletal source provenance is missing')
    for entry in entries:
        required=entry.get('source_correspondence_required',False)
        if type(required) is not bool or entry.get('source_receipt','').endswith('.aow4mod-skeleton-v2.json') and required is not True:raise FormatError('Skeletal source requirement was removed')
        if required and manifest.get('skeletal_source_validation_requested') is not True:raise FormatError('Required skeletal source validation is missing')
        gate=manifest.get('release_gate',{})
        if entry.get('file') not in expected or not entry.get('model') or not gate.get('review_required') or not any(e.get('issue')=='skeletal_model_requires_bind_space_bounds_variant_review' and e.get('file')==entry['file'] for e in gate.get('issues',[])):raise FormatError('Skeletal bind-space/bounds/variant review is missing')


def check_pose_bake_drafts(manifest,expected):
    entries=manifest.get('draft_pose_bake_assets',[])
    if not isinstance(entries,list) or any(not isinstance(e,dict) for e in entries):raise FormatError('Invalid pose bake draft policy')
    for relative,record in manifest.get('source_files',{}).items():
        parts=Path(relative).parts
        if len(parts)==3 and parts[0]=='SourcePoseBakes' and parts[-1].endswith('.json'):
            for file in expected:
                if Path(file).suffix.casefold()=='.clb' and Path(file).stem.casefold()==parts[1].casefold():
                    if not any(e.get('file')==file and e.get('source_receipt')==relative and e.get('source_receipt_sha256')==record['sha256'] for e in entries):raise FormatError('Pose bake provenance is missing')
    for entry in entries:
        gate=manifest.get('release_gate',{})
        if entry.get('file') not in expected or not entry.get('model') or not gate.get('review_required') or not any(e.get('issue')=='pose_bake_requires_static_variant_review' and e.get('file')==entry['file'] for e in gate.get('issues',[])):raise FormatError('Pose bake static variant findings are missing')


def check_skin_weight_drafts(manifest,expected):
    entries=manifest.get('draft_skin_weight_assets',[])
    if not isinstance(entries,list) or any(not isinstance(e,dict) for e in entries):raise FormatError('Invalid skin weight draft policy')
    for relative,record in manifest.get('source_files',{}).items():
        parts=Path(relative).parts
        if len(parts)==3 and parts[0]=='SourceSkinWeights' and parts[-1].endswith('.json'):
            for file in expected:
                if Path(file).suffix.casefold()=='.clb' and Path(file).stem.casefold()==parts[1].casefold():
                    if not any(e.get('file')==file and e.get('source_receipt')==relative and e.get('source_receipt_sha256')==record['sha256'] for e in entries):raise FormatError('Skin weight provenance is missing')
    for entry in entries:
        gate=manifest.get('release_gate',{})
        if entry.get('file') not in expected or not entry.get('mesh') or not gate.get('review_required') or not any(e.get('issue')=='skin_weights_require_runtime_variant_review' and e.get('file')==entry['file'] for e in gate.get('issues',[])):
            raise FormatError('Skin weight runtime/variant findings are missing')


def check_model_animation_drafts(manifest,expected):
    entries=manifest.get('draft_model_animation_assets',[])
    if not isinstance(entries,list) or any(not isinstance(e,dict) for e in entries):raise FormatError('Invalid model-animation draft policy')
    for relative,record in manifest.get('source_files',{}).items():
        parts=Path(relative).parts
        if len(parts)==3 and parts[0]=='SourceModelAnimations' and parts[-1].endswith('.json'):
            for file in expected:
                if Path(file).suffix.casefold()=='.clb' and Path(file).stem.casefold()==parts[1].casefold():
                    if not any(e.get('file')==file and e.get('source_receipt')==relative and e.get('source_receipt_sha256')==record['sha256'] for e in entries):raise FormatError('Model-animation draft provenance is missing')
    for entry in entries:
        gate=manifest.get('release_gate',{})
        if entry.get('file') not in expected or not entry.get('model') or not entry.get('animation') or not gate.get('review_required') or not any(e.get('issue')=='draft_model_animation_requires_skinning_review' and e.get('file')==entry['file'] for e in gate.get('issues',[])):
            raise FormatError('Model-animation skinning findings are missing from the release gate')


def check_animation_drafts(manifest,expected):
    entries=manifest.get('draft_animation_assets',[])
    if not isinstance(entries,list) or any(not isinstance(e,dict) for e in entries):raise FormatError('Invalid animation draft policy')
    for relative,record in manifest.get('source_files',{}).items():
        parts=Path(relative).parts
        if len(parts)==3 and parts[0]=='SourceAnimations' and parts[-1]=='.aow4mod-animation.json':
            for file in expected:
                if Path(file).suffix.casefold()=='.clb' and Path(file).stem.casefold()==parts[1].casefold():
                    if not any(e.get('file')==file and e.get('source_receipt')==relative and e.get('source_receipt_sha256')==record['sha256'] for e in entries):
                        raise FormatError('Animation draft provenance is missing from manifest')
    for entry in entries:
        gate=manifest.get('release_gate',{})
        if entry.get('file') not in expected or not gate.get('review_required') or not any(e.get('issue')=='draft_animation_asset_requires_binding_pose_review' and e.get('file')==entry['file'] for e in gate.get('issues',[])):
            raise FormatError('Animation binding/pose findings are missing from release gate')


def check_map_drafts(manifest,expected):
    entries=manifest.get('draft_map_assets',[])
    if not isinstance(entries,list) or any(not isinstance(e,dict) for e in entries):raise FormatError('Invalid draft map policy')
    for relative,record in manifest.get('source_files',{}).items():
        parts=Path(relative).parts
        if len(parts)==3 and parts[0]=='SourceMaps' and parts[-1]=='.aow4mod-map.json':
            for file in expected:
                if Path(file).suffix.casefold()=='.tam' and Path(file).name.casefold()==parts[1].casefold():
                    if not any(e.get('file')==file and e.get('source_receipt')==relative and e.get('source_receipt_sha256')==record['sha256'] for e in entries):
                        raise FormatError('Draft map provenance is missing from manifest')
    for entry in entries:
        gate=manifest.get('release_gate',{})
        if entry.get('file') not in expected or not gate.get('review_required') or not any(e.get('issue')=='draft_map_asset_requires_playability_review' and e.get('file')==entry['file'] for e in gate.get('issues',[])):
            raise FormatError('Draft map findings are missing from release gate')


def load_release(path,require_workshop=False):
    path=Path(path).resolve();root=path.parent
    manifest=json.loads(path.read_text(encoding='utf8'))
    if manifest.get('format') not in (None,'aow4mod-release/1') or manifest.get('app_id')!=APP_ID:
        raise FormatError('Unsupported release or app ID')
    content=release_path(root,manifest.get('content_directory'),True)
    expected=manifest.get('files')
    if not isinstance(expected,dict) or not expected:raise FormatError('Release has no payload manifest')
    actual={k:v['sha256'] for k,v in inventory(content).items()}
    if actual!=expected:raise FormatError('Release payload changed, has extra files, or is incomplete')
    normalized=dict(manifest);normalized['content_directory']=str(content)
    drafts=manifest.get('draft_mesh_assets',[])
    if not isinstance(drafts,list):raise FormatError('Invalid draft mesh asset policy')
    for draft in drafts:
        if not isinstance(draft,dict) or draft.get('file') not in expected:
            raise FormatError('Draft mesh policy names a missing payload asset')
        gate=manifest.get('release_gate',{})
        if not gate.get('review_required') or not any(i.get('issue')=='draft_mesh_asset_requires_binding_review' and i.get('file')==draft['file'] for i in gate.get('issues',[])):
            raise FormatError('Draft mesh findings are missing from the release gate')
    check_model_drafts(manifest,expected)
    check_material_drafts(manifest,expected)
    check_skin_weight_drafts(manifest,expected)
    check_pose_bake_drafts(manifest,expected)
    check_skeletal_drafts(manifest,expected)
    check_model_animation_drafts(manifest,expected)
    check_animation_drafts(manifest,expected)
    check_map_drafts(manifest,expected)
    from . import source_release
    if source_release.verify(root,manifest,content) is not None:normalized['_skeletal_source_root']=str(root)
    from . import maintenance_release
    maintenance_summary=maintenance_release.verify(root,manifest,content)
    if maintenance_summary is not None:normalized['_maintenance_root']=str(root)
    for name in ('description','change_notes'):
        p=release_path(root,manifest.get(name+'_file'))
        if file_hash(p)!=manifest.get(name+'_sha256'):raise FormatError(name+' changed after build')
        normalized[name+'_file']=str(p)
    if manifest.get('audit_file'):
        report=release_path(root,manifest['audit_file'])
        if file_hash(report)!=manifest.get('audit_sha256'):raise FormatError('Audit report changed after build')
        if json.loads(report.read_text(encoding='utf8')).get('summary')!=manifest.get('audit_summary'):
            raise FormatError('Manifest audit summary mismatch')
    if manifest.get('environment_file'):
        report=release_path(root,manifest['environment_file'])
        if file_hash(report)!=manifest.get('environment_sha256'):raise FormatError('Environment report changed after build')
        summary=json.loads(report.read_text(encoding='utf8')).get('summary')
        if not isinstance(summary,dict) or summary!=manifest.get('environment_summary'):
            raise FormatError('Manifest environment summary mismatch')
        if summary.get('review_required') and not manifest.get('release_gate',{}).get('review_required'):
            raise FormatError('Environment findings are missing from the release gate')
    native_requested=manifest.get('native_validation_requested',False)
    if type(native_requested) is not bool:raise FormatError('Invalid native release policy')
    if native_requested and not manifest.get('native_validation_file'):
        raise FormatError('Requested native validation evidence is missing')
    if manifest.get('native_validation_file'):
        from . import native_release
        report=release_path(root,manifest['native_validation_file'])
        if file_hash(report)!=manifest.get('native_validation_sha256'):raise FormatError('Native validation report changed after build')
        protocol=manifest.get('native_validation_protocol',1)
        if type(protocol) is not int or protocol not in (1,2,3,4,5,6,7,8):raise FormatError('Invalid native release protocol')
        summary=native_release.validate(json.loads(report.read_text(encoding='utf-8')),content,root,protocol)
        if summary!=manifest.get('native_validation_summary'):raise FormatError('Manifest native validation summary mismatch')
        if summary['review_required'] and (not manifest.get('release_gate',{}).get('review_required') or
            not any(i.get('issue')=='native_validation_review_required' for i in manifest.get('release_gate',{}).get('issues',[]))):
            raise FormatError('Native findings are missing from the release gate')
        normalized['_native_validation_path']=str(report)
        normalized['_native_evidence_root']=str(root)
    workshop=manifest.get('workshop',{}) if manifest.get('format') else manifest
    if not isinstance(workshop,dict):raise FormatError('Invalid Workshop configuration')
    required=('published_file_id','expected_owner','expected_title')
    configured=all(workshop.get(k) for k in required)
    if require_workshop and not configured:
        raise FormatError('Configure an existing Workshop item ID, expected owner and exact existing title before online operations')
    if configured:
        normalized['published_file_id']=identity_integer(workshop['published_file_id'],'Workshop item ID')
        normalized['expected_owner']=identity_integer(workshop['expected_owner'],'Workshop owner ID')
        if not isinstance(workshop['expected_title'],str):raise FormatError('Invalid expected title')
        normalized['expected_title']=workshop['expected_title']
    normalized['_manifest_sha256']=file_hash(path)
    normalized['_manifest_path']=str(path)
    normalized['_workshop_configured']=configured
    normalized['_review_required']=bool(manifest.get('release_gate',{}).get('review_required',bool(manifest.get('format'))))
    return normalized,content


def remote_matches(manifest,details):
    if int(details.get('consumer_app_id',0))!=APP_ID or int(details.get('creator',0))!=manifest['expected_owner'] or str(details.get('publishedfileid'))!=str(manifest['published_file_id']):
        raise FormatError('Remote app/item/author identity differs from the release')
    if details.get('title')!=manifest['expected_title']:
        raise FormatError('Remote Workshop title differs from the expected existing item')
    expected=Path(manifest['description_file']).read_text(encoding='utf8').rstrip().replace('\r\n','\n')
    return {'identity_matches':True,'description_matches':str(details.get('description','')).replace('\r\n','\n')==expected,
            'published_file_id':manifest['published_file_id'],'title':details['title'],
            'time_updated':details.get('time_updated'),'visibility':details.get('visibility')}


def backend(factory=None,fetcher=None):
    if factory is None or fetcher is None:
        from .steam_transport import Steam,public_details
        factory=factory or Steam;fetcher=fetcher or public_details
    return factory,fetcher


def stage_release(manifest,content,root):
    """Freeze every transmitted file, including description, notes and new preview."""
    frozen=dict(manifest);stage=Path(root).resolve()/'.upload-attempts'/uuid.uuid4().hex/'content'
    with copy_directory(content,stage) as (copied,before):
        if {k:v['sha256'] for k,v in before.items()}!=manifest['files']:raise FormatError('Payload changed during upload staging')
    for key in ('description','change_notes'):
        raw=Path(manifest[key+'_file']).read_bytes()
        if sha(raw)!=manifest[key+'_sha256']:raise FormatError('Publication text input changed')
        target=stage.parent/(key+'.txt');target.write_bytes(raw);frozen[key+'_file']=str(target)
    if manifest.get('_new_item_metadata'):
        initial=dict(manifest['_new_item_metadata']);source=Path(initial['preview']);raw=source.read_bytes()
        if sha(raw)!=initial['preview_sha256']:raise FormatError('Preview changed before upload staging')
        target=stage.parent/('preview'+source.suffix.lower());target.write_bytes(raw)
        initial['preview']=str(target);frozen['_new_item_metadata']=initial
    frozen['_staging_directory']=str(stage);frozen['content_directory']=str(stage)
    from . import source_release
    source_release.stage(frozen,stage.parent)
    if manifest.get('_native_validation_path'):
        from . import native_release
        raw=Path(manifest['_native_validation_path']).read_bytes()
        if sha(raw)!=manifest['native_validation_sha256']:raise FormatError('Native validation changed during upload staging')
        target=stage.parent/'native-validation.json';target.write_bytes(raw)
        proof_source=Path(manifest['_native_evidence_root'])/'native-evidence'
        with copy_directory(proof_source,stage.parent/'native-evidence'):pass
        frozen['_native_validation_path']=str(target);frozen['_native_evidence_root']=str(stage.parent)
        native_release.validate(json.loads(raw.decode('utf-8')),stage,stage.parent,manifest.get('native_validation_protocol',1))
    if manifest.get('_maintenance_root'):
        from . import maintenance_release
        maintenance_release.freeze(frozen,stage.parent)
    raw=Path(manifest['_manifest_path']).read_bytes()
    if sha(raw)!=manifest['_manifest_sha256']:raise FormatError('Source manifest changed during staging')
    source_record=stage.parent/'source-manifest.json';source_record.write_bytes(raw)
    frozen['_source_manifest_snapshot']=str(source_record)
    return frozen,stage


def check_snapshot_policy(manifest):
    """Retain the reviewed source policy even if a persisted attempt is edited."""
    source=manifest.get('_source_manifest_snapshot');staging=manifest.get('_staging_directory')
    if not isinstance(source,str) or not isinstance(staging,str):raise FormatError('Frozen source manifest witness is missing')
    root=Path(staging).resolve().parent;path=Path(source).resolve()
    if path!=root/'source-manifest.json' or file_hash(path)!=manifest.get('_manifest_sha256'):
        raise FormatError('Frozen source manifest witness changed')
    original=json.loads(path.read_text(encoding='utf-8'))
    for key in ('files','source_files','description_sha256','change_notes_sha256','release_gate','draft_material_assets','draft_animation_assets','draft_model_animation_assets','draft_skin_weight_assets','draft_pose_bake_assets','draft_skeletal_assets',
                'skeletal_source_validation_requested','skeletal_source_validation_file','skeletal_source_validation_sha256','skeletal_source_validation_summary',
                'native_validation_requested','native_validation_protocol','native_validation_file','native_validation_sha256','native_validation_summary',
                'maintenance_validation_requested','maintenance_validation_file','maintenance_validation_sha256','maintenance_validation_summary',
                'maintenance_context_requested'):
        if original.get(key)!=manifest.get(key):raise FormatError('Frozen policy differs from source manifest: '+key)
    if original.get('native_validation_requested') or original.get('native_validation_file'):
        if (not isinstance(manifest.get('_native_validation_path'),str) or
                Path(manifest['_native_validation_path']).resolve()!=root/'native-validation.json' or
                not isinstance(manifest.get('_native_evidence_root'),str) or Path(manifest['_native_evidence_root']).resolve()!=root):
            raise FormatError('Frozen native validation witnesses are missing or outside their snapshot')
    if original.get('skeletal_source_validation_requested'):
        if not isinstance(manifest.get('_skeletal_source_root'),str) or Path(manifest['_skeletal_source_root']).resolve()!=root:raise FormatError('Frozen skeletal source witness root differs')
    if original.get('maintenance_validation_requested'):
        if not isinstance(manifest.get('_maintenance_root'),str) or Path(manifest['_maintenance_root']).resolve()!=root:
            raise FormatError('Frozen maintenance witnesses are missing or outside their snapshot')


def check_staged(manifest):
    actual={k:v['sha256'] for k,v in inventory(manifest['content_directory']).items()}
    if actual!=manifest['files']:raise FormatError('Persistent upload snapshot changed')
    check_model_drafts(manifest,manifest['files'])
    check_material_drafts(manifest,manifest['files'])
    if manifest.get('maintenance_validation_requested'):
        from . import maintenance_release
        maintenance_release.verify(manifest['_maintenance_root'],manifest,manifest['content_directory'])
    check_skin_weight_drafts(manifest,manifest['files'])
    check_pose_bake_drafts(manifest,manifest['files'])
    check_skeletal_drafts(manifest,manifest['files'])
    from . import source_release
    if source_release.required(manifest):
        if not isinstance(manifest.get('_skeletal_source_root'),str):raise FormatError('Frozen skeletal source witnesses missing')
        source_release.verify(manifest['_skeletal_source_root'],manifest,manifest['content_directory'])
    check_model_animation_drafts(manifest,manifest['files'])
    check_animation_drafts(manifest,manifest['files'])
    check_map_drafts(manifest,manifest['files'])
    check_snapshot_policy(manifest)
    for key in ('description','change_notes'):
        if file_hash(manifest[key+'_file'])!=manifest[key+'_sha256']:raise FormatError('Persistent upload text changed')
    initial=manifest.get('_new_item_metadata')
    if initial and file_hash(initial['preview'])!=initial['preview_sha256']:raise FormatError('Persistent preview changed')
    if manifest.get('_native_validation_path'):
        from . import native_release
        path=Path(manifest['_native_validation_path'])
        if file_hash(path)!=manifest['native_validation_sha256']:raise FormatError('Persistent native validation report changed')
        observed=native_release.validate(json.loads(path.read_text(encoding='utf-8')),manifest['content_directory'],manifest['_native_evidence_root'],manifest.get('native_validation_protocol',1))
        if observed!=manifest['native_validation_summary']:raise FormatError('Persistent native validation summary changed')
        if observed['review_required'] and (not manifest.get('release_gate',{}).get('review_required') or not any(
                r.get('issue')=='native_validation_review_required' for r in manifest.get('release_gate',{}).get('issues',[]))):
            raise FormatError('Native findings are missing from the frozen release gate')


def check(path,online=False,game=None,proxy=None,factory=None,fetcher=None):
    manifest,content=load_release(path,online)
    result={'payload_verified':True,'files':len(manifest['files']),'version':manifest.get('version'),
            'workshop_configured':manifest['_workshop_configured'],'online_checked':False,
            'review_required':manifest['_review_required'],
            'native_validation_summary':manifest.get('native_validation_summary'),
            'maintenance_validation_summary':manifest.get('maintenance_validation_summary'),
            'existing_receipt':(Path(path).resolve().parent/'upload-receipt.json').is_file()}
    if online:
        factory,fetcher=backend(factory,fetcher)
        details=fetcher(manifest['published_file_id'],proxy);remote=remote_matches(manifest,details)
        client=factory(game)
        try:
            if client.steam_id()!=manifest['expected_owner']:raise FormatError('Signed-in Steam user is not the expected owner')
            result.update(online_checked=True,account_matches=True,remote=remote,
                          interfaces=getattr(client,'interface_versions',{}))
        finally:client.close()
    return result


def verify(path,download=False,game=None,proxy=None,timeout=90,factory=None,fetcher=None):
    manifest,_=load_release(path,True);factory,fetcher=backend(factory,fetcher)
    remote=remote_matches(manifest,fetcher(manifest['published_file_id'],proxy))
    result={'manifest_sha256':manifest['_manifest_sha256'],'remote':remote,'content_verified':False,
            'verified_utc':datetime.now(timezone.utc).isoformat()}
    if download:
        client=factory(game)
        try:
            if client.steam_id()!=manifest['expected_owner']:raise FormatError('Signed-in account does not match the expected owner')
            content=client.verify_download(manifest,timeout)
            result['download']=content;result['content_verified']=bool(content.get('content_matches'))
        finally:client.close()
    result['desired_state_verified']=remote['description_matches'] and result['content_verified']
    return result


def publish_existing(path,submit=False,game=None,proxy=None,timeout=240,factory=None,fetcher=None,allow_review_required=False):
    if not submit:return check(path,True,game,proxy,factory,fetcher)
    manifest,content=load_release(path,True);root=Path(path).resolve().parent
    if manifest['_review_required'] and not allow_review_required:
        raise FormatError('Release checks require review; resolve findings or explicitly allow the reviewed incomplete scope')
    manifest['_review_override']=manifest['_review_required'] and allow_review_required
    receipt_path=root/'upload-receipt.json'
    if receipt_path.exists():raise FormatError('A prior submission receipt exists; reconcile it instead of resubmitting')
    factory,fetcher=backend(factory,fetcher)
    remote_matches(manifest,fetcher(manifest['published_file_id'],proxy))
    client=factory(game)
    try:
        if client.steam_id()!=manifest['expected_owner']:raise FormatError('Signed-in Steam user is not the expected owner')
        # Keep a persistent upload snapshot even after a timeout/unknown result.
        manifest,stage=stage_release(manifest,content,root)
        if file_hash(path)!=manifest['_manifest_sha256']:raise FormatError('Manifest changed before publication')
        check_staged(manifest)
        result=client.publish(manifest,stage,receipt_path,timeout)
        return {'submission':result,'verification_required':True,
                'next_action':'workshop-verify --download, then reconcile; never assume an unverified callback is a gameplay test'}
    finally:client.close()


def reconcile(path,game=None,proxy=None,timeout=90,factory=None,fetcher=None):
    manifest,_=load_release(path,True);receipt_path=Path(path).resolve().parent/'upload-receipt.json'
    if not receipt_path.is_file():raise FormatError('No prior submission receipt to reconcile')
    receipt=json.loads(receipt_path.read_text(encoding='utf8'))
    if receipt.get('app_id')!=APP_ID or receipt.get('published_file_id')!=manifest['published_file_id']:
        raise FormatError('Receipt identity mismatch')
    if receipt.get('content_files')!=manifest['files']:raise FormatError('Receipt belongs to different payload bytes')
    if receipt.get('manifest_sha256') and receipt['manifest_sha256']!=manifest['_manifest_sha256']:
        raise FormatError('Release manifest changed after submission')
    result=verify(path,True,game,proxy,timeout,factory,fetcher)
    record={'format':'aow4mod-reconciliation/1','submission_status':receipt.get('status'),
            'resubmitted':False,**result}
    name='reconciliation-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')+'.json'
    output=receipt_path.parent/name
    with output.open('x',encoding='utf8') as f:json.dump(record,f,ensure_ascii=False,indent=2)
    return {**record,'record':str(output)}


def legacy_main(argv=None):
    import argparse
    parser=argparse.ArgumentParser(description='Compatibility wrapper; use aow4mod workshop commands for new releases')
    parser.add_argument('manifest',type=Path);parser.add_argument('--publish',action='store_true')
    parser.add_argument('--proxy',default=None);parser.add_argument('--timeout',type=int,default=240)
    parser.add_argument('--game',type=Path)
    args=parser.parse_args(argv)
    result=publish_existing(args.manifest,args.publish,args.game,args.proxy,args.timeout)
    print(json.dumps(result,ensure_ascii=False,indent=2));return 0
