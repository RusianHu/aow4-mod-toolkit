"""Freeze and recheck scoped ACP application material inside a release."""
from pathlib import Path
import shutil

from . import catalog,native_overlays,projects,native_dependencies,native_context
from .core import FormatError,read_rpk
from .filesystem import file_hash,reject_reparse

LEGACY_FORMAT='aow4mod-native-overlay-release/1'
FORMAT='aow4mod-native-overlay-release/2'


def resolve_base(pack,game,index=None,content=None,dependencies=(),explicit=None):
    selected={key.casefold():Path(value).resolve() for key,value in (explicit or {}).items()}
    if pack.casefold() in selected:
        path=selected[pack.casefold()]
        if read_rpk(path)[0]['name'].casefold()!=pack.casefold():raise FormatError('Explicit native merge base has the wrong internal pack name')
        return path
    candidates=[]
    if index:
        with catalog.open_existing(index) as db:
            rows=db.execute("SELECT path,sha256 FROM files WHERE origin='game' AND kind='rpk' AND error IS NULL AND package=? COLLATE NOCASE",(pack,)).fetchall()
        for row in rows:
            path=Path(row['path']).resolve()
            if not path.is_relative_to(Path(game).resolve()/'Content') or file_hash(path)!=row['sha256']:
                raise FormatError('Native merge base index is stale or outside the selected game')
            candidates.append(path)
    else:
        for path in (Path(game)/'Content').rglob('*.rpk'):
            try:info,_=read_rpk(path)
            except FormatError:continue
            if info['name'].casefold()==pack.casefold():candidates.append(path.resolve())
    for root in ([content] if content is not None else [])+list(dependencies):
        for row in projects.inspect_project(root)['packs']:
            if row['registered'] and row['name'].casefold()==pack.casefold():candidates.append(Path(row['path']).resolve())
    unique={str(path):path for path in candidates}
    hashes={file_hash(path) for path in unique.values()}
    if len(hashes)!=1:
        raise FormatError('Native merge base is missing or ambiguous; select validation.native_merge_bases for '+pack)
    return next(iter(unique.values()))


def artifact(root,value):
    if not isinstance(value,str) or Path(value).is_absolute() or '..' in Path(value).parts:
        raise FormatError('Frozen overlay material must use release-relative paths')
    path=(Path(root)/value).resolve()
    if Path(root).resolve() not in path.parents:raise FormatError('Frozen overlay material escapes the release')
    reject_reparse(path)
    return path


def assess(detail,content,root,require_context=False):
    if detail.get('format') not in (FORMAT,LEGACY_FORMAT):raise FormatError('Expected frozen native ACP application evidence')
    if require_context and detail.get('format')!=FORMAT:raise FormatError('Native release protocol requires dependency/context evidence')
    source=Path(content)/detail['source']['file']
    if file_hash(source)!=detail['source']['sha256']:raise FormatError('Frozen overlay evidence has a different ACP payload')
    paths={key:artifact(root,value['file']) for key,value in detail['artifacts'].items()}
    for key,path in paths.items():
        if file_hash(path)!=detail['artifacts'][key]['sha256']:raise FormatError('Frozen native overlay artifact changed')
    base=paths['base']
    info,_,records,_,_,targets=native_overlays.collect_records(base,[source])
    if info['name'].casefold()!=detail['pack'].casefold() or info['sha256']!=detail['base_source']['sha256']:
        raise FormatError('Frozen overlay base pack identity/hash differs')
    application={'baseline':{'output_file':str(paths['baseline']),'output_sha256':detail['artifacts']['baseline']['sha256']},
        'steps':[{'file':records[0]['file'],'sha256':records[0]['sha256'],'entries':detail['entries'],
          'mod_data_file':str(paths['mod_data']),'mod_data_sha256':detail['artifacts']['mod_data']['sha256'],
          'output':{'output_file':str(paths['merged']),'output_sha256':detail['artifacts']['merged']['sha256']}}]}
    prepared=base;prep_proof=None
    if detail['format']==FORMAT:
        needs=native_dependencies.needed(records)
        if bool(detail.get('preparation'))!=needs:raise FormatError('Frozen dependency preparation coverage differs')
        if needs:
            prep={'baseline':{'output_file':str(paths['prepare_baseline']),'output_sha256':detail['artifacts']['prepare_baseline']['sha256']},
                  'steps':[{'file':records[0]['file'],'sha256':records[0]['sha256'],'entries':detail['preparation']['entries'][0],
                    'mod_data_file':str(paths['prepare_mod_data_0']),'mod_data_sha256':detail['artifacts']['prepare_mod_data_0']['sha256'],
                    'output':{'output_file':str(paths['prepare_step_0']),'output_sha256':detail['artifacts']['prepare_step_0']['sha256']}}]}
            if len(detail['preparation']['entries'])!=1:raise FormatError('Frozen preparation entry coverage differs')
            prep_proof=native_dependencies.verify(base,records,prep);prepared=paths['prepared']
            if file_hash(prepared)!=file_hash(paths['prepare_step_0']):raise FormatError('Prepared context differs from its collector snapshot')
        all_bundled=[row for row in projects.inspect_project(content)['packs'] if row['registered']]
        if any(row['name'].casefold()==info['name'].casefold() and row['sha256']!=info['sha256'] for row in all_bundled):
            raise FormatError('Bundled native RPK context shadows the frozen base')
        bundled=native_context.bind([row['path'] for row in all_bundled
                                   if row['name'].casefold()!=info['name'].casefold()])
        expected={row['name'].casefold():row['sha256'] for row in bundled};actual={}
        frozen_context=[]
        for row in detail.get('context_packs',[]):
            key=row['name'].casefold();item=detail['artifacts'][row['key']]
            if key in actual or file_hash(paths[row['key']])!=row['sha256'] or item['sha256']!=row['sha256']:
                raise FormatError('Frozen native RPK context identity/hash differs')
            if read_rpk(paths[row['key']])[0]['name']!=row['name']:raise FormatError('Frozen native RPK internal name differs')
            if key in expected and expected[key]!=row['sha256']:raise FormatError('Frozen context differs from its bundled RPK')
            actual[key]=row['sha256']
            frozen_context.append({'file':paths[row['key']],'sha256':row['sha256']})
        if expected.keys()-actual.keys():raise FormatError('Frozen bundled RPK context coverage is incomplete')
        native_context.check_frozen_cycles(prepared.read_bytes(),frozen_context)
    comparison=native_overlays.verify_snapshots(prepared,records,application,targets)
    if prep_proof is not None:
        comparison['dependency_preparation']=prep_proof
        comparison['scoped_application_witness_matched'] &= prep_proof['dependency_preparation_matched']
    for step in comparison['steps']:
        step['mod']=detail['source']['file'];step['output_file']=detail['artifacts']['merged']['file']
    return comparison


def collect(acp,pack,content,game,unit,release_root,cache,timeout,index=None,dependencies=(),explicit=None):
    base=resolve_base(pack,game,index,content,dependencies,explicit)
    report=native_overlays.run(base,[acp],game,unit/'application',cache,timeout,
        libraries=list(Path(content).rglob('*.clb')),
        packages=[row['path'] for dep in dependencies for row in projects.inspect_project(dep)['packs'] if row['registered']])
    if report['binding'] is None:raise FormatError('Native overlay application has no backend binding')
    base_copy=unit/'base.rpk';shutil.copyfile(base,base_copy)
    if file_hash(base_copy)!=report['base']['sha256']:raise FormatError('Native overlay base changed before freezing')
    materials={'base':{'file':base_copy.relative_to(release_root).as_posix(),'sha256':file_hash(base_copy)}}
    for key,kind in [('baseline','baseline'),('merged','step'),('mod_data','mod_data')]:
        matches=[row for row in report['artifacts'] if row['kind']==kind]
        if len(matches)!=1:raise FormatError('Native release overlay expected one scoped application step')
        item=matches[0];path=unit/'application'/item['file']
        materials[key]={'file':path.relative_to(release_root).as_posix(),'sha256':item['sha256']}
    context_packs=[]
    for item in report['artifacts']:
        if item['kind'] not in ('prepared','prepare_baseline','prepare_step','prepare_mod_data','context_pack'):continue
        key=item['kind'] if item['step'] is None else item['kind']+'_'+str(item['step'])
        path=unit/'application'/item['file']
        materials[key]={'file':path.relative_to(release_root).as_posix(),'sha256':item['sha256']}
        if item['kind']=='context_pack':
            rows=[row for row in report.get('context_packs',[]) if row['artifact']==item['file']]
            if len(rows)!=1:raise FormatError('Native context source binding is incomplete')
            context_packs.append({'key':key,'name':rows[0]['name'],'sha256':rows[0]['sha256']})
    detail={'format':FORMAT,'binding':report['binding'],'source':{'file':Path(acp).relative_to(content).as_posix(),'sha256':file_hash(acp)},
            'pack':pack,'base_source':report['base'],'artifacts':materials,'entries':report['proof']['steps'][0]['entries'],
            'preparation':{'entries':report['preparation']['entries']} if report.get('preparation') else None,
            'context_packs':context_packs,
            'diagnostics':report['diagnostics'],'coverage':report['coverage']}
    detail['comparison']=assess(detail,content,release_root)
    return detail,unit/'overlay.json',artifact(release_root,materials['merged']['file'])
