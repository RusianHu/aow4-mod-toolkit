"""Model-local mapping/pose calls with owned native containers and real resources."""
import ctypes as c
from pathlib import Path
import struct

from . import model_poses,animation_poses
from .core import FormatError,Node,sha


def worker(api,content,params,run_dir):
    profile=api.profile['model_animation'];base=api.dll._handle;library=api.qword(content+api.profile['clb_library_offset'])
    def cid(ptr,expected):
        if api.memory(api.qword(api.qword(ptr)+0x30),6)!=b'\xb8'+struct.pack('<I',expected)+b'\xc3':raise FormatError('Native model-pose class differs')
    cid(library,api.profile['clb_class_id']);listing=library+api.profile['asset_templates']['list_offset'];cid(listing,0x400081)
    n=int.from_bytes(api.memory(listing+0x18,4),'little');data=api.qword(listing+0x10)
    def selected(identity,expected):
        ordinal=identity['ordinal']
        if type(ordinal) is not int or not 0<=ordinal<n<=100000:raise FormatError('Native model-pose source ordinal differs')
        ptr=api.qword(data+ordinal*8);cid(ptr,expected);raw=api.serialize_object(ptr);f=Node(raw,0,len(raw)).mapping()
        if f[0x14].string()!=identity['locator']:raise FormatError('Native model-pose selected locator differs')
        return ptr,raw
    model,model_before=selected(params['model'],0x41004b);animation,animation_before=selected(params['animation'],0x410005)
    mf=Node(model_before,0,len(model_before)).mapping();skeleton=model_poses.frames(mf[0x21]);clip=animation_poses.decode(animation_before)
    for time in params['times']:
        if type(time) is not int or not 0<=time<=clip['period']:raise FormatError('Model pose time is outside its take')
        model_poses.expected(skeleton,clip,time) # Reject unsupported/missing mapped channels before native calls.
    frames=model+profile['frames_offset'];cid(frames,0x410084)
    ready=api.memory(model+0x24,1)[0]&3==3
    link=model+profile['default_animation_offset'];cid(link,0x41006d)
    resolved=c.CFUNCTYPE(c.c_void_p,c.c_void_p)(base+api.profile['model_links']['get_resource_rva'])(link)
    tracks=api.qword(animation+api.profile['animation']['tracks_data']);count=int.from_bytes(api.memory(animation+api.profile['animation']['tracks_count'],4),'little')
    if count!=len(clip['tracks']):raise FormatError('Native model animation track count differs')
    pointers={api.qword(tracks+r['index']*8):r['index'] for r in clip['tracks'].values()}
    # AB5310 itself initializes this plain pointer/count/capacity POD array to zero.
    storage=c.create_string_buffer(16+128);start=c.addressof(storage);c.memset(start,0xa5,len(storage));mapping_ptr=start+64;c.memset(mapping_ptr,0,16)
    c.CFUNCTYPE(None,c.c_void_p,c.c_void_p,c.c_void_p)(base+profile['map_rva'])(mapping_ptr,frames,animation)
    mapped_count=int.from_bytes(api.memory(mapping_ptr+8,4),'little');array=api.qword(mapping_ptr)
    if mapped_count!=len(skeleton['frames']):raise FormatError('Native frame/track mapping coverage differs')
    mapping=[]
    for frame in skeleton['frames']:
        pointer=api.qword(array+frame['index']*8)
        if pointer and pointer not in pointers:raise FormatError('Native mapped track is not in the actual animation resource')
        mapping.append({'frame':frame['index'],'name':frame['name'],'track':pointers.get(pointer)})
    root=skeleton['root']
    if int.from_bytes(api.memory(frames+0x30,4),'little')!=len(skeleton['frames']) or int.from_bytes(api.memory(frames+0x34,4),'little',signed=True)!=root:
        raise FormatError('Native model frame/root count differs')
    directory=Path(run_dir)/'model-poses';directory.mkdir();objects={};poses=[];guards=True
    def save(label,raw):
        path=directory/(label+'.bin');path.write_bytes(raw);return {'file':str(path),'sha256':sha(raw)}
    for label,raw in [('model_before',model_before),('animation_before',animation_before)]:objects[label]=save(label,raw)
    for index,time in enumerate(params['times']):
        context=c.create_string_buffer(struct.pack('<QQQq',frames,animation,mapping_ptr,time))
        pose={'time_us':time}
        for role in ('frames','bones'):
            buffer=c.create_string_buffer(40+128);begin=c.addressof(buffer);c.memset(begin,0xa5,len(buffer));ptr=begin+64;c.memset(ptr,0,40)
            c.CFUNCTYPE(c.c_void_p,c.c_void_p)(base+profile['matrix_ctor_rva'])(ptr)
            try:
                cid(ptr,0x400010)
                if role=='frames':
                    c.CFUNCTYPE(None,c.c_void_p,c.c_int32)(base+profile['matrix_resize_rva'])(ptr,len(skeleton['frames']))
                    result=c.CFUNCTYPE(c.c_int32,c.c_void_p,c.c_void_p,c.c_int32,c.c_void_p)(base+profile['pose_rva'])(ptr,context,root,None)
                    if result!=-1:raise FormatError('Native root unexpectedly has an unprocessed sibling')
                else:c.CFUNCTYPE(None,c.c_void_p,c.c_void_p)(base+profile['bone_pose_rva'])(ptr,context)
                pose[role]=save(f'{index:02d}-{role}',api.serialize_object(ptr))
                guards=guards and api.memory(begin,64)==b'\xa5'*64 and api.memory(ptr+40,64)==b'\xa5'*64
            finally:c.CFUNCTYPE(None,c.c_void_p)(base+profile['matrix_dtor_rva'])(ptr)
        poses.append(pose)
    objects['model_after']=save('model_after',api.serialize_object(model));objects['animation_after']=save('animation_after',api.serialize_object(animation))
    guards=guards and api.memory(start,64)==b'\xa5'*64 and api.memory(mapping_ptr+16,64)==b'\xa5'*64
    return {'model':params['model'],'animation':params['animation'],'mapping':mapping,'poses':poses,'objects':objects,
        'guards_preserved':guards,'model_ready':ready,'default_animation_matches_selected':resolved==animation}
