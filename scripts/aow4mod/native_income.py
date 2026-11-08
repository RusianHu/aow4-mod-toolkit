"""Reviewed colony flat-income value objects; native IO and independent reads.

The public primitive property API does not expose the table. A legitimate parent
owns its legitimate inline values; only the selected value is deserialized using
official object IO. No fabricated object, pointer or direct memory write is used.
"""
import json
import struct
from pathlib import Path

from . import editing, linking, native, native_edits, native_inspection, native_roundtrip, native_validation
from .core import FormatError, Node, mod_file, sha
from .filesystem import copy_directory, file_hash, inventory

FORMAT='aow4mod-income-plan/1'
OWNER=0x47223d;BLOCK=0x473ea3;VALUE=0x473ea2;RESOURCE=0x473e9f


def fingerprint():
    root=Path(__file__).parent
    return sha(b''.join((root/n).read_bytes() for n in ('native_income.py','native_edits.py','editing.py','core.py','filesystem.py')))


def amount_value(value):
    if type(value) is not int or not -(2**31)<=value<2**31:
        raise FormatError('Income amount requires a signed 32-bit integer')
    return value


def value_fields(data):
    top=Node(data,0,len(data)).mapping()
    if set(top)!={VALUE}:raise FormatError('Income value has an unsupported class scope')
    fields=top[VALUE].mapping()
    if not {1}<=set(fields)<={1,2}:raise FormatError('Income value has unsupported fields')
    handle=fields[1].mapping()
    if set(handle)!={2} or len(handle[2].raw())!=8:raise FormatError('Income resource handle layout differs')
    guid=f'{handle[2].uint(8):016X}'
    if int(guid,16)==0:raise FormatError('Income resource is null')
    raw=fields[2].raw() if 2 in fields else b'\0'*4
    if len(raw)!=4:raise FormatError('Income amount storage must be four bytes')
    return guid,int.from_bytes(raw,'little',signed=True)


def value_bytes(data,value):
    amount_value(value);value_fields(data);top=Node(data,0,len(data))
    return editing.rewrite_map(top,{VALUE:editing.rewrite_map(top.mapping()[VALUE],{2:struct.pack('<i',value) if value else None})})


def flat_rows(data):
    top=Node(data,0,len(data)).mapping()
    if OWNER not in top or 0x1f not in top[OWNER].mapping():raise FormatError('Existing Income Flat block is required')
    block=top[OWNER].mapping()[0x1f].mapping()
    if set(block)!={BLOCK} or set(block[BLOCK].mapping())!={1}:raise FormatError('Unsupported flat-income block')
    rows=block[BLOCK].mapping()[1].mapping()
    count=rows[0xa].uint(4) if 0xa in rows else 0
    if not 1<=count<=128 or set(rows)!={0xa,*range(0xb,0xb+count)}:raise FormatError('Income rows are empty, malformed or exceed 128')
    data_rows=[rows[0xb+i].raw() for i in range(count)]
    for row in data_rows:value_fields(row)
    return data_rows


def replace_rows(data,rows):
    original=flat_rows(data)
    if len(rows)!=len(original):raise FormatError('Income edit cannot change row count')
    root=Node(data,0,len(data));owner=root.mapping()[OWNER];block=owner.mapping()[0x1f]
    inner=block.mapping()[BLOCK];array=inner.mapping()[1]
    array_bytes=editing.rewrite_map(array,{0xb+i:row for i,row in enumerate(rows)})
    block_bytes=editing.rewrite_map(block,{BLOCK:editing.rewrite_map(inner,{1:array_bytes})})
    return editing.rewrite_map(root,{OWNER:editing.rewrite_map(owner,{0x1f:block_bytes})})


def worker(api,params,run,edit=False):
    import ctypes as c
    body=bytes.fromhex(params['body_hex'])
    if params.get('cid')!='0047223D' or sha(body)!=params['body_sha256']:raise FormatError('Income input identity/hash differs')
    requested=flat_rows(body);target=params['resource_id']
    if edit:amount_value(params['amount'])
    profile=api.profile['income']
    if (profile['owner_cid'],profile['block_cid'],profile['value_cid'],profile['stride'])!=(OWNER,BLOCK,VALUE,64):raise FormatError('Income ABI profile differs')
    def exact_class(ptr,cid):
        address=api.qword(api.qword(ptr)+api.profile['class_getter_vtable_offset'])
        if c.CFUNCTYPE(c.c_uint32,c.c_void_p)(address)(ptr)!=cid:raise FormatError('Native income object class differs')
    def artifact(name,data):
        p=Path(run)/(name+'.bin');p.write_bytes(data)
        return {name+'_file':str(p),name+'_sha256':sha(data)}
    handle=api.create(OWNER)
    try:
        with api.borrowed(handle,OWNER) as parent:
            api.deserialize_object(parent,body);baseline=api.serialize_object(parent)
            block=parent+profile['owner_offset'];exact_class(block,BLOCK)
            count=int.from_bytes(api.memory(block+profile['count_offset'],4),'little')
            array=api.qword(block+profile['data_offset'])
            if count!=len(requested) or not array:raise FormatError('Native income row count/data differs')
            def observe(prefix):
                result=[]
                for i in range(count):
                    ptr=array+i*profile['stride'];exact_class(ptr,VALUE);data=api.serialize_object(ptr)
                    rid,value=value_fields(data)
                    actual=int.from_bytes(api.memory(ptr+profile['amount_offset'],4),'little',signed=True)
                    if actual!=value:raise FormatError('Native memory and serialized income disagree')
                    result.append({'ordinal':i,'resource_id':rid,'amount':actual,**artifact(prefix+str(i),data)})
                return result
            before=observe('before-');matches=[r['ordinal'] for r in before if r['resource_id']==target]
            if len(matches)!=1:raise FormatError('Income resource is missing or ambiguous')
            if edit:
                i=matches[0];raw=Path(before[i]['before-'+str(i)+'_file']).read_bytes()
                api.deserialize_object(array+i*profile['stride'],value_bytes(raw,params['amount']))
            after=observe('after-');output=api.serialize_object(parent)
            return {'cid':'0047223D','edited':edit,'before':before,'after':after,
                    **artifact('input',body),**artifact('baseline',baseline),**artifact('output',output)}
    finally:api.release(handle)


def validate_payload(operation,payload,params):
    if payload.get('cid')!='0047223D' or payload.get('edited') is not (operation=='income-edit'):
        raise FormatError('Native income worker identity/operation differs')
    for key in ('before','after'):
        rows=payload.get(key)
        if not isinstance(rows,list) or not 1<=len(rows)<=128:raise FormatError('Invalid native income row witnesses')
        for i,row in enumerate(rows):
            if row.get('ordinal')!=i or not isinstance(row.get('resource_id'),str) or len(row['resource_id'])!=16:
                raise FormatError('Invalid native income row identity/order')
            amount_value(row.get('amount'))
    if len(payload['before'])!=len(payload['after']):raise FormatError('Native income row count changed')
    return payload


def material(result,body,target,amount=None):
    operation='income-read' if amount is None else 'income-edit'
    if result.get('operation')!=operation:raise FormatError('Income evidence operation differs')
    p=result['params'];payload=result['payload'];run=result['run']
    validate_payload(operation,payload,p)
    if (p.get('cid')!='0047223D' or p.get('resource_id')!=target or p.get('body_hex')!=body.hex()
            or p.get('body_sha256')!=sha(body) or (amount is not None and p.get('amount')!=amount)):
        raise FormatError('Income request does not bind the source and assignment')
    artifact=lambda row,key:native_edits.artifact(row,key,run,key+'.bin')
    if artifact(payload,'input')!=body or artifact(payload,'baseline')!=body:raise FormatError('Income source normalizes or its witness differs')
    before=flat_rows(body);after=[];selected=[]
    for i,(old,a,b) in enumerate(zip(before,payload['before'],payload['after'])):
        rid,value=value_fields(old)
        if artifact(a,'before-'+str(i))!=old or (a['resource_id'],a['amount'])!=(rid,value):raise FormatError('Income original row witness differs')
        expected=value
        if rid==target:
            selected.append(i)
            if amount is not None:expected=amount_value(amount)
        raw=artifact(b,'after-'+str(i))
        if raw!=(value_bytes(old,expected) if amount is not None and rid==target else old):raise FormatError('Income edit changes another row or resource handle')
        if value_fields(raw)!=(rid,expected) or (b['resource_id'],b['amount'])!=(rid,expected):raise FormatError('Income independent value witness differs')
        after.append(raw)
    if len(before)!=len(payload['before']) or len(selected)!=1:raise FormatError('Income selection is missing or ambiguous')
    output=artifact(payload,'output')
    if output!=replace_rows(body,after):raise FormatError('Income output changes unrelated parent data')
    return output,{'resource_id':target,'ordinal':selected[0],'before':value_fields(before[selected[0]])[1],
                   'after':value_fields(after[selected[0]])[1],'rows':len(before),'unrelated_data_preserved':True}


def make_plan(path,rid,resource_id,amount,game,index=None,pack=None,source_root=None,cache_dir=Path('.aow4mod/native'),timeout=45):
    amount_value(amount);resource_id=f'{int(resource_id,16):016X}'
    body,source=native_inspection.source_body(path,rid,pack)
    if source['cid']!='0047223D':raise FormatError('Income editor requires TColonyIncomeProperty')
    file=Path(source['file']);root=Path(source_root).resolve() if source_root else file.parent
    if not file.is_relative_to(root):raise FormatError('Income source is outside its Mod')
    mod_file(root);files=inventory(root);binding=native.profile_for(game)
    target,target_file,target_hash=linking.target_resource(index,resource_id)
    if target.type_id!=RESOURCE:raise FormatError('Income resource is not a global resource definition')
    cache=native.check_cache_directory(cache_dir,game,[root,Path(target_file).parent])
    params={'cid':source['cid'],'body_hex':body.hex(),'body_sha256':sha(body),'resource_id':resource_id,'amount':amount}
    result=native.run_worker(game,'income-edit',params,cache,timeout);after,proof=material(result,body,resource_id,amount)
    reader=native.run_worker(game,'income-read',{k:v for k,v in {**params,'body_hex':after.hex(),'body_sha256':sha(after)}.items() if k!='amount'},cache,timeout)
    read,read_proof=material(reader,after,resource_id)
    if read!=after or read_proof['after']!=amount:raise FormatError('Independent income reader differs')
    data=native_edits.patch_file(file.read_bytes(),file.suffix.lower(),source['resource_id'],source['pack'],source['cid'],after)
    candidate=Path(result['run'])/('income-candidate'+file.suffix);candidate.write_bytes(data)
    wrapper=native_roundtrip.run(candidate,game,cache/('income-pack-'+Path(result['run']).name),cache,timeout,
        libraries=list(root.rglob('*.clb'))) if file.suffix.lower()=='.rpk' else None
    if inventory(root)!=files or file_hash(target_file)!=target_hash:raise FormatError('Income source/context changed during planning')
    return {'format':FORMAT,'generator_sha256':fingerprint(),'game':str(Path(game).resolve()),'binding':native.public_binding(binding),
            'source_root':str(root),'source_files':files,'source':source,'resource_id':resource_id,'amount':amount,
            'resource_definition':{'file':str(target_file),'sha256':target_hash},'native_result':result,'independent_result':reader,
            'proof':proof,'candidate_file':str(candidate),'file_sha256':sha(data),'native_pack':wrapper,
            'review_required':bool(wrapper and (wrapper['diagnostics'] or not wrapper['comparison']['all_serialized_data_equal']))}


def apply_plan(plan,output):
    if plan.get('format')!=FORMAT or plan.get('generator_sha256')!=fingerprint():raise FormatError('Income plan is unsupported or outdated')
    root=Path(plan['source_root']);game=Path(plan['game']);source=plan['source'];file=Path(source['file'])
    if not file.is_relative_to(root) or inventory(root)!=plan['source_files']:raise FormatError('Income source Mod changed')
    definition=plan['resource_definition']
    target,path,digest=linking.target_resource(None,plan['resource_id'],definition['file'])
    if target.type_id!=RESOURCE or digest!=definition['sha256']:raise FormatError('Income resource definition changed')
    binding=native.profile_for(game);result=plan['native_result'];reader=plan['independent_result']
    if native.public_binding(binding)!=plan['binding'] or any(r['binding']!=binding for r in (result,reader)):
        raise FormatError('Income backend binding changed')
    if Path(result['run']).resolve()==Path(reader['run']).resolve():raise FormatError('Income reader must use an independent process')
    for r in (result,reader):native.check_cache_directory(r['run'],game,[root,Path(path).parent])
    body,observed=native_inspection.source_body(file,source['resource_id'],source['pack'])
    if observed!=source:raise FormatError('Income selected source changed')
    after,proof=material(result,body,plan['resource_id'],amount_value(plan['amount']))
    read,read_proof=material(reader,after,plan['resource_id'])
    if read!=after or read_proof['after']!=plan['amount'] or proof!=plan['proof']:raise FormatError('Income reader or proof differs')
    data=native_edits.patch_file(file.read_bytes(),file.suffix.lower(),source['resource_id'],source['pack'],source['cid'],after)
    if sha(data)!=plan['file_sha256'] or file_hash(plan['candidate_file'])!=sha(data):raise FormatError('Income candidate differs')
    wrapper=plan['native_pack']
    if (wrapper is None)!=(file.suffix.lower()=='.acp'):raise FormatError('Income pack evidence is missing')
    if wrapper and (native_roundtrip.compare(Path(plan['candidate_file']),Path(wrapper['output_file']))!=wrapper['comparison']
            or wrapper['binding']!=plan['binding'] or native_validation.diagnostics(wrapper['run'])!=wrapper['diagnostics']):
        raise FormatError('Income pack evidence changed')
    review=bool(wrapper and (wrapper['diagnostics'] or not wrapper['comparison']['all_serialized_data_equal']))
    if review!=plan['review_required']:raise FormatError('Income review findings were changed')
    with copy_directory(root,output,[game]) as (stage,files):
        if files!=plan['source_files']:raise FormatError('Income source changed before staging')
        (stage/file.relative_to(root)).write_bytes(data)
        receipt={'format':'aow4mod-income-receipt/1','proof':proof,'native_pack_review_required':review,'gameplay_tested':False}
        (stage/'.aow4mod-income.json').write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
    return receipt
