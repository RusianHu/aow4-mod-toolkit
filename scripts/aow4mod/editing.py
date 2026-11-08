"""Hash-pinned, reviewable property edits applied only to a new staged copy."""
from collections import defaultdict
import json
import os
from pathlib import Path
import shutil
import struct
import tempfile
import math

from .core import FormatError, Node, Resource, mod_file, read_acp, read_rpk, sha
from .semantics import schema_bundle
from .filesystem import copy_directory, new_directory, file_hash, outside

PLAN_FORMAT = "aow4mod-edit-plan/1"


def rewrite_map(node, updates):
    fields = node.mapping()
    if all((key not in fields and value is None) or (key in fields and value == fields[key].raw()) for key,value in updates.items()):
        return node.raw()
    values = {k:v.raw() for k,v in fields.items()}
    for key,value in updates.items():
        if value is None:
            values.pop(key,None)
        else:
            values[key]=value
    # Retain extended entry choices for existing fields, even if offsets shrink.
    pos=node.start;tag=node.data[pos];pos+=1;small=tag&127;large=0
    if tag&128:
        large=struct.unpack_from('<I',node.data,pos)[0];pos+=4
    pos+=small*2
    extended_keys={struct.unpack_from('<II',node.data,pos+i*8)[0] for i in range(large)}
    payload=bytearray();compact=[];extended=[]
    for key,value in values.items():
        offset=len(payload)
        if key<256 and offset<256 and key not in extended_keys and len(compact)<127:
            compact.append(struct.pack('<BB',key,offset))
        else:
            extended.append(struct.pack('<II',key,offset))
        payload.extend(value)
    header=bytes([len(compact)|(128 if extended else 0)])
    if extended:header+=struct.pack('<I',len(extended))
    rebuilt=header+b''.join(compact)+b''.join(extended)+payload
    check=Node(bytes(rebuilt),0,len(rebuilt)).mapping()
    if {k:v.raw() for k,v in check.items()} != values:
        raise FormatError('Rebuilt map did not preserve field payloads')
    return bytes(rebuilt)


def rewrite_typed(node,updates):
    node.typed()  # validate before copying the type prefix
    return node.data[node.start:node.start+4]+rewrite_map(Node(node.data,node.start+4,node.end),updates)


def rewrite_wrapper(node,kind,updates):
    actual,outer=node.typed()
    if actual!=kind or set(outer)!={kind}:
        raise FormatError('Unsupported wrapped class')
    return rewrite_typed(node,{kind:rewrite_map(outer[kind],updates)})


def rewrite_array(node,transform):
    node.array()
    inner=node.mapping()[1]
    changes={i:transform(child,i) for i,child in inner.mapping().items()}
    return rewrite_map(node,{1:rewrite_map(inner,changes)})


def replace_array_items(node,raw_items):
    """Reindex the serialized array; resource locators themselves remain stable."""
    node.array();inner=node.mapping()[1]
    updates={k:None for k in inner.mapping()}
    updates.update(dict(enumerate(raw_items)))
    return rewrite_map(node,{1:rewrite_map(inner,updates)})


def transform_records(data,suffix,transform,canonical_changed_wrappers=False):
    """Transform/remove resource definitions; never express an engine delete opcode."""
    def transform_array(node,callback):
        return replace_array_items(node,[raw for child in node.array().values() if (raw:=callback(child)) is not None])
    if suffix=='.acp':
        root=Node(data,19,len(data));kind,outer=root.typed();fields=outer[kind].mapping()
        if kind!=0x40009d:raise FormatError('Unsupported ACP root')
        if 0x26 not in fields:return data
        def pack(node):
            pk,po=node.typed();pf=po[pk].mapping()
            if pk!=0x401204:raise FormatError('Unsupported ACP pack record')
            def change(node):
                ck,co=node.typed();cf=co[ck].mapping()
                if ck!=0x401206 or cf[2].uint(4)!=0x40:raise FormatError('Unverified ACP operation')
                raw=transform(pf[1].string(),cf[1])
                if raw is not None and raw!=cf[1].raw() and canonical_changed_wrappers:
                    # ACP 0x40 wrapper offsets can cross the compact/extended
                    # boundary when a linked name/locator shrinks. Re-encode
                    # this known wrapper only; preserve every member payload
                    # except the explicitly transformed resource body.
                    values={k:v.raw() for k,v in cf.items()};values[1]=raw
                    return rewrite_typed(node,{ck:rewrite_map(Node(b'\0',0,1),values)})
                return rewrite_wrapper(node,ck,{1:raw}) if raw is not None else None
            raw=transform_array(pf[2],change)
            return rewrite_wrapper(node,pk,{2:raw})
        changes=transform_array(fields[0x26],pack)
        updates={0x26:changes}
        if 0x25 in fields:
            if fields[0x25].uint(4)!=len(fields[0x26].raw()):raise FormatError('Unexpected change blob length')
            updates[0x25]=struct.pack('<I',len(changes))
        return data[:19]+rewrite_wrapper(root,kind,updates)
    if suffix=='.rpk':
        root=Node(data,0xb0,len(data));fields=root.mapping();pack_name=fields[0x16].string()
        changed={}
        for key,node in fields[13].mapping().items():
            _,outer=node.typed();pf=outer[0x33].mapping()
            raw=transform_array(pf[0x15],lambda n:transform(pack_name,n))
            changed[key]=rewrite_typed(node,{0x33:rewrite_map(outer[0x33],{0x15:raw})})
        payload=rewrite_map(root,{13:rewrite_map(fields[13],changed)})
        return data[:12]+struct.pack('<I',len(payload))+data[16:0xb0]+payload
    raise FormatError('Unsupported resource file')


def property_parts(path):
    parts=path.split('/')
    if len(parts)!=2:
        raise FormatError('Property path must be class-id/field-id, e.g. 0x00471C6D/0x01')
    a,b=(int(p,16) for p in parts)
    if not 0x10000<=a<=0xffffffff or not 0<=b<=0xffffffff:
        raise FormatError('Property identifiers are out of bounds')
    return a,b,f'0x{a:08X}/0x{b:02X}'


def encode_value(codec,value):
    if codec in ('u8','u16','u32','u64','i8','i16','i32','i64'):
        bits=int(codec[1:]);signed=codec[0]=='i'
        low=-(1<<(bits-1)) if signed else 0;high=(1<<(bits-int(signed)))-1
        if type(value) is not int or not low<=value<=high:raise FormatError(f'{codec} value must be an integer in range')
        return value.to_bytes(bits//8,'little',signed=signed)
    if codec=='bool':
        if type(value) is not bool:raise FormatError('bool codec requires JSON true or false')
        return bytes([value])
    if codec in ('f32','f64','vec2f','vec3f','vec4f'):
        count=int(codec[3]) if codec.startswith('vec') else 1
        values=value if count>1 else [value]
        if not isinstance(values,list) or len(values)!=count or any(type(v) not in (int,float) for v in values):
            raise FormatError('Float/vector values must be finite JSON numbers of the specified dimension')
        try:
            if not all(math.isfinite(v) for v in values):raise FormatError('Float/vector values must be finite')
            return struct.pack('<'+('d' if codec=='f64' else 'f')*count,*values)
        except (OverflowError,struct.error) as exc:raise FormatError('Float value exceeds storage range') from exc
    if codec=='string':
        if not isinstance(value,str):raise FormatError('string codec requires text')
        raw=value.encode('utf8');return struct.pack('<I',len(raw))+raw
    if codec=='hex':
        if not isinstance(value,str):raise FormatError('hex codec requires a hex string')
        return bytes.fromhex(value)
    if codec=='unset':
        if value is not None:raise FormatError('unset takes null')
        return None
    raise FormatError('Unsupported codec')


def resources_in_file(path):
    if path.suffix.lower()=='.acp':
        _,changes=read_acp(path)
        if any(c['operation_raw']!=0x40 for c in changes):
            raise FormatError('Editing unverified ACP operations is unsupported')
        return [(c['pack'],c['resource']) for c in changes]
    if path.suffix.lower()=='.rpk':
        info,resources=read_rpk(path)
        return [(info['name'],r) for r in resources]
    raise FormatError('Editing accepts ACP and RPK only')


def codec_evidence(schema,codec):
    if schema and schema.get('curated') and schema.get('codec')==codec:return 'evidenced_property_codec'
    if schema and schema.get('storage_validated') and (codec==schema.get('codec') or codec in schema.get('allowed_codecs',[])):
        return 'evidenced_storage_codec'
    return 'structural_only'


def make_plan(path,rid,property_path,value,codec='auto',pack=None,game=None,allow_opaque=False,schema_path=None):
    if len(property_path.split('/'))>2:
        from .structured import make_nested_plan
        return make_nested_plan(path,rid,property_path,value,codec,pack,game,allow_opaque)
    path=Path(path)
    source=mod_file(path) if path.is_dir() else path.resolve()
    class_id,field_id,normalized=property_parts(property_path)
    matches=[(p,r) for p,r in resources_in_file(source) if r.rid==int(rid,16) and (pack is None or p.casefold()==pack.casefold())]
    if len(matches)!=1:raise FormatError(f'Expected one matching resource, found {len(matches)}')
    actual_pack,resource=matches[0]
    if class_id not in resource.fields:
        raise FormatError('Adding an unverified class section is unsupported')
    fields=resource.fields[class_id].mapping()
    previous=fields[field_id].raw() if field_id in fields else None
    status,schemas,_=schema_bundle(game,schema_path)
    schema=schemas.get(normalized)
    if codec=='auto':
        if not schema or schema.get('codec') not in ('u32','bool'):
            raise FormatError('No automatic codec: inspect schema and choose an explicit integer codec; unknown storage requires --allow-opaque')
        codec=schema['codec']
    level=codec_evidence(schema,codec)
    if level=='structural_only' and not allow_opaque:
        raise FormatError('Property codec is unverified; --allow-opaque is required for explicit structural-only edits')
    updated=encode_value(codec,value)
    if schema and schema.get('enum') and codec=='u32' and value not in schema['enum']:
        raise FormatError('Value is not in the verified enum')
    if level=='evidenced_storage_codec' and previous is not None:
        if len(previous)!=schema['width'] or codec=='bool' and previous not in (b'\0',b'\1'):
            raise FormatError('Existing property does not match validated storage')
    return {'format':PLAN_FORMAT,'source':str(source),'source_sha256':sha(source.read_bytes()),
            'schema_tool_sha256':status['observed_tool_sha256'] if level!='structural_only' else None,
            'schema_path':status.get('schema_path') if level=='evidenced_storage_codec' else None,
            'schema_sha256':status.get('schema_sha256') if level=='evidenced_storage_codec' else None,
            'game':str(game) if game else None,'validation_level':level,
            'operations':[{'pack':actual_pack,'id':f'{resource.rid:016X}','type_id':resource.type_id,
                           'name':resource.name,'property':normalized,'codec':codec,'value':value,
                           'expected_hex':previous.hex() if previous is not None else None,
                           'replacement_hex':updated.hex() if updated is not None else None}],
            'note':'Plan only. Apply creates a separate staged copy; source files and Workshop remain unchanged.'}


def patch_resource(node,operations):
    resource=Resource.parse(node)
    groups=defaultdict(dict)
    for op in operations:
        class_id,field_id,_=property_parts(op['property'])
        if resource.type_id!=op['type_id'] or class_id not in resource.fields:
            raise FormatError('Resource type/class changed')
        fields=resource.fields[class_id].mapping()
        before=fields[field_id].raw().hex() if field_id in fields else None
        if before!=op['expected_hex']:raise FormatError('Expected property bytes do not match')
        if field_id in groups[class_id]:raise FormatError('Duplicate edit to the same field')
        replacement=encode_value(op['codec'],op['value'])
        if (replacement.hex() if replacement is not None else None)!=op['replacement_hex']:
            raise FormatError('Plan codec and replacement bytes disagree')
        groups[class_id][field_id]=replacement
    return rewrite_typed(node,{cls:rewrite_map(resource.fields[cls],changes) for cls,changes in groups.items()})


def patch_bytes(data,suffix,operations):
    operations_by_key=defaultdict(list)
    for op in operations:operations_by_key[(op['pack'].casefold(),int(op['id'],16))].append(op)
    applied=set()
    def transform_resource(pack,node):
        r=Resource.parse(node);k=(pack.casefold(),r.rid)
        if k not in operations_by_key:return node.raw()
        if k in applied:raise FormatError('Duplicate target identity')
        applied.add(k)
        return patch_resource(node,operations_by_key[k])
    if suffix=='.acp':
        root=Node(data,19,len(data));kind,outer=root.typed()
        if kind!=0x40009d or set(outer)!={kind}:raise FormatError('Unsupported ACP root')
        fields=outer[kind].mapping()
        if 0x26 not in fields:raise FormatError('No ACP overrides to edit')
        def transform_pack(node,_):
            pk,po=node.typed()
            if pk!=0x401204:raise FormatError('Unsupported pack change class')
            pf=po[pk].mapping();pack=pf[1].string()
            def transform_change(change,_):
                ck,co=change.typed()
                if ck!=0x401206:raise FormatError('Unsupported resource change class')
                cf=co[ck].mapping()
                return rewrite_wrapper(change,ck,{1:transform_resource(pack,cf[1])})
            return rewrite_wrapper(node,pk,{2:rewrite_array(pf[2],transform_change)})
        changes=rewrite_array(fields[0x26],transform_pack)
        updates={0x26:changes}
        if 0x25 in fields:
            if fields[0x25].uint(4)!=len(fields[0x26].raw()):raise FormatError('Unrecognized ACP change-blob length field')
            updates[0x25]=struct.pack('<I',len(changes))
        rebuilt=data[:19]+rewrite_wrapper(root,kind,updates)
    elif suffix=='.rpk':
        root=Node(data,0xb0,len(data));fields=root.mapping()
        pack=fields[0x16].string()
        def container(node):
            _,outer=node.typed();pf=outer[0x33].mapping()
            objects=rewrite_array(pf[0x15],lambda obj,_:transform_resource(pack,obj))
            return rewrite_typed(node,{0x33:rewrite_map(outer[0x33],{0x15:objects})})
        containers=fields[13].mapping()
        payload=rewrite_map(root,{13:rewrite_map(fields[13],{i:container(n) for i,n in containers.items()})})
        rebuilt=data[:12]+struct.pack('<I',len(payload))+data[16:0xb0]+payload
    else:raise FormatError('Unsupported edit format')
    if set(operations_by_key)!=applied:raise FormatError('Some planned resources were not found')
    return rebuilt


def validate_plan(plan):
    if not isinstance(plan,dict) or plan.get('format')!=PLAN_FORMAT:raise FormatError('Unsupported edit plan')
    for key in ('source','source_sha256'):
        if not isinstance(plan.get(key),str):raise FormatError(f'Invalid plan {key}')
    if not isinstance(plan.get('operations'),list) or not plan['operations']:
        raise FormatError('Edit plan must contain operations')
    level=plan.get('validation_level')
    if level not in ('evidenced_property_codec','evidenced_storage_codec','structural_only'):
        raise FormatError('Unknown edit validation level')
    identities=set()
    for op in plan['operations']:
        if not isinstance(op,dict):raise FormatError('Operations must be objects')
        for key in ('pack','id','property','codec'):
            if not isinstance(op.get(key),str):raise FormatError(f'Invalid operation {key}')
        if type(op.get('type_id')) is not int or not 0<=op['type_id']<=0xffffffff:
            raise FormatError('Invalid resource type ID')
        if not 0<int(op['id'],16)<=0xffffffffffffffff:raise FormatError('Invalid resource ID')
        _,_,prop=property_parts(op['property'])
        identity=(op['pack'].casefold(),int(op['id'],16),prop)
        if identity in identities:raise FormatError('Duplicate property operation')
        identities.add(identity)
        for key in ('expected_hex','replacement_hex'):
            if key not in op or (op[key] is not None and not isinstance(op[key],str)):
                raise FormatError(f'Invalid {key}')
            if op[key] is not None:bytes.fromhex(op[key])
        if 'value' not in op:raise FormatError('Missing operation value')
        encoded=encode_value(op['codec'],op['value'])
        if (encoded.hex() if encoded is not None else None)!=op['replacement_hex']:
            raise FormatError('Plan codec and replacement bytes disagree')
        if 'nested' in op:
            from .structured import validate_nested_operation
            validate_nested_operation(op)
        if 'array_edit' in op:
            from .structured import validate_array_operation
            validate_array_operation(op)
    source=Path(plan['source']).resolve()
    data=source.read_bytes()
    if sha(data)!=plan['source_sha256']:raise FormatError('Source changed since edit planning')
    for path,expected in plan.get('required_inputs',{}).items():
        if file_hash(path)!=expected:raise FormatError('A required input changed since planning')
    if plan.get('schema_tool_sha256'):
        if plan.get('schema_path') and file_hash(plan['schema_path'])!=plan.get('schema_sha256'):
            raise FormatError('Extracted schema changed since planning')
        status,schemas,_=schema_bundle(plan.get('game'),plan.get('schema_path'))
        if status['observed_tool_sha256']!=plan['schema_tool_sha256']:
            raise FormatError('Game schema changed since planning')
    if level in ('evidenced_property_codec','evidenced_storage_codec'):
        if not plan.get('schema_tool_sha256'):
            raise FormatError('Evidenced edit requires a bound schema')
        if level=='evidenced_storage_codec' and not plan.get('schema_path'):
            raise FormatError('Storage-validated edit requires a bound extracted schema')
        for op in plan['operations']:
            _,_,prop=property_parts(op['property'])
            schema=schemas.get(prop)
            if codec_evidence(schema,op['codec'])!=level:
                raise FormatError('Operation does not match the evidenced codec')
            if 'enum' in schema and op['value'] not in schema['enum']:
                raise FormatError('Operation value is outside the evidenced enum')
    return source,data


def verify_changes(before,after,operations):
    if len(before)!=len(after):raise FormatError('Resource count changed unexpectedly')
    expected={(op['pack'].casefold(),int(op['id'],16),property_parts(op['property'])[2]):op['replacement_hex'] for op in operations}
    observed=set()
    for (bp,br),(ap,ar) in zip(before,after):
        if (bp,br.rid,br.type_id)!=(ap,ar.rid,ar.type_id):raise FormatError('Resource identity/order changed')
        bf,af=br.leaves(),ar.leaves()
        for field in bf.keys()|af.keys():
            k=(bp.casefold(),br.rid,field)
            if bf.get(field)!=af.get(field):
                value=af[field].hex() if field in af else None
                if k not in expected or expected[k]!=value:raise FormatError('Unexpected property change after serialization')
                observed.add(k)
    return len(observed)


def apply_project_plans(plans,source_root,output,pack_import_updates=None):
    source_root=Path(source_root).resolve();output=Path(output).resolve()
    prepared=[];seen=set();protected=[]
    for plan in plans:
        source,data=validate_plan(plan)
        if source in seen:raise FormatError('Use one plan per source file')
        seen.add(source)
        try:relative=source.relative_to(source_root)
        except ValueError:raise FormatError('Planned source is outside project')
        if plan.get('game'):protected.append(Path(plan['game']))
        updated=patch_bytes(data,source.suffix.lower(),plan['operations'])
        prepared.append((plan,source,relative,data,updated,resources_in_file(source)))
    if not prepared:raise FormatError('No edits to apply')
    receipts=[]
    with copy_directory(source_root,output,protected) as (payload,source_manifest):
        for plan,source,relative,data,updated,before in prepared:
            target=payload/relative;target.write_bytes(updated)
            count=verify_changes(before,resources_in_file(target),plan['operations'])
            imports = (pack_import_updates or {}).get(relative.as_posix())
            if imports is not None:
                from . import pack_imports
                if source.suffix.lower()=='.acp' and imports.get('kind')=='acp_mod_dependencies':
                    from . import overlay_imports
                    key='optional' if imports['optional'] else 'required'
                    if overlay_imports.selected(updated,imports['pack'])[key]!=imports['before']:
                        raise FormatError('ACP imports changed before reference staging')
                    updated=overlay_imports.rewrite(updated,imports['pack'],imports['after'],imports['optional'])
                elif source.suffix.lower()=='.rpk' and 'kind' not in imports:
                    info,_ = read_rpk(target)
                    if info['dependencies'] != imports['before']:raise FormatError('Pack imports changed before reference staging')
                    updated = pack_imports.rewrite(updated,imports['after'])
                else:raise FormatError('Import update does not match its source format')
                target.write_bytes(updated)
                count=verify_changes(before,resources_in_file(target),plan['operations'])
            receipts.append({'source':str(source),'source_sha256':sha(data),'result_sha256':sha(updated),
                'output_file':str(output/relative),'verified_changed_fields':count,
                'validation_level':plan['validation_level'],'plan':plan})
        for plan,_,_,_,_,_ in prepared:validate_plan(plan)
        if set(pack_import_updates or {}) - {item[2].as_posix() for item in prepared}:
            raise FormatError('Import update refers to an unedited file')
        result={'format':'aow4mod-project-edit-receipt/1','source_root':str(source_root),
                'source_files':source_manifest,'output':str(output),'files':receipts,'gameplay_tested':False}
        (payload/'.aow4mod-edit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return result


def apply_plan(plan,output):
    source,data=validate_plan(plan);output=Path(output).resolve()
    if source.suffix.lower()=='.acp':
        project=apply_project_plans([plan],source.parent,output)
        receipt=project['files'][0]
        return {'format':'aow4mod-edit-receipt/1',**receipt,'gameplay_tested':False}
    updated=patch_bytes(data,source.suffix.lower(),plan['operations']);before=resources_in_file(source)
    protected=[source.parent]+([Path(plan['game'])] if plan.get('game') else [])
    with new_directory(output,protected) as payload:
        target=payload/source.name;target.write_bytes(updated)
        count=verify_changes(before,resources_in_file(target),plan['operations'])
        validate_plan(plan)
        result={'format':'aow4mod-edit-receipt/1','source':str(source),'source_sha256':sha(data),
                'result_sha256':sha(updated),'output_file':str(output/source.name),
                'verified_changed_fields':count,'validation_level':plan['validation_level'],'gameplay_tested':False,'plan':plan}
        (payload/'.aow4mod-edit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return result
