"""Serialized pass descriptor keys mapped to exact DXIL program-pool members."""
from pathlib import Path
import struct

from . import catalog,clb_streams,content_libraries,shaders
from .core import FormatError,sha
from .filesystem import file_hash

PASS_CID=0x41166a
RECORD_BYTES=72


def feature_names(fields):
    """FFeatureNames is the serialized ordered string vector (field 70)."""
    if 70 not in fields:return []
    raw=fields[70].raw()
    if len(raw)<4 or len(raw)>1024*1024:raise FormatError('Effect feature-name vector exceeds bounds')
    count=struct.unpack_from('<I',raw)[0];position=4;names=[]
    if count>256:raise FormatError('Effect feature-name count exceeds its 256-bit mask')
    for _ in range(count):
        if position+4>len(raw):raise FormatError('Truncated effect feature-name length')
        size=struct.unpack_from('<I',raw,position)[0];position+=4
        if not 1<=size<=1024 or size>len(raw)-position:raise FormatError('Effect feature name exceeds bounds')
        try:name=raw[position:position+size].decode('utf-8','strict')
        except UnicodeError as exc:raise FormatError('Effect feature name is not UTF-8') from exc
        if '\0' in name or name.casefold() in {n.casefold() for n in names}:raise FormatError('Ambiguous effect feature name')
        names.append(name);position+=size
    if position!=len(raw):raise FormatError('Effect feature-name vector has trailing bytes')
    return names


def decode(path,locator):
    path=Path(path).resolve();digest=file_hash(path);library=content_libraries.read(path)
    selected=[m for m in library.members if (m['identity']['locator'] or '').casefold()==locator.casefold()]
    if len(selected)!=1 or selected[0]['identity']['type_id']!='0x00411669':raise FormatError('Select one TREEffectResource locator')
    fields=selected[0]['fields']
    if not {51,72,73}<=fields.keys():raise FormatError('Effect has no reviewed DXIL program pool')
    pool,reference=clb_streams.read(library,fields[73],fields[72].uint(4));programs=shaders.decode(pool);by_offset={r['offset']:r for r in programs}
    names=feature_names(fields);rows=[];passes=[];used=set();unverified_stages=set();duplicates=0
    for pi,node in fields[51].array().items():
        cid,wrapper=node.typed()
        if cid!=PASS_CID or set(wrapper)!={PASS_CID}:raise FormatError('Unknown compiled effect-pass wrapper')
        p=wrapper[PASS_CID].mapping();name=library.string(p[0]);expression=library.string(p[1]) if 1 in p else ''
        pass_rows=[]
        for stage in range(8):
            count=p[60+stage*2].uint(4) if 60+stage*2 in p else 0;blob=p.get(61+stage*2)
            if not 0<=count<=100000 or (count and blob is None) or (blob is not None and len(blob.raw())!=count*RECORD_BYTES):
                raise FormatError('Effect shader lookup count/record stride differs')
            if not count:continue
            if len(rows)+count>100000:raise FormatError('Effect compiled lookup count exceeds bounds')
            raw=blob.raw();seen={}
            for ordinal in range(count):
                record=raw[ordinal*RECORD_BYTES:(ordinal+1)*RECORD_BYTES];key=record[:64]
                previous=seen.get(key)
                if previous is not None and previous[1]!=record:raise FormatError('Conflicting compiled descriptor key within a pass/stage')
                if previous is None:seen[key]=(ordinal,record)
                else:duplicates+=1
                offset,size=struct.unpack_from('<II',record,64);program=by_offset.get(offset)
                if program is None or program['bytes']!=size:raise FormatError('Effect lookup is not an exact DXIL container boundary/size')
                if stage in (0,1) and program['shader_kind']!=stage:raise FormatError('Effect stage differs from its DXIL shader kind')
                if stage not in (0,1):unverified_stages.add(stage)
                feature_mask=int.from_bytes(key[32:],'little')
                row={'pass':pi,'pass_name':name,'stage':stage,'lookup':ordinal,'descriptor_key':key.hex(),
                     'program_ordinal':program['ordinal'],'program_offset':offset,'program_bytes':size,
                     'program_sha256':program['sha256'],'shader_kind':program['shader_kind'],'inputs':program['inputs'],
                     'feature_mask':f'{feature_mask:064x}','enabled_features':[n for i,n in enumerate(names) if feature_mask>>i&1],
                     'unmapped_feature_bits':f'{feature_mask&~((1<<len(names))-1):064x}',
                     'duplicate_of':previous[0] if previous is not None else None}
                rows.append(row);pass_rows.append(row);used.add(program['ordinal'])
        passes.append({'index':pi,'name':name,'enable_static_expression':expression,'lookup_records':len(pass_rows)})
    if len(rows)>100000:raise FormatError('Effect compiled lookup count exceeds bounds')
    if file_hash(path)!=digest:raise FormatError('Effect library changed during program association')
    return {'source':str(path),'sha256':digest,'library':library.name,'locator':selected[0]['identity']['locator'],
            'pool_sha256':sha(pool),'pool_reference':reference,'programs':programs,'rows':rows,'passes':passes,
            'feature_names':names,'identical_duplicate_records':duplicates,
            'unreferenced_programs':[r['ordinal'] for r in programs if r['ordinal'] not in used],
            'unverified_stage_indices':sorted(unverified_stages)}


def inspect(path,locator,pass_index=None,stage=None,key=None,profile=None,limit=10,offset=0,require=(),forbid=()):
    catalog.check_page(limit,offset);data=decode(path,locator)
    wanted={n.casefold() for n in require};excluded={n.casefold() for n in forbid}
    if wanted&excluded:raise FormatError('A feature cannot be required and forbidden')
    if key is not None:
        try:parsed=bytes.fromhex(key)
        except ValueError as exc:raise FormatError('Descriptor key must be 64 bytes of hexadecimal') from exc
        if len(parsed)!=64:raise FormatError('Descriptor key must be 64 bytes of hexadecimal')
        key=parsed.hex()
    rows=[r.copy() for r in data['rows'] if (pass_index is None or r['pass']==pass_index) and (stage is None or r['stage']==stage) and
          (key is None or r['descriptor_key']==key) and wanted<={n.casefold() for n in r['enabled_features']} and
          not excluded&{n.casefold() for n in r['enabled_features']}]
    if profile is not None:
        if profile not in shaders.PROFILES:raise FormatError('Unknown vertex profile')
        for row in rows:
            if row['shader_kind']==1:
                row['vertex_profile_check']=shaders.profile_check(row['inputs'],profile) if row['inputs'] is not None else {
                    'profile':profile,'requirements_covered':False,'issues':[{'issue':'input_signature_not_available'}]}
    output={'format':'aow4mod-effect-programs/1','source':data['source'],'sha256':data['sha256'],'library':data['library'],'locator':data['locator'],
            'pool_sha256':data['pool_sha256'],'pool_reference':data['pool_reference'],'program_count':len(data['programs']),
            'lookup_count':len(data['rows']),'passes':data['passes'],'unreferenced_programs':data['unreferenced_programs'],
            'feature_names':data['feature_names'],'identical_duplicate_records':data['identical_duplicate_records'],
            'unverified_stage_indices':data['unverified_stage_indices'],**catalog.page(rows[offset:offset+limit],len(rows),limit,offset),
            'serialized_program_mapping_verified':bool(data['rows']),'zero_lookup_not_evaluated':not data['rows'],
            'selection_empty':not rows,'runtime_selection_verified':False,'rendering_verified':False,
            'feature_projection_source':'Record bytes 32..63 as a little-endian bitset indexed by effect field 70 (FFeatureNames); the first 32 bytes remain opaque.',
            'coverage':'Serialized pass/stage records and declared feature bits to exact decompressed DXIL containers and input declarations. Filters describe requested conditions; material/renderer feature evaluation and runtime choice remain unverified.'}
    if profile is not None:
        vertices=[r for r in rows if r['shader_kind']==1];output['profile_summary']={'profile':profile,'selected_vertex_lookups':len(vertices),
            'requirements_covered':sum(r['vertex_profile_check']['requirements_covered'] for r in vertices),
            'zero_vertices_not_evaluated':not vertices}
    return output
