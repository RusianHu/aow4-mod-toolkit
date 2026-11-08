"""Preserve and validate reviewed pooled TREAnimationResource data."""
import math
import struct
import base64

from . import editing,native_objects
from .core import FormatError,Node,sha


def fps_for(tracks):
    f32=lambda x:struct.unpack('<f',struct.pack('<f',x))[0]
    fps=0.0
    for body in tracks:
        fields=Node(body,0,len(body)).mapping()
        period=fields.get(0x13,Node(bytes(8),0,8)).uint(8)
        count=max(fields.get(k,Node(bytes(2),0,2)).uint(2) for k in (0x1c,0x25,0x28))
        if count>32767:raise FormatError('Native animation key count exceeds signed-int16 range')
        if count and period:fps=max(fps,f32(f32(f32(count-1)*1000000.0)/f32(period)))
    return fps


def derive(default,tracks,pools,namespace,title):
    fields=Node(default,0,len(default)).mapping()
    if set(pools)!={'rotation','translation','compressed_rotation','scale'} or not 1<=len(tracks)<=512:
        raise FormatError('Animation track/pool coverage differs')
    empty=Node(b'\0',0,1)
    animation_set=editing.rewrite_map(fields[0x21],{
        0x24:pools['rotation'],0x25:pools['translation'],0x26:pools['compressed_rotation'],0x27:pools['scale'],
        1:editing.rewrite_map(empty,{i:struct.pack('<I',0x410007)+body for i,body in enumerate(tracks)})})
    period=max(Node(body,0,len(body)).mapping().get(0x13,Node(bytes(8),0,8)).uint(8) for body in tracks)
    return editing.rewrite_map(Node(default,0,len(default)),{
        0x14:editing.encode_value('string',f'[{namespace}]ANIM\\0'),0x15:editing.encode_value('string',title),
        0x21:animation_set,0x1f:struct.pack('<Q',period),0x1e:struct.pack('<f',fps_for(tracks))})


def analyze(body,source):
    fields=Node(body,0,len(body)).mapping();period=fields[0x1f].uint(8);items=fields[0x21].mapping()
    if not 0<period<=0x7fffffff:raise FormatError('Animation period exceeds reviewed key-time range')
    if abs(period-source['seconds']*1000000)>1.01:raise FormatError('Imported duration differs from the source take')
    counts={};streams={}
    for role,key,stride in [('rotation',0x24,20),('translation',0x25,16),('scale',0x27,16)]:
        pool=items[key].mapping();count=pool.get(0x15,Node(bytes(4),0,4)).uint(4)
        data=pool[0x16].raw() if 0x16 in pool else b''
        if count>1000000 or len(data)!=count*stride:raise FormatError('Animation pool length/count differs: '+role)
        for row in struct.iter_unpack('<I'+str(stride//4-1)+'f',data):
            if row[0]>period or not all(math.isfinite(x) for x in row[1:]):raise FormatError('Animation pool time/value is invalid')
            if role=='rotation' and abs(sum(x*x for x in row[1:])-1)>0.005:raise FormatError('Animation rotation quaternion is not normalized')
        counts[role]=count;streams[role]=data
    compressed=items[0x26].mapping()[0x15].mapping()
    count=compressed.get(0x1e,Node(bytes(4),0,4)).uint(4)
    data=compressed[0x1f].raw() if 0x1f in compressed else b''
    if count!=counts['rotation'] or len(data)!=count*8:raise FormatError('Compressed/uncompressed rotation pool coverage differs')
    counts['compressed_rotation']=count
    tracks=items[1].mapping()
    if sorted(tracks)!=list(range(len(tracks))) or not 1<=len(tracks)<=512:raise FormatError('Animation track list is not contiguous/bounded')
    names=[];summaries=[]
    for index,node in tracks.items():
        cid,track=node.typed()
        if cid!=0x410007 or track[0x42].uint(4)!=5:raise FormatError('Unsupported animation track class/storage version')
        if set(track)-{0x42,0x14,0x13,0x1a,0x1b,0x1c,0x1d,0x1e,0x1f,0x23,0x24,0x25,0x26,0x27,0x28}:raise FormatError('Unreviewed animation track fields')
        name=track[0x14].string();names.append(name)
        if track.get(0x13,Node(bytes(8),0,8)).uint(8) not in (0,period):raise FormatError('Track period differs from the imported take')
        ranges={}
        for role,offset_key,count_key,stride in [('rotation',0x1e,0x1f,20),('translation',0x1b,0x1c,16),('scale',0x27,0x28,16),('compressed_rotation',0x24,0x25,8)]:
            offset=track.get(offset_key,Node(bytes(4),0,4)).uint(4)
            length=track.get(count_key,Node(bytes(2),0,2)).uint(2)
            if offset+length>counts[role]:raise FormatError('Track range exceeds its shared '+role+' pool')
            if role!='compressed_rotation':
                times=[struct.unpack_from('<I',streams[role],i*stride)[0] for i in range(offset,offset+length)]
                if any(a>=b for a,b in zip(times,times[1:])):raise FormatError('Track key times are not increasing')
            ranges[role]={'offset':offset,'count':length}
        summaries.append({'index':index,'name':name,'ranges':ranges})
    if len(set(n.casefold() for n in names))!=len(names) or not set(source['bone_names'])<=set(names) or not set(names)<=set(source['model_names']):
        raise FormatError('Imported bone/track names differ from the source skeleton')
    fps=fps_for([node.raw()[4:] for node in tracks.values()])
    if fields[0x1e].raw()!=struct.pack('<f',fps):raise FormatError('Native animation derived FPS differs')
    return {'tracks':summaries,'track_count':len(tracks),'pool_counts':counts,'period_native':period,'derived_fps':fps,
        'all_source_bones_present':True,'shared_pool_ranges_valid':True,
        'pose_sampling_verified':False,'model_binding_verified':False,'rendering_verified':False}


def compare(before,after):
    result=native_objects.compare_bodies(before,after)
    if not result['data_equal']:raise FormatError('Native animation independent object data differs')
    return result


def compare_yaml(body,document):
    """Bind independent YAML streams/tracks to complete native body witnesses."""
    from . import asset_yaml
    members=document['21_FList']['1_this']
    if len(members)!=1 or members[0].get('cid')!=str(0x410005):raise FormatError('Animation library member coverage differs')
    member=members[0];fields=Node(body,0,len(body)).mapping();pools=fields[0x21].mapping()
    if struct.pack('<f',float(member['30_FFPS']))!=fields[0x1e].raw():raise FormatError('Animation YAML derived FPS differs')
    for number in (0x14,0x15,0x1f):
        value=next((v for k,v in member.items() if k.startswith(str(number)+'_')),None)
        expected=fields[number].uint(8) if number==0x1f else fields[number].string()
        if str(value)!=str(expected):raise FormatError('Animation YAML identity/period differs')
    value=member['33_FAnimations']
    def data(raw):
        try:return base64.b64decode(''.join(raw.split()),validate=True)
        except Exception as exc:raise FormatError('Invalid animation YAML key stream') from exc
    for key,label,stream_key in [(0x24,'36_FRotationKeys','22_FList'),(0x25,'37_FTranslationKeys','22_FStream'),(0x27,'39_FScaleKeys','22_FStream')]:
        pool=pools[key].mapping();count=pool.get(0x15,Node(bytes(4),0,4)).uint(4)
        if int(value[label].get('21_FCount','0'))!=count or data(value[label].get(stream_key,''))!=(pool[0x16].raw() if 0x16 in pool else b''):
            raise FormatError('Animation YAML shared key data differs: '+label)
    pool=pools[0x26].mapping()[0x15].mapping();encoded=value['38_FCompressedRotationKeys']['21_FRotations']
    if int(encoded.get('30_FCount','0'))!=pool.get(0x1e,Node(bytes(4),0,4)).uint(4) or data(encoded.get('31_FStream',''))!=(pool[0x1f].raw() if 0x1f in pool else b''):
        raise FormatError('Animation YAML compressed key data differs')
    tracks=pools[1].mapping();observed=value['1_this']
    if len(observed)!=len(tracks):raise FormatError('Animation YAML track count differs')
    for index,node in tracks.items():
        cid,track=node.typed();row=observed[index]
        numeric={int(k.split('_')[0]):v for k,v in row.items() if k[:1].isdigit() and '_' in k}
        if row.get('cid')!=str(cid) or set(numeric)!=set(track):raise FormatError('Animation YAML track fields differ')
        for number,raw in track.items():
            if number==0x14:actual=editing.encode_value('string',numeric[number])
            elif number in (0x1a,0x1d,0x23,0x26):actual=struct.pack('<f',float(numeric[number]))
            else:actual=int(numeric[number]).to_bytes(len(raw.raw()),'little')
            if actual!=raw.raw():raise FormatError('Animation YAML track value differs')
