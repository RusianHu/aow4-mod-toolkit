"""Recognized external asset paths; availability is separate from asset semantics."""
from collections import defaultdict
from pathlib import Path,PurePosixPath
import re

from .core import FormatError,decode_link
from .filesystem import file_hash

LIBRARY=re.compile(r'^\[([^\]#]+)\]([A-Za-z0-9_]+)\\(\d+)$')
SUFFIXES={'.clb','.dds','.png','.jpg','.tga','.tam','.cam','.srm','.env','.ogg','.wav','.bank','.ttf','.mo'}
# TFXResource's observed serialized effect graph is metadata 0x1E, rather
# than the class scopes used by gameplay resources. Do not scan every name,
# developer label or opaque metadata field as an asset declaration.
GRAPH_ASSET_FIELDS={0x00730034:(0x1e,)}


def path_reference(value,explicit=False):
    if not value or '\n' in value or '\r' in value:return None
    match=LIBRARY.fullmatch(value)
    if match:
        return {'kind':'library_member','value':value,'library':match[1]+'.clb',
                'category':match[2],'member':int(match[3]),'confidence':'recognized_locator',
                'member_validated':False}
    if value.lower().startswith('root:/'):
        return {'kind':'content_uri','value':value,'confidence':'content_uri'}
    if PurePosixPath(value.replace('\\','/')).suffix.lower() in SUFFIXES:
        return {'kind':'file','value':value,'confidence':'asset_reference_map' if explicit else 'path_string_candidate'}
    return None


def references(resource):
    found=[];visited=set();truncated=False
    def visit(node,path,depth=0):
        nonlocal truncated
        if depth>64 or len(visited)>100000:truncated=True;return
        if (node.start,node.end) in visited:return
        visited.add((node.start,node.end))
        if decode_link(node):return
        try:
            fields=node.mapping()
            if set(fields)=={0x14,0x17}:
                value=fields[0x14].string();label=fields[0x17].string()
                ref=path_reference(value,True)
                if ref:found.append({**ref,'label':label,'property':path});return
        except FormatError:pass
        try:
            value=node.string();ref=path_reference(value)
            if ref:found.append({**ref,'property':path})
            return
        except FormatError:pass
        fields=None
        try:
            kind,candidate=node.typed()
            if 0x10000<=kind<0x10000000:fields=candidate
        except FormatError:pass
        if fields is None:
            try:fields=node.mapping()
            except FormatError:return
        for key,child in fields.items():visit(child,path+f'/0x{key:X}',depth+1)
    for key,value in resource.fields.items():
        if key>=0x10000:visit(value,f'0x{key:08X}')
        elif key in GRAPH_ASSET_FIELDS.get(getattr(resource,'type_id',None),()):visit(value,f'meta/0x{key:X}')
    return found,truncated


def classify_matches(locations,fingerprints):
    if len(locations)<2:return 'found' if locations else 'missing'
    if all(Path(item['file']).suffix.lower()=='.mo' for item in locations):
        buckets=[]
        for item in locations:
            parts=Path(item['file']).parts;folded=[x.casefold() for x in parts]
            if 'language' not in folded:break
            i=len(folded)-1-folded[::-1].index('language')
            bucket=parts[i+1].casefold() if i+2<len(parts) else 'default_unspecified'
            item['locale_directory']=bucket;buckets.append(bucket)
        if len(buckets)==len(locations) and len(set(buckets))==len(buckets):return 'localized_variants'
    hashes=[]
    for item in locations:
        path=item['file']
        if path not in fingerprints:fingerprints[path]=file_hash(path)
        hashes.append(fingerprints[path])
    return 'equivalent_files' if len(set(hashes))==1 else 'ambiguous'


class Inventory:
    def __init__(self,game,mods):
        self.files=[];self.names=defaultdict(list);self.relative=defaultdict(list);self.content={}
        roots=[('game',Path(game)/'Content')]+[('mod',Path(p)) for p in mods]
        for origin,root in roots:
            for path in sorted(root.rglob('*')):
                if not path.is_file() or path.suffix.lower() not in SUFFIXES:continue
                item={'file':str(path.resolve()),'origin':origin,'root':str(root.resolve())}
                relative=path.relative_to(root).as_posix().casefold()
                self.files.append(item);self.names[path.name.casefold()].append(item)
                self.relative[relative].append(item)
                if origin=='game':self.content.setdefault(relative,[]).append(item)

    def resolve(self,ref):
        value=ref.get('library',ref['value']).replace('\\','/')
        if ref['kind']=='library_member':return self.names.get(value.casefold(),[]),'library_file_only'
        if value.lower().startswith('root:/'):
            path=PurePosixPath(value[6:])
            if path.is_absolute() or '..' in path.parts:return [],'unsafe_path'
            return self.content.get(path.as_posix().casefold(),[]),'exact_content_uri'
        path=PurePosixPath(value)
        if path.is_absolute() or '..' in path.parts or ':' in value:return [],'external_or_unsafe_path'
        return (self.relative.get(path.as_posix().casefold(),[]) if len(path.parts)>1 else
                self.names.get(path.name.casefold(),[])),'relative_path_or_basename'
