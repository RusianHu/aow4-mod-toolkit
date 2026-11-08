"""Reviewed process-owned FBX importer initialization, shared by asset workflows."""
import ctypes as c
from pathlib import Path
import struct
from .core import FormatError
from .filesystem import file_hash


def import_scene(api,source):
    profile=api.profile['fbx'];base=api.dll._handle
    get=c.windll.kernel32.GetModuleHandleW;get.argtypes=[c.c_wchar_p];get.restype=c.c_void_p
    sdk=get('libfbxsdk.dll');filename=c.create_unicode_buffer(32768)
    readname=c.windll.kernel32.GetModuleFileNameW;readname.argtypes=[c.c_void_p,c.c_wchar_p,c.c_uint];readname.restype=c.c_uint
    if not sdk or not readname(sdk,filename,len(filename)) or file_hash(filename.value)!=profile['sdk_sha256']:
        raise FormatError('Loaded FBX SDK does not match the reviewed profile')
    storage=c.create_string_buffer(0x238+0x80);start=c.addressof(storage);c.memset(start,0xa5,len(storage));owner=start+0x40;c.memset(owner,0,0x238)
    def call(key,ptr):return c.CFUNCTYPE(c.c_void_p,c.c_void_p)(base+profile[key])(ptr)
    def table(ptr,key):c.c_uint64.from_address(ptr).value=base+profile[key]
    call('object_list_ctor',owner+8);table(owner+8,'mesh_list_vtable')
    call('object_list_ctor',owner+0x48);table(owner+0x48,'animation_list_vtable')
    call('object_ctor',owner+0x68);table(owner+0x68,'rotation_vtable')
    call('translation_ctor',owner+0x88)
    call('object_ctor',owner+0xb8);table(owner+0xb8,'compressed_rotation_vtable')
    call('compressed_quat_ctor',owner+0xc8);call('translation_ctor',owner+0xf8);call('frame_set_ctor',owner+0x138)
    for offset in (0x1c0,0x1d8,0x208,0x220):call('strings_ctor',owner+offset)
    call('string_ctor',owner+0x1f0)
    handles=[];borrowed=[]
    def create(cid):
        handle=api.create(cid)
        if not handle:raise FormatError('Native mesh/library factory failed')
        handles.append(handle);holder=c.create_string_buffer(16)
        api.private('borrow_object_rva',[c.c_void_p,c.c_uint64],c.c_void_p)(holder,handle)
        ptr=struct.unpack_from('<Q',holder.raw)[0]
        if not ptr:raise FormatError('Native mesh/library borrowing failed')
        borrowed.append(ptr);return ptr
    library=create(api.profile['clb_class_id']);c.c_uint64.from_address(owner).value=library
    def diagnostic(offset):
        ptr=owner+offset;count=c.CFUNCTYPE(c.c_int,c.c_void_p)(api.qword(api.qword(ptr)+0x78))(ptr)
        if not 0<=count<=1000:raise FormatError('Native FBX diagnostics exceed their bound')
        data=api.qword(ptr+8);result=[]
        for i in range(count):
            text=api.qword(data+i*24)
            result.append(c.string_at(text).decode('utf-8','replace')[:4000] if text else '')
        return result
    # Every conversion owns a fresh process. All result arrays stay alive until
    # BFinish exits it; callers may explicitly release lists after saving.
    status=c.CFUNCTYPE(c.c_int32,c.c_char_p,c.c_void_p)(base+profile['import_rva'])(str(source).encode('utf-8'),owner)
    errors,warnings=diagnostic(0x1c0),diagnostic(0x1d8)
    count=int.from_bytes(api.memory(owner+0x20,4),'little');array=api.qword(owner+0x18)
    animations=int.from_bytes(api.memory(owner+0x60,4),'little');frames=int.from_bytes(api.memory(owner+0x15c,4),'little')
    def guards():return api.memory(start,0x40)==b'\xa5'*0x40 and api.memory(owner+0x238,0x40)==b'\xa5'*0x40
    return {'owner':owner,'storage':storage,'library':library,'create':create,'call':call,
        'status':status,'errors':errors,'warnings':warnings,'meshes':count,'animations':animations,'frames':frames,'guards':guards}
