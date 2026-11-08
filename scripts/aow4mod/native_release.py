"""Self-contained, independently rechecked native serialization evidence for releases."""
import json
from pathlib import Path
import shutil

from .core import FormatError, read_acp
from .filesystem import file_hash, inventory, outside, save_record
from . import content_libraries, native, native_inspection, native_objects, native_roundtrip
from . import native_assets
from . import native_overlay_release,native_model_release,native_map_release,native_world_release,native_material_release,material_links
from . import native_texture_release

LEGACY_FORMAT = 'aow4mod-native-release/1'
PREVIOUS_FORMAT = 'aow4mod-native-release/2'
CONTEXT_FORMAT = 'aow4mod-native-release/3'
MODEL_FORMAT = 'aow4mod-native-release/4'
MAP_FORMAT = 'aow4mod-native-release/5'
WORLD_FORMAT = 'aow4mod-native-release/6'
MATERIAL_FORMAT = 'aow4mod-native-release/7'
FORMAT = 'aow4mod-native-release/8'
LEGACY_SCOPE = ('ACP metadata/override object binary readback, RPK native save and CLB data comparison. '
         'Not effective ACP merging, asset rendering, source-asset import or gameplay certification.')
PREVIOUS_SCOPE = ('ACP metadata/override object readback and explicit per-pack native ACP 0x40 application, RPK native save and CLB comparison. '
         'Not game activation/launcher precedence, complete assets, rendering or gameplay certification.')
CONTEXT_SCOPE = ('ACP metadata/override readback, native additive import preparation and explicit per-pack ACP 0x40 application with frozen local RPK context; RPK native save and CLB comparison. '
         'Not game activation/launcher precedence, complete assets, rendering or gameplay certification.')
MODEL_SCOPE = ('ACP metadata/override readback, additive import preparation and per-pack ACP application with frozen RPK context; '
         'RPK save, CLB comparison and frozen local TREModelResource mesh/effect-material link witnesses checked against payload graph/types. '
         'Not complete external shader/texture closure, shader variants, rendering, game mounting or gameplay certification.')
MAP_SCOPE = (MODEL_SCOPE+' Tactical maps add exact first/independent-second file saves and native object-body witnesses with bundled lookup inputs. '
         'Tactical terrain/entity semantics, complete map dependencies and gameplay remain unverified.')
WORLD_SCOPE = (MAP_SCOPE+' Known FZones and explicit cell-holder identities, all object/cell bytes and raw diagnostic logs are frozen in complete bounded batches. '
         'Other world managers, registered-world allocation/routing, terrain semantics and gameplay remain unverified.')
MATERIAL_SCOPE = (WORLD_SCOPE+' TREEffectMaterialResource effect pointer/type observations, independent source/effect object bodies, complete bounded CLB lookup inputs and raw diagnostic logs are frozen. '
         'Other material families, material feature evaluation, complete texture closure, rendering and game mounting remain unverified.')
SCOPE = (MATERIAL_SCOPE+' Known static material texture pointers/types/members and independent full texture objects are frozen and rechecked. '
         'Dynamic texture expressions, GPU upload, shader feature evaluation and rendering remain unverified.')


def targets(content, include_overlays=False, include_models=False,include_maps=False,include_world=False,include_materials=False,include_textures=False):
    content = Path(content).resolve()
    files = inventory(content)
    inputs = {p:r['sha256'] for p,r in files.items() if Path(p).suffix.casefold() in (('.acp','.rpk','.clb','.tam') if include_maps else ('.acp','.rpk','.clb'))}
    result = []
    for relative in sorted(inputs):
        suffix = Path(relative).suffix.casefold()
        if suffix == '.acp':
            _, changes = read_acp(content/relative)
            result.append({'file':relative,'operation':'acp_metadata','resource_id':None,'pack':None})
            result.extend({'file':relative,'operation':'acp_resource','resource_id':f"{c['resource'].rid:016X}",
                           'pack':c['pack']} for c in changes)
            if include_overlays:
                result.extend({'file':relative,'operation':'acp_apply','resource_id':None,'pack':pack}
                              for pack in sorted({c['pack'] for c in changes}))
        elif suffix=='.tam':
            result.append({'file':relative,'operation':'tam_roundtrip','resource_id':None,'pack':None})
            if include_world:result.append({'file':relative,'operation':'tam_world_entities','resource_id':None,'pack':None})
        else:
            result.append({'file':relative,'operation':'rpk_roundtrip' if suffix=='.rpk' else 'clb_export',
                           'resource_id':None,'pack':None})
            if suffix=='.clb' and include_models and native_model_release.model_members(content/relative):
                result.append({'file':relative,'operation':'clb_model_links','resource_id':None,'pack':None})
            if suffix=='.clb' and include_materials and material_links.members(content/relative):
                result.append({'file':relative,'operation':'clb_material_links','resource_id':None,'pack':None})
            if suffix=='.clb' and include_textures and material_links.members(content/relative):
                result.append({'file':relative,'operation':'clb_material_textures','resource_id':None,'pack':None})
    keys = [(x['file'],x['operation'],x['resource_id'],x['pack']) for x in result]
    if len(keys) != len(set(keys)): raise FormatError('Duplicate native release targets cannot be verified independently')
    return inputs, result


def _artifact(root, name):
    if not isinstance(name,str) or Path(name).is_absolute() or '..' in Path(name).parts:
        raise FormatError('Native evidence path must stay in its release')
    path = (Path(root)/name).resolve()
    if Path(root).resolve() not in path.parents or not path.is_file():
        raise FormatError('Native evidence file is missing or outside its release')
    return path


def _assessment(record, detail, content, root, binding,protocol=8):
    if detail.get('binding') != binding: raise FormatError('Native evidence binding differs between checks')
    source = Path(content)/record['file']; operation = record['operation']
    artifact = _artifact(root, record['artifact'])
    if file_hash(artifact) != record['artifact_sha256']: raise FormatError('Native serialized artifact changed')
    if operation=='clb_material_textures':
        if protocol<8:raise FormatError('Material texture evidence requires native release protocol 8')
        if detail.get('source')!={'file':record['file'],'sha256':file_hash(source)}:raise FormatError('Texture-link source differs')
        try:first=detail['batches'][0]['materials'][0]['artifacts']['before']
        except (KeyError,IndexError,TypeError) as exc:raise FormatError('Texture primary witness coverage is missing') from exc
        if first!={'file':record['artifact'],'sha256':record['artifact_sha256']}:raise FormatError('Texture primary witness differs')
        comparison=native_texture_release.assess(detail,source,content,root);equal=comparison['serialized_texture_data_equal']
    elif operation=='clb_material_links':
        if protocol<7:raise FormatError('Material-link evidence requires native release protocol 7')
        if detail.get('source')!={'file':record['file'],'sha256':file_hash(source)}:raise FormatError('Material-link source differs')
        try:first=detail['batches'][0]['materials'][0]['artifacts']['before']
        except (KeyError,IndexError,TypeError) as exc:raise FormatError('Material primary witness coverage is missing') from exc
        if first!={'file':record['artifact'],'sha256':record['artifact_sha256']}:raise FormatError('Material primary witness differs')
        comparison=native_material_release.assess(detail,source,content,root);equal=comparison['serialized_material_data_equal']
    elif operation=='tam_world_entities':
        if protocol<6:raise FormatError('World entity evidence requires native release protocol 6')
        if detail.get('source')!={'file':record['file'],'sha256':file_hash(source)}:raise FormatError('Native world target differs')
        if detail['index']!={'file':record['artifact'],'sha256':record['artifact_sha256']}:raise FormatError('Native world primary artifact differs')
        comparison=native_world_release.assess(detail,source,content,root)
        equal=comparison['serialized_world_data_equal']
    elif operation=='tam_roundtrip':
        if protocol<5:raise FormatError('TAM evidence requires native release protocol 5')
        if detail.get('source')!={'file':record['file'],'sha256':file_hash(source)}:raise FormatError('Native map target differs')
        if detail['artifacts']['first_saved']!={'file':record['artifact'],'sha256':record['artifact_sha256']}:raise FormatError('Native map primary artifact differs')
        comparison=native_map_release.assess(detail,source,content,root)
        equal=comparison['all_compared_data_equal']
    elif operation=='clb_model_links':
        if protocol<4:raise FormatError('Model-link evidence requires native release protocol 4')
        if detail.get('source')!=record['file']:raise FormatError('Model-link source file differs')
        first=detail['models'][0]['artifacts']['before']
        if first!={'file':record['artifact'],'sha256':record['artifact_sha256']}:raise FormatError('Model primary witness differs')
        comparison=native_model_release.assess(detail,source,root)
        equal=comparison['serialized_model_data_equal']
    elif operation=='acp_apply':
        if detail.get('source')!={'file':record['file'],'sha256':file_hash(source)} or detail.get('pack')!=record['pack']:
            raise FormatError('Native overlay evidence has a different ACP/pack target')
        if detail['artifacts']['merged']!={'file':record['artifact'],'sha256':record['artifact_sha256']}:
            raise FormatError('Native overlay primary artifact does not match its frozen material')
        comparison=native_overlay_release.assess(detail,content,root,require_context=protocol>=3)
        equal=comparison['scoped_application_witness_matched']
    elif operation.startswith('acp_'):
        if detail.get('format') != 'aow4mod-native-inspection/1': raise FormatError('Expected native object readback evidence')
        before, selected = native_inspection.source_body(source,record['resource_id'],record['pack'])
        if detail['source'].get('sha256') != file_hash(source) or detail['source'].get('cid') != selected['cid']:
            raise FormatError('Native object evidence does not bind its payload class/hash')
        if detail['source'].get('resource_id') != record['resource_id']:
            raise FormatError('Native object evidence names a different resource')
        profile = native.PROFILES.get(binding.get('dll_sha256'))
        if profile is None: raise FormatError('Native object omission rules require a reviewed historical DLL profile')
        omitted = profile['package_omitted_fields'] if selected['cid']=='0040009D' else None
        comparison = native_objects.compare_bodies(before,artifact.read_bytes(),omitted)
        equal = comparison['data_equal']
    elif operation=='rpk_roundtrip':
        if detail.get('format') != 'aow4mod-native-roundtrip/1': raise FormatError('Expected native RPK save evidence')
        comparison = native_roundtrip.compare(source,artifact)
        if 'context_libraries' in detail:
            expected=[{'file':p,'sha256':digest} for p,digest in targets(content)[0].items() if Path(p).suffix.casefold()=='.clb']
            if detail['context_libraries']!=expected:raise FormatError('Native RPK evidence has a different bundled CLB context')
        if 'context_languages' in detail:
            from . import language_context
            expected=[{'file':Path(p['file']).relative_to(content).as_posix(),'sha256':p['sha256']}
                      for p in language_context.bind(language_context.english(content))]
            if detail['context_languages']!=expected:raise FormatError('Native RPK evidence has a different bundled MO context')
        equal = comparison['all_serialized_data_equal']
    elif operation=='clb_export':
        if detail.get('format') != 'aow4mod-native-assets/1': raise FormatError('Expected native CLB export evidence')
        if detail.get('source_sha256') != file_hash(source): raise FormatError('Native CLB evidence has a different payload hash')
        comparison = content_libraries.compare(source,artifact)
        equal = comparison['all_compared_data_equal']
    else: raise FormatError('Unsupported native release operation')
    if comparison != detail.get('comparison'):
        raise FormatError('Native evidence comparison differs from its serialized artifact')
    diagnostics = detail.get('diagnostics',[])
    if not isinstance(diagnostics,list) or any(not isinstance(x,str) for x in diagnostics):
        raise FormatError('Invalid native diagnostic evidence')
    result={'data_equal':equal,'diagnostic_count':len(diagnostics),
            'state':'review_required' if not equal or diagnostics or comparison.get('link_issue_count',0) or comparison.get('issue_count',0) or comparison.get('material_link_issue_count',0) or comparison.get('texture_link_issue_count',0) or comparison.get('texture_objects_evaluated') is False else 'passed'}
    if operation=='clb_material_textures':result.update(texture_link_issue_count=comparison['texture_link_issue_count'],texture_count=comparison['texture_count'],texture_objects_evaluated=comparison['texture_objects_evaluated'])
    if operation=='clb_material_links':result.update(material_link_issue_count=comparison['material_link_issue_count'],material_count=comparison['material_count'],effect_objects_evaluated=comparison['effect_objects_evaluated'])
    if operation=='clb_model_links':result['link_issue_count']=comparison['link_issue_count']
    if operation=='tam_world_entities':
        result.update(world_entity_issue_count=comparison['issue_count'],world_entity_count=comparison['entity_count'],
                      world_objects_evaluated=comparison['objects_evaluated'])
    return result


def environment_blockers(checks):
    """Group repeated setup failures without changing check counts or gates."""
    grouped={}
    for check in checks:
        error=check.get('error','')
        if check.get('state')!='error' or not (error.startswith(('Unsupported native editor build ',
                'Native editor library not found:', 'Native editor introspection requires'))
                or error.startswith('Native operation ') and 'not reviewed for' in error):
            continue
        row=grouped.setdefault(error,{'reason':error,'affected_checks':0,'operations':set()})
        row['affected_checks']+=1;row['operations'].add(check['operation'])
    return [{**row,'operations':sorted(row['operations'])} for _,row in sorted(grouped.items())]


def summary(checks, version=8):
    errors = sum(x['state']=='error' for x in checks)
    mismatches = sum(x.get('data_equal') is False for x in checks)
    diagnostics = sum(x.get('diagnostic_count',0) for x in checks)
    result={'checks':len(checks),'passed':sum(x['state']=='passed' for x in checks),
            'errors':errors,'mismatches':mismatches,'diagnostic_count':diagnostics,
            'review_required':bool(errors or mismatches or diagnostics or (version>=4 and any(x['state']=='review_required' for x in checks))),
            'operations':{op:sum(x['operation']==op for x in checks)
                          for op in (('acp_metadata','acp_resource','rpk_roundtrip','clb_export','acp_apply') if version>=2 else
                                     ('acp_metadata','acp_resource','rpk_roundtrip','clb_export'))},
            'complete_compatibility_verified':False}
    if version>=4:
        result['operations']['clb_model_links']=sum(x['operation']=='clb_model_links' for x in checks)
        result['model_link_issue_count']=sum(x.get('link_issue_count',0) for x in checks)
    if version>=5:result['operations']['tam_roundtrip']=sum(x['operation']=='tam_roundtrip' for x in checks)
    if version>=6:
        result['operations']['tam_world_entities']=sum(x['operation']=='tam_world_entities' for x in checks)
        result['world_entity_issue_count']=sum(x.get('world_entity_issue_count',0) for x in checks)
        result['world_entity_count']=sum(x.get('world_entity_count',0) for x in checks)
        result['world_objects_unevaluated_maps']=sum(x.get('world_objects_evaluated') is False for x in checks)
    if version>=7:
        result['operations']['clb_material_links']=sum(x['operation']=='clb_material_links' for x in checks)
        result['material_link_issue_count']=sum(x.get('material_link_issue_count',0) for x in checks)
        result['material_count']=sum(x.get('material_count',0) for x in checks)
        result['material_effects_unevaluated_checks']=sum(x.get('effect_objects_evaluated') is False for x in checks)
    if version>=8:
        result['operations']['clb_material_textures']=sum(x['operation']=='clb_material_textures' for x in checks)
        result['material_texture_issue_count']=sum(x.get('texture_link_issue_count',0) for x in checks)
        result['material_texture_count']=sum(x.get('texture_count',0) for x in checks)
        result['material_textures_unevaluated_checks']=sum(x.get('texture_objects_evaluated') is False for x in checks)
    return result


def collect(content, game, root, cache, timeout=60, index=None, dependency_mods=(), merge_bases=None, _protocol=8):
    """Only write fresh evidence beneath a release staging root; never install it."""
    root, content = Path(root).resolve(), Path(content).resolve()
    root=outside(root,[content,game])
    cache=native.check_cache_directory(cache,game,[content,root])
    all_files=inventory(content)
    if _protocol not in (5,6,7,8):raise FormatError('Native collection supports current protocol 8 or historical protocol 5/6/7 fixtures')
    inputs, expected = targets(content,include_overlays=True,include_models=True,include_maps=True,include_world=_protocol>=6,include_materials=_protocol>=7,include_textures=_protocol>=8)
    directory = root/'native-evidence'
    if directory.exists(): raise FormatError('Native release evidence directory already exists')
    directory.mkdir()
    try:
        binding = native.public_binding(native.profile_for(game)); setup_error = None
    except (FormatError,OSError,RuntimeError) as exc:
        binding = None; setup_error = str(exc)
    checks = []
    for position, target in enumerate(expected):
        record = {**target,'source_sha256':inputs[target['file']]}
        unit = directory/f'{position:04d}'; unit.mkdir()
        source = content/target['file']; operation = target['operation']
        try:
            if setup_error: raise FormatError(setup_error)
            if operation=='clb_material_textures':
                detail,detail_file,artifact=native_texture_release.collect(source,content,game,unit,root,cache,timeout,dependency_mods)
            elif operation=='clb_material_links':
                detail,detail_file,artifact=native_material_release.collect(source,content,game,unit,root,cache,timeout,dependency_mods)
            elif operation=='tam_world_entities':
                detail,detail_file,artifact=native_world_release.collect(source,content,game,unit,root,cache,timeout)
            elif operation=='tam_roundtrip':
                detail,detail_file,artifact=native_map_release.collect(source,content,game,unit,root,cache,timeout)
            elif operation=='acp_apply':
                detail,detail_file,artifact=native_overlay_release.collect(source,target['pack'],content,game,unit,root,
                    cache,timeout,index,dependency_mods,merge_bases)
            elif operation=='clb_model_links':
                detail,detail_file,artifact=native_model_release.collect(source,content,game,unit,root,cache,timeout)
            elif operation.startswith('acp_'):
                detail = native_inspection.inspect(source,game,target['resource_id'],target['pack'],cache_dir=cache,timeout=timeout)
                artifact = unit/'body.bin'; witness = detail['roundtrip_artifact']
                if file_hash(witness['file']) != witness['sha256']: raise FormatError('Native body changed before release staging')
                shutil.copyfile(witness['file'],artifact)
                detail['roundtrip_artifact']={'file':artifact.relative_to(root).as_posix(),'sha256':file_hash(artifact)}
                detail['source']['file']=target['file']; detail_file=unit/'object.json'
            elif operation=='rpk_roundtrip':
                libraries=[content/p for p in inputs if Path(p).suffix.casefold()=='.clb']
                from . import language_context
                languages=[Path(row['file']) for row in language_context.bind(language_context.english(content))]
                detail = native_roundtrip.run(source,game,unit/'rpk',cache,timeout,libraries=libraries,
                    **({'languages':languages} if languages else {}))
                if languages:detail['context_languages']=[{'file':p.relative_to(content).as_posix(),'sha256':file_hash(p)} for p in languages]
                detail['context_libraries']=[{'file':p,'sha256':digest} for p,digest in inputs.items() if Path(p).suffix.casefold()=='.clb']
                artifact = Path(detail['output_file']); detail_file=unit/'rpk/roundtrip.json'
                detail['source']=target['file'];detail['output_file']=artifact.relative_to(root).as_posix()
            else:
                detail = native_assets.export_library(source,game,unit/'clb',cache,timeout)
                artifact = unit/'clb'/detail['files'][0]['name'];detail_file=unit/'clb/asset-export.json'
                detail['source']=target['file'];detail['output']=(unit/'clb').relative_to(root).as_posix()
            record.update(artifact=artifact.relative_to(root).as_posix(),artifact_sha256=file_hash(artifact))
            record.update(_assessment(record,detail,content,root,binding,_protocol))
            save_record(detail_file,detail)
            record.update(detail_file=detail_file.relative_to(root).as_posix(),detail_sha256=file_hash(detail_file))
        except (FormatError,OSError,RuntimeError,ValueError) as exc:
            record = {**target,'source_sha256':inputs[target['file']],'state':'error','error':str(exc)}
        checks.append(record)
    if inventory(content) != all_files: raise FormatError('Payload changed while native release evidence was collected')
    result = {'format':{8:FORMAT,7:MATERIAL_FORMAT,6:WORLD_FORMAT,5:MAP_FORMAT}[_protocol],'inputs':inputs,'binding':binding,'checks':checks,'summary':summary(checks,_protocol),
              'evidence_files':inventory(directory),'not_native_checked':sorted(set(all_files)-set(inputs)),'scope':{8:SCOPE,7:MATERIAL_SCOPE,6:WORLD_SCOPE,5:MAP_SCOPE}[_protocol]}
    blockers=environment_blockers(checks)
    if blockers:result['environment_blockers']=blockers
    validate(result,content,root)
    return result


def validate(report, content, root, expected_protocol=None):
    """Recompute comparisons from the frozen native binaries without calling the DLL."""
    if not isinstance(report,dict) or report.get('format') not in (FORMAT,MATERIAL_FORMAT,WORLD_FORMAT,MAP_FORMAT,MODEL_FORMAT,CONTEXT_FORMAT,PREVIOUS_FORMAT,LEGACY_FORMAT):
        raise FormatError('Unsupported native release evidence')
    version={FORMAT:8,MATERIAL_FORMAT:7,WORLD_FORMAT:6,MAP_FORMAT:5,MODEL_FORMAT:4,CONTEXT_FORMAT:3,PREVIOUS_FORMAT:2,LEGACY_FORMAT:1}[report['format']]
    if expected_protocol is not None and version!=expected_protocol:raise FormatError('Native release evidence protocol differs from its manifest')
    if report.get('scope')!={8:SCOPE,7:MATERIAL_SCOPE,6:WORLD_SCOPE,5:MAP_SCOPE,4:MODEL_SCOPE,3:CONTEXT_SCOPE,2:PREVIOUS_SCOPE,1:LEGACY_SCOPE}[version]:raise FormatError('Unsupported native release scope')
    inputs, expected = targets(content,include_overlays=version>=2,include_models=version>=4,include_maps=version>=5,include_world=version>=6,include_materials=version>=7,include_textures=version>=8)
    if report.get('inputs') != inputs: raise FormatError('Native evidence does not cover the current payload files/hashes')
    if report.get('not_native_checked') != sorted(set(inventory(content))-set(inputs)):
        raise FormatError('Native evidence omits its unchecked file scope')
    records = report.get('checks')
    if not isinstance(records,list) or len(records)!=len(expected): raise FormatError('Native evidence coverage is incomplete')
    if report.get('evidence_files') != inventory(Path(root)/'native-evidence'):
        raise FormatError('Frozen native evidence files changed')
    for record, target in zip(records,expected):
        if any(record.get(k)!=v for k,v in target.items()) or record.get('source_sha256')!=inputs[target['file']]:
            raise FormatError('Native evidence target identity or source hash differs')
        if record.get('state')=='error':
            if not isinstance(record.get('error'),str) or not record['error'] or any(k in record for k in ('data_equal','detail_file','artifact')):
                raise FormatError('Invalid native error record')
            continue
        detail_file = _artifact(root,record['detail_file'])
        if file_hash(detail_file)!=record['detail_sha256']: raise FormatError('Native detail report changed')
        result = _assessment(record,json.loads(detail_file.read_text(encoding='utf-8')),content,root,report['binding'],version)
        if any(record.get(k)!=v for k,v in result.items()): raise FormatError('Native evidence hides a comparison or diagnostic finding')
    observed = summary(records,version)
    if observed != report.get('summary'): raise FormatError('Native evidence summary differs from its checks')
    if 'environment_blockers' in report and report['environment_blockers']!=environment_blockers(records):
        raise FormatError('Native environment blocker groups differ from their checks')
    return observed
