"""Versioned scoped review notes; lifecycle evidence never clears release findings."""
import json
from pathlib import Path
import shutil

from . import session_evidence,maintenance_release,launcher
from .core import FormatError,mod_file,read_acp
from .filesystem import file_hash,inventory,save_record

FORMAT='aow4mod-maintenance-review/1'


def members(observed):
    mods={m['id']:m for m in observed['mods']}
    return [(m['modId'],m['position'],bool(m['enabled']),mods[m['modId']]['dirPath'],mods[m['modId']]['metadataId']) for m in observed['members']]


def preview_evidence(root,manifest,manifest_sha256,package_id):
    root=Path(root);plan=json.loads((root/'preview.json').read_text(encoding='utf-8'));state=json.loads((root/'state.json').read_text(encoding='utf-8'))
    if plan.get('format')!='aow4mod-maintenance-preview/1' or state.get('phase')!='withdrawn' or state.get('preview_sha256')!=file_hash(root/'preview.json'):
        raise FormatError('Review requires a complete, unchanged withdrawn preview')
    if plan.get('manifest_sha256')!=manifest_sha256 or plan.get('package_id')!=package_id:
        raise FormatError('Preview evidence belongs to another candidate/package')
    binding=plan['before_inspection']['application'];launcher.binding_profile(binding)
    for name in ('official-session','post-withdrawal-session'):
        session=json.loads((root/name/'session.json').read_text(encoding='utf-8'))
        if session.get('application')!=binding or session.get('signature',{}).get('status')!='Valid':
            raise FormatError('Preview session application/signature differs from the reviewed pairing')
    if file_hash(root/'baseline.sqlite')!=plan['database_sha256'] or file_hash(root/'baseline-content_load.json')!=plan['before_configuration_sha256']:
        raise FormatError('Preview baseline witnesses changed')
    baseline=session_evidence.database(root/'baseline.sqlite')
    if baseline['mods']!=plan['before_registered_rows']:raise FormatError('Preview baseline Mod rows differ')
    expected=[(m['mod_id'],m['position'],m['enabled'],m['path'],m['package_id']) for m in plan['before_inspection']['members']]
    if members(baseline)!=expected:raise FormatError('Preview baseline membership differs')
    observed=session_evidence.session(root/'official-session');after=observed['database']
    original=[m for m in after['mods'] if Path(m['dirPath']).resolve()==Path(plan['origin']).resolve()]
    candidate=[m for m in after['mods'] if Path(m['dirPath']).resolve()==Path(plan['destination']).resolve()]
    if len(original)!=1 or len(candidate)!=1 or original[0]['metadataId']!=candidate[0]['metadataId'] or candidate[0]['metadataId']!=package_id:
        raise FormatError('Preview archived variants do not preserve package identity')
    active={m['modId']:bool(m['enabled']) for m in after['members']}
    if active.get(original[0]['id']) is not False or active.get(candidate[0]['id']) is not True:
        raise FormatError('Preview archived activation does not select only the candidate')
    enabled=[plan['destination'] if Path(p).resolve()==Path(plan['origin']).resolve() else p for p in baseline['enabled_paths']]
    if [str(Path(p).resolve()) for p in after['enabled_paths']]!=[str(Path(p).resolve()) for p in enabled]:
        raise FormatError('Preview archived enabled paths differ from one exact replacement')
    withdrawn=session_evidence.session(root/'post-withdrawal-session');final=withdrawn['database']
    if final['mods']!=baseline['mods'] or members(final)!=members(baseline):raise FormatError('Preview archived original Mod rows/members were not restored')
    if file_hash(withdrawn['configuration_file'])!=plan['before_configuration_sha256']:
        raise FormatError('Preview archived configuration bytes were not restored')
    installed=root/'installation-backup/post-sync'
    files=inventory(installed)
    if any(files.get(name,{}).get('sha256')!=digest for name,digest in manifest['files'].items()):
        raise FormatError('Preview archived installed files differ from the candidate')
    if read_acp(mod_file(installed))[0].get('package_id')!=package_id:raise FormatError('Preview installed package identity differs')
    return {'launcher_lifecycle_verified':True,'same_package_identity':True,'exact_path_replacement':True,
            'withdrawal_rows_members_configuration_verified':True,'gameplay_verified':False,
            'scope':'Saved actual Steam renderer/logs and SQLite/configuration/install witnesses; no rendering or gameplay conclusion.'}


def snapshot_inputs(state):
    return {'manifest_sha256':state['build']['manifest_sha256'],
            'assessment_artifacts':state['assessment']['artifacts'],
            'source_files':state['assessment']['source_files'],
            'context':state.get('context')}


def conclusion(manifest,preview):
    native=manifest.get('native_validation_summary') or {}
    environment=manifest.get('environment_summary') or {}
    history=manifest.get('maintenance_validation_summary') or {}
    return {'release_review_required':bool(manifest.get('release_gate',{}).get('review_required')),
            'dispositions':[
                {'category':'maintenance_history','status':'unresolved' if history.get('review_required') else 'no_findings_in_captured_scope','finding_count':len(history.get('findings',[]))},
                {'category':'environment','status':'unresolved' if environment.get('review_required') else 'no_findings_in_captured_scope','actionable_items':environment.get('actionable_issues',0)},
                {'category':'native','status':'unresolved' if native.get('review_required') else 'no_findings_in_captured_scope','errors':native.get('errors',0),'mismatches':native.get('mismatches',0),'diagnostics':native.get('diagnostic_count',0)},
                {'category':'launcher_lifecycle','status':'verified_scope_only' if preview else 'not_evaluated'}],
            'preview':preview,'compatibility_resolved':False,'publication_authorized':False,'gameplay_verified':False}


def create(root,state,manifest,note,preview=None):
    if not isinstance(note,str) or not note.strip() or len(note)>12000:raise FormatError('Review note must contain 1..12000 characters')
    root=Path(root);root.mkdir()
    evidence=None
    if preview:
        source=Path(preview).resolve();files=inventory(source)
        selected={k:v for k,v in files.items() if not k.startswith('git-export/') and k!='skill-tests.log'}
        if sum(v['size'] for v in selected.values())>128*1024*1024:raise FormatError('Preview review evidence exceeds 128 MiB')
        target=root/'preview';target.mkdir()
        for name,item in selected.items():
            p=target/name;p.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source/name,p)
            if file_hash(p)!=item['sha256']:raise FormatError('Preview evidence changed while copying')
        evidence=preview_evidence(target,manifest,state['build']['manifest_sha256'],state['package_id'])
    record={'format':FORMAT,'inputs':snapshot_inputs(state),'note':note,'preview_requested':bool(preview),'conclusion':conclusion(manifest,evidence),
            'evidence_files':inventory(root/'preview') if evidence else {}}
    save_record(root/'review.json',record)
    return {'file':str(root/'review.json'),'sha256':file_hash(root/'review.json')}


def validate(root,state,manifest):
    root=Path(root);path=root/'review.json'
    if path.stat().st_size>16*1024*1024:raise FormatError('Maintenance review is too large')
    record=json.loads(path.read_text(encoding='utf-8'))
    if record.get('format')!=FORMAT or record.get('inputs')!=snapshot_inputs(state):raise FormatError('Review refers to stale/different maintenance inputs')
    evidence=None
    requested=record.get('preview_requested',bool(record.get('evidence_files')))
    if type(requested) is not bool or requested!=bool(record.get('evidence_files')) or requested!=(root/'preview').is_dir():
        raise FormatError('Requested review preview coverage is missing or changed')
    if record.get('evidence_files'):
        if inventory(root/'preview')!=record['evidence_files']:raise FormatError('Frozen review preview evidence changed')
        evidence=preview_evidence(root/'preview',manifest,state['build']['manifest_sha256'],state['package_id'])
    if record.get('conclusion')!=conclusion(manifest,evidence):raise FormatError('Review conclusion cannot clear findings or elevate verification scope')
    if not isinstance(record.get('note'),str) or not record['note'].strip():raise FormatError('Review note missing')
    return record['conclusion']
