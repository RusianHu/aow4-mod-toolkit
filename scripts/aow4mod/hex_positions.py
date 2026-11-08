"""Observed TGCHexEntity 12/12/8 position codec and tactical-grid bounds."""
from . import maps
from .core import FormatError

CODEC='tgchexposition-u32-12-12-8'


def decode(value):
    if type(value) is not int or not 0<=value<=0xffffffff:raise FormatError('Packed hex position must be uint32')
    x,y,level=value&0xfff,(value>>12)&0xfff,value>>24
    return {'x':-1 if x==0xfff else x,'y':-1 if y==0xfff else y,'level':-1 if level==0xff else level}


def encode(point):
    if not isinstance(point,dict) or set(point)!={'x','y','level'}:raise FormatError('Hex position requires exact x/y/level coordinates')
    for key,maximum in (('x',4094),('y',4094),('level',254)):
        if type(point[key]) is not int or not -1<=point[key]<=maximum:raise FormatError('Hex position coordinate exceeds the reviewed codec range')
    return (point['x']&0xfff)|((point['y']&0xfff)<<12)|((point['level']&0xff)<<24)


def grids(data):
    parsed=maps.decode(data);cid,world=parsed['map'][0x14].typed()
    if cid!=0x471516 or 0x401107c not in world:raise FormatError('Unsupported tactical grid world')
    base=world[0x401107c].mapping()
    if 3 not in base:raise FormatError('Tactical world grid list is absent')
    nodes=base[3].array()
    if not 1<=len(nodes)<=64:raise FormatError('Tactical grid count exceeds supported bounds')
    result={}
    for ordinal,node in nodes.items():
        cid,fields=node.typed()
        if cid!=0x471515 or 0x401107d not in fields:raise FormatError('Unsupported tactical hex-map class')
        values=fields[0x401107d].mapping()
        if not {1,2,7}<=values.keys():raise FormatError('Tactical grid dimensions/level are absent')
        rows,columns,level=(values[k].uint(4) for k in (1,2,7));count=rows*columns
        if not 1<=rows<=4095 or not 1<=columns<=4095 or not 0<=level<=254 or count>1000000:
            raise FormatError('Tactical grid dimensions/level exceed reviewed bounds')
        cells={k-100:v for k,v in values.items() if k>=100}
        if set(cells)!=set(range(count)) or level in result:raise FormatError('Tactical grid cells are incomplete or level is duplicated')
        result[level]={'ordinal':ordinal,'level':level,'rows':rows,'columns':columns,'cell_count':count,'cells':cells}
    return result


def checked(point,layout):
    encode(point)
    grid=layout.get(point['level'])
    if grid is None or not 0<=point['x']<grid['columns'] or not 0<=point['y']<grid['rows']:
        raise FormatError('Hex position is outside the declared tactical grid')
    return grid
