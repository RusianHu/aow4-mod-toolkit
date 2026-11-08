"""Standalone RGBA8 PNG texture authoring with native CPU pixel witnesses."""
import json
from pathlib import Path
import re
import uuid

from . import asset_yaml,clb_streams,content_libraries,images,native,native_assets,texture_levels
from .core import FormatError,mod_file,sha
from .filesystem import copy_directory,file_hash,inventory,save_record

FORMAT='aow4mod-texture-plan/1'


def fingerprint():
    root=Path(__file__).parent
    return sha(b''.join((root/n).read_bytes() for n in ('textures.py','texture_levels.py','clb_streams.py','images.py','asset_yaml.py','filesystem.py')))


def document(name,image,role='color',mips='none',levels=None):
    if not isinstance(name,str) or not re.fullmatch('[A-Z][A-Z0-9_]{0,29}',name):raise FormatError('Texture namespace requires 1..30 uppercase ASCII letters/digits/underscores')
    levels=levels if levels is not None else texture_levels.generate(image,role,mips)
    obj=images.object_record;dev=obj(0x402000,'TEResourceDevInfo',**{'4202496_scope':{}})
    texture=obj(0x41003d,'TRETextureResourceDEV',**{'19_FResourceDevInfo':dev,'20_FName':f'[{name}]TX\\0','21_FUserName':name,
        '30_FWidth':str(image.width),'31_FHeight':str(image.height),'32_*(int*)&FLegacyFormat':str(0x20053),'33_FLevels':str(len(levels)),
        '34_&FLegacyType':'2','35_FStreamingEnabled':'false','36_&FLegacyColorSpace':str(texture_levels.ROLES[role]),'37_&FQualityModifier':'1',
        '40_FSurfaces':obj(0x41014e,'TRETextureSurfaceList',**{'1_scope':{f'{i}_E':images.surface(im.width,im.height,images.padded_rgba(im)) for i,im in enumerate(levels)}})})
    result={'YAMLRPKVersion':'1','19_*(int*)&FVersionNumber':'8','20_FName':name,'22_FDependencies':['SYSTEM.CLB'],
        '21_FList':obj(0x400081,'TEResourceList',**{'1_this':[texture]}),
        '24_FFeatures':obj(0x4000ab,'TELibraryFeatureContainer',**{'1_this':[obj(0x412243,'TRETextureLibraryFeature',**{'10_FUserName':'Default','11_FCategoryID':'0'})]}),
        '28_FResourceSeparatorList':obj(0x402801,'TEResourceSeparatorList')}
    asset_yaml.validate_tree(result);return result


def checked(path,digest):
    p=Path(path)
    if file_hash(p)!=digest:raise FormatError('Texture evidence changed: '+str(p))
    return p


def binary_surfaces(path,locator):
    library=content_libraries.read(path);found=[m for m in library.members if (m['identity']['locator'] or '').casefold()==locator.casefold()]
    if len(found)!=1 or found[0]['identity']['type_id']!='0x0041003D':raise FormatError('Select a standalone texture DEV member')
    fields=found[0]['fields'];width=fields[30].uint(4);height=fields[31].uint(4);count=fields[33].uint(4);color_space=fields[36].uint(4)
    sizes=texture_levels.dimensions(width,height,'full')
    if (fields[32].uint(4)!=0x20053 or count not in (1,len(sizes)) or fields[34].uint(4)!=2
            or fields[35].raw()!=b'\0' or color_space not in (1,2)):raise FormatError('Texture format/mips/type/color-space is outside the reviewed RGBA8 preset')
    arrays=fields[40].mapping()
    if set(arrays)!={1} or set(arrays[1].mapping())!=set(range(count)):raise FormatError('Texture surface coverage differs from its mip count')
    result=[]
    for i,node in arrays[1].mapping().items():
        width,height=sizes[i];cid,surface=node.typed()
        if cid!=0x410024 or set(surface)!={20,21,22,23,24,25}:raise FormatError('Texture surface layout differs')
        if (surface[20].uint(4)!=width or surface[21].uint(4)!=height or surface[23].uint(4)!=83
                or int.from_bytes(surface[24].raw(),'little')!=32 or int.from_bytes(surface[25].raw(),'little')!=1):raise FormatError('Texture surface dimensions/format differ')
        raw,reference=clb_streams.read(library,surface[22],((width*4+7)//8*8)*height,allow_uncompressed=True);images.surface(width,height,raw)
        result.append({'level':i,'width':width,'height':height,'padded_pixels':raw,'stream_reference':reference})
    return {'locator':found[0]['identity']['locator'],'color_space':color_space,'levels':result}


def binary_surface(path,locator):
    result=binary_surfaces(path,locator)
    return {'locator':result['locator'],'color_space':result['color_space'],'level_count':len(result['levels']),**result['levels'][0]}


def make_plan(mod,image,name,game,cache_dir=Path('.aow4mod/native'),timeout=60,role='color',mips='none'):
    source=mod_file(Path(mod));root=source.parent;game=Path(game).resolve();files=inventory(root)
    name=name.upper();pixels,origin=images.read_png(image);images.namespace_check(name,root,game);levels=texture_levels.generate(pixels,role,mips);desired=document(name,pixels,role,mips,levels)
    cache=native.check_cache_directory(cache_dir,game,[root,Path(image).resolve().parent]);work=cache/'texture-plans'/uuid.uuid4().hex
    (work/'input').mkdir(parents=True);yaml=work/'input/texture.yaml';yaml.write_text(asset_yaml.dump(desired),encoding='utf-8')
    compile=native_assets.compile_library(yaml,game,work/'compiled',cache,timeout);binary=work/'compiled'/compile['files'][0]['name']
    read=native_assets.export_library(binary,game,work/'readback',cache,timeout)
    actual=asset_yaml.load((work/'readback'/read['files'][1]['name']).read_bytes())
    if actual!=desired or not read['comparison']['all_compared_data_equal']:raise FormatError('Standalone texture file reload changed data')
    binary_levels=binary_surfaces(binary,f'[{name}]TX\\0')['levels'];proofs=[]
    for i,im in enumerate(levels):
        if binary_levels[i]['padded_pixels']!=images.padded_rgba(im):raise FormatError('Compiled texture binary pixels differ at mip '+str(i))
        proof=images.native_png(actual['21_FList']['1_this'][0]['40_FSurfaces']['1_scope'][f'{i}_E'],game,cache,work/f'pixels-{i}',timeout)
        if proof['rgba_sha256']!=sha(im.tobytes()):raise FormatError('Compiled standalone texture pixels differ at mip '+str(i))
        proofs.append(proof)
    if inventory(root)!=files or file_hash(image)!=origin['sha256']:raise FormatError('Texture source changed during planning')
    return {'format':FORMAT,'generator_sha256':fingerprint(),'source':str(source),'source_files':files,'game':str(game),
        'namespace':name,'image':origin,'desired_file':str(yaml),'desired_sha256':file_hash(yaml),'compile':compile,'readback':read,
        'binary':str(binary),'binary_sha256':file_hash(binary),'native_pixels':proofs[0],'native_mip_pixels':proofs,'target':f'Libraries/{name}.clb',
        'role':role,'mips':mips,'level_count':len(levels),'color_space':texture_levels.ROLES[role],
        'locator':f'[{name}]TX\\0','coverage':'RGBA8 2D texture, color/data/XYZ-normal-aware area mip chain and exact per-level native CPU pixels. No compressed authoring, normal-Y conversion, alpha-coverage/variance correction or GPU/rendering certification.'}


def validate(plan):
    if plan.get('format')!=FORMAT or plan.get('generator_sha256')!=fingerprint():raise FormatError('Unsupported or stale texture plan')
    root=Path(plan['source']).parent;game=Path(plan['game']);origin=plan['image']
    if inventory(root)!=plan['source_files']:raise FormatError('Texture source Mod changed')
    pixels,current=images.read_png(origin['file'])
    if current!=origin:raise FormatError('Texture PNG source changed')
    levels=texture_levels.generate(pixels,plan['role'],plan['mips']);desired=document(plan['namespace'],pixels,plan['role'],plan['mips'],levels)
    if plan['level_count']!=len(levels) or plan['color_space']!=texture_levels.ROLES[plan['role']]:raise FormatError('Texture role/mip policy differs')
    yaml=checked(plan['desired_file'],plan['desired_sha256']);binary=checked(plan['binary'],plan['binary_sha256'])
    if asset_yaml.load(yaml.read_bytes())!=desired:raise FormatError('Texture plan differs from PNG pixels/recipe')
    binding=native.public_binding(native.profile_for(game))
    for key in ('compile','readback'):
        report=plan[key]
        if report['binding']!=binding:raise FormatError('Texture backend changed')
        expected_path=Path(report['output'])/report['files'][0]['name']
        if file_hash(expected_path)!=report['files'][0]['sha256']:raise FormatError('Texture native binary evidence changed')
        text=Path(report['output'])/report['files'][1]['name'];checked(text,report['files'][1]['sha256'])
        if asset_yaml.load(text.read_bytes())!=desired:raise FormatError('Texture native YAML readback differs')
    if (Path(plan['compile']['source']).resolve()!=yaml.resolve() or plan['compile']['source_sha256']!=plan['desired_sha256']
            or plan['compile']['files'][0]['sha256']!=plan['binary_sha256']
            or plan['readback']['source_sha256']!=plan['binary_sha256']
            or not plan['readback']['comparison']['all_compared_data_equal']):raise FormatError('Texture compiler/file witness binding differs')
    proofs=plan['native_mip_pixels']
    if len(proofs)!=len(levels) or plan['native_pixels']!=proofs[0]:raise FormatError('Texture native mip witness coverage differs')
    for im,proof in zip(levels,proofs):
        checked(proof['input'],proof['input_sha256']);png=checked(proof['png_file'],proof['png_sha256'])
        with images.pillow().open(png) as native_image:native_image=native_image.convert('RGBA');native_image.load()
        if (native_image.size!=im.size or native_image.tobytes()!=im.tobytes() or proof['binding']!=binding
                or proof['rgba_sha256']!=sha(im.tobytes()) or not proof['pixels_equal'] or Path(proof['input']).read_bytes()!=images.padded_rgba(im)):
            raise FormatError('Texture native pixel witness differs')
    if plan['target']!=f"Libraries/{plan['namespace']}.clb" or plan['locator']!=f"[{plan['namespace']}]TX\\0":raise FormatError('Texture namespace/output changed')
    lib=content_libraries.read(binary)
    if lib.name!=plan['namespace'] or len(lib.members)!=1 or lib.members[0]['identity']['locator']!=plan['locator'] or lib.members[0]['identity']['type_id']!='0x0041003D':
        raise FormatError('Texture compiled identity/type differs')
    actual=binary_surfaces(binary,plan['locator'])
    if actual['color_space']!=plan['color_space'] or len(actual['levels'])!=len(levels):raise FormatError('Texture compiled mip/color-space policy differs')
    for i,(im,layer) in enumerate(zip(levels,actual['levels'])):
        if layer['padded_pixels']!=images.padded_rgba(im):raise FormatError('Texture compiled stream differs from the source pixels at mip '+str(i))
    read_binary=Path(plan['readback']['output'])/plan['readback']['files'][0]['name']
    if not content_libraries.compare(binary,read_binary)['all_compared_data_equal']:raise FormatError('Texture independent binary differs')
    images.namespace_check(plan['namespace'],root,game);return binary


def apply_plan(plan,output):
    binary=validate(plan);root=Path(plan['source']).parent
    with copy_directory(root,output,[plan['game'],Path(plan['image']['file']),binary.parent]) as (stage,files):
        if files!=plan['source_files']:raise FormatError('Texture Mod changed before staging')
        target=stage/plan['target'];target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(binary.read_bytes())
        directory=stage/'SourceImages'/plan['namespace'];directory.mkdir(parents=True)
        image=Path(plan['image']['file']);checked(image,plan['image']['sha256']);(directory/image.name).write_bytes(image.read_bytes())
        if file_hash(target)!=plan['binary_sha256'] or file_hash(directory/image.name)!=plan['image']['sha256']:raise FormatError('Texture or PNG changed while staging')
        receipt={'format':'aow4mod-texture-receipt/1','target':plan['target'],'locator':plan['locator'],'sha256':file_hash(target),
            'source_image':plan['image'],'role':plan['role'],'mips':plan['mips'],'level_count':plan['level_count'],'color_space':plan['color_space'],
            'native_pixels_equal':True,'gpu_verified':False,'coverage':plan['coverage']}
        save_record(directory/'.aow4mod-texture.json',receipt)
    return receipt
