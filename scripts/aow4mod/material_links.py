"""Native material/effect pointers with independently loaded target-object witnesses."""
from pathlib import Path
import ctypes as c

from . import clb_imports,content_libraries,native,native_objects,native_validation
from .core import FormatError,Node,sha
from .filesystem import file_hash
from .native_overlays import locked_inputs

MATERIAL='0x0041166F'
EFFECT='0x00411669'
MAX_OBJECT=64*1024*1024
MAX_TOTAL=512*1024*1024


def members(path):
    return [m['identity'] for m in content_libraries.read(path).members if m['identity']['type_id']==MATERIAL]


def references(library,selected):
    result=[]
    for identity in selected:
        fields=library.members[identity['ordinal']]['fields']
        link=fields[50].mapping() if 50 in fields else {}
        result.append(library.string(link[20]) if 20 in link else '')
    return result


def context(source,selected,provided=(),game=None,additional_locators=()):
    """Resolve only the bounded declaration/selected-effect closure; no order guessing."""
    source=Path(source).resolve();available={};game_files=None;libraries={};records=[];slots={};total=0
    for value in [source,*provided]:
        path=Path(value).resolve();available.setdefault(path.name.casefold(),[])
        if path not in available[path.name.casefold()]:available[path.name.casefold()].append(path)
    def resolve(name):
        nonlocal game_files
        clb_imports.identifier(name);paths=available.get(name.casefold(),[])
        if not paths and game is not None:
            if game_files is None:
                game_files={}
                for p in Path(game).rglob('*.clb'):game_files.setdefault(p.name.casefold(),[]).append(p.resolve())
            paths=game_files.get(name.casefold(),[])
        if not paths:raise FormatError('Material CLB context dependency is missing: '+name)
        hashes={file_hash(p) for p in paths}
        if len(hashes)!=1:raise FormatError('Ambiguous material CLB dependency: '+name)
        return paths[0]
    def add(path,depth=0):
        nonlocal total
        path=Path(path).resolve()
        if path in slots:return slots[path]
        if depth>32 or len(records)>=64 or path.stat().st_size>256*1024*1024:raise FormatError('Material CLB context exceeds bounds')
        total+=path.stat().st_size
        if total>MAX_TOTAL:raise FormatError('Material CLB context exceeds 512 MiB')
        lib=content_libraries.read(path);digest=file_hash(path)
        for old in records:
            if old['namespace'].casefold()==lib.name.casefold() and old['sha256']!=digest:raise FormatError('Ambiguous material library namespace')
        dependencies=clb_imports.names(lib);slot=len(records);slots[path]=slot;libraries[slot]=lib
        records.append({'file':str(path),'sha256':digest,'namespace':lib.name,'dependencies':dependencies,'member_count':len(lib.members)})
        for name in dependencies:add(resolve(name),depth+1)
        return slot
    source_slot=add(source);targets=[]
    for locator in references(libraries[source_slot],selected):
        if not locator:targets.append(None);continue
        match=content_libraries.LOCATOR.fullmatch(locator)
        if not match:raise FormatError('Unsupported material effect locator')
        namespace=match[1]
        path=source if namespace.casefold()==libraries[source_slot].name.casefold() else resolve(namespace+'.clb')
        slot=add(path);found=[m['identity'] for m in libraries[slot].members if (m['identity']['locator'] or '').casefold()==locator.casefold()]
        if len(found)>1:raise FormatError('Ambiguous material effect identity')
        targets.append({'context':slot,'identity':found[0] if found else None})
    for locator in additional_locators:
        match=content_libraries.LOCATOR.fullmatch(locator)
        if not match:raise FormatError('Unsupported additional material target locator')
        path=source if match[1].casefold()==libraries[source_slot].name.casefold() else resolve(match[1]+'.clb')
        add(path)
    return records,targets


def artifacts(report):
    for row in report['materials']:
        yield from row['artifacts'].values()
        if row.get('target_artifact'):yield row['target_artifact']
    for row in report['objects']:yield row['artifact']


def issue_list(row):
    issues=[];expected=row['expected'];identity=expected['identity'] if expected else None
    if row['status']&3!=3:issues.append('material_not_connected_and_activated')
    if not row['declared_locator']:issues.append('material_effect_empty')
    if not identity:issues.append('effect_missing_from_bound_context')
    elif identity['type_id']!=EFFECT:issues.append('declared_effect_target_wrong_type')
    if not row['resolved']:issues.append('material_effect_not_resolved')
    elif row['actual_cid']!=EFFECT:issues.append('resolved_effect_wrong_type')
    elif not row['target_ready']:issues.append('effect_not_connected_and_activated')
    if row['resolved'] and not row['same_context_member']:issues.append('effect_pointer_not_expected_member')
    if not row['resolution_stable']:issues.append('effect_resolution_changed_during_inspection')
    if not row['comparison']['data_equal']:issues.append('material_data_changed')
    if not row['source_comparison']['data_equal']:issues.append('material_differs_from_independent_file_object')
    if row.get('target_comparison') and not row['target_comparison']['data_equal']:issues.append('effect_differs_from_independent_file_object')
    return issues


def _assess(report):
    objects={(r['context'],r['identity']['locator']):r for r in report['objects']}
    if len(objects)!=len(report['objects']):raise FormatError('Duplicate independent material effect witness')
    for row in report['materials']:
        before,after=[Path(row['artifacts'][k]['file']).read_bytes() for k in ('before','after')]
        for key,raw in (('before',before),('after',after)):
            if sha(raw)!=row['artifacts'][key]['sha256']:raise FormatError('Material object witness hash differs')
        comparison=native_objects.compare_bodies(before,after)
        if comparison!=row['comparison']:raise FormatError('Material object comparison differs')
        fields=Node(before,0,len(before)).mapping();link=fields[50].mapping() if 50 in fields else {}
        declared=link[20].string() if 20 in link else ''
        if fields[20].string()!=row['locator'] or declared!=row['declared_locator']:raise FormatError('Material object locator/link differs')
        source_object=objects.get((0,row['locator']))
        if source_object is None:raise FormatError('Independent material source object is missing')
        raw=Path(source_object['artifact']['file']).read_bytes()
        if sha(raw)!=source_object['artifact']['sha256'] or Node(raw,0,len(raw)).mapping()[20].string()!=row['locator']:raise FormatError('Independent material source differs')
        if native_objects.compare_bodies(before,raw)!=row['source_comparison']:raise FormatError('Independent material source comparison differs')
        expected=row['expected'];identity=expected['identity'] if expected else None
        if row['resolved'] and row['actual_cid']==EFFECT:
            if not row.get('target_artifact') or not identity or identity['type_id']!=EFFECT:raise FormatError('Resolved effect witness lacks its correct bound target')
            target=objects.get((expected['context'],identity['locator']))
            if target is None:raise FormatError('Independent effect witness is missing')
            actual=Path(row['target_artifact']['file']).read_bytes();independent=Path(target['artifact']['file']).read_bytes()
            if sha(actual)!=row['target_artifact']['sha256'] or sha(independent)!=target['artifact']['sha256']:raise FormatError('Effect object witness hash differs')
            for raw in (actual,independent):
                if Node(raw,0,len(raw)).mapping()[20].string()!=identity['locator']:raise FormatError('Effect object identity differs')
            comparison=native_objects.compare_bodies(actual,independent)
            if row.get('target_comparison')!=comparison:raise FormatError('Independent effect comparison differs')
        elif row.get('target_artifact') or row.get('target_comparison'):raise FormatError('Unexpected effect artifact for an unresolved/wrong-type link')
        if row.get('issues')!=issue_list(row):raise FormatError('Material link issues differ from observed witnesses')
    expected={(0,r['locator']) for r in report['materials']}|{(r['expected']['context'],r['expected']['identity']['locator']) for r in report['materials'] if r['expected'] and r['expected']['identity'] and r['expected']['identity']['type_id']==EFFECT}
    if set(objects)!=expected:raise FormatError('Independent effect object coverage differs')
    return {'material_count':len(report['materials']),'effect_object_count':sum(r['identity']['type_id']==EFFECT for r in report['objects']),
            'material_data_equal':all(r['comparison']['data_equal'] and r['source_comparison']['data_equal'] for r in report['materials']),
            'effect_data_equal':all(r['target_comparison']['data_equal'] for r in report['materials'] if r.get('target_comparison')) if any(r.get('target_comparison') for r in report['materials']) else None,
            'effect_objects_evaluated':bool(report['materials']) and all(r.get('target_artifact') is not None for r in report['materials']),
            'material_link_issue_count':sum(len(r['issues']) for r in report['materials'])}


def assess(report):
    try:return _assess(report)
    except (KeyError,IndexError,TypeError) as exc:raise FormatError('Malformed material link witnesses') from exc


def check(path,locators,game,libraries=(),cache_dir=Path('.aow4mod/native'),timeout=120):
    path,game=Path(path).resolve(),Path(game).resolve();library=content_libraries.read(path);available=members(path)
    if not isinstance(locators,list) or not 1<=len(locators)<=16 or any(not isinstance(x,str) for x in locators) or len({x.casefold() for x in locators})!=len(locators):raise FormatError('Select 1..16 nonduplicate material locators')
    selected=[]
    for locator in locators:
        found=[m for m in available if m['locator'].casefold()==locator.casefold()]
        if len(found)!=1:raise FormatError('Select an existing TREEffectMaterialResource')
        selected.append(found[0])
    contexts,targets=context(path,selected,libraries,game)
    if any(t and t['identity'] and t['identity']['type_id']!=EFFECT for t in targets):
        raise FormatError('Material effect reference targets a non-TREEffectResource member')
    language=game/'Language/EN/EN.MO'
    params={'path':str(path),'sha256':file_hash(path),'language':str(language),'language_sha256':file_hash(language),
            'materials':selected,'context':contexts,'targets':targets,'library_name':library.name,
            'libraries':native.library_context([r['file'] for r in contexts])}
    cache=native.check_cache_directory(cache_dir,game,[Path(r['file']).parent for r in contexts])
    with locked_inputs([Path(r['file']) for r in contexts]+[language,game/native.DLL]):
        first=native.run_worker(game,'material-links',params,cache,timeout);detail=first['payload'];objects=[];runs=[first['run']]
        wanted={0:list(selected)}
        for target in targets:
            if target and target['identity'] and target['identity']['type_id']==EFFECT:
                group=wanted.setdefault(target['context'],[])
                if target['identity'] not in group:group.append(target['identity'])
        for slot,identities in wanted.items():
            p={'path':contexts[slot]['file'],'sha256':contexts[slot]['sha256'],'language':str(language),'language_sha256':params['language_sha256'],
               'selection':identities,'library_name':contexts[slot]['namespace'],'member_count':contexts[slot]['member_count'],'libraries':params['libraries']}
            result=native.run_worker(game,'material-objects',p,cache,timeout);runs.append(result['run'])
            if native.public_binding(result['binding'])!=native.public_binding(first['binding']):raise FormatError('Material backend changed between independent processes')
            objects.extend({'context':slot,**row} for row in result['payload']['objects'])
        if any(file_hash(r['file'])!=r['sha256'] for r in contexts):raise FormatError('Material CLB context changed')
    report={'format':'aow4mod-native-material-links/1','source':str(path),'source_sha256':params['sha256'],'library':library.name,
            'selection':selected,'context':contexts,'targets':targets,'binding':native.public_binding(first['binding']),
            'materials':detail['materials'],'objects':objects,'runs':runs,'diagnostics':[d for run in runs for d in native_validation.diagnostics(run)],
            'coverage':'Actual material/effect pointer identity/type, stable material bytes and full effect bytes from an independent file-loading process. No shader feature selection, complete texture closure or rendering certification.'}
    by_target={(r['context'],r['identity']['locator']):r for r in objects}
    for row in report['materials']:
        target=row['expected'];identity=target['identity'] if target else None
        row['target_comparison']=native_objects.compare_bodies(Path(row['target_artifact']['file']).read_bytes(),Path(by_target[(target['context'],identity['locator'])]['artifact']['file']).read_bytes()) if row.get('target_artifact') else None
        row['source_comparison']=native_objects.compare_bodies(Path(row['artifacts']['before']['file']).read_bytes(),Path(by_target[(0,row['locator'])]['artifact']['file']).read_bytes())
        row['issues']=issue_list(row)
    report['comparison']=assess(report);report['status']='review_required' if report['diagnostics'] or report['comparison']['material_link_issue_count'] else 'material_effects_resolved'
    return report


def _cid(api,pointer):
    code=api.memory(api.qword(api.qword(pointer)+api.profile['class_getter_vtable_offset']),6)
    if code[:1]!=b'\xb8' or code[5:]!=b'\xc3':raise FormatError('Unreviewed native material/effect class getter')
    return f'0x{int.from_bytes(code[1:5],"little"):08X}'


def _array(api,library,count):
    profile=api.profile['material_links']
    if _cid(api,library)!='0x00410060' or not 1<=count<=100000 or int.from_bytes(api.memory(library+profile['count_offset'],4),'little')!=count:raise FormatError('Native material library type/count differs')
    return api.qword(library+profile['array_offset'])


def object_worker(api,content,params,run_dir):
    library=api.qword(content+api.profile['clb_library_offset']);array=_array(api,library,params['member_count']);folder=Path(run_dir)/'native-material';folder.mkdir();objects=[];total=0
    for i,identity in enumerate(params['selection']):
        pointer=api.qword(array+identity['ordinal']*8)
        if identity['type_id'] not in (MATERIAL,EFFECT,'0x0041003D') or _cid(api,pointer)!=identity['type_id']:raise FormatError('Independent material target type differs')
        raw=api.serialize_object(pointer,16*1024*1024 if identity['type_id']==MATERIAL else MAX_OBJECT);total+=len(raw)
        if total>256*1024*1024:raise FormatError('Independent material objects exceed 256 MiB')
        file=folder/f'object-{i}.bin';file.write_bytes(raw);objects.append({'identity':identity,'artifact':{'file':str(file),'sha256':sha(raw)}})
    return {'objects':objects,'library_name':params['library_name']}


def worker(api,content,params,run_dir,open_library):
    profile=api.profile['material_links'];own=api.qword(content+api.profile['clb_library_offset']);array=_array(api,own,params['context'][0]['member_count'])
    getter=c.CFUNCTYPE(c.c_void_p,c.c_void_p)(api.dll._handle+profile['get_resource_rva']);folder=Path(run_dir)/'native-material';folder.mkdir();rows=[];cache={};total=0
    def artifact(pointer,name,maximum=16*1024*1024):
        nonlocal total
        raw=api.serialize_object(pointer,maximum);total+=len(raw)
        if total>256*1024*1024:raise FormatError('Material link objects exceed 256 MiB')
        file=folder/name;file.write_bytes(raw);return {'file':str(file),'sha256':sha(raw)}
    pending=[]
    # Observe every source link before explicitly opening any target library.
    for index,(identity,expected) in enumerate(zip(params['materials'],params['targets'])):
        material=api.qword(array+identity['ordinal']*8)
        if _cid(api,material)!=MATERIAL:raise FormatError('Native material class differs')
        before=artifact(material,f'{index}-before.bin');link=material+profile['effect_link_offset'];pointer=getter(link)
        actual_cid=_cid(api,pointer) if pointer else None;target_artifact=None
        if pointer and actual_cid==EFFECT:
            if pointer not in cache:cache[pointer]=artifact(pointer,f'linked-{len(cache)}.bin',MAX_OBJECT)
            target_artifact=cache[pointer]
        pending.append((index,identity,expected,material,before,link,pointer,actual_cid,target_artifact))
    for index,identity,expected,material,before,link,pointer,actual_cid,target_artifact in pending:
        expected_pointer=None
        if expected and expected['identity']:
            record=params['context'][expected['context']];native_library=own if expected['context']==0 else open_library(record['file'])
            expected_array=_array(api,native_library,record['member_count']);expected_pointer=api.qword(expected_array+expected['identity']['ordinal']*8)
        after=artifact(material,f'{index}-after.bin');fields=Node(Path(before['file']).read_bytes(),0,Path(before['file']).stat().st_size).mapping();declared=fields[50].mapping()[20].string() if 50 in fields and 20 in fields[50].mapping() else ''
        rows.append({'locator':identity['locator'],'status':api.memory(material+0x24,1)[0],'declared_locator':declared,'expected':expected,
                     'resolved':bool(pointer),'actual_cid':actual_cid,'target_ready':bool(pointer and actual_cid==EFFECT and api.memory(pointer+0x24,1)[0]&3==3),
                     'same_context_member':bool(pointer and pointer==expected_pointer),'resolution_stable':getter(link)==pointer,
                     'artifacts':{'before':before,'after':after},'comparison':native_objects.compare_bodies(Path(before['file']).read_bytes(),Path(after['file']).read_bytes()),
                     'target_artifact':target_artifact})
    return {'materials':rows,'library_name':params['library_name'],'context':params['context']}
