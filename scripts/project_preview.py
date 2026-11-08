"""Install, observe and withdraw a fresh local project through the official launcher.

Append-only, launcher-only acceptance. It does not press Play or clear release
findings, and it never uploads to Steam.
"""
import argparse
import json
from pathlib import Path
import sys

from aow4mod import launcher,loadout,publication,synchronization
from aow4mod.core import FormatError,mod_file,read_acp,sha
from aow4mod.discovery import find_game
from aow4mod.filesystem import file_hash,inventory,new_directory,outside,save_record
import launcher_session
import launcher_runtime as runtime

FORMAT='aow4mod-project-preview/1'


def fingerprint():
    return sha(b''.join(Path(p).read_bytes() for p in (__file__,launcher_session.__file__,runtime.__file__)))


def registered(database):
    con=launcher.connect(database)
    try:return [dict(row) for row in con.execute('SELECT * FROM mods ORDER BY id')]
    finally:con.close()


def member_keys(rows):
    return [(m['mod_id'],m['position'],m['enabled'],m['path'],m['package_id']) for m in rows]


def prepare(manifest,destination,output,game=None,note=None):
    loadout._idle();game=find_game(game)
    manifest=Path(manifest).resolve();release,content=publication.load_release(manifest)
    summary=release.get('native_validation_summary',{})
    if (release.get('native_validation_requested') is not True or summary.get('errors')!=0
            or summary.get('mismatches')!=0):
        raise FormatError('Project preview requires native validation without errors or mismatches')
    if release['_review_required'] and (not isinstance(note,str) or not note.strip()):
        raise FormatError('A launcher-only review note is required; content findings stay unresolved')
    database=launcher.database_path();data=database.parent;destination=Path(destination).resolve()
    if destination.parent!=data/'Mods' or destination.exists():
        raise FormatError('Project preview destination must be a new direct child of the real Mods folder')
    before=launcher.inspect(database)
    if not before['pairing_supported'] or before['configuration_matches_database'] is not True:
        raise FormatError('Actual launcher pairing/configuration is not ready')
    package=read_acp(mod_file(content))[0]['package_id'];rows=registered(database)
    if any(str(row.get('metadataId','')).casefold()==package.casefold() for row in rows):
        raise FormatError('Package identity is already registered; use the maintenance preview for replacements')
    output=outside(output,[data,game,manifest.parent,destination])
    plan=synchronization.make_plan(manifest,destination,output/'installation-backup',game,allow_review=release['_review_required'])
    if plan['before'] is not None:raise FormatError('Project preview must not replace an existing directory')
    record={'format':FORMAT,'root':str(output),'generator_sha256':fingerprint(),'game':str(game),
            'manifest':str(manifest),'manifest_sha256':file_hash(manifest),'destination':str(destination),
            'package_id':package,'database':str(database),'database_sha256':file_hash(database),
            'before_configuration_sha256':file_hash(data/'content_load.json'),'before_inspection':before,
            'before_registered_rows':rows,'review_note':note,'review_resolved':False,
            'gameplay_tested':False,'published':False}
    with new_directory(output,[data,game,manifest.parent,destination]) as stage:
        runtime.copy_file(database,stage/'baseline.sqlite');runtime.copy_file(data/'content_load.json',stage/'baseline-content_load.json')
        if file_hash(stage/'baseline.sqlite')!=record['database_sha256'] or file_hash(stage/'baseline-content_load.json')!=record['before_configuration_sha256']:
            raise FormatError('Project preview baseline changed during capture')
        save_record(stage/'installation-plan.json',plan);record['installation_plan_sha256']=file_hash(stage/'installation-plan.json')
        save_record(stage/'preview.json',record)
        save_record(stage/'state.json',{'format':FORMAT,'phase':'prepared','preview_sha256':file_hash(stage/'preview.json')})
    return status(output)


def read(path,verify_release=False):
    root=Path(path).resolve();record=json.loads((root/'preview.json').read_text(encoding='utf8'))
    state=json.loads((root/'state.json').read_text(encoding='utf8'))
    if (record.get('format')!=FORMAT or state.get('format')!=FORMAT or record.get('root')!=str(root)
            or record.get('generator_sha256')!=fingerprint() or file_hash(root/'preview.json')!=state.get('preview_sha256')):
        raise FormatError('Unsupported, changed or outdated project-preview record')
    data=launcher.database_path(record['database']).parent;destination=Path(record['destination']).resolve()
    if destination.parent!=data/'Mods':raise FormatError('Project preview destination escaped the local Mods directory')
    outside(root,[data,Path(record['game']),Path(record['manifest']).parent,destination])
    if (file_hash(root/'baseline.sqlite')!=record['database_sha256']
            or file_hash(root/'baseline-content_load.json')!=record['before_configuration_sha256']
            or file_hash(root/'installation-plan.json')!=record['installation_plan_sha256']):
        raise FormatError('Project preview baseline or installation plan changed')
    if verify_release:
        if file_hash(record['manifest'])!=record['manifest_sha256']:raise FormatError('Project preview manifest changed')
        publication.load_release(record['manifest'])
    return root,record,state


def update(root,state,phase):
    state['phase']=phase;save_record(root/'state.json',state)


def apply(path):
    root,record,state=read(path,True);loadout._idle()
    if state['phase']!='prepared':raise FormatError('Project preview already attempted; inspect or withdraw')
    if (file_hash(record['database'])!=record['database_sha256']
            or file_hash(Path(record['database']).parent/'content_load.json')!=record['before_configuration_sha256']):
        raise FormatError('Actual launcher changed after project-preview preparation')
    plan=json.loads((root/'installation-plan.json').read_text(encoding='utf8'))
    release,_=publication.load_release(record['manifest'])
    expected=synchronization.make_plan(record['manifest'],record['destination'],root/'installation-backup',Path(record['game']),allow_review=release['_review_required'])
    if plan!=expected:raise FormatError('Project-preview installation recipe differs')
    update(root,state,'installing');synchronization.apply_plan(plan);update(root,state,'installed')
    plan=launcher.make_plan(record['database'],enable=[Path(record['destination'])],position=len(record['before_inspection']['members']))
    save_record(root/'enable-plan.json',plan);update(root,state,'enabling')
    launcher.apply_plan(plan,root/'enable-backup');update(root,state,'enabled')
    return status(root)


def observe(path,node=None,final=False):
    root,record,state=read(path)
    if state['phase']!=('withdrawn' if final else 'enabled'):
        raise FormatError('Project preview is not ready for this observation')
    session=root/('withdrawn-session' if final else 'official-session');launcher_session.prepare(session,Path(record['game']))
    update(root,state,'verifying_withdrawal' if final else 'observing')
    result=launcher_session.run(session/'session.json',node)
    observed=launcher.inspect(record['database']);before=record['before_inspection']['members']
    old_paths=[m['path'] for m in before if m['enabled']]
    paths=[m['path'] for m in observed['members'] if m['enabled']]
    candidates=[m for m in observed['members'] if Path(m['path']).resolve()==Path(record['destination'])]
    if final:
        passed=(result['passed'] and not candidates and member_keys(observed['members'])==member_keys(before)
                and registered(record['database'])==record['before_registered_rows'] and not Path(record['destination']).exists()
                and file_hash(Path(record['database']).parent/'content_load.json')==record['before_configuration_sha256'])
    else:
        retained=[m for m in observed['members'] if m not in candidates]
        passed=(result['passed'] and len(candidates)==1 and candidates[0]['enabled']
                and candidates[0]['package_id']==record['package_id']
                and member_keys(retained)==member_keys(before) and paths==old_paths+[record['destination']])
    save_record(root/('withdrawn-observation.json' if final else 'observation-proof.json'),
                {'passed':passed,'session_passed':result['passed'],'candidate':candidates,
                 'enabled_paths':paths,'original_members_preserved':member_keys([m for m in observed['members'] if m not in candidates])==member_keys(before),
                 'gameplay_tested':False})
    update(root,state,('verified_withdrawn' if final else 'observed') if passed else ('withdrawal_observation_failed' if final else 'observation_failed'))
    return status(root)


def withdraw(path):
    root,record,state=read(path);loadout._idle()
    if state['phase'] in ('withdrawn','verified_withdrawn'):return status(root)
    if state['phase'] in ('enabled','observed','observing','observation_failed'):
        plan=launcher.make_plan(record['database'],disable=[Path(record['destination'])],
             unregister=[Path(record['destination'])],registration_receipt=root/'enable-backup/receipt.json')
        save_record(root/'withdraw-plan.json',plan);update(root,state,'withdrawing')
        launcher.apply_plan(plan,root/'withdraw-backup');update(root,state,'registration_withdrawn')
    if state['phase'] in ('installed','registration_withdrawn'):
        synchronization.rollback(root/'installation-backup/receipt.json');update(root,state,'files_withdrawn')
    if state['phase'] not in ('prepared','files_withdrawn'):
        raise FormatError('Uncertain transaction boundary; inspect exact installation/registration receipts before recovery')
    inspected=launcher.inspect(record['database']);rows=registered(record['database'])
    original=(root/'baseline-content_load.json').read_bytes();configuration=Path(record['database']).parent/'content_load.json'
    # Formatting-only regeneration can be restored after exact logical equality.
    if (rows==record['before_registered_rows'] and member_keys(inspected['members'])==member_keys(record['before_inspection']['members'])
            and json.loads(configuration.read_text(encoding='utf-8-sig'))==json.loads(original.decode('utf-8-sig'))):
        if configuration.read_bytes()!=original:loadout._replace(configuration,original,file_hash(configuration))
    proof={'registered_rows_exact':rows==record['before_registered_rows'],
           'members_order_enable_exact':member_keys(inspected['members'])==member_keys(record['before_inspection']['members']),
           'configuration_bytes_exact':file_hash(configuration)==record['before_configuration_sha256'],
           'preview_directory_absent':not Path(record['destination']).exists()}
    save_record(root/'withdrawal-proof.json',proof)
    if not all(proof.values()):raise FormatError('Project preview withdrawal needs review; original state differs')
    update(root,state,'withdrawn');return status(root)


def status(path):
    root,record,state=read(path)
    return {'preview':str(root),'phase':state['phase'],'package_id':record['package_id'],
            'destination':record['destination'],'review_resolved':False,'gameplay_tested':False,'published':False}


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    q=sub.add_parser('prepare');q.add_argument('manifest',type=Path);q.add_argument('--destination',type=Path,required=True)
    q.add_argument('--output',type=Path,required=True);q.add_argument('--game',type=Path);q.add_argument('--review-note')
    for name in ('apply','observe','withdraw','verify-withdrawal','status'):
        q=sub.add_parser(name);q.add_argument('preview',type=Path)
        if name in ('observe','verify-withdrawal'):q.add_argument('--node')
    a=p.parse_args()
    try:
        if a.command=='prepare':result=prepare(a.manifest,a.destination,a.output,a.game,a.review_note)
        elif a.command=='apply':result=apply(a.preview)
        elif a.command in ('observe','verify-withdrawal'):result=observe(a.preview,a.node,a.command=='verify-withdrawal')
        elif a.command=='withdraw':result=withdraw(a.preview)
        else:result=status(a.preview)
        print(json.dumps(result,ensure_ascii=False,indent=2));return int('failed' in result['phase'])
    except (FormatError,OSError,ValueError) as exc:print(json.dumps({'error':str(exc)},ensure_ascii=False));return 1


if __name__=='__main__':sys.exit(main())
