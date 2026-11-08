"""Recomputable maintenance findings and frozen publication context."""
import json
from pathlib import Path
import shutil

from . import snapshots,maintenance_context
from .core import FormatError, mod_file, read_acp, read_rpk
from .filesystem import file_hash, inventory, save_record

FORMAT='aow4mod-maintenance-evidence/1'


def assessment(previous,current,context=None):
    findings=[]
    def add(kind,detail):findings.append({'issue':kind,'detail':detail})
    comparison=snapshots.compare(previous,current) if previous else None
    if previous is None:add('maintenance_baseline_missing','No previous original/author state; update compatibility is unassessed.')
    for label,snapshot in (('previous',previous),('current',current)):
        if snapshot is None:continue
        if snapshot.get('audit_issues'):add('maintenance_capture_issues',{'capture':label,'issues':snapshot['audit_issues']})
        if snapshot.get('dependency_capture',{}).get('issues'):
            add('maintenance_dependency_capture_incomplete',{'capture':label,'issues':snapshot['dependency_capture']['issues']})
    if comparison:
        for row in comparison['resources']:
            if row.get('action'):add('maintenance_original_requires_review',{'resource':row['resource'],'action':row['action']})
            if row.get('mod_type_changed'):add('maintenance_mod_type_changed',row['resource'])
            if row.get('mod_presence') in ('added','removed'):add('maintenance_mod_resource_changed',{'resource':row['resource'],'presence':row['mod_presence']})
            for field in row['fields']:
                if field['classification']=='overlapping_change_needs_review':
                    add('maintenance_author_upstream_overlap',{'resource':row['resource'],'property':field['property']})
    if context and context['review_required']:add('maintenance_playset_context_requires_review',context)
    author_changes=0
    for key,resource in current['modified'].items():
        original=current['originals'].get(key)
        if original:
            author_changes+=sum(resource['fields'].get(p)!=original['fields'].get(p)
                                for p in resource['fields'].keys()|original['fields'].keys() if not p.startswith('meta/'))
    result={'format':'aow4mod-maintenance-assessment/1','review_required':bool(findings),'findings':findings,
            'baseline_available':previous is not None,'author_delta_fields':author_changes,
            'comparison_summary':comparison['summary'] if comparison else None,
            'game_version_scope':'Installed original files captured in the snapshots; not a server-latest claim.',
            'gameplay_tested':False}
    if context is not None:result['playset_context']=context
    return result


def projection(path):
    acp=mod_file(Path(path));meta,changes=read_acp(acp);records={}
    def add(resource,pack,kind,operation=None):
        key=snapshots.key(pack,resource.rid)
        if key in records:raise FormatError('Ambiguous maintenance payload resource identity')
        records[key]={'id':f'{resource.rid:016X}','type_id':f'0x{resource.type_id:08X}',
                      'fields':{k:v.hex() for k,v in resource.leaves().items()},'kind':kind,'operation':operation}
    for change in changes:add(change['resource'],change['pack'],'override',change['operation_raw'])
    for file in sorted(acp.parent.rglob('*.rpk')):
        info,resources=read_rpk(file)
        for resource in resources:add(resource,info['name'],'local_rpk')
    return {'package_id':meta.get('package_id'),'registered_packs':meta['registered_resource_packs'],'resources':records}


def snapshot_projection(snapshot):
    return {'package_id':snapshot['mod'].get('package_id'),'registered_packs':snapshot['mod']['registered_resource_packs'],
            'resources':{k:{name:r.get(name) for name in ('id','type_id','fields','kind','operation')}
                         for k,r in snapshot['modified'].items()}}


def attach(manifest_path,previous,current,comparison,context=None):
    manifest_path=Path(manifest_path).resolve();root=manifest_path.parent
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'));content=(root/manifest['content_directory']).resolve()
    if projection(content)!=snapshot_projection(current):raise FormatError('Maintenance snapshot differs from built payload')
    expected=snapshots.compare(previous,current) if previous else None
    if comparison!=expected:raise FormatError('Maintenance comparison does not match its snapshots')
    evidence=root/'maintenance-evidence'
    if evidence.exists() or (root/'maintenance.json').exists():raise FormatError('Maintenance release evidence already exists')
    evidence.mkdir();records={}
    for name,value in (('baseline.json',previous),('current.json',current),('comparison.json',comparison)):
        if value is not None:
            save_record(evidence/name,value);records[name]=file_hash(evidence/name)
    context_record=None;context_summary=None
    if context is not None:
        target=root/'maintenance-context'
        if target.exists():raise FormatError('Maintenance context already exists')
        shutil.copytree(context,target)
        context_report=maintenance_context.verify(target,content);context_summary=context_report['summary']
        save_record(root/'maintenance-context-check.json',context_report)
        context_record={'directory':'maintenance-context','context_sha256':file_hash(target/'context.json'),
                        'check_file':'maintenance-context-check.json','check_sha256':file_hash(root/'maintenance-context-check.json')}
    summary=assessment(previous,current,context_summary)
    record={'format':FORMAT,'files':records,'payload_files':manifest['files'],'summary':summary,'native_required':True}
    if context_record is not None:record['context']=context_record;manifest['maintenance_context_requested']=True
    save_record(root/'maintenance.json',record)
    manifest.update(maintenance_validation_requested=True,maintenance_validation_file='maintenance.json',
                    maintenance_validation_sha256=file_hash(root/'maintenance.json'),maintenance_validation_summary=summary)
    if summary['review_required']:
        manifest['release_gate']['review_required']=True
        manifest['release_gate']['issues'].append({'issue':'maintenance_review_required','count':len(summary['findings'])})
    save_record(manifest_path,manifest)
    verify(root,manifest,content)
    return summary


def verify(root,manifest,content):
    root=Path(root).resolve();content=Path(content).resolve()
    requested=manifest.get('maintenance_validation_requested',False)
    if type(requested) is not bool:raise FormatError('Invalid maintenance release policy')
    present=(root/'maintenance.json').is_file()
    if not requested and not manifest.get('maintenance_validation_file') and not present:return None
    if not requested or manifest.get('maintenance_validation_file')!='maintenance.json' or not present:
        raise FormatError('Requested maintenance evidence/policy is missing')
    path=root/'maintenance.json'
    if path.stat().st_size>1024*1024 or file_hash(path)!=manifest.get('maintenance_validation_sha256'):
        raise FormatError('Maintenance evidence changed')
    record=json.loads(path.read_text(encoding='utf-8'))
    if record.get('format')!=FORMAT or record.get('payload_files')!=manifest['files']:
        raise FormatError('Maintenance evidence payload/format differs')
    files=record.get('files',{})
    if not isinstance(files,dict) or set(files) not in ({'current.json'},{'baseline.json','current.json','comparison.json'}):
        raise FormatError('Maintenance snapshot/comparison coverage is incomplete')
    loaded={}
    for name,digest in files.items():
        file=root/'maintenance-evidence'/name
        if not file.is_file() or file.stat().st_size>64*1024*1024 or file_hash(file)!=digest:
            raise FormatError('Frozen maintenance input changed')
        loaded[name]=snapshots.load(file) if name!='comparison.json' else json.loads(file.read_text(encoding='utf-8'))
    current=loaded['current.json'];previous=loaded.get('baseline.json')
    expected=snapshots.compare(previous,current) if previous else None
    if loaded.get('comparison.json')!=expected:raise FormatError('Frozen maintenance comparison was not derived from snapshots')
    if projection(content)!=snapshot_projection(current):raise FormatError('Frozen maintenance snapshot does not match payload resources')
    context=record.get('context');context_summary=None
    if context is not None or manifest.get('maintenance_context_requested') or (root/'maintenance-context').exists():
        if manifest.get('maintenance_context_requested') is not True or not isinstance(context,dict):
            raise FormatError('Requested maintenance playset context is missing')
        if context.get('directory')!='maintenance-context' or context.get('check_file')!='maintenance-context-check.json':
            raise FormatError('Maintenance context witness path changed')
        folder=root/'maintenance-context';check=root/'maintenance-context-check.json'
        if file_hash(folder/'context.json')!=context.get('context_sha256') or file_hash(check)!=context.get('check_sha256'):
            raise FormatError('Maintenance context witnesses changed')
        replay=maintenance_context.verify(folder,content)
        if json.loads(check.read_text(encoding='utf-8'))!=replay:raise FormatError('Maintenance conflict report differs from raw Mod witnesses')
        context_summary=replay['summary']
    summary=assessment(previous,current,context_summary)
    if record.get('summary')!=summary or manifest.get('maintenance_validation_summary')!=summary:
        raise FormatError('Maintenance findings/summary changed')
    if record.get('native_required') is not True or manifest.get('native_validation_requested') is not True:
        raise FormatError('Maintenance build requires frozen native validation')
    if summary['review_required'] and (not manifest.get('release_gate',{}).get('review_required') or not any(
        i.get('issue')=='maintenance_review_required' for i in manifest.get('release_gate',{}).get('issues',[]))):
        raise FormatError('Maintenance findings are missing from release gate')
    return summary


def freeze(manifest,stage):
    root=Path(manifest['_maintenance_root']);stage=Path(stage)
    verify(root,manifest,manifest['content_directory'])
    shutil.copyfile(root/'maintenance.json',stage/'maintenance.json')
    shutil.copytree(root/'maintenance-evidence',stage/'maintenance-evidence')
    if manifest.get('maintenance_context_requested'):
        shutil.copytree(root/'maintenance-context',stage/'maintenance-context')
        shutil.copyfile(root/'maintenance-context-check.json',stage/'maintenance-context-check.json')
    manifest['_maintenance_root']=str(stage)
