"""Bounded DXIL signature inspection; no shader execution or variant inference."""
from collections import Counter
from pathlib import Path
import struct

from .core import FormatError,sha
from . import catalog

MAX_BYTES=64*1024*1024
SOURCE='https://github.com/microsoft/DirectXShaderCompiler/blob/main/include/dxc/DxilContainer/DxilContainer.h'
PROFILES={'pnuv32':{('POSITION',0):7,('NORMAL',0):7,('TEXCOORD',0):3},
          'pnuvct52':{('POSITION',0):7,('NORMAL',0):7,('TEXCOORD',0):3,('COLOR',0):15,('TANGENT',0):15}}


def signature(data):
    if len(data)<8:raise FormatError('Truncated DXIL input signature')
    count,offset=struct.unpack_from('<II',data)
    if count>256 or offset<8 or offset+count*32>len(data):raise FormatError('DXIL input signature table is out of bounds')
    result=[];seen=set();minimum=offset+count*32
    for i in range(count):
        stream,name,index,system,ctype,register,mask,reads,pad,precision=struct.unpack_from('<6IBBHI',data,offset+i*32)
        if not minimum<=name<len(data):raise FormatError('DXIL semantic name overlaps records or is outside its part')
        end=data.find(b'\0',name,min(len(data),name+129))
        if end<0 or end==name:raise FormatError('DXIL semantic name is empty, oversized or unterminated')
        try:text=data[name:end].decode('ascii')
        except UnicodeError as exc:raise FormatError('DXIL semantic name is not ASCII') from exc
        if pad or not 1<=mask<=15 or reads&~mask:raise FormatError('Invalid DXIL signature mask/padding')
        key=(stream,text.upper(),index)
        if key in seen:raise FormatError('Duplicate DXIL input semantic')
        seen.add(key);result.append({'semantic':text,'index':index,'stream':stream,'system_value':system,
            'component_type':ctype,'register':register,'mask':mask,'reads':reads,'minimum_precision':precision})
    return result


def profile_check(inputs,profile):
    if profile not in PROFILES:raise FormatError('Unknown shader vertex profile')
    issues=[]
    for value in inputs:
        if not value['reads']:continue
        semantic=value['semantic'].upper();index=value['index']
        if (semantic,value['system_value']) in (('SV_VERTEXID',6),('SV_INSTANCEID',8)):continue
        available=PROFILES[profile].get((semantic,index),0)
        if value['stream'] or value['component_type']!=3:
            issues.append({'issue':'input_stream_or_component_type_not_verified','semantic':semantic,'index':index})
        elif value['reads']&~available:
            issues.append({'issue':'missing_vertex_semantic_components','semantic':semantic,'index':index,'required_mask':value['reads'],'available_mask':available})
    return {'profile':profile,'requirements_covered':not issues,'issues':issues,
            'coverage':'Declared semantic/component masks only; native DXGI format conversion, actual vertex binding and runtime variant choice are unverified.'}


def decode(data):
    if not isinstance(data,bytes) or not 1<=len(data)<=MAX_BYTES:raise FormatError('DXIL pool must be 1 byte..64 MiB')
    rows=[];position=0
    while position<len(data):
        if len(rows)>=4096:raise FormatError('DXIL pool exceeds 4096 containers')
        if position+32>len(data) or data[position:position+4]!=b'DXBC':raise FormatError('DXIL pool is truncated or contains non-container bytes')
        version,size,count=struct.unpack_from('<III',data,position+20)
        if version!=1 or not 1<=count<=64 or not 32+4*count<=size<=len(data)-position:
            raise FormatError('Invalid DXIL container version/size/part table')
        parts={};spans=[];header=32+4*count
        for i in range(count):
            offset=struct.unpack_from('<I',data,position+32+i*4)[0]
            if offset<header or offset+8>size:raise FormatError('DXIL part overlaps its header or exceeds container')
            kind,length=struct.unpack_from('<4sI',data,position+offset)
            end=offset+8+length
            if end>size or kind in parts:raise FormatError('DXIL part is truncated or duplicated')
            if any(offset<b and a<end for a,b in spans):raise FormatError('DXIL parts overlap')
            spans.append((offset,end));parts[kind]=data[position+offset+8:position+end]
        program=parts.get(b'DXIL');shader_kind=None;shader_model=None
        if program is not None:
            if len(program)<24:raise FormatError('Truncated DXIL program header')
            ver,words,magic,bitversion,bitoffset,bitsize=struct.unpack_from('<6I',program)
            if words*4!=len(program) or magic!=0x4c495844 or bitoffset<16 or 8+bitoffset+bitsize>len(program):
                raise FormatError('DXIL program/bitcode bounds differ')
            shader_kind=ver>>16;shader_model=f'{(ver>>4)&15}.{ver&15}'
        inputs=signature(parts[b'ISG1']) if b'ISG1' in parts else None
        rows.append({'ordinal':len(rows),'offset':position,'bytes':size,'sha256':sha(data[position:position+size]),
                     'parts':[k.decode('ascii','replace') for k in parts],'shader_kind':shader_kind,'shader_model':shader_model,'inputs':inputs})
        position+=size
    return rows


def inspect(path,profile=None,limit=10,offset=0):
    catalog.check_page(limit,offset);path=Path(path).resolve()
    with path.open('rb') as stream:data=stream.read(MAX_BYTES+1)
    rows=decode(data);counts=Counter(r['shader_kind'] for r in rows);vertices=[r for r in rows if r['shader_kind']==1]
    if profile is not None:
        if profile not in PROFILES:raise FormatError('Unknown shader vertex profile')
        for row in vertices:
            row['vertex_profile_check']=profile_check(row['inputs'],profile) if row['inputs'] is not None else {'profile':profile,'requirements_covered':False,'issues':[{'issue':'input_signature_not_available'}]}
    result={'format':'aow4mod-dxil-inspection/1','source':str(path),'sha256':sha(data),'container_count':len(rows),
            'shader_kind_counts':{str(k):v for k,v in counts.items()},'vertex_program_count':len(vertices),
            'items':rows[offset:offset+limit],'limit':limit,'offset':offset,'next_offset':offset+limit if offset+limit<len(rows) else None,
            'runtime_variant_verified':False,'rendering_verified':False,'primary_source':SOURCE,
            'coverage':'DXIL container boundaries, program header bounds and ISG1 input declarations. No bitcode execution/validation, container hash authentication or runtime selection certification.'}
    if profile is not None:
        result['profile_summary']={'profile':profile,'requirements_covered':sum(r['vertex_profile_check']['requirements_covered'] for r in vertices),
                                   'review_required':sum(not r['vertex_profile_check']['requirements_covered'] for r in vertices),'zero_vertices_not_evaluated':not vertices}
    return result
