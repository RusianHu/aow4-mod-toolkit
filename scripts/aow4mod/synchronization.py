"""Reviewable local release synchronization with full backups and hash-bound rollback."""
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import shutil
import tempfile

from .core import FormatError,mod_file,read_acp,sha
from .filesystem import inventory,file_hash,new_directory,outside,reject_reparse,save_record
from .publication import load_release

FORMAT='aow4mod-sync-plan/1'
STATE='.aow4mod-sync-state.json'
RUNTIME_EXTENSIONS={'.acp','.rpk','.clb','.mo','.tam','.cam','.srm','.env','.bank','.ogg','.ttf'}


def relative(root,value):
    if not isinstance(value,str) or not value or ':' in value or '\0' in value:raise FormatError('Invalid managed file path')
    value=value.replace('\\','/')
    if value.startswith('/') or any(x in ('','..','.') for x in value.split('/')):raise FormatError('Managed file path escapes its Mod')
    return root.joinpath(*value.split('/'))


def current_inventory(path):
    path=Path(path)
    if not path.exists():return None
    reject_reparse(path)
    return inventory(path)


def equivalent(a,b):
    if a is None or b is None:return a is b
    if os.name!='nt':return a==b
    left={k.casefold():v for k,v in a.items()};right={k.casefold():v for k,v in b.items()}
    return len(left)==len(a) and len(right)==len(b) and left==right


def runtime_file(name):
    path=Path(name)
    return path.suffix.lower() in RUNTIME_EXTENSIONS or name.replace('\\','/').casefold().startswith('textures/')


def guard_destination(destination,manifest,content,game=None):
    raw=Path(destination).absolute()
    if raw.exists():reject_reparse(raw)
    protected=[Path(manifest).resolve().parent]
    if game:protected.extend([Path(game).resolve(),Path(game).resolve().parent.parent/'workshop'])
    dest=outside(raw,protected)
    if dest in Path(content).resolve().parents:raise FormatError('Release content cannot be inside the destination tree')
    return dest


def make_plan(manifest,destination,backup,game=None,extra_runtime='block',allow_review=False):
    if extra_runtime not in ('block','keep','prune'):raise FormatError('Unknown extra-runtime policy')
    release,content=load_release(manifest,False);manifest=Path(manifest).resolve()
    if release['_review_required'] and not allow_review:raise FormatError('Release requires review; use --allow-review-required only after evaluating its findings')
    dest=guard_destination(destination,manifest,content,game);backup=outside(backup,[dest,content,manifest.parent]+([Path(game)] if game else []))
    if backup in dest.parents:raise FormatError('Backup cannot contain the destination')
    if backup.exists():raise FormatError('Backup directory already exists; select a fresh location')
    meta,_=read_acp(mod_file(content));package=meta.get('package_id')
    if not package:raise FormatError('Release has no package UUID; local synchronization requires an explicit identity')
    before=current_inventory(dest)
    if before:
        existing,_=read_acp(mod_file(dest))
        if existing.get('package_id')!=package:raise FormatError('Destination belongs to a different Mod package')
    managed={}
    if before and STATE in before:
        state=json.loads((dest/STATE).read_text(encoding='utf8'))
        if state.get('format')!='aow4mod-sync-state/1' or state.get('package_id')!=package or not isinstance(state.get('managed_files'),dict):
            raise FormatError('Invalid previous synchronization ownership record')
        managed=state['managed_files']
        for key in managed:relative(dest,key)
    for key in release['files']:relative(dest,key)
    if STATE.casefold() in {k.casefold() for k in release['files']}:raise FormatError('Release must not contain a local synchronization state file')
    before=before if before is not None else None
    known={key.casefold() for key in [*managed,*release['files'],STATE]}
    extras=sorted(k for k in (before or {}) if k.casefold() not in known and runtime_file(k))
    if extras and extra_runtime=='block':raise FormatError('Unmanaged runtime files need an explicit keep/prune policy: '+json.dumps(extras[:15]))
    deletions=set(managed)-set(release['files'])
    if extra_runtime=='prune':deletions.update(extras)
    # Case-only filename changes are removed before writing the new spelling.
    incoming_by_fold={k.casefold():k for k in release['files']}
    for key in before or {}:
        if key.casefold() in incoming_by_fold and incoming_by_fold[key.casefold()]!=key:deletions.add(key)
    deletions={key for key in deletions if key in (before or {})}
    state={'format':'aow4mod-sync-state/1','package_id':package,'version':release.get('version'),
           'manifest_sha256':release['_manifest_sha256'],'managed_files':release['files']}
    state_text=json.dumps(state,ensure_ascii=False,indent=2)+'\n'
    after={k:v for k,v in (before or {}).items() if k not in deletions}
    for key,h in release['files'].items():after[key]={'sha256':h,'size':relative(content,key).stat().st_size}
    after[STATE]={'sha256':sha(state_text.encode('utf8')),'size':len(state_text.encode('utf8'))}
    changed=[key for key,info in after.items() if (before or {}).get(key)!=info]
    return {'format':FORMAT,'manifest':str(manifest),'manifest_sha256':release['_manifest_sha256'],
            'content':str(content),'destination':str(dest),'backup':str(backup),'package_id':package,
            'game':str(Path(game).resolve()) if game else None,'extra_runtime_policy':extra_runtime,
            'allow_review_required':bool(allow_review),'before':before,'after':after,'state_utf8':state_text,
            'files_written':sorted(changed),'files_removed':sorted(deletions),'extra_runtime_files':extras,
            'preserved_files':sorted(k for k in (before or {}) if k not in deletions and k not in release['files'] and k!=STATE),
            'release_review_required':release['_review_required'],
            'note':'Plan only; apply replaces the local directory after a verified full backup. No Workshop or launcher changes.'}


def swap(destination,source,before,after,before_swap=None):
    """Use a same-parent staging directory; restore the old tree on swap failure."""
    dest=Path(destination).resolve();dest.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.aow4mod-swap-',dir=dest.parent) as temporary:
        temporary=Path(temporary).resolve()
        if temporary.parent!=dest.parent or not temporary.name.startswith('.aow4mod-swap-'):
            raise FormatError('Unexpected swap staging location')
        new=temporary/'new';old=temporary/'old'
        if source is not None:
            if not equivalent(inventory(source),after):raise FormatError('Replacement source changed')
            shutil.copytree(source,new)
            if not equivalent(inventory(new),after):raise FormatError('Staged replacement differs from its manifest')
        if not equivalent(current_inventory(dest),before):raise FormatError('Destination changed before synchronization')
        if before_swap:before_swap()
        moved_old=False;installed=False
        try:
            # All rename targets are resolved siblings or children of the checked staging directory.
            if before is not None:os.rename(dest,old);moved_old=True
            if source is not None:os.rename(new,dest);installed=True
            if not equivalent(current_inventory(dest),after):raise FormatError('Installed tree verification failed')
        except BaseException:
            if installed and equivalent(current_inventory(dest),after):os.rename(dest,temporary/'failed-new')
            if moved_old and not dest.exists():os.rename(old,dest)
            raise


def apply_plan(plan):
    if not isinstance(plan,dict) or plan.get('format')!=FORMAT:raise FormatError('Unsupported synchronization plan')
    current=make_plan(plan['manifest'],plan['destination'],plan['backup'],plan.get('game'),
                      plan['extra_runtime_policy'],plan['allow_review_required'])
    if current!=plan:raise FormatError('Release, local files or synchronization plan changed; create a new plan')
    dest=Path(plan['destination']);backup=Path(plan['backup']);content=Path(plan['content'])
    record={'format':'aow4mod-sync-receipt/1','status':'prepared','created_utc':datetime.now(timezone.utc).isoformat(),'plan':plan}
    # Commit the backup before touching the destination, including when storage volumes differ.
    with new_directory(backup,[dest,content]) as payload:
        original=payload/'original';original.mkdir()
        if plan['before'] is not None:
            shutil.copytree(dest,original,dirs_exist_ok=True)
            if not equivalent(inventory(original),plan['before']):raise FormatError('Backup does not match the original Mod')
        save_record(payload/'receipt.json',record)
    receipt=backup/'receipt.json'
    try:
        with tempfile.TemporaryDirectory(prefix='aow4mod-merged-',dir=backup) as temporary:
            merged=Path(temporary)/'mod';merged.mkdir()
            if plan['before'] is not None:shutil.copytree(backup/'original',merged,dirs_exist_ok=True)
            for key in plan['files_removed']:relative(merged,key).unlink()
            release,_=load_release(plan['manifest'],False)
            if release['_manifest_sha256']!=plan['manifest_sha256']:raise FormatError('Release changed after backup')
            for key in release['files']:
                target=relative(merged,key);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(relative(content,key),target)
            (merged/STATE).write_bytes(plan['state_utf8'].encode('utf8'))
            if not equivalent(inventory(merged),plan['after']):raise FormatError('Merged tree does not match the synchronization plan')
            def starting():record['status']='applying';save_record(receipt,record)
            swap(dest,merged,plan['before'],plan['after'],starting)
        record['status']='complete';record['completed_utc']=datetime.now(timezone.utc).isoformat();save_record(receipt,record)
    except BaseException as exc:
        record['status']='attention_required';record['error']=str(exc)
        try:save_record(receipt,record)
        except OSError:pass
        raise
    return {'destination':str(dest),'backup':str(backup),'receipt':str(receipt),'status':'complete',
            'files_written':len(plan['files_written']),'files_removed':len(plan['files_removed']),
            'source_files_preserved':len(plan['preserved_files']),'workshop_submitted':False}


def status(receipt):
    path=Path(receipt).resolve();record=json.loads(path.read_text(encoding='utf8'))
    if record.get('format')!='aow4mod-sync-receipt/1':raise FormatError('Unsupported sync receipt')
    plan=record['plan'];observed=current_inventory(plan['destination'])
    state='installed' if equivalent(observed,plan['after']) else 'original' if equivalent(observed,plan['before']) else 'diverged'
    return {'receipt':str(path),'recorded_status':record['status'],'observed_state':state,'destination':plan['destination']}


def rollback(receipt):
    path=Path(receipt).resolve();record=json.loads(path.read_text(encoding='utf8'))
    observed=status(path)
    if observed['observed_state']!='installed':raise FormatError('Current Mod no longer matches the installed result; refusing rollback')
    plan=record['plan'];backup=Path(plan['backup']).resolve();dest=guard_destination(plan['destination'],plan['manifest'],plan['content'],plan.get('game'))
    if path.parent!=backup:raise FormatError('Receipt must be inside its bound backup directory')
    if read_acp(mod_file(dest))[0].get('package_id')!=plan['package_id']:raise FormatError('Installed package identity changed')
    original=backup/'original'
    if plan['before'] is not None and not equivalent(inventory(original),plan['before']):raise FormatError('Original backup changed')
    if plan['before'] and read_acp(mod_file(original))[0].get('package_id')!=plan['package_id']:
        raise FormatError('Backup package identity mismatch')
    saved=backup/'post-sync'
    if saved.exists():
        if not equivalent(inventory(saved),plan['after']):raise FormatError('Post-sync backup differs from installed result')
    else:
        with new_directory(saved,[dest]) as payload:
            shutil.copytree(dest,payload,dirs_exist_ok=True)
            if not equivalent(inventory(payload),plan['after']):raise FormatError('Post-sync backup verification failed')
    def starting():record['status']='rolling_back';save_record(path,record)
    swap(dest,original if plan['before'] is not None else None,plan['after'],plan['before'],starting)
    record['status']='rolled_back';record['rolled_back_utc']=datetime.now(timezone.utc).isoformat();save_record(path,record)
    return {'destination':str(dest),'status':'rolled_back','original_restored':True,'post_sync_backup':str(saved),'workshop_submitted':False}
