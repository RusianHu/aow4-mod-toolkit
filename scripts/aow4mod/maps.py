"""Observed GMP v1 tactical-map envelope and opaque structural comparisons."""
from pathlib import Path
import struct

from .core import FormatError,Node,sha
from .filesystem import file_hash

MAX_BYTES=256*1024*1024
MAP_CID=0x470037
HEADER_CID=0x474225


def decode(data):
    if not isinstance(data,bytes) or not 36<len(data)<=MAX_BYTES:raise FormatError('TAM exceeds bounds or lacks its GMP envelope')
    magic,size,version,header_size,cid,*reserved=struct.unpack_from('<4s8I',data)
    if magic!=b'GMP\0' or size!=36 or version!=1 or cid!=MAP_CID or any(reserved):raise FormatError('Unsupported tactical-map GMP envelope/version/class/reserved data')
    split=size+header_size
    if not 7<=header_size<=16*1024*1024 or split>=len(data):raise FormatError('TAM header object length is outside the file')
    outer=Node(data,size,split).mapping()
    if set(outer)!={0}:raise FormatError('Unsupported tactical-map header wrapper')
    header_cid,header=outer[0].typed()
    if header_cid!=HEADER_CID or not {0x400006a,0x460009}<=header.keys():raise FormatError('Tactical-map header object/scopes differ')
    body=Node(data,split,len(data));fields=body.mapping()
    if not {0x14,0x460003,MAP_CID}<=fields.keys():raise FormatError('Tactical-map body lacks required observed scopes')
    # Validate class scopes as maps, while world/terrain payloads stay opaque.
    for scope in (0x400006a,0x460009):header[scope].mapping()
    for scope in (0x460003,MAP_CID):fields[scope].mapping()
    return {'envelope':data[:36],'header':header,'map':fields,'body':body,'header_bytes':data[36:split],
            'header_size':header_size,'map_size':len(data)-split,'map_class':cid,'header_class':header_cid}


def read(path):
    path=Path(path).resolve()
    with path.open('rb') as stream:data=stream.read(MAX_BYTES+1)
    return data,decode(data)


def inspect(path):
    path=Path(path).resolve();data,decoded=read(path)
    def fields(values):return [{'field':f'0x{k:X}','bytes':len(n.raw()),'sha256':sha(n.raw())} for k,n in values.items()]
    return {'format':'aow4mod-tam-inspection/1','source':str(path),'sha256':sha(data),'bytes':len(data),
            'gmp_version':1,'map_class':f'{decoded["map_class"]:08X}','header_class':f'{decoded["header_class"]:08X}',
            'header_bytes':decoded['header_size'],'map_bytes':decoded['map_size'],'header_fields':fields(decoded['header']),
            'map_fields':fields(decoded['map']),'terrain_and_entities_opaque':True,'playability_verified':False,
            'coverage':'Observed GMP v1 envelope, bounded header wrapper and top-level tactical map/header scopes. Terrain/entity internals, dependencies and gameplay remain unverified.'}


def compare(before,after):
    left,a=read(before);right,b=read(after)
    def differences(x,y):return [f'0x{k:X}' for k in sorted(x.keys()|y.keys()) if k not in x or k not in y or x[k].raw()!=y[k].raw()]
    header=differences(a['header'],b['header']);body=differences(a['map'],b['map'])
    # Header object length is derived, but classes/version/reserved bytes are data.
    envelope_equal=a['envelope'][:12]+a['envelope'][16:]==b['envelope'][:12]+b['envelope'][16:]
    return {'all_compared_data_equal':envelope_equal and not header and not body,'byte_identical':left==right,
            'envelope_data_equal':envelope_equal,'changed_header_scopes':header,'changed_map_fields':body,
            'source_sha256':sha(left),'saved_sha256':sha(right),'coverage':'Exact observed header/map field bytes and envelope data; unknown opaque fields are never normalized away.'}
