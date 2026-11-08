"""Native CPU terrain-weight buffers and pixel-object evidence; no renderer use."""
from pathlib import Path
import shutil

from . import map_terrain,maps,native,native_context,native_validation
from .core import FormatError,sha
from .filesystem import file_hash,new_directory,save_record
from .native_overlays import locked_inputs


def validate(report,path,root):
    if report.get('format')!='aow4mod-native-map-terrain/1' or report.get('sha256')!=file_hash(path):raise FormatError('Native terrain source/format differs')
    info=map_terrain.layout(Path(path).read_bytes(),report['level'])
    if report.get('resolution')!=info['resolution']:raise FormatError('Native terrain resolution differs')
    item=report['pixel_artifact']
    if item.get('file')!='pixel-body.bin':raise FormatError('Native terrain artifact path differs')
    file=Path(root)/item['file']
    if not 1<=file.stat().st_size<=maps.MAX_BYTES or file_hash(file)!=item.get('sha256'):raise FormatError('Native terrain pixel witness size/hash differs')
    equal=file.read_bytes()==info['pixels'].raw()
    if report.get('pixel_data_equal')!=equal:raise FormatError('Native terrain preservation summary differs')
    expected={str(k):sha(info['pixel_fields'][k].raw()) for k in (11,12)}
    if report.get('weight_buffers_sha256')!=expected:raise FormatError('Native terrain CPU buffer hash differs')
    points=report.get('samples')
    if not isinstance(points,list) or not 1<=len(points)<=64 or len({(r['x'],r['y']) for r in points})!=len(points):raise FormatError('Native terrain sample coverage differs')
    for row in points:
        if row.get('weights')!=map_terrain.sample(info,row['x'],row['y']):raise FormatError('Native terrain sample values differ')
    return equal


def run(path,game,output,level,samples,cache_dir=Path('.aow4mod/native'),timeout=120,libraries=(),packs=()):
    path,game=Path(path).resolve(),Path(game).resolve();data,_=maps.read(path);info=map_terrain.layout(data,level)
    if not isinstance(samples,list) or not 1<=len(samples)<=64:raise FormatError('Select 1..64 terrain weight pixels')
    for row in samples:
        if not isinstance(row,dict) or set(row)!={'x','y'}:raise FormatError('Select exact x/y pixel coordinates')
        map_terrain.sample(info,row['x'],row['y'])
    if len({(r['x'],r['y']) for r in samples})!=len(samples):raise FormatError('Duplicate terrain sample coordinates')
    lc=native.library_context(libraries);pc=native_context.bind(packs);inputs=[Path(r['file']) for r in lc+pc]
    cache=native.check_cache_directory(cache_dir,game,[path.parent,Path(output).resolve(),*[p.parent for p in inputs]])
    language=game/'Language/EN/EN.MO';params={'path':str(path),'sha256':sha(data),'language':str(language),'language_sha256':file_hash(language),
        'level':level,'samples':samples,'libraries':lc,'packs':pc}
    with new_directory(output,[path.parent,game,*[p.parent for p in inputs]]) as stage,locked_inputs([path,language,game/native.DLL,*inputs]):
        result=native.run_worker(game,'map-terrain',params,cache,timeout);payload=result['payload'];artifact=payload['pixel_artifact']
        source=Path(artifact['file']);shutil.copyfile(source,stage/'pixel-body.bin')
        report={'format':'aow4mod-native-map-terrain/1','source':str(path),'sha256':sha(data),'level':level,'binding':native.public_binding(result['binding']),
            'resolution':payload['resolution'],'samples':payload['samples'],'weight_buffers_sha256':payload['weight_buffers_sha256'],
            'pixel_artifact':{'file':'pixel-body.bin','sha256':file_hash(stage/'pixel-body.bin')},
            'pixel_data_equal':(stage/'pixel-body.bin').read_bytes()==info['pixels'].raw(),'context_libraries':lc,'context_packs':pc,
            'diagnostics':native_validation.diagnostics(result['run']),'output':str(Path(output).resolve()),'run':result['run'],
            'rendering_verified':False,'coverage':'Native pixel-object bytes, CPU weight-buffer hashes and selected serialized-channel pixels only. No brush sessions, geometry, material-name resolution, navigation or rendering certification.'}
        validate(report,path,stage)
        if file_hash(path)!=sha(data) or any(file_hash(r['file'])!=r['sha256'] for r in lc+pc):raise FormatError('Terrain inputs changed during native observation')
        save_record(stage/'terrain.json',report)
    return report


def worker(api,content,params,run_dir):
    import struct
    info=map_terrain.layout(Path(params['path']).read_bytes(),params['level']);profile=api.profile['tactical_maps']
    mp=api.qword(content+profile['content_map_offset']);world=api.qword(mp+profile['world_offset'])
    grid=api.qword(api.qword(world+profile['grid_array_offset'])+info['grid']['ordinal']*8)
    paint=grid+profile['terrain_paint_offset'];pixel=paint+profile['terrain_pixel_offset']
    def cid(pointer):
        method=api.qword(api.qword(pointer)+api.profile['class_getter_vtable_offset']);code=api.memory(method,6)
        if code[:1]!=b'\xb8' or code[5:]!=b'\xc3':raise FormatError('Unexpected native terrain class getter')
        return int.from_bytes(code[1:5],'little')
    if cid(mp)!=maps.MAP_CID or cid(world)!=0x471516 or cid(grid)!=0x471515 or cid(paint)!=map_terrain.PAINT_SCOPE or cid(pixel)!=map_terrain.PIXEL_SCOPE:
        raise FormatError('Native terrain owner/class differs')
    width,height=struct.unpack('<II',api.memory(pixel+profile['terrain_weight_dimensions_offset'],8))
    if (width,height)!=(info['resolution']['width'],info['resolution']['height']):raise FormatError('Native terrain weight dimensions differ')
    buffers=[]
    for offset in profile['terrain_weight_buffer_offsets']:
        pointer=api.qword(pixel+offset);buffers.append(api.memory(pointer,width*height*4))
    samples=[]
    for point in params['samples']:
        map_terrain.sample(info,point['x'],point['y']);at=(point['y']*width+point['x'])*4
        samples.append({**point,'weights':list(buffers[0][at:at+4]+buffers[1][at:at+4])})
    folder=Path(run_dir)/'native-terrain';folder.mkdir();path=folder/'pixel-body.bin';path.write_bytes(api.serialize_object(pixel,maximum=maps.MAX_BYTES))
    return {'level':params['level'],'resolution':info['resolution'],'samples':samples,
            'weight_buffers_sha256':{str(k):sha(v) for k,v in zip((11,12),buffers)},
            'pixel_artifact':{'file':str(path),'sha256':file_hash(path)}}
