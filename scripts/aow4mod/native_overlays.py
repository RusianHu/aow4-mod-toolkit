"""Read-only witnesses for explicit ACP 0x40 application to one native RPK."""
import json
from contextlib import contextmanager
import os
from pathlib import Path
import shutil

from . import native,native_objects,native_roundtrip,native_validation,editing
from . import native_dependencies,native_context,projects
from .core import FormatError,Node,Resource,mod_file,read_acp,read_rpk,sha
from .filesystem import file_hash,inventory,new_directory


@contextmanager
def locked_inputs(paths):
    """Deny native/editor writes to the explicitly selected input files on Windows."""
    if os.name!='nt':
        yield
        return
    import ctypes as c
    from ctypes import wintypes as w
    kernel=c.WinDLL('kernel32',use_last_error=True)
    create=kernel.CreateFileW;create.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,c.c_void_p,w.DWORD,w.DWORD,w.HANDLE];create.restype=w.HANDLE
    close=kernel.CloseHandle;close.argtypes=[w.HANDLE];handles=[]
    try:
        for path in sorted({str(Path(value).resolve()) for value in paths}):
            handle=create(path,0x80000000,1,None,3,0x80,None)
            if handle==c.c_void_p(-1).value:raise FormatError(f'Cannot obtain read-only overlay input guard: {path} ({c.get_last_error()})')
            handles.append(handle)
        yield
    finally:
        for handle in reversed(handles):close(handle)


def metadata_signature(path):
    return metadata_signature_data(Path(path).read_bytes())


def metadata_signature_data(data):
    root=Node(data,176,len(data)).mapping();libraries=[]
    for key,node in root[13].mapping().items():
        kind,wrapper=node.typed();feature=wrapper[0x33].mapping()
        libraries.append((key,kind,{field:value.raw() for field,value in wrapper.items() if field!=0x33},
                          {field:value.raw() for field,value in feature.items() if field!=0x15}))
    return ({key:value.raw() for key,value in root.items() if key!=13},libraries,data[:12]+data[16:176])


def library_membership(path):
    data=Path(path).read_bytes();root=Node(data,176,len(data)).mapping()
    return {key:frozenset(Resource.parse(item).rid for item in node.typed()[1][0x33].mapping()[0x15].array().values())
            for key,node in root[13].mapping().items()}


def resource_difference(expected,actual):
    left=expected.leaves();right=actual.leaves()
    fields=sorted(key for key in left.keys()|right.keys() if left.get(key)!=right.get(key))
    # Lists inside a saved resource can change their offset-table encoding too,
    # independently of the enclosing Mod record. Compare only complete,
    # unambiguous structures; missing/default/opaque values remain differences.
    from .resource_diff import compare_nodes
    encoding=[]
    for key in fields:
        if key not in left or key not in right:continue
        a,b=left[key],right[key]
        compared=compare_nodes(Node(a,0,len(a)),Node(b,0,len(b)))
        if compared['traversal_complete'] and compared['recognized_data_equal']:
            encoding.append(key)
    fields=[key for key in fields if key not in encoding]
    scopes=sorted(set(expected.fields)^set(actual.fields))
    result={'type_changed':expected.type_id!=actual.type_id,'fields':fields,'class_or_meta_keys':scopes}
    if encoding:result['encoding_only_fields']=encoding
    return result


def compare_stage(base,path,expected,expected_metadata=None):
    base,path=Path(base),Path(path)
    _,before=read_rpk(base);_,rows=read_rpk(path);actual={row.rid:row for row in rows}
    if len(actual)!=len(rows):raise FormatError('Duplicate native overlay output IDs')
    differences=[];encoding=[]
    for rid in sorted(set(expected)&set(actual)):
        diff=resource_difference(expected[rid],actual[rid])
        if diff['type_changed'] or diff['fields'] or diff['class_or_meta_keys']:
            differences.append({'id':f'{rid:016X}',**diff})
        if diff.get('encoding_only_fields'):
            encoding.append({'id':f'{rid:016X}','fields':diff['encoding_only_fields']})
    missing=sorted(f'{rid:016X}' for rid in set(expected)-set(actual));added=sorted(f'{rid:016X}' for rid in set(actual)-set(expected))
    metadata_equal=(metadata_signature(base) if expected_metadata is None else expected_metadata)==metadata_signature(path)
    membership_equal=library_membership(base)==library_membership(path)
    result={'resource_count':len(rows),'missing_ids':missing,'added_ids':added,'differences':differences,
            'nonresource_metadata_equal':metadata_equal,'library_membership_equal':membership_equal,
            'resource_order_changed':[row.rid for row in before]!=[row.rid for row in rows],
            'all_expected_data_equal':not(missing or added or differences) and metadata_equal and membership_equal}
    if encoding:result['encoding_only_resources']=encoding
    return result


def collect_records(base,mods):
    info,rows=read_rpk(base);by_id={row.rid:row for row in rows}
    if len(by_id)!=len(rows):raise FormatError('Duplicate base resource IDs prevent native overlay checks')
    mods=list(mods)
    if not 1<=len(mods)<=32:raise FormatError('Native merge check requires 1..32 explicit Mods in the desired order')
    records=[];inputs=[];ignored=[];targets=[]
    for ordinal,value in enumerate(mods):
        acp=mod_file(Path(value));before=inventory(acp.parent);meta,changes=read_acp(acp)
        data=acp.read_bytes();fields=Node(data,19,len(data)).typed()[1][0x40009d].mapping()
        selected=[node for node in fields[0x26].array().values() if node.typed()[1][0x401204].mapping()[1].string().casefold()==info['name'].casefold()] if 0x26 in fields else []
        other=sorted({row['pack'] for row in changes if row['pack'].casefold()!=info['name'].casefold()})
        inputs.append({'file':str(acp),'sha256':sha(data),'root':str(acp.parent),'files':before,
                       'matched_pack':bool(selected),'other_packs_not_checked':other,'position':ordinal})
        if len(selected)>1:raise FormatError('Multiple Mod-data entries target the same pack; resolve ambiguous records first')
        if not selected:ignored.append({'mod':str(acp),'reason':'no overrides for selected pack'});continue
        current=[row for row in changes if row['pack'].casefold()==info['name'].casefold()]
        ids=[row['resource'].rid for row in current]
        if not 1<=len(ids)<=4096 or len(set(ids))!=len(ids):raise FormatError('Selected Mod has empty, oversized or duplicate overlay IDs')
        expected=[]
        for change in current:
            resource=change['resource']
            if change['operation_raw']!=0x40:raise FormatError('Native merge check only supports existing-resource update operation 0x40')
            if resource.rid not in by_id or resource.type_id!=by_id[resource.rid].type_id:
                raise FormatError('Overlay ID/type does not match the selected base pack')
            if 0x14 not in resource.fields or 0x14 not in by_id[resource.rid].fields:
                raise FormatError('Native overlay resources require locators')
            expected.append({'id':f'{resource.rid:016X}','cid':f'{resource.type_id:08X}'})
        body=selected[0].raw()[4:]
        if len(body)>16*1024*1024:raise FormatError('Mod-data body exceeds 16 MiB')
        records.append({'file':str(acp),'sha256':sha(data),'body_hex':body.hex(),'body_sha256':sha(body),'resources':expected})
        targets.append(current)
    if not records:raise FormatError('No selected Mod overrides this base pack; application is not evaluated')
    return info,by_id,records,inputs,ignored,targets


def verify_application(base,params,payload,targets):
    native.validate_payload('package-apply',payload,params)
    return verify_snapshots(base,params['modifications'],payload['application'],targets)


def verify_snapshots(base,records,application,targets):
    """Recheck frozen package snapshots without starting a native process."""
    if len(application['steps'])!=len(records) or len(targets)!=len(records):
        raise FormatError('Native overlay snapshot coverage is incomplete')
    _,base_rows=read_rpk(base);expected={row.rid:row for row in base_rows}
    baseline=Path(application['baseline']['output_file'])
    if file_hash(baseline)!=application['baseline']['output_sha256']:raise FormatError('Native overlay baseline hash changed')
    baseline_check=compare_stage(base,baseline,expected);steps=[]
    for index,(record,step,changes) in enumerate(zip(records,application['steps'],targets)):
        if step['file']!=record['file'] or step['sha256']!=record['sha256'] or len(step['entries'])!=len(changes):
            raise FormatError('Native overlay snapshot input binding differs')
        raw=Path(step['mod_data_file']).read_bytes()
        if sha(raw)!=step['mod_data_sha256']:raise FormatError('Native Mod-data readback changed')
        reader_check=native_objects.compare_mod_data(bytes.fromhex(record['body_hex']),raw)
        entries_ok=True
        for change,entry in zip(changes,step['entries']):
            source=change['resource'];old=expected[source.rid]
            # The official library reuses the original logical slot after copying
            # the Mod object; stale source locators do not select a different ID.
            encoded=editing.rewrite_typed(source.node,{0x14:old.fields[0x14].raw()})
            expected[source.rid]=Resource.parse(Node(encoded,0,len(encoded)))
            entries_ok=entries_ok and entry=={'id':f'{source.rid:016X}','cid':f'{source.type_id:08X}',
                                              'operation':0x40,'applied_before':0,'applied_after':1}
        output=Path(step['output']['output_file'])
        if file_hash(output)!=step['output']['output_sha256']:raise FormatError('Native overlay stage hash changed')
        comparison=compare_stage(base,output,expected)
        steps.append({'mod':record['file'],'entries':step['entries'],'all_marked_applied':entries_ok,
                      'input_record_readback':reader_check,'comparison':comparison,'output_file':str(output)})
    matched=baseline_check['all_expected_data_equal'] and all(step['all_marked_applied'] and
        step['input_record_readback']['data_equal'] and step['comparison']['all_expected_data_equal'] for step in steps)
    return {'baseline':baseline_check,'steps':steps,'scoped_application_witness_matched':matched}


def run(base,mods,game,output,cache_dir=Path('.aow4mod/native'),timeout=60,libraries=(),packages=()):
    base=Path(base).resolve();info,by_id,records,inputs,ignored,targets=collect_records(base,mods)
    selected=list(packages)
    for row in inputs:selected.extend(pack['path'] for pack in projects.inspect_project(row['file'])['packs'] if pack['registered'])
    bound=native_context.bind(selected)
    for row in bound:
        if row['name'].casefold()==info['name'].casefold() and row['sha256']!=info['sha256']:
            raise FormatError('Native local RPK context shadows a different base pack')
    bound=[row for row in bound if row['name'].casefold()!=info['name'].casefold()]
    expected_context=native_dependencies.expected(base.read_bytes(),records)
    import_preflight=native_context.preflight(expected_context,bound,game,not base.is_relative_to(Path(game).resolve()))
    protected=[base.parent,*[Path(row['root']) for row in inputs],*[Path(row['file']).parent for row in bound]]
    destination=native.check_cache_directory(output,game,protected)
    cache=native.check_cache_directory(cache_dir,game,[*protected,destination])
    language=Path(game).resolve()/'Language/EN/EN.MO';context=native.library_context(libraries)
    params={'path':str(base),'sha256':info['sha256'],'language':str(language),'language_sha256':file_hash(language),
            'libraries':context,'modifications':records}
    with new_directory(destination,[game,*protected]) as stage:
        artifacts=[];preparation=None;prepared=base;preparation_runs=[]
        def keep(kind,index,source,digest,name):
            target=stage/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
            if file_hash(target)!=digest:raise FormatError('Native overlay evidence changed during output collection')
            item={'kind':kind,'step':index,'file':name,'sha256':digest};artifacts.append(item);return item
        context_packs=[]
        for index,row in enumerate(bound):
            item=keep('context_pack',index,row['file'],row['sha256'],'load-context/'+row['name'])
            context_packs.append({**row,'artifact':item['file']})
        params['packs']=native_context.bind([stage/row['artifact'] for row in context_packs])
        with locked_inputs([base,language,*[row['file'] for row in inputs],*[row['file'] for row in context],
                            *[row['file'] for row in bound],*import_preflight['original_input_hashes']]):
            if native_dependencies.needed(records):
                prep=native.run_worker(game,'package-dependencies',params,cache,timeout)
                native.validate_payload('package-dependencies',prep['payload'],params)
                prep_proof=native_dependencies.verify(base,records,prep['payload']['application'])
                if not prep_proof['dependency_preparation_matched']:
                    review=Path(prep['run'])/'dependency-review.json'
                    review.write_text(json.dumps(prep_proof,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
                    raise FormatError(f'Native Mod import preparation changed unexpected data; retained review: {review}')
                application=prep['payload']['application'];preparation_runs.append(prep['run'])
                keep('prepare_baseline',None,application['baseline']['output_file'],application['baseline']['output_sha256'],'prepare-baseline-'+base.name)
                for index,row in enumerate(application['steps']):
                    keep('prepare_step',index,row['output']['output_file'],row['output']['output_sha256'],f'prepare-step-{index}-'+base.name)
                    keep('prepare_mod_data',index,row['mod_data_file'],row['mod_data_sha256'],f'prepare-mod-data-{index}.bin')
                final=application['steps'][-1]['output']
                keep('prepared',None,final['output_file'],final['output_sha256'],'prepared/'+base.name)
                prepared=stage/'prepared'/base.name
                preparation={'proof':prep_proof,'entries':[row['entries'] for row in application['steps']]}
                params={**params,'path':str(prepared),'sha256':file_hash(prepared)}
            result=native.run_worker(game,'package-apply',params,cache,timeout)
            if preparation and native.public_binding(prep['binding'])!=native.public_binding(result['binding']):
                raise FormatError('Native preparation/application backend changed')
        proof=verify_application(prepared,params,result['payload'],targets)
        if preparation:proof['dependency_preparation']=preparation['proof']
        if file_hash(base)!=info['sha256'] or file_hash(language)!=params['language_sha256']:
            raise FormatError('Native overlay base/language changed during verification')
        if any(inventory(row['root'])!=row['files'] for row in inputs):raise FormatError('Native overlay Mod inputs changed')
        if any(file_hash(row['file'])!=row['sha256'] for row in context):raise FormatError('Native overlay CLB context changed')
        if any(file_hash(row['file'])!=row['sha256'] for row in bound):raise FormatError('Native overlay RPK context changed')
        if any(file_hash(path)!=digest for path,digest in import_preflight['original_input_hashes'].items()):
            raise FormatError('Native overlay original import context changed')
        baseline_item=result['payload']['application']['baseline']
        for kind,index,source,expected_hash in [('baseline',None,baseline_item['output_file'],baseline_item['output_sha256'])]+[
                ('step',index,row['output']['output_file'],row['output']['output_sha256'])
                for index,row in enumerate(result['payload']['application']['steps'])]:
            name=('baseline-' if kind=='baseline' else f'step-{index}-')+base.name
            keep(kind,index,source,expected_hash,name)
        for index,row in enumerate(result['payload']['application']['steps']):
            name=f'mod-data-{index}.bin';keep('mod_data',index,row['mod_data_file'],row['mod_data_sha256'],name)
        diagnostics=native_validation.diagnostics(result['run'])
        for run in preparation_runs:diagnostics.extend(native_validation.diagnostics(run))
        report={'format':'aow4mod-native-overlay/1','base':{'file':str(base),'sha256':info['sha256'],'pack':info['name']},
            'mod_inputs':inputs,'mods_without_selected_overrides':ignored,'binding':native.public_binding(result['binding']),
            'run':result['run'],'proof':proof,'diagnostics':diagnostics,'artifacts':artifacts,
            'preparation':preparation,'context_packs':context_packs,'import_preflight':import_preflight,
            'review_required':bool(diagnostics or not proof['scoped_application_witness_matched']),
            'status':'scoped_application_matched' if proof['scoped_application_witness_matched'] else 'application_differences',
            'complete_compatibility_verified':False,
            'coverage':'Official native ACP 0x40 application to one explicitly selected RPK in explicit Mod order. Not game activation, launcher precedence, DLC mounting, asset completeness or gameplay validation.'}
        (stage/'native-overlay.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return report
