"""Explicit CLB dependency declarations with native, structurally scoped staging."""
import copy
from pathlib import Path
import struct
import uuid

from . import asset_yaml,content_libraries,native,native_assets
from .asset_editing import relative_clb
from .authoring import safe_name
from .core import FormatError,mod_file,sha
from .filesystem import copy_directory,file_hash,inventory,save_record
from .native_overlays import locked_inputs

FORMAT='aow4mod-clb-import-plan/1'


def fingerprint():
    root=Path(__file__).parent
    return sha(b''.join((root/n).read_bytes() for n in ('clb_imports.py','content_libraries.py','asset_yaml.py','native_assets.py')))


def names(library):
    if 22 not in library.root:return []
    raw=library.root[22].raw();position=4;result=[]
    if not 4<=len(raw)<=1024*1024:raise FormatError('CLB dependency vector exceeds bounds')
    count=struct.unpack_from('<I',raw)[0]
    if count>512:raise FormatError('CLB dependency count exceeds bounds')
    for _ in range(count):
        if position+4>len(raw):raise FormatError('Truncated CLB dependency length')
        size=struct.unpack_from('<I',raw,position)[0];position+=4
        if not 1<=size<=4096 or size>len(raw)-position:raise FormatError('CLB dependency name exceeds bounds')
        try:value=raw[position:position+size].decode('utf-8','strict')
        except UnicodeError as exc:raise FormatError('CLB dependency name is not UTF-8') from exc
        if '\0' in value:raise FormatError('CLB dependency name contains a null')
        result.append(value);position+=size
    if position!=len(raw):raise FormatError('CLB dependency vector has trailing bytes')
    return result


def identifier(value):
    safe_name(value)
    if not value.casefold().endswith('.clb') or not value[:-4] or len(value.encode('utf-8'))>255:
        raise FormatError('CLB import must be a basename ending in .clb, at most 255 UTF-8 bytes')
    return value


def derive(document,add):
    after=copy.deepcopy(document);before=after.get('22_FDependencies',[])
    if not isinstance(before,list) or len(before)>512:raise FormatError('Unreviewed CLB dependency YAML layout')
    before=[identifier(n) for n in before];add=[identifier(n) for n in add]
    if not add or len(add)>512 or len({n.casefold() for n in before})!=len(before) or len({n.casefold() for n in add})!=len(add):
        raise FormatError('Select nonduplicate CLB imports and preserve an unambiguous baseline')
    if any(Path(n).stem.casefold()==document['20_FName'].casefold() for n in [*before,*add]):raise FormatError('A CLB cannot import itself')
    additions=[n for n in add if n.casefold() not in {b.casefold() for b in before}]
    if len(before)+len(additions)>512:raise FormatError('CLB dependency count exceeds bounds')
    after['22_FDependencies']=before+additions
    return after,{'before':before,'after':before+additions,'added':additions}


def inspect(path):
    path=Path(path).resolve();digest=file_hash(path);library=content_libraries.read(path);dependencies=names(library)
    if file_hash(path)!=digest:raise FormatError('CLB changed during dependency inspection')
    return {'format':'aow4mod-clb-dependencies/1','source':str(path),'sha256':digest,'library':library.name,
            'dependencies':dependencies,'count':len(dependencies),
            'coverage':'Explicit CLB field-22 declarations only; target existence, closure, cycles, actual links and rendering are not verified.'}


def compare(source,candidate,expected):
    """Compare actual files, independent of editable YAML/receipt claims."""
    left,right=content_libraries.read(source),content_libraries.read(candidate);result=content_libraries.compare(source,candidate)
    changes=set(result['changed_library_fields']);padding=False
    if '0x1D' in changes and 29 in left.root and 29 in right.root:
        a,b=left.root[29].raw(),right.root[29].raw()
        # Native root FIsOriginalContent is one bool followed by up to 15 zero
        # alignment bytes. Do not normalize other fields or nonzero suffixes.
        if 1<=len(a)<=16 and 1<=len(b)<=16 and a[0]==b[0] and a[0] in (0,1) and not any(a[1:]+b[1:]):
            changes.remove('0x1D');padding=True
    if (left.name!=right.name or names(right)!=expected or result['missing_members'] or result['added_members'] or result['changed_members'] or
            not result['string_table_equal'] or not result['opaque_data_equal'] or changes-{'0x16'}):
        raise FormatError('CLB import edit changed unrelated library/member/string/payload data')
    return {'dependencies':expected,'unrelated_serialized_data_equal':True,'root_bool_alignment_changed':padding,'comparison':result}


def make_plan(mod,library,add,game,cache_dir=Path('.aow4mod/native'),timeout=60):
    add=list(add);acp=mod_file(Path(mod));game=Path(game).resolve();library=Path(library).resolve()
    if not library.is_relative_to(acp.parent):raise FormatError('Select a CLB inside the source Mod')
    relative=relative_clb(library.relative_to(acp.parent).as_posix());original=file_hash(library);files=inventory(acp.parent)
    cache=native.check_cache_directory(cache_dir,game,[acp.parent]);unit=cache/'clb-import-plans'/uuid.uuid4().hex;unit.mkdir(parents=True)
    exported=native_assets.export_library(library,game,unit/'baseline',cache,timeout)
    if not exported['comparison']['all_compared_data_equal']:raise FormatError('CLB baseline is not independently roundtrip-stable')
    baseline=asset_yaml.load((Path(exported['output'])/exported['files'][1]['name']).read_bytes());desired,change=derive(baseline,add)
    if names(content_libraries.read(library))!=change['before']:raise FormatError('CLB baseline dependency declaration differs')
    if not change['added']:raise FormatError('CLB import plan makes no change')
    (unit/'input').mkdir();desired_file=unit/'input/desired.yaml';desired_file.write_text(asset_yaml.dump(desired),encoding='utf-8')
    compiled=native_assets.compile_library(desired_file,game,unit/'compiled',cache,timeout);binary=Path(compiled['output'])/compiled['files'][0]['name']
    preservation=compare(library,binary,change['after'])
    reloaded=native_assets.export_library(binary,game,unit/'readback',cache,timeout)
    if not reloaded['comparison']['all_compared_data_equal']:raise FormatError('CLB import candidate is not independently roundtrip-stable')
    if inventory(acp.parent)!=files or file_hash(library)!=original:raise FormatError('CLB import source changed during planning')
    return {'format':FORMAT,'generator_sha256':fingerprint(),'source':str(acp),'source_files':files,'game':str(game),
            'library':str(library),'library_sha256':original,'relative_path':relative,'request':{'add':list(add)},'change':change,
            'desired_yaml':str(desired_file),'desired_yaml_sha256':file_hash(desired_file),'export':exported,'compile':compiled,'readback':reloaded,
            'compiled_file':str(binary),'compiled_sha256':file_hash(binary),'preservation':preservation,
            'coverage':'Add explicit CLB dependencies in a new Mod copy; exact unrelated data and independent native file/YAML readback. Dependency resolution, cycles, shader selection and rendering need separate checks.'}


def _validate(plan):
    if not isinstance(plan,dict) or plan.get('format')!=FORMAT or plan.get('generator_sha256')!=fingerprint():raise FormatError('Unsupported or stale CLB import plan')
    acp=Path(plan['source']).resolve();library=Path(plan['library']).resolve();game=Path(plan['game']).resolve();binary=Path(plan['compiled_file']).resolve()
    relative=relative_clb(plan['relative_path'])
    if not library.is_relative_to(acp.parent) or library.relative_to(acp.parent).as_posix()!=relative or inventory(acp.parent)!=plan['source_files']:
        raise FormatError('CLB import source/path changed')
    for path,digest in ((library,plan['library_sha256']),(binary,plan['compiled_sha256']),(Path(plan['desired_yaml']),plan['desired_yaml_sha256'])):
        if file_hash(path)!=digest:raise FormatError('CLB import input/candidate changed')
    binding=native.public_binding(native.profile_for(game));documents=[]
    for receipt,source,digest,kind in ((plan['export'],library,plan['library_sha256'],'export'),(plan['compile'],Path(plan['desired_yaml']),plan['desired_yaml_sha256'],'compile'),(plan['readback'],binary,plan['compiled_sha256'],'export')):
        expected_format='aow4mod-native-assets/1' if kind=='export' else 'aow4mod-native-asset-compile/1'
        if (receipt.get('format')!=expected_format or receipt['source_sha256']!=digest or Path(receipt['source']).resolve()!=source.resolve() or receipt['binding']!=binding):
            raise FormatError('CLB import native receipt binding differs')
        if kind=='export' and not receipt['comparison']['all_compared_data_equal']:raise FormatError('CLB import native save differs')
        if kind=='compile' and (receipt.get('yaml_fields_preserved') is not True or receipt.get('member_identities_preserved') is not True):raise FormatError('CLB import native compilation is incomplete')
        if not isinstance(receipt['files'],list) or len(receipt['files'])!=2:raise FormatError('CLB import native artifact coverage differs')
        for artifact in receipt['files']:
            if Path(artifact['name']).name!=artifact['name'] or file_hash(Path(receipt['output'])/artifact['name'])!=artifact['sha256']:raise FormatError('CLB import native artifact changed')
        if kind=='export' and not content_libraries.compare(source,Path(receipt['output'])/receipt['files'][0]['name'])['all_compared_data_equal']:
            raise FormatError('CLB import independent native copy differs')
        documents.append(asset_yaml.load((Path(receipt['output'])/receipt['files'][1]['name']).read_bytes()))
    desired=asset_yaml.load(Path(plan['desired_yaml']).read_bytes());expected,change=derive(documents[0],plan['request']['add'])
    if names(content_libraries.read(library))!=change['before']:raise FormatError('CLB import baseline dependency declaration differs')
    if not change['added'] or change!=plan['change'] or expected!=desired or documents[1]!=desired or documents[2]!=desired:raise FormatError('CLB import recipe/readback differs')
    if (Path(plan['compile']['output'])/plan['compile']['files'][0]['name']).resolve()!=binary:raise FormatError('CLB import compiled path differs')
    if content_libraries.read(binary).name.casefold()!=Path(relative).stem.casefold():raise FormatError('CLB import filename/namespace differs')
    preservation=compare(library,binary,change['after'])
    if preservation!=plan['preservation']:raise FormatError('CLB import preservation evidence differs')
    return change


def validate(plan):
    try:return _validate(plan)
    except (KeyError,IndexError,TypeError) as exc:raise FormatError('Malformed CLB import plan or native evidence') from exc


def apply_plan(plan,output):
    change=validate(plan);source=Path(plan['source']).resolve();binary=Path(plan['compiled_file']).resolve();game=Path(plan['game']).resolve()
    with locked_inputs([Path(plan['library']),binary]),copy_directory(source.parent,output,[game]) as (stage,files):
        if files!=plan['source_files']:raise FormatError('CLB import source changed before staging')
        data=binary.read_bytes()
        if sha(data)!=plan['compiled_sha256']:raise FormatError('CLB import candidate changed before staging')
        destination=stage/relative_clb(plan['relative_path']);destination.write_bytes(data)
        if file_hash(destination)!=plan['compiled_sha256']:raise FormatError('CLB import staged bytes differ')
        report={'format':'aow4mod-clb-import-receipt/1','output':str(Path(output).resolve()),'relative_path':plan['relative_path'],
                'change':change,'compiled_sha256':plan['compiled_sha256'],'preservation':plan['preservation'],'source_files':files,
                'native_file_readback_verified':True,'runtime_links_verified':False,'coverage':plan['coverage']}
        save_record(stage/'.aow4mod-clb-import.json',report)
    return report
