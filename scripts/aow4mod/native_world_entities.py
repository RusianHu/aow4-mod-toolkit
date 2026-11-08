"""Independent native observations for known zone and cell-held entities."""
from pathlib import Path
import shutil

from . import map_world_entities,map_entities,maps,native,native_context,native_map_entities,native_validation
from .core import FormatError,Node,sha
from .filesystem import file_hash,new_directory,save_record
from .native_overlays import locked_inputs

SCOPE='zones_and_cells_v1'


def validate_observations(source,rows,root):
    result=native_map_entities.validate_observations(source,rows,root,with_position=True)
    for expected,row in zip(source,result):
        if row.get('location')!=expected['location']:raise FormatError('Native world entity location differs')
        if expected['location']['origin']!='cell':
            if any(k in row for k in ('cell_file','cell_sha256','cell_position','source_cell_data_equal')):
                raise FormatError('Unexpected cell witness on a zone observation')
            continue
        where=expected['location'];name=f'cell-{where["grid_ordinal"]:02d}-{where["cell_index"]:06d}.bin'
        if row.get('cell_file')!=name or row.get('cell_position')!=where['position']:
            raise FormatError('Native cell witness path/position differs')
        path=Path(root)/name
        if not 1<=path.stat().st_size<=16*1024*1024 or file_hash(path)!=row.get('cell_sha256'):
            raise FormatError('Native cell witness size/hash differs')
        body=path.read_bytes();stored=Node(body,0,len(body)).mapping()[map_world_entities.CELL_SCOPE].mapping()[where['slot']].mapping()[1]
        cid,_=stored.typed()
        if cid!=expected['cid'] or stored.raw()[4:]!=(Path(root)/row['file']).read_bytes():
            raise FormatError('Native cell holder and entity witness differ')
        equal=body==expected['cell_node'].raw()
        if row.get('source_cell_data_equal')!=equal:raise FormatError('Native cell preservation summary differs')
    return result


def run(path,game,output,ids=None,cache_dir=Path('.aow4mod/native'),timeout=120,libraries=(),packs=()):
    path,game=Path(path).resolve(),Path(game).resolve();data,_=maps.read(path);all_entities=map_world_entities.records(data)
    map_world_entities.require_consistent(all_entities)
    selected=all_entities if ids is None else [r for r in all_entities if r['id'] in ids]
    if ids is not None and (len(ids)!=len(set(ids)) or set(ids)!={r['id'] for r in selected}):raise FormatError('World entity selection is missing or duplicated')
    if not (1<=len(selected)<=native_map_entities.MAX_SELECTED or not all_entities and not selected):
        raise FormatError('Select 1..256 known world entities; an empty world is identity-only')
    lc=native.library_context(libraries);pc=native_context.bind(packs);inputs=[Path(r['file']) for r in lc+pc]
    cache=native.check_cache_directory(cache_dir,game,[path.parent,Path(output).resolve(),*[p.parent for p in inputs]])
    language=game/'Language/EN/EN.MO'
    params={'path':str(path),'sha256':sha(data),'language':str(language),'language_sha256':file_hash(language),
            'entity_ids':[f'{r["id"]:08X}' for r in selected],'entity_scope':SCOPE,'world_entity_count':len(all_entities),
            'world_identity_sha256':map_world_entities.identity_summary(all_entities)['sha256'],
            'libraries':lc,'packs':pc}
    with new_directory(output,[path.parent,game,*[p.parent for p in inputs]]) as stage,locked_inputs([path,language,game/native.DLL,*inputs]):
        result=native.run_worker(game,'map-entities',params,cache,timeout);payload=result['payload'];root=Path(payload['entity_directory'])
        observed=validate_observations(selected,payload['entities'],root);names=set()
        for r in observed:
            names.add(r['file'])
            if r.get('cell_file'):names.add(r['cell_file'])
        for name in names:shutil.copyfile(root/name,stage/name)
        if sum((stage/n).stat().st_size for n in names)>64*1024*1024:raise FormatError('World entity witnesses exceed bounds')
        validate_observations(selected,observed,stage)
        if file_hash(path)!=sha(data) or any(file_hash(r['file'])!=r['sha256'] for r in lc+pc):raise FormatError('World entity inputs changed')
        diagnostics=native_validation.diagnostics(result['run'])
        report={'format':'aow4mod-native-world-entities/1','source':str(path),'sha256':sha(data),'binding':native.public_binding(result['binding']),
                'context_libraries':lc,'context_packs':pc,'entity_count':len(all_entities),'entities':observed,
                'identity_summary':payload['identity_summary'],'selected_entity_count':len(observed),
                'run':result['run'],'output':str(Path(output).resolve()),'diagnostics':diagnostics,
                'status':'not_evaluated' if not all_entities else 'review_required' if diagnostics or any(not r['source_data_equal'] or r['direction_status'] or
                    r.get('source_cell_data_equal') is False for r in observed) else 'data_preserved','objects_evaluated':bool(observed),'playability_verified':False,
                'coverage':'Known FZones and explicit tactical-cell slots 1/3/4, complete native occupancy/identity checks, selected object/cell bodies and actual positions/directions. Other managers, world registration, terrain/rendering and gameplay remain unverified.'}
        save_record(stage/'world-entities.json',report)
    return report


def worker(api,content,params,run_dir):
    import ctypes as c
    import struct
    from . import hex_positions
    profile=api.profile['tactical_maps'];data,_=maps.read(params['path']);source=map_world_entities.records(data)
    map_world_entities.require_consistent(source);by_id={r['id']:r for r in source};wanted=[int(x,16) for x in params['entity_ids']]
    if (len(source)!=params['world_entity_count'] or not (1<=len(wanted)<=256 or not source and not wanted) or
            len(set(wanted))!=len(wanted) or set(wanted)-by_id.keys()):
        raise FormatError('Native world entity selection differs')
    def cid(pointer):
        method=api.qword(api.qword(pointer)+api.profile['class_getter_vtable_offset']);code=api.memory(method,6)
        if code[:1]!=b'\xb8' or code[5:]!=b'\xc3':raise FormatError('Unexpected native world class getter')
        return int.from_bytes(code[1:5],'little')
    mp=api.qword(content+profile['content_map_offset']);world=api.qword(mp+profile['world_offset'])
    if cid(mp)!=maps.MAP_CID or cid(world)!=map_entities.WORLD_CID:raise FormatError('Unexpected native world owner')
    manager=world+profile['entity_manager_offset'];zones=[r for r in source if r['location']['origin']=='zone']
    if int.from_bytes(api.memory(manager+profile['entity_count_offset'],4),'little')!=len(zones):raise FormatError('Native FZones count differs')
    pointers={};array=api.qword(manager+profile['entity_array_offset'])
    for i,row in enumerate(zones):pointers[row['id']]=(api.qword(array+i*8),None)
    grids=hex_positions.grids(data);array=api.qword(world+profile['grid_array_offset']);count=int.from_bytes(api.memory(world+profile['grid_count_offset'],4),'little')
    if count!=len(grids):raise FormatError('Native world grid count differs')
    cells={(r['location']['grid_ordinal'],r['location']['cell_index'],r['location']['slot']):r for r in source if r['location']['origin']=='cell'}
    for grid in grids.values():
        pointer=api.qword(array+grid['ordinal']*8)
        rows,cols=struct.unpack('<II',api.memory(pointer+profile['grid_rows_offset'],8));level=int.from_bytes(api.memory(pointer+profile['grid_level_offset'],4),'little')
        if cid(pointer)!=0x471515 or (rows,cols,level)!=(grid['rows'],grid['columns'],grid['level']):raise FormatError('Native grid layout differs')
        base=api.qword(pointer+profile['grid_cells_offset']);stride=int.from_bytes(api.memory(pointer+profile['grid_stride_offset'],4),'little')
        if stride!=profile['tactical_cell_stride']:raise FormatError('Native tactical-cell stride differs')
        for index in range(grid['cell_count']):
            cell=base+index*stride
            if cid(cell)!=map_world_entities.CELL_SCOPE:raise FormatError('Unexpected native tactical-cell class')
            point=dict(zip(('x','y','level'),struct.unpack('<iii',api.memory(cell+profile['cell_position_offset'],12))))
            if point!={'x':index%cols,'y':index//cols,'level':level}:raise FormatError('Native tactical-cell coordinates differ')
            for slot,offset in profile['cell_entity_offsets'].items():
                slot=int(slot);entity=api.qword(cell+offset);row=cells.get((grid['ordinal'],index,slot))
                if bool(entity)!=(row is not None):raise FormatError('Native cell entity occupancy differs')
                if row is not None:pointers[row['id']]=(entity,cell)
    if set(pointers)!=set(by_id):raise FormatError('Native world entity coverage differs')
    directory=Path(run_dir)/'native-entities';directory.mkdir();result={};total=0;cell_bodies={}
    getter=c.CFUNCTYPE(c.c_int,c.c_void_p,c.c_char_p,c.c_char_p,c.c_void_p,c.c_int)(api.dll._handle+profile['get_ordinal_property_rva'])
    pack=c.CFUNCTYPE(c.c_uint32,c.c_void_p)(api.dll._handle+profile['pack_position_rva'])
    identities=[]
    for eid,(pointer,cell) in pointers.items():
        row=by_id[eid]
        live_id=int.from_bytes(api.memory(pointer+profile['entity_id_offset'],4),'little');live_cid=cid(pointer)
        if live_id!=eid or live_cid!=row['cid']:
            raise FormatError('Native world entity identity differs')
        identities.append({'id':live_id,'cid':live_cid,'location':row['location']})
        if eid not in wanted:continue
        body=api.serialize_object(pointer);total+=len(body);name=f'entity-{row["ordinal"]:06d}.bin';(directory/name).write_bytes(body)
        variant=c.create_string_buffer(24);c.c_uint32.from_buffer(variant,8).value=0x80004005;status=getter(pointer,b'',b'Direction',variant,-1)
        point=None;packed=None
        if map_entities.HEX_SCOPE in row['fields']:
            raw=api.memory(pointer+profile['entity_position_offset'],12);point=dict(zip(('x','y','level'),struct.unpack('<iii',raw)));packed=pack(c.create_string_buffer(raw))
        record={'id':f'{eid:08X}','cid':f'{row["cid"]:08X}','ordinal':row['ordinal'],'location':row['location'],
                'file':name,'sha256':sha(body),'direction_status':status,'direction':int.from_bytes(variant.raw[:4],'little',signed=True) if status==0 else None,
                'position':point,'native_packed_position':packed}
        if cell is not None:
            where=row['location'];name=f'cell-{where["grid_ordinal"]:02d}-{where["cell_index"]:06d}.bin'
            if name not in cell_bodies:
                cb=api.serialize_object(cell);cell_bodies[name]=cb;total+=len(cb);(directory/name).write_bytes(cb)
            cb=cell_bodies[name]
            record.update(cell_file=name,cell_sha256=sha(cb),source_cell_data_equal=cb==row['cell_node'].raw(),
                cell_position=dict(zip(('x','y','level'),struct.unpack('<iii',api.memory(cell+profile['cell_position_offset'],12)))))
        if total>64*1024*1024:raise FormatError('Native world entity witnesses exceed bounds')
        result[eid]=record
    summary=map_world_entities.identity_summary(identities)
    if summary['sha256']!=params['world_identity_sha256']:raise FormatError('Native complete known-identity projection differs')
    return {'entity_scope':SCOPE,'world_entity_count':len(source),'identity_summary':summary,
            'entity_directory':str(directory),'entities':[result[x] for x in wanted]}
