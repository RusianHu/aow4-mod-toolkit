"""Deterministic area mip filtering for color, numeric data and XYZ normals."""
import math

from .core import FormatError
from . import images

ROLES={'color':2,'data':1,'normal-xyz':1}


def dimensions(width,height,mips):
    if mips not in ('none','full') or type(width) is not int or type(height) is not int or not 1<=width<=1024 or not 1<=height<=1024:
        raise FormatError('Texture dimensions/mip mode are outside the reviewed scope')
    result=[(width,height)]
    while mips=='full' and (width>1 or height>1):
        width=max(1,width//2);height=max(1,height//2);result.append((width,height))
    return result


def quantize(value):return max(0,min(255,int(math.floor(value*255+0.5))))


def linear(value):return value/12.92 if value<=0.04045 else ((value+0.055)/1.055)**2.4


def srgb(value):return value*12.92 if value<=0.0031308 else 1.055*value**(1/2.4)-0.055


def normal(pixel):
    vector=[v/127.5-1 for v in pixel[:3]];length=math.sqrt(sum(v*v for v in vector))
    if not 0.5<=length<=1.5:raise FormatError('XYZ normal contains a degenerate/out-of-range encoded vector')
    return [v/length for v in vector]


def contributors(source_size,target_size):
    """Integer overlaps retain the whole footprint, including odd-size edges."""
    result=[]
    for dst in range(target_size):
        start=dst*source_size;end=(dst+1)*source_size;row=[]
        for src in range(start//target_size,min(source_size,(end+target_size-1)//target_size)):
            weight=min(end,(src+1)*target_size)-max(start,src*target_size)
            if weight>0:row.append((src,weight))
        if sum(w for _,w in row)!=source_size:raise FormatError('Mip footprint does not cover its exact source area')
        result.append(row)
    return result


def downsample(image,role,width,height):
    if role not in ROLES:raise FormatError('Unsupported texture role')
    data=list(image.get_flattened_data());sw,sh=image.size;xs=contributors(sw,width);ys=contributors(sh,height);total=sw*sh;out=[]
    for yy in ys:
        for xx in xs:
            sums=[0.0]*4
            for sy,wy in yy:
                for sx,wx in xx:
                    pixel=data[sy*sw+sx];weight=wx*wy;alpha=pixel[3]/255
                    if role=='color':
                        for k in range(3):sums[k]+=linear(pixel[k]/255)*alpha*weight
                    elif role=='normal-xyz':
                        vector=normal(pixel)
                        for k in range(3):sums[k]+=vector[k]*weight
                    else:
                        for k in range(3):sums[k]+=pixel[k]/255*weight
                    sums[3]+=alpha*weight
            if role=='color':rgb=[srgb(v/sums[3]) if sums[3] else 0.0 for v in sums[:3]]
            elif role=='normal-xyz':
                length=math.sqrt(sum(v*v for v in sums[:3]))
                if length<=total*1e-8:raise FormatError('Normal mip footprint cancels to zero; choose an authored normal instead')
                rgb=[v/length*0.5+0.5 for v in sums[:3]]
            else:rgb=[v/total for v in sums[:3]]
            out.append(tuple(quantize(v) for v in [*rgb,sums[3]/total]))
    result=images.pillow().new('RGBA',(width,height));result.putdata(out);return result


def generate(image,role='color',mips='none'):
    if role not in ROLES:raise FormatError('Texture role must be color, data or normal-xyz')
    sizes=dimensions(*image.size,mips)
    if role=='normal-xyz':
        for pixel in image.get_flattened_data():normal(pixel)
    result=[image]
    for width,height in sizes[1:]:result.append(downsample(result[-1],role,width,height))
    return result
