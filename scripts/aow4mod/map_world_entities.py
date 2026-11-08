"""Known FZones and explicit tactical-cell entity holders; no opaque guessing."""
from collections import Counter,defaultdict
import hashlib
import json
from pathlib import Path

from . import catalog,editing,hex_positions,map_entities,maps
from .core import FormatError,sha

CELL_SCOPE=0x471519
CELL_HOLDERS=(1,3,4)
MAX_CELLS=1000000


def records(data):
    layout=hex_positions.grids(data)
    if sum(g['cell_count'] for g in layout.values())>MAX_CELLS:raise FormatError('World cell inspection exceeds bounds')
    result=[]
    for item in map_entities.records(data):
        result.append({**item,'location':{'origin':'zone','zone_ordinal':item['ordinal']}})
    for level,grid in sorted(layout.items(),key=lambda x:x[1]['ordinal']):
        for index,cell in grid['cells'].items():
            scopes=cell.mapping()
            if CELL_SCOPE not in scopes:raise FormatError('Unsupported tactical-cell entity-holder class')
            fields=scopes[CELL_SCOPE].mapping()
            for slot in CELL_HOLDERS:
                holder=fields.get(slot)
                if holder is None:continue
                values=holder.mapping()
                if not values:continue
                if set(values)!={1}:raise FormatError('Unknown nonempty tactical-cell entity-holder layout')
                node=values[1];cid,entity=node.typed()
                if map_entities.ENTITY_SCOPE not in entity or 1 not in entity[map_entities.ENTITY_SCOPE].mapping():
                    raise FormatError('Cell entity lacks its identity scope')
                eid=entity[map_entities.ENTITY_SCOPE].mapping()[1].uint(4)
                location={'origin':'cell','grid_ordinal':grid['ordinal'],'cell_index':index,'slot':slot,
                          'position':{'x':index%grid['columns'],'y':index//grid['columns'],'level':level}}
                result.append({'ordinal':len(result),'id':eid,'cid':cid,'node':node,'fields':entity,'location':location,'cell_node':cell})
                if len(result)>map_entities.MAX_ENTITIES:raise FormatError('World entity inspection exceeds bounds')
    return result


def issues(items):
    identities=defaultdict(list);mismatches=[]
    for r in items:
        identities[r['id']].append(r['location'])
        if r['location']['origin']=='cell':
            fields=r['fields'].get(map_entities.HEX_SCOPE)
            point=hex_positions.decode(fields.mapping()[8].uint(4)) if fields is not None and 8 in fields.mapping() else None
            if point!=r['location']['position']:
                mismatches.append({'id':f'{r["id"]:08X}','location':r['location'],'stored_position':point})
    return {'duplicate_ids':[{'id':f'{eid:08X}','locations':values} for eid,values in sorted(identities.items()) if len(values)>1],
            'cell_position_mismatches':mismatches}


def require_consistent(items):
    problems=issues(items)
    if problems['duplicate_ids'] or problems['cell_position_mismatches']:
        raise FormatError('Known world entities have duplicate IDs or mismatched cell positions')


def identity_summary(items):
    digest=hashlib.sha256()
    for r in sorted(items,key=lambda r:r['id']):
        digest.update(json.dumps([r['id'],r['cid'],r['location']],sort_keys=True,separators=(',',':')).encode('utf-8'))
        digest.update(b'\n')
    return {'count':len(items),'sha256':digest.hexdigest(),'scope':'known FZones and explicit cell holders 1/3/4'}


def inspect(path,limit=20,offset=0,cid=None,eid=None,origin=None):
    catalog.check_page(limit,offset);path=Path(path).resolve();data,_=maps.read(path);items=records(data)
    selected=[r for r in items if (cid is None or r['cid']==cid) and (eid is None or r['id']==eid) and
              (origin is None or r['location']['origin']==origin)]
    output=[]
    for item in selected[offset:offset+limit]:
        fields=item['fields'].get(map_entities.HEX_SCOPE);values=fields.mapping() if fields is not None else {}
        output.append({'id':f'{item["id"]:08X}','type_id':f'{item["cid"]:08X}','ordinal':item['ordinal'],'location':item['location'],
                       'bytes':len(item['node'].raw()),'sha256':sha(item['node'].raw()),
                       'stored_position':hex_positions.decode(values[8].uint(4)) if 8 in values else None,
                       'stored_direction':values[1].uint(1) if 1 in values else None})
    return {'format':'aow4mod-map-world-entities/1','source':str(path),'sha256':sha(data),'entity_count':len(items),
            'origin_counts':dict(Counter(r['location']['origin'] for r in items)),
            'type_counts':dict(sorted(Counter(f'{r["cid"]:08X}' for r in items).items())),**issues(items),
            **catalog.page(output,len(selected),limit,offset),'playability_verified':False,
            'coverage':'Known explicit FZones and tactical-cell slots 1/3/4 only. Other world managers, opaque/legacy layouts, terrain-painted visuals and gameplay are not certified.'}


def move_cell(data,eid,point):
    items=records(data);require_consistent(items);selected=[r for r in items if r['id']==eid]
    if len(selected)!=1 or selected[0]['location']['origin']!='cell':raise FormatError('Select one existing cell-held entity')
    item=selected[0];where=item['location'];layout=hex_positions.grids(data)
    point={**point,'level':where['position']['level'] if point.get('level') is None else point['level']}
    target_grid=hex_positions.checked(point,layout)
    if point==where['position']:raise FormatError('Cell entity move makes no change')
    target_index=point['y']*target_grid['columns']+point['x'];target_cell=target_grid['cells'][target_index]
    holder=target_cell.mapping()[CELL_SCOPE].mapping().get(where['slot'])
    if holder is None or holder.mapping():raise FormatError('Target cell holder must be explicitly empty')
    fields=item['fields'][map_entities.HEX_SCOPE]
    import struct
    replacement=editing.rewrite_typed(item['node'],{map_entities.HEX_SCOPE:editing.rewrite_map(fields,{8:struct.pack('<I',hex_positions.encode(point))})})
    destination=editing.rewrite_map(holder,{1:replacement})
    parsed=maps.decode(data);world=parsed['map'][0x14];_,wf=world.typed();base=wf[0x401107c];grids=base.mapping()[3];new_grids={}
    for _,grid in layout.items():
        changes={}
        if grid['ordinal']==where['grid_ordinal']:
            cell=grid['cells'][where['cell_index']];scope=cell.mapping()[CELL_SCOPE]
            changes[where['cell_index']+100]=editing.rewrite_map(cell,{CELL_SCOPE:editing.rewrite_map(scope,{where['slot']:b'\0'})})
        if grid['ordinal']==target_grid['ordinal']:
            # Different cells are required, including across levels.
            scope=target_cell.mapping()[CELL_SCOPE]
            changes[target_index+100]=editing.rewrite_map(target_cell,{CELL_SCOPE:editing.rewrite_map(scope,{where['slot']:destination})})
        if changes:
            node=grids.array()[grid['ordinal']];_,gf=node.typed()
            new_grids[grid['ordinal']]=editing.rewrite_typed(node,{0x401107d:editing.rewrite_map(gf[0x401107d],changes)})
    rebuilt=editing.rewrite_map(grids,{1:editing.rewrite_map(grids.mapping()[1],new_grids)})
    world=editing.rewrite_typed(world,{0x401107c:editing.rewrite_map(base,{3:rebuilt})})
    desired=parsed['envelope']+parsed['header_bytes']+editing.rewrite_map(parsed['body'],{0x14:world})
    after=records(desired);require_consistent(after)
    if len(after)!=len(items) or {r['id'] for r in after}!={r['id'] for r in items}:raise FormatError('Cell move changed entity coverage')
    before_by={r['id']:r['node'].raw() for r in items}
    if any(r['id']!=eid and r['node'].raw()!=before_by[r['id']] for r in after):raise FormatError('Cell move changed another entity')
    return desired,item,point
