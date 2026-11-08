"""First Workshop publication, explicit recovery, and authenticated verification."""
from datetime import datetime,timezone
import json
from pathlib import Path
import uuid

from . import publication
from .core import FormatError,mod_file,read_acp,sha
from .filesystem import file_hash
from .synchronization import save_record

FORMAT='aow4mod-workshop-create-plan/1'
VISIBILITY={'public':0,'friends':1,'private':2,'unlisted':3}


def make_plan(manifest,owner,preview,title=None,tags=None,visibility='private',allow_review=False,plan_id=None):
    release,content=publication.load_release(manifest,False)
    if release['_workshop_configured'] or release.get('workshop',{}).get('published_file_id'):
        raise FormatError('Release already identifies a Workshop item; use the existing-item updater')
    if release['_review_required'] and not allow_review:raise FormatError('Release findings require review before planning first publication')
    meta,_=read_acp(mod_file(content));title=meta['name'] if title is None else title
    if not isinstance(title,str) or not title.strip() or '\0' in title or len(title.encode('utf8'))>128:
        raise FormatError('Workshop title must be nonempty UTF-8 text of at most 128 bytes, without NUL')
    tags=(meta['tags'] or ['Mod']) if tags is None else tags
    if not isinstance(tags,list) or not tags or any(not isinstance(t,str) or not t or len(t.encode('utf8'))>255 or any(not 32<=ord(c)<127 or c==',' for c in t) for t in tags) or len(tags)!=len(set(tags)):
        raise FormatError('Use unique nonempty printable ASCII Workshop tags without commas, at most 255 bytes each')
    if len(','.join(tags).encode('utf8'))>1024:raise FormatError('Combined tags exceed the verifiable metadata limit')
    if visibility not in VISIBILITY:raise FormatError('Unsupported initial visibility')
    if not meta.get('package_id'):raise FormatError('New Workshop publication requires a package UUID')
    for key in ('description_file','change_notes_file'):
        text=Path(release[key]).read_text(encoding='utf8').rstrip()
        if '\0' in text or len(text.encode('utf8'))>=8000:raise FormatError('Publication description/notes exceed the supported UTF-8 bound')
    preview=Path(preview).resolve();raw=preview.read_bytes()
    signatures={'.png':raw.startswith(b'\x89PNG\r\n\x1a\n'),'.jpg':raw.startswith(b'\xff\xd8\xff'),
                '.jpeg':raw.startswith(b'\xff\xd8\xff'),'.gif':raw.startswith((b'GIF87a',b'GIF89a'))}
    if not signatures.get(preview.suffix.lower()) or not 0<len(raw)<=10*1024*1024:
        raise FormatError('Preview must be a recognizable PNG/JPEG/GIF no larger than the toolkit limit of 10 MiB')
    return {'format':FORMAT,'plan_id':str(uuid.UUID(plan_id)) if plan_id else str(uuid.uuid4()),
            'manifest':str(Path(manifest).resolve()),'manifest_sha256':release['_manifest_sha256'],
            'package_id':meta['package_id'],'expected_owner':publication.identity_integer(owner,'Steam owner'),
            'title':title,'tags':tags,'visibility':VISIBILITY[visibility],'visibility_name':visibility,
            'preview':str(preview),'preview_sha256':sha(raw),'preview_size':len(raw),
            'allow_review_required':bool(allow_review),'release_review_required':release['_review_required'],
            'note':'First publication plan only. Creation and upload require --submit; private visibility is the default. Never retry an unknown creation automatically.'}


def load_plan(path,verify_inputs=True):
    path=Path(path).resolve();plan=json.loads(path.read_text(encoding='utf8'))
    if plan.get('format')!=FORMAT:raise FormatError('Unsupported Workshop creation plan')
    if verify_inputs:
        current=make_plan(plan['manifest'],plan['expected_owner'],plan['preview'],plan['title'],plan['tags'],
                          plan['visibility_name'],plan['allow_review_required'],plan['plan_id'])
        if current!=plan:raise FormatError('Creation plan or its release/preview inputs changed')
    return plan,file_hash(path)


def paths(plan_path):
    path=Path(plan_path).resolve()
    return path.with_name(path.stem+'.creation-receipt.json'),path.with_name(path.stem+'.upload-receipt.json')


def client_for(game,factory):
    if factory:return factory(game)
    from .steam_transport import Steam
    return Steam(game)


def check_author(details,plan,item):
    if int(details.get('publishedfileid',0))!=item or int(details.get('consumer_app_id',0))!=publication.APP_ID or int(details.get('creator',0))!=plan['expected_owner']:
        raise FormatError('Authenticated Workshop app/item/author identity mismatch')


def read_receipt(path,plan,plan_hash):
    receipt=json.loads(Path(path).read_text(encoding='utf8'))
    if receipt.get('format')!='aow4mod-creation-receipt/1' or receipt.get('app_id')!=publication.APP_ID or receipt.get('plan_sha256')!=plan_hash or receipt.get('expected_owner')!=plan['expected_owner']:
        raise FormatError('Creation receipt identity or plan hash mismatch')
    return receipt


def staged_receipt(receipt,plan):
    path=Path(receipt['staged_manifest'])
    if file_hash(path)!=receipt['staged_manifest_sha256']:raise FormatError('Prepared upload metadata changed')
    manifest=json.loads(path.read_text(encoding='utf8'));publication.check_staged(manifest)
    source=Path(manifest['_source_manifest_snapshot'])
    if file_hash(source)!=plan['manifest_sha256'] or manifest.get('_manifest_sha256')!=plan['manifest_sha256']:
        raise FormatError('Staged source manifest differs from the reviewed release')
    original=json.loads(source.read_text(encoding='utf8'))
    for key in ('files','description_sha256','change_notes_sha256'):
        if manifest.get(key)!=original.get(key):raise FormatError('Staged payload/text does not match the original manifest')
    initial=manifest.get('_new_item_metadata',{})
    for key in ('title','tags','visibility','preview_sha256'):
        if initial.get(key)!=plan[key]:raise FormatError('Staged first-publication metadata differs from its plan')
    return manifest


def execute(plan_path,submit=False,resume=False,agreement_accepted=False,game=None,timeout=240,factory=None):
    plan,plan_hash=load_plan(plan_path,not resume);creation_path,upload_path=paths(plan_path)
    if not submit:
        return {'prepared':True,'submitted':False,'title':plan['title'],'visibility':plan['visibility_name'],
                'expected_owner':plan['expected_owner'],'existing_creation_receipt':creation_path.exists(),
                'existing_upload_receipt':upload_path.exists(),'release_review_required':plan['release_review_required']}
    if upload_path.exists():raise FormatError('An upload receipt exists; verify/reconcile before considering another submission')
    if creation_path.exists() and not resume:raise FormatError('A creation receipt exists; resume a known item or explicitly recover its ID, never create again')
    if resume and not creation_path.is_file():raise FormatError('No creation receipt to resume')
    client=client_for(game,factory)
    try:
        if client.steam_id()!=plan['expected_owner']:raise FormatError('Signed-in Steam user is not the planned author')
        if resume:
            receipt=read_receipt(creation_path,plan,plan_hash)
            item=receipt.get('published_file_id')
            if not item or receipt.get('status') not in ('created','created_recovered'):
                raise FormatError('Creation outcome has no confirmed ID; list owned items and recover an explicit ID first')
            check_author(client.item_details(item,min(timeout,30)),plan,item)
            frozen=staged_receipt(receipt,plan)
        else:
            manifest,content=publication.load_release(plan['manifest'],False)
            manifest['_new_item_metadata']={k:plan[k] for k in ('title','tags','visibility','preview','preview_sha256')}
            manifest['_review_override']=plan['allow_review_required'] and plan['release_review_required']
            frozen,stage=publication.stage_release(manifest,content,Path(plan_path).resolve().parent)
            staged_path=stage.parent/'staged-manifest.json';save_record(staged_path,frozen)
            context={'plan_id':plan['plan_id'],'plan_sha256':plan_hash,'expected_owner':plan['expected_owner'],
                     'manifest_sha256':plan['manifest_sha256'],'staged_manifest':str(staged_path),
                     'staged_manifest_sha256':file_hash(staged_path)}
            receipt=client.create_item(context,creation_path,min(timeout,90))
            receipt=read_receipt(creation_path,plan,plan_hash)
            item=receipt.get('published_file_id')
            if receipt.get('status')!='created' or not item:raise FormatError('Creation was not confirmed')
        configuration={'published_file_id':item,'expected_owner':str(plan['expected_owner']),'expected_title':plan['title']}
        if receipt.get('needs_workshop_agreement') and not agreement_accepted:
            return {'item_created':True,'published_file_id':item,'upload_submitted':False,'needs_workshop_agreement':True,
                    'receipt':str(creation_path),'next_action':'The user must accept the Workshop agreement, then explicitly resume with --agreement-accepted.'}
        if agreement_accepted:
            receipt['agreement_acknowledged_by_user']=True
            save_record(creation_path,receipt)
        frozen.update(published_file_id=item,expected_owner=plan['expected_owner'],expected_title=plan['title'])
        publication.check_staged(frozen)
        if file_hash(plan_path)!=plan_hash:raise FormatError('Creation plan changed before upload')
        result=client.publish(frozen,Path(frozen['content_directory']),upload_path,timeout)
        return {'item_created':True,'submission':result,'workshop_configuration':configuration,
                'creation_receipt':str(creation_path),'upload_receipt':str(upload_path),'verification_required':True}
    finally:client.close()


def recover(plan_path,item_id,game=None,timeout=30,factory=None):
    """Bind an explicitly selected, newly created owned item; never creates/uploads."""
    plan,plan_hash=load_plan(plan_path,False);creation_path,upload_path=paths(plan_path)
    if upload_path.exists():raise FormatError('Upload already attempted; use verification instead of creation recovery')
    receipt=read_receipt(creation_path,plan,plan_hash)
    if receipt.get('published_file_id'):raise FormatError('Creation already has a known item ID; resume it')
    staged_receipt(receipt,plan);item=publication.identity_integer(item_id,'Recovered Workshop ID')
    client=client_for(game,factory)
    try:
        if client.steam_id()!=plan['expected_owner']:raise FormatError('Signed-in author differs')
        details=client.item_details(item,timeout);check_author(details,plan,item)
        started=datetime.fromisoformat(receipt['created_utc']).timestamp()
        if int(details.get('time_created',0))<started-300:raise FormatError('Selected item predates this creation attempt; refusing to adopt an older Mod')
        receipt.update(published_file_id=item,status='created_recovered',recovered_utc=datetime.now(timezone.utc).isoformat(),
                       recovered_by_explicit_item_id=True)
        save_record(creation_path,receipt)
        return {'published_file_id':item,'recovered':True,'created_again':False,'uploaded':False,'receipt':str(creation_path)}
    finally:client.close()


def verify(plan_path,download=False,game=None,timeout=90,factory=None):
    plan,plan_hash=load_plan(plan_path,False);creation_path,upload_path=paths(plan_path)
    receipt=read_receipt(creation_path,plan,plan_hash);item=receipt.get('published_file_id')
    if not item:raise FormatError('No known Workshop ID to verify')
    frozen=staged_receipt(receipt,plan);client=client_for(game,factory)
    try:
        if client.steam_id()!=plan['expected_owner']:raise FormatError('Signed-in author differs')
        details=client.item_details(item,min(timeout,30));check_author(details,plan,item)
        description=Path(frozen['description_file']).read_text(encoding='utf8').rstrip().replace('\r\n','\n')
        checks={'title':details.get('title')==plan['title'],
                'description':details.get('description','').replace('\r\n','\n')==description,
                'visibility':details.get('visibility')==plan['visibility'],
                'tags':not details.get('tags_truncated',True) and set(details.get('tags',[]))==set(plan['tags']),
                'preview_present':bool(details.get('preview_present')),'not_banned':not details.get('banned',False),
                'agreement':not receipt.get('needs_workshop_agreement') or bool(receipt.get('agreement_acknowledged_by_user'))}
        if upload_path.exists():
            upload=json.loads(upload_path.read_text(encoding='utf8'))
            if upload.get('published_file_id')!=item or upload.get('content_files')!=frozen['files']:
                raise FormatError('Upload receipt does not match the created item and frozen payload')
            if upload.get('needs_workshop_agreement'):checks['agreement']=False
        content={'content_matches':False,'reason':'download_not_requested'}
        if download:
            frozen['published_file_id']=item;content=client.verify_download(frozen,timeout)
        result={'format':'aow4mod-creation-verification/1','published_file_id':item,'metadata_checks':checks,
                'download':content,'desired_state_verified':all(checks.values()) and bool(content.get('content_matches')),
                'preview_pixel_identity_verified':False,'resubmitted':False,'checked_utc':datetime.now(timezone.utc).isoformat()}
        output=creation_path.with_name(creation_path.stem+'-verification-'+uuid.uuid4().hex[:8]+'.json')
        with output.open('x',encoding='utf8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
        return {**result,'record':str(output)}
    finally:client.close()
