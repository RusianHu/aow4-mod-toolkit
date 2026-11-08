"""Bounded binary FBX source preflight for the initial static-mesh import scope."""
from pathlib import Path
import math
import struct
import zlib

from .core import FormatError,sha

MAGIC=b'Kaydara FBX Binary  \0\x1a\0'
MAX_FILE=16*1024*1024
MAX_EXPANDED=64*1024*1024


def rewrite_arrays(data,updates,object_kind):
    """Replace explicit object child-property bytes, retaining all other bytes.

    Callers must preflight with parse(). Absolute node offsets are rebuilt;
    scalar encodings, unedited arrays and footer are copied verbatim.
    """
    if len(data)>MAX_FILE or data[:23]!=MAGIC:raise FormatError('Invalid bounded FBX rewrite input')
    version=struct.unpack_from('<I',data,23)[0]
    if version not in (7400,7500):raise FormatError('Unsupported FBX rewrite version')
    header=25 if version>=7500 else 13;fmt='<QQQ' if header==25 else '<III';seen=set()
    def read(pos):
        end,count,size=struct.unpack_from(fmt,data,pos)
        if not end:return None,pos+header
        n=data[pos+header-1];name=data[pos+header:pos+header+n];begin=pos+header+n
        children=[];cursor=begin+size;null=False
        while cursor<end:
            child,cursor=read(cursor)
            if child is None:null=True;break
            children.append(child)
        return {'name':name,'count':count,'props':data[begin:begin+size],'children':children,'null':null},end
    nodes=[];cursor=27
    while True:
        node,end=read(cursor)
        if node is None:break
        nodes.append(node);cursor=end
    tail=data[cursor:]
    def write(node,start,owner=None):
        name=node['name'];props=node['props'];count=node['count']
        if name==object_kind.encode():
            if not props or props[0]!=ord('L'):raise FormatError('FBX object identity must be int64')
            owner=struct.unpack_from('<q',props,1)[0]
        key=(owner,name.decode('utf-8'))
        if owner in updates and key[1] in updates[owner]:
            if key in seen:raise FormatError('Duplicate FBX replacement target')
            seen.add(key);props=updates[owner][key[1]];count=1
        pos=start+header+len(name)+len(props);parts=[]
        for child in node['children']:
            raw=write(child,pos,owner);parts.append(raw);pos+=len(raw)
        if node['null']:parts.append(bytes(header));pos+=header
        return struct.pack(fmt,pos,count,len(props))+bytes([len(name)])+name+props+b''.join(parts)
    parts=[];pos=27
    for node in nodes:
        raw=write(node,pos);parts.append(raw);pos+=len(raw)
    if seen!={(identity,key) for identity,fields in updates.items() for key in fields}:raise FormatError('FBX replacement coverage differs')
    result=data[:27]+b''.join(parts)+tail
    if len(result)>MAX_FILE:raise FormatError('Rewritten FBX exceeds source bound')
    return result


def parse(path):
    path=Path(path).resolve()
    if path.stat().st_size>MAX_FILE:raise FormatError('FBX source exceeds 16 MiB')
    data=path.read_bytes()
    if len(data)<27 or data[:23]!=MAGIC:raise FormatError('Initial mesh importer supports little-endian binary FBX only')
    version=struct.unpack_from('<I',data,23)[0]
    if version not in (7400,7500):raise FormatError('Initial mesh importer supports binary FBX 7400/7500 only')
    wide=version>=7500;header=25 if wide else 13;nodes=0;expanded=0
    def fail(message):raise FormatError('FBX preflight: '+message)
    def read_node(start,limit,depth):
        nonlocal nodes,expanded
        nodes+=1
        if nodes>100000 or depth>64:fail('node/depth bound exceeded')
        if start+header>limit:fail('truncated node header')
        end,count,size=struct.unpack_from('<QQQ' if wide else '<III',data,start)
        length=data[start+header-1]
        if not end:
            if any(data[start:start+header]):fail('invalid null node')
            return None,start+header
        begin=start+header+length;props_end=begin+size
        if not begin<=props_end<=end<=limit or end<=start or count>100000:fail('invalid node/property bounds')
        try:name=data[start+header:begin].decode('utf-8')
        except UnicodeError:fail('invalid node name')
        props=[];pos=begin
        for _ in range(count):
            if pos>=props_end:fail('property count exceeds its bytes')
            kind=chr(data[pos]);pos+=1
            if kind in 'YILFDC':
                fmt={'Y':'h','I':'i','L':'q','F':'f','D':'d','C':'B'}[kind];width=struct.calcsize(fmt)
                if pos+width>props_end:fail('truncated scalar')
                value=struct.unpack_from('<'+fmt,data,pos)[0];pos+=width
                if isinstance(value,float) and not math.isfinite(value):fail('non-finite scalar')
            elif kind in 'SR':
                if pos+4>props_end:fail('truncated string/raw length')
                length=struct.unpack_from('<I',data,pos)[0];pos+=4
                if pos+length>props_end:fail('string/raw exceeds properties')
                value=data[pos:pos+length];pos+=length
                if kind=='S':
                    try:value=value.decode('utf-8')
                    except UnicodeError:fail('invalid UTF-8 property')
            elif kind in 'fdlibc':
                if pos+12>props_end:fail('truncated array header')
                length,encoding,packed=struct.unpack_from('<III',data,pos);pos+=12
                width={'f':4,'d':8,'l':8,'i':4,'b':1,'c':1}[kind];expected=length*width;expanded+=expected
                if expanded>MAX_EXPANDED or pos+packed>props_end or encoding not in (0,1):fail('invalid/oversized array')
                payload=data[pos:pos+packed];pos+=packed
                if encoding:
                    dec=zlib.decompressobj()
                    try:payload=dec.decompress(payload,expected+1)
                    except zlib.error:fail('invalid compressed array')
                    if not dec.eof or dec.unused_data or dec.unconsumed_tail:fail('compressed array bounds differ')
                if len(payload)!=expected:fail('array length differs')
                value={'kind':kind,'count':length,'data':payload}
            else:fail('unsupported property type '+repr(kind))
            props.append(value)
        if pos!=props_end:fail('property size differs')
        children=[]
        while pos<end:
            child,pos=read_node(pos,end,depth+1)
            if child is None:
                if pos!=end:fail('extra bytes after null child')
                break
            children.append(child)
        return {'name':name,'properties':props,'children':children},end
    roots=[];pos=27
    while pos+header<=len(data):
        node,pos=read_node(pos,len(data),0)
        if node is None:break
        roots.append(node)
    return data,roots,{'version':version,'nodes':nodes,'expanded_array_bytes':expanded}


def inspect(path):
    path=Path(path).resolve();data,roots,parsed=parse(path)
    version=parsed['version'];nodes=parsed['nodes'];expanded=parsed['expanded_array_bytes']
    def fail(message):raise FormatError('FBX preflight: '+message)
    groups=[node for node in roots if node['name']=='Objects']
    if len(groups)!=1:fail('expected one Objects section')
    allowed={'Geometry','Model','Material','AnimationStack','AnimationLayer'}
    objects=groups[0]['children'];identities=set();geometries=[];models=0;materials=0
    for obj in objects:
        name=obj['name'];values=obj['properties']
        if name not in allowed:fail('unsupported static-scope object '+name)
        if len(values)<3 or type(values[0]) is not int or values[0] in identities:fail('invalid/duplicate object ID')
        identities.add(values[0])
        if name in ('Geometry','Model') and values[2]!='Mesh':fail('non-mesh geometry/model is unsupported')
        if name=='Model':models+=1
        if name=='Material':materials+=1
        if name=='Geometry':
            arrays={node['name']:node['properties'] for node in obj['children'] if node['name'] in ('Vertices','PolygonVertexIndex')}
            if any(key not in arrays or len(arrays[key])!=1 or not isinstance(arrays[key][0],dict) for key in ('Vertices','PolygonVertexIndex')):
                fail('mesh geometry arrays missing')
            v=arrays['Vertices'][0];i=arrays['PolygonVertexIndex'][0]
            if v['kind']!='d' or v['count']%3 or not 0<v['count']<=3000000 or i['kind']!='i':fail('unsupported geometry array type/count')
            count=v['count']//3;triangles=0;corners=0
            if any(not math.isfinite(x[0]) for x in struct.iter_unpack('<d',v['data'])):fail('non-finite source vertices')
            for (index,) in struct.iter_unpack('<i',i['data']):
                ending=index<0;index=-index-1 if ending else index
                if index>=count:fail('source polygon index outside vertices')
                corners+=1
                if ending:
                    if corners<3:fail('source polygon has fewer than three corners')
                    triangles+=corners-2;corners=0
            if corners or not triangles:fail('incomplete/empty source polygons')
            geometries.append({'control_points':count,'expected_triangles_before_instancing':triangles})
    if not geometries or not models or not materials:fail('source requires mesh geometry, a mesh model and material')
    if len(geometries)>64 or models>64:fail('static object count exceeds 64')
    references=[node for node in roots if node['name']=='References' and node['children']]
    if references:fail('external references are unsupported')
    return {'format':'aow4mod-fbx-source/1','file':str(path),'sha256':sha(data),'version':version,
            'geometries':geometries,'models':models,'materials':materials,'nodes':nodes,'expanded_array_bytes':expanded,
            'scope':'Binary static mesh source only; textures, deformers, skeletons, cameras, lights and animation curves are refused. Empty animation stack/layer records are allowed.'}
