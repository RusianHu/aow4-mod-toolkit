"""Loaded-map entity witnesses; no standalone entity construction or mutation."""
from pathlib import Path
import shutil

from . import maps,map_entities,native,native_validation,hex_positions,native_context
from .core import FormatError,Node,sha
from .filesystem import file_hash,new_directory,save_record
from .native_overlays import locked_inputs

MAX_SELECTED=256


def validate_observations(source,observations,root,with_position=False):
    if not isinstance(observations,list) or len(observations)!=len(source):raise FormatError('Native map entity coverage differs')
    results=[]
    for expected,item in zip(source,observations):
        if (item.get('id')!=f'{expected["id"]:08X}' or item.get('cid')!=f'{expected["cid"]:08X}' or
                item.get('ordinal')!=expected['ordinal']):raise FormatError('Native map entity identity differs')
        name=item.get('file')
        if not isinstance(name,str) or name!=f'entity-{expected["ordinal"]:06d}.bin':raise FormatError('Native map entity witness path differs')
        path=Path(root)/name
        if path.stat().st_size>16*1024*1024:raise FormatError('Native map entity witness exceeds bounds')
        body=path.read_bytes()
        if sha(body)!=item.get('sha256'):raise FormatError('Native map entity witness changed')
        fields=Node(body,0,len(body)).mapping()
        if fields[map_entities.ENTITY_SCOPE].mapping()[1].uint(4)!=expected['id']:raise FormatError('Native map entity witness ID differs')
        status=item.get('direction_status');value=item.get('direction')
        if type(status) is not int or (status==0 and (type(value) is not int or value not in range(6))) or (status!=0 and value is not None):
            raise FormatError('Invalid native map entity direction observation')
        hex_fields=fields[map_entities.HEX_SCOPE].mapping() if map_entities.HEX_SCOPE in fields else {}
        if status==0 and 1 in hex_fields and hex_fields[1].uint(1)!=value:raise FormatError('Native getter differs from serialized entity direction')
        if with_position:
            point=item.get('position');packed=item.get('native_packed_position')
            if map_entities.HEX_SCOPE in fields:
                encoded=hex_positions.encode(point)
                if type(packed) is not int or packed!=encoded or packed!=(hex_fields[8].uint(4) if 8 in hex_fields else 0xffffffff):
                    raise FormatError('Loaded hex position/native packing differs from its serialized object')
            elif point is not None or packed is not None:raise FormatError('Unexpected native position without hex-entity scope')
        results.append({**item,'source_data_equal':body==expected['node'].raw()[4:]})
    return results


def run(path,game,output,ids=None,cache_dir=Path('.aow4mod/native'),timeout=120,libraries=(),packs=()):
    path,game=Path(path).resolve(),Path(game).resolve();data,_=maps.read(path);all_entities=map_entities.records(data)
    selected=all_entities if ids is None else [r for r in all_entities if r['id'] in ids]
    if ids is not None and (len(ids)!=len(set(ids)) or set(ids)!={r['id'] for r in selected}):raise FormatError('Map entity selection is missing or duplicated')
    if not 1<=len(selected)<=MAX_SELECTED:raise FormatError('Select 1..256 existing map entities')
    library_context=native.library_context(libraries);pack_context=native_context.bind(packs);context_files=[Path(r['file']) for r in library_context+pack_context]
    cache=native.check_cache_directory(cache_dir,game,[path.parent,Path(output).resolve(),*[p.parent for p in context_files]]);language=game/'Language/EN/EN.MO'
    params={'path':str(path),'sha256':sha(data),'language':str(language),'language_sha256':file_hash(language),
            'entity_ids':[f'{r["id"]:08X}' for r in selected],'libraries':library_context,'packs':pack_context}
    with new_directory(output,[path.parent,game,*[p.parent for p in context_files]]) as stage,locked_inputs([path,language,game/native.DLL,*context_files]):
        result=native.run_worker(game,'map-entities',params,cache,timeout);payload=result['payload'];root=Path(payload['entity_directory'])
        observed=validate_observations(selected,payload['entities'],root,with_position=True)
        for item in observed:
            shutil.copyfile(root/item['file'],stage/item['file'])
            if file_hash(stage/item['file'])!=item['sha256']:raise FormatError('Native entity changed while freezing witness')
        diagnostics=native_validation.diagnostics(result['run'])
        if file_hash(path)!=sha(data):raise FormatError('Map changed during native entity inspection')
        if any(file_hash(r['file'])!=r['sha256'] for r in library_context+pack_context):raise FormatError('Map entity lookup inputs changed during native inspection')
        report={'format':'aow4mod-native-map-entities/2','source':str(path),'sha256':sha(data),'binding':native.public_binding(result['binding']),
                'context_libraries':library_context,'context_packs':pack_context,
                'run':result['run'],'output':str(Path(output).resolve()),'entity_count':len(all_entities),'entities':observed,
                'diagnostics':diagnostics,'status':'review_required' if diagnostics or any(not r['source_data_equal'] or r['direction_status'] for r in observed) else 'data_preserved',
                'playability_verified':False,'coverage':'Loaded tactical-world entities, exact native bodies, actual Direction getters and runtime hex-position integers independently repacked by the official function. No standalone reconstruction, terrain or gameplay certification.'}
        save_record(stage/'map-entities.json',report)
    return report


def worker(api,content,params,run_dir):
    import ctypes as c
    import struct
    profile=api.profile['tactical_maps'];data,_=maps.read(params['path']);source=map_entities.records(data);by_id={r['id']:r for r in source}
    requested=[int(v,16) for v in params['entity_ids']]
    if not 1<=len(requested)<=MAX_SELECTED or len(requested)!=len(set(requested)) or set(requested)-by_id.keys():raise FormatError('Unsupported native entity selection')
    map_pointer=api.qword(content+profile['content_map_offset'])
    def cid(pointer):
        method=api.qword(api.qword(pointer)+api.profile['class_getter_vtable_offset']);code=api.memory(method,6)
        if code[:1]!=b'\xb8' or code[5:]!=b'\xc3':raise FormatError('Unexpected native map/entity class getter')
        return int.from_bytes(code[1:5],'little')
    if cid(map_pointer)!=maps.MAP_CID:raise FormatError('Unexpected loaded map class')
    world=api.qword(map_pointer+profile['world_offset'])
    if cid(world)!=map_entities.WORLD_CID:raise FormatError('Unexpected loaded tactical world class')
    manager=world+profile['entity_manager_offset'];count=int.from_bytes(api.memory(manager+profile['entity_count_offset'],4),'little')
    if count!=len(source) or count>map_entities.MAX_ENTITIES:raise FormatError(f'Loaded tactical entity count differs: file={len(source)}, live={count}')
    array=api.qword(manager+profile['entity_array_offset']);seen=set();observations={};directory=Path(run_dir)/'native-entities';directory.mkdir()
    getter=c.CFUNCTYPE(c.c_int,c.c_void_p,c.c_char_p,c.c_char_p,c.c_void_p,c.c_int)(api.dll._handle+profile['get_ordinal_property_rva'])
    pack_position=c.CFUNCTYPE(c.c_uint32,c.c_void_p)(api.dll._handle+profile['pack_position_rva'])
    total_bytes=0
    for index in range(count):
        entity=api.qword(array+index*8);eid=int.from_bytes(api.memory(entity+profile['entity_id_offset'],4),'little')
        if eid in seen or eid not in by_id or cid(entity)!=by_id[eid]['cid']:raise FormatError('Loaded tactical entity identity differs')
        seen.add(eid)
        if eid not in requested:continue
        body=api.serialize_object(entity);total_bytes+=len(body)
        if total_bytes>64*1024*1024:raise FormatError('Native map entity witnesses exceed bounds')
        variant=c.create_string_buffer(24);c.c_uint32.from_buffer(variant,8).value=0x80004005
        status=getter(entity,b'',b'Direction',variant,-1)
        point=None;packed=None
        if map_entities.HEX_SCOPE in by_id[eid]['fields']:
            raw=api.memory(entity+profile['entity_position_offset'],12)
            point=dict(zip(('x','y','level'),struct.unpack('<iii',raw)));hex_positions.encode(point)
            packed=pack_position(c.create_string_buffer(raw))
        item=by_id[eid];name=f'entity-{item["ordinal"]:06d}.bin';(directory/name).write_bytes(body)
        observations[eid]={'id':f'{eid:08X}','cid':f'{item["cid"]:08X}','ordinal':item['ordinal'],'file':name,'sha256':sha(body),
                           'direction_status':status,'direction':int.from_bytes(variant.raw[:4],'little',signed=True) if status==0 else None,
                           'position':point,'native_packed_position':packed}
    if seen!=by_id.keys():raise FormatError('Native map entity identity coverage differs')
    return {'entity_directory':str(directory),'entities':[observations[eid] for eid in requested]}
