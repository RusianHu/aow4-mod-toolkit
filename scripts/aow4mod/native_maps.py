"""Pinned tactical-map diagnostic saves with independent file reload witnesses."""
import json
from pathlib import Path
import shutil

from . import maps,native,native_validation,native_context
from .core import FormatError,sha
from .filesystem import file_hash,new_directory
from .native_overlays import locked_inputs


def run(path,game,output,cache_dir=Path('.aow4mod/native'),timeout=120,libraries=(),packs=()):
    path,game=Path(path).resolve(),Path(game).resolve();source=maps.inspect(path)
    library_context=native.library_context(libraries);pack_context=native_context.bind(packs)
    context_files=[Path(row['file']) for row in library_context+pack_context]
    cache=native.check_cache_directory(cache_dir,game,[path.parent,Path(output).resolve(),*[p.parent for p in context_files]]);language=game/'Language/EN/EN.MO'
    params={'path':str(path),'sha256':source['sha256'],'language':str(language),'language_sha256':file_hash(language),
            'libraries':library_context,'packs':pack_context}
    with new_directory(output,[path.parent,game,*[p.parent for p in context_files]]) as stage,locked_inputs([path,language,game/native.DLL,*context_files]):
        first=native.run_worker(game,'map-roundtrip',params,cache,timeout);payload=first['payload']
        candidate=Path(payload['artifacts']['saved']['file']);params2={**params,'path':str(candidate),'sha256':file_hash(candidate)}
        second=native.run_worker(game,'map-roundtrip',params2,cache,timeout)
        if native.public_binding(first['binding'])!=native.public_binding(second['binding']):raise FormatError('Map backend changed between file reads')
        for result in (first,second):
            p=result['payload'];body_before=Path(p['artifacts']['before']['file']).read_bytes();body_after=Path(p['artifacts']['after']['file']).read_bytes()
            if sha(body_before)!=p['artifacts']['before']['sha256'] or sha(body_after)!=p['artifacts']['after']['sha256']:
                raise FormatError('Map object witness changed during verification')
            _,saved=maps.read(p['artifacts']['saved']['file'])
            if body_before!=body_after or body_after!=saved['body'].raw():raise FormatError('Map native object changed during writing or differs from saved file')
        comparison=maps.compare(path,candidate);reload=maps.compare(candidate,second['payload']['artifacts']['saved']['file'])
        diagnostics=native_validation.diagnostics(first['run'])+native_validation.diagnostics(second['run'])
        files=[]
        for prefix,result in (('first',first),('reloaded',second)):
            for key,item in result['payload']['artifacts'].items():
                target=stage/(prefix+'-'+Path(item['file']).name)
                if file_hash(item['file'])!=item['sha256']:raise FormatError('Map artifact changed before staging')
                shutil.copyfile(item['file'],target)
                if file_hash(target)!=item['sha256']:raise FormatError('Map artifact changed during staging')
                files.append({'file':target.name,'sha256':item['sha256'],'bytes':target.stat().st_size})
        if file_hash(path)!=source['sha256']:raise FormatError('Original map changed during diagnostic save')
        if any(file_hash(row['file'])!=row['sha256'] for row in library_context+pack_context):raise FormatError('Map lookup context changed during diagnostic save')
        report={'format':'aow4mod-native-map-roundtrip/1','source':source,'binding':native.public_binding(first['binding']),
                'context_libraries':library_context,'context_packs':pack_context,
                'output':str(Path(output).resolve()),'runs':[first['run'],second['run']],'files':files,'comparison':comparison,
                'independent_reload_comparison':reload,'native_object_save_data_equal':True,'diagnostics':diagnostics,
                'status':'data_preserved' if comparison['all_compared_data_equal'] and reload['all_compared_data_equal'] and not diagnostics else 'review_required',
                'playability_verified':False,'coverage':'Official hidden-process tactical-map load/save, exact object/saved-body checks and independent second-file read/save. Unexplained original changes and diagnostics remain findings; no gameplay or terrain-edit certification.'}
        (stage/'map-roundtrip.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return report


def worker(api,content,params,run_dir):
    import ctypes as c
    import struct
    profile=api.profile['tactical_maps'];pointer=api.qword(content+profile['content_map_offset'])
    getter=api.qword(api.qword(pointer)+api.profile['class_getter_vtable_offset'])
    if api.memory(getter,6)!=b'\xb8\x37\x00\x47\x00\xc3':raise FormatError('Native map owner is not the reviewed TTacticalMap')
    directory=Path(run_dir)/'native-map';directory.mkdir();before=api.serialize_object(pointer,maximum=maps.MAX_BYTES)
    with api.memory_stream() as stream:
        status=c.CFUNCTYPE(c.c_int,c.c_void_p,c.c_void_p)(api.dll._handle+profile['write_stream_rva'])(pointer,stream)
        size=struct.unpack_from('<I',stream.raw,api.profile['stream_length_offset'])[0];data=struct.unpack_from('<Q',stream.raw,api.profile['stream_data_offset'])[0]
        if status!=0 or not data or not 36<size<=maps.MAX_BYTES:raise FormatError('Native map writer failed or exceeded size bound')
        saved=api.memory(data,size)
    maps.decode(saved);after=api.serialize_object(pointer,maximum=maps.MAX_BYTES);artifacts={}
    for key,name,data in (('before','before.bin',before),('saved','saved.tam',saved),('after','after.bin',after)):
        path=directory/name;path.write_bytes(data);artifacts[key]={'file':str(path),'sha256':sha(data)}
    return {'map_class':'00470037','save_result':status,'artifacts':artifacts}
