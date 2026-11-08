"""Native scalar edits with differential field mapping and canonical readbacks."""
import json
from pathlib import Path
import struct
import uuid

from . import editing,native,native_objects,native_inspection,native_roundtrip,native_validation
from .core import FormatError,Node,Resource,mod_file,read_acp,read_rpk,sha
from .filesystem import inventory,file_hash,copy_directory,reject_reparse

FORMAT='aow4mod-native-edit-plan/1'


def fingerprint():
    root=Path(__file__).parent
    return sha(b''.join((root/name).read_bytes() for name in ('native_edits.py','editing.py','core.py','filesystem.py')))


def same(codec,a,b):
    if type(a) is not type(b):return False
    return struct.pack('<f',a)==struct.pack('<f',b) if codec=='f32' else a==b


def field_path(value):
    if not isinstance(value,str) or len(value.split('/'))!=2:raise FormatError('Native scalar edits require a direct class/property path')
    if value.split('/')[0]=='meta':
        try:key=int(value.split('/')[1],16)
        except ValueError as exc:raise FormatError('Invalid native metadata field') from exc
        if key!=0x12c:raise FormatError('Only the reviewed DataStoreTag metadata field is editable')
        return 'meta/0x12C'
    try:cid,field,_=editing.property_parts(value)
    except (ValueError,TypeError) as exc:raise FormatError('Native edit field path must use hexadecimal class/property keys') from exc
    if not 0x10000<=cid<=0xffffffff or not 0<=field<=0xffffffff:raise FormatError('Native edit field path is out of range')
    return f'0x{cid:08X}/0x{field:X}'


def scopes(data):
    return {key for key in Node(data,0,len(data)).mapping() if key>=0x10000}


def property_scope(prop):
    return set() if prop.startswith('meta/') else {int(prop.split('/')[0],16)}


def subject(body,source,embedded,binding):
    """Select only an explicitly reviewed existing inline member."""
    if embedded is None:return body,source['cid']
    prop=field_path(embedded)
    matches=[(cid,row) for cid,row in binding['profile'].get('inline_resource_objects',{}).items()
             if row['owner_cid']==source['cid'] and row['property']==prop]
    if len(matches)!=1:raise FormatError('Inline owner/property layout has not been reviewed')
    cid,row=matches[0];class_id,field_id,_=editing.property_parts(prop)
    root=Node(body,0,len(body)).mapping()
    if class_id not in root or field_id not in root[class_id].mapping():raise FormatError('Inline member is absent; use a verified template first')
    value=root[class_id].mapping()[field_id].raw()
    if set(Node(value,0,len(value)).mapping())!={int(cid,16)}:raise FormatError('Inline body has an unsupported class scope')
    return value,cid


def assignments(values,fields,binding,schema_path):
    if not isinstance(values,list) or not 1<=len(values)<=32:raise FormatError('Native edit values must be a list of 1..32 assignments')
    result=[];paths=set();names=set()
    for value in values:
        if not isinstance(value,dict) or set(value)-{'name','category','property','value','probe_value'} or 'property' not in value:
            raise FormatError('Native edit assignment needs property, name, value and optional category/probe_value')
        normalized=native_objects.normalize_assignments([{key:item for key,item in value.items() if key in ('name','category','value')}],
                                                        fields,binding,schema_path)[0]
        prop=field_path(value['property']);key=(normalized['name'],normalized['category'])
        if prop in paths or key in names:raise FormatError('Duplicate native edit field or property')
        paths.add(prop);names.add(key)
        selected=next(row for row in fields if row['name']==normalized['name'] and row['category']==normalized['category'])
        if selected.get('default_status')!='read' or selected.get('getter_status')!=0:
            raise FormatError('Selected native getter is unavailable on the source object')
        before=native_objects.normalized_value(normalized['codec'],selected['default'])
        if 'probe_value' in value:
            probe=value['probe_value']
        elif not same(normalized['codec'],before,normalized['value']):probe=normalized['value']
        elif normalized['codec']=='bool':probe=not before
        elif normalized['codec']=='enum_i32':
            enriched=native.enrich_enums([selected],binding,schema_path)
            alternatives=[row['value'] for row in enriched[0].get('enum',{}).get('values',[]) if row['value']!=before]
            if not alternatives:raise FormatError('No alternate verified enum value is available to prove a no-op field mapping')
            probe=alternatives[0]
        else:raise FormatError('A no-op integer/float/string assignment needs an explicit distinct probe_value')
        probe_assignment=native_objects.normalize_assignments([{'name':normalized['name'],'category':normalized['category'],'value':probe}],
                                                              fields,binding,schema_path)[0]
        probe=probe_assignment['value']
        if same(normalized['codec'],before,probe):raise FormatError('Native edit probe value must differ from the source getter')
        result.append({**normalized,'property':prop,'expected_before':before,'probe_value':probe})
    return result


def scalar(raw,codec,value):
    if raw is None:return True  # Omission still requires actual native getter/mapping witnesses.
    if codec in ('i32','enum_i32'):
        return len(raw) in (1,2,4,8) and int.from_bytes(raw,'little',signed=value<0)==value
    if codec=='bool':return len(raw) in (1,2,4,8) and int.from_bytes(raw,'little')==int(value)
    if codec=='f32':return raw==struct.pack('<f',value)
    if codec=='string':return raw==editing.encode_value('string',value)
    return False


def artifact(item,key,run,name):
    path=Path(item[key+'_file']);reject_reparse(path)
    if path.resolve()!=(Path(run)/name).resolve() or not 1<=path.stat().st_size<=16*1024*1024:
        raise FormatError('Native edit artifact path/size is invalid')
    data=path.read_bytes()
    if sha(data)!=item[key+'_sha256']:raise FormatError('Native edit artifact hash changed')
    return data


def readback(value,assignment,expected):
    if (value.get('name')!=assignment['name'] or value.get('category')!=assignment['category']
            or any(value.get(key)!=assignment[key] for key in ('codec','editor_cid','editor_parameter'))
            or value.get('default_status')!='read' or value.get('getter_status')!=0
            or not same(assignment['codec'],value.get('default'),expected)):
        raise FormatError('Native edit getter identity/type/value does not match its assignment')


def verify_material(result,original):
    if result.get('operation')!='object-edit':raise FormatError('Native edit evidence has the wrong operation')
    params=result['params'];payload=result['payload'];run=result['run']
    native.validate_payload('object-edit',payload,params)
    if not isinstance(params['assignments'],list) or not 1<=len(params['assignments'])<=32:
        raise FormatError('Native edit requires a bounded assignment list')
    names=set();paths=set()
    for job in params['assignments']:
        key=(job['name'],job['category']);prop=field_path(job['property'])
        if key in names or prop in paths:raise FormatError('Duplicate native edit property/field witness')
        names.add(key);paths.add(prop)
        for name in ('value','expected_before','probe_value'):native_objects.normalized_value(job['codec'],job[name])
        if same(job['codec'],job['expected_before'],job['probe_value']):raise FormatError('Native edit probe does not change its getter value')
    if bytes.fromhex(params['body_hex'])!=original or sha(original)!=params['body_sha256']:
        raise FormatError('Native edit request does not bind the original object')
    if artifact(payload,'input',run,'edit-input.bin')!=original:raise FormatError('Native edit input witness differs')
    baseline=artifact(payload,'baseline',run,'edit-baseline.bin')
    edited=artifact(payload,'source',run,'object-source.bin');after=artifact(payload,'output',run,'object-serialized.bin')
    native_objects.verify_result(result)
    allowed={field_path(row['property']) for row in params['assignments']}
    allowed_scopes=set().union(*(property_scope(path) for path in allowed))
    scope_changes=scopes(original)^scopes(after);normalization_scopes=scopes(original)^scopes(baseline)
    if (scope_changes|normalization_scopes)-allowed_scopes or scopes(edited)!=scopes(after):
        raise FormatError('Native scalar edit changes unrequested class scopes or has an unstable canonical body')
    normalization=native_objects.compare_bodies(original,baseline)
    comparison=native_objects.compare_bodies(original,after)
    if (set(normalization['changed_fields'])|set(comparison['changed_fields']))-allowed:
        raise FormatError('Native scalar edit changes unrequested fields, including source normalization')
    old_fields=native_objects.storage_fields(original);base_fields=native_objects.storage_fields(baseline)
    after_fields=native_objects.storage_fields(after);mappings=[]
    for index,(assignment,witness,before_value,actual) in enumerate(zip(params['assignments'],payload['mapping_witnesses'],payload['before_values'],payload['readbacks'])):
        prop=assignment['property'];codec=assignment['codec']
        if prop!=field_path(prop) or witness.get('property')!=prop:raise FormatError('Native edit field witness identity differs')
        readback(before_value,assignment,assignment['expected_before'])
        readback(witness['before_readback'],assignment,assignment['expected_before'])
        readback(witness['probe_readback'],assignment,assignment['probe_value'])
        readback(actual,assignment,assignment['value'])
        a=artifact(witness,'before',run,f'edit-probe-{index}-before.bin')
        b=artifact(witness,'after',run,f'edit-probe-{index}-after.bin')
        if not native_objects.compare_bodies(a,baseline)['data_equal'] or scopes(a)!=scopes(baseline):
            raise FormatError('Native edit probe baseline differs from its source canonical state')
        diff=native_objects.compare_bodies(a,b)
        if diff['changed_fields']!=[prop] or (scopes(a)^scopes(b))-property_scope(prop):
            raise FormatError('Native setter does not map exclusively to the declared direct field')
        for raw,value in [(old_fields.get(prop),assignment['expected_before']),
                          (base_fields.get(prop),assignment['expected_before']),
                          (native_objects.storage_fields(b).get(prop),assignment['probe_value']),
                          (after_fields.get(prop),assignment['value'])]:
            if not scalar(raw,codec,value):raise FormatError('Native edit declared field is not the observed primitive scalar encoding')
        mappings.append({'name':assignment['name'],'category':assignment['category'],'property':prop,'codec':codec,
                         'before':assignment['expected_before'],'after':assignment['value'],'probe':assignment['probe_value'],
                         'mapping_changed_fields':diff['changed_fields'],'output_serialized':prop in after_fields})
    if len(mappings)!=len(params['assignments']) or edited!=Path(payload['source_file']).read_bytes():
        raise FormatError('Native edit coverage or edited artifact changed')
    return after,{'mapping':mappings,'changed_fields':comparison['changed_fields'],
                  'source_normalization_fields':normalization['changed_fields'],
                  'changed_class_scopes':[f'{key:08X}' for key in sorted(scope_changes)],
                  'source_normalization_class_scopes':[f'{key:08X}' for key in sorted(normalization_scopes)],
                  'fresh_object_data_equal':native_objects.compare_bodies(edited,after)['data_equal']}


def verify_independent(result,body,jobs):
    if result.get('operation')!='object-roundtrip' or result['params'].get('assignments')!=[]:
        raise FormatError('Independent native edit readback must use a reader without setters')
    params=result['params'];payload=result['payload'];run=result['run']
    expected_filters=[{'name':job['name'],'category':job['category']} for job in jobs]
    if params.get('include_properties') is not True or params.get('property_filter')!=expected_filters:
        raise FormatError('Independent native edit reader property selection differs')
    native.validate_payload('object-roundtrip',payload,params)
    if bytes.fromhex(params['body_hex'])!=body or params['body_sha256']!=sha(body):
        raise FormatError('Independent native edit reader does not bind the edited body')
    if artifact(payload,'source',run,'object-source.bin')!=body:
        raise FormatError('Independent native edit reader input differs')
    after=artifact(payload,'output',run,'object-serialized.bin')
    native_objects.verify_result(result)
    values=[]
    for job in jobs:
        matches=[row for row in payload['properties'] if row['name']==job['name'] and row['category']==job['category']]
        if len(matches)!=1:raise FormatError('Independent native edit property is missing or ambiguous')
        readback(matches[0],job,job['value'])
        values.append({'name':job['name'],'category':job['category'],'codec':job['codec'],'value':matches[0]['default']})
    if not native_objects.compare_bodies(body,after)['data_equal'] or scopes(body)!=scopes(after):
        raise FormatError('Independent native edit reader changes the canonical body')
    return {'reader_without_setters':True,'data_equal':True,'values':values}


def patch_file(data,suffix,rid,pack,cid,body,embedded=None):
    found=[]
    def transform(name,node):
        resource=Resource.parse(node)
        if resource.rid!=int(rid,16) or name.casefold()!=pack.casefold():return node.raw()
        if resource.type_id!=int(cid,16):raise FormatError('Native edit resource class changed before wrapping')
        found.append(resource.rid)
        if embedded is not None:
            class_id,field_id,_=editing.property_parts(embedded)
            return editing.rewrite_typed(node,{class_id:editing.rewrite_map(resource.fields[class_id],{field_id:body})})
        return struct.pack('<I',resource.type_id)+body
    updated=editing.transform_records(data,suffix,transform)
    if len(found)!=1:raise FormatError('Native edit resource wrapper is missing or ambiguous')
    return updated


def make_plan(path,rid,values,game,pack=None,source_root=None,schema_path=None,cache_dir=Path('.aow4mod/native'),timeout=45,embedded=None):
    if not isinstance(values,list) or not 1<=len(values)<=32 or any(not isinstance(row,dict) or 'name' not in row for row in values):
        raise FormatError('Native edit requires 1..32 named scalar assignments')
    owner_body,source=native_inspection.source_body(path,rid,pack)
    if source['cid'] in native_objects.CONTAINERS or source['cid']=='00410024':
        raise FormatError('Native scalar edits require a resource; use authoring/asset commands for containers')
    file=Path(source['file']).resolve();root=Path(source_root).resolve() if source_root else (Path(path).resolve() if Path(path).is_dir() else file.parent)
    if not file.is_relative_to(root):raise FormatError('Native edit source is outside its Mod root')
    mod_file(root)  # Exact RPK callers must select the complete Mod via --source-root.
    files=inventory(root);data=file.read_bytes()
    if sha(data)!=source['sha256']:raise FormatError('Native edit source changed during capture')
    cache=native.check_cache_directory(cache_dir,game,[root])
    binding=native.profile_for(game)
    embedded=field_path(embedded) if embedded is not None else None
    body,subject_cid=subject(owner_body,source,embedded,binding)
    registry=native.cached(game,'types',cache_dir=cache,timeout=timeout)
    selected=native.select_type(registry,subject_cid)
    filters=[{key:row[key] for key in ('name','category') if key in row} for row in values]
    request={**selected,'body_hex':body.hex(),'body_sha256':sha(body),'assignments':[],'include_properties':True,'property_filter':filters}
    described=native.run_worker(game,'object-roundtrip',request,cache,timeout)
    jobs=assignments(values,described['payload']['properties'],described['binding'],schema_path)
    params={**selected,'body_hex':body.hex(),'body_sha256':sha(body),'assignments':jobs}
    result=native.run_worker(game,'object-edit',params,cache,timeout)
    after,proof=verify_material(result,body)
    read_params={**selected,'body_hex':after.hex(),'body_sha256':sha(after),'assignments':[],
                 'include_properties':True,'property_filter':[{'name':job['name'],'category':job['category']} for job in jobs]}
    independent=native.run_worker(game,'object-roundtrip',read_params,cache,timeout)
    if Path(independent['run']).resolve()==Path(result['run']).resolve():raise FormatError('Native edit independent reader reused its setter run')
    proof['independent_readback']=verify_independent(independent,after,jobs)
    updated=patch_file(data,file.suffix.lower(),rid,source['pack'],source['cid'],after,embedded)
    directory=cache/'native-edits'/uuid.uuid4().hex;directory.mkdir(parents=True)
    candidate=directory/file.name;candidate.write_bytes(updated)
    if file.suffix.lower()=='.rpk':
        read_rpk(candidate)
        wrapper=native_roundtrip.run(candidate,game,directory.parent/(directory.name+'-roundtrip'),cache,timeout,
                                     libraries=list(root.rglob('*.clb')))
    else:
        read_acp(candidate);wrapper=None
    if inventory(root)!=files:raise FormatError('Native edit Mod changed while planning')
    plan={'format':FORMAT,'generator_sha256':fingerprint(),'source':source,'source_root':str(root),'source_files':files,
            'game':str(Path(game).resolve()),'binding':native.public_binding(result['binding']),
            'native_result':result,'independent_result':independent,'proof':proof,'candidate_file':str(candidate),'file_hex':updated.hex(),'file_sha256':sha(updated),
            'native_pack':wrapper,'review_required':bool(wrapper and (wrapper['diagnostics'] or not wrapper['comparison']['all_serialized_data_equal'])),
            'coverage':'Native primitive setter, differential direct-field mapping and fresh-object readback; unrelated serialized fields retained. No effective ACP merge or gameplay certification.'}
    if embedded is not None:plan['embedded']=embedded
    return plan


def apply_plan(plan,output):
    if not isinstance(plan,dict) or plan.get('format')!=FORMAT or plan.get('generator_sha256')!=fingerprint():
        raise FormatError('Unsupported or outdated native edit plan')
    root=Path(plan['source_root']);game=Path(plan['game']);source=plan['source'];file=Path(source['file'])
    if not file.is_relative_to(root) or inventory(root)!=plan['source_files']:raise FormatError('Native edit Mod source changed')
    values_source=plan.get('values_source')
    if values_source and file_hash(values_source['file'])!=values_source['sha256']:
        raise FormatError('Native edit assignment file changed')
    current=native.profile_for(game)
    if native.public_binding(current)!=plan['binding'] or plan['native_result']['binding']!=current:
        raise FormatError('Native edit library/profile/backend changed')
    independent=plan['independent_result']
    owner_body,observed=native_inspection.source_body(file,source['resource_id'],source['pack'])
    body,subject_cid=subject(owner_body,source,plan.get('embedded'),current)
    if (independent['binding']!=current or independent['params']['cid']!=subject_cid
            or plan['native_result']['params']['cid']!=subject_cid
            or independent['params']['name']!=plan['native_result']['params']['name']):
        raise FormatError('Native edit object/independent reader type or binding differs')
    if Path(independent['run']).resolve()==Path(plan['native_result']['run']).resolve():
        raise FormatError('Native edit independent reader reused its setter run')
    if observed!=source:raise FormatError('Native edit selected object changed')
    native.check_cache_directory(plan['native_result']['run'],game,[root])
    after,proof=verify_material(plan['native_result'],body)
    native.check_cache_directory(independent['run'],game,[root])
    proof['independent_readback']=verify_independent(independent,after,plan['native_result']['params']['assignments'])
    data=patch_file(file.read_bytes(),file.suffix.lower(),source['resource_id'],source['pack'],source['cid'],after,plan.get('embedded'))
    if proof!=plan['proof'] or data.hex()!=plan['file_hex'] or sha(data)!=plan['file_sha256'] or file_hash(plan['candidate_file'])!=sha(data):
        raise FormatError('Native edit plan differs from its source and scalar witnesses')
    wrapper=plan['native_pack']
    if (wrapper is None)!=(file.suffix.lower()=='.acp'):raise FormatError('Native edit container evidence is missing or has the wrong format')
    if wrapper:
        comparison=native_roundtrip.compare(Path(plan['candidate_file']),Path(wrapper['output_file']))
        if wrapper['binding']!=plan['binding'] or comparison!=wrapper['comparison']:
            raise FormatError('Native edit full-pack evidence changed')
        if native_validation.diagnostics(wrapper['run'])!=wrapper['diagnostics']:
            raise FormatError('Native edit diagnostic evidence differs from its saved log')
    expected_review=bool(wrapper and (wrapper['diagnostics'] or not wrapper['comparison']['all_serialized_data_equal']))
    if plan['review_required']!=expected_review:raise FormatError('Native edit plan hides its container review findings')
    with copy_directory(root,output,[game]) as (stage,files):
        if files!=plan['source_files']:raise FormatError('Native edit source changed before staging')
        (stage/file.relative_to(root)).write_bytes(data)
        receipt={'format':'aow4mod-native-edit-receipt/1','output':str(Path(output).resolve()),
                 'resource':source['resource_id'],'file':file.relative_to(root).as_posix(),'proof':proof,
                 'native_pack_review_required':plan['review_required'],'coverage':plan['coverage'],'gameplay_tested':False}
        if plan.get('embedded') is not None:receipt['embedded']=plan['embedded']
        (stage/'.aow4mod-native-edit.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return receipt
