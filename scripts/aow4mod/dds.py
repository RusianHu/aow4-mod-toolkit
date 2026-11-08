"""Bounded typed 2D DDS/DX10 parsing; no guessed layout or trailing payload."""
import struct

from .core import FormatError,sha

# DXGI format -> native format, name, block bytes, sRGB, signed.
FORMATS={71:(131,'BC1_UNORM',8,False,False),72:(132,'BC1_UNORM_SRGB',8,True,False),
         77:(151,'BC3_UNORM',16,False,False),78:(152,'BC3_UNORM_SRGB',16,True,False),
         83:(171,'BC5_UNORM',16,False,False),84:(172,'BC5_SNORM',16,False,True),
         98:(191,'BC7_UNORM',16,False,False),99:(192,'BC7_UNORM_SRGB',16,True,False),
         28:(83,'R8G8B8A8_UNORM',0,False,False),29:(83,'R8G8B8A8_UNORM_SRGB',0,True,False)}


def sizes(width,height,count):
    result=[]
    while True:
        result.append((width,height))
        if len(result)==count:break
        if width==height==1:raise FormatError('DDS mip count exceeds the complete chain')
        width=max(1,width//2);height=max(1,height//2)
    return result


def level_bytes(width,height,dxgi):
    block=FORMATS[dxgi][2]
    return max(1,(width+3)//4)*max(1,(height+3)//4)*block if block else width*height*4


def parse(data,allow_partial_base=False):
    if not isinstance(data,bytes) or not 128<=len(data)<=64*1024*1024 or data[:4]!=b'DDS ':raise FormatError('DDS is truncated, oversized or has invalid magic')
    header=struct.unpack_from('<31I',data,4);size,flags,height,width,pitch,depth,mips=header[:7]
    if size!=124 or header[18]!=32 or flags&0x1007!=0x1007:raise FormatError('DDS header/required flags differ')
    if not 1<=width<=2048 or not 1<=height<=2048 or depth not in (0,1) or flags&0x800000:raise FormatError('DDS supports only bounded 2D textures')
    caps,caps2,caps3,caps4,reserved2=header[26:31]
    if not caps&0x1000 or caps2 or caps3 or caps4 or reserved2:raise FormatError('DDS cube/volume/extra caps are unsupported')
    pf_flags,fourcc=header[19:21];offset=128;alpha=0
    if pf_flags!=4:raise FormatError('DDS requires a reviewed FourCC/DX10 pixel format')
    if fourcc==int.from_bytes(b'DX10','little'):
        if len(data)<148:raise FormatError('DDS DX10 extension is truncated')
        dxgi,dimension,misc,array,alpha=struct.unpack_from('<5I',data,128);offset=148
        if dimension!=3 or misc or array!=1 or alpha not in (0,1,3):raise FormatError('DDS arrays/cubes/premultiplied/custom alpha are unsupported')
    else:
        legacy={int.from_bytes(b'DXT1','little'):71,int.from_bytes(b'DXT5','little'):77,
                int.from_bytes(b'ATI2','little'):83,int.from_bytes(b'BC5U','little'):83,int.from_bytes(b'BC5S','little'):84}
        if fourcc not in legacy:raise FormatError('Unsupported DDS FourCC')
        dxgi=legacy[fourcc]
    if dxgi not in FORMATS:raise FormatError('DDS format is typeless or outside the reviewed codec set')
    if FORMATS[dxgi][2] and not allow_partial_base and (width%4 or height%4):raise FormatError('Compressed DDS base dimensions must be multiples of four')
    count=mips if flags&0x20000 else 1
    if not 1<=count<=12 or (count>1 and caps&0x400008!=0x400008):raise FormatError('DDS mip flags/count/caps differ')
    dimensions=sizes(width,height,count);expected_pitch=level_bytes(width,height,dxgi) if FORMATS[dxgi][2] else width*4
    if pitch!=expected_pitch:raise FormatError('DDS declared pitch/linear size differs from its format')
    levels=[]
    for level,(w,h) in enumerate(dimensions):
        size=level_bytes(w,h,dxgi)
        if offset+size>len(data):raise FormatError('DDS mip payload is truncated')
        raw=data[offset:offset+size];levels.append({'level':level,'width':w,'height':h,'data':raw,'sha256':sha(raw)});offset+=size
    if offset!=len(data):raise FormatError('DDS contains trailing/unassigned payload')
    native,name,block,srgb,signed=FORMATS[dxgi]
    return {'width':width,'height':height,'dxgi':dxgi,'native_format':native,'name':name,'block_bytes':block,
            'color_space':2 if srgb else 1,'signed':signed,'alpha_mode':alpha,'levels':levels}


def encode(width,height,dxgi,levels,alpha_mode=1):
    if dxgi not in FORMATS or not levels:raise FormatError('DDS writer requires a reviewed format and levels')
    dimensions=sizes(width,height,len(levels));block=FORMATS[dxgi][2]
    if any(len(data)!=level_bytes(w,h,dxgi) for data,(w,h) in zip(levels,dimensions)):raise FormatError('DDS level byte lengths differ')
    header=[0]*31;header[:7]=[124,0x1007|(0x80000 if block else 8)|(0x20000 if len(levels)>1 else 0),height,width,
                           level_bytes(width,height,dxgi) if block else width*4,1,len(levels)]
    header[18:26]=[32,4,int.from_bytes(b'DX10','little'),0,0,0,0,0]
    header[26]=0x1000|(0x400008 if len(levels)>1 else 0)
    return b'DDS '+struct.pack('<31I',*header)+struct.pack('<5I',dxgi,3,0,1,alpha_mode)+b''.join(levels)


def one_level(record,level):
    row=record['levels'][level]
    return encode(row['width'],row['height'],record['dxgi'],[row['data']],record['alpha_mode'])
