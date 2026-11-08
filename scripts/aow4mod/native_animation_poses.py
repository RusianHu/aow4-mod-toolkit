"""Read-only CPU channel getters on a legitimately loaded animation resource."""
import ctypes as c
from pathlib import Path
import struct

from . import animation_poses
from .core import FormatError,Node,sha


def worker(api,content,params,run_dir):
    profile=api.profile['animation'];library=api.qword(content+api.profile['clb_library_offset'])
    def cid(ptr,expected):
        code=api.memory(api.qword(api.qword(ptr)+0x30),6)
        if code!=b'\xb8'+struct.pack('<I',expected)+b'\xc3':raise FormatError('Native animation pose class differs')
    cid(library,api.profile['clb_class_id']);listing=library+api.profile['asset_templates']['list_offset'];cid(listing,0x400081)
    count=int.from_bytes(api.memory(listing+0x18,4),'little');array=api.qword(listing+0x10);selected=params['selection'];ordinal=selected['ordinal']
    if type(ordinal) is not int or not 0<=ordinal<count<=100000:raise FormatError('Native animation pose ordinal exceeds bounds')
    obj=api.qword(array+ordinal*8);cid(obj,0x410005)
    before=api.serialize_object(obj);fields=Node(before,0,len(before)).mapping()
    if fields[0x14].string()!=selected['locator']:raise FormatError('Native animation pose locator differs')
    decoded=animation_poses.decode(before);names=params['tracks'];times=params['times']
    if not 1<=len(names)<=32 or len(set(names))!=len(names) or not set(names)<=set(decoded['tracks']):raise FormatError('Select existing unique animation tracks')
    if not 1<=len(times)<=64 or len(set(times))!=len(times) or any(type(t) is not int or not 0<=t<=decoded['period'] for t in times):raise FormatError('Animation sample time is outside its take')
    tracks=api.qword(obj+profile['tracks_data']);n=int.from_bytes(api.memory(obj+profile['tracks_count'],4),'little')
    if n!=len(decoded['tracks']):raise FormatError('Native animation track count differs')
    sampler=c.CFUNCTYPE(c.c_bool,c.c_void_p,c.c_void_p,c.c_int32,c.c_bool)
    rotation=sampler(api.dll._handle+profile['rotation_sample_rva']);vector=sampler(api.dll._handle+profile['vector_sample_rva'])
    compressed=c.CFUNCTYPE(c.c_bool,c.c_void_p,c.c_float,c.c_bool,c.c_void_p)(api.dll._handle+profile['compressed_sample_rva'])
    def guarded(fn,size):
        buffer=c.create_string_buffer(128);start=c.addressof(buffer);c.memset(start,0xa5,len(buffer));out=(start+47)&~15
        if not fn(out):raise FormatError('Native sampler did not return its declared channel')
        if api.memory(start,out-start)!=b'\xa5'*(out-start) or api.memory(out+size,start+128-out-size)!=b'\xa5'*(start+128-out-size):raise FormatError('Native animation sampler output guard was overwritten')
        return list(struct.unpack('<'+str(size//4)+'f',api.memory(out,size)))
    rows=[]
    for name in names:
        record=decoded['tracks'][name];track=api.qword(tracks+record['index']*8);cid(track,0x410007)
        for descriptor,pool in [(0x38,0xf8),(0x50,0x118),(0x68,0x148),(0x80,0x188)]:
            if api.qword(track+descriptor)!=obj+pool:raise FormatError('Animation track is not attached to the actual resource key pools')
        ratio=struct.unpack('<f',api.memory(track+0x70,4))[0]
        if ratio!=record['ratio']:raise FormatError('Native compressed animation time mapping differs')
        offset=int.from_bytes(api.memory(track+0x78,4),'little');length=len(record['keys']['compressed_rotation'])
        if length!=int.from_bytes(api.memory(track+0x7c,2),'little') or offset!=record['compressed_offset']:raise FormatError('Native compressed animation range differs')
        for time in times:
            row={'name':name,'time_us':time,'guards_preserved':True}
            for role,descriptor in [('rotation',0x38),('translation',0x50),('scale',0x80)]:
                keys=record['keys'][role]
                if role=='rotation' and length:
                    position=animation_poses.f32(offset+max(0,min(length-1,animation_poses.f32(animation_poses.f32(time)*ratio))))
                    row[role]=guarded(lambda out:compressed(obj+0x158,position,decoded['interpolate'],out),16)
                elif not keys:row[role]=None
                elif role=='rotation' and time>=keys[-1][0]:
                    # The raw fallback helper wraps its search after the last key;
                    # this inspector explicitly clamps the local curve boundary.
                    index=record['rotation_offset']+len(keys)-1
                    pointer=api.qword(obj+0x110)+index*20+4
                    row[role]=list(struct.unpack('<4f',api.memory(pointer,16)))
                else:
                    fn=rotation if role=='rotation' else vector;size=16 if role=='rotation' else 12
                    row[role]=guarded(lambda out:fn(track+descriptor,out,time,decoded['interpolate']),size)
            rows.append(row)
    after=api.serialize_object(obj)
    if before!=after:raise FormatError('CPU pose sampling altered serialized animation data')
    directory=Path(run_dir)/'animation-poses';directory.mkdir();objects={}
    for role,raw in [('before',before),('after',after)]:
        path=directory/(role+'.bin');path.write_bytes(raw);objects[role]={'file':str(path),'sha256':sha(raw)}
    return {'animation':selected,'samples':rows,'interpolate':decoded['interpolate'],'objects':objects,
        'period_us':decoded['period'],'channel_scope':'local_animation_curves_with_clamped_boundaries'}
