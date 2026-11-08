"""Conservative geometry witnesses for one observed native MeshDEV layout."""
import base64
import math
from pathlib import Path
import struct

from . import asset_yaml
from .core import FormatError,sha
from .core import Node
from .filesystem import file_hash

MAX_STREAM=32*1024*1024
# Observed FBX output: float3 position, float3 normal, float2 UV.
PNUV32=bytes.fromhex('10000000030000004000000003000c003000000002001800')
PNUVCT52=PNUV32+struct.pack('<IHHIHH',0x20,0xc4,32,0x70,4,36)


def integer(record,key,maximum=1000000):
    value=record.get(key)
    if not isinstance(value,str) or not value.isascii() or not value.isdecimal() or not 0<=int(value)<=maximum:
        raise FormatError('Invalid mesh integer field: '+key)
    return int(value)


def stream(record,key,expected):
    value=record.get(key)
    if expected>MAX_STREAM or not isinstance(value,str) or len(value)>MAX_STREAM*2:
        raise FormatError('Missing or oversized mesh stream: '+key)
    try:raw=base64.b64decode(''.join(value.split()),validate=True)
    except (ValueError,UnicodeEncodeError) as exc:raise FormatError('Invalid mesh base64 stream: '+key) from exc
    if len(raw)!=expected:raise FormatError('Mesh stream length differs from its declared count/layout: '+key)
    return raw


def triangle_signature(vertex_data,index_data):
    """Ignore storage order/cyclic corner rotations, retain winding and all attributes."""
    digests=[]
    for triangle in struct.iter_unpack('<3H',index_data):
        corners=[]
        for index in triangle:
            row=struct.unpack_from('<8f',vertex_data,index*32)
            # Signed zero is numerically identical; no other normalization is allowed.
            corners.append(struct.pack('<8f',*(0.0 if value==0 else value for value in row)))
        canonical=min(b''.join(corners[n:]+corners[:n]) for n in range(3))
        digests.append(bytes.fromhex(sha(canonical)))
    return sha(b''.join(sorted(digests)))


def shading_signature(vertex_data,index_data):
    digests=[]
    for triangle in struct.iter_unpack('<3H',index_data):
        corners=[]
        for index in triangle:
            offset=index*52
            row=struct.unpack_from('<8f',vertex_data,offset);tangent=struct.unpack_from('<4f',vertex_data,offset+36)
            encode=lambda values:struct.pack('<'+'f'*len(values),*(0.0 if value==0 else value for value in values))
            corners.append(encode(row)+vertex_data[offset+32:offset+36]+encode(tangent))
        digests.append(bytes.fromhex(sha(min(b''.join(corners[n:]+corners[:n]) for n in range(3)))))
    return sha(b''.join(sorted(digests)))


def stored_bounds(raw,vertex_data):
    values=struct.unpack('<15f',raw)
    if not all(math.isfinite(v) for v in values):raise FormatError('Mesh contains non-finite stored bounds')
    axes=[values[n:n+3] for n in (0,3,6)];half=values[9:12];center=values[12:15]
    if any(value<0 for value in half):raise FormatError('Mesh stored bounds have negative half extents')
    orthonormal=all(abs(sum(v*v for v in axis)-1)<=0.001 for axis in axes)
    orthonormal=orthonormal and all(abs(sum(a*b for a,b in zip(axes[i],axes[j])))<=0.001 for i,j in ((0,1),(0,2),(1,2)))
    outside=0;tolerance=max(1e-5,max(half)*1e-5)
    if orthonormal:
        for row in struct.iter_unpack('<8f',vertex_data):
            offset=[row[i]-center[i] for i in range(3)]
            if any(abs(sum(a*b for a,b in zip(offset,axis)))>extent+tolerance for axis,extent in zip(axes,half)):outside+=1
    return {'axes':[list(axis) for axis in axes],'half_extents':list(half),'center':list(center),
            'orthonormal':orthonormal,'outside_vertex_count':outside if orthonormal else None,'tolerance':tolerance}


def native_member(data,cid):
    """Map a known native object-body witness to the same checked geometry reader."""
    if cid!=0x410035:raise FormatError('Native geometry witness requires MeshDEV')
    fields=Node(data,0,len(data)).mapping()
    if {0x14,0x15,0x3c,0x3d,0x3e}-fields.keys():raise FormatError('Native mesh body lacks required fields')
    indices=fields[0x3d].mapping();vertices=fields[0x3e].mapping()
    if {0x14,0x15,0x16}-indices.keys() or {0x14,0x15,0x16}-vertices.keys():raise FormatError('Native mesh streams lack required fields')
    fmt=vertices[0x14].mapping()
    if {0x14,0x15,0x16,0x17}-fmt.keys():raise FormatError('Native vertex format lacks required fields')
    encoded=lambda raw:base64.b64encode(raw).decode('ascii')
    result={'cid':str(cid),'cname':'TREMeshResourceDEV','60_Version':str(fields[0x3c].uint(4)),
        '20_FName':fields[0x14].string(),'21_FUserName':fields[0x15].string(),
        '61_FIndices':{'cid':str(0x41000e),'cname':'TREIndexList','20_(char&)FFormat':str(indices[0x14].uint(1)),
                      '21_FCount':str(indices[0x15].uint(4)),'22_FStream':encoded(indices[0x16].raw())},
        '62_FVertices':{'cid':str(0x41000c),'cname':'TREVertexList','21_FCount':str(vertices[0x15].uint(4)),
            '22_FStream':encoded(vertices[0x16].raw()),'20_FFormat':{'cid':str(0x41343e),'cname':'TREVertexFormat',
                '20_FVertexSize':str(fmt[0x14].uint(4)),'21_FCount':str(fmt[0x15].uint(4)),
                '22_&FAlignmentMode':str(fmt[0x16].uint(4)),'23_FList':encoded(fmt[0x17].raw())}}}
    for key,label in ((0x40,'64_&FVerticesOBB'),(0x42,'66_&FOBB')):
        if key in fields:result[label]=encoded(fields[key].raw())
    return result


def geometry(member):
    identity={key:member.get(key) for key in ('20_FName','21_FUserName','cid','cname')}
    unknown={'status':'not_evaluated','review_required':True,'identity':identity,
             'coverage':'Only observed MeshDEV v3/PNUV32 or PNUVCT52/uint16 triangle layouts are decoded.'}
    if member.get('cid')!=str(0x410035) or member.get('cname')!='TREMeshResourceDEV' or member.get('60_Version')!='3':return unknown
    indices=member.get('61_FIndices');vertices=member.get('62_FVertices')
    if not isinstance(indices,dict) or not isinstance(vertices,dict):raise FormatError('Mesh lacks index/vertex records')
    if (indices.get('cid'),indices.get('cname'))!=(str(0x41000e),'TREIndexList'):return unknown
    if (vertices.get('cid'),vertices.get('cname'))!=(str(0x41000c),'TREVertexList'):return unknown
    fmt=vertices.get('20_FFormat')
    if not isinstance(fmt,dict) or (fmt.get('cid'),fmt.get('cname'))!=(str(0x41343e),'TREVertexFormat'):return unknown
    layout=(fmt.get('20_FVertexSize'),fmt.get('21_FCount'),fmt.get('22_&FAlignmentMode'),indices.get('20_(char&)FFormat'))
    if layout==('32','3','1','1') and stream(fmt,'23_FList',24)==PNUV32:stride=32;profile='pnuv32'
    elif layout==('52','5','1','1') and stream(fmt,'23_FList',40)==PNUVCT52:stride=52;profile='pnuvct52'
    else:return unknown
    count=integer(vertices,'21_FCount');index_count=integer(indices,'21_FCount',3000000)
    if not count or not index_count or index_count%3:raise FormatError('Mesh requires nonempty triangle indices and vertices')
    full_vertex_data=stream(vertices,'22_FStream',count*stride);index_data=stream(indices,'22_FStream',index_count*2)
    vertex_data=full_vertex_data if stride==32 else b''.join(full_vertex_data[i:i+32] for i in range(0,len(full_vertex_data),52))
    low=[math.inf]*3;high=[-math.inf]*3;uv_low=[math.inf]*2;uv_high=[-math.inf]*2
    unique=set();unique_truncated=False;nonunit=0
    for row in struct.iter_unpack('<8f',vertex_data):
        if not all(math.isfinite(value) for value in row):raise FormatError('Mesh contains non-finite vertex data')
        for i in range(3):low[i]=min(low[i],row[i]);high[i]=max(high[i],row[i])
        for i in range(2):uv_low[i]=min(uv_low[i],row[6+i]);uv_high[i]=max(uv_high[i],row[6+i])
        if not unique_truncated:
            unique.add(row[:3])
            if len(unique)>65536:unique.clear();unique_truncated=True
        if abs(sum(value*value for value in row[3:6])-1)>0.001:nonunit+=1
    maximum=0;degenerate=0
    for a,b,c in struct.iter_unpack('<3H',index_data):
        maximum=max(maximum,a,b,c)
        if maximum>=count:raise FormatError('Mesh index is outside its vertex array')
        pa=struct.unpack_from('<3f',vertex_data,a*32);pb=struct.unpack_from('<3f',vertex_data,b*32);pc=struct.unpack_from('<3f',vertex_data,c*32)
        u=[pb[i]-pa[i] for i in range(3)];v=[pc[i]-pa[i] for i in range(3)]
        if all(value==0 for value in (u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0])):degenerate+=1
    issues=[]
    if nonunit:issues.append({'issue':'non_unit_vertex_normals','count':nonunit})
    if degenerate:issues.append({'issue':'degenerate_triangles','count':degenerate})
    shading={}
    if profile=='pnuvct52':
        from . import mesh_shading
        shading=mesh_shading.analyze(full_vertex_data,index_data);issues.extend(shading['issues'])
    bounds_checks={}
    for key in ('64_&FVerticesOBB','66_&FOBB'):
        if key not in member:continue
        raw=stream(member,key,60)
        if not any(raw) and low!=high:
            issues.append({'issue':'zero_stored_bounds_for_nonzero_geometry','field':key,
                           'interpretation':'Derived bounds need post-processing review; this is not rendering validation.'})
            continue
        bounds_checks[key]=stored_bounds(raw,vertex_data)
        if not bounds_checks[key]['orthonormal']:
            issues.append({'issue':'stored_bounds_basis_not_orthonormal','field':key})
        # Vertex OBB is in stored-vertex space; main OBB may include a bone transform.
        if key=='64_&FVerticesOBB' and bounds_checks[key]['outside_vertex_count']:
            issues.append({'issue':'vertices_outside_stored_vertex_bounds','field':key,'count':bounds_checks[key]['outside_vertex_count']})
    return {'status':'geometry_decoded','identity':identity,'vertex_count':count,'index_count':index_count,
            'triangle_count':index_count//3,'maximum_index':maximum,'stride':stride,'vertex_profile':profile,
            'unique_positions':None if unique_truncated else len(unique),'unique_position_count_truncated':unique_truncated,
            'computed_bounds':{'minimum':low,'maximum':high},'uv_bounds':{'minimum':uv_low,'maximum':uv_high},
            'coordinate_space':'Stored vertex stream; node/bone transforms are not applied.',
            'vertex_stream_sha256':sha(full_vertex_data),'index_stream_sha256':sha(index_data),'shading':shading,
            'oriented_triangle_sha256':triangle_signature(vertex_data,index_data),'stored_bounds':bounds_checks,
            'oriented_shading_triangle_sha256':shading_signature(full_vertex_data,index_data) if stride==52 else None,
            'source_material_name':member.get('77_FMaterialName'),'issues':issues,'review_required':bool(issues),
            'rendering_verified':False,'coverage':'Observed position/normal/UV0 and optional packed white color/float4 tangent, with uint16 triangles. Materials, skinning, animation, collision and GPU rendering are not certified.'}


def inspect(path,locator):
    path=Path(path).resolve()
    if path.stat().st_size>asset_yaml.MAX_TEXT:raise FormatError('Mesh YAML exceeds its text bound')
    before=file_hash(path);document=asset_yaml.load(path.read_bytes());asset_yaml.members(document)
    selected=[row for row in document['21_FList'].get('1_this',[]) if row.get('20_FName','').casefold()==locator.casefold()]
    if len(selected)!=1:raise FormatError('Select exactly one existing mesh locator from the exported YAML')
    result=geometry(selected[0])
    if file_hash(path)!=before:raise FormatError('Mesh YAML changed during inspection')
    return {'format':'aow4mod-mesh-inspection/1','source':str(path),'sha256':before,'library':document['20_FName'],**result}
