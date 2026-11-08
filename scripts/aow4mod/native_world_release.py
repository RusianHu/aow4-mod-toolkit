"""Frozen known-world identity/object/cell evidence, rechecked without the DLL."""
import json
from pathlib import Path
import shutil
import uuid

from . import map_entities,map_world_entities,native_map_release,native_validation,native_world_entities
from .core import FormatError
from .filesystem import file_hash,reject_reparse,save_record

FORMAT='aow4mod-native-world-release/1'
INDEX_FORMAT='aow4mod-native-world-index/1'
BATCH_SIZE=256
MAX_FROZEN_BYTES=512*1024*1024
MAX_LOG_BYTES=64*1024*1024


def selections(items):
    return [items[i:i+BATCH_SIZE] for i in range(0,len(items),BATCH_SIZE)] or [[]]


def assess(detail,source,content,root):
    source=Path(source);root=Path(root).resolve()
    if detail.get('format')!=FORMAT or detail.get('source',{}).get('sha256')!=file_hash(source):
        raise FormatError('Frozen world evidence source/format differs')
    libraries,packs=native_map_release.context(content)
    lc=native_map_release.relative_context(libraries,content);pc=native_map_release.relative_context(packs,content)
    if detail.get('context_libraries')!=lc or detail.get('context_packs')!=pc:
        raise FormatError('Frozen world lookup context differs')
    path=native_map_release.artifact(root,detail['index'])
    if path.name!='world-index.json':raise FormatError('Frozen world index name differs')
    index=json.loads(path.read_text(encoding='utf-8'))
    items=map_world_entities.records(source.read_bytes());map_world_entities.require_consistent(items)
    expected=map_world_entities.identity_summary(items)
    if (index.get('format')!=INDEX_FORMAT or index.get('source_sha256')!=file_hash(source) or
            index.get('identity_summary')!=expected or index.get('binding')!=detail.get('binding')):
        raise FormatError('Frozen world identity index differs from the payload')
    parts=selections(items);batches=index.get('batches')
    if not isinstance(batches,list) or len(batches)!=len(parts):raise FormatError('Frozen world batch coverage differs')
    objects=path.parent/'objects';total=path.stat().st_size;seen={path};diagnostics=[];rows=[]
    for number,(part,batch) in enumerate(zip(parts,batches)):
        if (batch.get('selection')!=[f'{r["id"]:08X}' for r in part] or batch.get('identity_summary')!=expected or
                batch.get('binding')!=detail['binding'] or batch.get('context_libraries')!=lc or batch.get('context_packs')!=pc or
                batch.get('source_sha256')!=file_hash(source) or batch.get('objects_evaluated')!=bool(part)):
            raise FormatError('Frozen world batch selection/source/context differs')
        log=native_map_release.artifact(root,batch['log'])
        if log!=path.parent/'batches'/f'{number:04d}'/'stdout.log' or log.stat().st_size>MAX_LOG_BYTES:
            raise FormatError('Frozen world log role/name/size differs')
        found=native_validation.diagnostics(log.parent)
        if batch.get('diagnostics')!=found:raise FormatError('Frozen world diagnostics differ from the raw log')
        diagnostics.extend(found)
        if log not in seen:total+=log.stat().st_size;seen.add(log)
        observed=batch.get('entities')
        if not isinstance(observed,list) or len(observed)!=len(part):raise FormatError('Frozen world object coverage differs')
        if part:
            reject_reparse(objects)
            for declared,row in zip(part,observed):
                name=f'entity-{declared["ordinal"]:06d}.bin'
                if row.get('file')!=name:raise FormatError('Frozen world object filename differs')
                witnesses=[(name,row.get('sha256'))]
                if declared['location']['origin']=='cell':
                    loc=declared['location'];cell_name=f'cell-{loc["grid_ordinal"]:02d}-{loc["cell_index"]:06d}.bin'
                    if row.get('cell_file')!=cell_name:raise FormatError('Frozen world cell filename differs')
                    witnesses.append((cell_name,row.get('cell_sha256')))
                for name,digest in witnesses:
                    relative=(objects/name).relative_to(root).as_posix()
                    artifact=native_map_release.artifact(root,{'file':relative,'sha256':digest})
                    if artifact.stat().st_size>16*1024*1024:raise FormatError('Frozen world object/cell exceeds 16 MiB')
                    if artifact not in seen:total+=artifact.stat().st_size;seen.add(artifact)
            assessed=native_world_entities.validate_observations(part,observed,objects)
            if assessed!=observed:raise FormatError('Frozen world observation summaries differ')
            rows.extend(assessed)
        if total>MAX_FROZEN_BYTES:raise FormatError('Frozen world evidence exceeds 512 MiB')
    if detail.get('diagnostics')!=diagnostics:raise FormatError('Frozen world diagnostic coverage differs')
    issues=[]
    for source_row,row in zip(items,rows):
        fields=source_row['fields'].get(map_entities.HEX_SCOPE)
        if fields is not None:
            values=fields.mapping();direction=values[1].uint(1) if 1 in values else 2
            if row['direction_status']!=0 or row['direction']!=direction:
                issues.append({'id':row['id'],'issue':'direction_not_independently_matched'})
        elif row['direction_status']!=0:issues.append({'id':row['id'],'issue':'direction_unavailable'})
    if not items:issues.append({'issue':'no_known_entities_evaluated'})
    return {'entity_count':len(items),'cell_entity_count':sum(r['location']['origin']=='cell' for r in items),
            'batch_count':len(batches),'identity_summary':expected,'objects_evaluated':bool(items),
            'serialized_world_data_equal':all(r['source_data_equal'] and r.get('source_cell_data_equal') is not False for r in rows) if items else None,
            'issue_count':len(issues),'issues':issues,
            'coverage':'Known FZones/cell-holder identity projection, complete selected object/cell bytes, positions/directions and raw diagnostic logs in fixed complete batches. Other managers, registered-world allocation, rendering/routing and gameplay remain unverified.'}


def collect(source,content,game,unit,root,cache,timeout):
    source=Path(source);root=Path(root).resolve();unit=Path(unit)
    items=map_world_entities.records(source.read_bytes());map_world_entities.require_consistent(items)
    expected=map_world_entities.identity_summary(items);source_hash=file_hash(source)
    libraries,packs=native_map_release.context(content)
    lc=native_map_release.relative_context(libraries,content);pc=native_map_release.relative_context(packs,content)
    folder=unit/'world';folder.mkdir();objects=folder/'objects';objects.mkdir();batches=[];diagnostics=[];binding=None;total=0
    for number,part in enumerate(selections(items)):
        output=Path(cache)/'release-worlds'/uuid.uuid4().hex
        report=native_world_entities.run(source,game,output,[r['id'] for r in part],cache,timeout,
            libraries=[r['file'] for r in libraries],packs=[r['file'] for r in packs])
        if (report.get('format')!='aow4mod-native-world-entities/1' or report.get('sha256')!=source_hash or
                report.get('identity_summary')!=expected or report.get('entity_count')!=len(items) or
                report.get('objects_evaluated')!=bool(part) or
                native_map_release.relative_context(report.get('context_libraries',[]),content)!=lc or
                native_map_release.relative_context(report.get('context_packs',[]),content)!=pc):
            raise FormatError('Native world batch source/coverage/context differs')
        if binding is None:binding=report['binding']
        elif report['binding']!=binding:raise FormatError('Native world backend changed between batches')
        observed=native_world_entities.validate_observations(part,report['entities'],report['output'])
        if observed!=report['entities']:raise FormatError('Native world batch preservation summary differs')
        names={r['file'] for r in observed}|{r['cell_file'] for r in observed if r.get('cell_file')}
        for name in sorted(names):
            source_file=Path(report['output'])/name;reject_reparse(source_file);target=objects/name
            if target.exists():
                if file_hash(target)!=file_hash(source_file):raise FormatError('Native world shared cell/object differs between batches')
                continue
            if source_file.stat().st_size>16*1024*1024:raise FormatError('Native world object/cell exceeds 16 MiB')
            total+=source_file.stat().st_size
            if total>MAX_FROZEN_BYTES:raise FormatError('Frozen world evidence exceeds 512 MiB')
            shutil.copyfile(source_file,target)
            if file_hash(target)!=file_hash(source_file):raise FormatError('Native world witness changed while freezing')
        log_source=Path(report['run'])/'stdout.log';reject_reparse(log_source)
        if not 1<=log_source.stat().st_size<=MAX_LOG_BYTES:raise FormatError('Native world raw log is empty or exceeds 64 MiB')
        total+=log_source.stat().st_size
        if total>MAX_FROZEN_BYTES:raise FormatError('Frozen world evidence exceeds 512 MiB')
        log_dir=folder/'batches'/f'{number:04d}';log_dir.mkdir(parents=True);log=log_dir/'stdout.log';shutil.copyfile(log_source,log)
        if file_hash(log)!=file_hash(log_source):raise FormatError('Native world log changed while freezing')
        found=native_validation.diagnostics(log_dir)
        if found!=report['diagnostics']:raise FormatError('Native world report differs from its raw diagnostics')
        diagnostics.extend(found)
        batches.append({'selection':[f'{r["id"]:08X}' for r in part],'source_sha256':source_hash,'binding':binding,
            'identity_summary':report['identity_summary'],'context_libraries':lc,'context_packs':pc,'objects_evaluated':bool(part),
            'entities':observed,'log':{'file':log.relative_to(root).as_posix(),'sha256':file_hash(log)},'diagnostics':found,'run':report['run']})
    path=folder/'world-index.json'
    save_record(path,{'format':INDEX_FORMAT,'source_sha256':source_hash,'binding':binding,'identity_summary':expected,'batches':batches})
    if path.stat().st_size>256*1024*1024 or total+path.stat().st_size>MAX_FROZEN_BYTES:raise FormatError('Frozen world index/total exceeds bounds')
    if file_hash(source)!=source_hash:raise FormatError('World payload changed during batch collection')
    detail={'format':FORMAT,'source':{'file':source.relative_to(content).as_posix(),'sha256':source_hash},'binding':binding,
            'index':{'file':path.relative_to(root).as_posix(),'sha256':file_hash(path)},
            'context_libraries':lc,'context_packs':pc,'diagnostics':diagnostics}
    detail['comparison']=assess(detail,source,content,root)
    return detail,folder/'world.json',path
