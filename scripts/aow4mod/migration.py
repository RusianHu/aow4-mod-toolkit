"""Rebase proposals derived from three-way evidence, never from guessed defaults."""
from collections import defaultdict
from pathlib import Path

from . import snapshots, editing
from .core import FormatError, mod_file, sha

FORMAT='aow4mod-rebase-plan/1'


def make_rebase_plan(previous,mod,game,index=None,allow_partial=False):
    current=snapshots.capture(mod,game,index,previous.get('dependency_capture',{}).get('depth',0))
    comparison=snapshots.compare(previous,current)
    by_file=defaultdict(list)
    skipped=[]
    for row in comparison['resources']:
        resource=current['modified'].get(row['resource'])
        if not resource:continue
        for change in row['fields']:
            if change['classification']!='safe_rebase_candidate':continue
            path=change['property']
            if path.startswith('meta/'):
                skipped.append({'resource':row['resource'],'property':path,'reason':'metadata_field_requires_review'})
                continue
            cls,_,normalized=editing.property_parts(path)
            present_classes={int(k.split('/')[0],16) for k in resource['fields'] if not k.startswith('meta/')}
            if cls not in present_classes:
                skipped.append({'resource':row['resource'],'property':path,'reason':'new_class_section_requires_review'})
                continue
            old=resource['fields'].get(path)
            new=current['originals'][row['resource']]['fields'].get(path)
            by_file[resource['file']].append({'pack':resource['package'],'id':resource['id'],
                'type_id':int(resource['type_id'],16),'name':resource['name'],'property':normalized,
                'codec':'unset' if new is None else 'hex','value':new,'expected_hex':old,'replacement_hex':new})
    plans=[]
    for source,ops in by_file.items():
        plans.append({'format':editing.PLAN_FORMAT,'source':source,'source_sha256':current['source_files'][source],
            'game':str(Path(game).resolve()),'schema_tool_sha256':None,'validation_level':'structural_only',
            'required_inputs':current['original_source_files'],'operations':ops,
            'note':'Copy only fields unchanged by the author from the verified new original; review is still required.'})
    blocking=bool(comparison['summary']['manual_conflicts'] or skipped or current['audit_issues'] or current['dependency_capture']['issues'])
    can_apply=bool(plans) and (not blocking or allow_partial)
    return {'format':FORMAT,'source_root':str(mod_file(Path(mod)).parent),
            'source_files':current['source_files'],'original_source_files':current['original_source_files'],
            'allow_partial':allow_partial,'applicable':can_apply,
            'status':'ready' if can_apply else ('review_required' if blocking else 'no_changes'),
            'comparison':comparison,'skipped':skipped,'plans':plans,
            'coverage':'Serialized field rebase of captured resources; class additions and semantic conflicts require review.'}


def apply_rebase(plan,output):
    if not isinstance(plan,dict) or plan.get('format')!=FORMAT:raise FormatError('Unsupported rebase plan')
    if not plan.get('applicable') or not plan.get('plans'):raise FormatError('Rebase plan has no applicable edits')
    conflicts=plan.get('comparison',{}).get('summary',{}).get('manual_conflicts',0)
    if (conflicts or plan.get('skipped')) and not plan.get('allow_partial'):
        raise FormatError('Unresolved rebase issues require explicit partial mode')
    for source,expected in {**plan['source_files'],**plan['original_source_files']}.items():
        if sha(Path(source).read_bytes())!=expected:raise FormatError('Rebase input changed since planning')
    result=editing.apply_project_plans(plan['plans'],plan['source_root'],output)
    result['rebase_partial']=plan['allow_partial']
    return result
