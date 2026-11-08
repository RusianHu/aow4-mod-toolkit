"""Same-package launcher-only maintenance preview with guarded withdrawal."""
import argparse
import json
from pathlib import Path
import sys

from aow4mod import launcher,loadout,maintenance,maintenance_context,publication,synchronization
from aow4mod.core import FormatError,mod_file,read_acp
from aow4mod.discovery import user_data
from aow4mod.filesystem import file_hash,inventory,new_directory,outside,save_record
import launcher_runtime as runtime
import launcher_session

FORMAT='aow4mod-maintenance-preview/1'


def prepare(workspace,destination,output,note=None):
    loadout._idle()
    root,state,config,settings,source,_=maintenance.read(workspace)
    maintenance.current_assessment(root,state,config,source)
    context=maintenance.context_root(root,state)
    if context is None:raise FormatError('Preview requires captured actual playset context')
    if not state.get('build'):raise FormatError('Build the context-bound candidate first')
    manifest_path=root/state['build']['manifest'];manifest,content=publication.load_release(manifest_path)
    if file_hash(manifest_path)!=state['build']['manifest_sha256']:raise FormatError('Maintenance candidate changed')
    native=manifest.get('native_validation_summary',{})
    if manifest.get('native_validation_requested') is not True or native.get('errors')!=0 or native.get('mismatches')!=0:
        raise FormatError('Preview requires native validation without errors/data mismatches')
    if manifest['_review_required'] and (not isinstance(note,str) or not note.strip()):
        raise FormatError('A scoped launcher-only review note is required; publication findings remain unresolved')
    database=launcher.database_path();data=database.parent;destination=Path(destination).resolve();origin=Path(state['origin']).resolve()
    if destination.parent!=data/'Mods' or destination==origin or destination.exists():
        raise FormatError('Preview destination must be a new distinct direct child of the real Mods folder')
    inspected=launcher.inspect()
    if not inspected['pairing_supported'] or inspected['configuration_matches_database'] is not True:raise FormatError('Actual launcher pairing/configuration is not ready')
    original=[m for m in inspected['members'] if Path(m['path']).resolve()==origin]
    if len(original)!=1 or not original[0]['enabled']:raise FormatError('Original Mod must be uniquely enabled')
    if original[0]['package_id']!=state['package_id'] or read_acp(mod_file(content))[0].get('package_id')!=state['package_id']:
        raise FormatError('Preview must preserve the original package identity')
    output=Path(output).resolve()
    if root not in output.parents:raise FormatError('Preview evidence must stay inside the maintenance workspace')
    outside(output,[data,origin,Path(state['game']),destination])
    plan=synchronization.make_plan(manifest_path,destination,output/'installation-backup',Path(state['game']),allow_review=manifest['_review_required'])
    con=launcher.connect(database)
    try:rows=[dict(r) for r in con.execute('SELECT * FROM mods ORDER BY id')]
    finally:con.close()
    record={'format':FORMAT,'root':str(output),'workspace':str(root),'game':state['game'],'manifest':str(manifest_path),'manifest_sha256':file_hash(manifest_path),
            'origin':str(origin),'origin_files':inventory(origin),'destination':str(destination),'package_id':state['package_id'],
            'original_member':original[0],'before_inspection':inspected,'before_registered_rows':rows,
            'before_configuration_sha256':file_hash(data/'content_load.json'),'database':str(database),
            'database_sha256':file_hash(database),'review_note':note,'review_resolved':False,'gameplay_tested':False,'published':False}
    with new_directory(output,[data,origin,Path(state['game']),destination]) as stage:
        runtime.copy_file(database,stage/'baseline.sqlite');runtime.copy_file(data/'content_load.json',stage/'baseline-content_load.json')
        save_record(stage/'installation-plan.json',plan);record['installation_plan_sha256']=file_hash(stage/'installation-plan.json');save_record(stage/'preview.json',record)
        save_record(stage/'state.json',{'format':FORMAT,'phase':'prepared','preview_sha256':file_hash(stage/'preview.json')})
    return status(output)


def read(path):
    root=Path(path).resolve();record=json.loads((root/'preview.json').read_text(encoding='utf-8'));state=json.loads((root/'state.json').read_text(encoding='utf-8'))
    if record.get('format')!=FORMAT or state.get('format')!=FORMAT or record.get('root')!=str(root) or file_hash(root/'preview.json')!=state.get('preview_sha256'):
        raise FormatError('Invalid or edited preview record')
    workspace,workflow,_,_,_,_=maintenance.read(record['workspace'])
    if Path(record['origin']).resolve()!=Path(workflow['origin']).resolve() or record['package_id']!=workflow['package_id']:
        raise FormatError('Preview origin/package differs from maintenance workspace')
    data=launcher.database_path(record['database']).parent;origin=Path(record['origin']).resolve();destination=Path(record['destination']).resolve()
    if destination.parent!=data/'Mods' or destination==origin or Path(record['workspace']).resolve() not in root.parents:
        raise FormatError('Preview paths changed')
    if inventory(origin)!=record['origin_files']:raise FormatError('Production Mod changed during preview; refusing overwrite')
    if file_hash(root/'baseline.sqlite')!=record['database_sha256'] or file_hash(root/'baseline-content_load.json')!=record['before_configuration_sha256']:
        raise FormatError('Preview baseline changed')
    if file_hash(record['manifest'])!=record['manifest_sha256']:raise FormatError('Preview manifest changed')
    if file_hash(root/'installation-plan.json')!=record['installation_plan_sha256']:raise FormatError('Preview installation plan changed')
    publication.load_release(record['manifest'])
    return root,record,state


def update(root,state,phase,**values):
    state.update(values);state['phase']=phase;save_record(root/'state.json',state)


def apply(path):
    root,record,state=read(path);loadout._idle()
    if state['phase']!='prepared':raise FormatError('Preview already attempted; inspect/withdraw instead of replaying')
    workspace,workflow,config,settings,source,_=maintenance.read(record['workspace'])
    maintenance.current_assessment(workspace,workflow,config,source)
    if file_hash(record['database'])!=record['database_sha256'] or file_hash(Path(record['database']).parent/'content_load.json')!=record['before_configuration_sha256']:
        raise FormatError('Actual launcher changed after preview planning')
    install=json.loads((root/'installation-plan.json').read_text(encoding='utf-8'))
    release,_=publication.load_release(record['manifest'])
    expected=synchronization.make_plan(record['manifest'],record['destination'],root/'installation-backup',Path(record['game']),allow_review=release['_review_required'])
    if install!=expected:raise FormatError('Preview installation recipe differs from its bound destination/manifest')
    update(root,state,'installing')
    synchronization.apply_plan(install)
    update(root,state,'installed')
    plan=launcher.make_plan(record['database'],enable=[Path(record['destination'])],disable=[Path(record['origin'])],
                           position=record['original_member']['position'])
    save_record(root/'enable-plan.json',plan);update(root,state,'enabling')
    launcher.apply_plan(plan,root/'enable-backup');update(root,state,'enabled')
    return status(root)


def observe(path,node=None):
    root,record,state=read(path)
    if state['phase'] not in ('enabled',):raise FormatError('Preview must be enabled and not previously observed')
    session=root/'official-session';launcher_session.prepare(session)
    update(root,state,'observing');result=launcher_session.run(session/'session.json',node)
    if not result['passed']:update(root,state,'observation_failed',session_result=str(session/'result.json'));return status(root)
    observed=launcher.inspect(record['database']);matches=[m for m in observed['members'] if Path(m['path']).resolve()==Path(record['destination'])]
    originals=[m for m in observed['members'] if Path(m['path']).resolve()==Path(record['origin'])]
    before=[Path(m['path']).resolve() for m in record['before_inspection']['members'] if m['enabled']]
    expected=[Path(record['destination']) if p==Path(record['origin']) else p for p in before]
    actual=[Path(m['path']).resolve() for m in observed['members'] if m['enabled']]
    passed=(len(matches)==len(originals)==1 and matches[0]['enabled'] and not originals[0]['enabled'] and
            matches[0]['package_id']==originals[0]['package_id']==record['package_id'] and actual==expected)
    save_record(root/'observation-proof.json',{'passed':passed,'candidate':matches,'original':originals,'enabled_paths_match_exact_replacement':actual==expected,'session_passed':True})
    update(root,state,'observed' if passed else 'observation_failed')
    return status(root)


def withdraw(path):
    root,record,state=read(path);loadout._idle()
    if state['phase']=='withdrawn':return status(root)
    if state['phase'] in ('enabling','withdrawing'):
        raise FormatError('Preview has an uncertain transaction boundary; inspect its exact receipt before continuing')
    if state['phase'] in ('enabled','observed','observing','observation_failed'):
        # A fresh transaction restores the original member and removes only our
        # receipt-owned variant. Never force the old pre-reopen database backup.
        plan=launcher.make_plan(record['database'],enable=[Path(record['origin'])],disable=[Path(record['destination'])],
             position=record['original_member']['position'],unregister=[Path(record['destination'])],registration_receipt=root/'enable-backup/receipt.json')
        save_record(root/'withdraw-plan.json',plan);update(root,state,'withdrawing')
        launcher.apply_plan(plan,root/'withdraw-backup');update(root,state,'registration_withdrawn')
    if state['phase'] in ('installed','registration_withdrawn'):
        synchronization.rollback(root/'installation-backup/receipt.json');update(root,state,'files_withdrawn')
    if state['phase'] not in ('prepared','files_withdrawn'):raise FormatError('Inspect incomplete installation/observation before withdrawal')
    inspected=launcher.inspect(record['database']);before=record['before_inspection']['members']
    key=lambda m:(m['mod_id'],m['position'],m['enabled'],m['path'],m['package_id'])
    if [key(m) for m in inspected['members']]!=[key(m) for m in before]:raise FormatError('Original playset members/order were not restored')
    con=launcher.connect(record['database'])
    try:rows=[dict(r) for r in con.execute('SELECT * FROM mods ORDER BY id')]
    finally:con.close()
    original_raw=(root/'baseline-content_load.json').read_bytes();original=json.loads(original_raw.decode('utf-8-sig'))
    configuration=Path(record['database']).parent/'content_load.json';current_raw=configuration.read_bytes();current=json.loads(current_raw.decode('utf-8-sig'))
    # Disabling/re-enabling a path can drop that entry's unknown JSON fields.
    # Restore the frozen user entry only when all other values are identical
    # and the original entry merely lost known keys in our own lifecycle.
    compatible={k:v for k,v in current.items() if k!='enabledMods'}=={k:v for k,v in original.items() if k!='enabledMods'}
    before_entries=original.get('enabledMods',[]);after_entries=current.get('enabledMods',[])
    compatible=compatible and len(before_entries)==len(after_entries)
    if compatible:
        for old,new in zip(before_entries,after_entries):
            if Path(old['path']).resolve()!=Path(new['path']).resolve():compatible=False;break
            if Path(old['path']).resolve()==Path(record['origin']):
                if any(k not in old or old[k]!=v for k,v in new.items()):compatible=False;break
            elif old!=new:compatible=False;break
    if rows==record['before_registered_rows'] and compatible and current_raw!=original_raw:
        loadout._replace(configuration,original_raw,file_hash(configuration));current=original
    proof={'registered_rows_exact':rows==record['before_registered_rows'],'configuration_values_exact':current==original,
           'original_members_order_enable_exact':True,'preview_directory_absent':not Path(record['destination']).exists(),'production_files_exact':inventory(record['origin'])==record['origin_files'],
           'configuration_bytes_exact':file_hash(configuration)==record['before_configuration_sha256']}
    save_record(root/'withdrawal-proof.json',proof)
    if not all(proof.values()):raise FormatError('Preview withdrawal needs review; unrelated rows/configuration differ')
    update(root,state,'withdrawn');return status(root)


def status(path):
    root,record,state=read(path)
    return {'preview':str(root),'phase':state['phase'],'package_id':record['package_id'],'identity_changed':False,
            'destination':record['destination'],'origin':record['origin'],'review_note':record['review_note'],
            'review_resolved':False,'gameplay_tested':False,'published':False}


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    q=sub.add_parser('prepare');q.add_argument('workspace',type=Path);q.add_argument('--destination',type=Path,required=True);q.add_argument('--output',type=Path,required=True);q.add_argument('--review-note')
    for command in ('apply','observe','withdraw','status'):
        q=sub.add_parser(command);q.add_argument('preview',type=Path)
        if command=='observe':q.add_argument('--node')
    args=p.parse_args()
    try:
        if args.command=='prepare':result=prepare(args.workspace,args.destination,args.output,args.review_note)
        elif args.command=='apply':result=apply(args.preview)
        elif args.command=='observe':result=observe(args.preview,args.node)
        elif args.command=='withdraw':result=withdraw(args.preview)
        else:result=status(args.preview)
        print(json.dumps(result,ensure_ascii=False,indent=2));return int(result['phase']=='observation_failed')
    except (FormatError,OSError,ValueError) as error:print(json.dumps({'error':str(error)},ensure_ascii=False));return 1


if __name__=='__main__':sys.exit(main())
