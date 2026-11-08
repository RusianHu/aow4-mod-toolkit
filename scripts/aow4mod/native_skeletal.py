"""Native single skinned FBX mesh import with explicit source/shading witnesses."""
import ctypes as c
from pathlib import Path
import struct

from . import skeletal_sources,skeletal_data,native_fbx,native_meshes,native_objects,editing,skin_meshes,source_correspondence
from .core import Node,FormatError,sha
from .filesystem import file_hash


def compare_finalization(before,after):
    a=Node(before,0,len(before)).mapping();b=Node(after,0,len(after)).mapping()
    if set(native_objects.compare_bodies(before,after)['changed_fields'])-{'meta/0x1A','meta/0x3D','meta/0x3E','meta/0x3F','meta/0x40','meta/0x42'}:raise FormatError('Skeletal finalizer changes unrequested fields')
    x,y=skin_meshes.decode(before),skin_meshes.decode(after)
    if skeletal_data.signature(x['vertex_bytes'],a[0x3d].mapping()[0x16].raw(),x['stride'])!=skeletal_data.signature(y['vertex_bytes'],b[0x3d].mapping()[0x16].raw(),y['stride']):raise FormatError('Skeletal finalizer changes oriented geometry/skin data')
    for key in (0x1a,0x3f):
        old,new=a[key].mapping(),b[key].mapping()
        if set(native_objects.compare_bodies(a[key].raw(),b[key].raw())['changed_fields'])-{'meta/0x16','meta/0x19'}:raise FormatError('Skeletal finalizer changes bind matrix/frame metadata')
        for field in (0x14,0x15,0x18):
            if old[field].raw()!=new[field].raw():raise FormatError('Skeletal finalizer changes root/count/inverse bind')
        raw,changed=old[0x16].raw(),new[0x16].raw()
        if len(raw)!=len(changed) or any(raw[i]!=changed[i] and i%160 not in (*range(140,144),*range(156,160)) for i in range(len(raw))):raise FormatError('Skeletal finalizer changes frame transforms, names or topology')
    return {'oriented_geometry_and_skin_preserved':True,'frame_transforms_names_topology_inverse_bind_preserved':True,'native_derived_frame_words':[140,156]}


def worker(api,params,run_dir):
    source=Path(params['path']);name=native_meshes.namespace(params['namespace']);_,_,_,summary=skeletal_sources.source(source)
    if summary['sha256']!=params['sha256']:raise FormatError('Skeletal FBX changed before import')
    scene=native_fbx.import_scene(api,source)
    if scene['status'] or scene['errors'] or scene['warnings'] or scene['meshes']!=1:raise FormatError('Skeletal importer must produce one diagnostic-free mesh')
    root=Path(run_dir)/'native-skeletal';root.mkdir();objects={}
    def save(label,body):
        path=root/(label+'.bin');path.write_bytes(body);objects[label]={'file':str(path),'sha256':sha(body)};return body
    owner=scene['owner'];mesh=api.qword(api.qword(owner+0x18))
    if api.memory(api.qword(api.qword(mesh)+0x30),6)!=b'\xb8\x35\x00\x41\x00\xc3':raise FormatError('Skeletal mesh class differs')
    if not c.CFUNCTYPE(c.c_bool,c.c_void_p,c.c_void_p)(api.qword(api.qword(owner+8)+0x90))(owner+8,mesh):raise FormatError('Skeletal mesh list rejected child')
    before=save('before',api.serialize_object(mesh));skin_meshes.decode(before)
    hierarchy,hierarchy_change=source_correspondence.repair_hierarchy(source,before);save('hierarchy_authored',hierarchy)
    api.deserialize_object(mesh,hierarchy);hierarchy_loaded=save('hierarchy_loaded',api.serialize_object(mesh))
    hierarchy_initialization=source_correspondence.hierarchy_readback(hierarchy,hierarchy_loaded)
    c.CFUNCTYPE(None,c.c_void_p)(api.dll._handle+api.profile['fbx']['finalize_rva'])(mesh)
    finalized=save('finalized',api.serialize_object(mesh));comparison=compare_finalization(hierarchy_loaded,finalized)
    extended,change=skeletal_data.extend(finalized);extended=editing.rewrite_map(Node(extended,0,len(extended)),{0x14:editing.encode_value('string',f'[{name}]MESH\\0')});save('extended',extended)
    fresh=scene['create'](0x410035);api.deserialize_object(fresh,extended);readback=save('readback',api.serialize_object(fresh))
    first=skin_meshes.compare_body(extended,readback)
    if not first['data_equal']:
        a,b=Node(extended,0,len(extended)).mapping(),Node(readback,0,len(readback)).mapping()
        if set(first['changed_fields'])-{'meta/0x1A','meta/0x3F'}:raise FormatError('Skeletal initial object load changes semantic fields')
        for key in (0x1a,0x3f):
            old,new=a[key].mapping(),b[key].mapping()
            if set(native_objects.compare_bodies(a[key].raw(),b[key].raw())['changed_fields'])-{'meta/0x16'}:raise FormatError('Skeletal initial frame load changes metadata')
            x,y=old[0x16].raw(),new[0x16].raw()
            if len(x)!=len(y) or any(x[i]!=y[i] and i%160 not in (158,159) for i in range(len(x))):raise FormatError('Skeletal initial load changes frame transforms or hierarchy')
    stable=scene['create'](0x410035);api.deserialize_object(stable,readback);again=save('stable',api.serialize_object(stable))
    if not skin_meshes.compare_body(readback,again)['data_equal']:raise FormatError('Skeletal second object load is unstable')
    library=scene['library'];body=api.serialize_object(library);fields=Node(body,0,len(body)).mapping();feature=scene['create'](0x412242);raw=api.serialize_object(feature);raw=editing.rewrite_map(Node(raw,0,len(raw)),{0x10:editing.encode_value('string','Default')})
    encoded=editing.rewrite_map(Node(body,0,len(body)),{0x14:editing.encode_value('string',name),0x15:editing.replace_array_items(fields[0x15],[struct.pack('<I',0x410035)+readback]),0x18:editing.replace_array_items(fields[0x18],[struct.pack('<I',0x412242)+raw])})
    api.deserialize_object(library,encoded);saved=api.write_library(library,name+'.clb',root,32*1024*1024)
    if not scene['guards']() or file_hash(source)!=params['sha256']:raise FormatError('Skeletal importer source/guard changed')
    return {'sha256':params['sha256'],'namespace':name,'sdk_sha256':api.profile['fbx']['sdk_sha256'],'guards_preserved':True,'objects':objects,'artifacts':saved['artifacts'],'comparison':comparison,'change':change,'source_summary':summary,'hierarchy_change':hierarchy_change,'hierarchy_initialization':hierarchy_initialization,'initial_object_comparison':first,'initial_frame_canonicalization_review_required':not first['data_equal'],'draft':True}
