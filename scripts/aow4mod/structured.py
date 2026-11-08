"""Bounded inspection and explicit structural edits inside serialized properties."""
from pathlib import Path

from . import editing
from .core import FormatError,Node,describe,mod_file,sha
from .filesystem import file_hash


def path_keys(path):
    parts=path.split('/')
    if not 2<=len(parts)<=64:raise FormatError('Property paths require 2..64 hexadecimal components')
    keys=[int(p,16) for p in parts]
    if keys[0]<0x10000 or any(not 0<=k<=0xffffffff for k in keys):raise FormatError('Invalid property path')
    return keys


def children(node,required_key=None):
    # A length-prefixed UTF-8 string can accidentally resemble a compact map.
    try:node.string()
    except FormatError:pass
    else:raise FormatError('Cannot descend into a string')
    candidates=[]
    try:
        kind,fields=node.typed()
        if 0x10000<=kind<0x10000000:candidates.append((True,fields,kind))
    except FormatError:pass
    try:candidates.append((False,node.mapping(),None))
    except FormatError:pass
    if required_key is not None:
        candidates=[candidate for candidate in candidates if required_key in candidate[1]]
    if len(candidates)!=1:raise FormatError('Node has no unique recognized map/object layout')
    return candidates[0]


def find_node(node,keys):
    for key in keys:
        _,fields,_=children(node,key)
        if key not in fields:raise FormatError('Nested path is absent')
        node=fields[key]
    return node


def replace(node,keys,raw):
    if not keys:return raw
    key=keys[0];typed,fields,_=children(node,key)
    if key not in fields:raise FormatError('Nested path is absent; create a verified template rather than guessing its structure')
    if raw is None and len(keys)==2 and keys[0]==1:
        try:array=node.array()
        except FormatError:array=None
        if array is not None:
            if keys[1] not in array:raise FormatError('Array index is absent')
            return editing.replace_array_items(node,[child.raw() for i,child in array.items() if i!=keys[1]])
    changed=replace(fields[key],keys[1:],raw)
    return (editing.rewrite_typed if typed else editing.rewrite_map)(node,{key:changed})


def source_resource(path,rid,pack=None):
    path=Path(path);source=mod_file(path) if path.is_dir() else path.resolve()
    matches=[(p,r) for p,r in editing.resources_in_file(source) if r.rid==int(rid,16) and (pack is None or p.casefold()==pack.casefold())]
    if len(matches)!=1:raise FormatError('Source resource is missing or ambiguous')
    return source,*matches[0]


def make_nested_plan(path,rid,property_path,value,codec,pack=None,game=None,allow_opaque=False):
    if codec=='auto' or not allow_opaque:
        raise FormatError('Nested edits require an explicit codec and --allow-opaque; nested domain semantics are not validated')
    keys=path_keys(property_path);source,pack,resource=source_resource(path,rid,pack)
    if keys[0] not in resource.fields:raise FormatError('Resource class is absent')
    fields=resource.fields[keys[0]].mapping()
    if keys[1] not in fields:raise FormatError('Top-level property is absent')
    node=fields[keys[1]];leaf=find_node(node,keys[2:]);raw=editing.encode_value(codec,value)
    updated=replace(node,keys[2:],raw)
    top=f'0x{keys[0]:08X}/0x{keys[1]:02X}'
    plan=editing.make_plan(source,rid,top,updated.hex(),'hex',pack,game,True)
    plan['operations'][0]['nested']={'path':[f'0x{k:X}' for k in keys[2:]],'codec':codec,'value':value,
                                    'expected_hex':leaf.raw().hex(),'replacement_hex':raw.hex() if raw is not None else None}
    plan['note']='Explicit nested structural edit; field-specific semantics remain unverified. Arrays are reindexed when deleting an element.'
    return plan


def validate_nested_operation(op):
    nested=op['nested']
    if op['codec']!='hex' or not isinstance(nested,dict) or op['expected_hex'] is None:raise FormatError('Invalid nested operation')
    keys=[int(k,16) for k in nested['path']]
    if not keys or len(keys)>62:raise FormatError('Invalid nested edit path')
    before=bytes.fromhex(op['expected_hex']);node=Node(before,0,len(before));leaf=find_node(node,keys)
    if leaf.raw().hex()!=nested['expected_hex']:raise FormatError('Nested expected bytes disagree')
    raw=editing.encode_value(nested['codec'],nested['value'])
    if (raw.hex() if raw is not None else None)!=nested['replacement_hex']:raise FormatError('Nested codec and bytes disagree')
    if replace(node,keys,raw).hex()!=op['replacement_hex']:raise FormatError('Nested operation changes unrelated payload bytes')


def array_bytes(node,action,at,item):
    items=[child.raw() for child in node.array().values()]
    if action=='append':
        if at is not None:raise FormatError('Append does not take an index')
        at=len(items)
    if action not in ('append','insert','remove','replace'):raise FormatError('Unsupported array action')
    if type(at) is not int or not 0<=at<=len(items):raise FormatError('Array position is out of bounds')
    if action in ('remove','replace') and at==len(items):raise FormatError('Array element is absent')
    if action!='remove' and item is None:raise FormatError('Array insertion/replacement needs an element')
    if action in ('append','insert'):items.insert(at,item)
    elif action=='remove':items.pop(at)
    else:items[at]=item
    return editing.replace_array_items(node,items)


def copied_element(spec):
    if file_hash(spec['file'])!=spec['sha256']:raise FormatError('Array element template changed')
    _,_,resource=source_resource(spec['file'],spec['id'],spec.get('pack'))
    keys=path_keys(spec['property'])
    if keys[0] not in resource.fields:raise FormatError('Template class is absent')
    return find_node(resource.fields[keys[0]],keys[1:]).raw()


def make_array_plan(path,rid,property_path,action,at=None,codec=None,value=None,copy_from=None,
                    pack=None,game=None,allow_opaque=False):
    if not allow_opaque:raise FormatError('Array edits require --allow-opaque; element/domain constraints are not inferred')
    source,pack,resource=source_resource(path,rid,pack);keys=path_keys(property_path)
    if keys[0] not in resource.fields:raise FormatError('Resource class is absent')
    node=find_node(resource.fields[keys[0]],keys[1:]);count=len(node.array())
    template=None
    if action=='remove':
        if codec is not None or copy_from is not None or value is not None:raise FormatError('Remove does not take an element value')
        item=None
    elif copy_from:
        if codec is not None or value is not None:raise FormatError('Choose a template or a scalar codec, not both')
        template_file,template_pack,_=source_resource(copy_from['file'],copy_from['id'],copy_from.get('pack'))
        template={'file':str(template_file),'id':copy_from['id'],'pack':template_pack,
                  'property':copy_from['property'],'sha256':file_hash(template_file)}
        item=copied_element(template)
    else:
        if codec in (None,'auto','unset'):raise FormatError('Array element requires an explicit codec or copy template')
        item=editing.encode_value(codec,value)
    result=array_bytes(node,action,at,item)
    plan=editing.make_plan(source,rid,property_path,result.hex(),'hex',pack,game,True)
    plan['operations'][0]['array_edit']={'path':[f'0x{k:X}' for k in keys[2:]],'action':action,'at':at,
        'codec':codec,'value':value,'copy_from':template,'item_hex':item.hex() if item is not None else None,
        'before_count':count,'after_count':len(Node(result,0,len(result)).array())}
    if template:plan['required_inputs']={template['file']:template['sha256']}
    plan['note']='Structural array edit; copied elements preserve all bytes and IDs. Retarget copied resource links separately when needed.'
    return plan


def validate_array_operation(op):
    spec=op['array_edit'];before=bytes.fromhex(op['expected_hex']);root=Node(before,0,len(before))
    keys=[int(k,16) for k in spec['path']]
    node=find_node(root,keys);items=node.array()
    if len(items)!=spec['before_count']:raise FormatError('Array before-count differs')
    if spec['action']=='remove':item=None
    elif spec['copy_from']:item=copied_element(spec['copy_from'])
    else:item=editing.encode_value(spec['codec'],spec['value'])
    if (item.hex() if item is not None else None)!=spec['item_hex']:raise FormatError('Array element evidence differs')
    changed=array_bytes(node,spec['action'],spec['at'],item)
    if len(Node(changed,0,len(changed)).array())!=spec['after_count']:raise FormatError('Array result-count differs')
    actual=replace(root,keys,changed)
    if actual.hex()!=op['replacement_hex']:raise FormatError('Array operation changes unrelated payload bytes')


def inspect(path,rid,property_path,pack=None,limit=20,offset=0):
    from .catalog import check_page,page
    check_page(limit,offset);keys=path_keys(property_path);source,pack,resource=source_resource(path,rid,pack)
    if keys[0] not in resource.fields:raise FormatError('Resource class is absent')
    node=find_node(resource.fields[keys[0]],keys[1:]);items=[];kind=None
    try:
        typed,fields,kind=children(node)
        for key,child in fields.items():items.append({'key':f'0x{key:X}','path':property_path+f'/0x{key:X}',**describe(child.raw())})
    except FormatError:pass
    return {'source':str(source),'pack':pack,'resource':resource.identity(),'path':property_path,
            'value':describe(node.raw()),'object_type':f'0x{kind:08X}' if kind else None,
            **page(items[offset:offset+limit],len(items),limit,offset),'coverage':'Recognized nested map/object layout only; values and property semantics may be opaque.'}
