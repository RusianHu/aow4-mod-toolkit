"""DDS block preservation plus independent CPU decoding; not a GPU certificate."""
import base64
import io
import json
import math
from pathlib import Path
import uuid

from . import asset_yaml,clb_streams,content_libraries,dds,images,material_links,native,native_assets,textures,texture_levels,texture_toolchain
from .core import FormatError,mod_file,sha
from .filesystem import copy_directory,file_hash,inventory,save_record

FORMAT='aow4mod-compressed-texture-plan/1'
NATIVE={v[0]:(k,v) for k,v in dds.FORMATS.items() if v[2]}


def fingerprint():
    root=Path(__file__).parent
    return sha(b''.join((root/n).read_bytes() for n in ('compressed_textures.py','dds.py','texture_toolchain.py','texture_levels.py','textures.py','asset_yaml.py','clb_streams.py','filesystem.py')))


def compression_inputs(image,codec,role,mips):
    pixels,origin=images.read_png(image)
    if pixels.width%4 or pixels.height%4:raise FormatError('Compression requires source dimensions divisible by four; no implicit resize')
    if codec=='bc5s':
        if role!='normal-xyz':raise FormatError('BC5_SNORM requires the explicit XYZ-normal source role')
        if any(p[3]!=255 or p[2]<128 for p in pixels.get_flattened_data()):raise FormatError('BC5 signed XY requires opaque normals in the positive-Z hemisphere')
        target=84;output_role='normal-xy-snorm'
    else:
        if codec not in ('bc1','bc3','bc7') or role not in ('color','data'):raise FormatError('Color/data compression supports BC1, BC3 or BC7')
        if codec=='bc1' and any(p[3] not in (0,255) for p in pixels.get_flattened_data()):raise FormatError('BC1 cannot preserve smooth source alpha; use BC3/BC7')
        target={'bc1':71,'bc3':77,'bc7':98}[codec]+(1 if role=='color' else 0);output_role=role
    levels=texture_levels.generate(pixels,role,mips)
    raw=dds.encode(pixels.width,pixels.height,29 if role=='color' else 28,[im.tobytes() for im in levels])
    return raw,origin,levels,target,output_role


def quality(levels,proofs,normal_mode,max_error,max_angle):
    if type(max_error) is not int or not 0<=max_error<=255 or type(max_angle) not in (int,float) or not math.isfinite(max_angle) or not 0<=max_angle<=90:
        raise FormatError('Compression quality bounds are invalid')
    if len(levels)!=len(proofs):raise FormatError('Compression changed source mip coverage')
    reports=[]
    for i,(expected,proof) in enumerate(zip(levels,proofs)):
        raw=textures.checked(proof['rgba'],proof['rgba_sha256']).read_bytes();decoded=images.pillow().frombytes('RGBA',(proof['width'],proof['height']),raw)
        if decoded.size!=expected.size:raise FormatError('Compression resized a source mip')
        if normal_mode:
            maximum=0.0
            for a,b in zip(expected.get_flattened_data(),decoded.get_flattened_data()):
                source=texture_levels.normal(a);x=b[0]/127.5-1;y=b[1]/127.5-1;z=math.sqrt(max(0,1-x*x-y*y));length=math.sqrt(x*x+y*y+z*z)
                dot=sum(v*w for v,w in zip(source,(x/length,y/length,z/length)));maximum=max(maximum,math.degrees(math.acos(max(-1,min(1,dot)))))
            if maximum>max_angle:raise FormatError('BC5 normal angular error exceeds the requested bound')
            reports.append({'level':i,'max_normal_angle_degrees':maximum,'threshold_degrees':max_angle})
        else:
            errors=[abs(a-b) for a,b in zip(expected.tobytes(),raw)];maximum=max(errors)
            if maximum>max_error:raise FormatError('Compression channel error exceeds the requested bound')
            reports.append({'level':i,'max_channel_error':maximum,'mean_absolute_error':sum(errors)/len(errors),'threshold':max_error})
    return reports


def compress_plan(mod,image,name,codec,role,tool,game,cache_dir=Path('.aow4mod/native'),timeout=180,mips='full',max_error=32,max_angle=8):
    source=mod_file(Path(mod));game=Path(game).resolve();root=source.parent;before=inventory(root)
    raw,origin,levels,target,output_role=compression_inputs(image,codec,role,mips)
    cache=native.check_cache_directory(cache_dir,game,[root,Path(image).resolve().parent]);work=cache/'texture-compression'/uuid.uuid4().hex
    (work/'input').mkdir(parents=True);reference=work/'input/source.dds';reference.write_bytes(raw);output=work/'encoded';output.mkdir()
    args=['-nogpu','-singleproc','-f',dds.FORMATS[target][1],'-m',str(len(levels)),'-dx10']
    if role=='color':args+=['-srgb']
    if codec=='bc5s':args+=['-x2bias']
    process=texture_toolchain.run(tool,[*args,'-o',output,reference],work/'encoder',timeout);encoded=output/'source.dds';record=dds.parse(encoded.read_bytes())
    if record['dxgi']!=target or [(v['width'],v['height']) for v in record['levels']]!=[im.size for im in levels]:raise FormatError('Compressor changed requested format/dimensions/mips')
    plan=make_plan(root,encoded,name,output_role,tool,game,cache,timeout)
    checks=quality(levels,plan['decoder_proofs'],codec=='bc5s',max_error,max_angle)
    if inventory(root)!=before or file_hash(image)!=origin['sha256']:raise FormatError('Compression source changed')
    plan['compression']={'image':origin,'codec':codec,'role':role,'mips':mips,'max_error':max_error,'max_angle':max_angle,
        'reference':str(reference),'reference_sha256':file_hash(reference),'encoder':process,'quality':checks}
    return plan


def document(name,record,role):
    if role not in ('color','data','normal-xy-snorm'):raise FormatError('DDS role must be color, data or normal-xy-snorm')
    if (role=='color')!=(record['color_space']==2) or (role=='normal-xy-snorm' and record['dxgi']!=84):raise FormatError('DDS format/color-space does not match its explicit role')
    if not record['block_bytes']:raise FormatError('Use the ordinary texture workflow for uncompressed sources')
    result=textures.document(name,images.pillow().new('RGBA',(1,1)));member=result['21_FList']['1_this'][0]
    member.update({'30_FWidth':str(record['width']),'31_FHeight':str(record['height']),
        '32_*(int*)&FLegacyFormat':str(0x20000|record['native_format']),'33_FLevels':str(len(record['levels'])),
        '36_&FLegacyColorSpace':str(record['color_space'])})
    member['40_FSurfaces']['1_scope']={f'{i}_E':images.object_record(0x410024,'TRECustomSurface',**{
        '20_FDescription.Width':str(row['width']),'21_FDescription.Height':str(row['height']),'25_FDescription.Depth':'1',
        '23_FDescription.Format':str(record['native_format']),'24_FDescription.Bpp':str(4 if record['block_bytes']==8 else 8),
        '22_FStream':base64.b64encode(row['data']).decode()}) for i,row in enumerate(record['levels'])}
    return result


def binary_surfaces(path,locator):
    library=content_libraries.read(path);matches=[m for m in library.members if (m['identity']['locator'] or '').casefold()==locator.casefold()]
    if len(matches)!=1 or matches[0]['identity']['type_id']!='0x0041003D':raise FormatError('Compressed texture member is missing/unsupported')
    f=matches[0]['fields'];width=f[30].uint(4);height=f[31].uint(4);count=f[33].uint(4);fmt=f[32].uint(4)&0xffff
    if fmt not in NATIVE or f[34].uint(4)!=2 or f[35].raw()!=b'\0':raise FormatError('Unsupported compressed native format/type/streaming')
    dxgi,description=NATIVE[fmt]
    if f[36].uint(4)!=(2 if description[3] else 1):raise FormatError('Compressed native color-space differs')
    if not 1<=width<=2048 or not 1<=height<=2048 or width%4 or height%4 or not 1<=count<=12:raise FormatError('Compressed native dimensions/count exceed bounds')
    sizes=dds.sizes(width,height,count);arrays=f[40].mapping()
    if set(arrays)!={1} or set(arrays[1].mapping())!=set(range(count)):raise FormatError('Compressed native mip coverage differs')
    levels=[]
    for i,node in arrays[1].mapping().items():
        cid,s=node.typed();w,h=sizes[i]
        if (cid!=0x410024 or set(s)!={20,21,22,23,24,25} or s[20].uint(4)!=w or s[21].uint(4)!=h or s[23].uint(4)!=fmt
                or int.from_bytes(s[24].raw(),'little')!=(4 if description[2]==8 else 8) or int.from_bytes(s[25].raw(),'little')!=1):raise FormatError('Compressed native mip description differs')
        data,reference=clb_streams.read(library,s[22],dds.level_bytes(w,h,dxgi),allow_uncompressed=True)
        levels.append({'level':i,'width':w,'height':h,'data':data,'sha256':sha(data),'stream_reference':reference})
    return {'locator':matches[0]['identity']['locator'],'width':width,'height':height,'dxgi':dxgi,'native_format':fmt,
            'color_space':f[36].uint(4),'name':description[1],'block_bytes':description[2],'signed':description[4],'levels':levels}


def pillow_decode(record,level):
    data=dds.one_level(record,level)
    if record['dxgi'] in (71,72,77,78):
        # Pillow's DXT decoders use the legacy header route. Only header spelling
        # changes; the original DXGI/sRGB metadata and block bytes remain bound.
        import struct
        raw=bytearray(data[:128]);struct.pack_into('<I',raw,84,int.from_bytes(b'DXT1' if record['dxgi'] in (71,72) else b'DXT5','little'));data=bytes(raw)+data[148:]
    with images.pillow().open(io.BytesIO(data)) as image:image=image.convert('RGBA');image.load()
    return image


def decoder_proofs(record,tool,folder,timeout):
    folder=Path(folder);folder.mkdir();results=[]
    for i,row in enumerate(record['levels']):
        unit=folder/f'level-{i}';(unit/'input').mkdir(parents=True);source=unit/'input/level.dds';source.write_bytes(dds.one_level(record,i))
        image=pillow_decode(record,i)
        output=unit/'decoded';output.mkdir();args=['-ft','png','-f','R8G8B8A8_UNORM_SRGB' if record['color_space']==2 else 'R8G8B8A8_UNORM','-m','1']
        if record['color_space']==2:args+=['-srgb']
        if record['signed']:args+=['-x2bias']
        process=texture_toolchain.run(tool,[*args,'-o',output,source],unit/'process',timeout);png=output/'level.png'
        with images.pillow().open(png) as decoded:decoded=decoded.convert('RGBA');decoded.load()
        channels=2 if record['dxgi'] in (83,84) else 4
        if image.size!=(row['width'],row['height']) or decoded.size!=image.size:raise FormatError('Independent DDS decoder dimensions differ')
        delta=max(abs(a-b) for n,(a,b) in enumerate(zip(image.tobytes(),decoded.tobytes())) if n%4<channels)
        if delta>1:raise FormatError('Independent DDS decoder outputs disagree beyond one code value')
        pixel_file=unit/'pillow.rgba';pixel_file.write_bytes(image.tobytes())
        results.append({'level':i,'width':image.width,'height':image.height,'channels_compared':channels,'max_decoder_delta':delta,
            'dds':str(source),'dds_sha256':file_hash(source),'png':str(png),'png_sha256':file_hash(png),
            'rgba':str(pixel_file),'rgba_sha256':file_hash(pixel_file),'process':process})
    return results


def make_plan(mod,source,name,role,tool,game,cache_dir=Path('.aow4mod/native'),timeout=180):
    acp=mod_file(Path(mod));root=acp.parent;source=Path(source).resolve();game=Path(game).resolve();files=inventory(root);name=name.upper()
    if source.stat().st_size>64*1024*1024:raise FormatError('DDS source exceeds 64 MiB')
    raw=source.read_bytes();record=dds.parse(raw);desired=document(name,record,role);images.namespace_check(name,root,game);binding=texture_toolchain.inspect(tool)
    cache=native.check_cache_directory(cache_dir,game,[root,source.parent]);work=cache/'dds-plans'/uuid.uuid4().hex;(work/'input').mkdir(parents=True)
    yaml=work/'input/texture.yaml';yaml.write_text(asset_yaml.dump(desired),encoding='utf-8');compiled=native_assets.compile_library(yaml,game,work/'compiled',cache,min(timeout,180));binary=work/'compiled'/compiled['files'][0]['name']
    read=native_assets.export_library(binary,game,work/'readback',cache,min(timeout,180))
    if not read['comparison']['all_compared_data_equal']:raise FormatError('Compressed texture changes during independent native file reload')
    actual=binary_surfaces(binary,f'[{name}]TX\\0')
    if actual['dxgi']!=record['dxgi'] or [r['data'] for r in actual['levels']]!=[r['data'] for r in record['levels']]:raise FormatError('Native compressed block bytes differ from DDS')
    proofs=decoder_proofs(record,tool,work/'decoders',timeout)
    if inventory(root)!=files or file_hash(source)!=sha(raw):raise FormatError('DDS/Mod changed during planning')
    return {'format':FORMAT,'generator_sha256':fingerprint(),'mod_source':str(acp),'source_files':files,'source':str(source),'source_sha256':sha(raw),'game':str(game),
        'namespace':name,'role':role,'tool':binding,'desired':str(yaml),'desired_sha256':file_hash(yaml),'compile':compiled,'readback':read,
        'binary':str(binary),'binary_sha256':file_hash(binary),'locator':f'[{name}]TX\\0','target':f'Libraries/{name}.clb','decoder_proofs':proofs,
        'dxgi':record['dxgi'],'level_count':len(record['levels']),'native_compressed_blocks_preserved':True,'native_pixel_decode_verified':False,'gpu_verified':False}


def validate(plan):
    if plan.get('format')!=FORMAT or plan.get('generator_sha256')!=fingerprint():raise FormatError('Compressed texture plan is unsupported/outdated')
    root=Path(plan['mod_source']).parent;game=Path(plan['game'])
    if inventory(root)!=plan['source_files'] or file_hash(plan['source'])!=plan['source_sha256']:raise FormatError('DDS/Mod source changed')
    if texture_toolchain.inspect(plan['tool']['file'])!=plan['tool']:raise FormatError('DDS toolchain changed')
    record=dds.parse(Path(plan['source']).read_bytes());desired=document(plan['namespace'],record,plan['role'])
    if asset_yaml.load(textures.checked(plan['desired'],plan['desired_sha256']).read_bytes())!=desired:raise FormatError('DDS desired document differs')
    binary=textures.checked(plan['binary'],plan['binary_sha256']);actual=binary_surfaces(binary,plan['locator'])
    if (plan['dxgi']!=record['dxgi'] or plan['level_count']!=len(record['levels']) or actual['dxgi']!=record['dxgi']
            or [r['data'] for r in actual['levels']]!=[r['data'] for r in record['levels']]):raise FormatError('DDS compressed blocks/format/mips differ')
    if plan['target']!=f"Libraries/{plan['namespace']}.clb" or plan['locator']!=f"[{plan['namespace']}]TX\\0":raise FormatError('DDS target identity differs')
    binding=native.public_binding(native.profile_for(game))
    for report in (plan['compile'],plan['readback']):
        if report['binding']!=binding:raise FormatError('DDS native backend changed')
        for item in report['files']:textures.checked(Path(report['output'])/item['name'],item['sha256'])
        if asset_yaml.load((Path(report['output'])/report['files'][1]['name']).read_bytes())!=desired:raise FormatError('DDS native YAML differs')
    c=plan['compile'];r=plan['readback']
    if c['source_sha256']!=plan['desired_sha256'] or c['files'][0]['sha256']!=plan['binary_sha256'] or r['source_sha256']!=plan['binary_sha256']:raise FormatError('DDS native witness binding differs')
    if not content_libraries.compare(binary,Path(r['output'])/r['files'][0]['name'])['all_compared_data_equal']:raise FormatError('DDS independent binary differs')
    if len(plan['decoder_proofs'])!=len(record['levels']):raise FormatError('DDS decoder level coverage differs')
    for i,(proof,row) in enumerate(zip(plan['decoder_proofs'],record['levels'])):
        if proof['level']!=i or textures.checked(proof['dds'],proof['dds_sha256']).read_bytes()!=dds.one_level(record,i):raise FormatError('DDS decoder input differs')
        image=pillow_decode(record,i)
        with images.pillow().open(textures.checked(proof['png'],proof['png_sha256'])) as other:other=other.convert('RGBA');other.load()
        channels=2 if record['dxgi'] in (83,84) else 4
        delta=max(abs(a-b) for n,(a,b) in enumerate(zip(image.tobytes(),other.tobytes())) if n%4<channels)
        if (image.size!=(row['width'],row['height']) or other.size!=image.size or proof['channels_compared']!=channels or delta>1
                or proof['max_decoder_delta']!=delta or textures.checked(proof['rgba'],proof['rgba_sha256']).read_bytes()!=image.tobytes()):raise FormatError('DDS decoder pixels/quality differ')
    if plan.get('native_compressed_blocks_preserved') is not True or plan.get('native_pixel_decode_verified') is not False or plan.get('gpu_verified') is not False:raise FormatError('DDS validation scope was changed')
    compression=plan.get('compression')
    if compression:
        raw,origin,levels,target,role=compression_inputs(compression['image']['file'],compression['codec'],compression['role'],compression['mips'])
        if origin!=compression['image'] or textures.checked(compression['reference'],compression['reference_sha256']).read_bytes()!=raw or target!=record['dxgi'] or role!=plan['role']:
            raise FormatError('Compression source/reference/format policy differs')
        if quality(levels,plan['decoder_proofs'],compression['codec']=='bc5s',compression['max_error'],compression['max_angle'])!=compression['quality']:
            raise FormatError('Compression quality report differs')
    images.namespace_check(plan['namespace'],root,game);return binary


def apply_plan(plan,output):
    binary=validate(plan);root=Path(plan['mod_source']).parent
    with copy_directory(root,output,[plan['game'],binary.parent,plan['source']]) as (stage,files):
        if files!=plan['source_files']:raise FormatError('DDS Mod changed before staging')
        target=stage/plan['target'];target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(binary.read_bytes())
        directory=stage/'SourceImages'/plan['namespace'];directory.mkdir(parents=True);source=Path(plan['source']);(directory/source.name).write_bytes(source.read_bytes())
        if file_hash(target)!=plan['binary_sha256'] or file_hash(directory/source.name)!=plan['source_sha256']:raise FormatError('DDS changed while staging')
        receipt={'format':'aow4mod-compressed-texture-receipt/1','target':plan['target'],'locator':plan['locator'],'dxgi':plan['dxgi'],'role':plan['role'],
            'levels':plan['level_count'],'sha256':plan['binary_sha256'],'native_blocks_preserved':True,'native_pixel_decode_verified':False,'gpu_verified':False}
        save_record(directory/'.aow4mod-dds.json',receipt)
    return receipt
