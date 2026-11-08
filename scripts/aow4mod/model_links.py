"""Scoped live native model-to-local-mesh/material link witnesses."""
from pathlib import Path

from . import content_libraries,native,native_objects,native_validation
from .core import FormatError,Node,sha
from .filesystem import file_hash
from .native_overlays import locked_inputs


def select(path,locators):
    if not isinstance(locators,list) or not 1<=len(locators)<=16 or any(not isinstance(x,str) for x in locators):
        raise FormatError('Select 1..16 explicit model locators')
    if len({x.casefold() for x in locators})!=len(locators):raise FormatError('Duplicate model selection')
    if any(not content_libraries.LOCATOR.fullmatch(x) or content_libraries.LOCATOR.fullmatch(x)[2].casefold()!='model' for x in locators):
        raise FormatError('Expected explicit native MODEL locators')
    library=content_libraries.read(path);rows=[m['identity'] for m in library.members];selected=[]
    for locator in locators:
        found=[row for row in rows if (row['locator'] or '').casefold()==locator.casefold()]
        if len(found)!=1 or found[0]['type_id']!='0x0041004B':raise FormatError('Model locator is missing or not TREModelResource: '+locator)
        selected.append(found[0])
    return library,rows,selected


def check(path,locators,game,cache_dir=Path('.aow4mod/native'),timeout=60):
    path,game=Path(path).resolve(),Path(game).resolve()
    if path.stat().st_size>256*1024*1024:raise FormatError('Model library exceeds 256 MiB')
    library,rows,selected=select(path,locators);language=game/'Language/EN/EN.MO'
    params={'path':str(path),'sha256':file_hash(path),'language':str(language),'language_sha256':file_hash(language),
            'models':selected,'members':rows,'library_name':library.name}
    cache_dir=native.check_cache_directory(cache_dir,game,[path.parent])
    with locked_inputs([path,language,game/native.DLL]):
        result=native.run_worker(game,'model-links',params,cache_dir,timeout)
        if file_hash(path)!=params['sha256'] or file_hash(language)!=params['language_sha256']:
            raise FormatError('Model link input changed during native inspection')
    payload=result['payload'];assess(payload)
    diagnostics=native_validation.diagnostics(result['run'])
    return {'format':'aow4mod-native-model-links/1','source':str(path),'source_sha256':params['sha256'],
            'library':library.name,'selection':selected,'binding':native.public_binding(result['binding']),
            'run':result['run'],'models':payload['models'],'diagnostics':diagnostics,
            'status':'review_required' if diagnostics or any(row['issues'] for row in payload['models']) else 'local_links_resolved',
            'coverage':'Actual post-load connection/activation, component readiness, local mesh/effect-material pointer identity/type '
                       'and serialized object witnesses. No second activation call. External shader/texture closure, shader variants, collision and rendering remain unverified.'}


def assess(payload):
    """Recompute serializable facts; transient pointer assertions remain native evidence."""
    for row in payload['models']:
        bodies={}
        for name in ('before','after'):
            item=row['artifacts'][name];raw=Path(item['file']).read_bytes()
            if sha(raw)!=item['sha256']:raise FormatError('Model object witness changed')
            bodies[name]=raw
        comparison=native_objects.compare_bodies(bodies['before'],bodies['after'])
        if comparison!=row['comparison']:raise FormatError('Model serialization comparison receipt differs')
        fields=Node(bodies['before'],0,len(bodies['before'])).mapping()
        if fields[0x14].string()!=row['locator']:raise FormatError('Model object locator differs')
        expected=[]
        for li,lod in fields[0x1f].array().items():
            cid,lod_fields=lod.typed()
            if cid!=0x412e00:raise FormatError('Unknown model LOD class in native witness')
            for ci,component in lod_fields[0xa].array().items():
                cid,component_fields=component.typed()
                if cid!=0x410067:raise FormatError('Unknown model component class in native witness')
                refs={}
                for name,key in (('mesh',0x1f),('material',0x21),('shadow',0x23)):
                    link_fields=component_fields[key].mapping()
                    refs[name]=link_fields[0x14].string() if 0x14 in link_fields else ''
                expected.append((li,ci,refs))
        if len(expected)!=len(row['components']):raise FormatError('Model component evidence coverage differs')
        for (li,ci,refs),component in zip(expected,row['components']):
            if (li,ci)!=(component['lod'],component['component']):raise FormatError('Model component evidence order differs')
            for name,locator in refs.items():
                link=component[name]
                if link['declared_locator']!=locator:raise FormatError('Model declared link differs from native object data')
                if link.get('target_artifact'):
                    item=link['target_artifact'];raw=Path(item['file']).read_bytes()
                    if sha(raw)!=item['sha256']:raise FormatError('Model resolved target witness changed')
                    target=Node(raw,0,len(raw)).mapping()
                    if target[0x14].string()!=link['resolved_local_locator']:raise FormatError('Model resolved target identity differs')
        if issues_for(row)!=row['issues']:raise FormatError('Model link findings do not match the actual observed fields')
    return True


def issues_for(model):
    issues=[]
    if model['status']&3!=3:issues.append({'issue':'model_not_connected_and_activated'})
    for component in model['components']:
        li,ci=component['lod'],component['component']
        if component['status']&3!=3:issues.append({'issue':'component_not_ready','lod':li,'component':ci})
        for label,allowed in (('mesh',{'00410035','0041006C'}),('material',{'0041166F'}),('shadow',{'00410035','0041006C'})):
            link=component[label];declared=link['declared_locator'];problem=None
            if not declared:
                if label!='shadow' or link['resolved']:problem='required_model_link_empty' if label!='shadow' else 'empty_shadow_link_resolved'
            elif not link['resolved']:problem='model_target_not_resolved'
            elif link['resolved_cid'] not in allowed:problem='model_target_wrong_type'
            elif not link['same_local_member'] or (link['resolved_local_locator'] or '').casefold()!=declared.casefold():problem='model_target_not_verified_local'
            elif not link['connected_and_activated']:problem='model_target_not_connected_and_activated'
            if problem:issues.append({'issue':problem,'lod':li,'component':ci,'link':label,'locator':declared})
            if (link['resolved_local_locator'] and link['resolved_cid'] in allowed and not link.get('target_artifact')):
                raise FormatError('Model local resolved target witness is missing')
    if not model['comparison']['data_equal']:issues.append({'issue':'model_data_changed_during_link_inspection'})
    return issues


def worker(api,content,params,run_dir):
    import ctypes as c
    profile=api.profile['model_links'];library=api.qword(content+api.profile['clb_library_offset'])
    def cid(pointer):
        method=api.qword(api.qword(pointer)+api.profile['class_getter_vtable_offset']);code=api.memory(method,6)
        if code[:1]!=b'\xb8' or code[-1:]!=b'\xc3':raise FormatError('Unreviewed native model-link class getter')
        return int.from_bytes(code[1:5],'little')
    if cid(library)!=api.profile['clb_class_id']:raise FormatError('Model context is not a native CLB')
    listing=library+api.profile['asset_templates']['list_offset']
    if cid(listing)!=0x400081:raise FormatError('Model context resource list differs')
    count=int.from_bytes(api.memory(listing+0x18,4),'little');array=api.qword(listing+0x10)
    if not 1<=count<=100000 or count!=len(params['members']):raise FormatError('Model context member count differs')
    pointers={api.qword(array+row['ordinal']*8):row for row in params['members']}
    local={row['locator'].casefold():pointer for pointer,row in pointers.items() if row['locator']}
    directory=Path(run_dir)/'native-model';directory.mkdir();models=[];target_cache={};total_bytes=0;component_count=0
    def artifact(pointer,name):
        nonlocal total_bytes
        raw=api.serialize_object(pointer)
        if not 1<=len(raw)<=16*1024*1024:raise FormatError('Native model/target object exceeds 16 MiB')
        total_bytes+=len(raw)
        if total_bytes>64*1024*1024:raise FormatError('Native model evidence exceeds 64 MiB')
        file=directory/name;file.write_bytes(raw);return {'file':str(file),'sha256':sha(raw)}
    for mi,selected in enumerate(params['models']):
        if type(selected['ordinal']) is not int or not 0<=selected['ordinal']<count:raise FormatError('Model ordinal is outside its library')
        model=api.qword(array+selected['ordinal']*8)
        if cid(model)!=0x41004b:raise FormatError('Native selected model type differs')
        before=artifact(model,f'{mi}-before.bin');issues=[];components=[]
        status=api.memory(model+0x24,1)[0]
        if status&3!=3:issues.append({'issue':'model_not_connected_and_activated'})
        lods=api.qword(model+0x1b0);n=int.from_bytes(api.memory(model+0x1b8,4),'little')
        if not 1<=n<=16:raise FormatError('Native model LOD count outside 1..16')
        for li in range(n):
            lod=api.qword(lods+li*8)
            if cid(lod)!=0x412e00:raise FormatError('Native model LOD type differs')
            items=api.qword(lod+0x20);size=int.from_bytes(api.memory(lod+0x28,4),'little')
            if not 1<=size<=64:raise FormatError('Native model component count outside 1..64')
            for ci in range(size):
                component_count+=1
                if component_count>256:raise FormatError('Model evidence exceeds 256 components')
                component=api.qword(items+ci*8)
                if cid(component)!=0x410067:raise FormatError('Native model component type differs')
                flags=int.from_bytes(api.memory(component+0x18,4),'little')
                row={'lod':li,'component':ci,'status':flags}
                if flags&3!=3:issues.append({'issue':'component_not_ready','lod':li,'component':ci})
                for label,offset,allowed in (('mesh',0x38,{0x410035,0x41006c}),('material',0xf8,{0x41166f}),('shadow',0x98,{0x410035,0x41006c})):
                    link=component+offset;raw=api.serialize_object(link);fields=Node(raw,0,len(raw)).mapping()
                    declared=fields[0x14].string() if 0x14 in fields else ''
                    pointer=c.CFUNCTYPE(c.c_void_p,c.c_void_p)(api.dll._handle+profile['get_resource_rva'])(link)
                    target=pointers.get(pointer);local_locator=target['locator'] if target else None
                    actual_cid=cid(pointer) if pointer else None
                    observed={'declared_locator':declared,'resolved':bool(pointer),'resolved_cid':f'{actual_cid:08X}' if actual_cid is not None else None,
                              'resolved_local_locator':local_locator,'same_local_member':bool(pointer and local.get(declared.casefold())==pointer),
                              'connected_and_activated':bool(pointer and api.memory(pointer+0x24,1)[0]&3==3)}
                    problem=None
                    if not declared:
                        if label!='shadow' or pointer:problem='required_model_link_empty' if label!='shadow' else 'empty_shadow_link_resolved'
                    elif not pointer:problem='model_target_not_resolved'
                    elif actual_cid not in allowed:problem='model_target_wrong_type'
                    elif not observed['same_local_member']:problem='model_target_not_verified_local'
                    elif not observed['connected_and_activated']:problem='model_target_not_connected_and_activated'
                    if problem:issues.append({'issue':problem,'lod':li,'component':ci,'link':label,'locator':declared})
                    if target:observed['target_ordinal']=target['ordinal']
                    if target and actual_cid in allowed:
                        ordinal=target['ordinal']
                        if pointer not in target_cache:target_cache[pointer]=artifact(pointer,f'target-{ordinal}.bin')
                        observed['target_artifact']=target_cache[pointer];observed['target_ordinal']=ordinal
                    row[label]=observed
                components.append(row)
        after=artifact(model,f'{mi}-after.bin')
        comparison=native_objects.compare_bodies(Path(before['file']).read_bytes(),Path(after['file']).read_bytes())
        if not comparison['data_equal']:issues.append({'issue':'model_data_changed_during_link_inspection'})
        models.append({'locator':selected['locator'],'status':status,'components':components,'issues':issues,
                       'artifacts':{'before':before,'after':after},'comparison':comparison})
    result={'models':models,'library_name':params['library_name']};assess(result);return result
