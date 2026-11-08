"""Native material-condition review; does not impersonate a renderer draw context."""
from collections import defaultdict
import json
from pathlib import Path
import struct

from . import condition_syntax,effect_programs,material_links,native,native_conditions,native_validation
from .core import FormatError,Node
from .filesystem import file_hash

FORMAT='aow4mod-material-conditions/1'


def fields(node):
    cid,body=node.typed()
    return cid,body[cid].mapping() if cid in body else body


def source_data(material_body,effect_body):
    mat=Node(material_body,0,len(material_body)).mapping();effect=Node(effect_body,0,len(effect_body)).mapping();definitions={}
    for node in effect[53].array().values():
        cid,body=fields(node);name=body[40].string()
        if name in definitions:raise FormatError('Ambiguous material condition declaration')
        definitions[name]=(cid,body)
    variables={};unsupported={}
    for node in mat[51].array().values():
        cid,body=fields(node);name=body[40].string()
        if name in variables or name in unsupported:raise FormatError('Duplicate loaded material parameter')
        decl=definitions.get(name)
        if cid!=0x411696 or not decl or decl[0]!=0x411694:
            unsupported[name]='nonconstant_or_unsupported_expression';continue
        info=decl[1];code=info[101].uint(4) if 101 in info and len(info[101].raw())==4 else None
        raw=body[100].raw() if 100 in body else b''
        if len(raw)!=16:raise FormatError('Material condition constant is not 16 bytes')
        editor=info[44].uint(4) if 44 in info else None
        if (code,editor)==(0x80109,0x401606):entry={'codec':'f32','value':struct.unpack_from('<f',raw)[0]}
        elif (code,editor)==(0x80121,0x401607):
            value=struct.unpack_from('<I',raw)[0]
            if value not in (0,1):raise FormatError('Material condition bool bits are invalid')
            entry={'codec':'bool','value':bool(value)}
        elif (code,editor)==(0x80119,0x40160d):entry={'codec':'i32','value':struct.unpack_from('<i',raw)[0]}
        else:unsupported[name]='nonscalar_or_unreviewed_type';continue
        if name in ('true','false'):raise FormatError('Material parameter shadows a reserved boolean literal')
        condition_syntax.f32(float(entry['value']));variables[name]=entry
    variables={'true':{'codec':'bool','value':True},'false':{'codec':'bool','value':False},**variables}
    conditions=[]
    for index,node in effect.get(63,Node(b'\1\1\0\0',0,4)).array().items():
        cid,body=fields(node)
        if cid!=0x41168b:raise FormatError('Unreviewed effect feature condition class')
        name=body[40].string()
        for key,kind in ((41,'feature_static'),(42,'feature_dynamic')):
            text=body[key].string() if key in body else ''
            if text:conditions.append({'kind':kind,'index':index,'name':name,'source':text})
    for index,node in effect[51].array().items():
        cid,body=fields(node)
        if cid!=0x41166a:raise FormatError('Unreviewed effect pass class')
        conditions.append({'kind':'pass_static','index':index,'name':body[0].string(),'source':body[1].string() if 1 in body else ''})
    if len(conditions)>256:raise FormatError('Material condition count exceeds 256')
    return variables,unsupported,conditions


def jobs_for(variables,conditions,dynamic_defaults=False):
    rows=[];jobs=[]
    for item in conditions:
        row=dict(item)
        if item['kind']=='feature_dynamic' and not dynamic_defaults:
            row.update(state='not_evaluated',reason='requires_draw_context; material defaults are not draw-time values')
        else:
            try:
                tree=condition_syntax.parse(item['source']);needed=condition_syntax.names(tree);missing=sorted(needed-set(variables))
                if missing:raise FormatError('Unavailable scalar condition variables: '+', '.join(missing))
                job={'source':item['source'],'normalized':condition_syntax.normalized(tree),'variables':{n:variables[n] for n in sorted(needed)}}
                native_conditions.validate_jobs([job]);row.update(state='evaluated',job=len(jobs),scope='material_default_what_if' if item['kind']=='feature_dynamic' else 'loaded_material_condition')
                jobs.append(job)
            except FormatError as exc:row.update(state='not_evaluated',reason=str(exc))
        rows.append(row)
    if len(jobs)>128:raise FormatError('Material condition jobs exceed 128')
    return rows,jobs


def program_candidates(effect,rows,pass_index,stage,require=(),forbid=()):
    decoded=effect_programs.decode(effect['file'],effect['locator']);grouped=defaultdict(list)
    for row in rows:
        if row['kind'].startswith('feature_'):grouped[row['name'].casefold()].append(row)
    known={name:group[0]['truth'] for name,group in grouped.items()
           if all(r['state']=='evaluated' for r in group) and len({r['truth'] for r in group})==1}
    matches=[r for r in decoded['rows'] if r['pass']==pass_index and r['stage']==stage]
    universe={name.casefold() for r in matches for name in r['enabled_features']}
    wanted={n.casefold() for n in require};excluded={n.casefold() for n in forbid}
    if wanted&excluded or any((n in known and not known[n]) for n in wanted) or any((n in known and known[n]) for n in excluded):
        raise FormatError('Explicit query features conflict with each other or computed conditions')
    wanted|={n for n,value in known.items() if value and n in universe};excluded|={n for n,value in known.items() if not value and n in universe}
    selected=[]
    for row in matches:
        enabled={n.casefold() for n in row['enabled_features']}
        if not wanted<=enabled or excluded&enabled:continue
        copied={k:row[k] for k in ('pass','pass_name','stage','lookup','program_ordinal','program_sha256','descriptor_key','enabled_features','duplicate_of')}
        selected.append(copied)
    return {'pass':pass_index,'stage':stage,'table_rows':len(matches),'candidate_count':len(selected),'candidates':selected,
            'required':sorted(wanted),'forbidden':sorted(excluded),'computed_conditions':known,'explicit_require':list(require),'explicit_forbid':list(forbid),
            'true_conditions_not_represented_in_table':sorted(n for n,v in known.items() if v and n not in universe),
            'opaque_descriptor_prefix_bytes':32,'renderer_selection_executed':False,
            'scope':'Conditional table query under loaded material/default values and explicit feature assumptions. Features absent from this table are not forced; unresolved/duplicate dynamic conditions and opaque key bytes remain unresolved. Not the renderer-selected program.'}


def check(path,locator,game,libraries=(),cache_dir=Path('.aow4mod/native'),timeout=120,dynamic_defaults=False,programs=False,pass_index=0,stage=1,require=(),forbid=()):
    path=Path(path).resolve();proof=material_links.check(path,[locator],game,libraries,cache_dir,timeout)
    if proof['comparison']['material_link_issue_count']:raise FormatError('Material conditions require a resolved, independently verified effect')
    row=proof['materials'][0];material=Path(row['artifacts']['before']['file']).read_bytes();effect_body=Path(row['target_artifact']['file']).read_bytes()
    context=proof['context'][row['expected']['context']];effect={'file':context['file'],'sha256':context['sha256'],'locator':row['expected']['identity']['locator']}
    variables,unsupported,conditions=source_data(material,effect_body);rows,jobs=jobs_for(variables,conditions,dynamic_defaults)
    result=native.run_worker(game,'condition-eval',{'jobs':jobs},cache_dir,timeout) if jobs else None
    if result:
        if native.public_binding(result['binding'])!=proof['binding']:raise FormatError('Condition backend changed after material capture')
        for row in rows:
            if row['state']=='evaluated':row.update(value=result['payload']['results'][row['job']]['value'],truth=result['payload']['results'][row['job']]['truth'])
    else:
        if any(r['state']=='evaluated' for r in rows):raise FormatError('Condition selection has no native evaluation')
    report={'format':FORMAT,'source':str(path),'source_sha256':file_hash(path),'material':locator,'effect':effect,'binding':proof['binding'],
            'source_proof':proof,'variables':variables,'unsupported_parameters':unsupported,'conditions':rows,'native_result':result,
            'dynamic_material_defaults_requested':bool(dynamic_defaults),'renderer_selection_executed':False,'shader_variant_certified':False,
            'diagnostics':proof['diagnostics']+(native_validation.diagnostics(result['run']) if result else []),
            'coverage':'Official TEExpression and legitimate variable mapping evaluate loaded scalar material conditions, with an independent float32 grammar check. Dynamic defaults are optional what-if input. No renderer draw context, final key selection, GPU upload or rendering certification.'}
    if programs:report['program_query']=program_candidates(effect,rows,pass_index,stage,require,forbid)
    report['summary']=validate(report);return report


def validate(report):
    if report.get('format')!=FORMAT:raise FormatError('Unsupported material condition report')
    proof=report['source_proof']
    if proof['binding']!=report['binding'] or material_links.assess(proof)['material_link_issue_count']:raise FormatError('Material condition source/effect proof differs')
    source=proof['materials'][0];material=Path(source['artifacts']['before']['file']).read_bytes();effect=Path(source['target_artifact']['file']).read_bytes()
    target=source['expected'];ctx=proof['context'][target['context']]
    if (report['source']!=proof['source'] or report['source_sha256']!=proof['source_sha256']
            or report['material'].casefold()!=source['locator'].casefold()
            or report['effect']!={'file':ctx['file'],'sha256':ctx['sha256'],'locator':target['identity']['locator']}):
        raise FormatError('Material condition source/effect identity differs')
    variables,unsupported,conditions=source_data(material,effect);rows,jobs=jobs_for(variables,conditions,report['dynamic_material_defaults_requested'])
    if variables!=report['variables'] or unsupported!=report['unsupported_parameters']:raise FormatError('Condition scalar input values/types differ')
    result=report.get('native_result')
    if bool(jobs)!=bool(result):raise FormatError('Condition native job coverage differs')
    if result:
        if (native.public_binding(result['binding'])!=report['binding'] or result['operation']!='condition-eval' or result['params']!={'jobs':jobs}):raise FormatError('Condition native request/binding differs')
        native_conditions.validate_payload(result['payload'],result['params'])
        for row in rows:
            if row['state']=='evaluated':row.update(value=result['payload']['results'][row['job']]['value'],truth=result['payload']['results'][row['job']]['truth'])
    if rows!=report['conditions'] or report.get('renderer_selection_executed') is not False or report.get('shader_variant_certified') is not False:
        raise FormatError('Condition results/scope differ')
    if 'program_query' in report:
        q=report['program_query'];expected=program_candidates(report['effect'],rows,q['pass'],q['stage'],q.get('explicit_require',()),q.get('explicit_forbid',()))
        if q!=expected:raise FormatError('Conditional program table query differs')
    diagnostics=[d for run in proof['runs'] for d in native_validation.diagnostics(run)]
    if diagnostics!=proof['diagnostics']:raise FormatError('Condition source diagnostics differ from raw logs')
    diagnostics+=native_validation.diagnostics(result['run']) if result else []
    if diagnostics!=report['diagnostics']:raise FormatError('Condition diagnostic scope differs')
    summary={'conditions':len(rows),'evaluated':len(jobs),'not_evaluated':sum(r['state']!='evaluated' for r in rows),
            'true':sum(r.get('truth') is True for r in rows),'false':sum(r.get('truth') is False for r in rows),
            'renderer_initialized':result['payload'].get('renderer_initialized') if result else None,'renderer_selection_executed':False,'shader_variant_certified':False}
    if 'summary' in report and report['summary']!=summary:raise FormatError('Condition summary differs')
    return summary
