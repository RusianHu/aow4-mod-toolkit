"""Reviewed tactical height scalars and eight-channel terrain weight storage."""
from pathlib import Path
import math
import struct

from . import catalog,editing,hex_positions,maps
from .core import FormatError,sha

PAINT_SCOPE=0x40112a3
PIXEL_SCOPE=0x40112a9
RESOLUTION_SCOPE=0x40112ad
MAX_WEIGHT_BYTES=64*1024*1024


def layout(data,level=0):
    grids=hex_positions.grids(data)
    if type(level) is not int or level not in grids:raise FormatError('Select one declared tactical grid level')
    grid=grids[level];parsed=maps.decode(data);world=parsed['map'][0x14];_,wf=world.typed();base=wf[0x401107c]
    array=base.mapping()[3];node=array.array()[grid['ordinal']];_,gf=node.typed()
    if 0x471515 not in gf or 0 not in gf[0x471515].mapping():raise FormatError('Tactical terrain paint data is absent')
    paint=gf[0x471515].mapping()[0]
    if set(paint.mapping())!={PAINT_SCOPE}:raise FormatError('Unknown terrain paint wrapper')
    pf=paint.mapping()[PAINT_SCOPE].mapping()
    if 5 in pf and pf[5].uint(4)!=8:raise FormatError('Terrain painting requires exactly eight declared types')
    if 9 not in pf:raise FormatError('Terrain pixel data is absent')
    pixels=pf[9]
    if set(pixels.mapping())!={PIXEL_SCOPE}:raise FormatError('Unknown terrain pixel-data wrapper')
    values=pixels.mapping()[PIXEL_SCOPE].mapping()
    if not {0,1,2,11,12}<=values.keys():raise FormatError('Terrain weight fields are incomplete')
    if (values[0].uint(4),values[1].uint(4))!=(grid['columns'],grid['rows']):raise FormatError('Terrain pixel grid dimensions differ')
    resolution=values[2]
    if set(resolution.mapping())!={RESOLUTION_SCOPE}:raise FormatError('Unknown terrain weight resolution wrapper')
    rf=resolution.mapping()[RESOLUTION_SCOPE].mapping()
    if not {0,1,2,3}<=rf.keys() or set(rf)-{0,1,2,3,4}:raise FormatError('Unknown terrain resolution fields')
    width,height=rf[0].uint(4),rf[1].uint(4)
    if not 1<=width<=4096 or not 1<=height<=4096 or width*height*4>MAX_WEIGHT_BYTES:raise FormatError('Terrain weight dimensions exceed bounds')
    unit_x,unit_z=(struct.unpack('<f',rf[k].raw())[0] for k in (2,3));border=rf[4].uint(4) if 4 in rf else 0
    if not all(math.isfinite(v) and v>0 for v in (unit_x,unit_z)) or border>min(width,height):raise FormatError('Terrain resolution units/border require review')
    if any(len(values[k].raw())!=width*height*4 for k in (11,12)):raise FormatError('Terrain weight buffer size differs')
    return {'parsed':parsed,'world':world,'world_base':base,'grids':array,'grid_node':node,'grid_fields':gf,'grid':grid,
            'paint':paint,'paint_fields':pf,'pixels':pixels,'pixel_fields':values,
            'resolution':{'width':width,'height':height,'unit_x':unit_x,'unit_z':unit_z,'border':border}}


def sample(info,x,y):
    r=info['resolution']
    if type(x) is not int or type(y) is not int or not 0<=x<r['width'] or not 0<=y<r['height']:raise FormatError('Terrain weight pixel is outside its texture')
    offset=(y*r['width']+x)*4
    a,b=info['pixel_fields'][11],info['pixel_fields'][12]
    return list(a.data[a.start+offset:a.start+offset+4]+b.data[b.start+offset:b.start+offset+4])


def derive(data,level,rectangle,weights):
    info=layout(data,level);r=info['resolution']
    if not isinstance(rectangle,dict) or set(rectangle)!={'x','y','width','height'}:raise FormatError('Select an exact terrain pixel rectangle')
    x,y,w,h=(rectangle[k] for k in ('x','y','width','height'))
    if any(type(v) is not int for v in (x,y,w,h)) or not 0<=x<r['width'] or not 0<=y<r['height'] or not 1<=w<=r['width']-x or not 1<=h<=r['height']-y:
        raise FormatError('Terrain paint rectangle is outside its texture')
    if not isinstance(weights,list) or len(weights)!=8 or any(type(v) is not int or not 0<=v<=255 for v in weights) or sum(weights)!=255:
        raise FormatError('Terrain weights require eight uint8 channels summing to 255')
    buffers={k:bytearray(info['pixel_fields'][k].raw()) for k in (11,12)};changed=0
    for row in range(y,y+h):
        for col in range(x,x+w):
            at=(row*r['width']+col)*4
            changed+=buffers[11][at:at+4]+buffers[12][at:at+4]!=bytes(weights)
        for key,start in ((11,0),(12,4)):
            at=(row*r['width']+x)*4;buffers[key][at:at+w*4]=bytes(weights[start:start+4])*w
    if not changed:raise FormatError('Terrain weight edit makes no change')
    pixels=editing.rewrite_map(info['pixels'],{PIXEL_SCOPE:editing.rewrite_map(info['pixels'].mapping()[PIXEL_SCOPE],{k:bytes(v) for k,v in buffers.items()})})
    paint=editing.rewrite_map(info['paint'],{PAINT_SCOPE:editing.rewrite_map(info['paint'].mapping()[PAINT_SCOPE],{9:pixels})})
    node=editing.rewrite_typed(info['grid_node'],{0x471515:editing.rewrite_map(info['grid_fields'][0x471515],{0:paint})})
    grids=editing.rewrite_map(info['grids'],{1:editing.rewrite_map(info['grids'].mapping()[1],{info['grid']['ordinal']:node})})
    world=editing.rewrite_typed(info['world'],{0x401107c:editing.rewrite_map(info['world_base'],{3:grids})})
    parsed=info['parsed'];desired=parsed['envelope']+parsed['header_bytes']+editing.rewrite_map(parsed['body'],{0x14:world})
    layout(desired,level)
    return desired,info['resolution'],changed


def inspect(path,level=0,x=0,y=0,limit=20,offset=0):
    catalog.check_page(limit,offset);path=Path(path).resolve();data,_=maps.read(path);info=layout(data,level);cells=[]
    for index,cell in info['grid']['cells'].items():
        base=cell.mapping()[0x401107e].mapping();tactical=cell.mapping()[0x471519].mapping()
        height=struct.unpack('<f',base[2].raw())[0] if 2 in base else 0.0
        stand=struct.unpack('<f',base[9].raw())[0] if 9 in base else 0.0
        if not math.isfinite(height) or not math.isfinite(stand):raise FormatError('Nonfinite tactical height data')
        cells.append({'index':index,'x':index%info['grid']['columns'],'y':index//info['grid']['columns'],'level':level,
                      'height':height,'stand_height':stand,'terrain_height_code':tactical[6].uint(4) if 6 in tactical else 0})
    return {'format':'aow4mod-map-terrain/1','source':str(path),'sha256':sha(data),'level':level,'weight_resolution':info['resolution'],
            'weight_buffers_sha256':{str(k):sha(info['pixel_fields'][k].raw()) for k in (11,12)},'pixel':{'x':x,'y':y,'weights':sample(info,x,y)},
            'height_range':[min(c['height'] for c in cells),max(c['height'] for c in cells)],
            **catalog.page(cells[offset:offset+limit],len(cells),limit,offset),'playability_verified':False,
            'coverage':'Stored geometric/stand heights, separate terrain-height code and eight texture-weight channels. No height sculpting, material-name mapping, rendering or gameplay certification.'}
