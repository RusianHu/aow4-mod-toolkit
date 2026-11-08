"""ACP per-pack additive Mod imports, independent of resource update bodies."""
import json
import struct
from pathlib import Path

from .core import FormatError, Node, mod_file, read_acp, sha, string_list
from . import editing
from .filesystem import copy_directory, file_hash, inventory

FORMAT = 'aow4mod-overlay-import-plan/1'


def identifier(value):
    from .pack_imports import identifier as validate
    return validate(value)


def records(data):
    fields = Node(data, 19, len(data)).typed()[1][0x40009d].mapping()
    if 0x25 in fields and fields[0x25].uint(4)!=(len(fields[0x26].raw()) if 0x26 in fields else 0):
        raise FormatError('ACP change-blob length does not match its serialized data')
    result = []
    for node in fields[0x26].array().values() if 0x26 in fields else ():
        kind, outer = node.typed()
        if kind != 0x401204 or set(outer) != {kind}:
            raise FormatError('Unsupported ACP Mod-data import record')
        values = outer[kind].mapping()
        result.append({'pack': values[1].string(),
                       'required': string_list(values[3]) if 3 in values else [],
                       'optional': string_list(values[4]) if 4 in values else []})
    return result


def replace_blob(data,root,array):
    fields=root.typed()[1][0x40009d].mapping();updates={0x26:array}
    if 0x25 in fields:updates[0x25]=struct.pack('<I',len(array))
    return data[:19]+editing.rewrite_wrapper(root,0x40009d,updates)


def selected(data, pack):
    matches = [row for row in records(data) if row['pack'].casefold() == pack.casefold()]
    if len(matches) != 1:
        raise FormatError('Expected exactly one ACP Mod-data record for the selected pack')
    return matches[0]


def rewrite(data, pack, imports, optional=False):
    from .authoring import string_vector
    imports = [identifier(value) for value in imports]
    if type(optional) is not bool:
        raise FormatError('Optional import selector must be boolean')
    selected(data, pack)
    root = Node(data, 19, len(data)); fields = root.typed()[1][0x40009d].mapping()
    def change(node, index):
        values = node.typed()[1][0x401204].mapping()
        if values[1].string().casefold() != pack.casefold():
            return node.raw()
        return editing.rewrite_wrapper(node, 0x401204,
            {4 if optional else 3: string_vector(imports) if imports else None})
    array = editing.rewrite_array(fields[0x26], change)
    updated = replace_blob(data,root,array)
    expected = selected(data, pack); expected['optional' if optional else 'required'] = imports
    if selected(updated, pack) != expected:
        raise FormatError('ACP Mod dependency edit does not match requested lists')
    # Reversing only this metadata edit must recover every other serialized byte,
    # including unknown fields and all resource update payloads.
    def strip(node):
        values = node.typed()[1][0x401204].mapping()
        if values[1].string().casefold() == pack.casefold():
            return editing.rewrite_wrapper(node, 0x401204, {4 if optional else 3: None})
        return node.raw()
    def without(raw):
        node=Node(raw,19,len(raw)); scope=node.typed()[1][0x40009d].mapping()
        return replace_blob(raw,node,editing.rewrite_array(scope[0x26],lambda n,i:strip(n)))
    if without(data) != without(updated):
        raise FormatError('ACP dependency edit changed unrelated data')
    return updated


def rpk_declarations(data):
    fields=Node(data,176,len(data)).mapping()
    return [{'name':name,'optional':optional,'mod_added':mod_added}
            for field,optional,mod_added in ((0x1e,False,False),(0x1f,True,False),
                                            (0x25,False,True),(0x26,True,True))
            for name in (string_list(fields[field]) if field in fields else [])]


def update(data, pack, name):
    identifier(name)
    if pack.casefold() == name.casefold():
        return None
    before=selected(data,pack)['required']
    if name.casefold() in {value.casefold() for value in before}:
        return None
    return {'kind':'acp_mod_dependencies','pack':pack,'optional':False,
            'before':before,'after':before+[name],'added':name}


def make_plan(path, pack, add=(), remove=(), optional=False, source_root=None, game=None):
    path=mod_file(Path(path)); root=Path(source_root).resolve() if source_root else path.parent
    if not path.is_relative_to(root):
        raise FormatError('ACP imports source is outside its Mod root')
    read_acp(path); data=path.read_bytes()
    before=selected(data,pack)['optional' if optional else 'required']
    additions=[identifier(value) for value in add]
    removals=[identifier(value) for value in remove]
    added={value.casefold() for value in additions}; removed={value.casefold() for value in removals}
    if added & removed or pack.casefold() in added:
        raise FormatError('Cannot add/remove the same import or import the selected pack itself')
    if removed-{value.casefold() for value in before}:
        raise FormatError('Import requested for removal is absent from the selected Mod list')
    after=[value for value in before if value.casefold() not in removed]
    for value in additions:
        if value.casefold() not in {item.casefold() for item in after}: after.append(value)
    if before==after: raise FormatError('ACP import plan makes no change')
    updated=rewrite(data,pack,after,optional)
    return {'format':FORMAT,'source':str(path),'source_sha256':sha(data),'source_root':str(root),
            'source_files':inventory(root),'pack':pack,'optional':optional,'before':before,'after':after,
            'add':additions,'remove':removals,'game':str(Path(game).resolve()) if game else None,
            'result_sha256':sha(updated),
            'coverage':'ACP additive Mod dependency declarations only. Removing a declaration does not remove original RPK imports. Effective loading and gameplay need separate validation.'}


def apply_plan(plan, output):
    if not isinstance(plan,dict) or plan.get('format')!=FORMAT:
        raise FormatError('Unsupported ACP import plan')
    path=Path(plan['source']); root=Path(plan['source_root'])
    if inventory(root)!=plan['source_files'] or file_hash(path)!=plan['source_sha256']:
        raise FormatError('ACP import source changed since planning')
    expected=make_plan(path,plan['pack'],plan['add'],plan['remove'],plan['optional'],root,plan.get('game'))
    if expected!=plan: raise FormatError('ACP import plan differs from its source and requested edits')
    data=rewrite(path.read_bytes(),plan['pack'],plan['after'],plan['optional'])
    with copy_directory(root,output,[Path(plan['game'])] if plan.get('game') else []) as (stage,files):
        (stage/path.relative_to(root)).write_bytes(data)
        receipt={'format':'aow4mod-overlay-import-receipt/1','output':str(Path(output).resolve()),
                 'file':path.relative_to(root).as_posix(),'pack':plan['pack'],'optional':plan['optional'],
                 'before':plan['before'],'after':plan['after'],'result_sha256':sha(data)}
        (stage/'.aow4mod-overlay-imports.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return receipt
