"""Bounded PNG-to-native-UI-atlas authoring and independent native PNG witnesses."""
import base64
import io
import json
from pathlib import Path
import re
import shutil
import struct
import uuid

from .core import FormatError, mod_file, sha
from .filesystem import copy_directory, file_hash, inventory, save_record
from . import asset_yaml, content_libraries, native, native_assets, native_validation

FORMAT='aow4mod-image-plan/1'
MAX_SOURCE=1024
MAX_ATLAS=2048
MAX_IMAGES=64


def pillow():
    try:
        from PIL import Image
    except ImportError as exc:raise FormatError('PNG authoring requires the [assets] extra (Pillow)') from exc
    return Image


def read_png(path):
    path=Path(path).resolve()
    if path.stat().st_size>16*1024*1024:raise FormatError('PNG source exceeds 16 MiB')
    with path.open('rb') as source:raw=source.read(16*1024*1024+1)
    if len(raw)>16*1024*1024 or len(raw)<33 or raw[:8]!=b'\x89PNG\r\n\x1a\n' or raw[12:16]!=b'IHDR':
        raise FormatError('Expected a PNG source no larger than 16 MiB')
    w,h=struct.unpack_from('>II',raw,16)
    if not 1<=w<=MAX_SOURCE or not 1<=h<=MAX_SOURCE or raw[24]>8:
        raise FormatError('PNG source must have 1..1024 pixels per side and at most 8 bits per channel')
    Image=pillow()
    with Image.open(io.BytesIO(raw)) as check:
        if check.format!='PNG' or getattr(check,'n_frames',1)!=1:raise FormatError('Animated PNG sources are unsupported')
        check.verify()
    with Image.open(io.BytesIO(raw)) as source:
        if source.info.get('icc_profile'):raise FormatError('Convert embedded ICC profiles to ordinary sRGB before importing')
        gamma=source.info.get('gamma')
        if gamma is not None and abs(gamma-0.45455)>0.0001:
            raise FormatError('PNG gamma differs from the supported ordinary sRGB UI preset')
        if source.getexif().get(274,1)!=1:raise FormatError('Apply PNG orientation metadata before importing')
        image=source.convert('RGBA');image.load()
    if image.size!=(w,h) or path.stat().st_size!=len(raw) or file_hash(path)!=sha(raw):raise FormatError('PNG changed or has inconsistent dimensions')
    return image,{'file':str(path),'sha256':sha(raw),'width':w,'height':h,'rgba_sha256':sha(image.tobytes())}


def power_two(value):return 1<<(value-1).bit_length()


def layout(sizes):
    if not 1<=len(sizes)<=MAX_IMAGES:raise FormatError('An atlas requires 1..64 PNG inputs')
    if any(type(w) is not int or type(h) is not int or not 1<=w<=MAX_SOURCE or not 1<=h<=MAX_SOURCE for w,h in sizes):
        raise FormatError('Invalid source image dimensions')
    order=sorted(range(len(sizes)),key=lambda i:(-sizes[i][1],-sizes[i][0],i))
    minimum=max(32,power_two(max(w+2 for w,h in sizes)));candidates=[]
    for width in (32,64,128,256,512,1024,2048):
        if width<minimum:continue
        x=y=row=0;positions={}
        for i in order:
            w,h=sizes[i]
            if x+w+2>width:x=0;y+=row;row=0
            positions[i]=(x+1,y+1);x+=w+2;row=max(row,h+2)
        height=max(4,power_two(y+row))
        if height<=MAX_ATLAS:candidates.append((width*height,max(width,height),width,height,positions))
    if not candidates:raise FormatError('PNG inputs do not fit one 2048x2048 atlas with borders')
    _,_,width,height,positions=min(candidates,key=lambda c:c[:4])
    return width,height,[positions[i] for i in range(len(sizes))]


def padded_rgba(image):
    width,height=image.size;raw=image.tobytes();padding=bytes((-width*4)%8)
    return b''.join(raw[y*width*4:(y+1)*width*4]+padding for y in range(height))


def surface(width,height,data):
    if type(width) is not int or type(height) is not int or not 1<=width<=MAX_ATLAS or not 1<=height<=MAX_ATLAS:
        raise FormatError('Native surface dimensions exceed 1..2048')
    stride=(width*4+7)//8*8
    if len(data)!=stride*height:raise FormatError('Native RGBA surface must include 8-byte-aligned rows')
    return {'cid':'4259876','cname':'TRECustomSurface','20_FDescription.Width':str(width),
            '21_FDescription.Height':str(height),'25_FDescription.Depth':'1','23_FDescription.Format':'83',
            '24_FDescription.Bpp':'32','22_FStream':base64.b64encode(data).decode('ascii')}


def surface_yaml(width,height,data):
    return ('\n'.join(f'{k}: {v}' for k,v in surface(width,height,data).items())+'\n').encode('ascii')


def unpack_surface(value):
    if value.get('cid')!='4259876' or value.get('cname')!='TRECustomSurface' or value.get('23_FDescription.Format')!='83' or value.get('24_FDescription.Bpp')!='32' or value.get('25_FDescription.Depth')!='1':
        raise FormatError('Expected a supported native RGBA8 2D surface')
    width,height=int(value['20_FDescription.Width']),int(value['21_FDescription.Height'])
    try:data=base64.b64decode(value['22_FStream'],validate=True)
    except ValueError as exc:raise FormatError('Invalid native surface stream') from exc
    surface(width,height,data)
    stride=(width*4+7)//8*8
    rgba=b''.join(data[y*stride:y*stride+width*4] for y in range(height))
    return pillow().frombytes('RGBA',(width,height),rgba),data


def object_record(cid,name,**fields):return {'cid':str(cid),'cname':name,**fields}


def array_layers(cid,name,first):
    return object_record(cid,name,**{'0_ArraySize':'8','1_ECAST_PTR_OBJ(FLayers[Cnt])':first,
        **{f'{i}_ECAST_PTR_OBJ(FLayers[Cnt])':{'cid':'0'} for i in range(2,9)}})


def document(name,images,names):
    if not re.fullmatch(r'[A-Z][A-Z0-9_]{0,29}',name):raise FormatError('Library name must be 1..30 ASCII uppercase letters, digits or underscores, starting with a letter')
    if len(images)!=len(names) or len({n.casefold() for n in names})!=len(names):raise FormatError('Sprite names must be unique within the atlas')
    for label in names:
        from .authoring import safe_name
        safe_name(label)
        if len(label.encode('utf-8'))>200:raise FormatError('Sprite name exceeds 200 UTF-8 bytes')
    cw,ch,positions=layout([im.size for im in images]);Image=pillow();canvas=Image.new('RGBA',(cw,ch));sprites=[]
    dev=lambda:object_record(0x402000,'TEResourceDevInfo',**{'4202496_scope':{}})
    placements=[]
    for index,(image,label,(x,y)) in enumerate(zip(images,names,positions),1):
        width,height=image.size;canvas.paste(image,(x,y))
        canvas.paste(image.crop((0,0,width,1)),(x,y-1));canvas.paste(image.crop((0,height-1,width,height)),(x,y+height))
        canvas.paste(image.crop((0,0,1,height)),(x-1,y));canvas.paste(image.crop((width-1,0,width,height)),(x+width,y))
        for dx,dy,sx,sy in [(-1,-1,0,0),(width,-1,width-1,0),(-1,height,0,height-1),(width,height,width-1,height-1)]:
            canvas.putpixel((x+dx,y+dy),image.getpixel((sx,sy)))
        filename=f'SourceImages/{name}/{label}'
        layer=object_record(0x4134c2,'TRETextureAtlasImageLayer',**{'0_FSurface':surface(width,height,padded_rgba(image)),
            '1_FTextureSurfaceList':object_record(0x410066,'TRESurfaceList'),'10_FSurfaceFileName':filename,'11_scope':{}})
        scope={'2_FOrigin':[format(x/cw,'.17g'),format(y/ch,'.17g')],
               '3_FSize':[format(width/cw,'.17g'),format(height/ch,'.17g')],'6_FWidth':str(width),'7_FHeight':str(height),
               '10_FTextureSurfaceUV':[format(width/power_two(width),'.17g'),format(height/power_two(height),'.17g')],
               '11_FLayers':array_layers(0x4134c3,'TRETextureAtlasImageLayerList',layer),'13_FLODClamp':'1'}
        sprites.append(object_record(0x413404,'TRETextureAtlasImage',**{'10_FSubResourceID':str(index),'11_FUserName':label,'4273156_scope':scope}))
        placements.append({'id':index,'name':label,'x':x,'y':y,'width':width,'height':height,'source_relative':filename})
    mask=bytearray(cw*ch//8)
    for i,alpha in enumerate(canvas.getchannel('A').tobytes()):
        if alpha:mask[i//8]|=1<<(i%8)
    texture=object_record(0x41003d,'TRETextureResourceDEV',**{'19_FResourceDevInfo':dev(),'21_FUserName':name+' layer',
        '30_FWidth':str(cw),'31_FHeight':str(ch),'32_*(int*)&FLegacyFormat':str(0x20053),'33_FLevels':'1',
        '34_&FLegacyType':'2','35_FStreamingEnabled':'false','36_&FLegacyColorSpace':'2','37_&FQualityModifier':'1',
        '40_FSurfaces':object_record(0x41014e,'TRETextureSurfaceList',**{'1_scope':{'0_E':surface(cw,ch,padded_rgba(canvas))}})})
    atlas_layer=object_record(0x4134c4,'TRETextureAtlasLayer',**{'0_FTextureResource':texture,
        '1_FCustomData':object_record(0x401402,'TEDataObject',**{'4199426_scope':{
            '0_(int&)Size':str(len(mask)),'1_FStreamHandle':base64.b64encode(mask).decode('ascii')}})})
    atlas=object_record(0x413403,'TRETextureAtlas',**{'19_FResourceDevInfo':dev(),'20_FName':f'[{name}]TXA\\0',
        '21_FUserName':name,'4273155_scope':{'13_Version':'3','1_FImages':object_record(0x413405,'TRETextureAtlasImageList',**{'1_this':sprites}),
        '2_FLayers':array_layers(0x4134c5,'TRETextureAtlasLayerList',atlas_layer),'3_&FUsage':'4','5_FIDGen':str(len(images)),
        '6_FWidth':str(cw),'7_FHeight':str(ch),'8_FLevels':'1'}})
    result={'YAMLRPKVersion':'1','19_*(int*)&FVersionNumber':'8','20_FName':name,'22_FDependencies':['SYSTEM.CLB'],
        '21_FList':object_record(0x400081,'TEResourceList',**{'1_this':[atlas]}),
        '24_FFeatures':object_record(0x4000ab,'TELibraryFeatureContainer',**{'1_this':[
            object_record(0x413482,'TRETextureAtlasLibraryFeature',**{'10_FUserName':'Default','11_FCategoryID':'0'})]}),
        '28_FResourceSeparatorList':object_record(0x402801,'TEResourceSeparatorList')}
    asset_yaml.validate_tree(result)
    return result,canvas,placements


def native_png(value,game,cache_dir,work,timeout=45):
    expected,data=unpack_surface(value);work=Path(work);work.mkdir()
    packet=work/'surface.bin';packet.write_bytes(data)
    params={'path':str(packet),'sha256':sha(data),'width':expected.width,'height':expected.height}
    result=native.run_worker(game,'surface-png',params,cache_dir,timeout)
    payload=result['payload'];image_file=Path(payload['output_file'])
    if file_hash(packet)!=params['sha256'] or file_hash(image_file)!=payload['output_sha256']:
        raise FormatError('Native image input/output changed')
    with pillow().open(image_file) as decoded:
        actual=decoded.convert('RGBA');actual.load()
    if actual.size!=expected.size or actual.tobytes()!=expected.tobytes():raise FormatError('Native PNG readback changed dimensions or RGBA pixels')
    return {'format':'aow4mod-native-png/1','binding':native.public_binding(result['binding']),'run':result['run'],
        'input':str(packet),'input_sha256':params['sha256'],'width':expected.width,'height':expected.height,
        'rgba_sha256':sha(actual.tobytes()),'png_file':str(image_file),'png_sha256':file_hash(image_file),
        'pixels_equal':True,'diagnostics':native_validation.diagnostics(result['run'])}


def namespace_check(name,mod,game):
    for root in [Path(mod),Path(game)/'Content']:
        for path in root.rglob('*.clb'):
            if path.stem.casefold()==name.casefold() or content_libraries.identity(path)['name'].casefold()==name.casefold():
                raise FormatError('Image library name already exists in the Mod or game: '+str(path))


def prepared(files,name):
    images=[];inputs=[];names=[]
    if not 1<=len(files)<=MAX_IMAGES:raise FormatError('Provide 1..64 PNG files')
    for file in files:
        image,evidence=read_png(file);images.append(image);inputs.append(evidence);names.append(Path(file).name)
    doc,canvas,placements=document(name,images,names)
    return doc,canvas,placements,inputs,images


def make_plan(mod,files,name,game,cache_dir=Path('.aow4mod/native'),timeout=60):
    if not isinstance(name,str):raise FormatError('Image library name must be text')
    acp=mod_file(Path(mod));name=name.upper();game=Path(game).resolve()
    cache_dir=native.check_cache_directory(cache_dir,game,[acp.parent,*[Path(p).resolve() for p in files]])
    original=inventory(acp.parent)
    doc,canvas,placements,inputs,sources=prepared(files,name)
    namespace_check(name,acp.parent,game)
    work=cache_dir/'image-plans'/uuid.uuid4().hex;work.mkdir(parents=True)
    desired=work/'input/desired.yaml';desired.parent.mkdir()
    desired.write_text(asset_yaml.dump(doc),encoding='utf-8')
    compiled=native_assets.compile_library(desired,game,work/'compiled',cache_dir,timeout)
    binary=work/'compiled'/compiled['files'][0]['name']
    exported=native_assets.export_library(binary,game,work/'file-readback',cache_dir,timeout)
    if not exported['comparison']['all_compared_data_equal']:
        raise FormatError('New image library changed during native file reload')
    verify_identity(binary,name)
    observed=asset_yaml.load((work/'file-readback'/exported['files'][1]['name']).read_bytes())
    if observed!=doc:raise FormatError('New image library file reload differs from the requested YAML')
    scope=observed['21_FList']['1_this'][0]['4273155_scope']
    proofs=[]
    for index,sprite in enumerate(scope['1_FImages']['1_this']):
        value=sprite['4273156_scope']['11_FLayers']['1_ECAST_PTR_OBJ(FLayers[Cnt])']['0_FSurface']
        proof=native_png(value,game,cache_dir,work/f'png-source-{index}',timeout)
        if proof['rgba_sha256']!=inputs[index]['rgba_sha256']:raise FormatError('Stored source image pixels differ from the PNG')
        proofs.append(proof)
    value=scope['2_FLayers']['1_ECAST_PTR_OBJ(FLayers[Cnt])']['0_FTextureResource']['40_FSurfaces']['1_scope']['0_E']
    composite=native_png(value,game,cache_dir,work/'png-atlas',timeout)
    if composite['rgba_sha256']!=sha(canvas.tobytes()):raise FormatError('Stored atlas pixels differ from the packed PNGs')
    if inventory(acp.parent)!=original or any(file_hash(x['file'])!=x['sha256'] for x in inputs):
        raise FormatError('Image source or Mod changed during planning')
    plan={'format':FORMAT,'generator_sha256':file_hash(__file__),'mod_source':str(acp),'source_files':original,
        'game':str(game),'library':name,'relative_path':f'Libraries/{name}.clb','inputs':inputs,'sprites':placements,
        'atlas_dimensions':[canvas.width,canvas.height],'atlas_rgba_sha256':sha(canvas.tobytes()),
        'desired_yaml':str(desired),'desired_yaml_sha256':file_hash(desired),
        'compiled_file':str(binary),'compiled_sha256':file_hash(binary),'compile':compiled,'file_readback':exported,
        'source_native_png':proofs,'atlas_native_png':composite,
        'diagnostics':sorted(set(compiled['diagnostics']+exported['diagnostics']+composite['diagnostics']+
            [line for proof in proofs for line in proof['diagnostics']])),
        'coverage':'New single-layer UI atlas, RGBA8, one mip, source images, extruded borders and pixel-exact official PNG witnesses. '
                   'Alpha mask follows the observed nonzero-alpha bit layout; click behavior, color-managed rendering, compression and game entry-point wiring remain unverified.'}
    save_record(work/'image-plan.json',plan)
    return plan


def verify_proof(proof,value):
    image,data=unpack_surface(value)
    if (proof.get('pixels_equal') is not True or proof.get('input_sha256')!=sha(data) or
        proof.get('rgba_sha256')!=sha(image.tobytes()) or [proof.get('width'),proof.get('height')]!=list(image.size)):
        raise FormatError('Native image proof differs from the requested surface')
    if file_hash(proof['input'])!=proof['input_sha256'] or file_hash(proof['png_file'])!=proof['png_sha256']:
        raise FormatError('Native image witness files changed')
    with pillow().open(proof['png_file']) as result:
        actual=result.convert('RGBA');actual.load()
    if actual.size!=image.size or actual.tobytes()!=image.tobytes():raise FormatError('Native PNG witness pixels changed')


def verify_identity(path,name):
    library=content_libraries.read(path)
    if library.name!=name or len(library.members)!=1:
        raise FormatError('Image binary library identity/member count differs from the plan')
    member=library.members[0]['identity']
    if member['type_id']!='0x00413403' or member['locator']!=f'[{name}]TXA\\0' or member['name']!=name:
        raise FormatError('Image binary atlas identity differs from the plan')


def apply_plan(plan,output):
    if not isinstance(plan,dict) or plan.get('format')!=FORMAT or plan.get('generator_sha256')!=file_hash(__file__):
        raise FormatError('Unsupported or stale PNG image plan')
    acp=Path(plan['mod_source']).resolve();game=Path(plan['game']).resolve()
    if inventory(acp.parent)!=plan['source_files']:raise FormatError('Mod changed since image planning')
    if any(file_hash(x['file'])!=x['sha256'] for x in plan['inputs']):raise FormatError('PNG source changed since planning')
    binding=native.public_binding(native.profile_for(game))
    for evidence in [plan['compile'],plan['file_readback'],plan['atlas_native_png'],*plan['source_native_png']]:
        if evidence['binding']!=binding:raise FormatError('Native image backend changed since planning')
    doc,canvas,sprites,inputs,source_images=prepared([x['file'] for x in plan['inputs']],plan['library'])
    if inputs!=plan['inputs'] or sprites!=plan['sprites'] or list(canvas.size)!=plan['atlas_dimensions'] or sha(canvas.tobytes())!=plan['atlas_rgba_sha256']:
        raise FormatError('Image plan differs from its PNG inputs or layout')
    namespace_check(plan['library'],acp.parent,game)
    for path,digest in [(plan['desired_yaml'],plan['desired_yaml_sha256']),(plan['compiled_file'],plan['compiled_sha256'])]:
        if file_hash(path)!=digest:raise FormatError('Image compilation input/output changed')
    if asset_yaml.load(Path(plan['desired_yaml']).read_bytes())!=doc:raise FormatError('Image YAML differs from the requested atlas')
    compiled=plan['compile'];exported=plan['file_readback']
    if (compiled.get('format')!='aow4mod-native-asset-compile/1' or compiled.get('source_sha256')!=plan['desired_yaml_sha256']
        or not compiled.get('yaml_fields_preserved') or not compiled.get('member_identities_preserved')
        or compiled['files'][0]['sha256']!=plan['compiled_sha256'] or exported.get('source_sha256')!=plan['compiled_sha256']
        or not exported.get('comparison',{}).get('all_compared_data_equal')):
        raise FormatError('Image compilation/readback evidence does not bind the planned binary')
    for evidence in [compiled,exported]:
        for artifact in evidence['files']:
            path=Path(evidence['output'])/artifact['name']
            if file_hash(path)!=artifact['sha256']:raise FormatError('Image native readback artifact changed')
        yaml_file=Path(evidence['output'])/evidence['files'][1]['name']
        if asset_yaml.load(yaml_file.read_bytes())!=doc:raise FormatError('Native image YAML readback differs')
    saved=Path(exported['output'])/exported['files'][0]['name']
    verify_identity(plan['compiled_file'],plan['library'])
    if not content_libraries.compare(plan['compiled_file'],saved)['all_compared_data_equal']:
        raise FormatError('Native image file readback comparison differs')
    scope=doc['21_FList']['1_this'][0]['4273155_scope']
    if len(plan['source_native_png'])!=len(inputs):raise FormatError('Image pixel evidence is incomplete')
    for proof,sprite in zip(plan['source_native_png'],scope['1_FImages']['1_this']):
        verify_proof(proof,sprite['4273156_scope']['11_FLayers']['1_ECAST_PTR_OBJ(FLayers[Cnt])']['0_FSurface'])
    verify_proof(plan['atlas_native_png'],scope['2_FLayers']['1_ECAST_PTR_OBJ(FLayers[Cnt])']['0_FTextureResource']['40_FSurfaces']['1_scope']['0_E'])
    relative=f"Libraries/{plan['library']}.clb"
    if plan['relative_path']!=relative:raise FormatError('Image output path differs from its library name')
    with copy_directory(acp.parent,output,[game]) as (staged,original):
        target=staged/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(plan['compiled_file'],target)
        if file_hash(target)!=plan['compiled_sha256']:raise FormatError('Staged image library differs')
        for source,sprite in zip(inputs,sprites):
            destination=staged/sprite['source_relative'];destination.parent.mkdir(parents=True,exist_ok=True)
            if destination.exists() and file_hash(destination)!=source['sha256']:raise FormatError('Image source destination already contains different author data')
            shutil.copyfile(source['file'],destination)
            if file_hash(destination)!=source['sha256']:raise FormatError('Staged source PNG differs')
        if file_hash(staged/acp.name)!=file_hash(acp):raise FormatError('Image import changed the ACP')
        if inventory(acp.parent)!=plan['source_files'] or any(file_hash(x['file'])!=x['sha256'] for x in inputs):
            raise FormatError('Image input changed during staging')
        result={'format':'aow4mod-image-receipt/1','output':str(Path(output).resolve()),'library':plan['library'],
            'relative_path':relative,'compiled_sha256':plan['compiled_sha256'],'sprites':sprites,
            'locator':f"[{plan['library']}]TXA\\0",'atlas_dimensions':plan['atlas_dimensions'],
            'native_pixels_verified':True,'source_images_preserved':True,'acp_unchanged':True,
            'diagnostics':plan['diagnostics'],'coverage':plan['coverage'],'gameplay_tested':False}
        save_record(staged/'.aow4mod-image.json',result)
    return result
