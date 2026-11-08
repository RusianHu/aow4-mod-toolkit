"""Bounded decoding of the observed CLB v8 compressed data-reference format."""
import struct

from .core import FormatError

MAX_BYTES=64*1024*1024
LZ4_SOURCE='https://github.com/lz4/lz4/blob/dev/doc/lz4_Block_format.md'


def lz4_block(data,size):
    if not isinstance(data,bytes) or not 1<=len(data)<=MAX_BYTES or type(size) is not int or not 0<=size<=MAX_BYTES:
        raise FormatError('LZ4 input/output exceeds shader stream bounds')
    out=bytearray();position=0;last_literals=0;last_match_start=None
    def length(value):
        nonlocal position
        if value==15:
            while True:
                if position>=len(data):raise FormatError('Truncated LZ4 length extension')
                extra=data[position];position+=1;value+=extra
                if value>MAX_BYTES:raise FormatError('LZ4 length exceeds output bounds')
                if extra!=255:break
        return value
    while position<len(data):
        token=data[position];position+=1;count=length(token>>4)
        if count>len(data)-position or count>size-len(out):raise FormatError('LZ4 literals exceed input/output bounds')
        out.extend(data[position:position+count]);position+=count;last_literals=count
        if position==len(data):break
        if position+2>len(data):raise FormatError('Truncated LZ4 match offset')
        distance=struct.unpack_from('<H',data,position)[0];position+=2
        if not 1<=distance<=len(out):raise FormatError('LZ4 match precedes output or has zero offset')
        count=length(token&15)+4
        if count>size-len(out):raise FormatError('LZ4 match exceeds output bounds')
        last_match_start=len(out);pattern=out[-distance:]
        out.extend((pattern*((count+distance-1)//distance))[:count])
    if position!=len(data) or len(out)!=size:raise FormatError('LZ4 decoded size differs from reference')
    if size>=5 and last_literals<5:raise FormatError('LZ4 final literal sequence is too short')
    if last_match_start is not None and size-last_match_start<12:raise FormatError('LZ4 final match violates block end bounds')
    return bytes(out)


def read(library,node,expected_size,allow_uncompressed=False):
    raw=node.raw()
    if len(raw)!=24 or type(expected_size) is not int or not 1<=expected_size<=MAX_BYTES:
        raise FormatError('Unsupported CLB shader stream reference size')
    offset,mode,size,packed,reserved1,reserved2=struct.unpack('<6I',raw)
    if (mode!=2 and not (allow_uncompressed and mode==0)) or reserved1 or reserved2:raise FormatError('Unreviewed CLB shader compression/reference flags')
    if size!=expected_size or not 1<=packed<=MAX_BYTES or offset>library.data_size-packed:
        raise FormatError('CLB shader stream reference exceeds its data section')
    with library.path.open('rb') as source:
        source.seek(library.data_offset+offset);data=source.read(packed)
    if len(data)!=packed:raise FormatError('CLB shader stream became truncated')
    if mode==0:
        if packed!=size:raise FormatError('Uncompressed CLB stream size differs')
        decoded=data
    else:decoded=lz4_block(data,size)
    return decoded,{'offset':offset,'mode':mode,'decoded_bytes':size,'packed_bytes':packed}
