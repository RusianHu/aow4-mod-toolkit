"""Bounded inspection of the observed tactical-world object-manager list."""
from collections import Counter
from pathlib import Path

from . import maps,catalog,editing,hex_positions
from .core import FormatError,sha

WORLD_CID=0x471516
ENTITY_SCOPE=0x4000003
HEX_SCOPE=0x401109e
MAX_ENTITIES=100000


def records(data):
    decoded=maps.decode(data)
    cid,world=decoded['map'][0x14].typed()
    if cid!=WORLD_CID or WORLD_CID not in world:
        raise FormatError('Unsupported tactical-world object manager')
    manager=world[WORLD_CID].mapping().get(2)
    if manager is None:raise FormatError('Tactical-world object manager is absent')
    objects=manager.mapping().get(0xa)
    if objects is None:raise FormatError('Unsupported tactical object-manager list')
    items=objects.array()
    if len(items)>MAX_ENTITIES:raise FormatError('Tactical entity count exceeds bounds')
    result=[];seen=set()
    for ordinal,node in items.items():
        cid,fields=node.typed()
        if ENTITY_SCOPE not in fields:raise FormatError('Map object lacks TGCEntity identity scope')
        identity=fields[ENTITY_SCOPE].mapping().get(1)
        if identity is None:raise FormatError('Map object lacks its entity ID')
        eid=identity.uint(4)
        if eid in seen:raise FormatError('Duplicate tactical entity IDs')
        seen.add(eid)
        result.append({'ordinal':ordinal,'cid':cid,'id':eid,'node':node,'fields':fields})
    return result


def replace(data,ordinal,replacement):
    parsed=maps.decode(data);world=parsed['map'][0x14];_,fields=world.typed();scope=fields[WORLD_CID]
    manager=scope.mapping()[2];objects=manager.mapping()[0xa];inner=objects.mapping()[1]
    if ordinal not in objects.array():raise FormatError('Map entity ordinal is absent')
    changed=editing.rewrite_map(objects,{1:editing.rewrite_map(inner,{ordinal:replacement})})
    changed=editing.rewrite_map(manager,{0xa:changed});changed=editing.rewrite_map(scope,{2:changed})
    changed=editing.rewrite_typed(world,{WORLD_CID:changed});body=editing.rewrite_map(parsed['body'],{0x14:changed})
    result=parsed['envelope']+parsed['header_bytes']+body;maps.decode(result)
    return result


def inspect(path,limit=20,offset=0,cid=None,eid=None,details=False):
    catalog.check_page(limit,offset)
    path=Path(path).resolve();data,_=maps.read(path);entities=records(data)
    selected=[r for r in entities if (cid is None or r['cid']==cid) and (eid is None or r['id']==eid)]
    output=[]
    for item in selected[offset:offset+limit]:
        fields=item['fields'];hex_fields=fields[HEX_SCOPE].mapping() if HEX_SCOPE in fields else {}
        direction=hex_fields[1].uint(1) if 1 in hex_fields else None
        row={'ordinal':item['ordinal'],'id':f'{item["id"]:08X}','type_id':f'{item["cid"]:08X}',
             'offset':item['node'].start,'bytes':len(item['node'].raw()),'sha256':sha(item['node'].raw()),
             'stored_direction':direction,'direction_is_known':direction in range(6) if direction is not None else None,
             'position_storage_hex':hex_fields[8].raw().hex() if 8 in hex_fields else None,
             'stored_hex_position':hex_positions.decode(hex_fields[8].uint(4)) if 8 in hex_fields else None,
             'position_codec':hex_positions.CODEC,'position_decoding_verified':8 in hex_fields}
        if details:
            row['scopes']=[{'id':f'{k:08X}','bytes':len(v.raw()),'sha256':sha(v.raw())} for k,v in fields.items()]
        output.append(row)
    return {'format':'aow4mod-map-entities/1','source':str(path),'sha256':sha(data),'entity_count':len(entities),
            'type_counts':dict(sorted(Counter(f'{r["cid"]:08X}' for r in entities).items())),
            **catalog.page(output,len(selected),limit,offset),'playability_verified':False,
            'coverage':'Observed TTacticalHexWorld object-manager entries, IDs, stored Direction and reviewed packed hex-position decoding. Omitted values remain unknown; live positions, terrain-painted objects and gameplay require separate checks.'}
