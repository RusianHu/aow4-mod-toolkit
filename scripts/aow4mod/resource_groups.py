"""Append native resources while preserving existing RPK categories and locators."""
from pathlib import Path
import re
import struct

from . import authoring, catalog, editing, pack_imports
from .core import FormatError, Node, Resource, string_list

LOCATOR = re.compile(r'\[#([0-9A-Fa-f]{16})\](\d+)\\(\d+)')
GENERIC = 0x0400017f


def layout(data):
    if len(data) < 177 or data[:12] != b'RPK\0\x1a\0\0\x04\0\0\0\0' or struct.unpack_from('<I',data,12)[0] != len(data)-176:
        raise FormatError('Unsupported or truncated RPK layout')
    root = Node(data,176,len(data)); fields = root.mapping()
    groups = []; identities = set()
    for key, node in fields[13].mapping().items():
        kind, wrapper = node.typed(); feature = wrapper[0x33].mapping()
        rows = []; used_positions = set()
        for index, obj in feature[0x15].array().items():
            resource = Resource.parse(obj)
            if resource.rid in identities:
                raise FormatError('Duplicate resource ID in RPK')
            identities.add(resource.rid)
            locator = resource.fields[0x14].string() if 0x14 in resource.fields else ''
            match = LOCATOR.fullmatch(locator)
            if not match or int(match[1],16) != resource.rid:
                raise FormatError('Resource locator is absent or disagrees with its ID')
            category, position = map(int,match.groups()[1:])
            if max(category,position) > 0xffffffff or (category,position) in used_positions:
                raise FormatError('Duplicate or oversized resource locator position')
            used_positions.add((category,position))
            rows.append({'index':index,'resource':resource,'category':category,'position':position})
        categories = []
        seen = set()
        for index, category in feature[0x18].array().items():
            cid, values = category.typed()
            if cid not in (0x0040100e,0x00400044) or not {0xa,0xb}.issubset(values):
                raise FormatError('Unsupported RPK editor category layout')
            identity = values[0xb].uint(4); name = values[0xa].string()
            if identity in seen:
                raise FormatError('Duplicate RPK category ID within a container')
            seen.add(identity)
            categories.append({'index':index,'id':identity,'name':name,'type':cid,'node':category})
        unclassified = sorted({row['category'] for row in rows}-seen)
        groups.append({'key':key,'kind':kind,'node':node,'wrapper':wrapper,
                       'feature':feature,'resources':rows,'categories':categories,'unclassified_categories':unclassified})
    return root, fields, groups


def placement(data, category, container=None):
    if container is not None and (type(container) is not int or not 0<=container<=0xffffffff):
        raise FormatError('Container key requires a nonnegative u32 integer')
    if not isinstance(category,str) or not category or '\0' in category or len(category.encode('utf-8'))>4096:
        raise FormatError('Category requires nonempty text without NUL, up to 4096 UTF-8 bytes')
    root, fields, groups = layout(data)
    eligible = [group for group in groups if group['kind'] == GENERIC and
                (container is None or group['key'] == container)]
    named = [group for group in eligible if any(row['name'] == category for row in group['categories'])]
    selected = named or eligible
    if len(selected) != 1:
        raise FormatError('Existing RPK container is missing or ambiguous; inspect pack-layout and select --container')
    group = selected[0]
    matches = [row for row in group['categories'] if row['name'] == category]
    if len(matches)>1:
        raise FormatError('Editor category name is ambiguous within this container')
    used = {row['id'] for row in group['categories']} | set(group['unclassified_categories'])
    category_id = matches[0]['id'] if matches else max(used,default=-1)+1
    position = max((row['position'] for row in group['resources'] if row['category']==category_id),default=-1)+1
    if max(category_id,position)>0xffffffff or len(group['resources'])>=65535:
        raise FormatError('RPK resource/category position exceeds supported bounds')
    return {'container':group['key'],'category':category,'category_id':category_id,
            'new_category':not bool(matches),'position':position,'resource_count_before':sum(len(g['resources']) for g in groups),
            'preserved_unclassified_categories':group['unclassified_categories']}


def append(data, resource_data, category, dependencies=(), container=None):
    place = placement(data,category,container)
    root, fields, groups = layout(data)
    resource = Resource.parse(Node(resource_data,0,len(resource_data)))
    if resource.rid in {r['resource'].rid for group in groups for r in group['resources']}:
        raise FormatError('Appended resource ID already exists')
    expected = f'[#{resource.rid:016X}]{place["category_id"]}\\{place["position"]}'
    if 0x14 not in resource.fields or resource.fields[0x14].string() != expected:
        raise FormatError('Appended resource locator differs from its allocated category position')
    group = next(group for group in groups if group['key']==place['container'])
    feature = group['feature']
    objects = [row['resource'].node.raw() for row in group['resources']]+[resource_data]
    updates = {0x15:editing.replace_array_items(feature[0x15],objects)}
    if place['new_category']:
        entry = struct.pack('<I',0x0040100e)+editing.rewrite_map(Node(b'\0',0,1),{
            0xa:authoring.string(category),0xb:struct.pack('<I',place['category_id'])})
        updates[0x18] = editing.replace_array_items(feature[0x18],
            [row['node'].raw() for row in group['categories']]+[entry])
    changed = editing.rewrite_typed(group['node'],{0x33:editing.rewrite_map(group['wrapper'][0x33],updates)})
    root_updates = {13:editing.rewrite_map(fields[13],{place['container']:changed})}
    imports = string_list(fields[0x1e]) if 0x1e in fields else []
    add = []
    for dependency in dependencies:
        pack_imports.identifier(dependency)
        if dependency.casefold()==fields[0x16].string().casefold():
            raise FormatError('A resource pack cannot import itself')
        if dependency.casefold() not in {value.casefold() for value in imports}:
            imports.append(dependency);add.append(dependency)
    if add:root_updates[0x1e]=authoring.string_vector(imports)
    payload = editing.rewrite_map(root,root_updates)
    result = data[:12]+struct.pack('<I',len(payload))+data[16:176]+payload
    # Reparse the complete changed pack; all old nodes and unknown metadata were
    # copied as bytes, while only array maps and the derived length were rebuilt.
    _,_,after = layout(result)
    old_rows = [row['resource'].node.raw() for item in groups for row in item['resources']]
    after_rows = [row['resource'].node.raw() for item in after for row in item['resources'] if row['resource'].rid!=resource.rid]
    if old_rows != after_rows:
        raise FormatError('Appending changed preexisting resources or their order')
    return result,{**place,'added_imports':add,'preexisting_resource_bytes_preserved':True}


def inspect(path, limit=20, offset=0):
    catalog.check_page(limit,offset)
    path=Path(path).resolve();_,fields,groups=layout(path.read_bytes());rows=[]
    for group in groups:
        categories = group['categories']+[{'name':None,'id':key,'type':None} for key in group['unclassified_categories']]
        for category in categories:
            resources=[row for row in group['resources'] if row['category']==category['id']]
            rows.append({'container':group['key'],'container_type':f'{group["kind"]:08X}',
                         'category':category['name'],'category_id':category['id'],'resources':len(resources),
                         'category_type':f'{category["type"]:08X}' if category['type'] is not None else None,
                         'category_record_present':category['type'] is not None,
                         'next_resource_position':max((row['position'] for row in resources),default=-1)+1})
    return {'file':str(path),'pack':fields[0x16].string(),'containers':len(groups),
            'resources':sum(len(group['resources']) for group in groups),
            **catalog.page(rows[offset:offset+limit],len(rows),limit,offset),
            'coverage':'Editor categories and locator positions. Appending supports the generic resource container only.'}
