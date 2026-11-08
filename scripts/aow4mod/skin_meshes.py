"""Reviewed MeshDEV skin declarations, bone names and inverse-bind matrices."""
import base64
import math
import struct

from .core import Node,FormatError


def yaml_body(member):
    """Project required YAML bytes for pure decoding; never construct a native object."""
    from . import editing
    pack=lambda fields:editing.rewrite_map(Node(b'\0',0,1),fields)
    binary=lambda value:base64.b64decode(''.join(value.split()),validate=True)
    vertices=member['62_FVertices'];fmt=vertices['20_FFormat'];frames=member['26_FFrames'];matrices=frames['24_FBoneMatrices']
    return pack({0x14:editing.encode_value('string',member['20_FName']),0x3c:struct.pack('<I',int(member['60_Version'])),
        0x3e:pack({0x14:pack({0x14:struct.pack('<I',int(fmt['20_FVertexSize'])),0x15:struct.pack('<I',int(fmt['21_FCount'])),
            0x16:struct.pack('<I',int(fmt['22_&FAlignmentMode'])),0x17:binary(fmt['23_FList'])}),
            0x15:struct.pack('<I',int(vertices['21_FCount'])),0x16:binary(vertices['22_FStream'])}),
        0x1a:pack({0x15:struct.pack('<I',int(frames['21_FCount'])),0x16:binary(frames['22_Frames']),
            0x18:pack({0x15:struct.pack('<I',int(matrices['21_FCount'])),0x16:binary(matrices['22_FList'])})})})


def decode(body):
    fields=Node(body,0,len(body)).mapping()
    if fields[0x3c].uint(4)!=3:raise FormatError('Skin inspection requires MeshDEV v3')
    vertices=fields[0x3e].mapping();fmt=vertices[0x14].mapping();stride=fmt[0x14].uint(4);count=vertices[0x15].uint(4)
    raw=vertices[0x16].raw();decl=fmt[0x17].raw()
    if not 1<=count<=1000000 or not 32<=stride<=256 or len(raw)!=count*stride or len(decl)!=fmt[0x15].uint(4)*8 or fmt[0x16].uint(4)!=1:
        raise FormatError('Skin vertex stream/declaration bounds differ')
    entries=list(struct.iter_unpack('<IHH',decl));attributes={a:(b,c) for a,b,c in entries}
    if len(attributes)!=len(entries) or attributes.get(0x10)!=(3,0) or attributes.get(0x80,(0,0))[0]!=0xa4 or attributes.get(0x90,(0,0))[0] not in (2,3):
        raise FormatError('Skin profile needs float3 position, uint8x4 indices and float2/float3 weights')
    weight_components,weight_offset=attributes[0x90];index_offset=attributes[0x80][1];influences=weight_components+1
    ranges=[(0,12),(index_offset,index_offset+4),(weight_offset,weight_offset+4*weight_components)]
    if any(b>stride for a,b in ranges) or any(max(a,c)<min(b,d) for i,(a,b) in enumerate(ranges) for c,d in ranges[i+1:]):raise FormatError('Skin attributes overlap or escape the vertex')
    frames=fields[0x1a].mapping();n=frames[0x15].uint(4);data=frames[0x16].raw();matrices=frames[0x18].mapping();bones=matrices.get(0x15,Node(bytes(4),0,4)).uint(4)
    bind=matrices[0x16].raw() if 0x16 in matrices else b''
    if not 1<=n<=256 or not 1<=bones<=256 or len(data)!=n*160 or len(bind)!=bones*64:raise FormatError('Unsupported mesh frame/inverse-bind layout')
    names=[None]*bones;frame_rows=[]
    for i in range(n):
        record=data[i*160:(i+1)*160];name=record[:32].split(b'\0',1)[0].decode('utf-8','strict');bone=struct.unpack_from('<i',record,136)[0]
        if not name or len(name.encode())>=32 or bone<-1 or bone>=bones or bone!=-1 and names[bone] is not None:raise FormatError('Invalid/duplicate mesh bone identity')
        frame_rows.append({'frame':i,'name':name,'bone':bone})
        if bone!=-1:names[bone]=name
    present=[n for n in names if n is not None]
    if len(set(present))!=len(present):raise FormatError('Duplicate mesh bone names')
    inverse=[list(row) for row in struct.iter_unpack('<16f',bind)]
    if any(not all(math.isfinite(v) for v in row) for row in inverse):raise FormatError('Nonfinite mesh inverse-bind matrix')
    rows=[];roundoff=0;used=set()
    for i in range(count):
        offset=i*stride;position=list(struct.unpack_from('<3f',raw,offset));indices=list(raw[offset+index_offset:offset+index_offset+influences]);weights=list(struct.unpack_from('<'+str(weight_components)+'f',raw,offset+weight_offset))
        if not all(math.isfinite(v) for v in position+weights) or any(not 0<=v<=1 for v in weights) or any(v>=bones for v in indices):raise FormatError('Invalid skin position, weight or shader-read bone index')
        residual=1.0
        for weight in weights:residual=struct.unpack('<f',struct.pack('<f',residual-weight))[0]
        if not -1e-6<=residual<=1+1e-6:raise FormatError('Implicit skin weight exceeds the reviewed roundoff bound')
        if residual<0:roundoff+=1
        used.update(indices);rows.append({'position':position,'indices':indices,'explicit_weights':weights,'weights':weights+[residual]})
    if any(names[i] is None for i in used):raise FormatError('A shader-read inverse-bind entry has no mesh bone name')
    return {'vertex_count':count,'stride':stride,'influences':influences,'index_offset':index_offset,'weight_offset':weight_offset,
        'declaration':decl,'vertex_bytes':raw,'frames':frame_rows,'bone_names':names,'inverse_bind':inverse,'vertices':rows,
        'used_palette_entries':sorted(used),'negative_roundoff_residuals':roundoff}


def point(position,matrix):return [sum(position[k]*matrix[k*4+j] for k in range(4)) for j in range(4)]


def frame_data(raw):
    """Semantic bytes of the pinned 160-byte MeshFrame serialization.

    B6C980 writes/reads flags at 156 and 157 (B6CB76/B6CB82 and
    B6CCB9/B6CCCA), skips 158..159, then archives count*160 bytes.
    Those two reserved bytes can contain process-local scratch contents.
    Keep original artifacts/hashes; normalize only this comparison projection.
    """
    if not raw or len(raw)%160 or len(raw)>256*160:raise FormatError('Mesh frame comparison layout exceeds profile')
    result=bytearray(raw)
    for i in range(158,len(raw),160):result[i:i+2]=b'\0\0'
    return bytes(result)


def compare_body(before,after):
    """Compare checked MeshDEV bodies, projecting only proven frame padding."""
    from . import native_objects
    decode(before);decode(after)
    a,b=Node(before,0,len(before)).mapping(),Node(after,0,len(after)).mapping();raw=native_objects.compare_bodies(before,after);normalized=[];changed=[]
    for key in a.keys()|b.keys():
        label=f'meta/0x{key:X}'
        if key not in a or key not in b:changed.append(label);continue
        if a[key].raw()==b[key].raw():continue
        if key not in (0x1a,0x3f):changed.append(label);continue
        x,y=a[key].mapping(),b[key].mapping()
        equal=x.keys()==y.keys() and all(frame_data(x[k].raw())==frame_data(y[k].raw()) if k==0x16 else x[k].raw()==y[k].raw() for k in x)
        if equal:normalized.append(label)
        else:changed.append(label)
    return dict(raw,data_equal=not changed,changed_fields=sorted(changed),raw_changed_fields=raw['changed_fields'],normalized_mesh_frame_padding=sorted(normalized))


def position(vertex,mapping,bones,inverse):
    return transform(vertex['position'],1.0,vertex,mapping,bones,inverse)


def transform(vector,w,vertex,mapping,bones,inverse):
    output=[0.0,0.0,0.0]
    for index,weight in zip(vertex['indices'],vertex['weights']):
        if not 0<=index<len(mapping) or not 0<=mapping[index]<len(bones):raise FormatError('Skin palette references an unavailable model bone')
        local=point(list(vector)+[w],inverse[index]);transformed=point(local[:3]+[w],bones[mapping[index]])
        for i in range(3):output[i]+=weight*transformed[i]
    return output


def shading(data):
    """Read only the two reviewed PNUVCT plus skin declaration extensions."""
    from .meshes import PNUVCT52
    expected=PNUVCT52+struct.pack('<IHHIHH',0x80,0xa4,52,0x90,data['influences']-1,56)
    if data['declaration']!=expected or data['stride']!=56+4*(data['influences']-1):
        raise FormatError('Direction references require the reviewed PNUVCT three/four-influence declaration')
    rows=[]
    for i in range(data['vertex_count']):
        offset=i*data['stride'];normal=list(struct.unpack_from('<3f',data['vertex_bytes'],offset+12));tangent=list(struct.unpack_from('<4f',data['vertex_bytes'],offset+36))
        if not all(math.isfinite(v) for v in normal+tangent) or tangent[3] not in (-1.0,1.0):raise FormatError('Invalid source normal/tangent/handedness')
        rows.append({'normal':normal,'tangent':tangent})
    return rows


def compare_yaml(body,member):
    fields=Node(body,0,len(body)).mapping();decoded=decode(body)
    if member['20_FName']!=fields[0x14].string() or member['cid']!=str(0x410035):raise FormatError('Skin mesh YAML identity differs')
    vertices=member['62_FVertices'];fmt=vertices['20_FFormat'];raw=lambda v:base64.b64decode(''.join(v.split()),validate=True)
    if int(vertices['21_FCount'])!=decoded['vertex_count'] or int(fmt['20_FVertexSize'])!=decoded['stride'] or raw(fmt['23_FList'])!=decoded['declaration'] or raw(vertices['22_FStream'])!=decoded['vertex_bytes']:raise FormatError('Skin mesh YAML vertex data differs')
    source=member['26_FFrames'];f=fields[0x1a].mapping();matrices=f[0x18].mapping()
    if frame_data(raw(source['22_Frames']))!=frame_data(f[0x16].raw()) or raw(source['24_FBoneMatrices']['22_FList'])!=matrices[0x16].raw():raise FormatError('Skin mesh YAML frame/inverse-bind data differs')
