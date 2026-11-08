"""Capture actual component meshes/maps and independently rebuild native palettes."""
import ctypes as c
from pathlib import Path
import struct

from . import skin_meshes
from .core import FormatError,sha


def worker(api,content,params,run_dir):
    library=api.qword(content+api.profile['clb_library_offset']);listing=library+api.profile['asset_templates']['list_offset'];count=int.from_bytes(api.memory(listing+0x18,4),'little');data=api.qword(listing+0x10)
    def cid(ptr,expected):
        if api.memory(api.qword(api.qword(ptr)+0x30),6)!=b'\xb8'+struct.pack('<I',expected)+b'\xc3':raise FormatError('Skinning native object class differs')
    cid(library,0x410060);ordinal=params['model']['ordinal']
    if type(ordinal) is not int or not 0<=ordinal<count<=100000:raise FormatError('Skinning model ordinal exceeds bounds')
    model=api.qword(data+ordinal*8);cid(model,0x41004b);before=api.serialize_object(model)
    lod_count=int.from_bytes(api.memory(model+0x1b8,4),'little');lod_index=params['lod']
    if type(lod_index) is not int or not 0<=lod_index<lod_count<=16:raise FormatError('Skinning LOD exceeds bounds')
    lod=api.qword(api.qword(model+0x1b0)+lod_index*8);cid(lod,0x412e00)
    n=int.from_bytes(api.memory(lod+0x28,4),'little');index=params['component']
    if type(index) is not int or not 0<=index<n<=64 or int.from_bytes(api.memory(lod+0x48,4),'little')!=n:raise FormatError('Skinning component/map coverage differs')
    component=api.qword(api.qword(lod+0x20)+index*8);cid(component,0x410067)
    mesh=c.CFUNCTYPE(c.c_void_p,c.c_void_p)(api.dll._handle+api.profile['model_links']['get_resource_rva'])(component+0x38);cid(mesh,0x410035)
    mesh_before=api.serialize_object(mesh);decoded=skin_meshes.decode(mesh_before)
    map_ptr=api.qword(api.qword(lod+0x40)+index*8);cid(map_ptr,0x4100a0);stored=api.serialize_object(map_ptr)
    handle=api.create(0x4100a0)
    if not handle:raise FormatError('Native matrix-map factory is unavailable')
    holder=c.create_string_buffer(16);api.private('borrow_object_rva',[c.c_void_p,c.c_uint64],c.c_void_p)(holder,handle);fresh=struct.unpack_from('<Q',holder.raw)[0];cid(fresh,0x4100a0)
    # Validate actual frame-to-serialized bone IDs before the native map builder.
    frames=mesh+0xc8;cid(frames,0x41007f)
    for row in decoded['frames']:
        entry=c.CFUNCTYPE(c.c_void_p,c.c_void_p,c.c_int32)(api.dll._handle+api.profile['skinning']['mesh_frame_rva'])(frames,row['frame'])
        if int.from_bytes(api.memory(entry+8,4),'little',signed=True)!=row['bone']:raise FormatError('Live mesh bone index differs from the serialized frame')
    c.CFUNCTYPE(None,c.c_void_p,c.c_void_p,c.c_void_p)(api.dll._handle+api.profile['skinning']['map_rva'])(fresh,frames,model+0xc0)
    rebuilt=api.serialize_object(fresh)
    root=Path(run_dir)/'skinning';root.mkdir();objects={}
    for role,raw in [('model_before',before),('model_after',api.serialize_object(model)),('mesh_before',mesh_before),('mesh_after',api.serialize_object(mesh)),('map_stored',stored),('map_rebuilt',rebuilt)]:
        path=root/(role+'.bin');path.write_bytes(raw);objects[role]={'file':str(path),'sha256':sha(raw)}
    return {'model':params['model'],'lod':lod_index,'component':index,'objects':objects,'native_palette_rebuilt':True}
