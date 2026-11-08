"""Explicit uniform baking of linear FBX transform curves; untouched data retained."""
import bisect
import math
from pathlib import Path
import struct
import uuid

from . import animation_sources,fbx
from .core import FormatError,sha
from .filesystem import file_hash,new_directory


def bake(source,fps,max_error=0.001):
    if type(fps) not in (int,float) or not math.isfinite(fps) or not 1<=fps<=120:raise FormatError('Animation baking FPS must be finite within 1..120')
    if type(max_error) not in (int,float) or not math.isfinite(max_error) or max_error<0:raise FormatError('Animation baking error must be finite and nonnegative')
    summary=animation_sources.inspect(source,False);data,roots,_=fbx.parse(source)
    if sha(data)!=summary['sha256']:raise FormatError('FBX changed during baking')
    objects={o['properties'][0]:o for r in roots if r['name']=='Objects' for o in r['children']}
    connections=[r['properties'] for n in roots if n['name']=='Connections' for r in n['children']]
    nodes={p[1] for p in connections if p[0]=='OP' and objects[p[1]]['name']=='AnimationCurveNode' and objects.get(p[2],{}).get('name')=='Model'}
    curves={p[1] for p in connections if p[0]=='OP' and p[2] in nodes and objects[p[1]]['name']=='AnimationCurve'}
    frames=max(1,int(math.floor(summary['seconds']*fps+0.5)))
    if frames>=32767:raise FormatError('Baked frame count exceeds signed-int16 native bounds')
    stop=summary['stop'];times=[(stop*i+frames//2)//frames for i in range(frames+1)]
    def array(kind,values):
        fmt={'l':'q','f':'f','i':'i'}[kind];raw=struct.pack('<'+str(len(values))+fmt,*values)
        return kind.encode()+struct.pack('<III',len(values),0,len(raw))+raw
    updates={};maximum_error=0.0
    for identity in curves:
        fields={c['name']:c['properties'][0] for c in objects[identity]['children'] if len(c['properties'])==1}
        try:
            flags=fields['KeyAttrFlags'];refs=fields['KeyAttrRefCount'];attributes=fields['KeyAttrDataFloat']
            if flags['kind']!='i' or refs['kind']!='i' or flags['count']!=refs['count'] or any((n[0]&15)!=4 for n in struct.iter_unpack('<i',flags['data'])):
                raise FormatError('Uniform baking currently requires explicitly linear transform curves')
            if attributes['kind']!='f' or attributes['count']!=4*flags['count'] or any(n[0]<1 for n in struct.iter_unpack('<i',refs['data'])) or sum(n[0] for n in struct.iter_unpack('<i',refs['data']))!=fields['KeyTime']['count']:
                raise FormatError('FBX linear key attribute coverage differs')
            original=[v[0] for v in struct.iter_unpack('<q',fields['KeyTime']['data'])]
            values=[v[0] for v in struct.iter_unpack('<f',fields['KeyValueFloat']['data'])]
        except (KeyError,TypeError) as exc:raise FormatError('FBX baking requires complete linear curve attributes') from exc
        sampled=[]
        for time in times:
            i=max(0,min(len(original)-1,bisect.bisect_right(original,time)-1));j=min(i+1,len(original)-1)
            fraction=max(0,min(1,(time-original[i])/(original[j]-original[i]))) if i!=j else 0
            sampled.append(values[i]+fraction*(values[j]-values[i]))
        unrounded=sampled;sampled=list(struct.unpack('<'+str(len(sampled))+'f',struct.pack('<'+str(len(sampled))+'f',*sampled)))
        maximum_error=max(maximum_error,max(abs(a-b) for a,b in zip(unrounded,sampled)))
        for time,value in zip(original,values):
            i=max(0,min(len(times)-1,bisect.bisect_right(times,time)-1));j=min(i+1,len(times)-1)
            t=(time-times[i])/(times[j]-times[i]) if i!=j else 0
            maximum_error=max(maximum_error,abs(value-(sampled[i]+t*(sampled[j]-sampled[i]))))
        if maximum_error>max_error:raise FormatError('Uniform FBX bake exceeds the requested source-scalar error bound')
        updates[identity]={'KeyTime':array('l',times),'KeyValueFloat':array('f',sampled),
            'KeyAttrFlags':array('i',[4]),'KeyAttrDataFloat':array('f',[0,0,0,0]),'KeyAttrRefCount':array('i',[frames+1])}
    wide=summary['version']>=7500;header=25 if wide else 13;fmt='<QQQ' if wide else '<III'
    def read(pos):
        end,count,size=struct.unpack_from(fmt,data,pos)
        if not end:return None,pos+header
        length=data[pos+header-1];name=data[pos+header:pos+header+length];begin=pos+header+length
        props=data[begin:begin+size];cursor=begin+size;children=[];null=False
        while cursor<end:
            child,cursor=read(cursor)
            if child is None:null=True;break
            children.append(child)
        return {'name':name,'count':count,'props':props,'children':children,'null':null},end
    raw_nodes=[];cursor=27
    while True:
        node,end=read(cursor)
        if node is None:break
        raw_nodes.append(node);cursor=end
    tail=data[cursor:]
    def write(node,start,owner=None):
        name=node['name'];props=node['props'];count=node['count']
        if name==b'AnimationCurve':
            if not props or props[0]!=ord('L'):raise FormatError('FBX curve identity is not int64')
            owner=struct.unpack_from('<q',props,1)[0]
        if owner in updates and name.decode() in updates[owner]:props=updates[owner][name.decode()];count=1
        pos=start+header+len(name)+len(props);body=b''
        for child in node['children']:
            raw=write(child,pos,owner);body+=raw;pos+=len(raw)
        if node['null']:body+=bytes(header);pos+=header
        return struct.pack(fmt,pos,count,len(props))+bytes([len(name)])+name+props+body
    output=b'';pos=27
    for node in raw_nodes:
        raw=write(node,pos);output+=raw;pos+=len(raw)
    output=data[:27]+output+tail
    if len(output)>fbx.MAX_FILE:raise FormatError('Baked FBX exceeds the 16 MiB import bound')
    return output,{'fps_requested':fps,'fps_effective':frames/summary['seconds'],'frames':frames+1,'curves':len(curves),
        'max_error_requested':max_error,'maximum_source_scalar_error':maximum_error,
        'source_sha256':summary['sha256'],'baked_sha256':sha(output),'scope':'Uniform samples of explicit linear TRS curves; other property bytes/footer retained, node offsets rebuilt. Cubic/step curves, layers, constraints and retargeting are unsupported.'}


def prepare(source,fps,cache,max_error=0.001):
    source=Path(source).resolve();raw,details=bake(source,fps,max_error)
    output=Path(cache)/'animation-bakes'/uuid.uuid4().hex;output.parent.mkdir(parents=True,exist_ok=True)
    with new_directory(output,[source.parent]) as stage:
        (stage/'baked.fbx').write_bytes(raw)
        animation_sources.inspect(stage/'baked.fbx')
    return {'original':str(source),'original_sha256':details['source_sha256'],'candidate':str(output/'baked.fbx'),'details':details}


def verify(record):
    if file_hash(record['original'])!=record['original_sha256']:raise FormatError('Original FBX changed after uniform baking')
    raw,details=bake(record['original'],record['details']['fps_requested'],record['details']['max_error_requested'])
    if details!=record['details'] or file_hash(record['candidate'])!=sha(raw):raise FormatError('FBX bake recipe/output differs')
    return raw
