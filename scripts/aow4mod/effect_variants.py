"""Read-only serialized effect-function feature variants from CLB metadata."""
from pathlib import Path

from . import catalog,content_libraries
from .core import FormatError
from .filesystem import file_hash


def inspect(path,locator,stage=None,pass_id=None,require=(),forbid=(),limit=10,offset=0):
    catalog.check_page(limit,offset);path=Path(path).resolve();digest=file_hash(path);library=content_libraries.read(path)
    matches=[m for m in library.members if (m['identity']['locator'] or '').casefold()==locator.casefold()]
    if len(matches)!=1 or matches[0]['identity']['type_id']!='0x00411669':raise FormatError('Select one TREEffectResource locator')
    fields=matches[0]['fields'];rows=[];functions=[]
    for index,node in fields[50].array().items():
        cid,f=node.typed()
        if cid!=0x411665:raise FormatError('Unknown effect-function class')
        kind=f[32].uint(4) if 32 in f else 0;render_pass=f[34].uint(4) if 34 in f else 4
        if stage is not None and kind!=stage or pass_id is not None and render_pass!=pass_id:continue
        feature_list=f[33].array() if 33 in f else {};features={}
        for feature in feature_list.values():
            cid,values=feature.typed()
            if cid!=0x411677:raise FormatError('Unknown function-feature class')
            name=library.string(values[40]);mask=values[41].uint(4)
            if name.upper() in {n.upper() for n in features} or not mask:raise FormatError('Ambiguous or empty effect feature mask')
            features[name]=mask
        wanted=[x.upper() for x in require];excluded=[x.upper() for x in forbid]
        names={n.upper():n for n in features}
        if set(wanted)&set(excluded):raise FormatError('A feature cannot be required and forbidden')
        # A requested feature absent from this function cannot be required;
        # it also cannot affect that function when excluded.
        if any(n not in names for n in wanted):continue
        codes=f[30].mapping()[2].mapping()
        if len(codes)>100000:raise FormatError('Effect function exceeds variant bound')
        functions.append({'index':index,'name':library.string(f[31]),'stage':kind,'pass_id':render_pass,'feature_masks':features,'variants':len(codes)})
        for ordinal,code in codes.items():
            cid,c=code.typed()
            if cid!=0x411667:raise FormatError('Unknown effect-code class')
            mask=c[31].uint(4) if 31 in c else 0
            if any(mask&features[names[n]]!=features[names[n]] for n in wanted):continue
            if any(n in names and mask&features[names[n]] for n in excluded):continue
            enabled=[name for name,bits in features.items() if mask&bits==bits]
            known=0
            for bits in features.values():known|=bits
            rows.append({'function':index,'variant':ordinal,'name':library.string(f[31]),'stage':kind,'pass_id':render_pass,
                         'mask':mask,'enabled_features':enabled,'unmapped_mask_bits':mask&~known})
    if file_hash(path)!=digest:raise FormatError('Effect library changed during metadata query')
    return {'format':'aow4mod-effect-variants/1','source':str(path),'sha256':digest,'library':library.name,'locator':matches[0]['identity']['locator'],
            'functions':functions,'items':rows[offset:offset+limit],'total':len(rows),'limit':limit,'offset':offset,
            'next_offset':offset+limit if offset+limit<len(rows) else None,'runtime_selection_verified':False,'program_pool_mapping_verified':False,
            'coverage':'Serialized function-specific feature masks and variant declarations only. Filters express requested conditions, not the material/renderer runtime choice.'}
